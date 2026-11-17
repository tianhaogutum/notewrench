# SPDX-License-Identifier: MIT
# Copyright (c) 2026 bookmark-receipts contributors (see licenses/browser-bookmarks-MIT.txt)
"""Portable review bundles, explicit decisions, and input integrity checks."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import tempfile
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout
from ijson.backends.python import basic_parse
from ijson.common import JSONError

from .parsing import MAX_DL_DEPTH, MAX_INPUT_BYTES, ReviewError, parse_html, read_limited, sha256, url_key

SCHEMA = "bookmark-receipts/review-v1"
POLICY = "conservative-http-v1"
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_REVIEW_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 25_000
MAX_RECORD_JSON_BYTES = 32 * 1024 * 1024
MAX_JSON_CONTAINERS = 650_000
MAX_JSON_EVENTS = 5_000_000
MAX_JSON_DEPTH = 128
MAX_JSON_STRING_BYTES = 32 * 1024 * 1024
FIELDS = ("url", "title", "description", "notes", "tags", "folder", "added", "modified", "private", "toread")
DEFAULTS = {
    "description": "all",
    "notes": "all",
    "tags": "union",
    "added": "earliest",
    "modified": "latest",
    "private": "safest",
    "toread": "any",
}


class ReviewPlan(dict):
    """A JSON-compatible plan with an out-of-band optimistic edit revision."""

    def __init__(self, value: dict, revision: str):
        super().__init__(value)
        self.revision = revision


def _revision(plan: dict) -> str:
    return sha256(canonical_json(plan))


def canonical_json(value) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ReviewError("JSON contains invalid Unicode, non-finite values, or excessive nesting") from exc


def _json_chunks(value):
    size = 1
    try:
        for chunk in json.JSONEncoder(ensure_ascii=False, indent=2, allow_nan=False).iterencode(value):
            size += len(chunk.encode("utf-8"))
            if size > MAX_REVIEW_BYTES:
                raise ReviewError("Expanded JSON exceeds the 64 MiB bundle limit; split inputs or reduce custom fields")
            yield chunk
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ReviewError):
            raise
        raise ReviewError("JSON contains invalid Unicode, values, or excessive nesting") from exc
    yield "\n"


def json_text(value) -> str:
    return "".join(_json_chunks(value))


def write_json(path: Path, value) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for chunk in _json_chunks(value):
            handle.write(chunk)


@contextmanager
def publish_directory(destination: Path):
    destination = destination.absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if os.path.lexists(destination):
        raise ReviewError(f"Output already exists: {destination}; choose a new directory")
    with tempfile.TemporaryDirectory(prefix=f".{destination.name}-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "bundle"
        stage.mkdir(mode=0o700)
        yield stage
        if os.path.lexists(destination):
            raise ReviewError(f"Output appeared while working: {destination}; nothing overwritten")
        stage.rename(destination)


def groups_for(records: list[dict]) -> list[dict]:
    grouped: OrderedDict[str, list[dict]] = OrderedDict()
    for record in records:
        grouped.setdefault(record["key"], []).append(record)
    groups = []
    for key, members in grouped.items():
        group_id = "g-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        conflicts = [field for field in FIELDS if len({canonical_json(r[field]) for r in members}) > 1]
        groups.append({
            "id": group_id,
            "key": key,
            "members": [record["id"] for record in members],
            "conflicts": conflicts,
        })
    if len({group["id"] for group in groups}) != len(groups):
        raise ReviewError("Cluster ID collision; cannot safely build review")
    return groups


def proposed_decision(group: dict) -> dict:
    first = group["members"][0]
    return {
        "action": "pending" if len(group["members"]) > 1 else "keep",
        "fields": {field: DEFAULTS.get(field, "@" + first) for field in FIELDS},
    }


def create_review(inputs: list[tuple[Path, str]], destination: Path) -> dict:
    if not inputs:
        raise ReviewError("Provide at least one HTML or linkding export")
    if len(inputs) > 32:
        raise ReviewError("At most 32 inputs per review")
    with publish_directory(destination) as stage:
        (stage / "sources").mkdir()
        sources, records = [], []
        total_bytes = 0
        total_record_bytes = 0
        for index, (path, kind) in enumerate(inputs, 1):
            source_id = f"s{index}"
            data = read_limited(path)
            total_bytes += len(data)
            if total_bytes > MAX_TOTAL_BYTES:
                raise ReviewError(f"Combined inputs exceed {MAX_TOTAL_BYTES:,} bytes")
            parsed, warnings = parse_html(data, source_id, kind)
            if len(records) + len(parsed) > MAX_RECORDS:
                raise ReviewError(f"Combined inputs exceed the {MAX_RECORDS:,}-bookmark limit")
            for record in parsed:
                total_record_bytes += len(canonical_json(record))
                if total_record_bytes > MAX_RECORD_JSON_BYTES:
                    raise ReviewError("Expanded bookmark metadata exceeds the 32 MiB review budget; split the inputs")
            filename = f"sources/{source_id}.html"
            (stage / filename).write_bytes(data)
            sources.append({
                "id": source_id,
                "name": path.name,
                "original_path": str(path.absolute()),
                "format": kind,
                "sha256": sha256(data),
                "bytes": len(data),
                "bundle_file": filename,
                "warnings": warnings,
            })
            records.extend(parsed)
        snapshot = {"policy": POLICY, "sources": sources, "records": records}
        groups = groups_for(records)
        for group in groups:
            group["decision"] = proposed_decision(group)
        plan = {"schema": SCHEMA, "snapshot_sha256": sha256(canonical_json(snapshot)), "snapshot": snapshot, "groups": groups}
        write_json(stage / "review.json", plan)
        from .rendering import write_view

        write_view(stage / "review.html", plan)
    return ReviewPlan(plan, _revision(plan))


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReviewError(f"Duplicate JSON key {key!r}")
        result[key] = value
    return result


def _bad_constant(value):
    raise ReviewError(f"Non-finite JSON number {value} is not supported")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ReviewError("JSON number exceeds the finite numeric range")
    return number


def _check_json_budget(data: bytes) -> None:
    containers, depth = 0, 0
    # Explicit Python backend: ijson 3.6.0's yajl2_c backend segfaulted on a
    # 5,000-digit integer in a bounded probe (see docs/browser-bookmarks.md).
    # Stream events before the standard decoder allocates a potentially huge DOM.
    for index, (event, value) in enumerate(basic_parse(io.BytesIO(data)), 1):
        if index > MAX_JSON_EVENTS:
            raise ReviewError("JSON exceeds the event complexity budget")
        if event in {"start_map", "start_array"}:
            containers += 1
            depth += 1
            if containers > MAX_JSON_CONTAINERS or depth > MAX_JSON_DEPTH:
                raise ReviewError("JSON exceeds the container/nesting complexity budget")
        elif event in {"end_map", "end_array"}:
            depth -= 1
        elif event in {"string", "map_key"} and len(value.encode("utf-8")) > MAX_JSON_STRING_BYTES:
            raise ReviewError("JSON contains a string exceeding the 32 MiB text budget")


def decode_json(data: bytes | str, label: str = "input"):
    try:
        if isinstance(data, str):
            data = data.encode("utf-8", "strict")
        if len(data) > MAX_REVIEW_BYTES:
            raise ReviewError("JSON exceeds the 64 MiB byte limit")
        if data.startswith(b"\xef\xbb\xbf"):
            data = data[3:]
        _check_json_budget(data)
        return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=_bad_constant, parse_float=_finite_float)
    except ReviewError:
        raise
    except (JSONError, UnicodeError, ValueError, RecursionError, OverflowError) as exc:
        raise ReviewError(f"Invalid JSON syntax or numeric value in {label}") from exc


def load_json(path: Path, limit: int = MAX_REVIEW_BYTES):
    return decode_json(read_limited(path, limit), str(path))


def validate_decision(group: dict) -> None:
    decision = group.get("decision")
    if not isinstance(decision, dict) or not isinstance(decision.get("action"), str) or decision["action"] not in {"pending", "merge", "keep"}:
        raise ReviewError(f"{group['id']}: action must be pending, merge, or keep")
    fields = decision.get("fields")
    if not isinstance(fields, dict) or set(fields) != set(FIELDS):
        raise ReviewError(f"{group['id']}: decision.fields must contain exactly {', '.join(FIELDS)}")
    for field, choice in fields.items():
        if isinstance(choice, str) and choice.startswith("@"):
            if choice[1:] not in group["members"]:
                raise ReviewError(f"{group['id']}: {field} references a record outside this group: {choice}")
            continue
        allowed = {
            "description": {"all"}, "notes": {"all"}, "tags": {"union"},
            "added": {"earliest"}, "modified": {"latest"}, "private": {"safest"}, "toread": {"any"},
        }.get(field, set())
        if isinstance(choice, str) and choice in allowed:
            continue
        if isinstance(choice, dict) and set(choice) == {"value"}:
            value = choice["value"]
            if field in {"tags", "folder"}:
                if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                    raise ReviewError(f"{group['id']}: custom {field} must be an array of strings")
                if field == "tags" and any("," in item for item in value):
                    raise ReviewError(f"{group['id']}: Netscape TAGS cannot represent a tag containing a comma")
                if field == "folder" and len(value) >= MAX_DL_DEPTH:
                    raise ReviewError(f"{group['id']}: custom folder path exceeds the nesting budget")
            elif value is not None and not isinstance(value, str):
                raise ReviewError(f"{group['id']}: custom {field} must be a string or null")
            elif value is None and field in {"url", "title", "description", "notes"}:
                raise ReviewError(f"{group['id']}: {field} cannot be null")
            if field in {"private", "toread"} and value not in {None, "0", "1"}:
                raise ReviewError(f"{group['id']}: custom {field} must be 0, 1, or null")
            if field == "url" and not value:
                raise ReviewError(f"{group['id']}: URL cannot be empty")
            if len(canonical_json(value)) > MAX_INPUT_BYTES:
                raise ReviewError(f"{group['id']}: custom {field} exceeds the 8 MiB serialized-value budget")
            strings = value if isinstance(value, list) else [value] if isinstance(value, str) else []
            for string in strings:
                try:
                    string.encode("utf-8", "strict")
                except UnicodeError as exc:
                    raise ReviewError(f"{group['id']}: custom {field} contains invalid Unicode") from exc
                if "\x00" in string:
                    raise ReviewError(f"{group['id']}: custom {field} contains a NUL character")
                if len(string.encode("utf-8")) > MAX_INPUT_BYTES:
                    raise ReviewError(f"{group['id']}: custom {field} exceeds the 8 MiB text limit")
            continue
        raise ReviewError(f"{group['id']}: invalid choice for {field}: {choice!r}")


def load_review(path: Path, *, verify_sources: bool = True) -> dict:
    plan = load_json(path)
    if not isinstance(plan, dict) or plan.get("schema") != SCHEMA:
        raise ReviewError(f"Unsupported review schema; expected {SCHEMA}")
    snapshot = plan.get("snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("policy") != POLICY:
        raise ReviewError("Missing snapshot or unknown URL policy")
    if plan.get("snapshot_sha256") != sha256(canonical_json(snapshot)):
        raise ReviewError("Snapshot checksum mismatch; edit only group decisions, or scan again")
    sources, records = snapshot.get("sources"), snapshot.get("records")
    if not isinstance(sources, list) or not isinstance(records, list) or not 1 <= len(sources) <= 32 or len(records) > MAX_RECORDS:
        raise ReviewError("Snapshot sources and records must be arrays")
    source_ids = set()
    for index, source in enumerate(sources, 1):
        if not isinstance(source, dict) or source.get("id") != f"s{index}":
            raise ReviewError("Invalid source ID/order")
        source_ids.add(source["id"])
        expected_file = f"sources/s{index}.html"
        if source.get("bundle_file") != expected_file:
            raise ReviewError(f"{source['id']}: source path must be {expected_file}")
        if not isinstance(source.get("format"), str) or source["format"] not in {"html", "linkding"} or not isinstance(source.get("name"), str):
            raise ReviewError(f"{source['id']}: invalid source metadata")
        if not isinstance(source.get("warnings"), list) or not all(isinstance(value, str) for value in source["warnings"]):
            raise ReviewError(f"{source['id']}: invalid source warnings")
        if type(source.get("bytes")) is not int or not 0 <= source["bytes"] <= MAX_INPUT_BYTES:
            raise ReviewError(f"{source['id']}: invalid source byte count")
        if not isinstance(source.get("original_path"), str) or not isinstance(source.get("sha256"), str) or len(source["sha256"]) != 64:
            raise ReviewError(f"{source['id']}: invalid source path/hash")
        if verify_sources:
            candidate = path.parent / expected_file
            if candidate.is_symlink() or not candidate.resolve().is_relative_to(path.parent.resolve()):
                raise ReviewError(f"{source['id']}: copied source must be inside the review bundle")
            data = read_limited(candidate)
            if sha256(data) != source.get("sha256") or len(data) != source.get("bytes"):
                raise ReviewError(f"{source['id']}: bundled source changed; restore it or scan again")
    record_ids = set()
    source_indexes = {source_id: 0 for source_id in source_ids}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("source_id"), str) or record["source_id"] not in source_ids:
            raise ReviewError("Invalid record source")
        source_indexes[record["source_id"]] += 1
        if type(record.get("index")) is not int or record["index"] != source_indexes[record["source_id"]]:
            raise ReviewError("Invalid record index")
        expected_id = f"{record['source_id']}:{record['index']}"
        if record.get("id") != expected_id or expected_id in record_ids:
            raise ReviewError("Invalid or duplicate record ID")
        record_ids.add(expected_id)
        for field in FIELDS:
            value = record.get(field)
            if field in {"tags", "folder"}:
                valid = isinstance(value, list) and all(isinstance(item, str) for item in value)
            else:
                valid = isinstance(value, str) or (value is None and field in {"added", "modified", "private", "toread"})
            if not valid:
                raise ReviewError(f"{expected_id}: invalid {field}")
        if record.get("key") != url_key(record["url"])[0]:
            raise ReviewError(f"{expected_id}: URL key does not match the URL policy")
        if not isinstance(record.get("attributes"), dict) or not isinstance(record.get("warnings"), list) or not all(isinstance(warning, str) for warning in record["warnings"]):
            raise ReviewError(f"{expected_id}: missing original metadata")
        if not isinstance(record.get("dd_html"), list) or not all(isinstance(value, str) for value in record["dd_html"]):
            raise ReviewError(f"{expected_id}: invalid original DD metadata")
    expected_groups = groups_for(records)
    groups = plan.get("groups")
    if not isinstance(groups, list) or len(groups) != len(expected_groups):
        raise ReviewError("Review groups do not match the snapshot")
    for group, expected in zip(groups, expected_groups):
        if not isinstance(group, dict) or any(group.get(key) != value for key, value in expected.items()):
            raise ReviewError("Group IDs, keys, members, or conflict lists changed; edit only decisions")
        validate_decision(group)
    return ReviewPlan(plan, _revision(plan))


@contextmanager
def staged_text(path: Path, text: str):
    with staged_chunks(path, [text]) as temporary:
        yield temporary


@contextmanager
def staged_chunks(path: Path, chunks):
    descriptor, temporary = tempfile.mkstemp(prefix=".review-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for chunk in chunks:
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        yield Path(temporary)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_review(path: Path, plan: dict) -> None:
    for group in plan["groups"]:
        validate_decision(group)
    expected = getattr(plan, "revision", None)
    if expected is None:
        raise ReviewError("Load the review with load_review() before saving, so concurrent edits can be detected")
    lock = FileLock(str(path) + ".lock", timeout=0)
    try:
        with lock:
            if _revision(load_json(path)) != expected:
                raise ReviewError("Review changed since it was loaded; reload and repeat this decision to avoid losing another edit")
            from .rendering import view_chunks

            # Prepare and fsync both files before the authoritative JSON changes.
            with staged_chunks(path, _json_chunks(plan)) as json_stage, staged_chunks(path, view_chunks(plan)) as html_stage:
                os.replace(json_stage, path)
                plan.revision = _revision(plan)
                try:
                    os.replace(html_stage, path.parent / "review.html")
                except OSError as exc:
                    raise ReviewError("Decision JSON saved, but the HTML view could not be refreshed; run render to repair it") from exc
    except Timeout as exc:
        raise ReviewError("Review is being edited by another process; retry after it finishes") from exc


def refresh_view(path: Path) -> None:
    from .rendering import view_chunks

    try:
        with FileLock(str(path) + ".lock", timeout=0):
            plan = load_review(path)
            with staged_chunks(path, view_chunks(plan)) as staged:
                os.replace(staged, path.parent / "review.html")
    except Timeout as exc:
        raise ReviewError("Review is being edited by another process; retry the view refresh after it finishes") from exc


def status(plan: dict) -> dict:
    duplicates = [group for group in plan["groups"] if len(group["members"]) > 1]
    return {
        "sources": len(plan["snapshot"]["sources"]),
        "records": len(plan["snapshot"]["records"]),
        "groups": len(plan["groups"]),
        "duplicate_groups": len(duplicates),
        "pending": sum(group["decision"]["action"] == "pending" for group in plan["groups"]),
        "warning_count": sum(len(source["warnings"]) for source in plan["snapshot"]["sources"]) + sum(len(record["warnings"]) for record in plan["snapshot"]["records"]),
    }
