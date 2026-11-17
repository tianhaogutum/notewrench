# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum
"""``notewrench anki-merge`` command line and atomic, reviewable artifact bundle.

Exit 0: no unresolved notes; 1: a bundle was written but some notes need review;
2: invalid input/output, no new bundle. ``--help`` and ``--version`` work without
the optional ``csvdiff`` dependency; it is imported only when a merge runs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from typing import TYPE_CHECKING

from .. import __version__ as NOTEWRENCH_VERSION
from .text import InputError, read_deck, require_regular_file, write_deck

if TYPE_CHECKING:
    from .merge import MergeResult

PROG = "notewrench anki-merge"

MAX_RESOLUTION_BYTES = 16 * 1024 * 1024
WINDOWS = os.name == "nt"


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError(f"Duplicate JSON key in resolutions: {key!r}.")
        result[key] = value
    return result


def _invalid_constant(value):
    raise InputError(f"Nonstandard JSON constant in resolutions: {value}.")


def read_resolutions(path: Path | None):
    if path is None:
        return None
    require_regular_file(path)
    with path.open("rb") as stream:
        payload = stream.read(MAX_RESOLUTION_BYTES + 1)
    if len(payload) > MAX_RESOLUTION_BYTES:
        raise InputError(f"Resolutions file is too large (maximum {MAX_RESOLUTION_BYTES} bytes). Split review choices into smaller rounds.")
    return json.loads(payload.decode("utf-8-sig"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)


def write_bundle(result: "MergeResult", out: Path) -> None:
    if os.path.lexists(out):
        raise InputError(f"Output already exists: {out}. Choose a fresh directory to preserve the previous review.")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{out.name}-", dir=out.parent) as temp:
        stage = Path(temp) / "bundle"
        stage.mkdir()
        plan = result.plan
        with (stage / "import.tsv").open("w", encoding="utf-8", newline="") as stream:
            write_deck(stream, plan["output_fields"], result.imports, plan["html"])
        with (stage / "plan.json").open("w", encoding="utf-8") as stream:
            json.dump(plan, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        pending = {}
        for note in plan["notes"]:
            if note["issues"]:
                pending[note["id"]] = {"fields": {item["field"]: None for item in note["issues"]
                                                    if item["kind"] in ("field_conflict", "concurrent_add", "empty_first_field",
                                                                        "first_field_changed_without_guid", "first_field_collision")
                                                    and item["field"] != plan["id_field"]}}
        with (stage / "resolutions.json").open("w", encoding="utf-8") as stream:
            json.dump({"fingerprint": plan["fingerprint"], "notes": pending}, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        summary = ["Anki course merge review", "=" * 24,
                   f"Ready to import: {plan['import_count']}",
                   f"Notes requiring review: {plan['unresolved_count']}",
                   "", "Import.tsv contains ONLY safe additions/updates; withheld and retained notes are excluded.",
                   "If the ready count is zero, no import is needed.",
                   "Import into the original student collection. Select Update existing notes and verify field mapping.",
                   "Anki preserves scheduling only when it matches an existing note; this file contains no review data.",
                   "", "Counts: " + json.dumps(plan["counts"], ensure_ascii=False), "", "Assumptions:"]
        summary.extend("- " + text for text in plan["assumptions"])
        with (stage / "review.txt").open("w", encoding="utf-8") as stream:
            stream.write("\n".join(summary) + "\n")
            for note in plan["notes"]:
                if note["status"] == "unchanged":
                    continue
                stream.write(f"\nID {json.dumps(note['id'], ensure_ascii=False)}: {note['status']} / {note['action']}\n")
                stream.write(f"  Identity: {note['identity']}; first field changed: {note['first_field_changed']}\n")
                for item in note["issues"]:
                    stream.write("  ISSUE " + json.dumps(item, ensure_ascii=False) + "\n")
                for decision in note["decisions"]:
                    stream.write("  FIELD " + json.dumps(decision, ensure_ascii=False) + "\n")
        if WINDOWS:
            # Windows rename already refuses any existing destination. A
            # reservation would prevent publication on that platform.
            os.rename(stage, out)
        else:
            # POSIX rename may replace an empty directory: reserve it first
            # so we cannot replace a concurrently created directory/symlink.
            out.mkdir()
            try:
                os.rename(stage, out)
            except BaseException:
                try:
                    out.rmdir()  # remove only our still-empty reservation
                except OSError:
                    pass
                raise


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        prog=PROG, description="Preview a three-way Anki text update using stable course IDs.",
        epilog="exit status: 0 nothing to review, 1 bundle written but notes need review, 2 invalid "
               "input/output (no bundle). Needs the optional csvdiff dependency: "
               "pip install 'notewrench[anki-merge]'. Docs: "
               "https://github.com/tianhaogutum/notewrench/blob/main/docs/anki-merge.md")
    value.add_argument("old", type=Path, help="old teacher text export (common baseline)")
    value.add_argument("new", type=Path, help="new teacher text export")
    value.add_argument("student", type=Path, help="current complete student text export; include GUIDs")
    value.add_argument("--id", dest="id_field", required=True, help="stable course ID field name")
    value.add_argument("--fields", nargs="+", help="regular field names in order, excluding GUID/deck/type/tags columns")
    for role in ("old", "new", "student"):
        value.add_argument(f"--{role}-fields", nargs="+", help=f"override regular field names for {role}")
        value.add_argument(f"--{role}-delimiter", help=f"separator override only for {role}")
        value.add_argument(f"--{role}-header-row", action=argparse.BooleanOptionalAction, default=None,
                           help=f"whether {role} explicitly has an ordinary CSV header row")
    value.add_argument("--delimiter", help="separator override (default: #separator, otherwise tab)")
    value.add_argument("--header-row", action="store_true", help="all three inputs have ordinary CSV header rows; use source flags for mixed exports")
    value.add_argument("--notetype", help="fill missing note-type metadata with this known student note type")
    value.add_argument("--deck", help="fill missing deck metadata with this intended deck for new notes")
    value.add_argument("--protect", nargs="+", default=[], help="always retain these student fields")
    value.add_argument("--resolutions", type=Path, help="review choices bound to this exact preview fingerprint")
    value.add_argument("--out", type=Path, required=True, help="new output directory; existing directories are never overwritten")
    value.add_argument("--version", action="version", version=f"{PROG} {NOTEWRENCH_VERSION}")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    from .merge import merge  # needs csvdiff (notewrench[anki-merge])

    try:
        if os.path.lexists(args.out):
            raise InputError(f"Output already exists: {args.out}. Choose a fresh directory to preserve the previous review.")
        defaults = {name: getattr(args, name) for name in ("notetype", "deck") if getattr(args, name) is not None}
        decks = []
        for role in ("old", "new", "student"):
            source_header = getattr(args, role + "_header_row")
            decks.append(read_deck(getattr(args, role), args.id_field,
                                   getattr(args, role + "_fields") or args.fields,
                                   getattr(args, role + "_delimiter") or args.delimiter,
                                   args.header_row if source_header is None else source_header, defaults))
        resolutions = read_resolutions(args.resolutions)
        result = merge(*decks, args.id_field, tuple(args.protect), resolutions)
        write_bundle(result, args.out)
    except RecursionError:
        print(f"{PROG}: resolutions JSON is nested too deeply.", file=sys.stderr)
        return 2
    except (InputError, OSError, ValueError) as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2
    plan = result.plan
    print(f"{plan['import_count']} safe additions/updates; {plan['unresolved_count']} notes need review. {args.out / 'review.txt'}")
    return 1 if plan["unresolved_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
