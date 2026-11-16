# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from .. import __version__
from . import ExportError, Limits, analyze, load_export
from .report import inspect_exact_group, inspect_note, render_json, render_markdown
from .reader import _open_anchored
from .rewrite import decision_template, outside_inputs, read_decisions, rewrite_archive, write_new_file


PROG = "notewrench joplin-dupes"


def positive(value: str) -> int:
    try:
        result = int(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError("must be a positive integer") from e
    if result <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Read-only duplicate review for offline Joplin JEX/raw snapshots, with explicitly approved new JEX copies. Never deletes or imports notes.",
        epilog="Exit 0: assessed; 3: report includes warnings/blocked notes/unknown items; 2: invalid input or output. Near candidates alone do not change exit status.")
    parser.add_argument("inputs", nargs="+", type=Path, help="uncompressed JEX tar files or flat raw export directories")
    parser.add_argument("--version", action="version", version=f"{PROG} {__version__}")
    parser.add_argument("--format", choices=("json", "markdown"), default="json", help="report format (default: json)")
    parser.add_argument("--target-pair", nargs=2, action="append", metavar=("SOURCE_A", "SOURCE_B"),
                        help="limit note-target uniqueness for these two sources (e.g. s1 s2); excluded sources keep global checks and evidence; supply once")
    parser.add_argument("--inspect-note", action="append", metavar="SOURCE:NOTE_ID",
                        help="JSON-only read-only view of one exact source-qualified note and its complete review groups (e.g. s1:<note-id>); supply once; all inputs are assessed")
    parser.add_argument("--inspect-exact-group", action="append", metavar="SIGNATURE",
                        help="JSON-only read-only view of one existing exact group by full lowercase signature, with all member assessments; supply once; all inputs are assessed")
    parser.add_argument("-o", "--output", type=Path, help="create a new report file outside input directories; default stdout; never overwrite")
    parser.add_argument("--decision-template", type=Path, help="create an unapproved exact-group decision JSON; review report remains on stdout")
    parser.add_argument("--apply-decisions", type=Path, help="create a new JEX using an explicitly approved decision JSON")
    parser.add_argument("--output-dir", type=Path, help="new directory for deduplicated.jex and rewrite-report.json; requires --apply-decisions")
    parser.add_argument("--max-file-bytes", type=positive, default=Limits.max_file_bytes, help="largest single file per input (default: %(default)s)")
    parser.add_argument("--max-total-bytes", type=positive, default=Limits.max_total_bytes, help="uncompressed payload budget per input (default: %(default)s)")
    parser.add_argument("--max-entries", type=positive, default=Limits.max_entries, help="most archive members or raw files per input (default: %(default)s)")
    parser.add_argument("--max-metadata-bytes", type=positive, default=Limits.max_metadata_bytes, help="largest metadata record per input (default: %(default)s)")
    args = parser.parse_args(argv)
    try:
        if args.inspect_exact_group:
            if len(args.inspect_exact_group) != 1:
                raise ExportError("--inspect-exact-group may be supplied only once")
            if (args.inspect_note or args.format != "json" or args.decision_template
                    or args.apply_decisions or args.output_dir):
                raise ExportError("--inspect-exact-group requires JSON review mode and cannot be combined with --inspect-note, decision modes or --output-dir")
        if args.inspect_note:
            if len(args.inspect_note) != 1:
                raise ExportError("--inspect-note may be supplied only once")
            if args.format != "json" or args.decision_template or args.apply_decisions or args.output_dir:
                raise ExportError("--inspect-note requires JSON review mode and cannot be combined with decision modes or --output-dir")
        if args.target_pair and len(args.target_pair) != 1:
            raise ExportError("--target-pair may be supplied only once")
        if bool(args.apply_decisions) != bool(args.output_dir):
            raise ExportError("--apply-decisions and --output-dir must be supplied together")
        if ((args.apply_decisions and args.decision_template)
                or ((args.apply_decisions or args.decision_template) and (args.output or args.format != "json"))):
            raise ExportError("Decision modes cannot be combined with each other, -o or --format markdown")
        resolved = [p.resolve() for p in args.inputs]
        if len(set(resolved)) != len(resolved):
            raise ExportError("The same input path was supplied more than once")
        if args.output:
            output = args.output.resolve()
            for source in resolved:
                if output == source or (source.is_dir() and output.is_relative_to(source)):
                    raise ExportError("Report output must be outside all inputs")
        limits = Limits(args.max_file_bytes, args.max_total_bytes, args.max_entries, args.max_metadata_bytes)
        exports = [load_export(path, limits) for path in args.inputs]
        report = analyze(exports, target_pair=args.target_pair[0] if args.target_pair else None)
        if args.apply_decisions:
            rewrite_archive(exports, report, read_decisions(args.apply_decisions), args.output_dir, limits)
            print(f"Reviewed archive and rewrite report created: {args.output_dir}", file=sys.stderr)
            return 0
        view = inspect_note(report, args.inspect_note[0]) if args.inspect_note else report
        if args.inspect_exact_group:
            view = inspect_exact_group(report, args.inspect_exact_group[0])
        content = (render_json if args.format == "json" else render_markdown)(view)
        if args.decision_template:
            outside_inputs(args.decision_template, exports)
            write_new_file(args.decision_template, render_json(decision_template(exports, report)))
        if args.output:
            # Exclusive creation prevents overwriting inputs, hardlinks or reports.
            absolute_output = Path(os.path.abspath(args.output))
            parent_fd = _open_anchored(absolute_output.parent, directory=True)
            try:
                fd = os.open(absolute_output.name,
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=parent_fd)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                        stream.write(content)
                except BaseException:
                    os.unlink(absolute_output.name, dir_fd=parent_fd)
                    raise
            finally:
                os.close(parent_fd)
            print(f"Review report created: {args.output}", file=sys.stderr)
        else:
            sys.stdout.write(content)
        return 3 if (report["warnings"] or report["summary"]["blocked_notes"]
                     or report["summary"]["unknown_items"]) else 0
    except (ExportError, OSError, ValueError) as e:
        print(f"{PROG}: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
