# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (MIT; see licenses/bibtex-keys-MIT.txt)
"""Strict parsing delegated to Pybtex; match only explicit stable identity."""

from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit

from pybtex.database import parse_string
from pybtex.exceptions import PybtexError

DEFAULT_ID_FIELDS = ("zotero-uri", "zotero_uri")
URI_FIELDS = frozenset(DEFAULT_ID_FIELDS)
ZOTERO_PATH = re.compile(r"/(?:users/(?:[0-9]+|local/[A-Za-z0-9]+)|groups/[0-9]+)/items/[A-Za-z0-9]+/?\Z")
BBT_JSON_TRANSLATOR = "36a3b0b5-bad0-4a04-b79b-441c7cef77db"
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024


class InputError(ValueError):
    """A fatal input problem, suitable for a concise CLI error."""


def lf_lines(text: str) -> list[str]:
    """Split only at LF, as Markdown and unified diff do, preserving endings."""
    parts = text.split("\n")
    return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def read_utf8(path: Path, limit: int) -> tuple[bytes, str]:
    try:
        info = path.stat()
        if not stat.S_ISREG(info.st_mode):
            raise InputError(f"{path}: expected a regular file")
        if info.st_size > limit:
            raise InputError(f"{path}: exceeds {limit} byte limit")
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise InputError(f"{path}: exceeds {limit} byte limit")
        if b"\x00" in data:
            raise InputError(f"{path}: NUL byte in text input")
        text = data.decode("utf-8")
        if "\r" in text.replace("\r\n", ""):
            raise InputError(f"{path}: bare CR line endings are unsupported; use LF or CRLF")
        return data, text
    except (OSError, UnicodeError) as exc:
        raise InputError(f"{path}: {exc}") from exc


@dataclass(frozen=True)
class Record:
    key: str
    identity: str | None


def identity_value(field: str, value: str) -> str:
    if field in URI_FIELDS:
        value = value.strip()
    if not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError("identity is empty or contains control characters")
    if field in URI_FIELDS:
        uri = urlsplit(value)
        if (uri.scheme not in {"http", "https"} or uri.netloc.lower() != "zotero.org"
                or uri.query or uri.fragment or not ZOTERO_PATH.fullmatch(uri.path)):
            raise ValueError("expected a full Zotero users/groups item URI")
        return "https://zotero.org" + uri.path.rstrip("/")
    return value


def snapshot_format(path: Path) -> str:
    return "bbt-json" if path.suffix.lower() == ".json" else "bibtex"


def _bbt_json_records(text: str, path: Path):
    def unique_members(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError(f"duplicate JSON member {name!r}")
            result[name] = value
        return result

    def reject_constant(value):
        raise ValueError(f"non-JSON number {value}")

    try:
        data = json.loads(text.removeprefix("\ufeff"), object_pairs_hook=unique_members, parse_constant=reject_constant)
        if not isinstance(data, dict) or not isinstance(data.get("config"), dict) or data["config"].get("id") != BBT_JSON_TRANSLATOR:
            raise ValueError("expected BetterBibTeX JSON's config.id; CSL-JSON is a different format")
        items = data.get("items")
        if not isinstance(items, list):
            raise ValueError("items must be an array")
        records, issues, seen_keys = [], [], set()
        for index, item in enumerate(items):
            if not isinstance(item, dict) or not isinstance(item.get("itemType"), str):
                raise ValueError(f"item {index}: expected an item object with itemType")
            if item["itemType"] in {"attachment", "note", "annotation"}:
                continue
            key = item.get("citationKey")
            if key is None or key == "":
                issues.append({"kind": "missing_citation_key", "item_index": index,
                               "message": "No exported citationKey; no key was generated or inferred."})
                continue
            if not isinstance(key, str) or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in key):
                raise ValueError(f"item {index}: citationKey must be a nonempty Unicode string without control characters")
            if key.lower() in seen_keys:
                raise ValueError(f"repeated citationKey {key!r}")
            seen_keys.add(key.lower())
            identity = None
            if "uri" not in item:
                issues.append({"kind": "missing_identity", "key": key,
                               "message": "No item uri; itemID/export order are not stable identity substitutes."})
            else:
                try:
                    if not isinstance(item["uri"], str):
                        raise ValueError("uri must be a string")
                    identity = identity_value("zotero-uri", item["uri"])
                except ValueError as exc:
                    issues.append({"kind": "invalid_identity", "key": key, "field": "uri", "message": str(exc)})
            records.append(Record(key, identity))
        if not records:
            raise ValueError("snapshot has no citable entries with exported citationKey")
        return records, issues
    except (ValueError, RecursionError) as exc:
        raise InputError(f"{path}: invalid BetterBibTeX JSON: {exc}") from exc


def load_snapshot(path: Path, id_fields: tuple[str, ...], limit: int = MAX_SNAPSHOT_BYTES):
    raw, text = read_utf8(path, limit)
    if snapshot_format(path) == "bbt-json":
        records, issues = _bbt_json_records(text, path)
        return raw, records, issues
    try:
        database = parse_string(text.lstrip("\ufeff"), "bibtex")
    except (PybtexError, ValueError, RecursionError) as exc:
        raise InputError(f"{path}: invalid BibTeX: {exc}") from exc
    records, issues = [], []
    for key, entry in database.entries.items():
        identities = set()
        invalid = False
        for field in id_fields:
            if field not in entry.fields:
                continue
            try:
                identities.add(identity_value(field, entry.fields[field]))
            except ValueError as exc:
                issues.append({"kind": "invalid_identity", "key": key, "field": field, "message": str(exc)})
                invalid = True
        if len(identities) > 1:
            issues.append({"kind": "conflicting_identity_fields", "key": key, "identities": sorted(identities)})
        identity = next(iter(identities)) if len(identities) == 1 and not invalid else None
        if not identities and not invalid:
            issues.append({"kind": "missing_identity", "key": key, "message": "No configured stable ID field; never matched by metadata."})
        records.append(Record(key, identity))
    if not records:
        raise InputError(f"{path}: bibliography has no entries")
    return raw, records, issues


def match_snapshots(old: list[Record], new: list[Record]):
    indexes = []
    conflicts = []
    for side, records in (("old", old), ("new", new)):
        index = defaultdict(list)
        for record in records:
            if record.identity is not None:
                index[record.identity].append(record)
        for identity, entries in sorted(index.items()):
            if len(entries) > 1:
                conflicts.append({"kind": "duplicate_identity", "snapshot": side, "identity": identity,
                                  "keys": sorted(e.key for e in entries)})
        indexes.append(index)
    old_index, new_index = indexes
    mappings, states = [], {}
    for record in old:
        identity = record.identity
        if identity is None:
            states[record.key] = {"status": "unresolved_identity", "new_key": None, "identity": None}
        elif len(old_index[identity]) != 1 or len(new_index.get(identity, [])) > 1:
            states[record.key] = {"status": "conflict", "new_key": None, "identity": identity}
        elif identity not in new_index:
            states[record.key] = {"status": "removed_item", "new_key": None, "identity": identity}
            conflicts.append({"kind": "removed_item", "old_key": record.key, "identity": identity})
        else:
            target = new_index[identity][0]
            status = "unchanged" if record.key == target.key else "renamed"
            states[record.key] = {"status": status, "new_key": target.key, "identity": identity}
            if status == "renamed":
                mappings.append({"old_key": record.key, "new_key": target.key, "identity": identity})
    return sorted(mappings, key=lambda x: x["old_key"]), states, conflicts
