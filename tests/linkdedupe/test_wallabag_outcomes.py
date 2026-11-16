# SPDX-License-Identifier: AGPL-3.0-only
# Port of linkdedupe tests/wallabag-outcomes.test.js.
import copy
import json

import pytest

from notewrench.linkdedupe import build_wallabag_export

from .helpers import js, read_fixture

ROWS = json.loads(read_fixture('wallabag-subgroups-v2.json'))


def source(records):
    return [{'name': 'outcomes.json', 'text': js(records), 'format': 'wallabag-v2'}]


def decision(refs, keeper=None, approved=True):
    return {'sourceRefs': [f's1:r{n}' for n in refs], 'keepSourceRef': f's1:r{refs[0] if keeper is None else keeper}',
            'approved': approved}


def outcome(index, refs, keeper, status, reason, pointer=None):
    return {'decisionIndex': index, 'sourceRefs': [f's1:r{n}' for n in refs], 'keepSourceRef': f's1:r{keeper}',
            'status': status, 'reason': reason, 'outputPointer': pointer}


def run(records, selections, **options):
    inputs = source(records)
    template = build_wallabag_export(inputs, **options)['wallabagDecisions']
    return build_wallabag_export(inputs, **options, decisions={**template, 'decisions': selections})


def test_audit_links_blocked_and_applied_subgroup_outcomes_to_input_decisions_without_inventing_omitted_outcomes():
    rows = copy.deepcopy(ROWS)
    selections = [decision([5, 1], 1), decision([4, 2], 2)]
    before = copy.deepcopy({'rows': rows, 'selections': selections})
    result = run(rows, selections)
    audit = result['wallabagMergeReport']
    assert audit['decisionOutcomes'] == [
        outcome(0, [1, 5], 1, 'blocked', 'annotation-content-mismatch'),
        outcome(1, [2, 4], 2, 'applied', 'explicitly-approved', '/1'),
    ]
    assert result['wallabagExport'] == [rows[0], {
        **rows[1], 'tags': ['tag-2', 'shared', 'tag-4'], 'annotations': [*rows[1]['annotations'], *rows[3]['annotations']],
    }, rows[2], rows[4], rows[5]]
    assert [[m['sourceRef'], m['status'], m['outputPointer'], m['reason']] for m in audit['mapping']] == [
        ['s1:r1', 'retained', '/0', 'annotation-content-mismatch'],
        ['s1:r2', 'retained', '/1', 'explicitly-approved'],
        ['s1:r3', 'retained', '/2', 'not-approved'],
        ['s1:r4', 'merged', '/1', 'explicitly-approved'],
        ['s1:r5', 'retained', '/3', 'annotation-content-mismatch'],
        ['s1:r6', 'retained', '/4', 'standalone'],
    ]
    assert audit['appliedDecisions']['decisions'] == selections
    assert audit['basisSha256'] == result['wallabagDecisions']['basisSha256']
    assert result['wallabagDecisions'] == build_wallabag_export(source(rows))['wallabagDecisions']
    assert audit['summary']['approvedGroups'] == 2
    assert audit['summary']['blockedGroups'] == 1
    assert {'rows': rows, 'selections': selections} == before


def test_outcomes_retain_decision_order_and_source_membership_order_for_multiple_applied_groups_and_later_keepers():
    first = run(ROWS, [decision([4, 2], 4), decision([3, 1], 3)])
    assert first['wallabagMergeReport']['decisionOutcomes'] == [
        outcome(0, [2, 4], 4, 'applied', 'explicitly-approved', '/1'),
        outcome(1, [1, 3], 3, 'applied', 'explicitly-approved', '/0'),
    ]
    assert [r['id'] for r in first['wallabagExport']] == [103, 104, 105, 106]
    reordered = run(ROWS, [decision([1, 3], 3), decision([2, 4], 4)])
    assert reordered['wallabagMergeReport']['decisionOutcomes'] == [
        outcome(0, [1, 3], 3, 'applied', 'explicitly-approved', '/0'),
        outcome(1, [2, 4], 4, 'applied', 'explicitly-approved', '/1'),
    ]
    assert reordered['wallabagExport'] == first['wallabagExport']
    assert reordered['wallabagMergeReport']['mapping'] == first['wallabagMergeReport']['mapping']


def test_false_decisions_have_explicit_not_approved_outcomes_and_do_not_run_guards():
    result = run(ROWS, [decision([5, 1], 5, False), decision([4, 2], 4)])
    assert result['wallabagMergeReport']['decisionOutcomes'] == [
        outcome(0, [1, 5], 5, 'not-approved', 'not-approved'),
        outcome(1, [2, 4], 4, 'applied', 'explicitly-approved', '/1'),
    ]
    assert result['wallabagMergeReport']['summary']['approvedGroups'] == 1
    assert result['wallabagMergeReport']['summary']['blockedGroups'] == 0
    assert result['wallabagMergeReport']['mapping'][0]['reason'] == 'not-approved'
    template = build_wallabag_export(source(ROWS))['wallabagDecisions']
    unapproved = run(ROWS, template['decisions'])
    assert unapproved['wallabagMergeReport']['decisionOutcomes'] == [outcome(0, [1, 2, 3, 4, 5], 1, 'not-approved', 'not-approved')]
    assert unapproved['wallabagExport'] == ROWS


def _invalid_annotation(records):
    records[4]['annotations'] = [{'text': 'invalid'}]


def _ambiguous_and_invalid(records):
    records[4]['id'] = '101'
    records[4]['annotations'] = [{'text': 'invalid'}]


@pytest.mark.parametrize('mutate,reason', [(_invalid_annotation, 'unrepresentable-annotation'),
                                           (_ambiguous_and_invalid, 'ambiguous-native-identity')])
def test_outcomes_expose_each_existing_guard_and_null_pointers_including_unprocessed_members_and_guard_precedence(mutate, reason):
    records = copy.deepcopy(ROWS)
    mutate(records)
    result = run(records, [decision([5, 1], 1), decision([4, 2], 4)])
    assert result['wallabagMergeReport']['decisionOutcomes'] == [
        outcome(0, [1, 5], 1, 'blocked', reason),
        outcome(1, [2, 4], 4, 'applied', 'explicitly-approved', '/1'),
    ]
    assert result['wallabagMergeReport']['mapping'][4]['status'] == 'unprocessed'
    assert result['wallabagMergeReport']['mapping'][4]['outputPointer'] is None


def test_missing_empty_decisions_and_empty_dumps_have_no_invented_outcomes():
    for records in [ROWS, []]:
        preview = build_wallabag_export(source(records))
        assert preview['wallabagMergeReport']['decisionOutcomes'] == []
        assert run(records, [])['wallabagMergeReport']['decisionOutcomes'] == []
        assert run(records, [])['wallabagExport'] == records


@pytest.mark.parametrize('approved', [True, False])
@pytest.mark.parametrize('skip_invalid', [True, False])
def test_a_late_invalid_cross_candidate_decision_aborts_the_entire_result(approved, skip_invalid):
    with pytest.raises(Exception, match='(?i)decision.*one current candidate'):
        run(ROWS, [decision([5, 1], 1), decision([4, 2], 2), decision([3, 6], 3, approved)], skip_invalid=skip_invalid)
