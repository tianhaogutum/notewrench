# SPDX-License-Identifier: AGPL-3.0-only
# Port of linkdedupe tests/require-no-unrepresented-cli.test.js (scenarios from
# scripts/require-no-unrepresented-contract.mjs). The optional LINKDEDUPE_BASELINE_CLI
# byte-parity run against a previously released CLI is not ported.
import copy
import itertools
import json
import re

import pytest

from .contract import (FILES, PATH_CASES, Context, boolean_flag_cases, check_error, common_cli_checks, decision, err,
                       precondition_failures, snapshot, write)

POLICY = '--require-no-unrepresented'
APPLIED, PROCESSED = '--require-all-applied', '--require-all-processed'
AUDIT_FLAGS = ['--wallabag-mapping', '--decision-outcomes', '--wallabag-conflicts', '--wallabag-unrepresented']
MODES = [['--out', 'rejected'], ['--dry-run'], ['--dry-run', '--out', 'dry-rejected']]


def diagnostic(row):
    return err(f"{POLICY}: {row['sourceRef']} {row['pointer']}: {row['reason']}")


def unknown(ref, pointer, value, reason='outside-pinned-native-fields'):
    return {'sourceRef': f's1:r{ref}', 'pointer': pointer, 'value': value, 'reason': reason}


def annotation(ref, pointer, value):
    return unknown(ref, pointer, value, 'outside-pinned-native-annotation-fields')


def malformed(ref, value):
    return unknown(ref, '/annotations', value, 'invalid-native-annotation-shape; entire record unprocessed')


def permutations(flags):
    return [list(p) for p in itertools.permutations(flags)]


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
        assert audit['unrepresentedFields'] == []
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


def test_requested_merged_away_extension_rejects_even_when_all_decisions_applied_and_all_rows_processed(c):
    rows = json.loads(c.original_text)
    rows[0]['note'] = 'review me'
    rows[2]['extension'] = {'enabled': False, 'values': [3, 1]}
    rows[0]['annotations'][0]['id'] = 9
    altered = c.source(rows, 'modified-preview')
    args = altered.selections('approved', [decision([3, 1], 3)])
    expected = [unknown(1, '/note', 'review me'), annotation(1, '/annotations/0/id', 9), unknown(3, '/extension', rows[2]['extension'])]
    ordinary = altered.invoke([*AUDIT_FLAGS, '--out', 'approved-default', *args])
    audit = c.json('approved-default/wallabag-merge-report.json')
    assert audit['unrepresentedFields'] == expected
    assert json.loads(ordinary.stdout)['wallabagUnrepresentedFields'] == expected
    assert audit['mapping'][0]['status'] == 'merged'
    assert all(row['status'] != 'unprocessed' for row in audit['mapping'])
    assert all(row['status'] == 'applied' for row in audit['decisionOutcomes'])
    rejects(altered, args, err('--require-no-unrepresented: s1:r1 /note: outside-pinned-native-fields'),
            [[POLICY], *permutations([POLICY, APPLIED, PROCESSED])])


def test_every_audit_reason_json_type_escaped_pointer_and_source_field_order_rejects_without_filtering(c):
    original = json.loads(c.original_text)
    rows = [{**copy.deepcopy(original[5]), 'id': 201 + i, 'url': f'https://example.com/{i}'} for i in range(11)]
    rows[0] = None  # reader quarantine creates a gap; r2 must precede r11.
    rows[1]['zeta'] = False
    rows[1]['a~/b'] = None
    rows[1]['array'] = [3, 1]
    rows[1]['object'] = {'enabled': False}
    rows[1][''] = ''
    rows[1]['zero'] = 0
    rows[1]['annotations'][0]['a~/b'] = {'values': [None, False]}
    rows[10]['alpha'] = 'later'
    expected = [unknown(2, '/zeta', False), unknown(2, '/a~0~1b', None), unknown(2, '/array', [3, 1]),
                unknown(2, '/object', {'enabled': False}), unknown(2, '/', ''), unknown(2, '/zero', 0),
                annotation(2, '/annotations/0/a~0~1b', {'values': [None, False]}), unknown(11, '/alpha', 'later')]
    # Each row becomes first in turn. Test presence rather than value truthiness.
    for i, row in enumerate(expected):
        altered = c.source(rows, f'ordered-{i}')
        assert altered.audit['unrepresentedFields'] == expected[i:]
        rejects(altered, ['--skip-invalid'], diagnostic(row))
        if i < 6:
            del rows[1][['zeta', 'a~/b', 'array', 'object', '', 'zero'][i]]
        elif i == 6:
            del rows[1]['annotations'][0]['a~/b']
    malformed_rows = json.loads(c.original_text)
    malformed_rows[0]['annotations'] = [{'text': 'invalid', 'id': 9}]
    malformed_rows[0]['extension'] = False
    bad = c.source(malformed_rows, 'malformed-preview')
    assert bad.audit['unrepresentedFields'] == [malformed(1, malformed_rows[0]['annotations']),
                                                unknown(1, '/extension', False), annotation(1, '/annotations/0/id', 9)]
    rejects(bad, [], diagnostic(bad.audit['unrepresentedFields'][0]))
    rejects(bad, [], err(f"{PROCESSED}: s1:r1 unprocessed: {bad.audit['mapping'][0]['reason']}"),
            [[POLICY, PROCESSED], *permutations([POLICY, APPLIED, PROCESSED])])


def _content(rows):
    pass


def _annotation(rows):
    rows[4]['annotations'] = [{'text': 'invalid', 'id': 19}]


def _identity(rows):
    rows[4]['id'] = '101'


def test_omitted_empty_false_template_and_all_blocked_decisions_retain_identical_source_audit(c):
    original = json.loads(c.original_text)
    original[4]['extension'] = [False, None]
    altered = c.source(original, 'unselected-preview')
    expected = [unknown(5, '/extension', [False, None])]
    for args in [[], altered.selections('empty', []), altered.selections('false', [decision([5, 1], 1, False)]),
                 altered.selections('template', altered.template['decisions'])]:
        stdout = json.loads(altered.invoke(['--wallabag-unrepresented', '--dry-run', *args]).stdout)
        assert stdout['wallabagUnrepresentedFields'] == expected
        rejects(altered, args, diagnostic(expected[0]))
    args = altered.selections('false-policy', [decision([5, 1], 1, False)])
    rejects(altered, args, err(f'{APPLIED}: decision 0 not-approved/not-approved'), [[POLICY, APPLIED], [APPLIED, POLICY]])
    for name, change, reason in [('content', _content, 'annotation-content-mismatch'),
                                 ('annotation', _annotation, 'unrepresentable-annotation'),
                                 ('identity', _identity, 'ambiguous-native-identity')]:
        rows = copy.deepcopy(original)
        change(rows)
        blocked = c.source(rows, f'{name}-preview')
        args = blocked.selections(f'{name}-approved', [decision([5, 1], 1)])
        preview = json.loads(blocked.invoke([*AUDIT_FLAGS, '--dry-run', *args]).stdout)
        assert preview['wallabagDecisionOutcomes'][0]['reason'] == reason
        assert preview['wallabagUnrepresentedFields'] == blocked.audit['unrepresentedFields']
        rejects(blocked, args, diagnostic(blocked.audit['unrepresentedFields'][0]))
        rejects(blocked, args, err(f'{APPLIED}: decision 0 blocked/{reason}'), permutations([POLICY, APPLIED, PROCESSED]))


def test_no_audit_rows_pass_including_scalar_conflicts_empty_dumps_and_reader_quarantine(c):
    native = c.source(json.loads(c.original_text), 'native-preview')
    args = native.selections('applied', [decision([3, 1], 3)])
    succeeds(native, 'native', args, [POLICY, PROCESSED, APPLIED])
    assert len(c.json('native-0/wallabag-merge-report.json')['conflicts']) > 0, 'scalar conflicts alone pass'
    succeeds(native, 'omitted')
    succeeds(native, 'zero', native.selections('zero', []))
    succeeds(native, 'false', native.selections('false', [decision([5, 1], 1, False)]))
    succeeds(native, 'blocked', native.selections('blocked', [decision([5, 1], 1)]))
    empty = c.source([], 'empty-preview')
    succeeds(empty, 'empty', [], [POLICY, APPLIED, PROCESSED])
    rows = json.loads(c.original_text)
    rows[0]['annotations'] = [None]
    rows[0]['note'] = 'reader rejected'
    rows[1]['annotations'] = 'bad'
    rows[1]['unknown'] = False
    rejected = c.source([None, rows[0], rows[1], {**rows[2], 'tags': 'bad', 'extension': None}], 'rejected-preview')
    assert rejected.audit['unrepresentedFields'] == []
    succeeds(rejected, 'quarantine', ['--skip-invalid'], [POLICY, APPLIED])
    rejects(rejected, ['--skip-invalid'], err(f'{PROCESSED}: s1:r1 unprocessed: Record must be an object'),
            [[POLICY, PROCESSED], [PROCESSED, POLICY]])


def test_invalid_stale_and_late_invalid_decisions_plus_strict_reader_errors_precede_policy(c):
    rows = json.loads(c.original_text)
    rows[0]['note'] = 'policy would fail'
    altered = c.source(rows, 'invalid-preview')
    valid = {**altered.template, 'decisions': [decision([3, 1], 3)]}
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
            rejects(altered, [*skip, '--decisions', f'{name}.json'], re.compile(error), [[POLICY], [POLICY, APPLIED, PROCESSED]])
    args = altered.selections('valid', valid['decisions'])
    rejects(altered, ['--sort-query', *args], re.compile('basis mismatch'))
    write(c.cwd / 'source.json', altered.original_text + '\n')
    rejects(altered, args, re.compile('basis mismatch'))
    rows[5]['tags'] = 'bad'
    strict = c.source(rows, 'strict-preview')
    rejects(strict, [], re.compile('tags must be an array of strings'))
    rejects(strict, ['--skip-invalid'], diagnostic(strict.audit['unrepresentedFields'][0]))


def test_boolean_native_mode_path_and_existing_precondition_precedence_remain_exact(c):
    precondition_failures(c, [
        *boolean_flag_cases(POLICY),
        *[([POLICY, f'--{name}'], err(f'--{name} requires --wallabag-export'))
          for name in ['decision-outcomes', 'require-all-applied', 'require-all-processed', 'wallabag-mapping',
                       'wallabag-conflicts', 'wallabag-unrepresented']],
        ([POLICY, '--decisions', 'missing.json'], err('--decisions requires --wallabag-export')),
        ([POLICY, '--compare-clusters'], err('--compare-clusters requires --rules')),
        *PATH_CASES(POLICY),
        ([POLICY, '--wallabag-export', '--format', 'wallabag-v2', '--format', 'auto'], re.compile('Supply one --format')),
    ], snap=True)
    common_cli_checks(c, POLICY)
