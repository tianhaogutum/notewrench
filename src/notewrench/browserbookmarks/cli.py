# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (see licenses/browser-bookmarks-MIT.txt)
"""``notewrench browser-bookmarks``: explicit merge/keep decisions and a resumable terminal reviewer.

The parser, ``--help`` and ``--version`` need only the standard library; the
third-party parsers of the ``browser-bookmarks`` extra are imported when a
subcommand runs.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .. import __version__ as NOTEWRENCH_VERSION
from .errors import ReviewError

PROG = "notewrench browser-bookmarks"

EPILOG = """\
workflow:
  scan     copy the exports into a new review bundle (review.json + review.html)
  show     print every original field value of each duplicate group
  review   decide groups interactively (m merge, k keep all, s skip, q quit)
  decide   record decisions non-interactively (--group ID or --all)
  apply    write merged bookmarks.html, receipts.json, sources.csv and originals
  verify   check a review bundle's copied inputs or an applied export

exit status: 0 success, 2 invalid input, pending decisions or an existing output
directory (nothing is overwritten), 130 interrupted. Inputs are never modified
and no URL is fetched.

example:
  notewrench browser-bookmarks scan --html firefox.html chrome.html \\
      --linkding linkding.html --out review
"""


def _display_sources(plan: dict, emit=print) -> None:
    for source in plan["snapshot"]["sources"]:
        emit(f"{source['id']} ({source['format']}): {json.dumps(source['original_path'], ensure_ascii=False)}")


def _display_group(records: dict[str, dict], group: dict, emit=print) -> None:
    from .review import FIELDS

    emit(f"{group['id']} [{group['decision']['action']}] {json.dumps(group['key'], ensure_ascii=False)}")
    emit("Conflicting fields: " + (", ".join(group["conflicts"]) or "none"))
    for member in group["members"]:
        record = records[member]
        emit(f"  {member}:")
        for field in FIELDS:
            emit(f"    {field}: {json.dumps(record[field], ensure_ascii=False)}")
        for warning in record["warnings"]:
            emit(f"    warning: {warning}")
    emit("Proposal: " + json.dumps(group["decision"]["fields"], ensure_ascii=False))


def _parse_choice(text: str):
    if text.startswith(("{", '"')):
        from .review import decode_json

        return decode_json(text, "custom field choice")
    return text


def interactive_review(path: Path, *, ask=input, emit=print) -> int:
    from .review import FIELDS, load_review, save_review, status, validate_decision

    plan = load_review(path)
    _display_sources(plan, emit)
    emit('Field choices: @record_id, all/union, earliest/latest, safest/any, or {"value":...}.')
    records = {record["id"]: record for record in plan["snapshot"]["records"]}
    for group in plan["groups"]:
        if group["decision"]["action"] != "pending":
            continue
        _display_group(records, group, emit)
        while True:
            try:
                action = ask("[m]erge, [k]eep all, [s]kip, [q]uit: ").strip().lower()
            except EOFError:
                emit("Input ended; earlier decisions are saved.")
                return 0
            if action in {"q", "quit"}:
                return 0
            if action in {"s", "skip", ""}:
                break
            if action in {"k", "keep"}:
                group["decision"]["action"] = "keep"
                save_review(path, plan)
                break
            if action not in {"m", "merge"}:
                emit("Enter m, k, s, or q.")
                continue
            proposed = dict(group["decision"]["fields"])
            for field in FIELDS:
                while True:
                    current = json.dumps(proposed[field], ensure_ascii=False)
                    try:
                        answer = ask(f"  {field} [{current}] (Enter accepts): ").strip()
                    except EOFError:
                        emit("Input ended; this incomplete group is still pending.")
                        return 0
                    if not answer:
                        break
                    try:
                        choice = _parse_choice(answer)
                        candidate = {**group, "decision": {"action": "merge", "fields": {**proposed, field: choice}}}
                        validate_decision(candidate)
                        proposed[field] = choice
                        break
                    except ReviewError as exc:
                        emit(str(exc))
            group["decision"] = {"action": "merge", "fields": proposed}
            save_review(path, plan)
            emit("Saved " + group["id"])
            break
    emit(json.dumps(status(plan)))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog=PROG, formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EPILOG,
        description="Review duplicate bookmarks across browser and linkding Netscape-HTML exports before "
                    "importing them: every duplicate group needs an explicit merge or keep decision, and the "
                    "result keeps receipts of every original note, tag and source file.")
    root.add_argument("--version", action="version", version=f"{PROG} {NOTEWRENCH_VERSION}")
    commands = root.add_subparsers(dest="command", required=True, metavar="COMMAND")
    scan = commands.add_parser("scan", help="Copy and scan existing exports into a new review bundle")
    scan.add_argument("--html", nargs="+", action="extend", default=[], type=Path, metavar="FILE",
                      help="browser Netscape HTML bookmark export (Firefox, Chrome, Safari, ...)")
    scan.add_argument("--linkding", nargs="+", action="extend", default=[], type=Path, metavar="FILE", help="linkding Netscape HTML export (not API JSON)")
    scan.add_argument("--out", required=True, type=Path, metavar="DIRECTORY", help="new review directory (must not exist)")
    for name, help_text in (("status", "Count pending decisions"), ("show", "Show each original field value"), ("review", "Review pending groups interactively"), ("compat", "Check decided output against pinned linkding import rules"), ("render", "Refresh the read-only HTML after manual JSON edits")):
        sub = commands.add_parser(name, help=help_text)
        sub.add_argument("plan", type=Path, metavar="REVIEW_JSON_OR_DIRECTORY")
        if name == "show":
            sub.add_argument("--group", help="Show only one cluster")
            sub.add_argument("--include-singletons", action="store_true")
    decide = commands.add_parser("decide", help="Record explicit merge/keep choices; original snapshot is immutable")
    decide.add_argument("plan", type=Path, metavar="REVIEW_JSON_OR_DIRECTORY")
    scope = decide.add_mutually_exclusive_group(required=True)
    scope.add_argument("--group", action="append", help="Cluster ID; repeat for multiple clusters")
    scope.add_argument("--all", action="store_true", help="Explicitly apply this policy to every duplicate group")
    decide.add_argument("--action", required=True, choices=["merge", "keep", "pending"])
    decide.add_argument("--field", action="append", default=[], metavar="FIELD=CHOICE", help='@s1:2, all, union, earliest/latest, safest/any, or {"value":...}')
    apply = commands.add_parser("apply", help="Write merged HTML, receipts, mapping CSV and byte-exact originals")
    apply.add_argument("plan", type=Path, metavar="REVIEW_JSON_OR_DIRECTORY")
    apply.add_argument("--out", required=True, type=Path, metavar="DIRECTORY", help="new export directory (must not exist)")
    verify = commands.add_parser("verify", help="Verify a review's copied inputs or an export's manifest")
    verify.add_argument("bundle", type=Path, metavar="REVIEW_JSON_OR_EXPORT_DIRECTORY")
    return root


def run(args) -> int:
    from .compatibility import assess_linkding
    from .merging import apply_review, make_outputs
    from .review import FIELDS, load_review, refresh_view, save_review, status, validate_decision
    from .verification import verify_export

    if args.command == "scan":
        from .review import create_review

        plan = create_review([(path, "html") for path in args.html] + [(path, "linkding") for path in args.linkding], args.out)
        print(json.dumps({**status(plan), "review": str(args.out / "review.json"), "view": str(args.out / "review.html")}, ensure_ascii=False))
        return 0
    if args.command == "verify":
        if args.bundle.is_dir() and (args.bundle / "review.json").is_file() and not (args.bundle / "manifest.json").exists():
            result = {"verified_sources": len(load_review(args.bundle / "review.json")["snapshot"]["sources"])}
        elif args.bundle.is_dir():
            result = verify_export(args.bundle)
        else:
            result = {"verified_sources": len(load_review(args.bundle)["snapshot"]["sources"])}
        print(json.dumps(result))
        return 0
    if args.plan.is_dir():
        args.plan = args.plan / "review.json"
    if args.command == "review":
        if not sys.stdin.isatty():
            raise ReviewError("Interactive review needs a terminal; use show + decide or edit decision fields in JSON")
        return interactive_review(args.plan)
    if args.command == "render":
        refresh_view(args.plan)
        print(json.dumps({"view": str(args.plan.parent / "review.html")}))
        return 0
    plan = load_review(args.plan)
    if args.command == "status":
        print(json.dumps(status(plan)))
    elif args.command == "compat":
        print(json.dumps(assess_linkding(make_outputs(plan)), ensure_ascii=False, indent=2))
    elif args.command == "show":
        groups = [group for group in plan["groups"] if (not args.group or group["id"] == args.group) and (args.group or args.include_singletons or len(group["members"]) > 1)]
        if args.group and not groups:
            raise ReviewError(f"Unknown group: {args.group}")
        records = {record["id"]: record for record in plan["snapshot"]["records"]}
        _display_sources(plan)
        if not groups:
            print("No duplicate groups to review. Use --include-singletons to show every record.")
        for group in groups:
            _display_group(records, group)
    elif args.command == "decide":
        group_ids = {group["id"] for group in plan["groups"]}
        if args.group and set(args.group) - group_ids:
            raise ReviewError("Unknown group(s): " + ", ".join(sorted(set(args.group) - group_ids)))
        changes = {}
        for setting in args.field:
            field, separator, choice = setting.partition("=")
            if not separator or field not in FIELDS:
                raise ReviewError(f"Expected FIELD=CHOICE; valid fields: {', '.join(FIELDS)}")
            changes[field] = _parse_choice(choice)
        changed = 0
        for group in plan["groups"]:
            if (args.all and len(group["members"]) > 1) or (args.group and group["id"] in args.group):
                group["decision"] = {"action": args.action, "fields": {**group["decision"]["fields"], **changes}}
                validate_decision(group)
                changed += 1
        if changed:
            save_review(args.plan, plan)
        print(json.dumps({"updated_groups": changed, **status(plan)}))
    elif args.command == "apply":
        result = apply_review(plan, args.plan, args.out)
        print(json.dumps({**result, "directory": str(args.out)}, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run ``notewrench browser-bookmarks`` with ``argv`` (default ``sys.argv[1:]``); returns the exit status."""
    try:
        result = run(parser().parse_args(argv))
        sys.stdout.flush()
        return result
    except BrokenPipeError:
        # Prevent a second flush error during interpreter shutdown when a user
        # intentionally stops reading (for example, `show | head`).
        try:
            descriptor = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(descriptor, sys.stdout.fileno())
            finally:
                os.close(descriptor)
        except (OSError, ValueError, AttributeError):
            pass
        return 0
    except (ReviewError, OSError) as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted; completed decisions remain saved.", file=sys.stderr)
        return 130


__all__ = ["interactive_review", "main", "parser"]

if __name__ == "__main__":
    raise SystemExit(main())
