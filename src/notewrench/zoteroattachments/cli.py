"""CLI: stdout reports only; never writes input files or output paths."""
# SPDX-License-Identifier: AGPL-3.0-only
import argparse
import json
import sys

from .. import __version__ as NOTEWRENCH_VERSION
from . import InputError, audit, compare_reports


def markdown(report: dict) -> str:
    # JSON carries *all* evidence in Markdown as well. Choose a fence that cannot
    # be terminated by an untrusted filename or database path.
    payload = json.dumps(report, ensure_ascii=True, indent=2)
    fence = '```'
    while fence in payload:
        fence += '`'
    comparison = 'comparisonVersion' in report
    title = 'Zotero attachment comparison' if comparison else 'Zotero attachment audit'
    complete = f"Comparison complete: **{report['comparisonComplete']}**" if comparison else f"Audit complete: **{report.get('auditComplete', False)}**"
    lines = ['# ' + title, '', complete, '']
    if comparison and report.get('changes') is not None:
        lines += ['| Change | Count |', '| --- | ---: |']
        lines += [f'| {key} | {len(value)} |' for key, value in report['changes'].items()]
    if 'statistics' in report:
        lines += ['| Metric | Value |', '| --- | ---: |']
        lines += [f'| {key} | {value} |' for key, value in report['statistics'].items() if not isinstance(value, dict)]
    lines += ['', 'Orphan directories are candidates only; keep trash and review errors before acting.',
              '', '## Complete evidence', '', fence + 'json', payload, fence, '']
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == 'compare':
        parser = argparse.ArgumentParser(prog='notewrench zotero-attachments compare', description='Compare two JSON audit reports made with --comparison-scope. Reports are never modified.')
        parser.add_argument('before', help='earlier JSON audit report (made with --comparison-scope)')
        parser.add_argument('after', help='later JSON audit report (made with --comparison-scope)')
        parser.add_argument('--map-linked-root', nargs=2, action='append', default=[],
                            metavar=('BEFORE_ROOT', 'AFTER_ROOT'),
                            help='Align BEFORE linked file descendants with AFTER identities once; repeat for disjoint roots. Reads reports only; preserves original paths and hashes.')
        parser.add_argument('--format', choices=('json', 'markdown'), default='json', help='output format on stdout (default: json)')
        args = parser.parse_args(argv[1:])
        report = compare_reports(args.before, args.after, linked_root_mappings=args.map_linked_root)
        code = 2 if report['fatal'] else (0 if report['comparisonComplete'] else 1)
        return emit(report, args.format, code)
    parser = argparse.ArgumentParser(prog='notewrench zotero-attachments', description='Read-only offline Zotero attachment audit (Linux). Supply a frozen standalone SQLite snapshot; never a live DB.')
    parser.epilog = ('subcommands:\n  compare BEFORE.json AFTER.json  compare two saved --comparison-scope reports '
                     '(notewrench zotero-attachments compare --help)')
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.add_argument('--version', action='version', version=f'%(prog)s {NOTEWRENCH_VERSION}')
    parser.add_argument('--snapshot', required=True, help='Explicit standalone snapshot of zotero.sqlite (no WAL/SHM/journal)')
    parser.add_argument('--storage', required=True, help='Storage directory corresponding to the snapshot')
    parser.add_argument('--allow-linked-root', action='append', default=[], metavar='DIRECTORY',
                        help='Allow reading linked files under this directory; repeatable. Symlinks still refused.')
    parser.add_argument('--linked-base-directory', metavar='DIRECTORY',
                        help='Map Zotero attachments:relative/path to this absolute POSIX directory. Also requires --allow-linked-root; no settings are inferred.')
    parser.add_argument('--map-linked-root', nargs=2, action='append', default=[],
                        metavar=('SOURCE', 'DESTINATION'),
                        help='Relocate absolute linked paths from SOURCE to DESTINATION once; repeat for disjoint roots. Authorize the destination separately with --allow-linked-root. Source paths are never read.')
    parser.add_argument('--format', choices=('json', 'markdown'), default='json', help='output format on stdout (default: json)')
    parser.add_argument('--hash-all', action='store_true', help='Reference baseline: hash every inventoried regular file')
    parser.add_argument('--comparison-scope', metavar='SOURCE_PROFILE',
                        help='Record a caller-declared source-profile namespace and all item identities for comparison; forces fresh SHA-256 for every file. Never reuse across unrelated databases.')
    args = parser.parse_args(argv)
    try:
        report = audit(args.snapshot, args.storage,
                       allow_linked_roots=tuple(args.allow_linked_root),
                       linked_base_directory=args.linked_base_directory, hash_all=args.hash_all,
                       linked_root_mappings=args.map_linked_root,
                       comparison_scope=args.comparison_scope)
        code = 0 if report['auditComplete'] else 1
    except InputError as exc:
        report = {'reportVersion': 1, 'tool': 'zotero-attach-audit', 'auditComplete': False,
                  'fatal': True, 'auditErrors': [{'code': exc.code, 'detail': exc.detail}]}
        code = 2
    return emit(report, args.format, code)


def emit(report: dict, output_format: str, code: int) -> int:
    try:
        print(markdown(report) if output_format == 'markdown' else json.dumps(report, ensure_ascii=True, indent=2))
    except BrokenPipeError:
        return 2
    return code


if __name__ == '__main__':
    sys.exit(main())
