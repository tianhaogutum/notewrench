# SPDX-License-Identifier: AGPL-3.0-only
"""Read bounded tar members without ever extracting archive paths."""

from dataclasses import dataclass
from pathlib import Path
import os
import stat
import re
import tarfile


class ConversionError(Exception):
    """An input, safety, or output validation failure."""


@dataclass(frozen=True)
class Limits:
    max_archive_bytes: int = 256 * 1024 * 1024
    max_member_bytes: int = 32 * 1024 * 1024
    max_total_bytes: int = 128 * 1024 * 1024
    max_members: int = 10000

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in vars(self).values()):
            raise ValueError("All archive limits must be positive integers")


def read_archive(source: Path, limits: Limits) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    seen: set[str] = set()
    total = 0
    try:
        if not stat.S_ISREG(source.stat().st_mode):
            raise ConversionError("Input must be a regular archive file")
        with source.open("rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ConversionError("Input must be a regular archive file")
            if info.st_size > limits.max_archive_bytes:
                raise ConversionError("Archive exceeds max_archive_bytes")
            # JEX is an uncompressed tar, not a sync directory or arbitrary ZIP.
            with tarfile.open(fileobj=stream, mode="r|") as archive:
                for count, member in enumerate(archive, 1):
                    if count > limits.max_members:
                        raise ConversionError("Archive exceeds max_members")
                    name = member.name
                    while name.startswith("./"):
                        name = name[2:]
                    name = name.rstrip("/") if member.isdir() else name
                    if (not name or name.startswith("/") or "\\" in name
                            or any(p in ("", ".", "..") for p in name.split("/"))
                            or any(ord(c) < 32 or ord(c) == 127 for c in name)):
                        raise ConversionError(f"Unsafe archive path: {member.name!r}")
                    if name in seen:
                        raise ConversionError(f"Duplicate archive path: {name}")
                    seen.add(name)
                    if member.isdir():
                        if name != "resources":
                            raise ConversionError(f"Unsupported archive directory: {name}")
                        continue
                    if not member.isfile() or member.issparse():
                        raise ConversionError(f"Unsafe archive member type: {name}")
                    if not (re.fullmatch(r"[a-fA-F0-9]{32}\.md", name)
                            or re.fullmatch(r"resources/[a-fA-F0-9]{32}(?:\.[a-zA-Z0-9_-]{1,32})?", name)):
                        raise ConversionError(f"Unsupported JEX member: {name}")
                    if member.size < 0:
                        raise ConversionError(f"Negative member size: {name}")
                    if member.size > limits.max_member_bytes:
                        raise ConversionError(f"Member exceeds max_member_bytes: {name}")
                    total += member.size
                    if total > limits.max_total_bytes:
                        raise ConversionError("Archive exceeds max_total_bytes")
                    data_stream = archive.extractfile(member)
                    if data_stream is None:
                        raise ConversionError(f"Unreadable member: {name}")
                    with data_stream:
                        data = data_stream.read(member.size + 1)
                    if len(data) != member.size:
                        raise ConversionError(f"Truncated archive member: {name}")
                    files[name] = data
                end_of_members = archive.offset
            # tarfile stops at the first zero block. Do not silently ignore a
            # concatenated archive, hidden members, or missing POSIX EOF blocks.
            stream.seek(end_of_members)
            padding_bytes = 0
            while chunk := stream.read(65536):
                padding_bytes += len(chunk)
                if any(chunk):
                    raise ConversionError("Nonzero data after tar end marker")
            if padding_bytes < 1024 or padding_bytes % 512:
                raise ConversionError("Truncated or malformed tar end padding")
    except (OSError, tarfile.TarError, EOFError, ValueError) as exc:
        raise ConversionError(f"Cannot read JEX: {exc}") from exc
    if not files:
        raise ConversionError("Archive contains no items")
    return files
