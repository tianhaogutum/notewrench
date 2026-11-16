# SPDX-License-Identifier: AGPL-3.0-only
"""Original bounded readers for the flat Joplin raw/JEX export layout.

Never extract archives. Never follow raw directory symlinks. Metadata is a
terminal block, not YAML and not every colon-bearing line in a note.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tarfile
from typing import BinaryIO

ID = r"[0-9a-f]{32}"
ITEM_NAME = re.compile(rf"({ID})\.md\Z")
BLOB_NAME = re.compile(rf"resources/({ID})(?:\.([A-Za-z0-9][A-Za-z0-9_.+-]*))?\Z")
PROP = re.compile(r"([A-Za-z_][A-Za-z0-9_]*):[ \t]*(.*)\Z")
KNOWN_TYPES = {1: "note", 2: "folder", 4: "resource", 5: "tag", 6: "note_tag"}
CHUNK = 128 * 1024


class ExportError(ValueError):
    """An export cannot be safely or unambiguously interpreted."""


@dataclass(frozen=True)
class Limits:
    max_file_bytes: int = 32 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    max_entries: int = 20000
    max_metadata_bytes: int = 4 * 1024 * 1024

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in vars(self).values()):
            raise ValueError("All limits must be positive integers")


@dataclass
class Item:
    id: str
    type: int
    title: str
    body: str
    metadata: dict[str, str]
    path: str

    def as_dict(self) -> dict:
        return {"id": self.id, "type": self.type, "title": self.title,
                "body": self.body, "metadata": self.metadata, "path": self.path}


@dataclass
class Blob:
    path: str
    size: int
    sha256: str


@dataclass
class Export:
    path: str
    format: str
    items: dict[str, Item] = field(default_factory=dict)
    blobs: dict[str, Blob] = field(default_factory=dict)
    warnings: list[dict] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=lambda: {
        "entries": 0, "input_bytes": 0, "resource_hashes": 0,
        "resource_bytes_hashed": 0, "metadata_files": 0})
    fingerprint: str = ""


def normalize(text: str) -> str:
    """Only NFC and line endings: retain whitespace, case, and blank lines."""
    import unicodedata
    return unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))


def parse_item(data: bytes, path: str) -> Item:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ExportError(f"{path}: metadata must be UTF-8") from e
    if "\0" in text:
        raise ExportError(f"{path}: NUL in metadata record")
    text = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    prefix, separator, props = text.rpartition("\n\n")
    if not separator:
        prefix, props = "", text  # Records such as note_tag have no title/body.
    metadata = {}
    for line in props.split("\n"):
        match = PROP.fullmatch(line.strip())
        if not match:
            raise ExportError(f"{path}: invalid terminal metadata block")
        key, value = match.groups()
        if key in metadata:
            raise ExportError(f"{path}: duplicate metadata key {key}")
        metadata[key] = value.strip()
    item_id = metadata.get("id", "")
    if not re.fullmatch(ID, item_id):
        raise ExportError(f"{path}: missing/invalid lowercase 32-hex id")
    if path != item_id + ".md":
        raise ExportError(f"{path}: filename and metadata id disagree")
    if not re.fullmatch(r"[1-9][0-9]*", metadata.get("type_", "")):
        raise ExportError(f"{path}: missing/invalid type_")
    title, body_sep, body = prefix.partition("\n\n")
    if "\n" in title:
        raise ExportError(f"{path}: unsupported multi-line title/header")
    kind = int(metadata["type_"])
    if kind != 1 and body_sep and kind in KNOWN_TYPES:
        raise ExportError(f"{path}: unexpected body in {KNOWN_TYPES[kind]} record")
    if "file_extension" in metadata and (
        "/" in metadata["file_extension"] or "\\" in metadata["file_extension"]
        or ".." in metadata["file_extension"]
    ):
        raise ExportError(f"{path}: unsafe file_extension")
    return Item(item_id, kind, title, body if body_sep else "", metadata, path)


def _name(name: str, directory: bool = False) -> str:
    if name.startswith("./"):
        name = name[2:]
    if directory:
        name = name.removesuffix("/")
    if not name or name.startswith("/") or "\\" in name or any(
        p in ("", ".", "..") for p in name.split("/")
    ):
        raise ExportError(f"Unsafe export path: {name!r}")
    if directory:
        if name != "resources":
            raise ExportError(f"Unsupported export directory: {name!r}")
    elif not ITEM_NAME.fullmatch(name) and not BLOB_NAME.fullmatch(name):
        raise ExportError(f"Unsupported raw layout/member: {name!r}; expected <id>.md or resources/<id>[.ext]")
    return name


def _open_anchored(path: Path, directory: bool = False) -> int:
    """Open every component relative to its parent fd, with no symlink hops."""
    parent = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        components = path.parts[1:]
        for index, component in enumerate(components):
            is_dir = directory or index < len(components) - 1
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if is_dir:
                flags |= os.O_DIRECTORY
            child = os.open(component, flags, dir_fd=parent)
            os.close(parent)
            parent = child
        result, parent = parent, None
        return result
    finally:
        if parent is not None:
            os.close(parent)


def _regular_open(path: Path) -> BinaryIO:
    stream = os.fdopen(_open_anchored(path), "rb")
    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
        stream.close()
        raise ExportError(f"Not a regular file: {path}")
    return stream


def _raw_read(root: Path, ingest, limits: Limits):
    # Pin directories by fd so a concurrent rename cannot redirect a leaf read.
    root_fd = _open_anchored(root, directory=True)
    budget = 0

    def read_dir(directory_fd: int, prefix: str = ""):
        nonlocal budget
        before_dir = os.fstat(directory_fd)
        names = []
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                budget += 1
                if budget > limits.max_entries:
                    raise ExportError("Entry count limit exceeded")
                names.append(entry.name)
        for name in sorted(names):
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            logical = prefix + name
            if stat.S_ISDIR(info.st_mode) and not prefix:
                _name(logical, True)
                ingest(logical, 0, None)
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory_fd)
                try:
                    read_dir(child, logical + "/")
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                _name(logical)
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
                with os.fdopen(fd, "rb") as stream:
                    before = os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        raise ExportError(f"Not a regular file: {logical}")
                    ingest(logical, before.st_size, stream)
                    if _fingerprint(before) != _fingerprint(os.fstat(stream.fileno())):
                        raise ExportError(f"Input changed during read: {logical}")
            else:
                raise ExportError(f"Symlink, special file or nested directory: {logical}")
        if _fingerprint(before_dir) != _fingerprint(os.fstat(directory_fd)):
            raise ExportError(f"Input directory changed during read: {root / prefix}")

    try:
        read_dir(root_fd)
    finally:
        os.close(root_fd)


def _fingerprint(s: os.stat_result) -> tuple:
    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def load_export(path: str | Path, limits: Limits | None = None, *, _resource_chunk=None) -> Export:
    """Read a snapshot; the private chunk consumer supports verified offline copies.

    The consumer receives (logical path, bytes), starting with an empty chunk
    for every binary (including empty files). The reader itself never writes.
    """
    limits = limits or Limits()
    path = Path(os.path.abspath(path))
    # Reject symlink ancestors too; this reader is intended for stable snapshots.
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ExportError(f"Symlink input/ancestor is not supported: {part}")
    result = Export(str(path), "raw" if path.is_dir() else "jex")
    seen: set[str] = set()
    manifest = []

    def ingest(name: str, size: int, stream: BinaryIO | None):
        if name in seen:
            raise ExportError(f"Duplicate archive path: {name}")
        seen.add(name)
        result.stats["entries"] += 1
        if result.stats["entries"] > limits.max_entries:
            raise ExportError("Entry count limit exceeded")
        if size < 0 or size > limits.max_file_bytes:
            raise ExportError(f"{name}: per-file size limit exceeded")
        result.stats["input_bytes"] += size
        if result.stats["input_bytes"] > limits.max_total_bytes:
            raise ExportError("Total uncompressed size limit exceeded")
        if stream is None:
            manifest.append([name, 0, None])
            return
        is_item = ITEM_NAME.fullmatch(name)
        if is_item and size > limits.max_metadata_bytes:
            raise ExportError(f"{name}: metadata size limit exceeded")
        digest = hashlib.sha256()
        if not is_item and _resource_chunk is not None:
            _resource_chunk(name, b"")
        chunks = []
        remaining = size
        while remaining:
            chunk = stream.read(min(CHUNK, remaining))
            if not chunk:
                raise ExportError(f"{name}: truncated file")
            remaining -= len(chunk)
            digest.update(chunk)
            if is_item:
                chunks.append(chunk)
            elif _resource_chunk is not None:
                _resource_chunk(name, chunk)
        manifest.append([name, size, digest.hexdigest()])
        if is_item:
            item = parse_item(b"".join(chunks), name)
            result.items[item.id] = item
            result.stats["metadata_files"] += 1
            if item.type not in KNOWN_TYPES:
                result.warnings.append({"code": "unknown_item_type", "id": item.id, "type": item.type})
        else:
            match = BLOB_NAME.fullmatch(name)
            blob_id = match[1]
            if blob_id in result.blobs:
                raise ExportError(f"Ambiguous multiple resource files for id {blob_id}")
            result.blobs[blob_id] = Blob(name, size, digest.hexdigest())
            result.stats["resource_hashes"] += 1
            result.stats["resource_bytes_hashed"] += size

    try:
        if path.is_dir():
            _raw_read(path, ingest, limits)
        else:
            with _regular_open(path) as archive_stream:
                before = os.fstat(archive_stream.fileno())
                if before.st_size > limits.max_total_bytes + limits.max_entries * 4096 + 10240:
                    raise ExportError("Archive physical size limit exceeded")
                last_end = 0
                # JEX is an uncompressed tar. Compression is deliberately not guessed.
                with tarfile.open(fileobj=archive_stream, mode="r|") as archive:
                    for member in archive:
                        if not member.isfile() and not member.isdir():
                            raise ExportError(f"Links/special tar entries forbidden: {member.name!r}")
                        if member.issparse() or member.pax_headers.get("GNU.sparse.map"):
                            raise ExportError("Sparse tar entries forbidden")
                        name = _name(member.name, member.isdir())
                        last_end = member.offset_data + ((member.size + 511) // 512) * 512
                        if member.isdir():
                            if member.size:
                                raise ExportError("Nonempty tar directory entry")
                            ingest(name, 0, None)
                        else:
                            with archive.extractfile(member) as stream:
                                ingest(name, member.size, stream)
                # tarfile normally ignores everything following the first zero
                # header. Reject concatenated/hidden archives and require trailer.
                archive_stream.seek(last_end)
                trailer_bytes = 0
                while trailer := archive_stream.read(CHUNK):
                    trailer_bytes += len(trailer)
                    if trailer.strip(b"\0"):
                        raise ExportError("Nonzero trailing tar data/concatenated archive")
                if trailer_bytes < 1024:
                    raise ExportError("Truncated tar: missing two zero trailer blocks")
                if _fingerprint(before) != _fingerprint(os.fstat(archive_stream.fileno())):
                    raise ExportError(f"Archive changed during read: {path}")
    except (OSError, tarfile.TarError, EOFError) as e:
        raise ExportError(f"Cannot read export {path}: {e}") from e
    if not result.items:
        raise ExportError("Unsupported/empty export: no item metadata records")
    for blob_id, blob in sorted(result.blobs.items()):
        if blob_id not in result.items or result.items[blob_id].type != 4:
            result.warnings.append({"code": "orphan_resource_file", "id": blob_id, "path": blob.path})
    result.fingerprint = hashlib.sha256(json.dumps(sorted(manifest), separators=(",", ":")).encode()).hexdigest()
    return result
