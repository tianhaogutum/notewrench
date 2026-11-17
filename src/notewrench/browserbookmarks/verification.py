# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (see licenses/browser-bookmarks-MIT.txt)
"""Validate whole bundles, original coverage, and stable HTML receipt mappings."""

from __future__ import annotations

import csv
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path

from .parsing import ReviewError, hash_file, parse_html, read_limited, sha256
from .review import FIELDS, POLICY, canonical_json, load_json


def _html_lines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


class _DescriptionText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def verify_export(directory: Path) -> dict:
    manifest = load_json(directory / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("schema") != "bookmark-receipts/manifest-v1" or not isinstance(manifest.get("files"), dict):
        raise ReviewError("Invalid export manifest")
    files = manifest["files"]
    required = {"bookmarks.html", "receipts.json", "sources.csv"}
    if not required <= set(files) or len(files) > 35:
        raise ReviewError("Export manifest is missing required files or has too many files")
    actual_files = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise ReviewError("Export bundles cannot contain symlinks")
        relative_name = path.relative_to(directory).as_posix()
        if path.is_file() and relative_name != "manifest.json":
            actual_files.add(relative_name)
    if actual_files != set(files):
        raise ReviewError("Export files do not match the manifest (missing or untracked files)")
    for name, expected in files.items():
        relative = Path(name)
        if "\\" in name or relative.is_absolute() or ".." in relative.parts or relative.as_posix() != name:
            raise ReviewError("Unsafe manifest path")
        path = directory / relative
        if not path.resolve().is_relative_to(directory.resolve()):
            raise ReviewError("Manifest cannot reference files outside the bundle")
        if not isinstance(expected, str) or hash_file(path) != expected:
            raise ReviewError(f"Export checksum mismatch: {name}")
    receipt = load_json(directory / "receipts.json")
    if not isinstance(receipt, dict) or receipt.get("schema") != "bookmark-receipts/receipt-v1":
        raise ReviewError("Invalid receipt schema")
    if receipt.get("policy") != POLICY:
        raise ReviewError("Unsupported receipt URL policy")
    sources, originals, outputs = (receipt.get(field) for field in ("sources", "original_records", "outputs"))
    if not all(isinstance(value, list) for value in (sources, originals, outputs)):
        raise ReviewError("Receipt sources, original_records, and outputs must be arrays")
    if not 1 <= len(sources) <= 32:
        raise ReviewError("Receipt must contain 1 to 32 sources")
    expected_files = set(required)
    snapshot_sources = []
    for index, source in enumerate(sources, 1):
        filename = f"originals/s{index}.html"
        if not isinstance(source, dict) or source.get("id") != f"s{index}" or source.get("bundle_file") != filename:
            raise ReviewError("Invalid receipt source mapping")
        if files.get(filename) != source.get("sha256"):
            raise ReviewError("Receipt source hash does not match the manifest")
        expected_files.add(filename)
        snapshot_sources.append({**source, "bundle_file": f"sources/s{index}.html"})
    if set(files) != expected_files:
        raise ReviewError("Manifest files do not match receipt sources")
    snapshot = {"policy": receipt.get("policy"), "sources": snapshot_sources, "records": originals}
    if sha256(canonical_json(snapshot)) != receipt.get("snapshot_sha256"):
        raise ReviewError("Receipt original snapshot checksum mismatch")
    reparsed = []
    for source in sources:
        data = read_limited(directory / source["bundle_file"])
        if len(data) != source.get("bytes"):
            raise ReviewError("Receipt source byte count is incorrect")
        records, _ = parse_html(data, source["id"], source.get("format"))
        reparsed.extend(records)
    if canonical_json(reparsed) != canonical_json(originals):
        raise ReviewError("Receipt original records do not match the copied source files")
    record_ids = [record.get("id") if isinstance(record, dict) else None for record in originals]
    if any(not isinstance(value, str) for value in record_ids) or len(set(record_ids)) != len(record_ids):
        raise ReviewError("Invalid or duplicate original record IDs")
    output_ids, contributed = [], []
    for output in outputs:
        if not isinstance(output, dict) or not isinstance(output.get("id"), str) or not isinstance(output.get("source_records"), list):
            raise ReviewError("Invalid output mapping")
        if not isinstance(output.get("fields"), dict) or set(output["fields"]) != set(FIELDS):
            raise ReviewError("Invalid output fields")
        for field, value in output["fields"].items():
            if field in {"tags", "folder"}:
                valid = isinstance(value, list) and all(isinstance(item, str) for item in value)
            else:
                valid = isinstance(value, str) or (value is None and field in {"added", "modified", "private", "toread"})
            if not valid:
                raise ReviewError(f"Invalid output {field} value")
        if not output["source_records"] or not all(isinstance(value, str) for value in output["source_records"]):
            raise ReviewError("Every output must reference original source records")
        output_ids.append(output["id"])
        contributed.extend(output["source_records"])
    if len(set(output_ids)) != len(output_ids) or sorted(contributed) != sorted(record_ids):
        raise ReviewError("Every original must map exactly once, and output IDs must be unique")
    expected_rows = Counter((output["id"], member) for output in outputs for member in output["source_records"])
    previous_limit = csv.field_size_limit()
    try:
        csv.field_size_limit(128 * 1024 * 1024)
        with (directory / "sources.csv").open(encoding="utf-8", newline="") as handle:
            actual_rows = Counter((row.get("output_id"), row.get("record_id")) for row in csv.DictReader(handle))
    except (csv.Error, UnicodeError) as exc:
        raise ReviewError("Invalid source mapping CSV") from exc
    finally:
        csv.field_size_limit(previous_limit)
    if actual_rows != expected_rows:
        raise ReviewError("CSV source mappings do not match the receipt")
    parsed, _ = parse_html(read_limited(directory / "bookmarks.html", 128 * 1024 * 1024), "export", "linkding")
    html_ids = [record["attributes"].get("data-receipt") for record in parsed]
    if any(not isinstance(value, str) for value in html_ids) or sorted(html_ids) != sorted(output_ids):
        raise ReviewError("HTML receipt identifiers do not match the output mapping")
    records_by_id = {record["attributes"]["data-receipt"]: record for record in parsed}
    for output in outputs:
        record = records_by_id[output["id"]]
        for field in ("url", "title", "folder"):
            expected = output["fields"][field]
            if isinstance(expected, str):
                expected = _html_lines(expected)
            elif isinstance(expected, list):
                expected = [_html_lines(item) for item in expected]
            if record[field] != expected:
                raise ReviewError(f"{output['id']}: HTML {field} differs from the receipt")
        fields = output["fields"]
        if record["attributes"].get("tags") != _html_lines(",".join(fields["tags"])):
            raise ReviewError(f"{output['id']}: HTML tags differ from the receipt")
        for field in ("added", "modified", "private", "toread"):
            expected = fields[field]
            if isinstance(expected, str):
                expected = _html_lines(expected)
            if record[field] != expected:
                raise ReviewError(f"{output['id']}: HTML {field} differs from the receipt")
        if len(record["dd_html"]) > 1:
            raise ReviewError(f"{output['id']}: unexpected multiple HTML description blocks")
        collector = _DescriptionText()
        for fragment in record["dd_html"]:
            collector.feed(fragment)
        collector.close()
        expected_text = fields["description"]
        if fields["notes"]:
            expected_text += "[linkding-notes]" + fields["notes"] + "[/linkding-notes]"
        if "".join(collector.parts).strip() != _html_lines(expected_text).strip():
            raise ReviewError(f"{output['id']}: HTML description/notes differ from the receipt")
    return {"verified_files": len(files), "verified_outputs": len(outputs), "verified_original_records": len(originals)}
