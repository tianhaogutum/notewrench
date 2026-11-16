import argparse
import json
from pathlib import Path
import sys

from .. import __version__ as NOTEWRENCH_VERSION
from .compare import make_plan
from .decisions import merge_plan
from .files import validate_verification_input
from .reader import ReviewError, locate_library, read_library


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def nonnegative_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a nonnegative integer") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("must be a nonnegative integer")
    return number


def lock_timeout(value: str) -> float:
    number = float(value)
    if not 0 <= number <= 30:
        raise argparse.ArgumentTypeError("must be between 0 and 30 seconds")
    return number


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="notewrench calibre-dupes", description=(
        "Offline, read-only Calibre duplicate preflight. One library: within-library; "
        "two libraries: cross-library only. Output is a JSON human review plan, never commands. "
        "Use closed, clean rollback-mode SQLite libraries; WAL/journal states are refused."))
    result.add_argument("libraries", metavar="LIBRARY", nargs="+", help="one or two library directories or metadata.db paths")
    result.add_argument("-o", "--output", type=Path, help="create a NEW JSON file outside input libraries (default: stdout)")
    result.add_argument("--max-pairs", type=positive_int, default=100_000, help="abort above this many blocked pairs (default: 100000)")
    result.add_argument("--timeout", type=lock_timeout, default=1.0, help="SQLite lock timeout, 0–30 seconds (default: 1)")
    result.add_argument("--verify-files", action="store_true", help=(
        "opt in to read-only size/SHA256 checks of declared format files inside the explicit "
        "library roots; no symlinks followed (POSIX only; default: metadata only)"))
    result.add_argument("--max-total-file-bytes", type=nonnegative_int, metavar="N", help=(
        "abort before cumulative format-file reads exceed N bytes across all libraries; "
        "requires --verify-files (0 allowed; default: unlimited; excludes database reads)"))
    result.add_argument("--max-file-records", type=nonnegative_int, metavar="N", help=(
        "abort before inspecting format declaration N+1 across all libraries, including "
        "missing/rejected paths; requires --verify-files (0 allowed; default: unlimited)"))
    result.add_argument("--decisions", type=Path, help=(
        "validate a human-edited decisionTemplate JSON against current library/member hashes; "
        "emit keeper-specific format additions and manual metadata review only, never write libraries"))
    result.add_argument("--version", action="version", version="%(prog)s " + NOTEWRENCH_VERSION)
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if len(args.libraries) > 2:
        parser().error("provide one or two libraries")
    if args.max_total_file_bytes is not None and not args.verify_files:
        parser().error("--max-total-file-bytes requires --verify-files")
    if args.max_file_records is not None and not args.verify_files:
        parser().error("--max-file-records requires --verify-files")
    try:
        if args.verify_files:
            for value in args.libraries:
                validate_verification_input(value)
        locations = [locate_library(value) for value in args.libraries]
        if len(locations) == 2 and locations[0][1].samefile(locations[1][1]):
            raise ReviewError("the two libraries refer to the same database")
        if args.output is not None:
            try:
                requested_output = args.output.expanduser()
            except RuntimeError as exc:
                raise ReviewError(f"cannot expand output path {args.output}: {exc}") from exc
            output = requested_output.resolve()
            if any(output.is_relative_to(root) for root, db in locations):
                raise ReviewError("output must be outside all input libraries")
            if output.exists() or requested_output.is_symlink():
                raise ReviewError("output already exists; choose a new path (no overwrite)")
        libraries = [read_library(value, label, timeout=args.timeout)
                     for value, label in zip(args.libraries, ("A", "B"))]
        plan = make_plan(libraries, max_pairs=args.max_pairs, verify_files=args.verify_files,
                         max_total_file_bytes=args.max_total_file_bytes,
                         max_file_records=args.max_file_records)
        if args.decisions is not None:
            plan["mergePlan"] = merge_plan(plan, args.decisions)
        encoded = json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        encoded.encode("utf-8")  # fail before creating a report for non-Unicode paths
        if args.output is None:
            sys.stdout.write(encoded)
        else:
            # Exclusive creation also protects a racing symlink/hardlink. No
            # report opens until the entire plan is successfully serialized.
            created = False
            try:
                with output.open("x", encoding="utf-8") as stream:
                    created = True
                    stream.write(encoded)
            except (OSError, UnicodeError):
                if created:
                    output.unlink(missing_ok=True)
                raise
        return 0
    except (ReviewError, OSError, ValueError) as exc:
        print(f"notewrench calibre-dupes: {exc}", file=sys.stderr)
        return 2
