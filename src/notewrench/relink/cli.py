# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum
"""``notewrench relink`` command line: discovery, review and selected patch output are separate steps.

The parser, ``--help`` and ``--version`` use only the standard library; the
third-party parsers of the optional ``relink`` extra are imported when a
sub-command actually runs.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .. import __version__ as NOTEWRENCH_VERSION
from . import __version__

PROG = "notewrench relink"
# Distribution names reported by `notewrench relink doctor`.
PACKAGES = ("beautifulsoup4", "idna", "markdown-it-py", "mdit-py-plugins", "warcio")


def atomic_write(path: Path, text: str) -> None:
    from .workflow import atomic_write as write

    write(path, text)


def _json_config(path: Path, limit: int = 4 * 1024 * 1024):
    from .archive import read_bounded

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key in {path}: {key}")
            result[key] = value
        return result

    return json.loads(read_bounded(path, limit=limit), object_pairs_hook=unique)


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Find existing private web captures for Markdown citations and write a patch "
            "for the copies you select. Never contacts the web."
        ),
        epilog=(
            "steps: index ARCHIVE --db INDEX, then scan NOTES --db INDEX --out PLAN, then "
            "patch PLAN --select LINK=CANDIDATE --out PATCH. 'relink doctor' checks this "
            "command's SQLite and parser setup (it is not 'notewrench doctor')."
        ),
    )
    parser.add_argument("--version", action="version", version=f"{PROG} {NOTEWRENCH_VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)
    index = sub.add_parser("index", help="build a disposable local archive index")
    sub.add_parser(
        "doctor", help="check local Python, SQLite FTS5, and parser packages for relink"
    )
    index.add_argument("archive", type=Path)
    index.add_argument("--db", type=Path, required=True)
    scan = sub.add_parser("scan", help="write candidates and evidence; never edits notes")
    scan.add_argument("notes", type=Path, nargs="+")
    scan.add_argument("--db", type=Path, required=True)
    scan.add_argument("--out", type=Path, required=True, help="JSON plan path")
    scan.add_argument("--review", type=Path, help="optional human-readable Markdown review")
    scan.add_argument(
        "--quotes", type=Path, help="JSON object mapping original URL to quotation(s)"
    )
    scan.add_argument("--only-urls", type=Path, help="UTF-8 file with one failed URL per line")
    scan.add_argument("--limit", type=int, default=20, help="candidates per link, 1–100")
    patch = sub.add_parser("patch", help="generate a patch for explicitly selected candidates")
    patch.add_argument("plan", type=Path)
    patch.add_argument("--select", action="append", default=[], metavar="LINK=CANDIDATE")
    patch.add_argument(
        "--selections", type=Path, help="JSON object of link IDs to selected candidate IDs"
    )
    patch.add_argument("--base", type=Path, default=Path.cwd(), help="patch application directory")
    patch.add_argument(
        "--absolute", action="store_true", help="write file:// URIs instead of relative links"
    )
    patch.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run ``notewrench relink`` with ``argv`` (default ``sys.argv[1:]``); returns the exit status."""
    args = parser().parse_args(argv)
    try:
        from .archive import ArchiveChangedError
    except ModuleNotFoundError:
        if args.command != "doctor":
            raise  # the dispatcher turns this into the 'notewrench[relink]' install hint
        ArchiveChangedError = ValueError  # noqa: N806 - doctor reports missing packages itself
    try:
        if args.command == "doctor":
            with sqlite3.connect(":memory:") as connection:
                connection.execute(
                    "CREATE VIRTUAL TABLE probe USING fts5(body, tokenize='trigram')"
                )
            print(
                json.dumps(
                    {
                        "archive_relink": __version__,
                        "python": sys.version.split()[0],
                        "sqlite": sqlite3.sqlite_version,
                        "fts5_trigram": True,
                        "packages": {name: _package_version(name) for name in PACKAGES},
                    },
                    indent=2,
                )
            )
        elif args.command == "index":
            from .workflow import build_index

            result = build_index(args.archive, args.db)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "scan":
            from .archive import read_bounded
            from .urls import url_key
            from .workflow import plan_inputs, protect_outputs, review_markdown, scan_notes

            protect_outputs(
                [args.out, args.review],
                [args.db, *args.notes, *[p for p in (args.quotes, args.only_urls) if p]],
            )
            quotes = _json_config(args.quotes) if args.quotes else {}
            if not isinstance(quotes, dict):
                raise ValueError("--quotes must contain a JSON object")
            only = None
            if args.only_urls:
                only = set()
                for line in read_bounded(args.only_urls).decode("utf-8").split("\n"):
                    line = line.strip()
                    if line.strip() and not line.startswith("#"):
                        key = url_key(line)
                        if not key:
                            raise ValueError(f"invalid HTTP(S) URL in --only-urls: {line}")
                        only.add(key)
            plan = scan_notes(args.db, args.notes, quotes, only, args.limit)
            protect_outputs([args.out, args.review], plan_inputs(plan))
            serialized = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"
            if len(serialized.encode("utf-8")) > 64 * 1024 * 1024:
                raise ValueError(
                    "serialized plan exceeds 64 MiB; scan fewer notes or lower --limit"
                )
            if args.review:
                atomic_write(args.review, review_markdown(plan))
            # Publish the authoritative plan only after its optional review is
            # ready, so a failed review write cannot replace a previous plan.
            atomic_write(args.out, serialized)
            print(f"Reviewed {len(plan['links'])} citations; plan: {args.out}")
        else:
            from .workflow import make_patch, plan_inputs, protect_outputs

            plan = _json_config(args.plan, limit=64 * 1024 * 1024)
            protected = [args.plan, *plan_inputs(plan)]
            if args.selections:
                protected.append(args.selections)
            protect_outputs([args.out], protected)
            selections = _json_config(args.selections) if args.selections else {}
            if not isinstance(selections, dict) or any(
                not isinstance(key, str) or (value is not None and not isinstance(value, str))
                for key, value in selections.items()
            ):
                raise ValueError("--selections must map link IDs to candidate IDs or null")
            selections = {key: value for key, value in selections.items() if value is not None}
            for selection in args.select:
                if selection.count("=") != 1:
                    raise ValueError("each --select must be LINK=CANDIDATE")
                link, candidate = selection.split("=")
                if link in selections:
                    raise ValueError(f"duplicate selection for {link}")
                selections[link] = candidate
            result = make_patch(plan, selections, args.base, args.absolute)
            atomic_write(args.out, result)
            print(f"Wrote {len(selections)} selected repairs: {args.out}")
        return 0
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RecursionError,
        ArchiveChangedError,
        sqlite3.Error,
    ) as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
