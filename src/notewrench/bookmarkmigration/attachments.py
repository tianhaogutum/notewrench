# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (MIT; see licenses/bookmark-migration-MIT.txt)
"""Optional attachment manifest (this command's own sidecar format, schema_version 1)."""

from pathlib import Path

from .model import AuditError, Export, Field
from .parsers import parse_json, read_text, require


def attach_manifest(export: Export, path: Path | None):
    if path is None:
        return
    data = parse_json(read_text(path), path)
    require(isinstance(data, dict) and type(data.get("schema_version")) is int and data["schema_version"] == 1, str(path), "attachment manifest needs schema_version: 1")
    require(set(data) == {"schema_version", "entries"} and isinstance(data["entries"], dict), str(path), "attachment manifest allows only schema_version and an entries object")
    entries = {entry.location: entry for entry in export.entries}
    root = path.resolve().parent
    for location, refs in data["entries"].items():
        require(location in entries, str(path), f"manifest location does not exist or is not a URL entry: {location}")
        require(isinstance(refs, list), location, "attachment references must be an array")
        seen = set()
        checked = []
        for item in refs:
            require(isinstance(item, dict) and set(item) <= {"reference", "path"}, location, "each attachment must be an object with a reference and an optional path")
            reference = item.get("reference")
            require(isinstance(reference, str) and bool(reference), location, "reference must be a non-empty string")
            require(reference not in seen, location, f"duplicate attachment reference: {reference}")
            seen.add(reference)
            record = {"reference": reference, "path": item.get("path"), "local_status": "not_checked"}
            if "path" in item:
                relative = item["path"]
                require(isinstance(relative, str) and bool(relative) and "\x00" not in relative, location, "path must be a non-empty relative path")
                local = Path(relative)
                require(not local.is_absolute(), location, "attachment path must not be absolute")
                try:
                    resolved = (root / local).resolve()
                    require(resolved.is_relative_to(root), location, "attachment path escapes the manifest directory (symlinks included)")
                    record["local_status"] = "present" if resolved.is_file() else "missing"
                except (OSError, ValueError, RuntimeError) as exc:
                    raise AuditError(f"{location}: cannot check attachment {relative!r}: {exc}") from exc
            checked.append(record)
        entries[location].attachments = Field(checked)
    # Manifest is an explicit declaration of all references for all link entries.
    for location, entry in entries.items():
        if location not in data["entries"]:
            entry.attachments = Field([])
    export.metadata["attachment_manifest"] = str(path)
    export.metadata["attachment_coverage"] = "complete reference list supplied by the user; only references and local presence are checked, not content"
