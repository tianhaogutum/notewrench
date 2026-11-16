# SPDX-License-Identifier: AGPL-3.0-only
"""Conservative signature grouping; never proposes a deletion or a keeper."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
import json
import re

from .reader import Export, KNOWN_TYPES, normalize

LINK = re.compile(r":/([0-9a-fA-F]{32})(?![0-9a-fA-F])")
# Fields affecting task/conflict/trash semantics belong in the exact key.
STATE_DEFAULTS = {
    "is_todo": "0", "todo_due": "0", "todo_completed": "0",
    "is_conflict": "0", "conflict_original_id": "", "deleted_time": "0",
}


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _blocked_encryption(meta: dict) -> bool:
    return (meta.get("encryption_applied", "0") not in ("0", "")
            or bool(meta.get("encryption_cipher_text", ""))
            or meta.get("is_locked", "0") not in ("0", ""))


def _notebook(export: Export, parent: str) -> dict:
    chain, seen = [], set()
    while parent:
        if len(chain) >= 128:
            return {"chain": chain, "problem": "folder_depth_limit", "at": parent}
        if parent in seen:
            return {"chain": chain, "problem": "folder_cycle", "at": parent}
        seen.add(parent)
        item = export.items.get(parent)
        if not item or item.type != 2:
            return {"chain": chain, "problem": "missing_folder", "at": parent}
        chain.append({"id": item.id, "title": item.title, "metadata": item.metadata})
        parent = item.metadata.get("parent_id", "")
    return {"chain": list(reversed(chain)), "problem": None}


def _conflicts(members: list[dict]) -> dict:
    """Preserve per-occurrence values, including missing vs explicitly empty."""
    fields = {"title", "tags", "notebook", "resources", "note_links", "state",
              "normalized_body_sha256", "content_signature", "blocked_reasons"}
    for member in members:
        fields.update("metadata." + key for key in member["metadata"] if key not in ("id", "type_"))
    conflicts = {}
    for field in sorted(fields):
        if field.startswith("metadata."):
            key = field[9:]
            values = [{"key": m["key"], "present": key in m["metadata"],
                       "value": m["metadata"].get(key)} for m in members]
            distinct = {_json([v["present"], v["value"]]) for v in values}
        else:
            values = [{"key": m["key"], "value": m[field]} for m in members]
            distinct = {_json(v["value"]) for v in values}
        if len(distinct) > 1:
            conflicts[field] = values
    return conflicts


def _resolve_note_links(notes: list[dict], bodies: dict[str, list],
                        sources: list[str], target_pair: list[str] | None) -> int:
    """Finalize whole dependency layers before permitting target correspondence.

    Equal recursive signatures have equal dependency depth (assuming SHA-256
    collision resistance), so all possible counterparts are in the same layer.
    Two equal targets in any effective source make correspondence ambiguous.
    Only selected-source links use the pair; excluded links use all sources.
    Candidate inventories always include every assessable equivalent target.
    Kahn traversal leaves cycles and their incoming dependencies unassessable.
    """
    by_key = {n["key"]: n for n in notes}
    dependents = defaultdict(set)
    pending = {}
    for note in notes:
        scope = target_pair if target_pair and note["source"] in target_pair else sources
        for link in note["note_links"]:
            link.update({"correspondence_sources": scope,
                         "candidate_counts_by_source": {}, "ambiguous_sources": [],
                         "scope_ambiguous_sources": []})
        dependencies = {link["target_key"] for link in note["note_links"]
                        if link["status"] == "pending"}
        pending[note["key"]] = len(dependencies)
        for target in dependencies:
            dependents[target].add(note["key"])
    ready = sorted(key for key, count in pending.items() if not count)
    correspondences = {}
    hashes = 0

    def finalize(key, cyclic=frozenset()):
        nonlocal hashes
        note = by_key[key]
        segments = []
        for segment in bodies[key]:
            if segment[0] != "note_link":
                segments.append(segment)
                continue
            link = segment[1]
            target_key = link["target_key"]
            target = by_key.get(target_key)
            if link["status"] == "unresolved":
                pass  # Original unresolved_internal_link reason is already kept.
            elif target_key in cyclic:
                link["status"] = "cyclic_dependency"
            elif target["exact_signature"] is None:
                link["status"] = "target_blocked"
            else:
                candidates, counts = correspondences[target["exact_signature"]]
                link["candidate_targets"] = candidates
                link["candidate_counts_by_source"] = dict(counts)
                link["ambiguous_sources"] = [s for s in sources if counts[s] > 1]
                link["scope_ambiguous_sources"] = [s for s in link["correspondence_sources"]
                                                   if counts[s] > 1]
                unique = not link["scope_ambiguous_sources"]
                link["status"] = "normalized" if unique else "ambiguous_target"
                if unique:
                    link["target_exact_signature"] = target["exact_signature"]
            if link["status"] == "normalized":
                segments.append(["note_content", link["target_exact_signature"]])
            else:
                note["blocked_reasons"].append(f"note_link_{link['status']}:{link['id']}")
                # Preserve occurrence identity when content correspondence is unproven.
                segments.append(["note", note["source"], link["id"]])
        note["blocked_reasons"] = sorted(set(note["blocked_reasons"]))
        note["content_signature"] = _hash([note["metadata"].get("markup_language", "1"), segments])
        note["exact_signature"] = (None if note["blocked_reasons"] else
                                   _hash([note["content_signature"], note["state"]]))
        hashes += 1 + (note["exact_signature"] is not None)

    while ready:
        layer = defaultdict(list)
        for key in ready:
            finalize(key)
            signature = by_key[key]["exact_signature"]
            if signature:
                layer[signature].append(by_key[key])
        for signature, members in layer.items():
            counts = Counter(n["source"] for n in members)
            correspondences[signature] = ([n["key"] for n in members], counts)
        following = []
        for key in ready:
            del pending[key]
            for dependent in dependents[key]:
                pending[dependent] -= 1
                if not pending[dependent]:
                    following.append(dependent)
        ready = sorted(following)
    cyclic = frozenset(pending)
    for key in sorted(cyclic):
        finalize(key, cyclic)
    return hashes


def analyze(exports: list[Export], *, target_pair: list[str] | tuple[str, str] | None = None) -> dict:
    """Assess all exports; optionally limit selected sources' target uniqueness.

    A pair is unordered and uses the original input keys (s1, s2, ...). It
    never filters inventories or grants permission to merge ambiguous targets.
    """
    if not exports:
        raise ValueError("At least one export is required")
    sources = [f"s{i + 1}" for i in range(len(exports))]
    if target_pair is not None:
        if (not isinstance(target_pair, (list, tuple)) or len(target_pair) != 2
                or not all(isinstance(s, str) and s in sources for s in target_pair)
                or target_pair[0] == target_pair[1]):
            raise ValueError("Target pair must name two distinct input source keys (s1, s2, ...)")
        target_pair = [s for s in sources if s in target_pair]
    notes, unknown, warnings, non_notes = [], [], [], []
    content_buckets: dict[str, list[dict]] = defaultdict(list)
    exact_buckets: dict[str, list[dict]] = defaultdict(list)
    identity: dict[str, list[dict]] = defaultdict(list)
    body_hashes = 0
    note_hashes = 0
    bodies = {}

    for source_index, export in enumerate(exports):
        source = f"s{source_index + 1}"
        for warning in export.warnings:
            warnings.append({"source": source, **warning})
        tag_map: dict[str, list[dict]] = defaultdict(list)
        for item in sorted(export.items.values(), key=lambda x: x.id):
            if item.type == 6:
                note_id, tag_id = item.metadata.get("note_id", ""), item.metadata.get("tag_id", "")
                note, tag = export.items.get(note_id), export.items.get(tag_id)
                if not note or note.type != 1 or not tag or tag.type != 5:
                    warnings.append({"source": source, "code": "dangling_note_tag", "id": item.id,
                                     "metadata": item.metadata})
                tag_map[note_id].append({"id": tag_id, "title": tag.title if tag else None,
                                         "metadata": tag.metadata if tag else None,
                                         "relation_id": item.id, "relation_metadata": item.metadata})
            if item.type not in KNOWN_TYPES:
                unknown.append({"source": source, **item.as_dict()})
            elif item.type != 1:
                non_notes.append({"source": source, **item.as_dict()})
        for item in sorted(export.items.values(), key=lambda x: x.id):
            if item.type != 1:
                continue
            meta = item.metadata
            blocked = []
            if _blocked_encryption(meta):
                blocked.append("encrypted_or_locked_note")
            markup = meta.get("markup_language", "1")
            if markup != "1":
                blocked.append("unsupported_markup_language")
            state = {key: meta.get(key, default) for key, default in STATE_DEFAULTS.items()}
            for key in ("is_todo", "is_conflict"):
                if state[key] not in ("0", "1"):
                    blocked.append(f"invalid_state:{key}")
            for key in ("todo_due", "todo_completed", "deleted_time"):
                if not re.fullmatch(r"[0-9]+", state[key]):
                    blocked.append(f"invalid_state:{key}")
            notebook = _notebook(export, meta.get("parent_id", ""))
            if notebook["problem"]:
                warnings.append({"source": source, "id": item.id, "code": notebook["problem"]})
            body = normalize(item.body)
            if not body.strip():
                blocked.append("empty_body_insufficient_evidence")
            resources, note_links, segments, shape = [], [], [], []
            cursor = 0
            # Scan all literal internal-URL tokens, even in code. Over-recognition
            # can reject exactness; it cannot hide an unresolved attachment.
            for match in LINK.finditer(body):
                linked_id = match[1].lower()
                target = export.items.get(linked_id)
                segments.append(["text", body[cursor:match.start()]])
                shape.append(["text", body[cursor:match.start()]])
                if target and target.type == 1:
                    link = {"id": linked_id, "original_url": match[0],
                            "target_key": f"{source}:{linked_id}", "status": "pending",
                            "target_exact_signature": None, "candidate_targets": []}
                    note_links.append(link)
                    segments.append(["note_link", link])
                else:
                    resource = {"id": linked_id, "metadata": target.metadata if target else None,
                                "title": target.title if target else None}
                    blob = export.blobs.get(linked_id)
                    problem = None
                    if not target or target.type != 4:
                        problem = "unresolved_internal_link"
                    elif _blocked_encryption(target.metadata) or target.metadata.get("encryption_blob_encrypted", "0") not in ("0", ""):
                        problem = "encrypted_resource"
                    elif not blob:
                        problem = "missing_resource_file"
                    elif target.metadata.get("file_extension") and blob.path != f"resources/{linked_id}.{target.metadata['file_extension']}":
                        problem = "resource_extension_mismatch"
                    elif target.metadata.get("size") and (
                        not target.metadata["size"].isdigit() or int(target.metadata["size"]) != blob.size
                    ):
                        problem = "resource_size_mismatch"
                    resource.update({"blob": asdict(blob) if blob else None, "problem": problem})
                    resources.append(resource)
                    if problem:
                        blocked.append(f"{problem}:{linked_id}")
                    if not target or target.type != 4:
                        link = {"id": linked_id, "original_url": match[0],
                                "target_key": f"{source}:{linked_id}", "status": "unresolved",
                                "target_exact_signature": None, "candidate_targets": []}
                        note_links.append(link)
                        segments.append(["note_link", link])
                    else:
                        segments.append(["resource", blob.sha256 if blob else None,
                                         target.metadata.get("mime", "")])
                shape.append(["internal_link"])
                cursor = match.end()
            segments.append(["text", body[cursor:]])
            shape.append(["text", body[cursor:]])
            shape_signature = _hash([markup, shape])
            body_hashes += 1
            note = {
                "key": f"{source}:{item.id}", "source": source, "id": item.id,
                "title": item.title, "metadata": meta, "notebook": notebook,
                "tags": tag_map.get(item.id, []), "state": state, "resources": resources,
                "note_links": note_links,
                "normalized_body_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "content_signature": None, "shape_signature": shape_signature,
                "exact_signature": None,
                "blocked_reasons": sorted(set(blocked)),
            }
            note_hashes += 2
            bodies[note["key"]] = segments
            notes.append(note)
            identity[item.id].append(note)
            # Ciphertext/HTML is reported but never compared for similarity.
            if not _blocked_encryption(meta) and markup == "1":
                content_buckets[shape_signature].append(note)

    note_hashes += _resolve_note_links(notes, bodies, sources, target_pair)
    for note in notes:
        if note["exact_signature"]:
            exact_buckets[note["exact_signature"]].append(note)

    exact = []
    for signature, members in sorted(exact_buckets.items()):
        if len(members) > 1:
            exact.append({"signature": signature, "members": [m["key"] for m in members],
                          "conflicts": _conflicts(members), "action": "review_only"})
    near = []
    for signature, members in sorted(content_buckets.items()):
        if len(members) < 2:
            continue
        exact_keys = {m["exact_signature"] for m in members}
        if len(exact_keys) == 1 and None not in exact_keys:
            continue
        reasons = []
        if len({_json(m["state"]) for m in members}) > 1:
            reasons.append("todo_conflict_or_trash_state_differs")
        if len({m["content_signature"] for m in members}) > 1:
            if any(m["note_links"] for m in members):
                reasons.append("note_link_target_differs_or_unproven")
            resource_values = {_json([[r["blob"]["sha256"] if r["blob"] else None,
                                      r["metadata"].get("mime", "") if r["metadata"] else None]
                                     for r in m["resources"]]) for m in members}
            if len(resource_values) > 1:
                reasons.append("resource_content_or_mime_differs")
        if any(m["blocked_reasons"] for m in members):
            reasons.append("incomplete_or_unsupported_evidence")
        near.append({"signature": signature, "members": [m["key"] for m in members],
                     "reasons": reasons, "conflicts": _conflicts(members),
                     "action": "manual_comparison_required"})
    identity_conflicts = []
    for item_id, members in sorted(identity.items()):
        # Source IDs can recur across snapshots. Retain occurrences separately.
        if len(members) > 1 and (len({m["content_signature"] for m in members}) > 1
                                or len({_json(m["metadata"]) for m in members}) > 1
                                or len({m["title"] for m in members}) > 1):
            identity_conflicts.append({"id": item_id, "members": [m["key"] for m in members],
                                       "conflicts": _conflicts(members)})
    return {
        "schema_version": 1, "tool": "joplin-dedupe", "read_only": True,
        "target_pair": target_pair,
        "policy": "Review only. Exact means normalized body, ordered resource bytes/MIME, unambiguous acyclic note-link target content/state and task/conflict/trash state agree. Metadata conflicts still require review.",
        "sources": [{"key": f"s{i + 1}", "path": e.path, "format": e.format, "stats": e.stats}
                    for i, e in enumerate(exports)],
        "summary": {"notes": len(notes), "exact_groups": len(exact), "near_groups": len(near),
                    "blocked_notes": sum(bool(m["blocked_reasons"]) for m in notes),
                    "unknown_items": len(unknown), "identity_conflicts": len(identity_conflicts)},
        "metrics": {"note_signatures": body_hashes, "note_hashes": note_hashes,
                    "resource_hashes": sum(e.stats["resource_hashes"] for e in exports),
                    "resource_bytes_hashed": sum(e.stats["resource_bytes_hashed"] for e in exports)},
        "exact_duplicates": exact, "near_candidates": near, "identity_conflicts": identity_conflicts,
        "notes": notes, "non_note_items": non_notes,
        "resource_files": [{"source": f"s{i + 1}", "id": blob_id, **asdict(blob)}
                           for i, e in enumerate(exports) for blob_id, blob in sorted(e.blobs.items())],
        "unknown_items": unknown, "warnings": warnings,
    }
