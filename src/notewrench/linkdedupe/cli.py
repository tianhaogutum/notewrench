# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 tianhaogutum
"""``notewrench links`` command line (port of ``bin/linkdedupe.js``).

Option parsing reproduces Node's ``util.parseArgs`` (strict, positionals allowed)
rather than :mod:`argparse`, so error messages and the exit status (1 for every
failure) stay identical to the original tool.
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
import sys
import uuid
from typing import Any

from . import __version__
from .jsjson import stringify, well_formed
from .report import build_report
from .rules import parse_rule_file
from .strictjson import parse_json
from .wallabag_export import build_wallabag_export

PROG = 'notewrench links'

HELP = f"""{PROG} — offline duplicate candidates for Karakeep / Wallabag exports

usage: {PROG} [options] export.json [more-exports.json ...]

  --out DIR                New output directory (required unless --dry-run)
  --format FORMAT          auto (default), karakeep, or wallabag-v2
                           Repeat once per input to mix explicit formats
  --skip-invalid           Quarantine invalid records with raw data and counts
  --dry-run                Validate and print summary; write nothing
  --wallabag-export        Project one Wallabag v2 dump to a new native export
                           Includes decisions template and mapping/conflict audit
  --wallabag-mapping       Include complete per-source mapping in stdout
                           Requires --wallabag-export; also works in dry-run
  --wallabag-conflicts     Include scalar conflict audit in stdout
                           Requires --wallabag-export; also works in dry-run
  --wallabag-unrepresented Include unrepresented native fields in stdout
                           Requires --wallabag-export; also works in dry-run
  --wallabag-decisions     Include fresh hash-bound false decision template in stdout
                           Requires --wallabag-export; also works in dry-run
  --decision-outcomes      Include per-decision audit outcomes in stdout
                           Requires --wallabag-export; also works in dry-run
  --require-all-applied    Fail before publication if any decision is not applied
                           Requires --wallabag-export; false rows also fail
                           Omitted/empty decisions pass; also works in dry-run
  --require-all-processed  Fail before publication if any source row is unprocessed
                           Requires --wallabag-export; includes quarantined rows
                           Also works in dry-run; empty dumps pass
  --require-no-unrepresented Fail before publication if native field audit is nonempty
                           Requires --wallabag-export; also works in dry-run
                           Not a lossless-import guarantee; scalar conflicts pass
  --require-no-conflicts   Fail before publication if native conflict audit is nonempty
                           Requires --wallabag-export; also works in dry-run
                           Not a lossless-import guarantee; empty conflicts pass
  --decisions FILE         Hash-bound approvals from wallabag-decisions.json
                           Requires --wallabag-export; no implicit merges
  --rules FILE             Strict exact-host JSON parameter rules
                           Dry-run includes per-rule before/after comparisons
  --compare-clusters       Compare baseline vs rule-enabled cluster membership
                           Requires --rules; stdout and clusters.json include
                           exact source refs entering/leaving and peer changes
  --compare-rule-clusters  Include cumulative membership steps for every rule
                           Implies --compare-clusters; requires --rules
  --compact-rule-clusters  Count endpoint and cumulative rule cluster effects
                           Implies rule comparison; requires --rules
                           Omits inventories/peer lists from comparison only
  --keep-tracking          Keep all tracking parameters
  --drop-param NAME        Extra exact utm_, pk_ or mtm_ name; repeatable
  --strip-fragment         Explicitly ignore fragments
  --sort-query             Sort keys, preserving duplicate-key value order
  --https                  Explicitly treat http as https
  --strip-trailing-slash   Explicitly remove trailing path slashes
  --help, -h               Show help
  --version                Show version

Defaults preserve fragment, query order/encoding, http vs https, and trailing
slash. Known signed/authenticated URLs remain byte-for-byte unchanged even
with opt-in rules. No API calls, script execution, deletion, or app imports.

Writes records.json, clusters.json, merge-plan.json, provenance.json.
--wallabag-export also writes wallabag-export.json, wallabag-decisions.json,
wallabag-merge-report.json. Unsupported fields remain in audit/provenance;
annotation transfers require identical content. No lossless import claim.
Existing output paths are never overwritten. Use -- before dash-prefixed paths.
"""

# name -> (type, multiple, short)
OPTIONS: dict[str, tuple[str, bool, str | None]] = {
    'out': ('string', False, None), 'rules': ('string', False, None), 'format': ('string', True, None),
    'wallabag-export': ('boolean', False, None), 'decisions': ('string', False, None),
    'decision-outcomes': ('boolean', False, None),
    'wallabag-mapping': ('boolean', False, None),
    'wallabag-conflicts': ('boolean', False, None),
    'wallabag-unrepresented': ('boolean', False, None),
    'wallabag-decisions': ('boolean', False, None),
    'require-all-applied': ('boolean', False, None),
    'require-all-processed': ('boolean', False, None),
    'require-no-unrepresented': ('boolean', False, None),
    'require-no-conflicts': ('boolean', False, None),
    'compare-clusters': ('boolean', False, None), 'compare-rule-clusters': ('boolean', False, None),
    'compact-rule-clusters': ('boolean', False, None),
    'drop-param': ('string', True, None),
    'skip-invalid': ('boolean', False, None), 'dry-run': ('boolean', False, None),
    'keep-tracking': ('boolean', False, None), 'strip-fragment': ('boolean', False, None),
    'sort-query': ('boolean', False, None), 'https': ('boolean', False, None),
    'strip-trailing-slash': ('boolean', False, None),
    'help': ('boolean', False, 'h'), 'version': ('boolean', False, None),
}


class CLIError(Exception):
    """A failure reported as ``notewrench links: <message>`` with exit status 1."""


# --- util.parseArgs ----------------------------------------------------------------

def _long_for_short(short: str) -> str:
    for name, (_, _, option_short) in OPTIONS.items():
        if option_short == short:
            return name
    return short


def _type(name: str) -> str | None:
    option = OPTIONS.get(name)
    return option[0] if option else None


def parse_args(args: list[str]) -> tuple[dict[str, Any], list[str]]:
    """Node's ``util.parseArgs({options, allowPositionals: true, strict: true})``."""
    tokens: list[tuple[str, str, str | None, bool]] = []  # (name, rawName, value, inlineValue)
    positionals: list[str] = []
    remaining = list(args)
    while remaining:
        arg = remaining.pop(0)
        following = remaining[0] if remaining else None
        if arg == '--':
            positionals.extend(remaining)
            break
        if len(arg) == 2 and arg[0] == '-' and arg[1] != '-':
            name = _long_for_short(arg[1])
            value = None
            if _type(name) == 'string' and following is not None:
                value = remaining.pop(0)
            tokens.append((name, arg, value, False))
            continue
        if len(arg) > 2 and arg[0] == '-' and arg[1] != '-':
            if _type(_long_for_short(arg[1])) != 'string':
                remaining[:0] = ['-' + char for char in arg[1:]]
                continue
            tokens.append((_long_for_short(arg[1]), arg[:2], arg[2:], True))
            continue
        if len(arg) > 2 and arg.startswith('--'):
            if '=' not in arg[3:]:
                name = arg[2:]
                value = None
                if _type(name) == 'string' and following is not None:
                    value = remaining.pop(0)
                tokens.append((name, arg, value, False))
            else:
                index = arg.index('=', 3)
                tokens.append((arg[2:index], arg[:index], arg[index + 1:], True))
            continue
        positionals.append(arg)
    values: dict[str, Any] = {}
    for name, raw_name, value, inline in tokens:
        if not inline and value is not None and len(value) > 1 and value[0] == '-':
            example = f"'{raw_name}=-XYZ'" if raw_name.startswith('--') else f"'--{name}=-XYZ' or '{raw_name}-XYZ'"
            raise CLIError(f"Option '{raw_name}' argument is ambiguous.\n"
                           f"Did you forget to specify the option argument for '{raw_name}'?\n"
                           f"To specify an option argument starting with a dash use {example}.")
        if name not in OPTIONS:
            raise CLIError(f"Unknown option '{raw_name}'. To specify a positional argument starting with a '-', "
                           f"place it at the end of the command after '--', as in '-- {stringify(raw_name)}")
        kind, multiple, short = OPTIONS[name]
        short_and_long = f"{f'-{short}, ' if short else ''}--{name}"
        if kind == 'string' and value is None:
            raise CLIError(f"Option '{short_and_long} <value>' argument missing")
        if kind == 'boolean' and value is not None:
            raise CLIError(f"Option '{short_and_long}' does not take an argument")
        stored = True if kind == 'boolean' else value
        if multiple:
            values.setdefault(name, []).append(stored)
        else:
            values[name] = stored
    return values, positionals


# --- Node-style filesystem errors ----------------------------------------------------

_DESCRIPTIONS = {
    errno.ENOENT: 'no such file or directory', errno.EACCES: 'permission denied', errno.EEXIST: 'file already exists',
    errno.ENOTDIR: 'not a directory', errno.EISDIR: 'illegal operation on a directory', errno.ELOOP:
    'too many symbolic links encountered', errno.ENAMETOOLONG: 'name too long', errno.EPERM: 'operation not permitted',
    errno.ENOSPC: 'no space left on device', errno.EROFS: 'read-only file system', errno.EIO: 'i/o error',
    errno.EXDEV: 'cross-device link not permitted', errno.ENOTEMPTY: 'directory not empty',
}


def _fs_error(error: OSError, syscall: str, path: str | None = None) -> CLIError:
    code = errno.errorcode.get(error.errno or 0, 'UNKNOWN')
    description = _DESCRIPTIONS.get(error.errno or 0) or (error.strerror or 'unknown error').lower()
    suffix = f", {syscall} '{path}'" if path is not None else f', {syscall}'
    return CLIError(f'{code}: {description}{suffix}')


def _resolve(path: str) -> str:
    resolved = os.path.abspath(path)
    return '/' + resolved.lstrip('/') if resolved.startswith('//') else resolved


def _read_json_text(name: str) -> str:
    try:
        fd = os.open(name, os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0))
    except OSError as error:
        raise _fs_error(error, 'open', name) from None
    try:
        chunks = []
        while True:
            try:
                chunk = os.read(fd, 1 << 20)
            except OSError as error:
                raise _fs_error(error, 'read') from None
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(fd)
    try:
        return b''.join(chunks).decode('utf-8')
    except UnicodeDecodeError:
        raise CLIError(f'{name}: input must be valid UTF-8 JSON') from None


def _publish(out: str, report: dict[str, Any]) -> None:
    target = _resolve(out)
    files = [('records.json', report['records']), ('clusters.json', report['clusters']),
             ('merge-plan.json', report['mergePlan']), ('provenance.json', report['provenance'])]
    if 'wallabagExport' in report:
        files += [('wallabag-export.json', report['wallabagExport']),
                  ('wallabag-decisions.json', report['wallabagDecisions']),
                  ('wallabag-merge-report.json', report['wallabagMergeReport'])]
    # Reserve a fresh directory before writing; never follow an existing symlink.
    # No recursive creation: parents must already exist, including for dry runs.
    try:
        os.mkdir(target, 0o700)
    except OSError as error:
        raise _fs_error(error, 'mkdir', target) from None
    staging = os.path.join(target, f'.staging-{uuid.uuid4()}')
    try:
        try:
            os.mkdir(staging, 0o700)
        except OSError as error:
            raise _fs_error(error, 'mkdir', staging) from None
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
        for filename, data in files:
            path = os.path.join(staging, filename)
            try:
                fd = os.open(path, flags, 0o600)
            except OSError as error:
                raise _fs_error(error, 'open', path) from None
            try:
                view = memoryview((stringify(data, 2) + '\n').encode('utf-8'))
                while view:
                    view = view[os.write(fd, view):]
            except OSError as error:
                raise _fs_error(error, 'write') from None
            finally:
                os.close(fd)
        for filename, _ in files:
            source, destination = os.path.join(staging, filename), os.path.join(target, filename)
            try:
                os.rename(source, destination)
            except OSError as error:
                raise _fs_error(error, 'rename', source) from None
        shutil.rmtree(staging)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)
        raise


# --- main -------------------------------------------------------------------------------

def _write(stream: Any, text: str) -> None:
    data = well_formed(text).encode('utf-8')
    buffer = getattr(stream, 'buffer', None)
    if buffer is None:
        stream.write(data.decode('utf-8'))
    else:
        stream.flush()
        buffer.write(data)
    stream.flush()


def _requires(values: dict[str, Any], flag: str, *, needs: str = 'wallabag-export') -> None:
    if values.get(flag) is not None and not values.get(needs):
        raise CLIError(f'--{flag} requires --{needs}')


def _run(args: list[str]) -> None:
    values, positionals = parse_args(args)
    if values.get('help'):
        _write(sys.stdout, HELP)
        return
    if values.get('version'):
        _write(sys.stdout, f'{PROG} {__version__}\n')
        return
    if not positionals:
        raise CLIError('At least one export is required; see --help')
    if not values.get('out') and not values.get('dry-run'):
        raise CLIError('--out is required unless --dry-run')
    for flag in ('decision-outcomes', 'require-all-applied', 'require-all-processed', 'decisions'):
        _requires(values, flag)
    for flag in ('compact-rule-clusters', 'compare-rule-clusters', 'compare-clusters'):
        if values.get(flag) and values.get('rules') is None:
            raise CLIError(f'--{flag} requires --rules')
    for flag in ('wallabag-mapping', 'wallabag-conflicts', 'wallabag-unrepresented', 'require-no-unrepresented',
                 'require-no-conflicts', 'wallabag-decisions'):
        _requires(values, flag)
    formats = values.get('format')
    if formats and len(formats) != 1 and len(formats) != len(positionals):
        raise CLIError('Supply one --format for all inputs, or one per input in positional order')
    out = values.get('out')
    if out:
        target = _resolve(out)
        try:
            os.lstat(target)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise _fs_error(error, 'lstat', target) from None
        else:
            raise CLIError('Output path already exists; choose a new directory')
        parent = os.path.dirname(target)
        try:
            parent_stat = os.lstat(parent)
        except OSError as error:
            raise _fs_error(error, 'lstat', parent) from None
        if not stat.S_ISDIR(parent_stat.st_mode):
            raise CLIError('Output parent must be an existing directory')
        if not os.path.basename(target):
            raise CLIError('Invalid output path')
    rules_path = values.get('rules')
    rules = parse_rule_file(_read_json_text(rules_path), rules_path) if rules_path is not None else None
    inputs = []
    for index, name in enumerate(positionals):
        text = _read_json_text(name)
        inputs.append({'name': name, 'text': text,
                       'format': formats[0] if formats and len(formats) == 1 else formats[index] if formats else None})
    options: dict[str, Any] = {}
    if values.get('decisions') is not None:
        options['decisions'] = parse_json(_read_json_text(values['decisions']))
    options.update(
        skip_invalid=values.get('skip-invalid', False),
        compare_clusters=values.get('compare-clusters', False),
        compare_rule_clusters=values.get('compare-rule-clusters', False),
        compact_rule_clusters=values.get('compact-rule-clusters', False),
        normalization={
            'rules': rules,
            'keepTracking': values.get('keep-tracking', False),
            'dropParams': values.get('drop-param', []),
            'stripFragment': values.get('strip-fragment', False),
            'sortQuery': values.get('sort-query', False),
            'https': values.get('https', False),
            'stripTrailingSlash': values.get('strip-trailing-slash', False),
        },
    )
    report = build_wallabag_export(inputs, **options) if values.get('wallabag-export') else build_report(inputs, **options)
    audit = report.get('wallabagMergeReport')
    if values.get('require-all-applied'):
        failed = next((outcome for outcome in audit['decisionOutcomes'] if outcome['status'] != 'applied'), None)
        if failed:
            raise CLIError(f"--require-all-applied: decision {failed['decisionIndex']} {failed['status']}/{failed['reason']}")
    if values.get('require-all-processed'):
        failed = next((row for row in audit['mapping'] if row['status'] == 'unprocessed'), None)
        if failed:
            raise CLIError(f"--require-all-processed: {failed['sourceRef']} unprocessed: {failed['reason']}")
    if values.get('require-no-unrepresented') and audit['unrepresentedFields']:
        failed = audit['unrepresentedFields'][0]
        raise CLIError(f"--require-no-unrepresented: {failed['sourceRef']} {failed['pointer']}: {failed['reason']}")
    if values.get('require-no-conflicts') and audit['conflicts']:
        failed = audit['conflicts'][0]
        pointer = '/' + failed['field'].replace('~', '~0').replace('/', '~1')
        raise CLIError(f"--require-no-conflicts: {failed['keptSourceRef']} {pointer}: {failed['resolution']}")
    if not values.get('dry-run'):
        _publish(out, report)
    comparisons: dict[str, Any] = {}
    if values.get('dry-run') and rules:
        rule_comparisons = [{'sourceRef': member['sourceRef'], 'originalUrl': member['originalUrl'], **comparison}
                            for record in report['records']['records'] for member in record['members']
                            for comparison in member['ruleComparisons']]
        rule_summary = []
        for index, rule in enumerate(rules['rules']):
            entries = [entry for entry in rule_comparisons if entry['ruleId'] == rule['id']]
            rule_summary.append({
                'ruleId': rule['id'], 'host': rule['host'], 'source': {**rules['source'], 'pointer': f'/rules/{index}'},
                'hostMatchedUrls': len(entries),
                'parameterMatchedUrls': sum(1 for entry in entries if entry['status'] == 'applied'),
                'changedUrls': sum(1 for entry in entries if entry['beforeUrl'] != entry['afterUrl']),
                'protectedUrls': sum(1 for entry in entries if entry['status'] == 'protected'),
            })
        comparisons = {'ruleSummary': rule_summary, 'ruleComparisons': rule_comparisons}
    summary: dict[str, Any] = {'dryRun': bool(values.get('dry-run')), **report['records']['summary'], **comparisons}
    if 'clusterComparison' in report['clusters']:
        summary['clusterComparison'] = report['clusters']['clusterComparison']
    if audit:
        summary['wallabag'] = audit['summary']
    for flag, key, value in (
            ('decision-outcomes', 'wallabagDecisionOutcomes', lambda: audit['decisionOutcomes']),
            ('wallabag-mapping', 'wallabagMapping', lambda: audit['mapping']),
            ('wallabag-conflicts', 'wallabagConflicts', lambda: audit['conflicts']),
            ('wallabag-unrepresented', 'wallabagUnrepresentedFields', lambda: audit['unrepresentedFields']),
            ('wallabag-decisions', 'wallabagDecisions', lambda: report['wallabagDecisions'])):
        if values.get(flag):
            summary[key] = value()
    _write(sys.stdout, stringify(summary) + '\n')
    skipped = report['records']['summary']['skippedRecords']
    if skipped:
        _write(sys.stderr, f'Quarantined {skipped} invalid record(s); review clusters.json and provenance.json.\n')


def main(argv: list[str] | None = None) -> int:
    """Run ``notewrench links``; returns the exit status (0 success, 1 failure)."""
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        _run(args)
    except RecursionError:
        _write(sys.stderr, f'{PROG}: Maximum call stack size exceeded\n')
        return 1
    except Exception as error:  # every failure is reported like the original: one line, exit 1
        _write(sys.stderr, f'{PROG}: {error}\n')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
