# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (see licenses/browser-bookmarks-MIT.txt)
"""Destination checks using actual, pinned linkding rules; never change a URL."""

from __future__ import annotations

from ._linkding_rules import normalize_url, parse_tag_string

TARGET = "linkding v1.47.0"


def assess_linkding(outputs: list[dict]) -> dict:
    collisions: dict[str, list[str]] = {}
    issues = []
    for output in outputs:
        fields, output_id = output["fields"], output["id"]
        normalized = normalize_url(fields["url"])
        collisions.setdefault(normalized, []).append(output_id)
        imported_tags = parse_tag_string(",".join(fields["tags"]))
        imported_tags = [tag for tag in imported_tags if len(tag) <= 64]
        if imported_tags != sorted(fields["tags"], key=str.lower):
            issues.append({"kind": "tag_normalization", "output_id": output_id, "exported_tags": fields["tags"], "imported_tags": imported_tags})
        if "linkding:archived" in fields["tags"] and "linkding:bookmarks.archived" not in fields["tags"]:
            issues.append({"kind": "legacy_archive_tag", "output_id": output_id, "detail": "v1.31's linkding:archived marker is an ordinary tag in v1.47; add linkding:bookmarks.archived explicitly if archived state is intended"})
        for field, limit in (("url", 2048), ("title", 512)):
            if len(fields[field]) > limit:
                issues.append({"kind": "field_length", "output_id": output_id, "field": field, "length": len(fields[field]), "limit": limit})
    issues.extend({"kind": "normalized_url_collision", "normalized_url": url, "output_ids": ids} for url, ids in collisions.items() if len(ids) > 1)
    return {
        "target": TARGET,
        "scope": "Pinned URL/tag normalization and URL/title lengths; not a full Django import validation",
        "issues": issues,
    }
