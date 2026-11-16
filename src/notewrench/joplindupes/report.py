# SPDX-License-Identifier: AGPL-3.0-only
"""Deterministic reports. Markdown carries the complete JSON evidence."""
import json
import re

from .reader import ExportError

MAX_REPORT_BYTES = 64 * 1024 * 1024


def inspect_exact_group(report: dict, selector: str) -> dict:
    """Select an existing complete exact group; never regroup a subset."""
    if not re.fullmatch(r"[0-9a-f]{64}", selector):
        raise ExportError("--inspect-exact-group requires a full 64-lowercase-hex signature from exact_duplicates")
    group = next((g for g in report["exact_duplicates"] if g["signature"] == selector), None)
    if group is None:
        raise ExportError(f"Unknown exact group in --inspect-exact-group: {selector}")
    members = set(group["members"])
    notes = {n["key"]: n for n in report["notes"] if n["key"] in members}
    return {
        **{field: report[field] for field in
           ("schema_version", "tool", "read_only", "policy", "target_pair",
            "sources", "summary", "metrics", "warnings")},
        "view": "exact_group_inspection",
        "scope": {"analysis": "all_inputs", "selection": "one_existing_exact_group",
                  "relationships": "complete_groups_intersecting_selected_group"},
        "notice": "Focused exact-group view; not a complete global inventory. Summary, warnings and exit status "
                  "describe all inputs. Review all conflicts; a human keeper decision and explicit approval "
                  "are required before merging. This view selects no keeper and grants no approval.",
        "selected_signature": selector,
        "group": group,
        "notes": [notes[key] for key in group["members"]],
        **{field: [g for g in report[field] if members.intersection(g["members"])]
           for field in ("near_candidates", "identity_conflicts")},
    }


def inspect_note(report: dict, selector: str) -> dict:
    """Project one exact occurrence from a completed all-input assessment.

    Keep containing groups intact: filtering their members or reanalyzing a
    subset would lose conflicts and global target-correspondence evidence.
    """
    if not re.fullmatch(r"s[1-9][0-9]*:[0-9a-f]{32}", selector):
        raise ExportError("--inspect-note requires sN:<32-lowercase-hex-note-id> (for example s1:...)")
    source = selector.split(":", 1)[0]
    if not any(s["key"] == source for s in report["sources"]):
        raise ExportError(f"Unknown source in --inspect-note: {source}")
    note = next((n for n in report["notes"] if n["key"] == selector), None)
    if note is None:
        raise ExportError(f"Unknown note in --inspect-note: {selector}")
    groups = {field: [g for g in report[field] if selector in g["members"]]
              for field in ("exact_duplicates", "near_candidates", "identity_conflicts")}
    return {
        **{field: report[field] for field in
           ("schema_version", "tool", "read_only", "policy", "target_pair",
            "sources", "summary", "metrics", "warnings")},
        "view": "note_inspection",
        "scope": {"analysis": "all_inputs", "selection": "one_source_qualified_note",
                  "relationships": "complete_groups_containing_selected_note"},
        "notice": "Focused note view; not a complete global inventory. Summary, warnings and exit status "
                  "describe all inputs. Review only; no keeper or merge approval is selected.",
        "selected_key": selector,
        "assessment_status": "blocked" if note["blocked_reasons"] else "assessed",
        "has_review_relationships": any(groups.values()),
        "note": note,
        # Literal occurrences only: equivalence candidates are not references.
        # Retain full-report note order, per-note indices and repeated links,
        # including blocked, cyclic and self references without reassessment.
        "incoming_note_links": [
            {"referring_key": referring["key"], "link_index": index, "link": link}
            for referring in report["notes"]
            for index, link in enumerate(referring["note_links"])
            if link["target_key"] == selector
        ],
        **groups,
    }


def render_json(report: dict, max_bytes: int = MAX_REPORT_BYTES) -> str:
    chunks, size = [], 1  # Include terminal newline in the budget.
    encoder = json.JSONEncoder(ensure_ascii=False, indent=2, sort_keys=True)
    for chunk in encoder.iterencode(report):
        size += len(chunk.encode("utf-8"))
        if size > max_bytes:
            raise ExportError("Report size limit exceeded (64 MiB); split inputs into smaller snapshots")
        chunks.append(chunk)
    return "".join(chunks) + "\n"


def render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = ["# Joplin duplicate review", "", report["policy"], "",
             f"Notes: {summary['notes']}; exact groups: {summary['exact_groups']}; "
             f"near groups: {summary['near_groups']}; blocked notes: {summary['blocked_notes']}.", ""]
    if report.get("target_pair"):
        lines += ["Target correspondence pair: " + ", ".join(f"`{s}`" for s in report["target_pair"])
                  + ". Excluded sources retain global checks and complete evidence.", ""]
    lines += ["## Blocked notes", "",
              "Manual review required; blocked notes do not establish duplicate status "
              "or target correspondence. No keeper or merge approval is selected.", ""]
    blocked = [note for note in report["notes"] if note["blocked_reasons"]]
    if not blocked:
        lines.append("No blocked notes.")
    for note in blocked:
        # Keys and analyzer-generated reasons contain only safe identifiers.
        lines.append(f"- `{note['key']}`; blocked reasons: "
                     + ", ".join(f"`{reason}`" for reason in note["blocked_reasons"]) + ".")
    lines.append("")
    lines += ["## Identity conflicts", "",
              "Same note ID across snapshots does not imply equal content. "
              "Manual review required; no keeper or merge approval is selected.", ""]
    if not report["identity_conflicts"]:
        lines.append("No identity conflicts found.")
    for group in report["identity_conflicts"]:
        # IDs and metadata keys use the reader's safe identifier grammars.
        fields = ", ".join(f"`{field}`" for field in sorted(group["conflicts"])) or "none"
        lines.append(f"- Original note ID `{group['id']}`; occurrences: "
                     + ", ".join(f"`{key}`" for key in group["members"])
                     + "; conflicting fields (manual review): " + fields + ".")
    lines += ["", "## Exact duplicates", ""]
    if not report["exact_duplicates"]:
        lines.append("No exact duplicate groups found.")
    for group in report["exact_duplicates"]:
        # Metadata keys use the reader's identifier grammar: no backticks or newlines.
        fields = ", ".join(f"`{field}`" for field in sorted(group["conflicts"])) or "none"
        lines += ["- " + ", ".join(f"`{key}`" for key in group["members"])
                  + f"; conflicting fields: {len(group['conflicts'])}. Review all conflicts below."
                  + " Conflicting fields (manual review): " + fields + "."]
    lines += ["", "## Near candidates", ""]
    if not report["near_candidates"]:
        lines.append("No near candidate groups found.")
    for group in report["near_candidates"]:
        # Metadata keys use the reader's identifier grammar: no backticks or newlines.
        fields = ", ".join(f"`{field}`" for field in sorted(group["conflicts"])) or "none"
        lines.append("- " + ", ".join(f"`{key}`" for key in group["members"])
                     + "; " + ", ".join(group["reasons"])
                     + "; conflicting fields (manual comparison): " + fields + ".")
    lines += ["", "## Complete evidence", "",
              "Source IDs, note-link target evidence, tags, notebook ancestry, metadata, timestamps, state, resource hashes, "
              "unknown items and warnings are retained below. No keeper or deletion is selected.", ""]
    evidence = render_json(report)
    # A note title or unknown field may contain Markdown fences. Keep it data.
    fence = "`" * max(3, 1 + max((len(m[0]) for m in re.finditer(r"`+", evidence)), default=0))
    lines += [fence + "json", evidence.rstrip("\n"), fence, ""]
    return "\n".join(lines)
