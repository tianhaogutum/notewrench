# SPDX-License-Identifier: MIT
# Copyright (c) 2026 citekey-audit contributors (MIT; see licenses/bibtex-keys-MIT.txt)
"""Verify a saved plan's exact note snapshot before or after external application."""

import json
from pathlib import Path, PurePosixPath
import re

from .bibliography import MAX_SNAPSHOT_BYTES, InputError, read_utf8
from .planner import MAX_REVIEW_BYTES, digest, note_paths

HASH = re.compile(r"[0-9a-f]{64}\Z")


def _relative_path(value):
    if not isinstance(value, str) or not value or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value):
        raise InputError("invalid review bundle: invalid note path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {".", ".."} for part in path.parts) or path.as_posix() != value:
        raise InputError("invalid review bundle: note path must be a normalized relative path")
    return value


def _hash(value):
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise InputError("invalid review bundle: invalid SHA-256")
    return value


def verify_bundle(bundle: Path, notes_root: Path | None = None, after: bool = False, notes_only: bool = False):
    _, text = read_utf8(bundle / "review.json", MAX_REVIEW_BYTES)
    try:
        report = json.loads(text)
        if report["tool"] != "citekey-audit" or report["schema_version"] != 1:
            raise InputError("unsupported review bundle format")
        plan_status = report["status"]
        if plan_status not in {"ready", "review_required"} or not isinstance(report["scan_issues"], list):
            raise InputError("invalid review bundle: status or scan issues")
        original_incomplete = bool(report["scan_issues"])
        expected = {}
        for item in report["note_manifest"]:
            name = _relative_path(item["path"])
            if name in expected:
                raise InputError("invalid review bundle: duplicate note path")
            expected[name] = _hash(item["sha256"])
        for item in report["files"]:
            name = _relative_path(item["path"])
            if name not in expected or _hash(item["sha256_before"]) != expected[name]:
                raise InputError("invalid review bundle: file and manifest disagree")
            if after:
                expected[name] = _hash(item["sha256_after"])
        root_value = str(notes_root) if notes_root is not None else report["inputs"]["notes_root"]
        if not isinstance(root_value, str) or not root_value or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in root_value):
            raise InputError("invalid review bundle: notes root path")
        root = Path(root_value).resolve()
        snapshots = []
        if not notes_only:
            for side in ("old", "new"):
                path = report["inputs"][side + "_bib"]
                if not isinstance(path, str) or not path or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in path):
                    raise InputError("invalid review bundle: snapshot path")
                snapshots.append((side, Path(path), _hash(report["inputs"][side + "_sha256"])))
        extensions = frozenset(report["options"]["extensions"])
        if not extensions or not all(isinstance(value, str) and value.startswith(".") for value in extensions):
            raise InputError("invalid review bundle: note extensions")
        limit = report["options"]["max_note_bytes"]
        max_files = report["options"]["max_files"]
        if type(limit) is not int or not 0 < limit <= 1024 * 1024 * 1024 or type(max_files) is not int or max_files <= 0:
            raise InputError("invalid review bundle: scan limits")
        patch_hash = _hash(report["patch_sha256"])
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise InputError(f"invalid review bundle: {exc}") from exc
    if not root.is_dir():
        raise InputError(f"{root}: notes root does not exist")
    issues = []
    for side, path, expected_hash in snapshots:
        try:
            raw, _ = read_utf8(path, MAX_SNAPSHOT_BYTES)
        except InputError as exc:
            issues.append({"kind": "bibliography_unreadable", "snapshot": side, "message": str(exc)})
            continue
        if digest(raw) != expected_hash:
            issues.append({"kind": "bibliography_changed", "snapshot": side, "path": str(path)})
    # Stream the patch rather than materializing a second copy of a large diff.
    import hashlib
    current_patch = hashlib.sha256()
    try:
        with (bundle / "changes.patch").open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                current_patch.update(chunk)
    except OSError as exc:
        raise InputError(f"cannot read patch: {exc}") from exc
    if current_patch.hexdigest() != patch_hash:
        issues.append({"kind": "patch_changed", "message": "changes.patch differs from the saved plan"})
    seen = set()
    for path, relative in note_paths(root, extensions, issues):
        if len(seen) >= max_files:
            issues.append({"kind": "scan_limit_reached", "message": "Verification scan incomplete"})
            break
        seen.add(relative)
        if relative not in expected:
            issues.append({"kind": "new_note", "path": relative})
            continue
        try:
            raw, _ = read_utf8(path, limit)
        except InputError as exc:
            issues.append({"kind": "unreadable_note", "path": relative, "message": str(exc)})
            continue
        if digest(raw) != expected[relative]:
            issues.append({"kind": "note_changed", "path": relative})
    for relative in sorted(expected.keys() - seen):
        issues.append({"kind": "note_missing", "path": relative})
    if original_incomplete:
        issues.append({"kind": "original_scan_incomplete", "message": "The saved plan has scan exclusions/errors; exact coverage cannot be verified."})
    return {"status": "snapshot_matches" if not issues else "stale_or_incomplete",
            "phase": "after" if after else "before", "notes_root": str(root),
            "notes_checked": len(seen), "bibliographies_checked": not notes_only,
            "plan_status": plan_status, "issues": issues}
