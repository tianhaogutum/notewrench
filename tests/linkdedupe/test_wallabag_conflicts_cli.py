# SPDX-License-Identifier: AGPL-3.0-only
# Port of linkdedupe tests/wallabag-conflicts-cli.test.js (scenarios from
# scripts/wallabag-conflicts-contract.mjs). The optional LINKDEDUPE_BASELINE_CLI
# byte-parity run against a previously released CLI is not ported.
import json
import re

import pytest

from .contract import (FILES, MIXED, PATH_CASES, Context, boolean_flag_cases, check_error, common_cli_checks, decision, err,
                       js, malformed_decisions_scenario, precondition_failures, snapshot)

FLAG = '--wallabag-conflicts'
FIELD = 'wallabagConflicts'
COMBINATIONS = [[], ['--wallabag-mapping'], ['--decision-outcomes'], ['--wallabag-mapping', '--decision-outcomes']]
_ABSENT = object()


def value(n, present, data=_ABSENT):
    return {'sourceRef': f's1:r{n}', 'present': present, **({'value': data} if present else {})}


def conflict(field, keeper, values, resolution='keeper-value-or-absence'):
    return {'field': field, 'keptSourceRef': f's1:r{keeper}', 'resolution': resolution, 'values': values}


def pair(rows, a, b, keeper, fields):
    return [conflict(key, keeper, [value(n, key in rows[n - 1], rows[n - 1].get(key)) for n in (a, b)]) for key in fields]


@pytest.fixture
def c(cwd):
    return Context(cwd)


def succeeds(c, name, args, expected):
    for i, extra in enumerate(COMBINATIONS):
        target, base = f'{name}-{i}', [*extra, *args]
        written = c.invoke([FLAG, '--out', target, *base])
        summary, audit = json.loads(written.stdout), c.json(f'{target}/wallabag-merge-report.json')
        assert summary[FIELD] == expected, 'independent exact conflict expectation'
        assert summary[FIELD] == audit['conflicts']
        assert audit['summary']['conflicts'] == len(expected)
        for option, key, array in [('--wallabag-mapping', 'wallabagMapping', 'mapping'),
                                   ('--decision-outcomes', 'wallabagDecisionOutcomes', 'decisionOutcomes')]:
            if option in extra:
                assert summary[key] == audit[array]
            else:
                assert key not in summary
        assert audit['basisSha256'] == c.template['basisSha256']
        assert c.json(f'{target}/wallabag-decisions.json') == c.template
        assert audit['roundTrip']['verified'] is True
        ordinary = c.invoke(['--out', f'{target}-default', *base])
        assert ordinary.stdout == js({k: v for k, v in summary.items() if k != FIELD}) + '\n', 'only conflicts field is added'
        assert written.stderr == ordinary.stderr
        assert sorted(p.name for p in (c.cwd / target).iterdir()) == FILES
        assert sorted(p.name for p in (c.cwd / f'{target}-default').iterdir()) == FILES
        for file in FILES:
            assert (c.cwd / target / file).read_bytes() == (c.cwd / f'{target}-default' / file).read_bytes(), file
        before = snapshot(c.cwd)
        for out in [[], ['--out', f'{target}-dry']]:
            dry = c.invoke([FLAG, '--dry-run', *out, *base])
            assert json.loads(dry.stdout) == {**summary, 'dryRun': True}
            assert dry.stderr == written.stderr
        assert snapshot(c.cwd) == before, 'dry-run with/without --out writes nothing'
    assert (c.cwd / 'source.json').read_text(encoding='utf-8') == c.original_text
    return c.json(f'{name}-0/wallabag-export.json')


def rejects(c, args, expected):
    before = snapshot(c.cwd)
    for extra in [[], ['--wallabag-mapping', '--decision-outcomes']]:
        for mode in [['--out', 'rejected'], ['--dry-run'], ['--dry-run', '--out', 'dry-rejected']]:
            base = [*extra, *mode, *args]
            ordinary, result = c.invoke(base, 1), c.invoke([FLAG, *base], 1)
            assert result.stdout == ''
            assert result.stderr == ordinary.stderr, 'conflicts preserves prior error'
            check_error(result.stderr, expected)
    assert snapshot(c.cwd) == before, 'failure writes nothing'


def test_later_keeper_exposes_exact_base_conflicts_independently_and_with_mapping_outcomes(c):
    rows = json.loads(c.original_text)
    args = c.selections('approved', [decision([4, 2], 4)])
    output = succeeds(c, 'base', args, pair(rows, 2, 4, 4, ['id', 'title', 'url', 'created_at']))
    assert output == [rows[0], {**rows[3], 'tags': ['tag-2', 'shared', 'tag-4'],
                                'annotations': [*rows[1]['annotations'], *rows[3]['annotations']]}, rows[2], rows[4], rows[5]]
    assert [m['outputPointer'] for m in c.json('base-0/wallabag-merge-report.json')['mapping']] == ['/0', '/1', '/2', '/1', '/3', '/4']


def test_disjoint_groups_source_order_missing_scalars_and_audit_only_native_values_remain_exact(c):
    rows = json.loads(c.original_text)
    del rows[2]['created_at']
    rows[0]['extension'] = {'list': [3, 1], 'enabled': False}
    rows[2]['extension'] = {'list': [1, 3], 'enabled': True}
    rows[0]['nullable'] = None  # Unknown scalar present vs absent is also audit-only.
    altered = c.source(rows, 'missing-preview')
    args = altered.selections('reversed', [decision([4, 2], 4), decision([3, 1], 3)])
    expected = [
        *pair(rows, 1, 3, 3, ['id', 'title', 'url', 'created_at']),
        conflict('extension', 3, [value(1, True, rows[0]['extension']), value(3, True, rows[2]['extension'])], 'audit-only'),
        conflict('nullable', 3, [value(1, True, None), value(3, False)], 'audit-only'),
        *pair(rows, 2, 4, 4, ['id', 'title', 'url', 'created_at']),
    ]
    output = succeeds(altered, 'disjoint', args, expected)
    assert [r['id'] for r in output] == [103, 104, 105, 106]
    assert 'created_at' not in output[0]
    assert all('extension' not in row and 'nullable' not in row for row in output)
    assert output[0]['tags'] == ['tag-1', 'shared', 'tag-3']
    assert output[0]['annotations'] == [*rows[0]['annotations'], *rows[2]['annotations']]
    audit = c.json('disjoint-0/wallabag-merge-report.json')
    assert [[r['sourceRef'], r['pointer'], r['value']] for r in audit['unrepresentedFields']] == [
        ['s1:r1', '/extension', rows[0]['extension']], ['s1:r1', '/nullable', None], ['s1:r3', '/extension', rows[2]['extension']]]
    reordered = altered.selections('source-ordered', [decision([1, 3], 3), decision([2, 4], 4)])
    dry = json.loads(altered.invoke([FLAG, '--dry-run', *reordered]).stdout)
    assert dry[FIELD] == expected
    assert [r['raw'] for r in c.json('disjoint-0/provenance.json')['records']] == rows


def _content(rows):
    pass


def _annotation(rows):
    rows[4]['annotations'] = [{'text': 'invalid'}]


def _identity(rows):
    rows[4]['id'] = '101'


def test_omitted_empty_false_and_every_blocked_approval_invent_no_conflicts(c):
    for name, args in [('omitted', []), ('empty', c.selections('zero', [])),
                       ('false', c.selections('false', [decision([5, 1], 5, False)])),
                       ('template', c.selections('template', c.template['decisions']))]:
        succeeds(c, name, args, [])
    for name, change, reason in [('content', _content, 'annotation-content-mismatch'),
                                 ('annotation', _annotation, 'unrepresentable-annotation'),
                                 ('identity', _identity, 'ambiguous-native-identity')]:
        rows = json.loads(c.original_text)
        change(rows)
        altered = c.source(rows, f'{name}-preview')
        blocked = altered.selections(f'{name}-blocked', [decision([5, 1], 1)])
        succeeds(altered, name, blocked, [])
        rejects(altered, ['--require-all-applied', *blocked], err(f'--require-all-applied: decision 0 blocked/{reason}'))
        # A blocked group must not suppress conflicts of a disjoint applied group.
        mixed_args = altered.selections(f'{name}-mixed', MIXED)
        succeeds(altered, f'{name}-mixed', mixed_args, pair(rows, 2, 4, 2, ['id', 'title', 'url', 'created_at']))
        if name == 'annotation':
            rejects(altered, ['--require-all-processed', *mixed_args], re.compile('s1:r5 unprocessed'))


def test_empty_all_rejected_and_unselected_unprocessed_dumps_have_no_invented_conflicts(c):
    empty = c.source([], 'empty-preview')
    succeeds(empty, 'empty', ['--require-all-applied', '--require-all-processed'], [])
    succeeds(empty, 'empty-zero', empty.selections('empty-zero', []), [])
    rows = json.loads(c.original_text)
    rows[4]['annotations'] = [{'text': 'invalid'}]
    rows[5]['tags'] = 'invalid'
    altered = c.source(rows, 'unprocessed-preview')
    for name, args in [('omitted', []), ('zero', altered.selections('zero', [])),
                       ('false', altered.selections('false', [decision([5, 1], 5, False)]))]:
        succeeds(altered, f'unprocessed-{name}', ['--skip-invalid', *args], [])
    rejects(altered, [], re.compile('tags must be an array'))
    rejects(altered, ['--skip-invalid', '--require-all-processed'], re.compile('s1:r5 unprocessed'))
    rejected = c.source([None, {**rows[0], 'tags': 'invalid'}], 'rejected-preview')
    succeeds(rejected, 'all-rejected', ['--skip-invalid'], [])
    rejects(rejected, ['--skip-invalid', '--require-all-processed'], re.compile('s1:r1 unprocessed'))


def test_invalid_stale_and_late_invalid_decisions_fail_before_conflicts_and_policies(c):
    malformed_decisions_scenario(c.prefixed(FLAG, '--wallabag-mapping', '--require-all-applied', '--require-all-processed'))


def test_policy_rejections_and_validation_precedence_remain_fail_closed(c):
    args = c.selections('mixed', MIXED)
    rejects(c, ['--require-all-applied', *args], err('--require-all-applied: decision 0 blocked/annotation-content-mismatch'))
    false_args = c.selections('false-policy', [decision([4, 2], 4, False)])
    rejects(c, ['--require-all-applied', *false_args], err('--require-all-applied: decision 0 not-approved/not-approved'))
    precondition_failures(c, [
        *boolean_flag_cases(FLAG),
        *[([FLAG, f'--{name}'], err(f'--{name} requires --wallabag-export'))
          for name in ['decision-outcomes', 'require-all-applied', 'require-all-processed', 'wallabag-mapping']],
        ([FLAG, '--decisions', 'missing.json'], err('--decisions requires --wallabag-export')),
        ([FLAG, '--compare-clusters'], err('--compare-clusters requires --rules')),
        *PATH_CASES(FLAG),
    ], snap=True)
    common_cli_checks(c, FLAG)
