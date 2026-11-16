# SPDX-License-Identifier: AGPL-3.0-only
# Port of linkdedupe tests/wallabag-mapping-cli.test.js (scenarios from
# scripts/wallabag-mapping-contract.mjs).
import json
import re

import pytest

from .contract import (ANNOTATION_REASON, FILES, MIXED, PATH_CASES, Context, boolean_flag_cases, check_error,
                       common_cli_checks, decision, err, js, malformed_decisions_scenario, precondition_failures, snapshot)

FLAG = '--wallabag-mapping'
FIELD = 'wallabagMapping'


def retained(n, pointer, reason='not-approved', keeper=None):
    keeper = n if keeper is None else keeper
    return {'sourceRef': f's1:r{n}', 'status': 'retained' if n == keeper else 'merged', 'keptSourceRef': f's1:r{keeper}',
            'outputPointer': pointer, 'reason': reason}


def unprocessed(n, reason):
    return {'sourceRef': f's1:r{n}', 'status': 'unprocessed', 'outputPointer': None, 'reason': reason}


def normal_mapping(reason):
    return [retained(n, f'/{n - 1}', 'standalone' if n == 6 else reason) for n in range(1, 7)]


@pytest.fixture
def c(cwd):
    return Context(cwd)


# The expected mapping is independent of the API/audit used for parity checks.
def succeeds(c, name, args, expected):
    for suffix, outcomes in [('plain', []), ('outcomes', ['--decision-outcomes'])]:
        target = f'{name}-{suffix}'
        base = [*outcomes, *args]
        written = c.invoke([FLAG, '--out', target, *base])
        summary = json.loads(written.stdout)
        assert summary[FIELD] == expected
        audit = c.json(f'{target}/wallabag-merge-report.json')
        assert summary[FIELD] == audit['mapping']
        if outcomes:
            assert summary['wallabagDecisionOutcomes'] == audit['decisionOutcomes']
        else:
            assert 'wallabagDecisionOutcomes' not in summary
        assert audit['basisSha256'] == c.template['basisSha256']
        assert c.json(f'{target}/wallabag-decisions.json') == c.template
        assert audit['roundTrip']['verified'] is True
        ordinary = c.invoke(['--out', f'{target}-default', *base])
        assert ordinary.stdout == js({k: v for k, v in summary.items() if k != FIELD}) + '\n', 'only mapping field is added'
        assert written.stderr == ordinary.stderr
        assert sorted(p.name for p in (c.cwd / target).iterdir()) == FILES
        assert sorted(p.name for p in (c.cwd / f'{target}-default').iterdir()) == FILES
        for file in FILES:
            assert (c.cwd / target / file).read_bytes() == (c.cwd / f'{target}-default' / file).read_bytes(), file
        # Also guard overwrites of existing files, not just newly-created directory names.
        before = snapshot(c.cwd)
        for out in [[], ['--out', f'{target}-dry']]:
            dry = c.invoke([FLAG, '--dry-run', *out, *base])
            assert json.loads(dry.stdout) == {**summary, 'dryRun': True}
            assert dry.stderr == written.stderr
        assert snapshot(c.cwd) == before, 'dry-run writes nothing'
    assert (c.cwd / 'source.json').read_text(encoding='utf-8') == c.original_text


def rejects(c, args, expected):
    before = snapshot(c.cwd)
    for outcomes in [[], ['--decision-outcomes']]:
        for mode in [['--out', 'rejected'], ['--dry-run'], ['--dry-run', '--out', 'dry-rejected']]:
            base = [*outcomes, *mode, *args]
            ordinary = c.invoke(base, 1)
            result = c.invoke([FLAG, *base], 1)
            assert result.stdout == ''
            assert result.stderr == ordinary.stderr, 'mapping preserves prior error'
            check_error(result.stderr, expected)
    assert snapshot(c.cwd) == before, 'failure writes nothing'


def test_mixed_mapping_retains_source_order_later_keeper_and_exact_unprocessed_reasons(c):
    rows = json.loads(c.original_text)
    rows[4]['annotations'] = [{'text': 'invalid'}]
    rows[5]['tags'] = 'invalid'
    altered = c.source(rows, 'mixed-preview')
    args = ['--skip-invalid', *altered.selections('approved', [decision([4, 2], 4)])]
    succeeds(altered, 'mixed', args, [
        retained(1, '/0'), retained(2, '/1', 'explicitly-approved', 4), retained(3, '/2'),
        retained(4, '/1', 'explicitly-approved'), unprocessed(5, ANNOTATION_REASON),
        unprocessed(6, 'tags must be an array of strings'),
    ])
    assert [row['id'] for row in c.json('mixed-plain/wallabag-export.json')] == [101, 104, 103]
    # Decision policy alone passes this approval; all-source policy still rejects.
    dry = altered.invoke([FLAG, '--require-all-applied', '--dry-run', *args])
    assert len(json.loads(dry.stdout)[FIELD]) == 6
    rejects(altered, ['--require-all-processed', *args], err(f'--require-all-processed: s1:r5 unprocessed: {ANNOTATION_REASON}'))
    rejects(altered, ['--require-all-applied', '--require-all-processed', *args],
            err(f'--require-all-processed: s1:r5 unprocessed: {ANNOTATION_REASON}'))
    rejects(altered, [], re.compile(r'source.json/5: tags must be an array of strings \(strict mode'))


def _content(rows):
    pass


def _annotation(rows):
    rows[4]['annotations'] = [{'text': 'invalid'}]


def _identity(rows):
    rows[4]['id'] = '101'


BLOCKERS = [('content', _content, 'annotation-content-mismatch'),
            ('annotation', _annotation, 'unrepresentable-annotation'),
            ('identity', _identity, 'ambiguous-native-identity')]


def test_omitted_empty_false_and_all_blocker_reasons_preserve_full_mappings(c):
    succeeds(c, 'omitted', [], normal_mapping('not-approved'))
    succeeds(c, 'empty', c.selections('empty-decisions', []), normal_mapping('not-approved'))
    succeeds(c, 'false', c.selections('false-decisions', [decision([5, 1], 5, False)]), normal_mapping('not-approved'))
    for name, change, reason in BLOCKERS:
        rows = json.loads(c.original_text)
        change(rows)
        altered = c.source(rows, f'{name}-preview')
        args = altered.selections(f'{name}-decisions', MIXED)
        expected = [retained(1, '/0', reason), retained(2, '/1', 'explicitly-approved'),
                    retained(3, '/2'), retained(4, '/1', 'explicitly-approved', 2),
                    unprocessed(5, ANNOTATION_REASON) if name == 'annotation' else retained(5, '/3', reason),
                    retained(6, '/3' if name == 'annotation' else '/4', 'standalone')]
        succeeds(altered, name, args, expected)
        for policies in [['--require-all-applied'], ['--require-all-processed', '--require-all-applied']]:
            rejects(altered, [*policies, *args], err(f'--require-all-applied: decision 0 blocked/{reason}'))
    # A false decision is rejected even when every mapping row is retained.
    restored = c.source(json.loads(c.original_text), 'restored-preview')
    args = restored.selections('false-policy', [decision([5, 1], 5, False)])
    rejects(restored, ['--require-all-applied', *args], err('--require-all-applied: decision 0 not-approved/not-approved'))


def test_unselected_unprocessed_rows_remain_visible_for_omitted_empty_and_false_approvals(c):
    rows = json.loads(c.original_text)
    rows[4]['annotations'] = [{'text': 'invalid'}]
    rows[5]['tags'] = 'invalid'
    altered = c.source(rows, 'unselected-preview')
    expected = [*normal_mapping('not-approved')[:4], unprocessed(5, ANNOTATION_REASON),
                unprocessed(6, 'tags must be an array of strings')]
    for name, args in [('omitted', []), ('empty', altered.selections('zero-unselected', [])),
                       ('false', altered.selections('false-unselected', [decision([5, 1], 5, False)]))]:
        succeeds(altered, f'unselected-{name}', ['--skip-invalid', *args], expected)


def test_quarantine_gaps_preserve_numeric_source_order_and_actual_output_positions(c):
    original = json.loads(c.original_text)
    rows = [{**original[5], 'id': 200 + i, 'url': f'https://example.com/{i}'} for i in range(12)]
    rows[0] = None
    rows[1]['tags'] = 'invalid'
    rows[10]['annotations'] = [{'text': 'invalid'}]
    altered = c.source(rows, 'gaps-preview')
    expected = [unprocessed(1, 'Record must be an object'), unprocessed(2, 'tags must be an array of strings'),
                *[retained(i + 3, f'/{i}', 'standalone') for i in range(8)],
                unprocessed(11, ANNOTATION_REASON), retained(12, '/8', 'standalone')]
    succeeds(altered, 'gaps', ['--skip-invalid'], expected)


def test_empty_and_all_rejected_dumps_expose_complete_mappings_without_invented_rows(c):
    original = json.loads(c.original_text)
    empty = c.source([], 'empty-preview')
    succeeds(empty, 'empty-omitted', ['--require-all-applied', '--require-all-processed'], [])
    succeeds(empty, 'empty-zero', empty.selections('zero', []), [])
    rejected = c.source([None, {**original[0], 'tags': 'invalid'}], 'rejected-preview')
    expected = [unprocessed(1, 'Record must be an object'), unprocessed(2, 'tags must be an array of strings')]
    succeeds(rejected, 'rejected', ['--skip-invalid'], expected)
    rejects(rejected, ['--skip-invalid', '--require-all-processed'],
            err('--require-all-processed: s1:r1 unprocessed: Record must be an object'))


def test_malformed_stale_and_late_invalid_approvals_remain_fail_closed_before_mapping_or_policies(c):
    rows = json.loads(c.original_text)
    rows[4]['annotations'] = [{'text': 'invalid'}]
    altered = c.source(rows, 'invalid-preview')
    # Includes malformed JSON/UTF-8, duplicate keys, schema, stale source/policy hash,
    # overlap, and late true/false cross-candidate rows after blocked/applied rows.
    malformed_decisions_scenario(altered.prefixed(FLAG, '--require-all-applied', '--require-all-processed'))


def test_boolean_native_mode_preconditions_and_existing_error_precedence_are_explicit(c):
    precondition_failures(c, [
        *boolean_flag_cases(FLAG),
        ([FLAG, '--decision-outcomes'], err('--decision-outcomes requires --wallabag-export')),
        ([FLAG, '--require-all-applied'], err('--require-all-applied requires --wallabag-export')),
        ([FLAG, '--require-all-processed'], err('--require-all-processed requires --wallabag-export')),
        ([FLAG, '--decisions', 'missing.json'], err('--decisions requires --wallabag-export')),
        ([FLAG, '--compare-clusters'], err('--compare-clusters requires --rules')),
        *PATH_CASES(FLAG),
    ], snap=True)
    common_cli_checks(c, FLAG)
