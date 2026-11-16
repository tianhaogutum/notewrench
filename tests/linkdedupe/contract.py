# SPDX-License-Identifier: AGPL-3.0-only
"""Shared CLI contract harness (port of linkdedupe scripts/decision-outcomes-contract.mjs).

All fixtures are synthetic and offline. A :class:`Context` owns one temporary
working directory containing ``source.json`` (a copy of the subgroup fixture)
and the hash-bound decision template generated from it.
"""

from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from typing import Any, Callable

from .helpers import PREFIX, fixture, invoke as invoke_cli, js, listdir

FILES = ['clusters.json', 'merge-plan.json', 'provenance.json', 'records.json',
         'wallabag-decisions.json', 'wallabag-export.json', 'wallabag-merge-report.json']
OUTCOMES = 'wallabagDecisionOutcomes'
ANNOTATION_REASON = 'annotation requires string text, string quote and array ranges; full record is in provenance'


def decision(refs: list[int], keeper: int | None = None, approved: Any = True) -> dict[str, Any]:
    return {'sourceRefs': [f's1:r{n}' for n in refs], 'keepSourceRef': f's1:r{refs[0] if keeper is None else keeper}',
            'approved': approved}


def outcome(index: int, refs: list[int], keeper: int, status: str, reason: str, pointer: str | None = None) -> dict[str, Any]:
    return {'decisionIndex': index, 'sourceRefs': [f's1:r{n}' for n in refs], 'keepSourceRef': f's1:r{keeper}',
            'status': status, 'reason': reason, 'outputPointer': pointer}


MIXED = [decision([5, 1], 1), decision([4, 2], 2)]
MIXED_OUTCOMES = [
    outcome(0, [1, 5], 1, 'blocked', 'annotation-content-mismatch'),
    outcome(1, [2, 4], 2, 'applied', 'explicitly-approved', '/1'),
]


def err(message: str) -> str:
    """The exact stderr line for a failure (``linkdedupe: `` became ``notewrench links: ``)."""
    return f'{PREFIX}{message}\n'


def check_error(stderr: str, expected: str | re.Pattern[str]) -> None:
    if isinstance(expected, str):
        assert stderr == expected
    else:
        assert expected.search(stderr), (expected.pattern, stderr)


def snapshot(cwd: Path) -> dict[str, Any]:
    """Every file's bytes and every directory under ``cwd``."""
    result: dict[str, Any] = {}
    for root, dirs, files in os.walk(cwd):
        for name in dirs:
            result[os.path.relpath(os.path.join(root, name), cwd)] = 'directory'
        for name in files:
            path = os.path.join(root, name)
            with open(path, 'rb') as handle:
                result[os.path.relpath(path, cwd)] = handle.read()
    return result


def write(path: Path, data: Any) -> None:
    if isinstance(data, bytes):
        path.write_bytes(data)
    elif isinstance(data, str):
        path.write_text(data, encoding='utf-8')
    else:
        path.write_text(js(data), encoding='utf-8')


class Context:
    def __init__(self, cwd: Path, source_fixture: str = 'wallabag-subgroups-v2.json') -> None:
        self.cwd = cwd
        with open(fixture(source_fixture), encoding='utf-8', newline='') as handle:
            self.original_text = handle.read()
        write(cwd / 'source.json', self.original_text)
        self.invoke(['--out', 'preview'])
        self.template = self.json('preview/wallabag-decisions.json')
        self.audit: dict[str, Any] | None = None

    # -- primitives -----------------------------------------------------------------
    def json(self, path: str) -> Any:
        return json.loads((self.cwd / path).read_text(encoding='utf-8'))

    def run(self, args: list[str], expected: int = 0):
        result = invoke_cli(args, self.cwd)
        assert result.status == expected, result.stderr
        return result

    def invoke(self, args: list[str], expected: int = 0):
        return self.run(['--wallabag-export', *args, 'source.json'], expected)

    def selections(self, name: str, rows: list[dict[str, Any]]) -> list[str]:
        write(self.cwd / f'{name}.json', {**self.template, 'decisions': rows})
        return ['--decisions', f'{name}.json']

    def listdir(self) -> list[str]:
        return listdir(self.cwd)

    # -- derived contexts ---------------------------------------------------------------
    def with_invoke(self, wrap: Callable[[Any, list[str], int], Any]) -> Context:
        """A copy whose ``invoke`` is ``wrap(original_invoke, args, expected)``."""
        derived = copy.copy(self)
        inner = self.invoke
        derived.invoke = lambda args, expected=0: wrap(inner, args, expected)  # type: ignore[method-assign]
        return derived

    def prefixed(self, *flags: str) -> Context:
        return self.with_invoke(lambda inner, args, expected: inner([*flags, *args], expected))

    def source(self, rows: Any, name: str, text: str | None = None) -> Context:
        """Replace ``source.json`` with ``rows`` and derive a ``--format wallabag-v2`` context."""
        derived = copy.copy(self)
        derived.original_text = js(rows) if text is None else text
        write(self.cwd / 'source.json', derived.original_text)
        self.invoke(['--format', 'wallabag-v2', '--skip-invalid', '--out', name])
        derived.template = self.json(f'{name}/wallabag-decisions.json')
        derived.audit = self.json(f'{name}/wallabag-merge-report.json')
        base = self.invoke
        derived.invoke = lambda args, expected=0: base(['--format', 'wallabag-v2', *args], expected)  # type: ignore[method-assign]
        return derived

    # -- decision-outcomes contract -------------------------------------------------------
    def execute(self, name: str, rows: list[dict[str, Any]] | None, expected: list[dict[str, Any]]) -> dict[str, Any]:
        args = [] if rows is None else self.selections(name, rows)
        flagged = self.invoke(['--decision-outcomes', '--out', name, *args])
        summary = json.loads(flagged.stdout)
        assert summary[OUTCOMES] == expected
        audit = self.json(f'{name}/wallabag-merge-report.json')
        assert summary[OUTCOMES] == audit['decisionOutcomes']
        assert audit['basisSha256'] == self.template['basisSha256']
        assert self.json(f'{name}/wallabag-decisions.json') == self.template
        assert audit['appliedDecisions'] == (None if rows is None else {**self.template, 'decisions': rows})
        assert audit['roundTrip']['verified'] is True
        # Flag must add exactly one stdout key and change no file bytes.
        plain = self.invoke(['--out', f'{name}-plain', *args])
        assert plain.stdout == js({k: v for k, v in summary.items() if k != OUTCOMES}) + '\n'
        files = listdir(self.cwd / name)
        assert files == FILES
        assert listdir(self.cwd / f'{name}-plain') == files
        for file in files:
            assert (self.cwd / name / file).read_bytes() == (self.cwd / f'{name}-plain' / file).read_bytes(), file
        before = self.listdir()
        for out in [[], ['--out', f'{name}-dry']]:
            dry = json.loads(self.invoke(['--decision-outcomes', '--dry-run', *out, *args]).stdout)
            assert dry == {**summary, 'dryRun': True}
        assert self.listdir() == before, 'dry-run writes nothing'
        assert (self.cwd / 'source.json').read_text(encoding='utf-8') == self.original_text
        return {'summary': summary, 'audit': audit, 'output': self.json(f'{name}/wallabag-export.json')}


def malformed_decisions_scenario(c: Context) -> None:
    """'malformed, stale and late cross-candidate decisions fail before outcomes or files'."""
    valid = {**c.template, 'decisions': MIXED}
    documents = [
        ('schema', {**valid, 'unknown': True}, re.compile('schema')),
        ('type', {**valid, 'decisions': [decision([1, 2], 1, 'true')]}, re.compile('Invalid Wallabag decision')),
        ('missing-approval', {**valid, 'decisions': [{'sourceRefs': ['s1:r1', 's1:r2'], 'keepSourceRef': 's1:r1'}]},
         re.compile('Invalid Wallabag decision')),
        ('stale', {**valid, 'basisSha256': '0' * 64}, re.compile('basis mismatch')),
        ('cross-true', {**valid, 'decisions': [*MIXED, decision([3, 6], 3)]}, re.compile('one current candidate')),
        ('cross-false', {**valid, 'decisions': [*MIXED, decision([3, 6], 3, False)]}, re.compile('one current candidate')),
        ('overlap', {**valid, 'decisions': [*MIXED, decision([3, 2], 3, False)]}, re.compile('overlapping')),
        ('foreign', {**valid, 'decisions': [decision([1, 99], 1)]}, re.compile('one current candidate')),
        ('keeper', {**valid, 'decisions': [decision([1, 2], 3)]}, re.compile('keeper')),
        ('json', '{broken', re.compile('JSON')),
        ('duplicate-key', '{"decisions":[],"decisions":[]}', re.compile('Duplicate')),
        ('utf8', b'\xff', re.compile('valid UTF-8')),
    ]
    for name, document, error in documents:
        path = f'bad-{name}.json'
        write(c.cwd / path, document)
        before = c.listdir()
        for extra in [[], ['--dry-run'], ['--skip-invalid'], ['--dry-run', '--skip-invalid']]:
            failed = c.invoke(['--decision-outcomes', '--out', 'failed', '--decisions', path, *extra], 1)
            assert failed.stdout == ''
            check_error(failed.stderr, error)
            assert c.listdir() == before
    args = c.selections('valid', MIXED)
    # Stale normalization and changed source bytes fail even though refs still exist.
    for extra in [['--sort-query'], ['--dry-run', '--sort-query']]:
        failed = c.invoke(['--decision-outcomes', '--out', 'failed', *args, *extra], 1)
        assert failed.stdout == ''
        check_error(failed.stderr, re.compile('basis mismatch'))
    write(c.cwd / 'source.json', c.original_text + '\n')
    failed = c.invoke(['--decision-outcomes', '--out', 'failed', *args], 1)
    assert failed.stdout == ''
    check_error(failed.stderr, re.compile('basis mismatch'))
    assert 'failed' not in c.listdir()


def precondition_failures(c: Context, cases: list[tuple[list[str], Any]], snap: bool = False) -> None:
    """Each ``--dry-run <args> source.json`` exits 1 with empty stdout and writes nothing."""
    for args, error in cases:
        before = snapshot(c.cwd) if snap else c.listdir()
        result = c.run(['--dry-run', *args, 'source.json'], 1)
        assert result.stdout == ''
        check_error(result.stderr, error)
        assert (snapshot(c.cwd) if snap else c.listdir()) == before


def common_cli_checks(c: Context, flag: str) -> None:
    assert c.run([flag, '--dry-run'], 1).stderr == err('At least one export is required; see --help')
    assert c.run([flag, 'source.json'], 1).stderr == err('--out is required unless --dry-run')
    assert re.search(f'{re.escape(flag)}.*Requires --wallabag-export', c.run([flag, '--help']).stdout, re.S)
    assert c.run([flag, '--version']).stdout == 'notewrench links 1.0.0\n'


def boolean_flag_cases(flag: str, unknown_negation: bool = True) -> list[tuple[list[str], Any]]:
    cases: list[tuple[list[str], Any]] = [
        ([flag], err(f'{flag} requires --wallabag-export')),
        ([f'{flag}=true', '--wallabag-export'], re.compile('does not take an argument')),
        ([f'{flag}=false', '--wallabag-export'], re.compile('does not take an argument')),
    ]
    if unknown_negation:
        cases.append(([f'--no-{flag[2:]}', '--wallabag-export'], re.compile('Unknown option')))
    return cases


PATH_CASES = lambda flag: [  # noqa: E731
    ([flag, '--wallabag-export', '--out', 'preview'], re.compile('already exists')),
    ([flag, '--wallabag-export', '--out', 'missing/child'], re.compile('ENOENT')),
]
