# SPDX-License-Identifier: AGPL-3.0-only
"""Command-line entry point."""

import argparse
import json
import sys

from .. import __version__
from . import ConversionError, Limits, convert

PROG = 'notewrench joplin-logseq'


def positive(value):
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError('must be a positive integer') from exc
    if result <= 0:
        raise argparse.ArgumentTypeError('must be a positive integer')
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=PROG, description='Convert unencrypted Joplin JEX tar to a fresh Logseq Markdown file graph (offline).')
    parser.add_argument('--version', action='version', version=f'{PROG} {__version__}')
    parser.add_argument('archive', help='uncompressed .jex tar archive')
    parser.add_argument('output', help='new output directory; its parent must already exist')
    parser.add_argument('--dry-run', action='store_true', help='validate and print the complete report as JSON; create no files')
    parser.add_argument('--fail-on-broken-links', action='store_true',
                        help='require --dry-run; exit 1 if the complete report contains broken_link issues')
    parser.add_argument('--include-organization-pages', action='store_true',
                        help='write both notebook and tag pages (shortcut for both independent flags)')
    parser.add_argument('--include-notebook-pages', action='store_true',
                        help='write all notebook pages, including empty notebooks, with hierarchy and membership links')
    parser.add_argument('--include-tag-pages', action='store_true',
                        help='write all tag pages, including unused tags, with membership links')
    defaults = Limits()
    for field, default in vars(defaults).items():
        parser.add_argument('--' + field.replace('_', '-'), type=positive, default=default, help=f'input safety limit (default: {default})')
    args = parser.parse_args(argv)
    if args.fail_on_broken_links and not args.dry_run:
        parser.error('--fail-on-broken-links requires --dry-run')
    try:
        limits = Limits(**{field: getattr(args, field) for field in vars(defaults)})
        report = convert(args.archive, args.output, limits=limits, dry_run=args.dry_run,
                         include_organization_pages=args.include_organization_pages,
                         include_notebook_pages=args.include_notebook_pages,
                         include_tag_pages=args.include_tag_pages)
        if args.dry_run:
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            if args.fail_on_broken_links and any(issue['code'] == 'broken_link' for issue in report['issues']):
                return 1
        else:
            print(json.dumps({'output': args.output, 'counts': report['counts'], 'issues': len(report['issues'])}, ensure_ascii=False))
        return 0
    except (ConversionError, OSError, ValueError) as exc:
        print(f'{PROG}: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
