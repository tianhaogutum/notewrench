# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (see licenses/zotero-dupes-MIT.txt)
"""``notewrench zotero-dupes`` command line. All outputs go into a new directory.

The parser, ``--help`` and ``--version`` use only the standard library; the
matching, reading and validation modules (BibDedupe, RDFLib, isbnlib, jsonschema,
...) are imported when a subcommand runs. Install them with
``pip install 'notewrench[zotero-dupes]'``.
"""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from .. import __version__ as NOTEWRENCH_VERSION
from .schemas import KINDS, artifact_schema

PROG = "notewrench zotero-dupes"
MAX_OUTPUT_BYTES = 32 * 1024 * 1024


def _write_json(path: Path, payload: dict) -> None:
    from .model import InputError
    size = 1  # Include the final newline in the artifact budget.
    with path.open("w", encoding="utf-8") as stream:
        encoder = json.JSONEncoder(ensure_ascii=False, indent=2, allow_nan=False)
        for chunk in encoder.iterencode(payload):
            size += len(chunk.encode("utf-8"))
            if size > MAX_OUTPUT_BYTES:
                raise InputError(f"{path.name} exceeds 32 MiB; export/review a smaller collection to bound memory and artifact size")
            stream.write(chunk)
        stream.write("\n")


def _publish(output: Path, writer) -> None:
    from .model import InputError
    if output.exists() or output.is_symlink():
        raise InputError(f"Output already exists: {output}. Choose a new directory to preserve previous review decisions.")
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".library-bridge-", dir=output.parent))
    try:
        writer(stage)
        os.rename(stage, output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def _new_output(output: Path) -> None:
    from .model import InputError
    if output.exists() or output.is_symlink():
        raise InputError(f"Output already exists: {output}. Choose a new directory to preserve previous review decisions.")


def _csv_cell(value) -> str:
    text = str(value or "")
    # Neutralize spreadsheet formulas in user-controlled labels and comments.
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")) else text


def compare(args) -> None:
    from .decisions import decision_template
    from .model import InputError
    from .readers import load_library
    from .render import render_review
    _new_output(args.output)
    if len(args.library) < 2:
        raise InputError("Provide at least two libraries with distinct, persistent labels")
    from .matching import build_review
    libraries, records, labels, input_paths = [], [], set(), set()
    for argument in args.library:
        if "=" not in argument:
            raise InputError("Each --library must be LABEL=PATH, for example personal=personal.json")
        label, filename = argument.split("=", 1)
        if label in labels:
            raise InputError(f"Duplicate library label: {label}")
        path = Path(filename).expanduser().resolve()
        if path in input_paths:
            raise InputError("The same export file cannot represent two different libraries")
        labels.add(label)
        input_paths.add(path)
        parsed, info = load_library(label, path)
        records.extend(parsed)
        libraries.append(info)
    review = build_review(records, libraries, title_threshold=args.title_threshold,
                          max_block_pairs=args.max_block_pairs,
                          max_title_comparisons=args.max_title_comparisons)
    decisions = decision_template(review)

    def write(stage: Path) -> None:
        _write_json(stage / "review.json", review)
        _write_json(stage / "decisions.json", decisions)
        (stage / "review.html").write_text(render_review(review, decisions), encoding="utf-8")

    _publish(args.output, write)
    for library in libraries:
        for warning in library["warnings"]:
            print(f"Warning [{library['label']}]: {warning}", file=sys.stderr)
    print(f"{len(records)} items · {len(review['pairs'])} candidate pairs · {len(review['clusters'])} review clusters")
    print(f"Open {args.output / 'review.html'} in a browser. Download decisions when you finish.")


def finalize(args) -> None:
    from .decisions import export_confirmed
    from .readers import read_json
    _new_output(args.output)
    review, decisions = read_json(args.review, MAX_OUTPUT_BYTES), read_json(args.decisions)
    mapping, suggestions = export_confirmed(review, decisions)

    def write(stage: Path) -> None:
        _write_json(stage / "correspondences.json", mapping)
        _write_json(stage / "suggestions.json", suggestions)
        with (stage / "correspondences.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["correspondence_id", "pair_id", "left_library", "left_source_id", "left_item_id", "right_library", "right_source_id", "right_item_id", "preferred", "comment"])
            for row in mapping["correspondences"]:
                writer.writerow([_csv_cell(x) for x in [row["correspondence_id"], row["pair_id"], row["left"]["library"], row["left"]["source_id"], row["left"]["id"], row["right"]["library"], row["right"]["source_id"], row["right"]["id"], row["preferred"], row["comment"]]])
    _publish(args.output, write)
    print(f"Exported {len(mapping['correspondences'])} explicitly confirmed correspondences and preservation suggestions to {args.output}")
    print("Decision counts: " + ", ".join(f"{k}={v}" for k, v in mapping["decision_counts"].items()))


def inspect(args) -> None:
    from .readers import load_library
    records, info = load_library(args.library, args.export)
    summary = {**info, "identity_quality": dict(Counter(record.identity_quality for record in records)),
               "item_types": dict(Counter(str(record.metadata.get("type", "unknown")) for record in records)),
               "tags": sum(len(record.tags) for record in records),
               "notes": sum(len(record.notes) for record in records),
               "annotations": sum(len(record.annotations) for record in records),
               "attachment_metadata": sum(len(record.attachments) for record in records)}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def carry(args) -> None:
    from .decisions import carry_decisions
    from .readers import read_json
    _new_output(args.output)
    old_review = read_json(args.old_review, MAX_OUTPUT_BYTES)
    new_review = read_json(args.new_review, MAX_OUTPUT_BYTES)
    decisions, audit = carry_decisions(old_review, read_json(args.decisions), new_review)

    def write(stage):
        _write_json(stage / "decisions.json", decisions)
        _write_json(stage / "carryover-audit.json", audit)
    _publish(args.output, write)
    print(f"Carried {len(audit['carried'])} unchanged pairs; reset {len(audit['reset_changed'])} changed pairs; {len(audit['new_pairs'])} new pairs. Load {args.output / 'decisions.json'} into the new review.")


def schema(args) -> None:
    print(json.dumps(artifact_schema(args.kind), indent=2))


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog=PROG,
        description="Read-only cross-library duplicate review of Zotero exports (a personal library and "
                    "group libraries), powered by BibDedupe. No library files are changed.",
        epilog="Needs the optional dependencies: pip install 'notewrench[zotero-dupes]'. "
               "Docs: https://github.com/tianhaogutum/notewrench/blob/main/docs/zotero-dupes.md")
    root.add_argument("--version", action="version", version=f"{PROG} {NOTEWRENCH_VERSION}")
    sub = root.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("compare", help="Create an offline HTML review from two or more exports")
    cmd.add_argument("--library", action="append", required=True, metavar="LABEL=PATH", help="Persistent library label and export path; repeat for each library")
    cmd.add_argument("--output", type=Path, required=True, help="New output directory (never overwritten)")
    cmd.add_argument("--title-threshold", type=float, default=92, help="RapidFuzz title candidate cutoff, 70–100 (default 92)")
    cmd.add_argument("--max-block-pairs", type=int, default=50_000, help="Upper bound on BibDedupe intermediate pairs, at most 100000")
    cmd.add_argument("--max-title-comparisons", type=int, default=5_000_000, help="Bound on title comparisons, at most 25000000")
    cmd.set_defaults(run=compare)
    cmd = sub.add_parser("finalize", help="Export only human-confirmed pair mappings and merge suggestions")
    cmd.add_argument("--review", type=Path, required=True, help="Original review.json")
    cmd.add_argument("--decisions", type=Path, required=True, help="Decision file downloaded from review.html")
    cmd.add_argument("--output", type=Path, required=True, help="New export directory")
    cmd.set_defaults(run=finalize)
    cmd = sub.add_parser("inspect", help="Check one export's contents and identity quality without matching or writing files")
    cmd.add_argument("export", type=Path)
    cmd.add_argument("--library", default="library", help="Label used for input diagnostics")
    cmd.set_defaults(run=inspect)
    cmd = sub.add_parser("carry-decisions", help="Explicitly carry decisions to a refreshed review only for unchanged source records")
    cmd.add_argument("--old-review", type=Path, required=True)
    cmd.add_argument("--new-review", type=Path, required=True)
    cmd.add_argument("--decisions", type=Path, required=True, help="Decisions belonging to old-review")
    cmd.add_argument("--output", type=Path, required=True, help="New directory for carried decisions and audit")
    cmd.set_defaults(run=carry)
    cmd = sub.add_parser("schema", help="Print a self-contained JSON Schema for an artifact (no network or file writes)")
    cmd.add_argument("kind", choices=KINDS)
    cmd.set_defaults(run=schema)
    return root


def main(argv: list[str] | None = None) -> int:
    """Run ``notewrench zotero-dupes`` with ``argv`` (default ``sys.argv[1:]``); returns the exit status."""
    args = parser().parse_args(argv)
    from .model import InputError
    try:
        args.run(args)
    except (InputError, OSError) as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(f"{PROG}: interrupted; input exports were not changed", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

