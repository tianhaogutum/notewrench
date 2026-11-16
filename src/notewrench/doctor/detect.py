"""Work out what each PATH given to ``notewrench doctor`` is. Reads only; never writes.

A path is either a source itself (a Zotero data folder or ``zotero.sqlite``, a Calibre
library folder or ``metadata.db``, a Joplin ``.jex`` file or raw export folder, a
Karakeep/Wallabag export, a CSL-JSON bibliography, a Markdown file) or a plain folder
that is scanned for such sources (hidden entries and symlinks are skipped).
"""
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import stat
import tarfile

from ..joplindupes.reader import ITEM_NAME
from ..linkdedupe.adapters import AdapterError, detect_format
from ..linkdedupe.strictjson import parse_json

MARKDOWN = re.compile(r"\.(md|markdown)\Z", re.IGNORECASE)
MAX_DEPTH = 6
MAX_ENTRIES = 50_000
MAX_JSON_BYTES = 64 * 1024 * 1024

KINDS = ("zotero", "joplin", "calibre", "readlater", "bibliography", "markdown")


class UsageError(Exception):
    """A path that does not exist or that doctor does not recognize (exit 2)."""


@dataclass
class Source:
    kind: str
    path: str                    # as displayed and used in suggested commands
    snapshot: str | None = None  # zotero: the zotero.sqlite file
    storage: str | None = None   # zotero: the storage/ folder
    format: str | None = None    # readlater: karakeep | wallabag-v2
    text: str | None = None      # readlater: the export text, read once


@dataclass
class Scan:
    sources: list[Source]
    ignored: int = 0     # files/folders in scanned folders that are not a known source
    truncated: bool = False


def _read_json(path: str) -> tuple[object, str] | None:
    try:
        if os.path.getsize(path) > MAX_JSON_BYTES:
            return None
        with open(path, "rb") as handle:
            text = handle.read().decode("utf-8")
        return parse_json(text), text
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return None


def _json_source(path: str) -> Source | None:
    parsed = _read_json(path)
    if parsed is None:
        return None
    document, text = parsed
    try:
        return Source("readlater", path, format=detect_format(document), text=text)
    except AdapterError:
        pass
    # CSL-JSON: an array of item objects; CSL requires both "id" and "type".
    if isinstance(document, list) and document and all(
            isinstance(item, dict) and "id" in item and "type" in item for item in document):
        return Source("bibliography", path)
    return None


def _zotero(folder: str) -> Source:
    return Source("zotero", folder, snapshot=os.path.join(folder, "zotero.sqlite"),
                  storage=os.path.join(folder, "storage"))


def classify_dir(path: str) -> Source | None:
    """A folder that is itself one source, or None."""
    if os.path.isfile(os.path.join(path, "zotero.sqlite")):
        return _zotero(path)
    if os.path.isfile(os.path.join(path, "metadata.db")):
        return Source("calibre", path)
    try:
        names = os.listdir(path)
    except OSError:
        return None
    if any(ITEM_NAME.fullmatch(name) for name in names):
        return Source("joplin", path)
    return None


def classify_file(path: str, *, explicit: bool) -> Source | None:
    name = os.path.basename(path)
    lower = name.lower()
    if name == "zotero.sqlite" or (explicit and lower.endswith(".sqlite")):
        return Source("zotero", path, snapshot=path, storage=os.path.join(os.path.dirname(path) or ".", "storage"))
    if name == "metadata.db":
        return Source("calibre", path)
    if lower.endswith(".jex"):
        return Source("joplin", path)
    if lower.endswith(".json"):
        return _json_source(path)
    if MARKDOWN.search(name):
        return Source("markdown", path)
    if explicit:
        try:
            if tarfile.is_tarfile(path):  # a JEX export saved under another name
                return Source("joplin", path)
        except OSError:
            pass
    return None


def _walk(folder: str, scan: Scan, depth: int, budget: list[int]) -> None:
    try:
        entries = sorted(os.scandir(folder), key=lambda entry: entry.name)
    except OSError:
        scan.ignored += 1
        return
    for entry in entries:
        if budget[0] <= 0:
            scan.truncated = True
            return
        budget[0] -= 1
        if entry.name.startswith("."):
            continue
        path = entry.name if folder == os.curdir else os.path.join(folder, entry.name)
        try:
            mode = entry.stat(follow_symlinks=False).st_mode
        except OSError:
            scan.ignored += 1
            continue
        if stat.S_ISDIR(mode):
            source = classify_dir(path)
            if source is not None:
                scan.sources.append(source)
            elif depth < MAX_DEPTH:
                _walk(path, scan, depth + 1, budget)
            else:
                scan.truncated = True
        elif stat.S_ISREG(mode):
            source = classify_file(path, explicit=False)
            if source is None:
                scan.ignored += 1
            else:
                scan.sources.append(source)
        else:
            scan.ignored += 1  # symlinks, sockets, devices


def detect(paths: list[str]) -> Scan:
    """Classify every path; raise UsageError for a missing or unrecognized one."""
    scan = Scan([])
    seen: set[str] = set()
    problems = []
    for raw in paths:
        path = os.path.normpath(raw)
        real = os.path.realpath(path)
        if real in seen:
            problems.append(f"{raw}: given more than once")
            continue
        seen.add(real)
        if not os.path.lexists(path):
            problems.append(f"{raw}: no such file or directory")
            continue
        if os.path.isdir(path):
            source = classify_dir(path)
            if source is not None:
                scan.sources.append(source)
                continue
            before = len(scan.sources)
            _walk(path, scan, 1, [MAX_ENTRIES])
            if len(scan.sources) == before:
                problems.append(f"{raw}: no Zotero, Joplin, Calibre, Karakeep/Wallabag, CSL-JSON or Markdown "
                                "source found in this folder")
            continue
        source = classify_file(path, explicit=True) if os.path.isfile(path) else None
        if source is None:
            problems.append(f"{raw}: not a recognized export or library")
        else:
            scan.sources.append(source)
    if problems:
        raise UsageError("\n".join(problems))
    # The same source reached twice (a folder and a file inside it) is checked once.
    unique, keys = [], set()
    for source in scan.sources:
        key = (source.kind, os.path.realpath(source.snapshot or source.path))
        if key not in keys:
            keys.add(key)
            unique.append(source)
    scan.sources = unique
    return scan
