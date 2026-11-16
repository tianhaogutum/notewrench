# SPDX-License-Identifier: AGPL-3.0-only
# Port of linkdedupe tests/decision-outcomes-cli.test.js (scenarios from
# scripts/decision-outcomes-contract.mjs).
import json
import re

import pytest

from .contract import MIXED, MIXED_OUTCOMES, OUTCOMES, Context, decision, malformed_decisions_scenario, outcome, write


@pytest.fixture
def c(cwd):
    return Context(cwd)


def test_mixed_outcomes_preserve_audit_native_default_stdout_and_zero_write_dry_run(c):
    result = c.execute('mixed', MIXED, MIXED_OUTCOMES)
    rows = json.loads(c.original_text)
    assert result['output'] == [rows[0], {**rows[1], 'tags': ['tag-2', 'shared', 'tag-4'],
                                          'annotations': [*rows[1]['annotations'], *rows[3]['annotations']]},
                                rows[2], rows[4], rows[5]]
    assert [m['outputPointer'] for m in result['audit']['mapping']] == ['/0', '/1', '/2', '/1', '/3', '/4']
    assert result['summary']['wallabag']['approvedGroups'] == 2
    assert result['summary']['wallabag']['blockedGroups'] == 1
    assert result['summary']['wallabag']['outputRecords'] == 5
    assert c.json('mixed/provenance.json')['sources'][0]['originalText'] == c.original_text
    readback = c.run(['--dry-run', '--format', 'wallabag-v2', 'mixed/wallabag-export.json'])
    assert json.loads(readback.stdout)['acceptedRecords'] == 5
    assert OUTCOMES not in json.loads(readback.stdout)


def test_false_omitted_zero_and_template_decisions_never_invent_outcome_rows(c):
    c.execute('false', [decision([5, 1], 5, False), decision([4, 2], 4)], [
        outcome(0, [1, 5], 5, 'not-approved', 'not-approved'),
        outcome(1, [2, 4], 4, 'applied', 'explicitly-approved', '/1'),
    ])
    rows = json.loads(c.original_text)
    for name, selections, expected in [
        ('omitted', None, []), ('zero', [], []),
        ('template', c.template['decisions'], [outcome(0, [1, 2, 3, 4, 5], 1, 'not-approved', 'not-approved')]),
    ]:
        assert c.execute(name, selections, expected)['output'] == rows
    write(c.cwd / 'empty.json', '[]')
    for args in [['--out', 'empty'], ['--dry-run', '--out', 'empty-dry']]:
        result = c.run(['--wallabag-export', '--decision-outcomes', '--format', 'wallabag-v2', *args, 'empty.json'])
        assert json.loads(result.stdout)[OUTCOMES] == []
    assert 'empty-dry' not in c.listdir()


def test_input_decision_indices_and_source_order_survive_later_keepers_and_quarantine(c):
    expected = [
        outcome(0, [2, 4], 4, 'applied', 'explicitly-approved', '/1'),
        outcome(1, [1, 3], 3, 'applied', 'explicitly-approved', '/0'),
    ]
    output = c.execute('later', [decision([4, 2], 4), decision([3, 1], 3)], expected)['output']
    assert [r['id'] for r in output] == [103, 104, 105, 106]
    # A reader-rejected prefix keeps original source positions but has no output position.
    rows = json.loads(c.original_text)
    write(c.cwd / 'shifted.json', [{**rows[0], 'id': 999, 'url': 'javascript:bad'}, *rows])
    base = ['--wallabag-export', '--skip-invalid', '--format', 'wallabag-v2']
    c.run([*base, '--out', 'shift-preview', 'shifted.json'])
    template = c.json('shift-preview/wallabag-decisions.json')
    write(c.cwd / 'shift-decisions.json', {**template, 'decisions': [decision([5, 3], 5), decision([4, 2], 4)]})
    args = [*base, '--decision-outcomes', '--decisions', 'shift-decisions.json']
    written = c.run([*args, '--out', 'shifted', 'shifted.json'])
    outcomes = [outcome(0, [3, 5], 5, 'applied', 'explicitly-approved', '/1'),
                outcome(1, [2, 4], 4, 'applied', 'explicitly-approved', '/0')]
    assert json.loads(written.stdout)[OUTCOMES] == outcomes
    assert c.json('shifted/wallabag-merge-report.json')['decisionOutcomes'] == outcomes
    before = c.listdir()
    dry = c.run([*args, '--dry-run', '--out', 'shift-dry', 'shifted.json'])
    assert json.loads(dry.stdout)[OUTCOMES] == outcomes
    assert c.listdir() == before


def test_flag_precondition_and_output_failures_leave_stdout_empty(c):
    cases = [
        (['--decision-outcomes'], '--decision-outcomes requires --wallabag-export'),
        (['--decision-outcomes=true', '--wallabag-export'], 'does not take an argument'),
        (['--decision-outcomes=false', '--wallabag-export'], 'does not take an argument'),
        (['--decision-outcomes', '--wallabag-export', '--out', 'preview'], 'already exists'),
        (['--decision-outcomes', '--wallabag-export', '--out', 'missing/child'], 'ENOENT'),
    ]
    for args, error in cases:
        before = c.listdir()
        failed = c.run(['--dry-run', *args, 'source.json'], 1)
        assert failed.stdout == ''
        assert error in failed.stderr
        assert c.listdir() == before
    assert re.search('--decision-outcomes.*Requires --wallabag-export', c.run(['--help']).stdout, re.S)


def test_malformed_stale_and_late_cross_candidate_decisions_fail_before_outcomes_or_files(c):
    malformed_decisions_scenario(c)
