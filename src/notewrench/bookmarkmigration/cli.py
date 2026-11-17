# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (MIT; see licenses/bookmark-migration-MIT.txt)
"""``notewrench bookmark-migration`` command line: compare two exports read-only, write reports."""

import argparse
import sys
from pathlib import Path

from .. import __version__ as NOTEWRENCH_VERSION
from .attachments import attach_manifest
from .compare import compare_exports
from .model import AuditError
from .parsers import load_export
from .reports import VERDICTS, write_reports

PROG = "notewrench bookmark-migration"

EPILOG = """exit status:
  0  no differences and nothing left unchecked within the declared scope
  1  reports written; missing, added, conflicting, duplicate or missing-attachment findings
  2  input, output or usage error (no new report)
  3  reports written; no differences, but some fields or entries could not be checked
"""


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog=PROG,
        description="Read-only audit of a bookmark migration: compare the export from before and\n"
                    "the export from after (Netscape bookmarks HTML or Karakeep JSON, in any\n"
                    "combination) offline and write a traceable Markdown, JSON and HTML report.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("source", type=Path, help="export from before the migration (Netscape HTML or Karakeep JSON)")
    result.add_argument("target", type=Path, help="export from after the migration (Netscape HTML or Karakeep JSON)")
    for side in ("source", "target"):
        result.add_argument(f"--{side}-format", choices=("auto", "netscape", "karakeep"), default="auto",
                            help=f"format of the {side} export (default: auto, detected from the content)")
        result.add_argument(f"--{side}-attachments", type=Path, metavar="MANIFEST",
                            help=f"optional complete attachment manifest for the {side} export (this command's own JSON format)")
    result.add_argument("--url-mode", choices=("conservative", "raw"), default="conservative",
                        help="conservative normalization (default) or exact raw URL match")
    result.add_argument("--output-dir", type=Path, default=Path("audit-report"), help="report directory (default: audit-report)")
    result.add_argument("--formats", nargs="+", choices=("md", "json", "html"), default=["md", "json", "html"],
                        help="report formats to write (default: md json html)")
    result.add_argument("--force", action="store_true", help="overwrite existing reports; inputs and attachments are never overwritten")
    result.add_argument("--version", action="version", version=f"{PROG} {NOTEWRENCH_VERSION}")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        source = load_export(args.source, args.source_format)
        target = load_export(args.target, args.target_format)
        attach_manifest(source, args.source_attachments)
        attach_manifest(target, args.target_attachments)
        report = compare_exports(source, target, args.url_mode)
        protected = [args.source, args.target] + [p for p in (args.source_attachments, args.target_attachments) if p is not None]
        for export, manifest in ((source, args.source_attachments), (target, args.target_attachments)):
            if manifest:
                for entry in export.entries:
                    if entry.attachments.status == "available":
                        protected.extend(manifest.resolve().parent / ref["path"] for ref in entry.attachments.value if ref["path"] is not None)
        paths = write_reports(report, args.output_dir, list(dict.fromkeys(args.formats)), args.force, protected)
        s = report["summary"]
        print(f'{VERDICTS[report["verdict"]]}: matched {s["matched"]}, missing {s["missing"]}, added {s["added"]}, field conflicts {s["conflicts"]}.')
        print(f'Field comparisons not possible {s["not_comparable"]}; entries not audited {s["source_skipped"] + s["target_skipped"]}; local attachments missing {s["attachment_local_missing"]}.')
        for path in paths:
            print(path)
        return report["exit_code"]
    except (AuditError, OSError, ValueError, RecursionError) as exc:
        print(f"{PROG}: error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(f"{PROG}: cancelled", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
