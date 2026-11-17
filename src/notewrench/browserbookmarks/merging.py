# SPDX-License-Identifier: MIT
# Copyright (c) 2026 bookmark-receipts contributors (see licenses/browser-bookmarks-MIT.txt)
"""Apply reviewed field choices, then export through linkding's HTML producer."""

from __future__ import annotations

import csv
import html
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from . import _linkding_exporter as producer
from ._linkding_rules import parse_timestamp
from .compatibility import assess_linkding
from .parsing import NOTES_CLOSE, NOTES_OPEN, ReviewError, hash_file, read_limited, sha256
from .review import FIELDS, canonical_json, publish_directory, status, write_json

MAX_HTML_BYTES = 64 * 1024 * 1024


def _timestamp(value: str | None) -> datetime | None:
    if value is None or len(value) > 32:
        return None
    try:
        return parse_timestamp(value)
    except (ValueError, OverflowError, OSError):
        return None


def _combine(members: list[dict], field: str) -> str:
    values: dict[str, list[str]] = {}
    for record in members:
        if record[field]:
            values.setdefault(record[field], []).append(record["id"])
    return "\n\n".join(f"[Source records: {', '.join(ids)}]\n{value}" for value, ids in values.items())


def resolve_fields(group: dict, records: dict[str, dict]) -> dict:
    members = [records[record_id] for record_id in group["members"]]
    resolved = {}
    for field, choice in group["decision"]["fields"].items():
        if isinstance(choice, dict):
            value = choice["value"]
        elif choice.startswith("@"):
            value = records[choice[1:]][field]
        elif choice == "all":
            value = _combine(members, field)
        elif choice == "union":
            value = list(dict.fromkeys(tag for record in members for tag in record["tags"]))
        elif choice in {"earliest", "latest"}:
            dated = [(stamp, record[field]) for record in members if (stamp := _timestamp(record[field])) is not None]
            if dated:
                value = (min if choice == "earliest" else max)(dated, key=lambda pair: pair[0])[1]
            else:
                value = next((record[field] for record in members if record[field] is not None), None)
        elif choice == "safest":
            value = "0" if all(record["private"] == "0" for record in members) else "1"
        elif choice == "any":
            value = "1" if any(record["toread"] == "1" for record in members) else "0"
        else:  # load_review validates choices before they reach here
            raise ReviewError(f"Unsupported {field} strategy: {choice}")
        resolved[field] = list(value) if isinstance(value, list) else value
    return resolved


def make_outputs(plan: dict) -> list[dict]:
    pending = status(plan)["pending"]
    if pending:
        raise ReviewError(f"{pending} duplicate group(s) still pending; review merge/keep choices before apply")
    records = {record["id"]: record for record in plan["snapshot"]["records"]}
    outputs = []
    for group in plan["groups"]:
        action = group["decision"]["action"]
        if action == "keep":
            candidates = [({field: records[member][field] for field in FIELDS}, [member]) for member in group["members"]]
        elif action == "merge":
            candidates = [(resolve_fields(group, records), group["members"])]
        else:
            raise ReviewError(f"{group['id']}: unresolved action {action}")
        for fields, contributions in candidates:
            outputs.append({
                "id": f"b{len(outputs) + 1:06d}",
                "group_id": group["id"],
                "action": action,
                "fields": fields,
                "source_records": list(contributions),
            })
    return outputs


def _append_bookmark(doc: list[str], output: dict) -> None:
    fields = output["fields"]
    # Upstream formats dates from model objects. Browser exports may have no
    # dates, or a timestamp spelling worth preserving; explicit overrides carry
    # those original strings without inventing dates or changing units.
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    bookmark = SimpleNamespace(
        url=fields["url"], resolved_title=fields["title"],
        resolved_description=fields["description"], notes=fields["notes"],
        tag_names=list(fields["tags"]), is_archived=False,
        unread=fields["toread"] == "1", shared=fields["private"] == "0",
        date_added=epoch, date_modified=epoch,
        export_attributes={
            "ADD_DATE": fields["added"], "LAST_MODIFIED": fields["modified"],
            "PRIVATE": fields["private"], "TOREAD": fields["toread"],
            "DATA-RECEIPT": output["id"],
        },
    )
    producer.append_bookmark(doc, bookmark)


def _html_lines(outputs: list[dict]):
    # An insertion-ordered trie rebuilds selected folders, including names with
    # literal slashes. Empty folders and folder-level attributes live in originals.
    root = {"bookmarks": [], "children": {}}
    for output in outputs:
        node = root
        for name in output["fields"]["folder"]:
            node = node["children"].setdefault(name, {"bookmarks": [], "children": {}})
        node["bookmarks"].append(output)
    doc: list[str] = []
    producer.append_header(doc)
    producer.append_list_start(doc)
    yield from doc
    doc.clear()
    # Explicit stack avoids recursion errors for unusually deep bookmark folders.
    stack = [("node", root)]
    while stack:
        action, node = stack.pop()
        if action == "close":
            producer.append_list_end(doc)
            yield from doc
            doc.clear()
            continue
        if action == "open":
            doc.append(f"<DT><H3>{html.escape(node)}</H3>")
            producer.append_list_start(doc)
            yield from doc
            doc.clear()
            continue
        for output in node["bookmarks"]:
            _append_bookmark(doc, output)
            yield from doc
            doc.clear()
        for name, child in reversed(list(node["children"].items())):
            stack.extend([("close", None), ("node", child), ("open", name)])
    producer.append_list_end(doc)
    yield from doc


def html_chunks(outputs: list[dict]):
    size = 0
    for line in _html_lines(outputs):
        size += len(line.encode("utf-8")) + 1
        if size > MAX_HTML_BYTES:
            raise ReviewError("Expanded bookmark HTML exceeds the 64 MiB output budget; split the review")
        yield line + "\n"


def export_html(outputs: list[dict]) -> str:
    return "".join(html_chunks(outputs))


def write_html(path: Path, outputs: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for chunk in html_chunks(outputs):
            handle.write(chunk)


def _csv_safe(value: str) -> str:
    # CSV quoting alone does not prevent spreadsheet formula execution. The JSON
    # receipt remains exact; this human-oriented table prefixes dangerous cells.
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else value


def export_csv(path: Path, plan: dict, outputs: list[dict]) -> None:
    sources = {source["id"]: source for source in plan["snapshot"]["sources"]}
    records = {record["id"]: record for record in plan["snapshot"]["records"]}
    columns = [
        "output_id", "group_id", "action", "record_id", "source_name", "source_sha256",
        "source_index", "original_url", "original_title", "original_description", "original_notes",
        "original_tags_json", "original_folder_json",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for output in outputs:
            for record_id in output["source_records"]:
                record = records[record_id]
                source = sources[record["source_id"]]
                values = {
                    "output_id": output["id"], "group_id": output["group_id"], "action": output["action"],
                    "record_id": record_id, "source_name": source["name"], "source_sha256": source["sha256"],
                    "source_index": str(record["index"]),
                    **{f"original_{field}": record[field] for field in ("url", "title", "description", "notes")},
                    "original_tags_json": canonical_json(record["tags"]).decode(),
                    "original_folder_json": canonical_json(record["folder"]).decode(),
                }
                writer.writerow({key: _csv_safe(value) for key, value in values.items()})


def output_warnings(outputs: list[dict]) -> list[str]:
    warnings = []
    for output in outputs:
        fields = output["fields"]
        if any(marker in fields[field] for field in ("description", "notes") for marker in (NOTES_OPEN, NOTES_CLOSE)):
            warnings.append(f"{output['id']}: literal linkding markers are ambiguous to linkding's importer; originals and JSON retain exact values")
        if any(_timestamp(fields[field]) is None and fields[field] is not None for field in ("added", "modified")):
            warnings.append(f"{output['id']}: nonstandard date retained verbatim; destination importer may reject it")
        if any(tag != tag.strip() for tag in fields["tags"]) or len({tag.lower() for tag in fields["tags"]}) != len(fields["tags"]):
            warnings.append(f"{output['id']}: original tag case/spacing preserved; linkding may normalize it on import")
    return warnings


def apply_review(plan: dict, review_path: Path, destination: Path) -> dict:
    outputs = make_outputs(plan)
    warnings = output_warnings(outputs)
    compatibility = assess_linkding(outputs)
    if compatibility["issues"]:
        warnings.append(f"{len(compatibility['issues'])} compatibility issue(s) for linkding v1.47.0; see receipts.json compatibility")
    receipt = {
        "schema": "bookmark-receipts/receipt-v1",
        "snapshot_sha256": plan["snapshot_sha256"],
        "policy": plan["snapshot"]["policy"],
        "sources": [{**source, "bundle_file": source["bundle_file"].replace("sources/", "originals/", 1)} for source in plan["snapshot"]["sources"]],
        "original_records": plan["snapshot"]["records"],
        "decisions": [{"group_id": group["id"], **group["decision"]} for group in plan["groups"]],
        "outputs": outputs,
        "warnings": warnings,
        "compatibility": compatibility,
        "csv_formula_policy": "Potential formulas prefixed by apostrophe; exact originals are in this JSON and originals/",
    }
    with publish_directory(destination) as stage:
        (stage / "originals").mkdir()
        for source in plan["snapshot"]["sources"]:
            data = read_limited(review_path.parent / source["bundle_file"])
            if sha256(data) != source["sha256"]:
                raise ReviewError(f"{source['id']}: source changed during export; nothing published")
            (stage / "originals" / f"{source['id']}.html").write_bytes(data)
        write_html(stage / "bookmarks.html", outputs)
        write_json(stage / "receipts.json", receipt)
        export_csv(stage / "sources.csv", plan, outputs)
        manifest = {
            "schema": "bookmark-receipts/manifest-v1",
            "files": {path.relative_to(stage).as_posix(): hash_file(path) for path in sorted(stage.rglob("*")) if path.is_file()},
        }
        write_json(stage / "manifest.json", manifest)
    return {"outputs": len(outputs), "source_records": len(plan["snapshot"]["records"]), "warnings": warnings}
