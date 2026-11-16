# SPDX-License-Identifier: AGPL-3.0-only
# Port of linkdedupe tests/require-all-applied-cli.test.js (scenarios from
# scripts/require-all-applied-contract.mjs).
import copy
import json
import re

import pytest

from .contract import (MIXED, PATH_CASES, Context, boolean_flag_cases, decision, err,
                       malformed_decisions_scenario, precondition_failures, write)

POLICY = '--require-all-applied'
MODES = [[], ['--dry-run'], ['--dry-run', '--out', 'dry-policy']]


def diagnostic(index, status, reason):
    return err(f'{POLICY}: decision {index} {status}/{reason}')


@pytest.fixture
def c(cwd):
    return Context(cwd)


def rejects(c, args, expected):
    before = c.listdir()
    for outcomes in [[], ['--decision-outcomes']]:
        for mode in MODES:
            output = mode or ['--out', 'rejected-policy']
            result = c.invoke([POLICY, *outcomes, *output, *args], 1)
            assert result.stdout == ''
            assert result.stderr == expected
            assert c.listdir() == before, 'rejection creates no output directory'


def succeeds(c, name, args):
    for suffix, outcomes in [('plain', []), ('outcomes', ['--decision-outcomes'])]:
        target = f'{name}-{suffix}'
        base = [*args, *outcomes]
        ordinary = c.invoke(['--out', f'{target}-default', *base])
        strict = c.invoke([POLICY, '--out', target, *base])
        assert strict.stdout == ordinary.stdout, 'policy adds no stdout fields'
        assert strict.stderr == ordinary.stderr
        files = sorted((c.cwd / target).iterdir())
        assert len(files) == 7
        assert [f.name for f in files] == sorted(p.name for p in (c.cwd / f'{target}-default').iterdir())
        for file in files:
            assert file.read_bytes() == (c.cwd / f'{target}-default' / file.name).read_bytes(), file.name
        audit = c.json(f'{target}/wallabag-merge-report.json')
        assert audit['basisSha256'] == c.template['basisSha256']
        assert c.json(f'{target}/wallabag-decisions.json') == c.template
        assert audit['roundTrip']['verified'] is True
        if outcomes:
            assert json.loads(strict.stdout)['wallabagDecisionOutcomes'] == audit['decisionOutcomes']
        else:
            assert 'wallabagDecisionOutcomes' not in json.loads(strict.stdout)
        before = c.listdir()
        for out in [[], ['--out', f'{target}-dry']]:
            dry = c.invoke([POLICY, '--dry-run', *out, *base])
            assert json.loads(dry.stdout) == {**json.loads(strict.stdout), 'dryRun': True}
        assert c.listdir() == before, 'successful dry-run writes nothing'
    assert (c.cwd / 'source.json').read_text(encoding='utf-8') == c.original_text


def test_mixed_blocked_applied_decisions_reject_before_publication_with_or_without_stdout_outcomes(c):
    args = c.selections('mixed', MIXED)
    rejects(c, args, diagnostic(0, 'blocked', 'annotation-content-mismatch'))
    # Without the policy the same request still partially applies.
    c.invoke(['--out', 'partial', *args])
    audit = c.json('partial/wallabag-merge-report.json')
    assert [[o['status'], o['reason']] for o in audit['decisionOutcomes']] == [
        ['blocked', 'annotation-content-mismatch'], ['applied', 'explicitly-approved']]
    assert [r['id'] for r in c.json('partial/wallabag-export.json')] == [101, 102, 103, 105, 106]
    later = c.selections('blocked-later', [MIXED[1], MIXED[0]])
    rejects(c, later, diagnostic(1, 'blocked', 'annotation-content-mismatch'))


def _annotation(rows):
    rows[4]['annotations'] = [{'text': 'invalid'}]


def _identity_id(rows):
    rows[4]['id'] = '101'
    rows[4]['annotations'] = [{'text': 'invalid'}]


def _identity_uid(rows):
    rows[4]['uid'] = rows[0]['uid'] = 'duplicate'


def test_every_native_blocker_and_guard_precedence_reject_before_publication(c):
    original = json.loads(c.original_text)
    for name, mutate, reason in [('annotation', _annotation, 'unrepresentable-annotation'),
                                 ('identity-id', _identity_id, 'ambiguous-native-identity'),
                                 ('identity-uid', _identity_uid, 'ambiguous-native-identity')]:
        rows = copy.deepcopy(original)
        mutate(rows)
        write(c.cwd / f'{name}.json', rows)
        c.run(['--wallabag-export', '--out', f'{name}-preview', f'{name}.json'])
        template = c.json(f'{name}-preview/wallabag-decisions.json')
        write(c.cwd / f'{name}-decisions.json', {**template, 'decisions': MIXED})

        def wrap(inner, args, expected, name=name):
            return c.run(['--wallabag-export', '--skip-invalid', *args, '--decisions', f'{name}-decisions.json',
                          f'{name}.json'], expected)
        rejects(c.with_invoke(wrap), [], diagnostic(0, 'blocked', reason))


def test_all_applied_disjoint_subgroups_and_later_keepers_preserve_files_basis_template_stdout_and_dry_run(c):
    args = c.selections('later', [decision([4, 2], 4), decision([3, 1], 3)])
    succeeds(c, 'later', args)
    assert [r['id'] for r in c.json('later-outcomes/wallabag-export.json')] == [103, 104, 105, 106]
    assert c.json('later-outcomes/wallabag-merge-report.json')['decisionOutcomes'] == [
        {'decisionIndex': 0, 'sourceRefs': ['s1:r2', 's1:r4'], 'keepSourceRef': 's1:r4', 'status': 'applied',
         'reason': 'explicitly-approved', 'outputPointer': '/1'},
        {'decisionIndex': 1, 'sourceRefs': ['s1:r1', 's1:r3'], 'keepSourceRef': 's1:r3', 'status': 'applied',
         'reason': 'explicitly-approved', 'outputPointer': '/0'},
    ]


def test_false_and_template_rows_reject_while_omitted_and_zero_decisions_pass_vacuously(c):
    for name, rows, index in [('false-first', [decision([5, 1], 5, False), MIXED[1]], 0),
                              ('false-later', [MIXED[1], decision([5, 1], 5, False)], 1),
                              ('template', c.template['decisions'], 0)]:
        rejects(c, c.selections(name, rows), diagnostic(index, 'not-approved', 'not-approved'))
    succeeds(c, 'omitted', [])
    succeeds(c, 'zero', c.selections('zero-decisions', []))
    write(c.cwd / 'empty.json', '[]')
    c.run(['--wallabag-export', '--format', 'wallabag-v2', '--out', 'empty-preview', 'empty.json'])
    write(c.cwd / 'empty-decisions.json', c.json('empty-preview/wallabag-decisions.json'))
    for decisions in [[], ['--decisions', 'empty-decisions.json']]:
        before = c.listdir()
        for out in [[], ['--out', 'empty-dry']]:
            result = c.run(['--wallabag-export', POLICY, '--decision-outcomes', '--dry-run', *out,
                            '--format', 'wallabag-v2', *decisions, 'empty.json'])
            assert json.loads(result.stdout)['wallabagDecisionOutcomes'] == []
        assert c.listdir() == before


def test_malformed_stale_and_late_cross_candidate_approvals_retain_validation_errors_before_policy(c):
    malformed_decisions_scenario(c.prefixed(POLICY))


def test_boolean_flag_requires_native_export_and_retains_path_validation_precedence(c):
    precondition_failures(c, [*boolean_flag_cases(POLICY, unknown_negation=False), *PATH_CASES(POLICY)])
    assert re.search(f'{POLICY}.*Requires --wallabag-export', c.run(['--help']).stdout, re.S)
