# SPDX-License-Identifier: AGPL-3.0-only
# Port of linkdedupe tests/wallabag-unrepresented-cli.test.js (scenarios from
# scripts/wallabag-unrepresented-contract.mjs). The optional LINKDEDUPE_BASELINE_CLI
# byte-parity run against a previously released CLI is not ported.
import copy
import json
import re

import pytest

from .contract import (FILES, MIXED, PATH_CASES, Context, boolean_flag_cases, check_error, common_cli_checks, decision, err,
                       js, malformed_decisions_scenario, precondition_failures, snapshot)

FLAG = '--wallabag-unrepresented'
FIELD = 'wallabagUnrepresentedFields'
COMBINATIONS = [[], ['--wallabag-mapping'], ['--decision-outcomes'], ['--wallabag-conflicts'],
                ['--wallabag-mapping', '--decision-outcomes'], ['--wallabag-mapping', '--wallabag-conflicts'],
                ['--decision-outcomes', '--wallabag-conflicts'], ['--wallabag-mapping', '--decision-outcomes', '--wallabag-conflicts']]


def unknown(n, pointer, value, reason='outside-pinned-native-fields'):
    return {'sourceRef': f's1:r{n}', 'pointer': pointer, 'value': value, 'reason': reason}


def annotation(n, pointer, value):
    return unknown(n, pointer, value, 'outside-pinned-native-annotation-fields')


def malformed(n, value):
    return unknown(n, '/annotations', value, 'invalid-native-annotation-shape; entire record unprocessed')


@pytest.fixture
def c(cwd):
    return Context(cwd)


def succeeds(c, name, args, expected):
    for i, extra in enumerate(COMBINATIONS):
        target, base = f'{name}-{i}', [*extra, *args]
        written = c.invoke([FLAG, '--out', target, *base])
        summary, audit = json.loads(written.stdout), c.json(f'{target}/wallabag-merge-report.json')
        assert summary[FIELD] == expected, 'independent exact unrepresented expectation'
        assert summary[FIELD] == audit['unrepresentedFields']
        assert audit['summary']['unrepresentedFields'] == len(expected)
        for option, key, array in [('--wallabag-mapping', 'wallabagMapping', 'mapping'),
                                   ('--decision-outcomes', 'wallabagDecisionOutcomes', 'decisionOutcomes'),
                                   ('--wallabag-conflicts', 'wallabagConflicts', 'conflicts')]:
            if option in extra:
                assert summary[key] == audit[array]
            else:
                assert key not in summary
        assert audit['basisSha256'] == c.template['basisSha256']
        assert c.json(f'{target}/wallabag-decisions.json') == c.template
        assert audit['roundTrip']['verified'] is True
        ordinary = c.invoke(['--out', f'{target}-default', *base])
        assert ordinary.stdout == js({k: v for k, v in summary.items() if k != FIELD}) + '\n', 'only unrepresented field is added'
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
    for extra in [[], ['--wallabag-mapping', '--decision-outcomes', '--wallabag-conflicts']]:
        for mode in [['--out', 'rejected'], ['--dry-run'], ['--dry-run', '--out', 'dry-rejected']]:
            base = [*extra, *mode, *args]
            ordinary, result = c.invoke(base, 1), c.invoke([FLAG, *base], 1)
            assert result.stdout == ''
            assert result.stderr == ordinary.stderr, 'unrepresented preserves prior error'
            check_error(result.stderr, expected)
    assert snapshot(c.cwd) == before, 'failure writes nothing'


def test_merged_away_fields_annotation_metadata_and_unselected_malformed_records_are_exposed_exactly(c):
    rows = json.loads(c.original_text)
    rows[0]['note'] = 'review me'
    rows[2]['extension'] = {'enabled': False, 'values': [3, 1]}
    rows[0]['annotations'][0]['id'] = 9
    rows[4]['annotations'] = [{'text': 'invalid'}]
    altered = c.source(rows, 'modified-preview')
    args = altered.selections('approved', [decision([3, 1], 3)])
    expected = [unknown(1, '/note', 'review me'), annotation(1, '/annotations/0/id', 9),
                unknown(3, '/extension', rows[2]['extension']), malformed(5, rows[4]['annotations'])]
    output = succeeds(altered, 'base', ['--require-all-applied', *args], expected)
    clean = copy.deepcopy(rows)
    del clean[0]['note']
    del clean[0]['annotations'][0]['id']
    del clean[2]['extension']
    assert output == [{**clean[2], 'tags': ['tag-1', 'shared', 'tag-3'],
                       'annotations': [*clean[0]['annotations'], *clean[2]['annotations']]}, clean[1], clean[3], clean[5]]
    assert [[m['status'], m['outputPointer']] for m in c.json('base-0/wallabag-merge-report.json')['mapping']] == [
        ['merged', '/0'], ['retained', '/1'], ['retained', '/0'], ['retained', '/2'], ['unprocessed', None], ['retained', '/3']]
    assert [r['raw'] for r in c.json('base-0/provenance.json')['records']] == rows
    rejects(altered, ['--require-all-processed', *args], re.compile('s1:r5 unprocessed'))
    ordered = altered.selections('source-ordered', [decision([1, 3], 3)])
    assert json.loads(altered.invoke([FLAG, '--dry-run', *ordered]).stdout)[FIELD] == expected


def test_source_field_order_escaped_keys_json_types_and_quarantine_gaps_stay_exact(c):
    rows = json.loads(c.original_text)
    # Insertion order is intentional: annotation extensions follow all top-level extensions.
    rows[0]['annotations'][0]['a~/b'] = {'nested': [None, False, {'x/y': 2}]}
    rows[0]['zeta'] = False
    rows[0]['a~/b'] = 0
    rows[0]['empty'] = ''
    rows[0]['nullable'] = None
    rows[0]['array'] = [3, 1, None]
    rows[0]['alpha'] = {'z': 1, 'a': [True]}
    rows[0][''] = 'empty key'
    rows[0]['annotations'].append({'text': 'second', 'quote': 'same', 'ranges': [], 'nullable': None, 'values': [2, 1]})
    rows[4]['annotations'] = [{'id': 17, 'text': 'invalid'}, {'text': 'ok', 'quote': 'same', 'ranges': [], 'x/~': False}]
    rows[4]['unselected'] = 'still audited'
    rows[5]['standalone'] = {'retained': True}
    tail = {**rows[1], 'id': 111, 'url': 'https://subgroups.example/tail', 'last': None}
    shifted = [None, *rows, *[{**rows[1], 'id': i, 'url': f'https://subgroups.example/{i}'} for i in (108, 109, 110)], tail]
    altered = c.source(shifted, 'shift-preview')
    expected = [
        unknown(2, '/zeta', False), unknown(2, '/a~0~1b', 0), unknown(2, '/empty', ''), unknown(2, '/nullable', None),
        unknown(2, '/array', rows[0]['array']), unknown(2, '/alpha', rows[0]['alpha']), unknown(2, '/', 'empty key'),
        annotation(2, '/annotations/0/a~0~1b', rows[0]['annotations'][0]['a~/b']),
        annotation(2, '/annotations/1/nullable', None), annotation(2, '/annotations/1/values', [2, 1]),
        malformed(6, rows[4]['annotations']), unknown(6, '/unselected', 'still audited'),
        annotation(6, '/annotations/0/id', 17), annotation(6, '/annotations/1/x~1~0', False),
        unknown(7, '/standalone', rows[5]['standalone']), unknown(11, '/last', None),
    ]
    args = altered.selections('shifted-approval', [decision([4, 2], 4)])
    output = succeeds(altered, 'types', ['--skip-invalid', *args], expected)
    assert output[0]['id'] == 103
    assert 'zeta' not in output[0]
    assert 'a~/b' not in output[0]['annotations'][0]
    assert [r['sourceRef'] for r in c.json('types-0/wallabag-merge-report.json')['mapping']] == [f's1:r{i + 1}' for i in range(11)]
    rejects(altered, [], re.compile('Record must be an object'))
    rejects(altered, ['--skip-invalid', '--require-all-processed', *args], re.compile('s1:r1 unprocessed'))


def _content(rows):
    pass


def _annotation(rows):
    rows[4]['annotations'] = [{'text': 'invalid', 'id': 19}]


def _identity(rows):
    rows[4]['id'] = '101'


def test_omitted_empty_false_template_and_every_blocked_approval_preserve_source_audit(c):
    rows = json.loads(c.original_text)
    rows[0]['note'] = 'unmerged'
    rows[4]['extension'] = [False, None]
    expected = [unknown(1, '/note', 'unmerged'), unknown(5, '/extension', [False, None])]
    altered = c.source(rows, 'omitted-preview')
    for name, args in [('omitted', []), ('empty', altered.selections('zero', [])),
                       ('false', altered.selections('false', [decision([5, 1], 5, False)])),
                       ('template', altered.selections('template', altered.template['decisions']))]:
        succeeds(altered, name, args, expected)
    for name, change, reason in [('content', _content, 'annotation-content-mismatch'),
                                 ('annotation', _annotation, 'unrepresentable-annotation'),
                                 ('identity', _identity, 'ambiguous-native-identity')]:
        altered_rows = copy.deepcopy(rows)
        change(altered_rows)
        blocked_context = c.source(altered_rows, f'{name}-preview')
        audit = ([expected[0], malformed(5, altered_rows[4]['annotations']), expected[1], annotation(5, '/annotations/0/id', 19)]
                 if name == 'annotation' else expected)
        blocked = blocked_context.selections(f'{name}-blocked', [decision([5, 1], 1)])
        succeeds(blocked_context, name, blocked, audit)
        rejects(blocked_context, ['--require-all-applied', *blocked], err(f'--require-all-applied: decision 0 blocked/{reason}'))
        mixed_args = blocked_context.selections(f'{name}-mixed', MIXED)
        succeeds(blocked_context, f'{name}-mixed', mixed_args, audit)
        if name == 'annotation':
            rejects(blocked_context, ['--require-all-processed', *mixed_args], re.compile('s1:r5 unprocessed'))


def test_empty_all_rejected_and_reader_rejected_annotations_never_invent_audit_rows(c):
    empty = c.source([], 'empty-preview')
    succeeds(empty, 'empty', ['--require-all-applied', '--require-all-processed'], [])
    succeeds(empty, 'empty-zero', empty.selections('empty-zero', []), [])
    rows = json.loads(c.original_text)
    rows[0]['annotations'] = [None]
    rows[0]['note'] = 'reader rejected'
    rows[1]['annotations'] = 'invalid'
    rows[1]['unknown'] = {'retain': 'provenance only'}
    rejected = c.source([None, rows[0], rows[1], {**rows[2], 'tags': 'invalid', 'unknown': True}], 'rejected-preview')
    succeeds(rejected, 'all-rejected', ['--skip-invalid'], [])
    rejects(rejected, ['--skip-invalid', '--require-all-processed'], re.compile('s1:r1 unprocessed'))
    unprocessed = c.source([rows[0], {**rows[2], 'annotations': [{}], 'extension': None}], 'unprocessed-preview')
    succeeds(unprocessed, 'unprocessed', ['--skip-invalid'], [malformed(2, [{}]), unknown(2, '/extension', None)])
    untouched = c.source(json.loads(c.original_text), 'native-preview')
    succeeds(untouched, 'native', [], [])


def test_invalid_stale_and_late_invalid_decisions_fail_before_audit_and_policies(c):
    malformed_decisions_scenario(c.prefixed(FLAG, '--wallabag-mapping', '--wallabag-conflicts', '--require-all-applied',
                                            '--require-all-processed'))


def test_policy_rejections_boolean_preconditions_and_validation_precedence_remain_fail_closed(c):
    args = c.selections('mixed', MIXED)
    rejects(c, ['--require-all-applied', *args], err('--require-all-applied: decision 0 blocked/annotation-content-mismatch'))
    false_args = c.selections('false-policy', [decision([4, 2], 4, False)])
    rejects(c, ['--require-all-applied', *false_args], err('--require-all-applied: decision 0 not-approved/not-approved'))
    precondition_failures(c, [
        *boolean_flag_cases(FLAG),
        *[([FLAG, f'--{name}'], err(f'--{name} requires --wallabag-export'))
          for name in ['decision-outcomes', 'require-all-applied', 'require-all-processed', 'wallabag-mapping', 'wallabag-conflicts']],
        ([FLAG, '--decisions', 'missing.json'], err('--decisions requires --wallabag-export')),
        ([FLAG, '--compare-clusters'], err('--compare-clusters requires --rules')),
        *PATH_CASES(FLAG),
        ([FLAG, '--wallabag-export', '--format', 'wallabag-v2', '--format', 'auto'], re.compile('Supply one --format')),
    ], snap=True)
    common_cli_checks(c, FLAG)
    # A fully retained blocked/false input still passes the source-processing policy.
    succeeds(c, 'processed-blocked', ['--require-all-processed', *args], [])
    succeeds(c, 'processed-false', ['--require-all-processed', *false_args], [])
