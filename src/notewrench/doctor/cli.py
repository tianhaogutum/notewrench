"""``notewrench doctor`` command line. Exit 0 clean, 1 findings at/above --fail-on, 2 usage/input error."""
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import argparse
import json
import os
import sys

from .. import __version__ as NOTEWRENCH_VERSION
from . import SCHEMA
from .checks import RANK, SEVERITIES, run_checks, top_fixes
from .detect import UsageError, detect

PROG = "notewrench doctor"

EPILOG = """\
detects:
  Zotero      a data folder (zotero.sqlite + storage/) or a zotero.sqlite copy
  Joplin      a .jex export or a raw export folder
  Calibre     a library folder or its metadata.db
  Read-later  Karakeep and Wallabag v2 JSON exports
  Citations   CSL-JSON bibliographies + Markdown files (Pandoc [@key] citations)
  Any other folder is scanned for these (hidden entries and symlinks are skipped).

exit status: 0 nothing at or above --fail-on, 1 findings at or above it, 2 usage
error, unrecognized path, or a source that could not be read. Read-only: nothing
is written. Close Zotero and Calibre first, or point doctor at copies: databases
with -wal/-journal sidecars are refused. Zotero attachment audits need Linux and
are SKIPPED elsewhere.

examples:
  notewrench doctor ~/Zotero ~/Calibre\\ Library
  notewrench doctor joplin.jex karakeep.json wallabag.json
  notewrench doctor thesis/ --fail-on warning --format json
"""


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # usage errors exit 2, like the other commands
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: error: {message}\n")


def parser() -> argparse.ArgumentParser:
    cli = Parser(prog=PROG, formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EPILOG,
                 description="One read-only health check across your Zotero, Joplin, Calibre, Karakeep/Wallabag "
                             "and citation files: what is wrong, and the notewrench command that fixes it.")
    cli.add_argument("--version", action="version", version=f"{PROG} {NOTEWRENCH_VERSION}")
    cli.add_argument("paths", nargs="+", metavar="PATH", help="exports, libraries, or folders that contain them")
    cli.add_argument("--format", choices=("text", "json"), default="text")
    cli.add_argument("--fail-on", choices=(*SEVERITIES, "none"), default="error",
                     help="lowest severity that makes the exit status 1 (default: error)")
    cli.add_argument("--fixes", type=int, default=5, metavar="N", help="top fixes to list in text output (default 5)")
    cli.add_argument("--no-color", action="store_true", help="disable color (also: NO_COLOR, non-TTY stdout)")
    return cli


class Style:
    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _wrap(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.enabled and text else text

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)

    def cyan(self, text: str) -> str:
        return self._wrap("36", text)


def build(results, scan, fail_on: str) -> dict:
    records = [result.record() for result in results]
    counts = {severity: 0 for severity in SEVERITIES}
    for record in records:
        for finding in record["findings"]:
            counts[finding["severity"]] += finding["count"]
    threshold = len(SEVERITIES) if fail_on == "none" else RANK[fail_on] + 1
    failed = any(RANK[f["severity"]] < threshold for record in records for f in record["findings"])
    errored = any(record["status"] == "error" for record in records)
    summary = {"sources": len(records), "with_findings": sum(r["status"] == "findings" for r in records),
               "skipped": sum(r["status"] == "skipped" for r in records),
               "unreadable": sum(r["status"] == "error" for r in records),
               "errors": counts["error"], "warnings": counts["warning"], "notes": counts["note"],
               "fail_on": fail_on, "failed": failed, "exit_status": 2 if errored else (1 if failed else 0),
               "ignored_entries": scan.ignored, "scan_truncated": scan.truncated}
    fixes = [{key: value for key, value in fix.items() if key != "weight"} for fix in top_fixes(results)]
    return {"schema": SCHEMA, "notewrench_version": NOTEWRENCH_VERSION, "read_only": True,
            "summary": summary, "sources": records, "top_fixes": fixes}


def _n(count: int, word: str) -> str:
    return f"{count:,} {word}" + ("" if count == 1 else "s")


def render_text(doc: dict, s: Style, max_fixes: int) -> str:
    summary, records = doc["summary"], doc["sources"]
    checks = f"{len(records)} check" + ("" if len(records) == 1 else "s")
    out = [s.bold("notewrench doctor") + "  " + s.dim(f"{checks} · read-only, nothing is written"), ""]
    width = max(len(record["label"]) for record in records) + 2
    for record in records:
        worst = min((f["severity"] for f in record["findings"]), key=RANK.__getitem__, default=None)
        if record["status"] == "error":
            mark, verdict = s.red("✗"), s.red(record["verdict"])
        elif record["status"] == "skipped":
            mark, verdict = s.dim("-"), s.dim(record["verdict"])
        elif worst == "error":
            mark, verdict = s.red("✗"), record["verdict"]
        elif worst == "warning":
            mark, verdict = s.yellow("!"), record["verdict"]
        else:
            mark, verdict = s.green("✓"), record["verdict"]
        out.append(f"  {mark} {s.bold(record['label'].ljust(width))}{verdict}")
        out.append(f"    {' ' * width}{s.dim(record['where'])}")
    out += ["", s.bold("Top fixes")]
    fixes = doc["top_fixes"][:max(max_fixes, 0)]
    if not doc["top_fixes"]:
        out.append("  " + s.green("Nothing to fix."))
    for index, fix in enumerate(fixes, 1):
        out.append(f"  {index}. {fix['title']}")
        out.append(f"     {s.dim(fix['detail'])}")
        out += [f"     {s.cyan(command)}" for command in fix["commands"]]
    if len(doc["top_fixes"]) > len(fixes):
        out.append(s.dim(f"  ... {len(doc['top_fixes']) - len(fixes)} more (--fixes N, or --format json)"))
    out.append("")
    total = summary["errors"] + summary["warnings"] + summary["notes"]
    if total:
        head = (s.red if summary["errors"] else s.yellow if summary["warnings"] else s.green)(_n(total, "finding"))
        line = (f"{head} in {summary['with_findings']} of {checks}: {_n(summary['errors'], 'error')}, "
                f"{_n(summary['warnings'], 'warning')}, {_n(summary['notes'], 'note')}.")
    else:
        line = s.green(f"No findings in {checks}.")
    if summary["skipped"]:
        line += s.dim(f" {summary['skipped']} skipped.")
    out.append(line)
    if summary["unreadable"]:
        out.append(s.red(f"{summary['unreadable']} source(s) could not be read (exit 2)."))
    else:
        verdict = s.red("FAIL (exit 1)") if summary["failed"] else s.green("pass (exit 0)")
        out.append(f"--fail-on {summary['fail_on']}: {verdict}")
    if summary["scan_truncated"]:
        out.append(s.yellow("note: folder scan stopped at its depth/entry limit; pass sub-folders directly."))
    return "\n".join(out) + "\n"


def _use_color(args) -> bool:
    if args.no_color or args.format != "text" or os.environ.get("NO_COLOR"):
        return False
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        scan = detect(args.paths)
    except UsageError as exc:
        for line in str(exc).splitlines():
            print(f"{PROG}: {line}", file=sys.stderr)
        print(f"Run '{PROG} --help' for the inputs it recognizes.", file=sys.stderr)
        return 2
    doc = build(run_checks(scan.sources), scan, args.fail_on)
    try:
        if args.format == "json":
            sys.stdout.write(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
        else:
            sys.stdout.write(render_text(doc, Style(_use_color(args)), args.fixes))
        sys.stdout.flush()
    except BrokenPipeError:
        try:
            sys.stdout = open(os.devnull, "w")
        except OSError:
            pass
    return doc["summary"]["exit_status"]


__all__ = ["main", "parser"]

if __name__ == "__main__":
    raise SystemExit(main())
