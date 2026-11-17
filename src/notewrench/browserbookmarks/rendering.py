# SPDX-License-Identifier: MIT
# Copyright (c) 2026 bookmark-receipts contributors (see licenses/browser-bookmarks-MIT.txt)
"""Static, escaped, network-free review reports (no active bookmark links)."""

from __future__ import annotations
import html
import json
from collections.abc import Iterator
from pathlib import Path
from .review import FIELDS, status

MAX_VIEW_BYTES = 128 * 1024 * 1024


def _show(value) -> str:
    if value is None:
        return "<span class=muted>absent</span>"
    if isinstance(value, (list, dict)):
        value = json.dumps(value, ensure_ascii=False, indent=2)
    return "<pre>" + html.escape(str(value)) + "</pre>"


def _iter_review(plan: dict) -> Iterator[str]:
    summary = status(plan)
    yield from [
        "<!doctype html><html lang=en><meta charset=utf-8>",
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">",
        "<meta name=viewport content='width=device-width,initial-scale=1'><title>Bookmark merge review</title>",
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem;color:#152329;background:#f5f6f3}h1,h2,h3{line-height:1.2}section{background:white;padding:1.2rem;margin:1.5rem 0;border:1px solid #cdd5d1;border-radius:8px}table{border-collapse:collapse;width:100%;table-layout:fixed}th,td{padding:.6rem;text-align:left;vertical-align:top;border:1px solid #ccd5d1;overflow-wrap:anywhere}th{background:#e8eeea}pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;font:14px/1.5 ui-monospace,monospace}.conflict{background:#fff0d6}.muted{color:#66756e}.pending{color:#9b3c10}.warnings{border-left:4px solid #b35d16;padding-left:1rem}code{overflow-wrap:anywhere}details{margin-top:1rem}summary{cursor:pointer}.scroll{overflow:auto}@media(max-width:600px){body{font-size:14px}th,td{padding:.3rem}section{padding:.6rem}}</style>",
        "<h1>Bookmark merge review</h1>",
        f"<p>{summary['records']} original records · {summary['duplicate_groups']} duplicate groups · <strong class=pending>{summary['pending']} pending</strong></p>",
        "<p>This is a read-only view. Use <code>notewrench browser-bookmarks review review.json</code>, <code>decide</code>, or edit only each group's <code>decision</code> in JSON. Apply refuses pending duplicate groups.</p>",
        "<p>Default merge proposal: keep every distinct description/note with source labels, union tags exactly, choose the first URL/title/folder, earliest added/latest modified dates, private if any source is private or unspecified, unread if any source is unread. Every original stays in the receipt and copied source files.</p>",
        "<section><h2>Inputs</h2><ul>",
    ]
    for source in plan["snapshot"]["sources"]:
        yield f"<li><strong>{html.escape(source['id'])}</strong>: {html.escape(source['name'])} ({source['format']}, {source['bytes']:,} bytes)<br><code>{source['sha256']}</code></li>"
        yield from (
            f"<li class=warnings>{html.escape(warning)}</li>"
            for warning in source["warnings"]
        )
    yield "</ul></section>"
    records = {record["id"]: record for record in plan["snapshot"]["records"]}
    for group in plan["groups"]:
        duplicate = len(group["members"]) > 1
        yield f"<section id='{group['id']}'><h2>{group['id']} · {group['decision']['action']} · {len(group['members'])} source record(s)</h2><p><code>{html.escape(group['key'])}</code></p>"
        if not duplicate:
            yield "<details><summary>Single source bookmark</summary>"
        yield "<div class=scroll><table><thead><tr><th>Field</th>"
        yield from (f"<th>{html.escape(member)}</th>" for member in group["members"])
        yield "</tr></thead><tbody>"
        for field in FIELDS:
            css = " class=conflict" if field in group["conflicts"] else ""
            yield f"<tr{css}><th>{field}</th>"
            yield from (
                f"<td>{_show(records[member][field])}</td>"
                for member in group["members"]
            )
            yield "</tr>"
        yield "</tbody></table></div><h3>Decision / proposed fields</h3><dl>"
        yield "<dt>action</dt><dd>" + _show(group["decision"]["action"]) + "</dd>"
        for field, choice in group["decision"]["fields"].items():
            yield "<dt>" + field + "</dt><dd>" + _show(choice) + "</dd>"
        yield "</dl>"
        for member in group["members"]:
            record = records[member]
            for warning in record["warnings"]:
                yield f"<p class=warnings>{html.escape(member + ': ' + warning)}</p>"
            yield f"<details><summary>{html.escape(member)} original metadata</summary>{_show({'attributes': record['attributes'], 'dd_html': record['dd_html']})}</details>"
        if not duplicate:
            yield "</details>"
        yield "</section>"
    yield "<p class=muted>Conservative URL matching preserves HTTP/HTTPS, query order, tracking parameters, fragments, trailing path slashes, and percent spellings. No URLs are fetched. Browser importers may discard notes or tags; retain receipts and originals.</p></html>"
    return


def view_chunks(plan: dict):
    size = 0
    for part in _iter_review(plan):
        size += len(part.encode("utf-8")) + 1
        if size > MAX_VIEW_BYTES:
            yield "</tbody></table></div><h2>Full HTML view exceeded its size limit</h2><p>Use <code>show</code> or <code>review</code> for every field. The complete review JSON and source copies are preserved.</p></section></html>\n"
            return
        yield part + "\n"


def render_review(plan: dict) -> str:
    """Convenience string API for small reports; disk workflows stream chunks."""
    return "".join(view_chunks(plan))


def write_view(path: Path, plan: dict) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for chunk in view_chunks(plan):
            handle.write(chunk)
