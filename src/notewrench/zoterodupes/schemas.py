# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Library Bridge contributors (see licenses/zotero-dupes-MIT.txt)
"""Self-contained JSON Schemas for local artifact interchange.

These describe emitted artifacts. Fingerprint/reference and decision-consistency
checks remain semantic checks in decisions.py; JSON Schema alone cannot do them.
"""

from copy import deepcopy

KINDS = ("review", "decisions", "correspondences", "suggestions", "carryover-audit")


def artifact_schema(kind: str) -> dict:
    if kind not in KINDS:
        raise ValueError(f"Unknown artifact kind: {kind}")

    def text(pattern=None):
        return {"type": "string", **({"pattern": pattern} if pattern else {})}

    def array(items, **options):
        return {"type": "array", "items": items, **options}

    def obj(properties, required=None, **options):
        return {"type": "object", "properties": properties, "required": list(properties) if required is None else required, **options}

    item_id = text(r"^item-[0-9a-f]{24}$")
    pair_id = text(r"^pair-[0-9a-f]{24}$")
    correspondence_id = text(r"^correspondence-[0-9a-f]{24}$")
    review_id = text(r"^review-[0-9a-f]{32}$")
    preferred = {"anyOf": [item_id, {"type": "null"}]}
    decision = obj({"status": {"enum": ["unreviewed", "same", "different", "defer"]},
                    "preferred": preferred, "comment": {"type": "string", "maxLength": 10_000}}, required=["status"])
    identity = obj({"id": item_id, "library": text(), "source_id": text(), "identity_quality": text(), "fingerprint": text(r"^[0-9a-f]{64}$")})
    record = deepcopy(identity)
    record["properties"].update({"metadata": {"type": "object"}, "raw": {"type": "object"},
                                  **{name: array({}) for name in ["tags", "notes", "annotations", "attachments"]}})
    record["required"] = list(record["properties"])
    variant = obj({"value": {}, "sources": array(item_id, minItems=1, uniqueItems=True)})
    counts = obj({status: {"type": "integer", "minimum": 0} for status in ["unreviewed", "same", "different", "defer"]})
    base = {"schema_version": {"type": "integer", "const": 1}, "review_id": review_id}
    common = {**base, "decision_counts": counts}
    if kind == "review":
        pair = obj({"id": pair_id, "left": item_id, "right": item_id,
                    "title_similarity": {"type": "number", "minimum": 0, "maximum": 100},
                    "reasons": array(text()), "warnings": array(text()),
                    "bib_dedupe_label": {"enum": [None, "maybe", "duplicate"]}})
        schema = obj({**base,
                      "libraries": array(obj({"label": text()}, required=["label"]), minItems=2),
                      "records": {"type": "object", "propertyNames": item_id, "additionalProperties": record},
                      "pairs": array(pair),
                      "clusters": array(obj({"id": text(r"^cluster-[0-9a-f]{24}$"), "members": array(item_id, minItems=2, uniqueItems=True), "pairs": array(pair_id, minItems=1, uniqueItems=True)})),
                      "matching": obj({"engine": {"const": "bib-dedupe"}, "version": text()}, required=["engine", "version"])})
    elif kind == "decisions":
        schema = obj({**base, "decisions": {"type": "object", "propertyNames": pair_id, "additionalProperties": decision}})
    elif kind == "correspondences":
        mapping = obj({"correspondence_id": correspondence_id, "pair_id": pair_id,
                       "left": identity, "right": identity, "preferred": preferred, "comment": text()})
        schema = obj({**common, "correspondences": array(mapping)})
    elif kind == "suggestions":
        suggestion = obj({"correspondence_id": correspondence_id, "pair_id": pair_id,
                          "action": {"const": "manual-review-only"}, "preferred": preferred,
                          "field_differences": {"type": "object", "additionalProperties": array(obj({"source": item_id, "value": {}}), minItems=2, maxItems=2)},
                          "missing_field_suggestions": array(obj({"field": text(), "value": {}, "source": item_id})),
                          **{name: array(variant) for name in ["tag_variants", "preserve_notes", "preserve_annotations", "preserve_attachment_metadata"]},
                          "source_records": array(record, minItems=2, maxItems=2),
                          "candidate_warnings": array(text()), "instructions": text()})
        schema = obj({**common, "suggestions": array(suggestion)})
    else:
        schema = obj({"schema_version": {"type": "integer", "const": 1}, "old_review_id": review_id, "new_review_id": review_id,
                      "carried": array(pair_id, uniqueItems=True), "new_pairs": array(pair_id, uniqueItems=True),
                      "reset_changed": array(obj({"pair_id": pair_id, "changed_items": array(item_id, minItems=1), "previous_decision": decision})),
                      "absent_from_new": array(obj({"pair_id": pair_id, "previous_decision": decision}))})
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": f"urn:library-bridge:schema:{kind}:1", "title": f"Library Bridge {kind} v1", **schema}

