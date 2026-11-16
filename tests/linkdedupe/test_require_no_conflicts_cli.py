# SPDX-License-Identifier: AGPL-3.0-only
# Port of linkdedupe tests/require-no-conflicts-cli.test.js (scenarios from
# scripts/require-no-conflicts-contract.mjs). The optional LINKDEDUPE_BASELINE_CLI
# byte-parity run against a previously released CLI is not ported.
import copy
import itertools
import json
import re

import pytest

from .contract import (FILES, PATH_CASES, Context, boolean_flag_cases, check_error, common_cli_checks, decision, err,
                       precondition_failures, snapshot, write)

POLICY = '--require-no-conflicts'
UNREPRESENTED = '--require-no-unrepresented'
APPLIED, PROCESSED = '--require-all-applied', '--require-all-processed'
AUDIT_FLAGS = ['--wallabag-mapping', '--decision-outcomes', '--wallabag-conflicts', '--wallabag-unrepresented']
MODES = [['--out', 'rejected'], ['--dry-run'], ['--dry-run', '--out', 'dry-rejected']]
EVERY_ORDER = [list(p) for p in itertools.permutations([POLICY, UNREPRESENTED, APPLIED, PROCESSED])]
_ABSENT = object()


def diagnostic(row):
    return err(f"{POLICY}: {row['keptSourceRef']} /{row['field'].replace('~', '~0').replace('/', '~1')}: {row['resolution']}")


def value(n, present, data=_ABSENT):
    return {'sourceRef': f's1:r{n}', 'present': present, **({'value': data} if present else {})}


def conflict(field, keeper, values, resolution='keeper-value-or-absence'):
    return {'field': field, 'keptSourceRef': f's1:r{keeper}', 'resolution': resolution, 'values': values}


def pair(rows, a, b, keeper, fields):
    return [conflict(key, keeper, [value(n, key in rows[n - 1], rows[n - 1].get(key)) for n in (a, b)]) for key in fields]


@pytest.fixture
def c(cwd):
    return Context(cwd)


def rejects(c, args, expected, policies=([POLICY],)):
    before = snapshot(c.cwd)
    for flags in policies:
        for extra in [[], AUDIT_FLAGS]:
            for mode in MODES:
                result = c.invoke([*flags, *extra, *mode, *args], 1)
                assert result.stdout == ''
                check_error(result.stderr, expected)
                assert snapshot(c.cwd) == before, 'failure creates no files/directories and changes no bytes'


def succeeds(c, name, args=(), policies=(POLICY,)):
    # Each stdout flag independently and all flags together; they remain opt-in.
    for i, extra in enumerate([[], *([flag] for flag in AUDIT_FLAGS), AUDIT_FLAGS]):
        target, base = f'{name}-{i}', [*extra, *args]
        ordinary = c.invoke(['--out', f'{target}-default', *base])
        strict = c.invoke([*policies, '--out', target, *base])
        assert strict.stdout == ordinary.stdout, 'policy adds no stdout fields or bytes'
        assert strict.stderr == ordinary.stderr
        audit = c.json(f'{target}/wallabag-merge-report.json')
        assert audit['conflicts'] == []
        assert audit['basisSha256'] == c.template['basisSha256']
        assert c.json(f'{target}/wallabag-decisions.json') == c.template
        assert audit['roundTrip']['verified'] is True
        assert sorted(p.name for p in (c.cwd / target).iterdir()) == FILES
        assert sorted(p.name for p in (c.cwd / f'{target}-default').iterdir()) == FILES
        for file in FILES:
            assert (c.cwd / target / file).read_bytes() == (c.cwd / f'{target}-default' / file).read_bytes(), file
        before = snapshot(c.cwd)
        for out in [[], ['--out', f'{target}-dry']]:
            dry = c.invoke([*policies, '--dry-run', *out, *base])
            plain_dry = c.invoke(['--dry-run', *out, *base])
            assert dry.stdout == plain_dry.stdout
            assert dry.stderr == plain_dry.stderr
            assert json.loads(dry.stdout) == {**json.loads(strict.stdout), 'dryRun': True}
        assert snapshot(c.cwd) == before, 'successful dry-run writes nothing'
    assert (c.cwd / 'source.json').read_text(encoding='utf-8') == c.original_text


def test_requested_native_only_conflicts_reject_even_when_every_prior_policy_passes(c):
    rows = json.loads(c.original_text)
    args = c.selections('approved', [decision([3, 1], 3)])
    expected = pair(rows, 1, 3, 3, ['id', 'title', 'url', 'created_at'])
    ordinary = c.invoke([*AUDIT_FLAGS, '--out', 'approved-default', *args])
    audit = c.json('approved-default/wallabag-merge-report.json')
    assert audit['conflicts'] == expected
    assert json.loads(ordinary.stdout)['wallabagConflicts'] == expected
    assert audit['unrepresentedFields'] == []
    assert all(row['status'] != 'unprocessed' for row in audit['mapping'])
    assert all(row['status'] == 'applied' for row in audit['decisionOutcomes'])
    assert [row['id'] for row in c.json('approved-default/wallabag-export.json')] == [103, 102, 104, 105, 106]
    rejects(c, args, err('--require-no-conflicts: s1:r3 /id: keeper-value-or-absence'), [[POLICY], *EVERY_ORDER])


@pytest.mark.parametrize('index,field,left,right,resolution,pointer', [
    (0, 'created_at', _ABSENT, None, 'keeper-value-or-absence', '/created_at'),
    (1, 'zeta~/first', False, None, 'audit-only', '/zeta~0~1first'),
    (2, 'a/b~', [3, 1], {'enabled': False}, 'audit-only', '/a~1b~0'),
    (3, '', None, _ABSENT, 'audit-only', '/'),
], ids=['created_at', 'escaped-tilde-slash', 'escaped-slash-tilde', 'empty-key'])
def test_both_resolutions_missing_versus_null_json_types_and_escaped_fields_reject_in_emitted_order(
        c, index, field, left, right, resolution, pointer):
    # No id or differing URL/title: each selected field is genuinely the first conflict.
    base = {'title': 'Same', 'url': 'https://conflicts.example/a', 'tags': [], 'annotations': []}
    rows = [copy.deepcopy(base) for _ in range(4)]
    if left is not _ABSENT:
        rows[0][field] = left
    if right is not _ABSENT:
        rows[2][field] = right
    # A second group and field test that first means emitted-group/field order, not sorted strings or decision order.
    if left is not _ABSENT:
        rows[0]['alpha'] = [False]
    rows[2]['alpha'] = [True]
    rows[1]['aardvark'] = {'later': True}
    rows[3]['aardvark'] = False
    altered = c.source(rows, f'typed-{index}-preview')
    args = altered.selections(f'typed-{index}', [decision([4, 2], 4), decision([3, 1], 3)])
    altered.invoke([*AUDIT_FLAGS, '--out', f'typed-{index}-ordinary', *args])
    audit = c.json(f'typed-{index}-ordinary/wallabag-merge-report.json')
    expected = [conflict(field, 3, [value(1, left is not _ABSENT, left), value(3, right is not _ABSENT, right)], resolution),
                conflict('alpha', 3, [value(1, left is not _ABSENT, [False]), value(3, True, [True])], 'audit-only'),
                conflict('aardvark', 4, [value(2, True, {'later': True}), value(4, True, False)], 'audit-only')]
    assert audit['conflicts'] == expected
    if index == 0:
        assert field in c.json(f'typed-{index}-ordinary/wallabag-export.json')[0]
    rejects(altered, args, err(f'{POLICY}: s1:r3 {pointer}: {resolution}'))
    reordered = altered.selections(f'typed-{index}-reordered', [decision([1, 3], 3), decision([2, 4], 4)])
    assert json.loads(altered.invoke(['--wallabag-conflicts', '--dry-run', *reordered]).stdout)['wallabagConflicts'] == expected
    rejects(altered, reordered, diagnostic(expected[0]))


def test_later_keeper_absence_remains_absence_and_null_is_present(c):
    original = json.loads(c.original_text)
    rows = [copy.deepcopy(original[0]), copy.deepcopy(original[0])]
    del rows[0]['id']
    del rows[1]['id']
    rows[0]['created_at'] = None
    del rows[1]['created_at']
    absent = c.source(rows, 'absent-preview')
    args = absent.selections('absent', [decision([2, 1], 2)])
    absent.invoke(['--out', 'absent-ordinary', *args])
    audit = c.json('absent-ordinary/wallabag-merge-report.json')
    assert audit['conflicts'] == [conflict('created_at', 2, [value(1, True, None), value(2, False)])]
    assert 'created_at' not in c.json('absent-ordinary/wallabag-export.json')[0]
    rejects(absent, args, diagnostic(audit['conflicts'][0]))


def test_fixed_four_policy_precedence_follows_complete_validation_independent_of_flag_order(c):
    rows = json.loads(c.original_text)
    rows[0]['extension'] = False
    rows[4]['annotations'] = [{'text': 'invalid'}]
    altered = c.source(rows, 'precedence-preview')
    applied_only = altered.selections('applied', [decision([3, 1], 3)])
    false_first = altered.selections('false-first', [decision([5, 2], 2, False), decision([3, 1], 3)])
    rejects(altered, false_first, err(f'{APPLIED}: decision 0 not-approved/not-approved'), EVERY_ORDER)
    rejects(altered, applied_only, err(f"{PROCESSED}: s1:r5 unprocessed: {altered.audit['mapping'][4]['reason']}"), EVERY_ORDER)
    rows[4] = json.loads(c.original_text)[4]
    represented = c.source(rows, 'represented-preview')
    args = represented.selections('represented', [decision([3, 1], 3)])
    rejects(represented, args, err(f'{UNREPRESENTED}: s1:r1 /extension: outside-pinned-native-fields'), EVERY_ORDER)
    rejects(represented, args, err('--require-no-conflicts: s1:r3 /id: keeper-value-or-absence'))


def _content(rows):
    pass


def _annotation(rows):
    rows[4]['annotations'] = [{'text': 'invalid'}]


def _identity(rows):
    rows[4]['id'] = '101'


def test_empty_conflicts_pass_omitted_empty_false_blocked_unchanged_empty_and_quarantined_inputs(c):
    succeeds(c, 'omitted')
    succeeds(c, 'zero', c.selections('zero', []))
    succeeds(c, 'false', c.selections('false', [decision([5, 1], 1, False)]))
    succeeds(c, 'template', c.selections('template', c.template['decisions']))
    for name, change, reason in [('content', _content, 'annotation-content-mismatch'),
                                 ('annotation', _annotation, 'unrepresentable-annotation'),
                                 ('identity', _identity, 'ambiguous-native-identity')]:
        rows = json.loads(c.original_text)
        change(rows)
        blocked = c.source(rows, f'{name}-preview')
        args = blocked.selections(f'{name}-blocked', [decision([5, 1], 1)])
        succeeds(blocked, name, args)
        assert c.json(f'{name}-0/wallabag-merge-report.json')['decisionOutcomes'][0]['reason'] == reason
        # A blocked group does not hide a disjoint applied group's conflicts.
        mixed = blocked.selections(f'{name}-mixed', [decision([5, 1], 1), decision([4, 2], 4)])
        rejects(blocked, mixed, err('--require-no-conflicts: s1:r4 /id: keeper-value-or-absence'))
    same = [{'title': 'Same', 'url': 'https://same.example/', 'tags': []}, {'title': 'Same', 'url': 'https://same.example/', 'tags': []}]
    equal = c.source(same, 'equal-preview')
    succeeds(equal, 'equal', equal.selections('equal', [decision([2, 1], 2)]), [POLICY, APPLIED, PROCESSED, UNREPRESENTED])
    empty = c.source([], 'empty-preview')
    succeeds(empty, 'empty', [], [POLICY, APPLIED, PROCESSED, UNREPRESENTED])
    rejected = c.source([None, {**same[0], 'tags': 'bad', 'extension': False}], 'rejected-preview')
    succeeds(rejected, 'quarantined', ['--skip-invalid'])


def test_invalid_stale_late_invalid_decisions_and_strict_reader_errors_precede_conflicts(c):
    valid = {**c.template, 'decisions': [decision([3, 1], 3)]}
    for name, document, error in [
        ('schema', {**valid, 'unknown': True}, 'schema'),
        ('type', {**valid, 'decisions': [decision([1, 2], 1, 'true')]}, 'Invalid Wallabag decision'),
        ('missing', {**valid, 'decisions': [{'sourceRefs': ['s1:r1', 's1:r2'], 'keepSourceRef': 's1:r1'}]}, 'Invalid Wallabag decision'),
        ('stale', {**valid, 'basisSha256': '0' * 64}, 'basis mismatch'),
        ('late-true', {**valid, 'decisions': [*valid['decisions'], decision([2, 6], 2)]}, 'one current candidate'),
        ('late-false', {**valid, 'decisions': [*valid['decisions'], decision([2, 6], 2, False)]}, 'one current candidate'),
        ('overlap', {**valid, 'decisions': [*valid['decisions'], decision([2, 3], 2, False)]}, 'overlapping'),
        ('foreign', {**valid, 'decisions': [decision([1, 99], 1)]}, 'one current candidate'),
        ('keeper', {**valid, 'decisions': [decision([1, 2], 3)]}, 'keeper'),
        ('json', '{broken', 'JSON'), ('duplicate', '{"decisions":[],"decisions":[]}', 'Duplicate'),
        ('utf8', b'\xff', 'valid UTF-8'),
    ]:
        write(c.cwd / f'{name}.json', document)
        for skip in [[], ['--skip-invalid']]:
            rejects(c, [*skip, '--decisions', f'{name}.json'], re.compile(error),
                    [[POLICY], [POLICY, APPLIED, PROCESSED, UNREPRESENTED]])
    args = c.selections('valid', valid['decisions'])
    rejects(c, ['--sort-query', *args], re.compile('basis mismatch'))
    write(c.cwd / 'source.json', c.original_text + '\n')
    rejects(c, args, re.compile('basis mismatch'))
    rows = json.loads(c.original_text)
    rows[5]['tags'] = 'bad'
    strict = c.source(rows, 'strict-preview')
    strict_args = strict.selections('strict-approved', valid['decisions'])
    rejects(strict, strict_args, re.compile('tags must be an array of strings'))
    rejects(strict, ['--skip-invalid', *strict_args], err('--require-no-conflicts: s1:r3 /id: keeper-value-or-absence'))


def test_boolean_native_mode_path_and_prior_precondition_precedence_stay_exact(c):
    precondition_failures(c, [
        *boolean_flag_cases(POLICY),
        *[([POLICY, f'--{name}'], err(f'--{name} requires --wallabag-export'))
          for name in ['decision-outcomes', 'require-all-applied', 'require-all-processed', 'wallabag-mapping',
                       'wallabag-conflicts', 'wallabag-unrepresented', 'require-no-unrepresented']],
        ([POLICY, '--decisions', 'missing.json'], err('--decisions requires --wallabag-export')),
        ([POLICY, '--compare-clusters'], err('--compare-clusters requires --rules')),
        *PATH_CASES(POLICY),
        ([POLICY, '--wallabag-export', '--format', 'wallabag-v2', '--format', 'auto'], re.compile('Supply one --format')),
    ], snap=True)
    common_cli_checks(c, POLICY)
