# SPDX-License-Identifier: AGPL-3.0-only
# Port of linkdedupe tests/wallabag-decisions-cli.test.js (scenarios from
# scripts/wallabag-decisions-contract.mjs). The optional LINKDEDUPE_BASELINE_CLI
# byte-parity run against a previously released CLI is not ported.
import hashlib
import json
import re

import pytest

from notewrench.linkdedupe import build_wallabag_export

from .contract import (FILES, MIXED, PATH_CASES, Context, boolean_flag_cases, check_error, common_cli_checks, decision, err,
                       js, precondition_failures, snapshot, write)

FLAG, FIELD = '--wallabag-decisions', 'wallabagDecisions'
AUDIT_FLAGS = ['--wallabag-mapping', '--decision-outcomes', '--wallabag-conflicts', '--wallabag-unrepresented']
POLICIES = ['--require-all-applied', '--require-all-processed', '--require-no-unrepresented', '--require-no-conflicts']
MODES = [['--out', 'rejected'], ['--dry-run'], ['--dry-run', '--out', 'dry-rejected']]
ADAPTER = 'wallabag-v2@2c15fef98033c43d86cf3dcdd97bfe53f3b4f22d'
ORIGINAL_BASIS = 'b898bd2458d8dc273eb86c7335144c2d5dc6c9a41a9b02a5d191b33ef42074c4'


def expected(basis_sha256, rows=None):
    return {'schemaVersion': 1, 'adapter': ADAPTER, 'basisSha256': basis_sha256,
            'decisions': [decision([1, 2, 3, 4, 5], 1, False)] if rows is None else rows}


# Independent basis calculation from the public normalization contract.
def stable(value):
    if isinstance(value, list):
        return '[' + ','.join(stable(item) for item in value) + ']'
    if isinstance(value, dict):
        return '{' + ','.join(f'{json.dumps(k, ensure_ascii=False)}:{stable(value[k])}' for k in sorted(value)) + '}'
    return json.dumps(value, ensure_ascii=False)


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def basis(text, **normalization):
    return sha(stable({'adapter': ADAPTER, 'sourceSha256': sha(text), 'normalization': {
        'keepTracking': False, 'stripFragment': False, 'sortQuery': False, 'https': False, 'stripTrailingSlash': False,
        'dropParams': [], 'rules': None, **normalization}}))


def source(c, text):
    write(c.cwd / 'source.json', text)
    derived = c.with_invoke(lambda inner, args, expected_status: inner(args, expected_status))
    derived.original_text = text
    return derived


def selections(c, name, template, rows):
    write(c.cwd / f'{name}.json', {**template, 'decisions': rows})
    return ['--decisions', f'{name}.json']


@pytest.fixture
def c(cwd):
    return Context(cwd)


def succeeds(c, name, template, args=(), options=None, combinations=([], AUDIT_FLAGS)):
    for i, extra in enumerate(combinations):
        target, base = f'{name}-{i}', [*extra, *args]
        result = c.invoke([FLAG, '--out', target, *base])
        stdout, audit = json.loads(result.stdout), c.json(f'{target}/wallabag-merge-report.json')
        assert stdout[FIELD] == template, 'exact independent template expectation'
        assert stdout[FIELD] == c.json(f'{target}/wallabag-decisions.json')
        assert stdout[FIELD] == build_wallabag_export([{'name': 'source.json', 'text': c.original_text, 'format': 'wallabag-v2'}],
                                                      **(options or {}))['wallabagDecisions']
        assert audit['basisSha256'] == template['basisSha256']
        assert audit['roundTrip']['verified'] is True
        for option, key, array in [('--wallabag-mapping', 'wallabagMapping', 'mapping'),
                                   ('--decision-outcomes', 'wallabagDecisionOutcomes', 'decisionOutcomes'),
                                   ('--wallabag-conflicts', 'wallabagConflicts', 'conflicts'),
                                   ('--wallabag-unrepresented', 'wallabagUnrepresentedFields', 'unrepresentedFields')]:
            if option in extra:
                assert stdout[key] == audit[array]
            else:
                assert key not in stdout
        plain = c.invoke(['--out', f'{target}-plain', *base])
        summary = {k: v for k, v in stdout.items() if k != FIELD}
        assert result.stdout == js({**summary, FIELD: template}) + '\n', 'new key is appended last'
        assert plain.stdout == js(summary) + '\n', 'only opt-in key added'
        assert plain.stderr == result.stderr
        assert sorted(p.name for p in (c.cwd / target).iterdir()) == FILES
        assert sorted(p.name for p in (c.cwd / f'{target}-plain').iterdir()) == FILES
        for file in FILES:
            assert (c.cwd / target / file).read_bytes() == (c.cwd / f'{target}-plain' / file).read_bytes(), file
        before = snapshot(c.cwd)
        for out in [[], ['--out', f'{target}-dry']]:
            dry = c.invoke([FLAG, '--dry-run', *out, *base])
            assert json.loads(dry.stdout) == {**stdout, 'dryRun': True}
            assert dry.stderr == result.stderr
        assert snapshot(c.cwd) == before, 'dry-run writes zero files/directories'


def rejects(c, args, error, policy_sets=([],)):
    before = snapshot(c.cwd)
    for policy in policy_sets:
        for extra in [[], AUDIT_FLAGS]:
            for mode in MODES:
                result = c.invoke([FLAG, *policy, *extra, *mode, *args], 1)
                assert result.stdout == ''
                check_error(result.stderr, error)
                assert snapshot(c.cwd) == before, 'failure writes zero files/directories'


def test_first_pass_dry_run_returns_usable_exact_hash_bound_false_template_without_output_or_decisions(c):
    before = snapshot(c.cwd)
    result = c.invoke([FLAG, '--dry-run'])
    assert json.loads(result.stdout)[FIELD] == expected(ORIGINAL_BASIS)
    assert result.stderr == ''
    assert snapshot(c.cwd) == before
    assert basis(c.original_text) == ORIGINAL_BASIS
    # This is the object copied from stdout, not a template read from a prior output file.
    document = json.loads(result.stdout)[FIELD]
    document['decisions'] = [decision([3, 1], 3)]
    write(c.cwd / 'from-stdout.json', document)
    c.invoke([FLAG, '--decision-outcomes', '--out', 'from-stdout', '--decisions', 'from-stdout.json'])
    assert c.json('from-stdout/wallabag-export.json')[0]['id'] == 103
    assert [r['status'] for r in c.json('from-stdout/wallabag-merge-report.json')['decisionOutcomes']] == ['applied']


def test_omitted_and_subgroup_approvals_preserve_fresh_template_seven_files_and_all_sixteen_audit_flag_combinations(c):
    combinations = [[flag for i, flag in enumerate(AUDIT_FLAGS) if mask & (1 << i)] for mask in range(16)]
    succeeds(c, 'omitted', expected(ORIGINAL_BASIS), [], {}, combinations)
    rows = [decision([4, 2], 4), decision([3, 1], 3)]
    args = selections(c, 'subgroups', c.template, rows)
    succeeds(c, 'subgroups', expected(ORIGINAL_BASIS), args, {'decisions': {**c.template, 'decisions': rows}}, combinations)
    audit = c.json('subgroups-0/wallabag-merge-report.json')
    assert audit['appliedDecisions']['decisions'] == rows
    assert [[r['status'], r['keepSourceRef']] for r in audit['decisionOutcomes']] == [['applied', 's1:r4'], ['applied', 's1:r3']]
    assert audit['appliedDecisions'] != expected(ORIGINAL_BASIS)


def _content(rows):
    pass


def _annotation(rows):
    rows[4]['annotations'] = [{'text': 'invalid'}]


def _identity(rows):
    rows[4]['id'] = '101'


def test_empty_false_whole_template_and_all_three_blocked_decisions_never_become_the_fresh_template(c):
    for name, rows in [('empty', []), ('false', [decision([5, 1], 5, False)]), ('whole-false', c.template['decisions']),
                       ('mixed', MIXED)]:
        args = selections(c, name, c.template, rows)
        succeeds(c, name, expected(ORIGINAL_BASIS), args, {'decisions': {**c.template, 'decisions': rows}})
    for name, change, reason in [('content', _content, 'annotation-content-mismatch'),
                                 ('annotation', _annotation, 'unrepresentable-annotation'),
                                 ('identity', _identity, 'ambiguous-native-identity')]:
        rows = json.loads(c.original_text)
        change(rows)
        altered = source(c, js(rows))
        template = expected(basis(altered.original_text))
        decisions = [decision([5, 1], 1)]
        args = selections(c, f'{name}-approval', template, decisions)
        succeeds(altered, name, template, args, {'decisions': {**template, 'decisions': decisions}})
        assert c.json(f'{name}-0/wallabag-merge-report.json')['decisionOutcomes'][0]['reason'] == reason


def test_changed_bytes_and_normalization_update_basis_independently_of_candidate_changes_and_reject_stale_approvals(c):
    for name, text, args, normalization, refs in [
        ('whitespace', c.original_text + '\n', [], {}, None),
        ('sort', c.original_text, ['--sort-query'], {'sortQuery': True}, None),
        ('tracking', c.original_text, ['--keep-tracking'], {'keepTracking': True}, []),
    ]:
        altered = source(c, text)
        template = expected(basis(text, **normalization), refs)
        assert template['basisSha256'] != ORIGINAL_BASIS
        succeeds(altered, name, template, args, {'normalization': normalization})
        stale = selections(c, f'{name}-stale', c.template, [decision([3, 1], 3)])
        rejects(altered, [*args, *stale], re.compile('basis mismatch'))


def test_empty_standalone_quarantined_gaps_and_multiple_candidates_do_not_invent_template_members(c):
    original = json.loads(c.original_text)
    for name, rows, refs, skip_invalid in [
        ('empty', [], [], False), ('standalone', [original[5]], [], False),
        ('quarantined', [None, {**original[0], 'tags': 'bad'}], [], True),
        ('gaps', [None, original[0], original[1], {**original[2], 'annotations': [None]}, original[2], original[5]],
         [decision([2, 3, 5], 2, False)], True),
        # Malformed native annotations remain accepted URL candidates; reader quarantine does not.
        ('native-unprocessed', [{**original[0], 'annotations': [{}]}, original[1]], [decision([1, 2], 1, False)], False),
        ('two-candidates', [original[0], original[5], original[1], {**original[5], 'id': 107}],
         [decision([1, 3], 1, False), decision([2, 4], 2, False)], False),
    ]:
        altered = source(c, js(rows))
        succeeds(altered, name, expected(basis(altered.original_text), refs),
                 ['--format', 'wallabag-v2', *(['--skip-invalid'] if skip_invalid else [])], {'skip_invalid': skip_invalid})


def test_complete_invalid_stale_late_invalid_approvals_and_input_validation_precede_stdout_and_four_policies(c):
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
            rejects(c, [*skip, '--decisions', f'{name}.json'], re.compile(error), ([], POLICIES))
    for name, text, error in [
        ('strict', js([None, *json.loads(c.original_text)]), 'Record must be an object'),
        ('json', '{bad', 'JSON'), ('utf8', b'\xff', 'valid UTF-8'),
    ]:
        altered = source(c, text)
        rejects(altered, [], re.compile(error), ([], POLICIES))


def test_all_four_policy_rejections_precede_template_output_and_keep_fixed_precedence(c):
    rows = json.loads(c.original_text)
    rows[0]['extension'] = False
    rows[4]['annotations'] = [{}]
    altered = source(c, js(rows))
    template = expected(basis(altered.original_text))
    for name, decisions, flags, error in [
        ('false', [decision([2, 4], 2, False)], POLICIES, '--require-all-applied: decision 0 not-approved'),
        ('processed', [decision([3, 1], 3)], POLICIES, '--require-all-processed: s1:r5 unprocessed'),
        ('unrepresented', [decision([3, 1], 3)], [POLICIES[2], POLICIES[3]], r'--require-no-unrepresented: s1:r1 /extension'),
    ]:
        args = selections(c, name, template, decisions)
        rejects(altered, args, re.compile(error), (flags, list(reversed(flags))))
    args = selections(c, 'conflicts', c.template, [decision([3, 1], 3)])
    source(c, c.original_text)
    rejects(c, args, err('--require-no-conflicts: s1:r3 /id: keeper-value-or-absence'), (POLICIES, list(reversed(POLICIES))))
    succeeds(c, 'policies-pass', expected(ORIGINAL_BASIS), POLICIES)


def test_boolean_native_preconditions_help_version_paths_and_existing_error_precedence_stay_exact(c):
    precondition_failures(c, [
        *boolean_flag_cases(FLAG),
        *[([FLAG, other], err(f'{other} requires --wallabag-export')) for other in [*AUDIT_FLAGS, *POLICIES]],
        ([FLAG, '--decisions', 'missing.json'], err('--decisions requires --wallabag-export')),
        *[([FLAG, f'--{other}'], err(f'--{other} requires --rules'))
          for other in ['compare-clusters', 'compare-rule-clusters', 'compact-rule-clusters']],
        *PATH_CASES(FLAG),
        ([FLAG, '--wallabag-export', '--format', 'auto', '--format', 'wallabag-v2'], re.compile('Supply one --format')),
        ([FLAG, '--wallabag-export', '--rules', 'missing.json'], re.compile('ENOENT')),
        ([FLAG, '--wallabag-export', '--keep-tracking', '--drop-param', 'utm_extra'], re.compile('conflicts with')),
    ], snap=True)
    common_cli_checks(c, FLAG)
