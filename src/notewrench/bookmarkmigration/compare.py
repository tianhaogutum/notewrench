# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (MIT; see licenses/bookmark-migration-MIT.txt)
"""Pair entries by URL key and compare their fields."""

import json
from collections import Counter, defaultdict, deque
from dataclasses import asdict

from . import __version__
from .model import Entry, Export, Field
from .urls import url_key


POLICIES = {
    "conservative": "HTTP(S) only: lower-case scheme and host, drop the scheme's default port, add / to an empty path. Query parameters (including order and empty values), fragment, path case and encoding and user info are kept as they are. No scheme upgrade, no deduplication. Other schemes compare as the original string.",
    "raw": "Exact match on the URL string after HTML entity decoding, without normalization; the original HTML attribute value is also kept in raw.",
}
LIMITATIONS = [
    "Only URL bookmarks are audited; text bookmarks, assets, empty lists, list definitions and the dynamic members of smart lists are out of scope.",
    "Karakeep createdAt is the last saved time and does not prove when a bookmark was first saved; the report compares exported time values.",
    "Native exports have no attachment list; the optional manifest proves only the declared references and the presence of local files, not their content or online assets.",
    "Duplicate URLs are paired first by raw URL plus all comparable fields, then by raw URL, then by file order; the identity of duplicates is always ambiguous, and their conflicts come from a suggested pairing.",
    "Folder paths compare by exact name and tags as an unordered set; folders are never turned into tags and import root folders are not removed.",
]


def compare_field(source: Field, target: Field, *, attachments=False) -> dict:
    result = {"source": asdict(source), "target": asdict(target)}
    if source.status != "available" or target.status != "available":
        result.update(status="not_comparable", reason="; ".join(filter(None, [source.reason, target.reason])))
    else:
        left, right = source.value, target.value
        if attachments:
            left = sorted(item["reference"] for item in left)
            right = sorted(item["reference"] for item in right)
        result.update(status="equal" if left == right else "conflict")
    return result


def exact_signature(entry: Entry, comparable: list[str]) -> str:
    fields = {key: entry.fields[key].value for key in comparable}
    # Only identity of references affects pairing; relocation of paths does not.
    if entry.attachments.status == "available":
        fields["attachment_refs"] = sorted(item["reference"] for item in entry.attachments.value)
    return json.dumps([entry.url, fields], ensure_ascii=False, sort_keys=True)


def pair_group(source: list[Entry], target: list[Entry]):
    comparable = [key for key in source[0].fields if all(e.fields[key].status == "available" for e in source + target)] if source and target else []
    pending_source = list(range(len(source)))
    pending_target = set(range(len(target)))
    pairs = []
    for key_fn in (lambda e: exact_signature(e, comparable), lambda e: e.url):
        candidates = defaultdict(deque)
        for ti in sorted(pending_target):
            candidates[key_fn(target[ti])].append(ti)
        remaining = []
        for si in pending_source:
            possible = candidates.get(key_fn(source[si]))
            if possible:
                ti = possible.popleft()
                pairs.append((si, ti))
                pending_target.remove(ti)
            else:
                remaining.append(si)
        pending_source = remaining
    rest_target = sorted(pending_target)
    count = min(len(pending_source), len(rest_target))
    pairs.extend(zip(pending_source[:count], rest_target[:count]))
    return ([(source[si], target[ti]) for si, ti in sorted(pairs)],
            [source[si] for si in pending_source[count:]],
            [target[ti] for ti in rest_target[count:]])


def compare_exports(source: Export, target: Export, mode: str = "conservative") -> dict:
    groups = [defaultdict(list), defaultdict(list)]
    for group, export in zip(groups, (source, target)):
        for entry in export.entries:
            group[url_key(entry.url, mode)].append(entry)
    source_groups, target_groups = groups
    matched, missing, added = [], [], []
    duplicates = {"source": [], "target": []}
    for side, group in zip(("source", "target"), groups):
        for key, entries in group.items():
            if len(entries) > 1:
                duplicates[side].append({"key": key, "count": len(entries), "locations": [e.location for e in entries], "raw_urls": [e.url for e in entries]})
    for key in sorted(source_groups.keys() | target_groups.keys()):
        left, right = source_groups[key], target_groups[key]
        pairs, lost, new = pair_group(left, right)
        missing.extend(entry.to_dict() for entry in lost)
        added.extend(entry.to_dict() for entry in new)
        for s_entry, t_entry in pairs:
            fields = {name: compare_field(s_entry.fields[name], t_entry.fields[name]) for name in s_entry.fields}
            fields["attachments"] = compare_field(s_entry.attachments, t_entry.attachments, attachments=True)
            matched.append({"key": key, "source_location": s_entry.location, "target_location": t_entry.location,
                            "raw_url_equal": s_entry.url == t_entry.url, "source_url": s_entry.url, "target_url": t_entry.url,
                            "ambiguous": len(left) > 1 or len(right) > 1, "fields": fields})
    coverage = {}
    for name in ("title", "tags", "folders", "notes", "saved_at", "archived", "attachments"):
        counts = Counter(match["fields"][name]["status"] for match in matched)
        coverage[name] = {status: counts[status] for status in ("equal", "conflict", "not_comparable")}
    attachment_checks = []
    for side, export in (("source", source), ("target", target)):
        for entry in export.entries:
            if entry.attachments.status == "available":
                for ref in entry.attachments.value:
                    attachment_checks.append({"side": side, "location": entry.location, **ref})
    summary = {
        "source_entries": len(source.entries), "target_entries": len(target.entries),
        "matched": len(matched), "missing": len(missing), "added": len(added),
        "source_duplicate_groups": len(duplicates["source"]), "target_duplicate_groups": len(duplicates["target"]),
        "conflicts": sum(v["conflict"] for v in coverage.values()),
        "not_comparable": sum(v["not_comparable"] for v in coverage.values()),
        "ambiguous_matches": sum(m["ambiguous"] for m in matched),
        "raw_url_differences": sum(not m["raw_url_equal"] for m in matched),
        "source_skipped": len(source.skipped), "target_skipped": len(target.skipped),
        "attachment_local_missing": sum(c["local_status"] == "missing" for c in attachment_checks),
        "attachment_local_not_checked": sum(c["local_status"] == "not_checked" for c in attachment_checks),
    }
    differences = any(summary[k] for k in ("missing", "added", "conflicts", "source_duplicate_groups", "target_duplicate_groups", "attachment_local_missing"))
    incomplete = any(summary[k] for k in ("not_comparable", "source_skipped", "target_skipped", "attachment_local_not_checked")) or not matched or bool(source.warnings or target.warnings)
    verdict = "differences_found" if differences else ("incomplete" if incomplete else "no_differences_in_scope")
    return {"schema_version": 1, "tool_version": __version__, "verdict": verdict,
            "exit_code": 1 if differences else (3 if incomplete else 0),
            "scope": "URL bookmarks and the listed fields in the two local exports only; never a pass for a whole account or for online assets",
            "policy": {"url_mode": mode, "url_rule": POLICIES[mode], "pairing": LIMITATIONS[3]},
            "summary": summary, "coverage": coverage, "matched": matched, "missing": missing, "added": added,
            "duplicates": duplicates, "attachment_checks": attachment_checks,
            "source": source.to_dict(), "target": target.to_dict(), "limitations": LIMITATIONS}
