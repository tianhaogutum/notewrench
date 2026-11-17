# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (see licenses/zotero-dupes-MIT.txt)
"""Validate explicit pair decisions and produce provenance-rich suggestions."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from jsonschema import Draft202012Validator, ValidationError

from .model import InputError, canonical, digest, stable_id
from .schemas import artifact_schema

STATUSES = {"unreviewed", "same", "different", "defer"}


def decision_template(review: dict) -> dict:
    return {"schema_version": 1, "review_id": review["review_id"],
            "decisions": {pair["id"]: {"status": "unreviewed", "preferred": None, "comment": ""} for pair in review["pairs"]}}


def validate_review(review: Any) -> None:
    if not isinstance(review, dict) or type(review.get("schema_version")) is not int or review.get("schema_version") != 1:
        raise InputError("Unsupported or missing review schema_version (expected 1)")
    try:
        Draft202012Validator(artifact_schema("review")).validate(review)
    except ValidationError as exc:
        path = ".".join(map(str, exc.absolute_path)) or "root"
        raise InputError(f"Invalid review at {path}: {exc.message[:300]}") from exc
    expected = "review-" + digest({k: v for k, v in review.items() if k != "review_id"})[:32]
    if review.get("review_id") != expected:
        raise InputError("Review fingerprint does not match its contents; regenerate it from the source exports")
    records = review["records"]
    labels = [library["label"] for library in review["libraries"]]
    if len(labels) != len(set(labels)):
        raise InputError("Review library labels must be unique")
    for item_id, record in records.items():
        if record.get("id") != item_id:
            raise InputError("Review record IDs do not match their map keys")
        if record["library"] not in labels:
            raise InputError(f"{item_id}: record belongs to an unlisted library")
        if stable_id("item", record["library"], record["source_id"]) != item_id:
            raise InputError(f"{item_id}: stable item ID does not match source identity")
        if record["fingerprint"] != digest({key: value for key, value in record.items() if key != "fingerprint"}):
            raise InputError(f"{item_id}: source record fingerprint does not match its contents")
    seen = set()
    for pair in review["pairs"]:
        left, right = pair["left"], pair["right"]
        if left not in records or right not in records or left == right:
            raise InputError(f"{pair['id']}: pair references missing or identical records")
        if pair["id"] in seen or stable_id("pair", *sorted([left, right])) != pair["id"]:
            raise InputError("Review pair IDs must be unique and match their source records")
        if records[left]["library"] == records[right]["library"]:
            raise InputError("Review contains a within-library candidate")
        seen.add(pair["id"])


def validate_decisions(review: dict, payload: Any) -> dict:
    if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int or payload.get("schema_version") != 1:
        raise InputError("Unsupported or missing decisions schema_version (expected 1)")
    if payload.get("review_id") != review["review_id"]:
        raise InputError("Decisions belong to a different review snapshot. Open the matching review.html and export decisions again.")
    decisions = payload.get("decisions")
    if not isinstance(decisions, dict):
        raise InputError("decisions must be an object keyed by pair ID")
    pairs = {pair["id"]: pair for pair in review["pairs"]}
    if set(decisions) - set(pairs):
        raise InputError("Decisions contain unknown pair IDs")
    result = decision_template(review)
    for pair_id, decision in decisions.items():
        if not isinstance(decision, dict) or not isinstance(decision.get("status"), str) or decision.get("status") not in STATUSES:
            raise InputError(f"{pair_id}: status must be unreviewed, same, different or defer")
        preferred = decision.get("preferred")
        if preferred is not None and (not isinstance(preferred, str) or preferred not in {pairs[pair_id]["left"], pairs[pair_id]["right"]}):
            raise InputError(f"{pair_id}: preferred must be one of this pair's item IDs, or null")
        comment = decision.get("comment", "")
        if not isinstance(comment, str) or len(comment) > 10_000:
            raise InputError(f"{pair_id}: comment must be a string of at most 10000 characters")
        result["decisions"][pair_id] = {"status": decision["status"], "preferred": preferred, "comment": comment}
    # Clusters are navigational, but explicitly saying A=B, B=C and A!=C is an
    # inconsistency worth rejecting before publishing recommendations.
    parent: dict[str, str] = {}

    def root(node: str) -> str:
        parent.setdefault(node, node)
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = parent[node]
        return node

    for pair in pairs.values():
        if result["decisions"][pair["id"]]["status"] == "same":
            parent[root(pair["left"])] = root(pair["right"])
    for pair in pairs.values():
        if result["decisions"][pair["id"]]["status"] == "different" and root(pair["left"]) == root(pair["right"]):
            raise InputError(f"{pair['id']}: contradictory decisions (marked different but linked by confirmed same-work pairs)")
    return result


def carry_decisions(old_review: dict, old_payload: dict, new_review: dict) -> tuple[dict, dict]:
    """Explicitly carry decisions only when both complete source records match.

    Stable pair IDs alone never suffice. A new note/tag/annotation or any raw
    source change invalidates carryover, even if the DOI/title still matches.
    """
    validate_review(old_review)
    validate_review(new_review)
    old = validate_decisions(old_review, old_payload)
    result = decision_template(new_review)
    old_pairs = {pair["id"]: pair for pair in old_review["pairs"]}
    new_pairs = {pair["id"]: pair for pair in new_review["pairs"]}
    audit = {"schema_version": 1, "old_review_id": old_review["review_id"], "new_review_id": new_review["review_id"],
             "carried": [], "reset_changed": [], "new_pairs": [], "absent_from_new": []}
    for pair_id, pair in new_pairs.items():
        if pair_id not in old_pairs:
            audit["new_pairs"].append(pair_id)
            continue
        changed = [item_id for item_id in [pair["left"], pair["right"]]
                   if old_review["records"][item_id]["fingerprint"] != new_review["records"][item_id]["fingerprint"]]
        if changed:
            audit["reset_changed"].append({"pair_id": pair_id, "changed_items": changed,
                                          "previous_decision": old["decisions"][pair_id]})
        else:
            result["decisions"][pair_id] = dict(old["decisions"][pair_id])
            audit["carried"].append(pair_id)
    for pair_id in old_pairs.keys() - new_pairs.keys():
        audit["absent_from_new"].append({"pair_id": pair_id, "previous_decision": old["decisions"][pair_id]})
    audit["absent_from_new"].sort(key=lambda row: row["pair_id"])
    return validate_decisions(new_review, result), audit


def _variants(left: dict, right: dict, field: str) -> list[dict]:
    grouped: dict[str, dict] = {}
    for record in [left, right]:
        for value in record.get(field, []):
            key = canonical(value)
            grouped.setdefault(key, {"value": value, "sources": []})["sources"].append(record["id"])
    return [grouped[key] for key in sorted(grouped)]


def export_confirmed(review: dict, payload: dict) -> tuple[dict, dict]:
    validate_review(review)
    decisions = validate_decisions(review, payload)
    correspondences, suggestions = [], []
    counts: dict[str, int] = defaultdict(int)
    for pair in review["pairs"]:
        decision = decisions["decisions"][pair["id"]]
        counts[decision["status"]] += 1
        if decision["status"] != "same":
            continue
        left, right = (review["records"][pair[side]] for side in ["left", "right"])
        correspondence_id = stable_id("correspondence", *sorted([left["id"], right["id"]]))
        correspondences.append({"correspondence_id": correspondence_id, "pair_id": pair["id"],
                                "left": {k: left[k] for k in ["id", "library", "source_id", "identity_quality", "fingerprint"]},
                                "right": {k: right[k] for k in ["id", "library", "source_id", "identity_quality", "fingerprint"]},
                                "preferred": decision["preferred"], "comment": decision["comment"]})
        fields, missing = {}, []
        preferred = review["records"].get(decision["preferred"])
        other = right if preferred and preferred["id"] == left["id"] else left
        for field in sorted(set(left["metadata"]) | set(right["metadata"])):
            lv, rv = left["metadata"].get(field), right["metadata"].get(field)
            if canonical(lv) != canonical(rv):
                fields[field] = [{"source": r["id"], "value": r["metadata"].get(field)} for r in [left, right]]
            if preferred and not preferred["metadata"].get(field) and other["metadata"].get(field):
                missing.append({"field": field, "value": other["metadata"][field], "source": other["id"]})
        suggestions.append({"correspondence_id": correspondence_id, "pair_id": pair["id"],
                            "action": "manual-review-only", "preferred": decision["preferred"],
                            "field_differences": fields, "missing_field_suggestions": missing,
                            "tag_variants": _variants(left, right, "tags"),
                            "preserve_notes": _variants(left, right, "notes"),
                            "preserve_annotations": _variants(left, right, "annotations"),
                            "preserve_attachment_metadata": _variants(left, right, "attachments"),
                            "source_records": [left, right], "candidate_warnings": pair["warnings"],
                            "instructions": "Review each conflicting field. Keep every distinct note and annotation with provenance. Attachment metadata does not include files. Apply any edits manually in the source tools; this file is not an import or deletion plan."})
    base = {"schema_version": 1, "review_id": review["review_id"], "decision_counts": {status: counts[status] for status in sorted(STATUSES)}}
    return ({**base, "correspondences": correspondences}, {**base, "suggestions": suggestions})

