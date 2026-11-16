"""CLI and exclusive output publication. SPDX-License-Identifier: AGPL-3.0-only."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

from .. import __version__ as NOTEWRENCH_VERSION
from .core import BagnotesError, inspect_sources, parse, parse_sources, validate_namespace, json_encode
from .render import FORMATS, render


def check_output(output: Path) -> None:
    if output.exists() or output.is_symlink():
        raise BagnotesError("output_conflict", "Output path already exists; choose a new directory", str(output))
    if not output.parent.is_dir():
        raise BagnotesError("output_parent", "Output parent must already be a directory", str(output.parent))


def publish(output: Path, files: dict[str, bytes]) -> None:
    """Reserve a new directory exclusively. Roll back our files on ordinary I/O errors.

    Publication is not crash-atomic: a killed process can leave a partial directory.
    Never replace existing files, including in a competing concurrent invocation.
    """
    output.mkdir()  # Exclusive reservation; keep outside cleanup if it fails.
    created_files: list[Path] = []
    created_dirs: list[Path] = []
    try:
        for name, content in files.items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Internal output path must be relative and contained")
            target = output / relative
            if target.parent != output and target.parent not in created_dirs:
                target.parent.mkdir()
                created_dirs.append(target.parent)
            with target.open("xb") as handle:
                created_files.append(target)
                handle.write(content)
        # report.json is inserted last and serves as a completion marker.
    except BaseException:
        for target in reversed(created_files):
            try:
                target.unlink()
            except OSError:
                pass
        for directory in [*reversed(created_dirs), output]:
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def deletion_limit(value: str) -> int:
    if not re.fullmatch(r'[0-9]+', value):
        raise argparse.ArgumentTypeError('expected a nonnegative ASCII decimal integer')
    try:
        return int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('deletion limit is too large') from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="notewrench wallabag-notes", description="Export Wallabag v2 annotations offline to Markdown, Logseq blocks and CSV.")
    parser.add_argument("input", nargs="?", type=Path,
                        help="legacy single-account export; IDs are scoped to that account only")
    parser.add_argument("--source", nargs=2, action="append", metavar=("NAMESPACE", "FILE"),
                        help="namespaced Wallabag v2 JSON export; repeat to merge offline files (cannot combine with input)")
    parser.add_argument("--output", "-o", type=Path, help="required for export: output directory (new unless --update; parent must exist)")
    parser.add_argument("--format", choices=FORMATS, help="output format (default: all)")
    parser.add_argument("--inspect-conflicts", action="store_true",
                        help="read-only --source inspection: JSON for all conflicting pairs; no export options")
    parser.add_argument("--conflict-values", action="store_true",
                        help="include original differing field values and presence flags (requires --inspect-conflicts)")
    parser.add_argument("--inspect-managed", type=Path, metavar="DIR",
                        help="read-only managed bundle inspection: artifact issues and all edited regions with expected/current diffs")
    parser.add_argument("--managed-only", action="store_true",
                        help="suppress whole-page diagnostics for damaged markers (requires --inspect-managed)")
    parser.add_argument("--metadata-only", action="store_true",
                        help="omit managed text/diffs and raw proposed values; retain fingerprints/paths (requires --inspect-managed; implies --managed-only)")
    parser.add_argument("--propose-update", action="store_true",
                        help="with --inspect-managed, also compare explicit next complete snapshot input(s); no writes")
    parser.add_argument("--proposed-paths", action="store_true",
                        help="also inspect new-page destinations for collisions (requires --propose-update)")
    parser.add_argument("--proposed-regions", action="store_true",
                        help="preview saved-to-proposed managed-region bytes and diffs (requires --propose-update)")
    parser.add_argument("--region-relationships", action="store_true",
                        help="classify saved/local/proposed tracked regions by exact bytes (requires --propose-update)")
    parser.add_argument("--local-proposed-regions", action="store_true",
                        help="preview differing local-to-proposed tracked region bytes/diffs (requires --region-relationships)")
    parser.add_argument("--update", action="store_true",
                        help="initialize or update a managed directory from a complete snapshot; preserve personal notes")
    parser.add_argument("--max-deleted-annotations", type=deletion_limit, metavar="N",
                        help="refuse more than N annotation deletions (nonnegative integer; requires --update)")
    parser.add_argument("--dry-run", action="store_true", help="validate and print a plan without writing any files")
    parser.add_argument("--version", action="version", version="%(prog)s " + NOTEWRENCH_VERSION)
    args = parser.parse_args(argv)
    if args.max_deleted_annotations is not None and not args.update:
        parser.error("--max-deleted-annotations requires --update")
    if args.metadata_only and args.inspect_managed is None:
        parser.error("--metadata-only requires --inspect-managed DIR")
    if args.managed_only and args.inspect_managed is None:
        parser.error("--managed-only requires --inspect-managed DIR")
    if args.proposed_paths and not args.propose_update:
        parser.error("--proposed-paths requires --propose-update")
    if args.proposed_regions and not args.propose_update:
        parser.error("--proposed-regions requires --propose-update")
    if args.local_proposed_regions and not args.region_relationships:
        parser.error("--local-proposed-regions requires --region-relationships")
    if args.region_relationships and not args.propose_update:
        parser.error("--region-relationships requires --propose-update")
    if args.conflict_values and not args.inspect_conflicts:
        parser.error("--conflict-values requires --inspect-conflicts")
    if args.propose_update and args.inspect_managed is None:
        parser.error("--propose-update requires --inspect-managed DIR")
    if args.inspect_managed is not None:
        if args.output is not None or args.update or args.dry_run or args.inspect_conflicts:
            parser.error("--inspect-managed DIR cannot combine with export/inspection options")
        if args.propose_update:
            if (args.input is not None and args.source) or (args.input is None and not args.source):
                parser.error("--propose-update requires one input file or --source NAMESPACE FILE pairs")
        elif args.input is not None or args.source or args.format is not None:
            parser.error("--inspect-managed inputs and --format require --propose-update")
    elif (args.input is not None and args.source) or (args.input is None and not args.source):
        parser.error("provide either one input file or one or more --source NAMESPACE FILE pairs")
    if args.inspect_conflicts:
        if not args.source:
            parser.error("--inspect-conflicts requires --source NAMESPACE FILE")
        if args.output is not None or args.format is not None or args.update or args.dry_run:
            parser.error("--inspect-conflicts cannot combine with --output, --format, --update or --dry-run")
    elif args.inspect_managed is None and args.output is None:
        parser.error("--output/-o is required for export")
    args.format = args.format or "all"
    try:
        if args.inspect_managed is not None and not args.propose_update:
            from .update import inspect_managed
            report = inspect_managed(args.inspect_managed, managed_only=args.managed_only,
                                     metadata_only=args.metadata_only)
            print(json_encode(report))
            return 2 if report['status'] == 'conflicts' else 0
        if args.source:
            for index, (namespace, _) in enumerate(args.source):
                validate_namespace(namespace, f"$inputs[{index}].namespace")
            inputs = [(namespace, Path(filename).read_bytes()) for namespace, filename in args.source]
            if args.inspect_conflicts:
                report = inspect_sources(inputs, include_values=args.conflict_values)
                for descriptor, (_, filename) in zip(report['inputs'], args.source):
                    descriptor['file'] = filename
                print(json_encode(report))
                return 2 if report['conflicts'] else 0
            export = parse_sources(inputs)
        else:
            export = parse(args.input.read_bytes())
        if args.propose_update:
            from .update import inspect_managed
            report = inspect_managed(args.inspect_managed, next_export=export, format_name=args.format,
                                     include_paths=args.proposed_paths, include_regions=args.proposed_regions,
                                     include_relationships=args.region_relationships,
                                     include_local_regions=args.local_proposed_regions, managed_only=args.managed_only,
                                     metadata_only=args.metadata_only)
            print(json_encode(report))
            return 2 if report['status'] == 'conflicts' else 0
        if args.update:
            from .update import plan_update, publish_update
            plan = plan_update(args.output, export, args.format,
                               max_deleted_annotations=args.max_deleted_annotations)
            summary = dict(status="dry-run" if args.dry_run else plan.action, output=str(args.output),
                           action=plan.action, counts=plan.report["counts"], warnings=plan.report["warnings"],
                           changes=plan.changes,
                           files=[dict(path=k, bytes=len(v)) for k, v in plan.files.items()])
            if not args.dry_run:
                if plan.action == "initialized":
                    publish(args.output, plan.files)
                else:
                    publish_update(args.output, plan)
            print(json_encode(summary))
            return 0
        files, report = render(export, args.format)
        check_output(args.output)
        summary = dict(status="dry-run" if args.dry_run else "ok", output=str(args.output),
                       counts=report["counts"], warnings=report["warnings"],
                       files=[dict(path=k, bytes=len(v)) for k, v in files.items()])
        if not args.dry_run:
            publish(args.output, files)
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    except BagnotesError as exc:
        print(json.dumps(exc.diagnostic, ensure_ascii=True), file=sys.stderr)
        return 2
    except (OSError, ValueError, RecursionError) as exc:
        print(json.dumps(dict(status="error", code="io_error", message=str(exc)), ensure_ascii=True), file=sys.stderr)
        return 3
