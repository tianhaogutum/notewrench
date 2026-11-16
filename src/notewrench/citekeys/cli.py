# SPDX-License-Identifier: AGPL-3.0-only
"""``notewrench citekeys``: offline CSL JSON -> Markdown citation key migration.

The option grammar is the original tool's (not argparse): every usage problem is
reported as stderr JSON ``{"error": {"code": "USAGE", ...}}`` with exit status 2,
exactly like every other refusal, so scripts can rely on one error format.
"""
from __future__ import annotations

import os
import re
import stat as stat_module
import sys

from ._js import stringify
from .diff import unified_diff
from .files import check_output, join, no_symlinks, read_csl, read_utf8, resolve, safe_relative, system_error, write_outputs
from .identity import RekeyError, build_mapping
from .markdown import rekey_markdown

PROG = 'notewrench citekeys'
# Report identity fields are part of the JSON contract and are kept verbatim.
TOOL = 'citekey-rekey'
VERSION = '1.0.0'

HELP = f"""{PROG} — offline CSL JSON → Markdown citation migration

Usage:
  {PROG} --old old.json --new new.json [options] file.md ...

Options:
  --old FILE       Old CSL JSON array; id is the bibliography key
  --new FILE       New CSL JSON array; match DOI first, then exact URL
  --root DIR       Markdown input root (default: current directory)
  --out-dir DIR    Write copies into a NEW directory; never overwrite sources
  --dry-run        Explicit read-only mode (default; incompatible with --out-dir)
  --help, -h       Show this help
  --version        Show version
  --               End options (for filenames beginning with '-')

Output: JSON report on stdout with mapping, conflicts, unmatched, document
edits, unresolved keys, unsupported syntax and unified diffs. Errors: stderr JSON.
Exit: 0 clean; 1 unresolved identity/key or unsupported citation left unchanged;
2 invalid input, identity conflict, unsupported document structure or I/O error.

Supported citations: [@key], [-@key], [see @key, p. 4; -@other, chap. 2].
Prose affixes retain plain text and punctuation escapes; one marker per item.
Keys: ASCII letters/digits/underscore separated by single '.', ':', or '-'.
Explicit nested list/quote prose and quoted bare-key bracket groups or comma
lists in flat frontmatter nocite are supported. Code, math, HTML and other scalar metadata are protected.
Ambiguous containers or metadata fail closed. Ambiguous affixes, braced keys,
nested citations, and author-in-text citations are reported unchanged.
Full contract and output safety limits:
https://github.com/tianhaogutum/notewrench/blob/main/docs/citekeys.md
"""

_VALUE_FLAGS = {'--old': 'old', '--new': 'new', '--root': 'root', '--out-dir': 'out_dir'}
_MARKDOWN_NAME = re.compile(r'\.(md|markdown)\Z', re.IGNORECASE | re.ASCII)


def _write(stream: object, text: str) -> None:
    data = text.encode('utf-8', 'surrogateescape')
    buffer = getattr(stream, 'buffer', None)
    if buffer is not None:
        stream.flush()  # type: ignore[attr-defined]
        buffer.write(data)
        buffer.flush()
    else:
        stream.write(text)  # type: ignore[attr-defined]
        stream.flush()  # type: ignore[attr-defined]


def _arguments(argv: list[str]) -> dict[str, object]:
    options: dict[str, object] = {'files': [], 'root': os.getcwd()}
    files: list[str] = options['files']  # type: ignore[assignment]
    positional = False
    seen: set[str] = set()
    i = 0
    while i < len(argv):
        arg = argv[i]
        if positional:
            files.append(arg)
        elif arg == '--':
            positional = True
        elif arg in ('--help', '-h'):
            return {'help': True}
        elif arg == '--version':
            return {'version': True}
        elif arg in _VALUE_FLAGS or arg == '--dry-run':
            if arg in seen:
                raise RekeyError('USAGE', f'Duplicate option {arg}')
            seen.add(arg)
            if arg == '--dry-run':
                options['dry_run'] = True
            else:
                if i + 1 >= len(argv) or not argv[i + 1] or argv[i + 1].startswith('--'):
                    raise RekeyError('USAGE', f'Missing value for {arg}')
                i += 1
                options[_VALUE_FLAGS[arg]] = argv[i]
        elif arg.startswith('-'):
            raise RekeyError('USAGE', f'Unknown option {arg}')
        else:
            files.append(arg)
        i += 1
    if not options.get('old') or not options.get('new') or not files:
        raise RekeyError('USAGE', 'Required: --old FILE --new FILE file.md ... (see --help)')
    if options.get('dry_run') and options.get('out_dir'):
        raise RekeyError('USAGE', '--dry-run and --out-dir are mutually exclusive')
    return options


def _run(argv: list[str]) -> int:
    options = _arguments(argv)
    if options.get('help'):
        _write(sys.stdout, HELP)
        return 0
    if options.get('version'):
        _write(sys.stdout, VERSION + '\n')
        return 0
    root = resolve(str(options['root']))
    no_symlinks(root)
    try:
        info = os.stat(root)
    except OSError as error:
        raise system_error(error, 'stat', root) from None
    if not stat_module.S_ISDIR(info.st_mode):
        raise RekeyError('NOT_DIRECTORY', '--root must be a directory')
    output = resolve(str(options['out_dir'])) if options.get('out_dir') else None
    if output:
        check_output(output)
    old = read_csl(resolve(str(options['old'])))
    updated = read_csl(resolve(str(options['new'])))
    identity = build_mapping(old, updated)
    report: dict[str, object] = {'tool': TOOL, 'version': VERSION, 'mode': 'out-dir' if output else 'dry-run',
                                 **identity, 'documents': [], 'written': []}
    if identity['conflicts']:
        _write(sys.stdout, stringify(report, 2) + '\n')
        return 2
    documents = []
    reported: list[dict[str, object]] = report['documents']  # type: ignore[assignment]
    paths: set[str] = set()
    for file in options['files']:  # type: ignore[attr-defined]
        location = safe_relative(root, file)
        if location['relative'] in paths:
            raise RekeyError('DUPLICATE_INPUT', f'Duplicate input {file}')
        paths.add(location['relative'])
        if not _MARKDOWN_NAME.search(file):
            raise RekeyError('UNSUPPORTED_FORMAT', f'Only .md/.markdown input is supported: {file}')
        before = read_utf8(location['absolute'])
        result = rekey_markdown(before, identity['mapping'])
        documents.append({'path': location['relative'], 'text': result['text']})
        reported.append({'path': location['relative'], 'changed': before != result['text'], 'edits': result['edits'],
                         'unresolved': result['unresolved'], 'unsupported': result['unsupported'],
                         'diff': unified_diff(before, result['text'], location['relative'])})
    if output:
        write_outputs(output, documents)
        report['written'] = [join(output, document['path']) for document in documents]
    _write(sys.stdout, stringify(report, 2) + '\n')
    if identity['unmatched'] or any(doc['unresolved'] or doc['unsupported'] for doc in reported):
        return 1
    return 0


def _error(code: str, message: str, details: object) -> int:
    _write(sys.stderr, stringify({'error': {'code': code, 'message': message, 'details': details}}, 2) + '\n')
    return 2


def main(argv: list[str] | None = None) -> int:
    """Run ``notewrench citekeys`` with ``argv`` (default ``sys.argv[1:]``); returns the exit status."""
    args = sys.argv[1:] if argv is None else list(argv)
    try:
        return _run(args)
    except RekeyError as error:
        return _error(error.code, error.message, error.details)
    except BrokenPipeError:
        return 2
    except Exception as error:  # noqa: BLE001 - every failure becomes the stderr JSON contract
        return _error('ERROR', str(error), [])


if __name__ == '__main__':
    raise SystemExit(main())
