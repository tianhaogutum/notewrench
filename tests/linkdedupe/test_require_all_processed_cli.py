# SPDX-License-Identifier: AGPL-3.0-only
# Port of linkdedupe tests/require-all-processed-cli.test.js (scenarios from
# scripts/require-all-processed-contract.mjs).
import json

import pytest

from .contract import (ANNOTATION_REASON, MIXED, PATH_CASES, Context, boolean_flag_cases, common_cli_checks, decision, err,
                       malformed_decisions_scenario, precondition_failures)

POLICY = '--require-all-processed'
APPLIED = '--require-all-applied'
MODES = [['--out', 'rejected'], ['--dry-run'], ['--dry-run', '--out', 'dry-rejected']]


def diagnostic(ref, reason):
    return err(f'{POLICY}: {ref} unprocessed: {reason}')


@pytest.fixture
def c(cwd):
    return Context(cwd)


def rejects(c, args, expected, policies=([POLICY], [POLICY, APPLIED]), stdout_modes=(['--decision-outcomes'],)):
    before = c.listdir()
    for flags in policies:
        for outcomes in stdout_modes:
            for mode in MODES:
                result = c.invoke([*flags, *outcomes, *mode, *args], 1)
                assert result.stdout == ''
                assert result.stderr == expected
                assert c.listdir() == before, 'policy failure creates no directory'


def succeeds(c, name, args, flags=(POLICY,)):
    for suffix, outcomes in [('plain', []), ('outcomes', ['--decision-outcomes'])]:
        target = f'{name}-{suffix}'
        base = [*outcomes, *args]
        ordinary = c.invoke(['--out', f'{target}-default', *base])
        strict = c.invoke([*flags, '--out', target, *base])
        assert strict.stdout == ordinary.stdout
        assert strict.stderr == ordinary.stderr
        files = sorted(p.name for p in (c.cwd / target).iterdir())
        assert len(files) == 7
        assert files == sorted(p.name for p in (c.cwd / f'{target}-default').iterdir())
        for file in files:
            assert (c.cwd / target / file).read_bytes() == (c.cwd / f'{target}-default' / file).read_bytes(), file
        audit = c.json(f'{target}/wallabag-merge-report.json')
        assert audit['basisSha256'] == c.template['basisSha256']
        assert c.json(f'{target}/wallabag-decisions.json') == c.template
        assert audit['roundTrip']['verified'] is True
        assert audit['summary']['unprocessedRecords'] == 0
        before = c.listdir()
        for out in [[], ['--out', f'{target}-dry']]:
            dry = c.invoke([*flags, '--dry-run', *out, *base])
            assert json.loads(dry.stdout) == {**json.loads(strict.stdout), 'dryRun': True}
        assert c.listdir() == before, 'successful dry-run writes nothing'
    assert (c.cwd / 'source.json').read_text(encoding='utf-8') == c.original_text


def malformed_rows(c):
    rows = json.loads(c.original_text)
    rows[4]['annotations'] = [{'text': 'invalid'}]
    return rows


def test_unselected_malformed_annotation_rejects_omitted_and_empty_decisions_before_publication(c):
    altered = c.source(malformed_rows(c), 'malformed-preview')
    audit = c.json('malformed-preview/wallabag-merge-report.json')
    assert audit['mapping'][4] == {'sourceRef': 's1:r5', 'status': 'unprocessed', 'outputPointer': None, 'reason': ANNOTATION_REASON}
    assert [r['id'] for r in c.json('malformed-preview/wallabag-export.json')] == [101, 102, 103, 104, 106]
    for args in [[], altered.selections('zero', [])]:
        rejects(altered, args, diagnostic('s1:r5', ANNOTATION_REASON), stdout_modes=([], ['--decision-outcomes']))
        # Earlier policy alone still permits partial native projection with no decisions.
        written = altered.invoke([APPLIED, '--out', 'applied-zero' if args else 'applied-omitted', *args])
        assert json.loads(written.stdout)['wallabag']['unprocessedRecords'] == 1
        before = c.listdir()
        result = altered.invoke([APPLIED, '--dry-run', *args])
        assert json.loads(result.stdout)['wallabag']['unprocessedRecords'] == 1
        assert c.listdir() == before


@pytest.mark.parametrize('approved', [False, True])
def test_false_and_blocked_selections_use_mapping_reason_with_deterministic_dual_policy_precedence(c, approved):
    altered = c.source(malformed_rows(c), 'selected-preview')
    args = altered.selections(f'selected-{approved}', [decision([4, 2], 4), decision([5, 1], 1, approved)])
    rejects(altered, args, diagnostic('s1:r5', ANNOTATION_REASON), policies=([POLICY],))
    reason = 'blocked/unrepresentable-annotation' if approved else 'not-approved/not-approved'
    rejects(altered, args, err(f'{APPLIED}: decision 1 {reason}'), policies=([POLICY, APPLIED], [APPLIED, POLICY]))
    result = altered.invoke(['--dry-run', '--decision-outcomes', *args])
    assert json.loads(result.stdout)['wallabagDecisionOutcomes'][1]['reason'] == ('unrepresentable-annotation' if approved else 'not-approved')


def test_reader_quarantine_and_later_source_positions_retain_first_source_reasons_without_renumbering(c):
    original = json.loads(c.original_text)
    rows = [{**original[5], 'id': 200 + i, 'url': f'https://example.com/{i}'} for i in range(12)]
    rows[10]['annotations'] = [{'text': 'invalid'}]
    rows[11]['tags'] = 'bad'
    later = c.source(rows, 'later-preview')
    rejects(later, ['--skip-invalid'], diagnostic('s1:r11', ANNOTATION_REASON))
    rows[1]['tags'] = 'bad'
    middle = c.source(rows, 'middle-preview')
    rejects(middle, ['--skip-invalid'], diagnostic('s1:r2', 'tags must be an array of strings'))
    rows[0]['annotations'] = [None]
    early = c.source(rows, 'early-preview')
    rejects(early, ['--skip-invalid'], diagnostic('s1:r1', 'annotations must be an array of objects'))
    # Strict reader validation wins even over an earlier native-only unprocessed row.
    rows[0]['annotations'] = [{'text': 'invalid'}]
    strict = c.source(rows, 'strict-preview')
    before = c.listdir()
    for mode in MODES:
        result = strict.invoke([POLICY, APPLIED, *mode], 1)
        assert result.stdout == ''
        assert result.stderr == err('source.json/1: tags must be an array of strings (strict mode; use --skip-invalid to quarantine)')
    assert c.listdir() == before
    rejects(strict, ['--skip-invalid'], diagnostic('s1:r1', ANNOTATION_REASON))


def test_all_rejected_inputs_fail_but_empty_dumps_pass_in_normal_and_dry_run_modes(c):
    original = json.loads(c.original_text)
    rejected = c.source([None, {**original[0], 'tags': 'bad'}], 'rejected-preview')
    rejects(rejected, ['--skip-invalid'], diagnostic('s1:r1', 'Record must be an object'))
    empty = c.source([], 'empty-preview')
    succeeds(empty, 'empty-omitted', [], (POLICY, APPLIED))
    succeeds(empty, 'empty-zero', empty.selections('empty-decisions', []), (POLICY, APPLIED))


def test_representable_omitted_empty_false_blocked_and_applied_selections_preserve_all_outputs(c):
    succeeds(c, 'omitted', [], (POLICY, APPLIED))
    succeeds(c, 'zero', c.selections('zero-decisions', []), (POLICY, APPLIED))
    succeeds(c, 'false', c.selections('false-decisions', [decision([5, 1], 5, False)]))
    succeeds(c, 'blocked', c.selections('blocked-decisions', MIXED))
    succeeds(c, 'later', c.selections('later-decisions', [decision([4, 2], 4), decision([3, 1], 3)]), (POLICY, APPLIED))
    assert [r['id'] for r in c.json('later-outcomes/wallabag-export.json')] == [103, 104, 105, 106]
    rows = json.loads(c.original_text)
    rows[4]['id'] = '101'
    ambiguous = c.source(rows, 'ambiguous-preview')
    succeeds(ambiguous, 'ambiguous', ambiguous.selections('ambiguous-decisions', MIXED))


def test_malformed_stale_and_late_invalid_decisions_take_precedence_over_unprocessed_rows(c):
    altered = c.source(malformed_rows(c), 'invalid-preview')
    malformed_decisions_scenario(altered.prefixed(POLICY, APPLIED))


def test_boolean_native_mode_preconditions_and_existing_path_validation_remain_fail_closed(c):
    c.source(malformed_rows(c), 'path-preview')
    precondition_failures(c, [*boolean_flag_cases(POLICY, unknown_negation=False), *PATH_CASES(POLICY)])
    common_cli_checks(c, POLICY)
