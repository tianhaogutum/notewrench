# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (MIT; see licenses/bibtex-keys-MIT.txt)
"""``notewrench bibtex-keys`` command line; planning is read-only with respect to all inputs.

The parser, ``--help`` and ``--version`` use only the standard library; the Pybtex,
markdown-it-py, mdit-py-plugins, pylatexenc and PyYAML based modules are imported
when a subcommand actually runs.
"""

import argparse
import json
from pathlib import Path
import sys

from .. import __version__ as NOTEWRENCH_VERSION

PROG = "notewrench bibtex-keys"


def positive_integer(value):
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected a positive integer") from exc
    if result <= 0:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return result


def parser():
    root = argparse.ArgumentParser(prog=PROG, description="Review stable-ID BibTeX/BetterBibTeX JSON key changes and create an offline note patch.")
    root.add_argument("--version", action="version", version=f"{PROG} {NOTEWRENCH_VERSION}")
    sub = root.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="Compare exports, scan notes, and write a new review bundle; never changes notes")
    plan.add_argument("old_bib", type=Path, help="Old .bib export or .json BetterBibTeX snapshot")
    plan.add_argument("new_bib", type=Path, help="New .bib export or .json BetterBibTeX snapshot")
    plan.add_argument("notes", type=Path)
    plan.add_argument("--out", type=Path, required=True, help="New output directory (must not already exist)")
    plan.add_argument("--id-field", action="append", help="Stable ID field; repeat to accept aliases. Default: zotero-uri, zotero_uri")
    plan.add_argument("--extension", action="append", help="Note extension; repeat to override defaults: md, markdown, qmd, rmd")
    plan.add_argument("--max-note-bytes", type=positive_integer, default=2 * 1024 * 1024)
    plan.add_argument("--max-files", type=positive_integer, default=10000)
    plan.add_argument("--max-occurrences", type=positive_integer, default=100000)
    verify = sub.add_parser("verify", help="Read-only hash check against the entire saved note snapshot and patch")
    verify.add_argument("bundle", type=Path)
    verify.add_argument("--notes-root", type=Path, help="Relocate the snapshot to a copy of the original notes")
    verify.add_argument("--after", action="store_true", help="Expect the proposed replacement bytes after external patch application")
    verify.add_argument("--notes-only", action="store_true", help="Check notes and patch only, explicitly skipping original bibliography snapshot hashes")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    from .bibliography import DEFAULT_ID_FIELDS, InputError
    try:
        if args.command == "verify":
            from .verify import verify_bundle
            result = verify_bundle(args.bundle, args.notes_root, args.after, args.notes_only)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0 if result["status"] == "snapshot_matches" else 2
        from .planner import DEFAULT_EXTENSIONS, Options, make_plan, write_bundle
        if args.out.exists() or args.out.is_symlink():
            raise InputError(f"{args.out}: output already exists; choose a new directory")
        if args.out.resolve().is_relative_to(args.notes.resolve()):
            raise InputError(f"{args.out}: output must be outside the notes directory")
        fields = tuple(field.lower() for field in args.id_field) if args.id_field else DEFAULT_ID_FIELDS
        extensions = frozenset("." + extension.lower().lstrip(".") for extension in args.extension) if args.extension else DEFAULT_EXTENSIONS
        options = Options(fields, extensions, args.max_note_bytes, args.max_files, args.max_occurrences)
        report, patch = make_plan(args.old_bib, args.new_bib, args.notes, options)
        write_bundle(args.out, report, patch)
        counts = report["summary"]
        print(f"{report['status']}: {counts['renamed_keys']} key changes, {counts['replacements']} replacements, "
              f"{counts['manual_or_blocked_references']} manual/blocked references. Review: {args.out}")
        return 2 if report["status"] == "review_required" else 0
    except (InputError, OSError, RecursionError) as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(f"{PROG}: interrupted; note inputs were not modified", file=sys.stderr)
        return 130


__all__ = ["main", "parser"]

if __name__ == "__main__":
    raise SystemExit(main())
