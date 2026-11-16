# SPDX-License-Identifier: AGPL-3.0-only
"""Explicitly approved, offline JEX copies. Never modify source snapshots."""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile

from .analyze import LINK, _blocked_encryption, analyze
from .reader import ExportError, ID, Item, _open_anchored, _regular_open, load_export, parse_item
from .report import MAX_REPORT_BYTES, render_json


def _sources(exports):
    return [{"key": f"s{n + 1}", "path": export.path, "format": export.format,
             "fingerprint": export.fingerprint} for n, export in enumerate(exports)]


def decision_template(exports, assessment):
    template = {"schema_version": 1, "sources": _sources(exports), "groups": [
        {"signature": g["signature"], "members": g["members"], "approved": False, "keeper": None}
        for g in assessment["exact_duplicates"]]}
    if assessment.get("target_pair") is not None:
        template["target_pair"] = assessment["target_pair"]
    return template


def outside_inputs(path, exports):
    target = Path(path).resolve()
    for export in exports:
        source = Path(export.path)
        if target == source or (export.format == "raw" and target.is_relative_to(source)):
            raise ExportError("Output must be outside all inputs")


def write_new_file(path, content):
    absolute = Path(os.path.abspath(path))
    parent = _open_anchored(absolute.parent, directory=True)
    try:
        fd = os.open(absolute.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
        except BaseException:
            os.unlink(absolute.name, dir_fd=parent)
            raise
    finally:
        os.close(parent)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ExportError(f"Duplicate decision JSON key: {key}")
        result[key] = value
    return result


def read_decisions(path):
    with _regular_open(Path(os.path.abspath(path))) as stream:
        data = stream.read(MAX_REPORT_BYTES + 1)
    if len(data) > MAX_REPORT_BYTES:
        raise ExportError("Decision file exceeds 64 MiB budget")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as e:
        raise ExportError(f"Invalid decision JSON: {e}") from e


def _approved_aliases(decisions, exports, assessment):
    if (not isinstance(decisions, dict)
            or set(decisions) not in ({"schema_version", "sources", "groups"},
                                     {"schema_version", "sources", "groups", "target_pair"})
            or type(decisions["schema_version"]) is not int or decisions["schema_version"] != 1):
        raise ExportError("Unsupported decision schema")
    if decisions["sources"] != _sources(exports):
        raise ExportError("Stale decisions: source paths, order or snapshot bytes changed")
    if decisions.get("target_pair") != assessment.get("target_pair"):
        raise ExportError("Stale decisions: target correspondence scope changed")
    groups = decisions["groups"]
    if not isinstance(groups, list) or not groups:
        raise ExportError("At least one explicitly approved exact group is required")
    current = {g["signature"]: g["members"] for g in assessment["exact_duplicates"]}
    aliases, seen = {}, set()
    for group in groups:
        if (not isinstance(group, dict) or set(group) != {"signature", "members", "approved", "keeper"}
                or group["approved"] is not True or not isinstance(group["signature"], str)
                or not isinstance(group["members"], list)
                or not all(isinstance(m, str) for m in group["members"])
                or not isinstance(group["keeper"], str)):
            raise ExportError("Every decision must explicitly approve an exact group and select a keeper")
        signature, members, keeper = group["signature"], group["members"], group["keeper"]
        if (signature not in current or sorted(members) != sorted(current[signature])
                or keeper not in members or seen.intersection(members)):
            raise ExportError("Decision is not a complete current exact group, repeats members, or has an invalid keeper")
        seen.update(members)
        aliases.update({member: keeper for member in members})
    return aliases


# Supported semantic ID fields in the pinned flat export subset. Other metadata
# remains literal, except for the same explicit :/<id> URL tokens as in bodies.
REFERENCES = {1: {"parent_id": 2, "conflict_original_id": 1},
              2: {"parent_id": 2}, 6: {"note_id": 1, "tag_id": 5}}

# These reasons prevent exact comparison, but not faithful preservation. A
# target_blocked chain is safe only when every note passes this same allowlist
# and all original/output references and resource bytes validate separately.
PRESERVATION_BLOCKS = frozenset({"empty_body_insufficient_evidence",
    "note_link_ambiguous_target", "note_link_cyclic_dependency", "note_link_target_blocked"})


def _validate_references(export):
    for item in export.items.values():
        if item.type not in (1, 2, 4, 5, 6) or _blocked_encryption(item.metadata):
            raise ExportError(f"Unsupported/encrypted output item: {item.id}")
        for field, kind in REFERENCES.get(item.type, {}).items():
            value = item.metadata.get(field, "")
            if not value and not (item.type == 6):
                continue
            target = export.items.get(value)
            if not re.fullmatch(ID, value) or not target or target.type != kind:
                raise ExportError(f"Dangling/invalid {field} in item {item.id}")
        fields = {"title": item.title, "body": item.body, **{"metadata." + k: v for k, v in item.metadata.items()}}
        for field, value in fields.items():
            for match in LINK.finditer(value):
                target = export.items.get(match[1].lower())
                if not target or target.type not in (1, 4):
                    raise ExportError(f"Unreachable internal URL in {item.id} {field}: {match[0]}")
        if item.type == 4:
            blob = export.blobs.get(item.id)
            meta = item.metadata
            if not blob:
                raise ExportError(f"Missing resource file: {item.id}")
            if meta.get("encryption_blob_encrypted", "0") not in ("0", ""):
                raise ExportError(f"Encrypted resource: {item.id}")
            if meta.get("file_extension") and blob.path != f"resources/{item.id}.{meta['file_extension']}":
                raise ExportError(f"Resource extension mismatch: {item.id}")
            if meta.get("size") and (not meta["size"].isdigit() or int(meta["size"]) != blob.size):
                raise ExportError(f"Resource size mismatch: {item.id}")
    if export.warnings:
        raise ExportError("Archive contains warnings or orphan resource files")


def _serialize(item):
    sections = [item.title] if item.title or item.type != 6 else []
    if item.body:
        sections.append(item.body)
    sections.append("\n".join(f"{key}: {value}" for key, value in item.metadata.items()))
    data = "\n\n".join(sections).encode("utf-8")
    if parse_item(data, item.path) != item:
        raise ExportError(f"Item does not round-trip through the supported format: {item.id}")
    return data


def _publish(stage, output):
    """Reserve a fresh private directory only after all semantic checks pass."""
    absolute = Path(os.path.abspath(output))
    parent = _open_anchored(absolute.parent, directory=True)
    directory = None
    created = []
    reserved = False
    try:
        os.mkdir(absolute.name, mode=0o700, dir_fd=parent)
        reserved = True
        directory = os.open(absolute.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        for name in ("deduplicated.jex", "rewrite-report.json"):
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            created.append(name)
            with os.fdopen(fd, "wb") as dest, (stage / name).open("rb") as source:
                shutil.copyfileobj(source, dest)
                dest.flush()
                os.fsync(dest.fileno())
        os.fsync(directory)
    except BaseException:
        if directory is not None:
            for name in created:
                os.unlink(name, dir_fd=directory)
        if reserved:
            os.rmdir(absolute.name, dir_fd=parent)
        raise
    finally:
        if directory is not None:
            os.close(directory)
        os.close(parent)


def rewrite_archive(exports, assessment, decisions, output, limits):
    """Retain all occurrences except approved non-keepers; remap every item ID."""
    outside_inputs(output, exports)
    aliases = _approved_aliases(decisions, exports, assessment)
    blocked = [note for note in assessment["notes"] if note["blocked_reasons"]]
    if (assessment["warnings"] or assessment["unknown_items"]
            or any(reason.split(":", 1)[0] not in PRESERVATION_BLOCKS
                   for note in blocked for reason in note["blocked_reasons"])):
        raise ExportError("Archive writing requires supported, reachable evidence without warnings; "
                          "only empty-body and note-correspondence blocks may be retained")
    for export in exports:
        _validate_references(export)
    originals = {f"s{n + 1}:{item.id}": item for n, export in enumerate(exports) for item in export.items.values()}
    representatives = {key: aliases.get(key, key) for key in originals}
    # Source qualification prevents collisions across snapshots sharing original IDs.
    seed = json.dumps(_sources(exports), sort_keys=True, separators=(",", ":"))
    allocated = {key: hashlib.sha256(("joplin-dedupe:jex:v1:" + seed + ":" + key).encode()).hexdigest()[:32]
                 for key in sorted(set(representatives.values()))}
    if len(set(allocated.values())) != len(allocated):
        raise ExportError("Generated ID collision")
    id_map = {key: allocated[representative] for key, representative in representatives.items()}
    report = {"schema_version": 1, "tool": "joplin-dedupe", "sources": _sources(exports),
              "target_pair": assessment.get("target_pair"),
              "approved_groups": decisions["groups"], "id_map": [], "omitted_notes": [],
              "retained_blocked_notes": [{"old_key": note["key"], "new_id": id_map[note["key"]],
                  "blocked_reasons": note["blocked_reasons"]} for note in blocked],
              "link_rewrites": [], "validation": {}}
    items = []
    for key, original in sorted(originals.items()):
        representative = representatives[key]
        action = "retained" if representative == key else "merged_into_keeper"
        report["id_map"].append({"old_key": key, "old_id": original.id, "type": original.type,
                                 "new_id": id_map[key], "action": action, "keeper_key": representative})
        if representative != key:
            report["omitted_notes"].append({"old_key": key, "keeper_key": representative,
                                             "original": original.as_dict()})
            continue
        source = key.split(":", 1)[0]

        def replace_urls(value, field):
            def replace(match):
                target_key = f"{source}:{match[1].lower()}"
                new_url = ":/" + id_map[target_key]
                report["link_rewrites"].append({"old_key": key, "field": field, "offset": match.start(),
                    "old_url": match[0], "target_old_key": target_key, "new_url": new_url})
                return new_url
            return LINK.sub(replace, value)

        metadata = {}
        for field, value in original.metadata.items():
            if field == "id":
                value = id_map[key]
            elif field in REFERENCES.get(original.type, {}) and value:
                new_value = id_map[f"{source}:{value}"]
                report["link_rewrites"].append({"old_key": key, "field": "metadata." + field,
                    "old_id": value, "target_old_key": f"{source}:{value}", "new_id": new_value})
                value = new_value
            else:
                value = replace_urls(value, "metadata." + field)
            metadata[field] = value
        new_id = id_map[key]
        items.append(Item(new_id, original.type, replace_urls(original.title, "title"),
                          replace_urls(original.body, "body"), metadata, new_id + ".md"))

    with tempfile.TemporaryDirectory(prefix="joplin-dedupe-rewrite-") as temporary:
        stage = Path(temporary)
        (stage / "resources").mkdir()
        binary_names = []
        # Re-read through the same safe reader while copying bytes. Approvals
        # remain bound to the complete snapshots, including unreferenced files.
        for n, export in enumerate(exports):
            names = {}
            for old_id, blob in export.blobs.items():
                suffix = blob.path[len("resources/" + old_id):]
                name = "resources/" + id_map[f"s{n + 1}:{old_id}"] + suffix
                names[blob.path] = name
                binary_names.append(name)

            def copy_chunk(name, chunk):
                if name not in names:
                    raise ExportError("Source changed while copying resources")
                with (stage / names[name]).open("ab") as dest:
                    dest.write(chunk)

            reread = load_export(export.path, limits, _resource_chunk=copy_chunk)
            if reread.fingerprint != export.fingerprint:
                raise ExportError("Stale source: snapshot bytes changed while copying resources")
        archive_path = stage / "deduplicated.jex"
        with tarfile.open(archive_path, "w", format=tarfile.USTAR_FORMAT) as archive:
            for item in sorted(items, key=lambda i: i.id):
                data = _serialize(item)
                info = tarfile.TarInfo(item.path)
                info.size, info.mode = len(data), 0o600
                archive.addfile(info, io.BytesIO(data))
            for name in sorted(binary_names):
                info = tarfile.TarInfo(name)
                info.size, info.mode = (stage / name).stat().st_size, 0o600
                with (stage / name).open("rb") as stream:
                    archive.addfile(info, stream)
        result = load_export(archive_path, limits)
        _validate_references(result)
        if result.items != {item.id: item for item in items}:
            raise ExportError("Written item content failed round-trip verification")
        for n, export in enumerate(exports):
            for old_id, blob in export.blobs.items():
                copied = result.blobs[id_map[f"s{n + 1}:{old_id}"]]
                if (copied.size, copied.sha256) != (blob.size, blob.sha256):
                    raise ExportError("Written resource bytes failed verification")
        with archive_path.open("rb") as stream:
            archive_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        report["validation"] = {"reparsed": True, "all_internal_references_reachable": True,
            "items": len(result.items), "notes": sum(i.type == 1 for i in result.items.values()),
            "resources": len(result.blobs), "archive_sha256": archive_hash}
        # The combined archive is one export. Its target uniqueness can differ
        # from the source assessment; the original pair is not its scope.
        review = analyze([result])
        # Include approved non-keepers as origins too. Source reasons describe
        # the original assessment; link evidence below belongs to the output.
        source_notes = {note["key"]: note for note in assessment["notes"]}
        origins = {}
        for entry in report["id_map"]:
            if entry["type"] == 1:
                origins.setdefault(entry["new_id"], []).append({"old_key": entry["old_key"],
                    "blocked_reasons": source_notes[entry["old_key"]]["blocked_reasons"]})
        output_ids = {note["key"]: note["id"] for note in review["notes"]}
        report["post_rewrite_assessment"] = {
            "archive": "deduplicated.jex", "archive_sha256": archive_hash,
            "target_pair": review["target_pair"], "summary": review["summary"],
            "blocked_notes": [{"id": note["id"], "blocked_reasons": note["blocked_reasons"],
                               "origins": origins[note["id"]], "note_links": note["note_links"]}
                              for note in review["notes"] if note["blocked_reasons"]],
            "remaining_exact_groups": [
                {"signature": group["signature"], "action": "review_only",
                 "conflict_fields": sorted(group["conflicts"]),
                 "members": [{"id": output_ids[key], "origins": origins[output_ids[key]]}
                             for key in group["members"]]}
                for group in review["exact_duplicates"]],
            "remaining_near_candidates": [
                {"signature": group["signature"], "reasons": group["reasons"],
                 "action": "manual_comparison_required",
                 "conflict_fields": sorted(group["conflicts"]),
                 "members": [{"id": output_ids[key], "origins": origins[output_ids[key]]}
                             for key in group["members"]]}
                for group in review["near_candidates"]],
            "warnings": review["warnings"],
            "review_exit_code": 3 if (review["warnings"] or review["summary"]["blocked_notes"]
                                      or review["summary"]["unknown_items"]) else 0,
        }
        (stage / "rewrite-report.json").write_text(render_json(report), encoding="utf-8")
        _publish(stage, output)
    return report
