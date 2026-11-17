# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (MIT; see licenses/bibtex-keys-MIT.txt)
"""Read inputs, build a review bundle, and never mutate a note."""

from dataclasses import dataclass
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from . import __version__
from .bibliography import DEFAULT_ID_FIELDS, InputError, lf_lines, load_snapshot, match_snapshots, read_utf8, snapshot_format
from .notes import NoteScanner, apply_edits, note_target_key

IGNORED_DIRS = frozenset({".git", ".venv", ".cache", "node_modules", "vendor-tmp"})
DEFAULT_EXTENSIONS = frozenset({".md", ".markdown", ".qmd", ".rmd"})
MAX_REVIEW_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class Options:
    id_fields: tuple[str, ...] = DEFAULT_ID_FIELDS
    extensions: frozenset[str] = DEFAULT_EXTENSIONS
    max_note_bytes: int = 2 * 1024 * 1024
    max_files: int = 10000
    max_occurrences: int = 100000


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def note_paths(notes: Path, extensions: frozenset[str], scan_issues: list):
    """A deterministic, non-symlink-following scan shared by plan and verify."""
    def walk_error(error):
        scan_issues.append({"kind": "unreadable_directory", "message": str(error)})

    for root, dirs, names in os.walk(notes, followlinks=False, onerror=walk_error):
        dirs.sort()
        for directory in list(dirs):
            path = Path(root, directory)
            if directory in IGNORED_DIRS or directory.startswith("."):
                dirs.remove(directory)
            elif any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in directory):
                display = path.relative_to(notes).as_posix().encode("utf-8", "backslashreplace").decode("utf-8")
                scan_issues.append({"kind": "unsafe_path_skipped", "path": display})
                dirs.remove(directory)
            elif path.is_symlink():
                scan_issues.append({"kind": "symlink_directory_skipped", "path": path.relative_to(notes).as_posix()})
                dirs.remove(directory)
        for name in sorted(names):
            path = Path(root, name)
            if path.suffix.lower() not in extensions or name.startswith("."):
                continue
            relative = path.relative_to(notes).as_posix()
            if any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in relative):
                display = relative.encode("utf-8", "backslashreplace").decode("utf-8")
                scan_issues.append({"kind": "unsafe_path_skipped", "path": display})
                continue
            if path.is_symlink():
                scan_issues.append({"kind": "symlink_file_skipped", "path": relative})
                continue
            if not path.is_file():
                scan_issues.append({"kind": "nonregular_file_skipped", "path": relative})
                continue
            yield path, relative


def unified_patch(old: str, new: str, path: str) -> str:
    # Quoted git-style names protect spaces, quotes, and non-ASCII filenames.
    def quoted(name):
        raw = name.encode("utf-8")
        return '"' + "".join(chr(c) if 32 <= c < 127 and c not in {34, 92}
                            else '\\"' if c == 34 else '\\\\' if c == 92
                            else f"\\{c:03o}" for c in raw) + '"'
    left, right = quoted("a/" + path), quoted("b/" + path)
    result = [f"diff --git {left} {right}\n"]
    for line in difflib.unified_diff(lf_lines(old), lf_lines(new),
                                     fromfile=left, tofile=right, n=3):
        result.append(line)
        if not line.endswith("\n"):
            result.append("\n\\ No newline at end of file\n")
    return "".join(result)


def make_plan(old_path: Path, new_path: Path, notes: Path, options: Options = Options()):
    notes = notes.resolve()
    for path in (old_path.resolve(), new_path.resolve(), notes):
        if any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in str(path)):
            raise InputError(f"{str(path)!r}: input paths must be Unicode text without control characters")
    if not notes.is_dir():
        raise InputError(f"{notes}: notes directory does not exist")
    old_raw, old, old_issues = load_snapshot(old_path, options.id_fields)
    new_raw, new, new_issues = load_snapshot(new_path, options.id_fields)
    mappings, states, conflicts = match_snapshots(old, new)
    conflicts.extend(dict(issue, snapshot=side) for side, issues in (("old", old_issues), ("new", new_issues)) for issue in issues)
    scanner = NoteScanner(states)
    files, patches, scan_issues, manifest = [], [], [], []
    scanned = references = replacements = 0
    for path, relative in note_paths(notes, options.extensions, scan_issues):
        if scanned >= options.max_files or references >= options.max_occurrences:
            scan_issues.append({"kind": "scan_limit_reached", "message": "Scan incomplete; increase --max-files or --max-occurrences."})
            break
        scanned += 1
        try:
            raw, text = read_utf8(path, options.max_note_bytes)
        except InputError as exc:
            scan_issues.append({"kind": "unreadable_note", "path": relative, "message": str(exc)})
            continue
        manifest.append({"path": relative, "sha256": digest(raw)})
        edits, occurrences, issues = scanner.scan(text)
        stem = note_target_key(path.name)
        if stem in states and states[stem]["status"] != "unchanged":
            occurrences.append({"kind": "note_filename", "old_key": stem,
                                "new_key": states[stem]["new_key"], "identity": states[stem]["identity"],
                                "status": "manual_review", "line": 1, "column": 1,
                                "message": "Filename contains the old key; rename with your note application and update backlinks."})
        if references + len(occurrences) > options.max_occurrences:
            scan_issues.append({"kind": "scan_limit_reached", "path": relative,
                                "message": "File omitted because it exceeds the remaining occurrence budget."})
            break
        references += len(occurrences)
        new_text = apply_edits(text, edits)
        if new_text != text:
            patches.append(unified_patch(text, new_text, relative))
            replacements += len(edits)
        if occurrences or issues:
            files.append({"path": relative, "sha256_before": digest(raw),
                          "sha256_after": digest(new_text.encode("utf-8")),
                          "occurrences": occurrences, "issues": issues,
                          "replacements": len(edits)})
    blocked = sum(occurrence["status"] != "replace" for file in files for occurrence in file["occurrences"])
    if scanned == 0:
        scan_issues.append({"kind": "no_notes_matched", "message": "No note files matched the selected extensions and directory exclusions."})
    needs_review = bool(conflicts or scan_issues or blocked or any(file["issues"] for file in files))
    # The bundle identity ("tool": "citekey-audit", schema_version 1) is the saved-file
    # format of the former standalone tool and is kept unchanged.
    report = {
        "schema_version": 1, "tool": "citekey-audit", "tool_version": __version__,
        "status": "review_required" if needs_review else "ready",
        "inputs": {"old_bib": str(old_path.resolve()), "new_bib": str(new_path.resolve()),
                   "old_sha256": digest(old_raw), "new_sha256": digest(new_raw), "notes_root": str(notes),
                   "old_format": snapshot_format(old_path), "new_format": snapshot_format(new_path)},
        "options": {"id_fields": list(options.id_fields), "extensions": sorted(options.extensions),
                    "max_note_bytes": options.max_note_bytes, "max_files": options.max_files,
                    "max_occurrences": options.max_occurrences},
        "summary": {"old_entries": len(old), "new_entries": len(new), "renamed_keys": len(mappings),
                    "files_scanned": scanned, "affected_files": len(files),
                    "changed_files": len(patches), "replacements": replacements,
                    "affected_references": references, "manual_or_blocked_references": blocked,
                    "bibliography_issues": len(conflicts), "scan_issues": len(scan_issues)},
        "mappings": mappings, "conflicts": conflicts, "scan_issues": scan_issues, "files": files,
        "note_manifest": manifest, "patch_sha256": digest("".join(patches).encode("utf-8")),
    }
    return report, "".join(patches)


def _code(value):
    content = json.dumps(value, ensure_ascii=False)
    fence = "`" * (max((len(run) for run in re.findall(r"`+", content)), default=0) + 1)
    return fence + content + fence


def review_markdown(report):
    summary = report["summary"]
    lines = ["# Citation-key change review", "", f"Status: **{report['status']}**", "",
             f"{summary['renamed_keys']} changed keys; {summary['files_scanned']} notes scanned; "
             f"{summary['replacements']} replacements in {summary['changed_files']} files.", "",
             "Notes have not been modified. Review `review.json` and `changes.patch` before applying.", "",
             "## Key mapping", ""]
    for mapping in report["mappings"]:
        lines.append(f"- {_code(mapping['old_key'])} → {_code(mapping['new_key'])} ({_code(mapping['identity'])})")
    if not report["mappings"]:
        lines.append("No verified key changes.")
    lines += ["", "## Affected locations", ""]
    shown = 0
    total = sum(len(file["occurrences"]) for file in report["files"])
    for file in report["files"]:
        for occurrence in file["occurrences"]:
            if shown >= 200:
                break
            location = f"{file['path']}:{occurrence['line']}:{occurrence['column']}"
            target = " → " + _code(occurrence['new_key']) if occurrence.get('new_key') is not None else ""
            lines.append(f"- {_code(location)}: {occurrence['kind']} — {_code(occurrence['old_key'])}{target} ({occurrence['status']})")
            shown += 1
    if total == 0:
        lines.append("No affected references were found in scanned notes.")
    elif total > shown:
        lines.append(f"\nShowing {shown} of {total} locations. Every location is retained in `review.json`.")
    lines += ["", "## Issues and manual work", ""]
    for issue in report["conflicts"] + report["scan_issues"]:
        lines.append("- " + _code(issue))
    for file in report["files"]:
        for issue in file["issues"]:
            lines.append(f"- {_code(file['path'])}: {_code(issue)}")
    lines += ["", "## Applying after review", "",
              "Save or commit your notes first. Check the patch against the same notes root, then apply it using Git or your patch tool. "
              "Paths in the patch are relative to `inputs.notes_root`. An empty patch means there are no automatic replacements.", "",
              "Wiki/file links and note filenames are reported for manual work because changing a link without renaming its target would break it.", ""]
    return "\n".join(lines)


def write_bundle(out: Path, report: dict, patch: str):
    if out.resolve().is_relative_to(Path(report["inputs"]["notes_root"]).resolve()):
        raise InputError(f"{out}: output must be outside the notes directory to keep the input tree unchanged")
    if out.exists() or out.is_symlink():
        raise InputError(f"{out}: output already exists; use a new output directory")
    if not out.parent.is_dir():
        raise InputError(f"{out.parent}: output parent directory does not exist")
    # Staging name kept from the former tool (tests and cleanup scripts look for it).
    stage = Path(tempfile.mkdtemp(prefix=".citekey-audit-", dir=out.parent))
    try:
        encoder = json.JSONEncoder(indent=2, ensure_ascii=False)
        size = 0
        with (stage / "review.json").open("wb") as stream:
            for chunk in encoder.iterencode(report):
                encoded = chunk.encode("utf-8")
                size += len(encoded)
                if size + 1 > MAX_REVIEW_BYTES:
                    raise InputError(f"review.json exceeds {MAX_REVIEW_BYTES} bytes; lower --max-occurrences or scan smaller note subsets")
                stream.write(encoded)
            stream.write(b"\n")
        (stage / "changes.patch").write_bytes(patch.encode("utf-8"))
        (stage / "REVIEW.md").write_text(review_markdown(report), encoding="utf-8")
        stage.rename(out)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
