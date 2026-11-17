# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (MIT; see licenses/bookmark-migration-MIT.txt)
"""Markdown, JSON and self-contained HTML reports, written without overwriting inputs."""

import html
import json
import os
import re
import tempfile
from pathlib import Path

from .model import AuditError

LABELS = {"title": "Title", "tags": "Tags", "folders": "Folder path", "notes": "Notes", "saved_at": "Exported save time", "archived": "Archived", "attachments": "Attachment references"}
STATUSES = {"equal": "equal", "conflict": "conflict", "not_comparable": "not applicable / not comparable"}
VERDICTS = {"differences_found": "Differences found; review needed", "incomplete": "No differences in the comparable fields, but the audit scope is incomplete", "no_differences_in_scope": "No differences within the declared scope"}
SUMMARY_LABELS = {
    "source_entries": "Source URL entries", "target_entries": "Target URL entries", "matched": "Matched", "missing": "Missing", "added": "Added",
    "source_duplicate_groups": "Source duplicate URL groups", "target_duplicate_groups": "Target duplicate URL groups", "conflicts": "Field conflicts",
    "not_comparable": "Field comparisons not possible", "ambiguous_matches": "Ambiguous pairings", "raw_url_differences": "Matches whose raw URLs differ",
    "source_skipped": "Source entries not audited", "target_skipped": "Target entries not audited", "attachment_local_missing": "Local attachments missing",
    "attachment_local_not_checked": "Attachments not checked locally",
}
SIDES = {"source": "Source", "target": "Target"}
LOCAL_STATUSES = {"present": "present locally", "missing": "missing locally", "not_checked": "not checked (no local path)"}
TITLE = "Bookmark migration audit report"


def pretty(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def value_of(field: dict) -> str:
    return pretty(field["value"]) if field["status"] == "available" else "not comparable: " + field["reason"]


def sections(report: dict):
    """Plain text rows shared by Markdown and HTML, always escaped by renderers."""
    s = report["summary"]
    yield "Summary", ["Item", "Count"], [[SUMMARY_LABELS[key], str(value)] for key, value in s.items()]
    yield "Field coverage (matched entries only)", ["Field", "Equal", "Conflict", "Not comparable"], [[LABELS[name], str(c["equal"]), str(c["conflict"]), str(c["not_comparable"])] for name, c in report["coverage"].items()]
    yield "Missing (including fewer copies of a duplicate URL)", ["Source location", "Raw URL", "Title"], [[e["location"], e["url"], value_of(e["fields"]["title"])] for e in report["missing"]]
    yield "Added", ["Target location", "Raw URL", "Title"], [[e["location"], e["url"], value_of(e["fields"]["title"])] for e in report["added"]]
    yield "Duplicate URL groups", ["Side", "Match key", "Count", "Locations"], [[SIDES[side], d["key"], str(d["count"]), pretty(d["locations"])] for side in ("source", "target") for d in report["duplicates"][side]]
    yield "Local attachment presence (user manifest only)", ["Side / location", "Reference", "Relative path", "Result"], [[f'{SIDES[c["side"]]} / {c["location"]}', c["reference"], c["path"] or "not given", LOCAL_STATUSES[c["local_status"]]] for c in report["attachment_checks"]]
    for side in ("source", "target"):
        export = report[side]
        yield f"{SIDES[side]} entries not audited", ["Location", "Reason"], [[e["location"], e["reason"]] for e in export["skipped"]]
        yield f"{SIDES[side]} input notes", ["Note"], [[w] for w in export["warnings"]]


def md_cell(value: str) -> str:
    # Variable-length code spans keep arbitrary input from becoming Markdown links/HTML.
    text = str(value).replace("\r", "\\r").replace("\n", "\\n").replace("|", "\\|")
    longest = max((len(m.group()) for m in re.finditer(r"`+", text)), default=0)
    delimiter = "`" * (longest + 1)
    return delimiter + " " + text + " " + delimiter


def markdown_report(report: dict) -> str:
    parts = ["# " + TITLE, "", "**" + VERDICTS[report["verdict"]] + "**", "", report["scope"], "",
             f'Source: {md_cell(report["source"]["path"])} ({report["source"]["format"]})', "",
             f'Target: {md_cell(report["target"]["path"])} ({report["target"]["format"]})', "",
             "URL policy: " + report["policy"]["url_mode"] + ". " + report["policy"]["url_rule"], "",
             "Duplicate pairing: " + report["policy"]["pairing"], ""]
    for title, headers, rows in sections(report):
        parts.extend(["## " + title, ""])
        if not rows:
            parts.extend(["None.", ""])
            continue
        parts.extend(["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"])
        parts.extend("| " + " | ".join(md_cell(cell) for cell in row) + " |" for row in rows)
        parts.append("")
    parts.extend(["## Matched entries, field by field", ""])
    for match in report["matched"]:
        parts.extend([f'### {match["source_location"]} → {match["target_location"]}', "",
                      "Source URL: " + md_cell(match["source_url"]), "", "Target URL: " + md_cell(match["target_url"]), "",
                      "Raw URL: " + ("identical." if match["raw_url_equal"] else "different; equivalent only under the selected normalization."), "",
                      "Pairing: " + ("duplicate group, identity ambiguous; check by hand." if match["ambiguous"] else "unique match."), "",
                      "| Field | Result | Source | Target | Reason |", "| --- | --- | --- | --- | --- |"])
        for name, field in match["fields"].items():
            parts.append("| " + " | ".join(md_cell(v) for v in [LABELS[name], STATUSES[field["status"]], value_of(field["source"]), value_of(field["target"]), field.get("reason", "")]) + " |")
        parts.append("")
    parts.extend(["## Traceability and scope", "", "report.json holds the raw data, HTML line number and A[n] / $.bookmarks[n] location of every input entry, and every comparison result.", ""])
    parts.extend("- " + line for line in report["limitations"])
    return "\n".join(parts) + "\n"


def html_report(report: dict) -> str:
    esc = html.escape
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width, initial-scale=1">',
             '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; base-uri \'none\'; form-action \'none\'">',
             '<title>' + esc(TITLE) + '</title><style>body{font:16px/1.65 system-ui,sans-serif;margin:0;background:#f5f7fa;color:#162338}main{max-width:1100px;margin:auto;padding:32px}h1{font-size:32px}h2{margin-top:36px}table{border-collapse:collapse;width:100%;background:white;font-size:14px}th,td{text-align:left;border:1px solid #d6deea;padding:9px;vertical-align:top;overflow-wrap:anywhere;white-space:pre-wrap}th{background:#eaf0f8}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:16px}details{margin:14px 0;background:white;padding:12px;border:1px solid #d6deea;border-radius:6px}summary{cursor:pointer;font-weight:600}.verdict{background:#e2edf9;padding:16px;border-left:5px solid #315b85}.wrap{overflow:auto}code{overflow-wrap:anywhere}footer{color:#526079}</style><main>',
             '<h1>' + esc(TITLE) + '</h1>', '<p class="verdict">' + esc(VERDICTS[report["verdict"]]) + '</p>',
             '<p>' + esc(report["scope"]) + '</p>']
    for side in ("source", "target"):
        parts.append('<p>' + esc(SIDES[side] + ': ' + report[side]["path"] + ' (' + report[side]["format"] + ')') + '</p>')
    parts.append('<p>URL policy: ' + esc(report["policy"]["url_mode"] + '. ' + report["policy"]["url_rule"]) + '</p>')
    parts.append('<p>Duplicate pairing: ' + esc(report["policy"]["pairing"]) + '</p>')

    def table(headers, rows):
        if not rows:
            return '<p>None.</p>'
        return '<div class="wrap"><table><thead><tr>' + ''.join('<th>' + esc(str(h)) + '</th>' for h in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join('<td>' + esc(str(v)) + '</td>' for v in row) + '</tr>' for row in rows) + '</tbody></table></div>'

    for title, headers, rows in sections(report):
        parts.extend(['<h2>' + esc(title) + '</h2>', table(headers, rows)])
    parts.append('<h2>Matched entries, field by field</h2>')
    for match in report["matched"]:
        title = match["source_location"] + ' → ' + match["target_location"]
        if match["ambiguous"]:
            title += ' · duplicate group, pairing ambiguous'
        parts.extend(['<details><summary>' + esc(title) + '</summary>',
                      '<p>Source raw URL: <code>' + esc(match["source_url"]) + '</code></p>',
                      '<p>Target raw URL: <code>' + esc(match["target_url"]) + '</code></p>',
                      '<p>Raw URL ' + ('identical' if match["raw_url_equal"] else 'different; equivalent only under the selected policy') + '</p>'])
        rows = [[LABELS[name], STATUSES[f["status"]], value_of(f["source"]), value_of(f["target"]), f.get("reason", "")] for name, f in match["fields"].items()]
        parts.extend([table(["Field", "Result", "Source", "Target", "Reason"], rows), '</details>'])
    parts.extend(['<h2>Traceability and scope</h2><ul>', *('<li>' + esc(line) + '</li>' for line in report["limitations"]), '</ul>',
                  '<details><summary>Full JSON (raw entries, HTML line numbers and comparison results)</summary><pre>', esc(json.dumps(report, ensure_ascii=False, indent=2)), '</pre></details>',
                  '<footer>Generated offline · no external resources · no bookmark URL was opened · notewrench bookmark-migration ' + esc(str(report["tool_version"])) + '</footer></main></html>'])
    return '\n'.join(parts) + '\n'


def write_reports(report: dict, directory: Path, formats: list[str], force: bool, protected: list[Path]):
    renderers = {"json": lambda r: json.dumps(r, ensure_ascii=False, indent=2) + "\n", "md": markdown_report, "html": html_report}
    destinations = [directory / ("report." + fmt) for fmt in formats]
    protected_resolved = {path.resolve() for path in protected}
    for dest in destinations:
        same_input = dest.exists() and any(path.exists() and dest.samefile(path) for path in protected)
        if dest.resolve() in protected_resolved or same_input:
            raise AuditError(f"a report must not overwrite an input file or a declared attachment: {dest}")
        if dest.is_symlink():
            raise AuditError(f"report target must not be a symlink: {dest}")
        if dest.exists() and not force:
            raise AuditError(f"report already exists: {dest}; choose a new directory or pass --force to overwrite reports")
        if dest.exists() and not dest.is_file():
            raise AuditError(f"report target is not a regular file: {dest}")
    pending = []
    try:
        directory.mkdir(parents=True, exist_ok=True)
        for fmt, dest in zip(formats, destinations):
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, prefix=".audit-", delete=False) as handle:
                pending.append((Path(handle.name), dest))
                handle.write(renderers[fmt](report))
        for temporary, dest in pending:
            if force:
                os.replace(temporary, dest)
            else:
                # Exclusive creation even when another audit targets this directory concurrently.
                os.link(temporary, dest)
                temporary.unlink()
    except OSError as exc:
        raise AuditError(f"cannot write reports to {directory}: {exc}") from exc
    finally:
        for temporary, _ in pending:
            temporary.unlink(missing_ok=True)
    return destinations
