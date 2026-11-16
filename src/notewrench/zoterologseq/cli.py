# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zotlog-sync contributors
"""``notewrench zotero-logseq``: all sync results (including conflicts) are JSON."""
import argparse
import json
from pathlib import Path
import sys

from .. import __version__ as NOTEWRENCH_VERSION
from . import Invalid, synchronize
from .archive import apply_archive, plan_archive
from .restore import apply_restore, plan_restore
from .recovery import audit_recovery


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='notewrench zotero-logseq',
        description='Offline CSL-JSON → Logseq Markdown file graph. Close apps before syncing.',
        epilog='Exit: 0 success, 2 invalid/unsupported input, conflicts or opted-in audit ambiguity, '
               '3 I/O failure. No pages are deleted.')
    parser.add_argument('--version', action='version', version=f'%(prog)s {NOTEWRENCH_VERSION}')
    parser.add_argument('source', type=Path, help='CSL-JSON array, or archive batch directory for restore/audit modes')
    parser.add_argument('--graph', type=Path, required=True, help='Logseq file graph root (contains pages/)')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--dry-run', action='store_true', help='plan sync without writing or locking the graph')
    modes.add_argument('--archive-plan', type=Path, metavar='FILE',
                       help='write a new dry-run orphan decision file outside the graph; do not sync')
    modes.add_argument('--archive-apply', type=Path, metavar='FILE',
                       help='revalidate an orphan decision file and move pages into a recovery archive; do not sync')
    modes.add_argument('--restore-plan', type=Path, metavar='FILE',
                       help='write a new restore decision outside the graph; source is a completed archive batch')
    modes.add_argument('--restore-apply', type=Path, metavar='FILE',
                       help='revalidate a restore decision and move batch pages back without overwriting')
    modes.add_argument('--recovery-audit', action='store_true',
                       help='read-only audit of archive/restore evidence; no lock, moves or writes')
    parser.add_argument('--fail-on-ambiguity', action='store_true',
                        help='with --recovery-audit, exit 2 for ambiguous evidence; JSON unchanged, I/O remains exit 3')
    args = parser.parse_args(argv)
    if args.fail_on_ambiguity and not args.recovery_audit:
        parser.error('--fail-on-ambiguity requires --recovery-audit')
    try:
        if args.recovery_audit:
            report = audit_recovery(args.source, args.graph)
        elif args.archive_plan is not None:
            report = plan_archive(args.source, args.graph, args.archive_plan)
        elif args.archive_apply is not None:
            report = apply_archive(args.source, args.graph, args.archive_apply)
        elif args.restore_plan is not None:
            report = plan_restore(args.source, args.graph, args.restore_plan)
        elif args.restore_apply is not None:
            report = apply_restore(args.source, args.graph, args.restore_apply)
        else:
            report = synchronize(args.source, args.graph, dry_run=args.dry_run)
        code = 3 if report['status'] == 'partial' else 2 if report['conflicts'] else 0
        if code == 0 and args.fail_on_ambiguity and report['ambiguous']:
            code = 2
    except Invalid as exc:
        report, code = {'status': 'error', 'error': str(exc)}, 2
    except OSError as exc:
        report, code = {'status': 'error', 'error': str(exc)}, 3
    except RecursionError:
        report, code = {'status': 'error', 'error': 'JSON nesting is too deep'}, 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return code


if __name__ == '__main__':
    sys.exit(main())
