"""Run the existing read-only analyses on detected sources and summarize them.

Every rule lives in the command that owns it; this module only calls their library
APIs and counts what they report:

* Zotero: ``notewrench.zoteroattachments.audit`` (Linux only; SKIPPED elsewhere)
* Joplin: ``notewrench.joplindupes.load_export`` + ``analyze``
* Calibre: ``notewrench.calibredupes`` ``read_library`` + ``make_plan`` (metadata only)
* Karakeep/Wallabag: ``notewrench.linkdedupe.build_report``
* Citations: ``notewrench.citekeys`` ``build_mapping`` + ``rekey_markdown`` (dry run)
"""
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from collections import Counter
import os
import shlex
import sqlite3

from .detect import Source

SEVERITIES = ("error", "warning", "note")
RANK = {severity: index for index, severity in enumerate(SEVERITIES)}


def _plural(count: int, word: str, plural: str | None = None) -> str:
    return f"{count:,} {word if count == 1 else (plural or word + 's')}"


def _bytes(count: int) -> str:
    size = float(count)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{count} B"


def _shorten(paths: list[str], keep: int = 3) -> str:
    if len(paths) <= keep:
        return ", ".join(paths)
    return ", ".join(paths[:keep - 1]) + f" + {len(paths) - keep + 1} more"


def _command(*parts: str, redirect: str | None = None) -> str:
    text = "notewrench " + " ".join(shlex.quote(part) for part in parts)
    return text + (f" > {shlex.quote(redirect)}" if redirect else "")


class Result:
    """One check (one Zotero library, all Joplin exports, ...) and what it found."""

    def __init__(self, check: str, label: str, paths: list[str], where: str | None = None):
        self.check, self.label, self.paths = check, label, paths
        self.where = where or _shorten(paths)
        self.status = "ok"
        self.reason: str | None = None
        self.counts: dict[str, int] = {}
        self.scope = ""
        self.findings: list[dict] = []
        self.fixes: list[dict] = []

    def add(self, severity: str, code: str, count: int, message: str) -> None:
        if count:
            self.findings.append({"severity": severity, "code": code, "count": count, "message": message})

    def fix(self, title: str, detail: str, commands: list[str]) -> None:
        severity = min((f["severity"] for f in self.findings), key=RANK.__getitem__, default="note")
        self.fixes.append({"check": self.check, "severity": severity, "title": title,
                           "detail": detail, "commands": commands,
                           "weight": sum(f["count"] for f in self.findings)})

    def skip(self, reason: str) -> "Result":
        self.status, self.reason = "skipped", reason
        return self

    def fail(self, reason: str) -> "Result":
        self.status, self.reason = "error", reason
        return self

    @property
    def verdict(self) -> str:
        if self.status == "skipped":
            return f"SKIPPED: {self.reason}"
        if self.status == "error":
            return f"could not check: {self.reason}"
        found = ", ".join(f["message"] for f in self.findings) or "no problems found"
        return f"{self.scope}: {found}" if self.scope else found

    @property
    def worst(self) -> str | None:
        return min((f["severity"] for f in self.findings), key=RANK.__getitem__, default=None)

    def record(self) -> dict:
        if self.status == "ok" and self.findings:
            self.status = "findings"
        return {"check": self.check, "label": self.label, "paths": self.paths, "where": self.where,
                "status": self.status,
                "reason": self.reason, "verdict": self.verdict, "counts": self.counts,
                "findings": self.findings, "fixes": self.fixes}


# --- Zotero ----------------------------------------------------------------------

def _attachment_name(attachment: dict) -> str:
    path = attachment.get("path") or ""
    if path.startswith("storage:"):
        return f"{attachment['key']}/{path[len('storage:'):]}"
    return path or attachment["key"]


def _numbered(name: str, index: int) -> str:
    """zotero-audit.md, zotero-audit-2.md, ...: one output name per checked library."""
    stem, dot, suffix = name.rpartition(".")
    return name if index == 0 else f"{stem}-{index + 1}{dot}{suffix}"


def check_zotero(source: Source, index: int = 0) -> Result:
    from ..zoteroattachments import InputError, audit

    result = Result("zotero", "Zotero", [source.path])
    if not os.path.isdir(source.storage):
        return result.fail(f"no storage/ folder next to {source.snapshot}")
    try:
        report = audit(source.snapshot, source.storage)
    except InputError as exc:
        if exc.code == "unsupported_platform":
            return result.skip(exc.detail)
        return result.fail(f"{exc.code}: {exc.detail}")
    stats = report["statistics"]
    attachments = report["attachments"]
    missing = [a for a in attachments if a["status"] == "missing"]
    unchecked = [a for a in attachments if a["status"] not in ("present", "missing", "remote_url")]
    duplicates, orphans, errors = report["duplicateGroups"], report["orphanCandidates"], report["auditErrors"]
    result.counts = {"items": stats["items"], "attachments": stats["attachments"],
                     "files": stats["filesInventoried"], "missing": len(missing),
                     "duplicate_sets": len(duplicates), "duplicate_extra_bytes": stats["duplicateExtraPathBytes"],
                     "orphan_folders": len(orphans), "orphan_bytes": stats["orphanCandidateBytes"],
                     "unchecked": len(unchecked), "audit_errors": len(errors)}
    result.scope = _plural(stats["attachments"], "attachment")
    result.add("error", "missing_attachment", len(missing), _plural(len(missing), "missing file"))
    result.add("error", "audit_error", len(errors), _plural(len(errors), "unreadable path"))
    result.add("warning", "duplicate_files", len(duplicates),
               f"{_plural(len(duplicates), 'duplicate set')} ({_bytes(stats['duplicateExtraPathBytes'])} extra)")
    result.add("warning", "orphan_folders", len(orphans),
               f"{_plural(len(orphans), 'orphan folder')} ({_bytes(stats['orphanCandidateBytes'])})")
    result.add("note", "unchecked_attachments", len(unchecked),
               f"{_plural(len(unchecked), 'linked file')} not checked")
    if result.findings:
        parts = []
        if missing:
            names = ", ".join(_attachment_name(a) for a in missing[:3]) + (", ..." if len(missing) > 3 else "")
            parts.append(f"Missing: {names}. Restore the file or re-link it in Zotero.")
        if duplicates or orphans:
            parts.append("Duplicate sets and orphan folders are candidates only; keep trashed items.")
        if unchecked:
            parts.append("Linked files outside storage/ need --allow-linked-root to be read.")
        candidates = len(duplicates) + len(orphans)
        if missing:
            title = f"Restore {_plural(len(missing), 'missing Zotero attachment')}" + (
                f" and review {_plural(candidates, 'cleanup candidate')}" if candidates else "")
        elif candidates:
            title = f"Review {_plural(candidates, 'cleanup candidate')} in Zotero storage before deleting anything"
        else:
            title = "Review the Zotero attachment audit"
        result.fix(title,
                   " ".join(parts),
                   [_command("zotero-attachments", "--snapshot", source.snapshot, "--storage", source.storage,
                             "--format", "markdown", redirect=_numbered("zotero-audit.md", index))])
    return result


# --- Joplin ----------------------------------------------------------------------

def check_joplin(sources: list[Source]) -> Result:
    from ..joplindupes import ExportError, analyze, load_export

    paths = [source.path for source in sources]
    result = Result("joplin", "Joplin", paths)
    try:
        report = analyze([load_export(path) for path in paths])
    except (ExportError, OSError, ValueError) as exc:
        return result.fail(str(exc))
    summary = report["summary"]
    groups = report["exact_duplicates"]
    extra = sum(len(group["members"]) - 1 for group in groups)
    reasons = Counter(reason.split(":", 1)[0] for note in report["notes"] for reason in note.get("blocked_reasons", []))
    result.counts = {"exports": len(paths), "notes": summary["notes"], "exact_groups": summary["exact_groups"],
                     "duplicate_notes": extra, "near_groups": summary["near_groups"],
                     "identity_conflicts": summary["identity_conflicts"], "blocked_notes": summary["blocked_notes"],
                     "unknown_items": summary["unknown_items"], "warnings": len(report["warnings"])}
    result.scope = _plural(summary["notes"], "note") + (f" in {len(paths)} exports" if len(paths) > 1 else "")
    result.add("warning", "exact_duplicates", summary["exact_groups"],
               f"{_plural(summary['exact_groups'], 'exact-duplicate group')} ({_plural(extra, 'extra copy', 'extra copies')})")
    result.add("warning", "identity_conflicts", summary["identity_conflicts"],
               _plural(summary["identity_conflicts"], "note ID", "note IDs") + " with different content")
    blocked = summary["blocked_notes"]
    top = ", ".join(reason.replace("_", " ") for reason, _ in reasons.most_common(2))
    result.add("warning", "blocked_notes", blocked,
               f"{_plural(blocked, 'note')} not comparable" + (f" ({top})" if top else ""))
    result.add("note", "near_candidates", summary["near_groups"], _plural(summary["near_groups"], "near-duplicate candidate"))
    result.add("note", "unknown_items", summary["unknown_items"], _plural(summary["unknown_items"], "unknown item"))
    result.add("note", "export_warnings", len(report["warnings"]), _plural(len(report["warnings"]), "export warning"))
    if result.findings:
        result.fix(f"Review {_plural(summary['exact_groups'] + summary['near_groups'], 'Joplin duplicate group')}"
                   if summary["exact_groups"] + summary["near_groups"] else "Review the Joplin export warnings",
                   "Approve groups and pick a keeper in joplin-decisions.json, then add --apply-decisions "
                   "joplin-decisions.json --output-dir joplin-deduplicated to write a new JEX to import. "
                   "Your exports are never changed.",
                   [_command("joplin-dupes", *paths, "--format", "markdown", "-o", "joplin-review.md"),
                    _command("joplin-dupes", *paths, "--decision-template", "joplin-decisions.json",
                             redirect="joplin-review.json")])
    return result


# --- Calibre ---------------------------------------------------------------------

def check_calibre(source: Source, index: int = 0) -> Result:
    from ..calibredupes.compare import make_plan
    from ..calibredupes.reader import ReviewError, read_library

    result = Result("calibre", "Calibre", [source.path])
    try:
        plan = make_plan([read_library(source.path, "A")])
    except (ReviewError, OSError, ValueError, sqlite3.Error) as exc:
        return result.fail(str(exc))
    clusters = plan["clusters"]
    strong = sum(cluster["level"] == "strong" for cluster in clusters)
    books = sum(len(cluster["members"]) for cluster in clusters)
    issues = Counter(issue for item in plan["diagnostics"] for issue in item["issues"])
    result.counts = {"books": plan["summary"]["books"], "clusters": len(clusters), "strong_clusters": strong,
                     "candidate_clusters": len(clusters) - strong, "clustered_books": books,
                     "invalid_isbn": issues["invalid-isbn"], "missing_author": issues["missing-author"],
                     "missing_title": issues["missing-title"]}
    result.scope = _plural(plan["summary"]["books"], "book")
    result.add("warning", "strong_duplicates", strong,
               _plural(strong, "strong duplicate cluster"))
    result.add("note", "candidate_duplicates", len(clusters) - strong,
               _plural(len(clusters) - strong, "possible duplicate"))
    result.add("note", "invalid_isbn", issues["invalid-isbn"], _plural(issues["invalid-isbn"], "invalid ISBN"))
    result.add("note", "missing_author", issues["missing-author"],
               _plural(issues["missing-author"], "book") + " without authors")
    result.add("note", "missing_title", issues["missing-title"],
               _plural(issues["missing-title"], "book") + " without a title")
    if result.findings:
        result.fix(f"Review {_plural(len(clusters), 'Calibre duplicate cluster')} ({_plural(books, 'book')})"
                   if clusters else "Review the Calibre metadata diagnostics",
                   "Pick a keeper per cluster in the plan's decisionTemplate, check it with --decisions, then "
                   "merge in Calibre itself. calibre-dupes never writes the library.",
                   [_command("calibre-dupes", source.path, "-o", _numbered("calibre-review.json", index))])
    return result


# --- Karakeep / Wallabag -----------------------------------------------------------

def check_links(sources: list[Source]) -> Result:
    from ..linkdedupe.report import build_report

    paths = [source.path for source in sources]
    result = Result("links", "Read-later", paths)
    try:
        report = build_report([{"name": source.path, "text": source.text, "format": source.format}
                               for source in sources], skip_invalid=True)
    except (ValueError, RecursionError) as exc:
        return result.fail(str(exc))
    summary = report["clusters"]["summary"]
    clusters = report["clusters"]["clusters"]
    across = sum(len({member["sourceId"] for member in cluster["members"]}) > 1 for cluster in clusters)
    formats = sorted({"Karakeep" if source.format == "karakeep" else "Wallabag" for source in sources})
    result.counts = {"exports": len(paths), "bookmarks": summary["totalRecords"],
                     "clusters": summary["candidateClusters"], "clustered_bookmarks": summary["clusteredRecords"],
                     "clusters_across_exports": across, "skipped_records": summary["skippedRecords"],
                     "records_without_url": summary["nonUrlRecords"]}
    result.scope = f"{_plural(summary['totalRecords'], 'bookmark')} in {' + '.join(formats)}"
    detail = f", {across} across exports" if across and len(paths) > 1 else ""
    result.add("warning", "duplicate_bookmarks", summary["candidateClusters"],
               f"{_plural(summary['candidateClusters'], 'duplicate cluster')} "
               f"({_plural(summary['clusteredRecords'], 'bookmark')}{detail})")
    result.add("warning", "invalid_records", summary["skippedRecords"],
               _plural(summary["skippedRecords"], "invalid record"))
    if result.findings:
        result.fix(f"Review {_plural(summary['candidateClusters'], 'duplicate bookmark cluster')}",
                   "merge-plan.json lists each candidate with its tags, notes and provenance; nothing is merged "
                   "automatically. For one Wallabag export, --wallabag-export writes a new deduplicated export "
                   "from decisions you approve.",
                   [_command("links", "--out", "links-review",
                             *(["--skip-invalid"] if summary["skippedRecords"] else []), *paths)])
    return result


# --- Citations (CSL-JSON + Markdown) -----------------------------------------------

def check_citations(bibliographies: list[Source], documents: list[Source]) -> Result:
    from ..citekeys import RekeyError, build_mapping, rekey_markdown
    from ..citekeys.files import read_csl, read_utf8, resolve
    from ..citekeys.identity import is_key

    paths = [source.path for source in bibliographies + documents]
    result = Result("citations", "Citations", paths,
                    where=_shorten([source.path for source in bibliographies])
                    + (f" + {_plural(len(documents), 'Markdown file')}" if documents else ""))
    if not bibliographies:
        return result.skip(f"{_plural(len(documents), 'Markdown file')} but no CSL-JSON bibliography to check "
                           "citekeys against")
    if not documents:
        return result.skip("no Markdown files to check the bibliography against")
    items, keys = {}, {}
    try:
        for bib in bibliographies:
            items[bib.path] = read_csl(resolve(bib.path))
            keys[bib.path] = {item["id"] for item in items[bib.path]
                              if isinstance(item, dict) and is_key(item.get("id"))}
    except RekeyError as exc:
        return result.fail(f"{exc.code}: {exc}")
    union = set().union(*keys.values())
    renames: dict[tuple[str, str], dict[str, str]] = {}
    for old in items:
        for new in items:
            if old != new:
                try:
                    mapping = build_mapping(items[old], items[new])["mapping"]
                except RekeyError:
                    mapping = []  # conflicting identities: citekeys itself refuses this pair
                renames[old, new] = {e["old"]: e["new"] for e in mapping if e["old"] != e["new"]}
    unknown, unsupported, unreadable, stale_count = [], 0, [], 0
    stale: dict[tuple[str, str], list[tuple[Source, list[dict]]]] = {}
    for doc in documents:
        try:
            text = read_utf8(resolve(doc.path))
            scan = rekey_markdown(text, {key: key for key in union})
            unknown += [(doc, entry) for entry in scan["unresolved"]]
            unsupported += len(scan["unsupported"])
            if len(items) < 2:
                continue
            # The document's "home" bibliography is the one that resolves the most of its keys;
            # with a unique home, keys that another export renames for the same items are stale.
            misses = {path: len(rekey_markdown(text, {key: key for key in keys[path]})["unresolved"])
                      for path in items}
            best = min(misses.values())
            homes = [path for path, count in misses.items() if count == best]
            if len({frozenset(keys[path]) for path in homes}) != 1:
                continue  # no single best match (identical copies of one export are fine)
            home, targets, offsets = homes[0], {frozenset(keys[homes[0]])}, set()
            for new in items:
                if frozenset(keys[new]) in targets or not renames[home, new]:
                    continue  # the home export itself, or a copy of an export already compared
                targets.add(frozenset(keys[new]))
                edits = rekey_markdown(text, renames[home, new])["edits"]
                if edits:
                    stale.setdefault((home, new), []).append((doc, edits))
                    offsets.update(edit["offset"] for edit in edits)
            stale_count += len(offsets)
        except RekeyError as exc:
            unreadable.append(f"{doc.path}: {exc}")
    result.counts = {"bibliographies": len(bibliographies), "entries": len(union), "documents": len(documents),
                     "unknown_citations": len(unknown), "stale_citations": stale_count,
                     "unsupported_citations": unsupported, "unreadable_documents": len(unreadable)}
    result.scope = _plural(len(documents), "Markdown file")
    result.add("error", "unknown_citekeys", len(unknown),
               _plural(len(unknown), "unknown-key citation"))
    result.add("warning", "stale_citekeys", stale_count,
               _plural(stale_count, "renamed-key citation"))
    result.add("note", "unsupported_citations", unsupported,
               _plural(unsupported, "unsupported citation"))
    result.add("note", "unreadable_documents", len(unreadable), _plural(len(unreadable), "unreadable file"))
    for (old, new), groups in sorted(stale.items()):
        docs = [doc.path for doc, _ in groups]
        root = os.path.commonpath([os.path.dirname(os.path.abspath(path)) for path in docs])
        shown = os.path.relpath(root) if not os.path.isabs(docs[0]) else root
        files = [os.path.relpath(os.path.abspath(path), root) for path in docs]
        examples = sorted({(edit["key"], edit["replacement"]) for _, edits in groups for edit in edits})
        sample = ", ".join(f"{a} -> {b}" for a, b in examples[:3]) + (
            f" and {len(examples) - 3} more" if len(examples) > 3 else "")
        target = os.path.basename(new)
        count = sum(len(edits) for _, edits in groups)
        result.fixes.append({
            "check": "citations", "severity": "warning", "weight": count,
            "title": f"Rekey {_plural(count, 'citation')} in {_plural(len(docs), 'file')} from "
                     f"{os.path.basename(old)} to {target}",
            "detail": f"Same items (matched by DOI or URL) under new keys: {sample}. If {target} is your "
                      "current export, preview the rewrite, then rerun with --out-dir rekeyed to write copies.",
            "commands": [_command("citekeys", "--old", old, "--new", new, "--root", shown, *files, "--dry-run")]})
    if unknown:
        where = "; ".join(f"{doc.path}:{entry['line']} @{entry['key']}" for doc, entry in unknown[:3])
        result.fixes.append({
            "check": "citations", "severity": "error", "weight": len(unknown),
            "title": f"Fix {_plural(len(unknown), 'citation')} whose key is in no bibliography",
            "detail": f"{where}{'; ...' if len(unknown) > 3 else ''}. Add the item to your library and "
                      "export again, or correct the key.",
            "commands": []})
    return result


# --- all checks ------------------------------------------------------------------

def run_checks(sources: list[Source]) -> list[Result]:
    by_kind: dict[str, list[Source]] = {}
    for source in sources:
        by_kind.setdefault(source.kind, []).append(source)
    results = [check_zotero(source, index) for index, source in enumerate(by_kind.get("zotero", []))]
    if by_kind.get("joplin"):
        results.append(check_joplin(by_kind["joplin"]))
    results += [check_calibre(source, index) for index, source in enumerate(by_kind.get("calibre", []))]
    if by_kind.get("readlater"):
        results.append(check_links(by_kind["readlater"]))
    if by_kind.get("bibliography") or by_kind.get("markdown"):
        results.append(check_citations(by_kind.get("bibliography", []), by_kind.get("markdown", [])))
    return results


def top_fixes(results: list[Result]) -> list[dict]:
    fixes = [fix for result in results for fix in result.fixes]
    return sorted(fixes, key=lambda fix: (RANK[fix["severity"]], -fix["weight"]))
