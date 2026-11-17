# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum
"""Anki metadata adapter around Python's CSV reader and writer.

Only regular note fields receive user-supplied names. Special columns are
identified by Anki's 1-based headers, never by guessing from their contents.
"""

from __future__ import annotations

import csv
import hashlib
import io
import itertools
import os
import stat
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, TextIO


class InputError(ValueError):
    """Actionable input or review error, safe to display without a traceback."""


def require_regular_file(path: Path) -> None:
    if not stat.S_ISREG(path.stat().st_mode):
        raise InputError(f"{path}: expected a regular text file; pipes and devices are not supported.")


SEPARATORS = {"tab": "\t", "comma": ",", "semicolon": ";", "space": " ", "pipe": "|", "colon": ":"}
SPECIALS = ("guid", "notetype", "deck", "tags")
KNOWN_HEADERS = {
    "separator", "html", "columns", "tags", "notetype", "deck",
    "guid column", "notetype column", "deck column", "tags column",
    "if matches", "match scope",
}
MAX_FIELD_CHARS = 4 * 1024 * 1024
MAX_LINE_CHARS = 8 * 1024 * 1024
csv.field_size_limit(MAX_FIELD_CHARS)


@dataclass
class Note:
    fields: dict[str, str]
    guid: str = ""
    notetype: str = ""
    deck: str = ""
    tags: list[str] = field(default_factory=list)

    def snapshot(self) -> dict:
        return {"fields": self.fields, "guid": self.guid, "notetype": self.notetype,
                "deck": self.deck, "tags": self.tags}


@dataclass
class TextDeck:
    path: Path
    fields: tuple[str, ...]
    notes: dict[str, Note]
    html: bool
    sha256: str
    headers: dict[str, str]
    delimiter: str = "\t"
    header_row: bool = False
    defaults: dict[str, str] = field(default_factory=dict)


def _separator(value: str) -> str:
    if value.lower() in SEPARATORS:
        return SEPARATORS[value.lower()]
    if value in SEPARATORS.values():
        return value
    raise InputError(f"Unsupported separator {value!r}; use an Anki separator name.")


def _header(line: str, headers: dict[str, str], path: Path, line_no: int) -> None:
    text = line.rstrip("\r\n")[1:]
    if "\x00" in text or "\x1f" in text:
        raise InputError(f"{path}:{line_no}: NUL/Anki field-separator characters are not allowed in headers.")
    if ":" not in text:
        return  # ordinary Anki comment
    key, value = text.split(":", 1)
    key = key.strip().lower()
    if key not in KNOWN_HEADERS:
        raise InputError(f"{path}:{line_no}: unsupported header #{key}; remove it explicitly if irrelevant.")
    if key in headers:
        raise InputError(f"{path}:{line_no}: duplicate #{key} header.")
    headers[key] = value


def _names(names: Iterable[str], path: Path) -> tuple[str, ...]:
    result = tuple(names)
    if not result or any(not n.strip() or any(ord(c) < 32 for c in n) for n in result):
        raise InputError(f"{path}: field names must be nonempty single-line strings.")
    if len(set(result)) != len(result):
        raise InputError(f"{path}: duplicate field names.")
    return result


class _DigestReader(io.RawIOBase):
    """Hash the exact bytes consumed by the text parser, with bounded buffering."""

    def __init__(self, raw, digest):
        self.raw, self.digest = raw, digest

    def readable(self):
        return True

    def readinto(self, buffer):
        size = self.raw.readinto(buffer)
        if size:
            self.digest.update(memoryview(buffer)[:size])
        return size


def _physical_lines(source, path):
    while True:
        line = source.readline(MAX_LINE_CHARS + 1)
        if not line:
            return
        if len(line) > MAX_LINE_CHARS:
            raise InputError(f"{path}: physical line exceeds {MAX_LINE_CHARS} characters; refusing an unbounded record.")
        yield line


def _records(source, headers, path, delimiter):
    enumerated = enumerate(_physical_lines(source, path), 1)
    started = False
    separator = None
    for line_no, line in enumerated:
        if line_no == 1 and line.startswith("tags:"):
            _header("#" + line, headers, path, line_no)
            continue  # historical Anki text format, verified against upstream
        if line.startswith("#"):
            if not started:
                _header(line, headers, path, line_no)
            continue
        if not line.strip():
            continue
        started = True
        if separator is None:
            separator = _separator(delimiter or headers.get("separator", "tab"))
        # csv.reader consumes exactly one logical record, including its
        # continuation lines. No comment filtering occurs inside that record.
        continuation = (text for _, text in enumerated)
        try:
            reader = csv.reader(itertools.chain([line], continuation), delimiter=separator, strict=True)
            row = next(reader)
        except csv.Error as exc:
            raise InputError(f"{path}:{line_no}: invalid CSV (maximum field {MAX_FIELD_CHARS} characters): {exc}") from exc
        if any("\x00" in cell or "\x1f" in cell for cell in row):
            raise InputError(f"{path}:{line_no}: NUL/Anki field-separator characters are not allowed.")
        yield line_no, row


def read_deck(path: Path, id_field: str, fields: Iterable[str] | None = None,
              delimiter: str | None = None, header_row: bool = False,
              defaults: dict[str, str] | None = None) -> TextDeck:
    """Read UTF-8 Anki text. Reject ragged rows rather than silently losing data."""
    require_regular_file(path)
    if path.suffix.lower() in {".apkg", ".colpkg", ".anki2", ".anki"}:
        raise InputError(f"{path}: collection/package files are not supported; export Notes in Plain Text first.")
    headers: dict[str, str] = {}
    fields = tuple(fields) if fields is not None else None
    defaults = dict(defaults or {})
    if not set(defaults).issubset({"notetype", "deck"}) or any(
            not value.strip() or any(ord(c) < 32 for c in value) for value in defaults.values()):
        raise InputError("Default note type/deck names must be nonempty single-line text.")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as raw:
            before = os.fstat(raw.fileno())
            with io.TextIOWrapper(io.BufferedReader(_DigestReader(raw, digest)), encoding="utf-8-sig", newline="") as source:
                records = iter(_records(source, headers, path, delimiter))
                first_row = next(records, None)
                separator = _separator(delimiter or headers.get("separator", "tab"))
                html_value = headers.get("html", "false").lower()
                if html_value not in ("true", "false"):
                    raise InputError(f"{path}: #html must be true or false.")
                if headers.get("if matches", "update current") not in ("update current", "keep current", "keep both"):
                    raise InputError(f"{path}: invalid #if matches policy.")
                if headers.get("match scope", "notetype") not in ("notetype", "notetype + deck"):
                    raise InputError(f"{path}: invalid #match scope.")
                for name in ("notetype", "deck"):
                    if name in headers and f"{name} column" in headers:
                        raise InputError(f"{path}: conflicting global #{name} and #{name} column headers.")
                    if name in headers and any(ord(c) < 32 or ord(c) == 127 for c in headers[name]):
                        raise InputError(f"{path}: #{name} must contain a single-line name.")
                special: dict[str, int] = {}
                for name in SPECIALS:
                    if f"{name} column" in headers:
                        try:
                            index = int(headers[f"{name} column"]) - 1
                        except ValueError as exc:
                            raise InputError(f"{path}: #{name} column must be a positive integer.") from exc
                        if index < 0:
                            raise InputError(f"{path}: #{name} column must be a positive integer.")
                        special[name] = index
                if len(set(special.values())) != len(special):
                    raise InputError(f"{path}: special columns overlap.")
                column_labels = None
                if "columns" in headers:
                    try:
                        column_labels = next(csv.reader([headers["columns"]], delimiter=separator, strict=True))
                    except csv.Error as exc:
                        raise InputError(f"{path}: invalid CSV in #columns: {exc}") from exc
                if header_row:
                    if column_labels is not None:
                        raise InputError(f"{path}: choose #columns or --header-row, not both.")
                    if first_row is None:
                        raise InputError(f"{path}: missing header row.")
                    _, column_labels = first_row
                    first_row = next(records, None)
                width = len(first_row[1]) if first_row else (
                    len(column_labels) if column_labels is not None else len(fields or ()) + len(special))
                if any(index >= width for index in special.values()):
                    raise InputError(f"{path}: special column index exceeds {width} columns.")
                regular_indices = [i for i in range(width) if i not in special.values()]
                if column_labels is not None and len(column_labels) != width:
                    raise InputError(f"{path}: #columns/header width does not match data width.")
                if fields is None and column_labels is None:
                    raise InputError(f"{path}: field names are missing; supply --fields for an Anki export without #columns.")
                names = _names(fields if fields is not None else [column_labels[i] for i in regular_indices], path)
                if len(names) != len(regular_indices):
                    raise InputError(f"{path}: expected {len(regular_indices)} regular field names, got {len(names)}.")
                if header_row and fields is not None and tuple(column_labels[i] for i in regular_indices) != names:
                    raise InputError(f"{path}: header row does not match the supplied field names; refusing to discard a possible note. Use source-specific --*-header-row flags.")
                if id_field not in names:
                    raise InputError(f"{path}: course ID field {id_field!r} is missing.")
                notes: dict[str, Note] = {}
                guids: set[str] = set()
                for line_no, row in itertools.chain([first_row] if first_row else (), records):
                    if len(row) != width:
                        raise InputError(f"{path}:{line_no}: expected {width} columns, got {len(row)}; refusing to truncate/pad.")
                    values = dict(zip(names, (row[i] for i in regular_indices)))
                    key = values[id_field]
                    if not key or key != key.strip() or any(ord(c) < 32 or ord(c) == 127 for c in key):
                        raise InputError(f"{path}:{line_no}: course IDs must be nonempty, unpadded plain single-line text.")
                    if unicodedata.normalize("NFC", key) != key:
                        raise InputError(f"{path}:{line_no}: course ID {key!r} is not Unicode NFC; Anki may normalize it. Use stable NFC IDs.")
                    if key in notes:
                        raise InputError(f"{path}:{line_no}: duplicate course ID {key!r}.")
                    meta = {name: row[index] for name, index in special.items()}
                    guid = meta.get("guid", "")
                    if guid and (guid in guids or any(ord(c) < 33 or ord(c) > 126 for c in guid)):
                        raise InputError(f"{path}:{line_no}: duplicate or invalid GUID {guid!r}.")
                    if guid:
                        guids.add(guid)
                    for name in ("notetype", "deck"):
                        if name in special and not meta[name] and name not in defaults:
                            raise InputError(f"{path}:{line_no}: empty {name} column.")
                        if name in meta and any(ord(c) < 32 or ord(c) == 127 for c in meta[name]):
                            raise InputError(f"{path}:{line_no}: {name} must contain a single-line name.")
                    tags = sorted(set((meta.get("tags", "") + " " + headers.get("tags", "")).split()))
                    note_type = meta.get("notetype", headers.get("notetype", "")) or defaults.get("notetype", "")
                    deck_name = meta.get("deck", headers.get("deck", "")) or defaults.get("deck", "")
                    notes[key] = Note(values, guid, note_type, deck_name, tags)
            after = os.fstat(raw.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise InputError(f"{path}: input changed while reading; retry after export finishes.")
    except (UnicodeError, csv.Error) as exc:
        raise InputError(f"{path}: invalid UTF-8/CSV: {exc}") from exc
    return TextDeck(path, names, notes, html_value == "true", digest.hexdigest(), headers, separator, header_row, defaults)


def write_deck(stream: TextIO, fields: Iterable[str], notes: Iterable[Note], html: bool) -> None:
    """Write explicit Anki headers and quote every first-field comment safely."""
    names = tuple(fields)
    notes = list(notes)
    include = [name for name in SPECIALS if any(getattr(n, name) for n in notes)]
    for name in ("notetype", "deck"):
        if name in include and any(not getattr(note, name) for note in notes):
            raise InputError(f"Output has incomplete {name} metadata. Re-export consistently, or fill missing names with --{name}.")
    # Tags are a special column whenever any tags exist. Empty metadata cells
    # are deliberate; no made-up GUID is assigned to a newly authored note.
    stream.write("#separator:tab\n")
    stream.write(f"#html:{str(html).lower()}\n")
    stream.write("#if matches:update current\n#match scope:notetype\n")
    for index, name in enumerate(include, 1):
        stream.write(f"#{name} column:{index}\n")
    labels = [f"Anki {name}" for name in include] + list(names)
    writer = csv.writer(stream, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_ALL)
    stream.write("#columns:")
    writer.writerow(labels)
    for note in notes:
        values = [" ".join(note.tags) if name == "tags" else getattr(note, name) for name in include]
        writer.writerow(values + [note.fields[name] for name in names])
