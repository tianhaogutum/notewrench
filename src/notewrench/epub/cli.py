"""Command line entry point; all operations are offline."""
import argparse
import json
from pathlib import Path
import sys

from notewrench import __version__ as NOTEWRENCH_VERSION

from . import BundleError
from .core import check_destinations, plan, prepare, publish_new
from .epub import DEFAULT_MODIFIED, json_bytes, validate_epub
from .ordering import load_chapter_order


def parser():
    root = argparse.ArgumentParser(prog="notewrench epub", description="Build deterministic offline EPUB 3 books from cached Wallabag/Karakeep JSON.")
    root.add_argument("--version", action="version", version=f"%(prog)s {NOTEWRENCH_VERSION}")
    sub = root.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="convert local cached bodies to EPUB and a JSON report",
                           description="Convert locally cached Wallabag/Karakeep article bodies into one new EPUB 3 and a JSON provenance/loss report. Offline; nothing is fetched.")
    build.add_argument("-o", "--output", type=Path, required=True, help="new EPUB path (never overwritten)")
    build.add_argument("--report", type=Path, help="new JSON path; default OUTPUT with .report.json suffix")
    build.add_argument("--title", default="Reading bundle", help="book title (default: %(default)s)")
    build.add_argument("--author", default="notewrench", help="book author/creator (default: %(default)s)")
    build.add_argument("--language", default="en", help="simple BCP 47 tag, e.g. zh-Hans")
    build.add_argument("--modified", default=DEFAULT_MODIFIED, help="fixed UTC metadata timestamp; default %(default)s")
    build.add_argument("--dry-run", action="store_true", help="build/validate in memory and print full report; write no files")
    planning = sub.add_parser("plan", help="print usable keys and provenance in source or proposed order; no EPUB or files",
                              description="Print the selected entries, their keys and provenance as JSON in source or proposed chapter order. Writes no EPUB or other files.")
    for command in (build, planning):
        command.add_argument("--chapter-order", type=Path, help="strict UTF-8 JSON array of all usable selected keys in chapter order (<=1 MiB/10000 keys)")
        command.add_argument("inputs", type=Path, nargs="*", help="UTF-8 JSON files in reading order")
        command.add_argument("--format", choices=("auto", "wallabag", "karakeep-export", "karakeep-api"), default="auto",
                             help="input format (default: auto, detect from the file contents)")
        command.add_argument("--text-mode", choices=("literal", "markdown"), default="literal",
                           help="Karakeep text bodies: literal (default) or restricted static Markdown")
        command.add_argument("--archive-bundle", dest="bagkeep_bundle", type=Path, metavar="DIR",
                             help="explicit `notewrench archive api-import` bundle directory (manifest tool bagkeep 0.1.0, schema 1; offline)")
        # Former spelling, kept as a hidden alias.
        command.add_argument("--bagkeep-bundle", dest="bagkeep_bundle", type=Path, help=argparse.SUPPRESS)
        command.add_argument("--image-manifest", type=Path, help="explicit local PNG/JPEG declarations (offline; paths below manifest directory)")
        command.add_argument("--tag", action="append", default=[], help="include any matching tag (repeatable, case-sensitive)")
        command.add_argument("--id", action="append", default=[], help="include exact key INPUT_NUMBER:ID; native Karakeep IDs are entry-N")
        command.add_argument("--exclude-id", action="append", default=[], help="exclude exact known key INPUT_NUMBER:ID (repeatable; overrides tag/ID inclusion)")
        command.add_argument("--skip-missing", action="store_true", help="explicitly omit missing/empty/unsupported bodies and record each in report")
    validate = sub.add_parser("validate", help="check ZIP/XML/manifest/spine/nav/images in the notewrench epub static XHTML/PNG/JPEG profile",
                              description="Structurally check an EPUB against the static XHTML/PNG/JPEG profile that notewrench epub writes. Read-only; not a replacement for EPUBCheck.")
    validate.add_argument("epub", type=Path, help="EPUB file to check (read-only)")
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "validate":
            print(json.dumps(validate_epub(args.epub), sort_keys=True))
            return 0
        if not args.inputs and args.bagkeep_bundle is None:
            raise BundleError(f"{args.command} requires JSON inputs or --archive-bundle")
        if args.command == "plan":
            chapter_order = load_chapter_order(args.chapter_order) if args.chapter_order is not None else None
            report = plan(args.inputs, format=args.format, tags=args.tag, ids=args.id, exclude_ids=args.exclude_id,
                          skip_missing=args.skip_missing, text_mode=args.text_mode,
                          image_manifest=args.image_manifest, bagkeep_bundle=args.bagkeep_bundle,
                          chapter_order=chapter_order)
            print(json_bytes(report).decode("utf-8"), end="")
            if report["errors"]:
                print("notewrench epub: " + " ".join(report["errors"]), file=sys.stderr)
                return 2
            return 0
        report_path = args.report or args.output.with_suffix(".report.json")
        check_destinations([args.output, report_path], [*args.inputs, *([args.image_manifest] if args.image_manifest else []),
                      *([args.chapter_order] if args.chapter_order is not None else [])])
        chapter_order = load_chapter_order(args.chapter_order) if args.chapter_order is not None else None
        payload, report = prepare(args.inputs, format=args.format, title=args.title,
                                  author=args.author, language=args.language, modified=args.modified,
                                  tags=args.tag, ids=args.id, exclude_ids=args.exclude_id, skip_missing=args.skip_missing,
                                  text_mode=args.text_mode, image_manifest=args.image_manifest,
                                  bagkeep_bundle=args.bagkeep_bundle, chapter_order=chapter_order)
        if args.dry_run:
            print(json_bytes(report).decode("utf-8"), end="")
        else:
            files = [(report_path, json_bytes(report))]
            if payload is not None:
                files.insert(0, (args.output, payload))
            publish_new(files)
            print(json.dumps({"status": report["status"], "epub": str(args.output) if payload else None,
                              "report": str(report_path), "included": len(report["included"]),
                              "skipped": len(report["skipped"])}, ensure_ascii=False))
        if payload is None:
            print("notewrench epub: " + " ".join(report["errors"]), file=sys.stderr)
            return 2
        return 0
    except (BundleError, OSError, RecursionError) as exc:
        print(f"notewrench epub: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
