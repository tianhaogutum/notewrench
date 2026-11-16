# SPDX-License-Identifier: AGPL-3.0-only
# Port of linkdedupe tests/wallabag-subgroups.test.js.
import copy
import json

import pytest

from notewrench.linkdedupe import build_wallabag_export

from .helpers import js, read_fixture

ROWS = json.loads(read_fixture('wallabag-subgroups-v2.json'))


def source(records):
    return [{'name': 'subgroups.json', 'text': js(records), 'format': 'wallabag-v2'}]


def decision(refs, keep=None, approved=True):
    return {'sourceRefs': [f's1:r{n}' for n in refs], 'keepSourceRef': f's1:r{(refs[0] if refs else None) if keep is None else keep}',
            'approved': approved}


def export_with(records, selections, **options):
    inputs = source(records)
    template = build_wallabag_export(inputs, **options)['wallabagDecisions']
    return build_wallabag_export(inputs, **options, decisions={**template, 'decisions': selections})


def test_a_three_member_candidate_permits_a_pair_approval_and_preserves_the_unselected_member():
    records = ROWS[:3]
    result = export_with(records, [decision([3, 1], 3)])
    assert result['wallabagExport'] == [
        {**records[2], 'tags': ['tag-1', 'shared', 'tag-3'], 'annotations': [records[0]['annotations'][0], records[2]['annotations'][0]]},
        records[1],
    ]
    assert [[m['sourceRef'], m['status'], m['outputPointer'], m['reason']] for m in result['wallabagMergeReport']['mapping']] == [
        ['s1:r1', 'merged', '/0', 'explicitly-approved'], ['s1:r2', 'retained', '/1', 'not-approved'],
        ['s1:r3', 'retained', '/0', 'explicitly-approved']]
    assert result['wallabagMergeReport']['summary']['approvedGroups'] == 1
    assert result['wallabagMergeReport']['roundTrip']['verified'] is True


def test_disjoint_subgroups_use_source_order_and_isolate_tags_annotations_conflicts_and_unselected_rows():
    rows = copy.deepcopy(ROWS)
    selections = [decision([3, 1], 3), decision([4, 2], 2)]
    before = copy.deepcopy({'rows': rows, 'selections': selections})
    result = export_with(rows, selections)
    assert result['wallabagExport'] == [
        {**rows[2], 'tags': ['tag-1', 'shared', 'tag-3'], 'annotations': [rows[0]['annotations'][0], rows[2]['annotations'][0]]},
        {**rows[1], 'tags': ['tag-2', 'shared', 'tag-4'], 'annotations': [rows[1]['annotations'][0], rows[3]['annotations'][0]]},
        rows[4], rows[5],
    ]
    audit = result['wallabagMergeReport']
    assert [[m['status'], m['keptSourceRef'], m['outputPointer']] for m in audit['mapping']] == [
        ['merged', 's1:r3', '/0'], ['retained', 's1:r2', '/1'], ['retained', 's1:r3', '/0'],
        ['merged', 's1:r2', '/1'], ['retained', 's1:r5', '/2'], ['retained', 's1:r6', '/3']]
    assert audit['mapping'][4]['reason'] == 'not-approved'
    assert audit['mapping'][5]['reason'] == 'standalone'
    for conflict in audit['conflicts']:
        expected = ['s1:r1', 's1:r3'] if conflict['keptSourceRef'] == 's1:r3' else ['s1:r2', 's1:r4']
        assert [v['sourceRef'] for v in conflict['values']] == expected
    assert [r['raw'] for r in result['provenance']['records']] == rows
    assert result['provenance']['sources'][0]['originalText'] == source(rows)[0]['text']
    assert audit['summary']['approvedGroups'] == 2
    assert audit['summary']['blockedGroups'] == 0
    assert audit['summary']['mergedRecords'] == 2
    assert audit['roundTrip']['verified'] is True
    assert len(result['wallabagDecisions']['decisions']) == 1
    assert result['wallabagDecisions']['decisions'][0] == decision([1, 2, 3, 4, 5], 1, False)
    assert result['clusters'] == build_wallabag_export(source(rows))['clusters']
    reordered = export_with(rows, [decision([2, 4], 2), decision([1, 3], 3)])
    assert reordered['wallabagExport'] == result['wallabagExport']
    assert reordered['wallabagMergeReport']['mapping'] == audit['mapping']
    assert reordered['wallabagMergeReport']['conflicts'] == audit['conflicts']
    assert {'rows': rows, 'selections': selections} == before


def test_false_and_omitted_subgroup_approvals_retain_members_without_sharing_merged_fields():
    result = export_with(ROWS, [decision([1, 3], 3), decision([2, 4], 2, False)])
    assert [r['id'] for r in result['wallabagExport']] == [103, 102, 104, 105, 106]
    assert result['wallabagExport'][1] == ROWS[1]
    assert result['wallabagExport'][2] == ROWS[3]
    assert result['wallabagMergeReport']['summary']['approvedGroups'] == 1
    assert export_with(ROWS, [decision([1, 3], 3, False)])['wallabagExport'] == ROWS


def test_subgroup_guards_block_only_selected_members_while_native_identity_ambiguity_remains_source_wide():
    result = export_with(ROWS, [decision([1, 5]), decision([2, 4])])
    assert [r['id'] for r in result['wallabagExport']] == [101, 102, 103, 105, 106]
    audit = result['wallabagMergeReport']
    assert audit['summary']['approvedGroups'] == 2
    assert audit['summary']['blockedGroups'] == 1
    assert audit['mapping'][0]['reason'] == 'annotation-content-mismatch'
    assert audit['mapping'][2]['reason'] == 'not-approved'
    assert audit['mapping'][3]['status'] == 'merged'
    malformed = copy.deepcopy(ROWS)
    malformed[4]['annotations'] = [{'text': 'missing native fields'}]
    unselected = export_with(malformed, [decision([1, 3])])
    assert unselected['wallabagMergeReport']['summary']['blockedGroups'] == 0
    assert unselected['wallabagMergeReport']['summary']['mergedRecords'] == 1
    assert unselected['wallabagMergeReport']['mapping'][4]['status'] == 'unprocessed'
    selected = export_with(malformed, [decision([1, 5]), decision([2, 4])])
    assert selected['wallabagMergeReport']['summary']['blockedGroups'] == 1
    assert selected['wallabagMergeReport']['mapping'][0]['reason'] == 'unrepresentable-annotation'
    assert selected['wallabagMergeReport']['mapping'][3]['status'] == 'merged'
    ambiguous = copy.deepcopy(ROWS)
    ambiguous[4]['id'] = '101'
    blocked = export_with(ambiguous, [decision([1, 3]), decision([2, 4])])
    assert blocked['wallabagMergeReport']['summary']['blockedGroups'] == 1
    assert blocked['wallabagMergeReport']['mapping'][0]['reason'] == 'ambiguous-native-identity'
    assert blocked['wallabagMergeReport']['mapping'][3]['status'] == 'merged'


INVALID = [
    [decision([])], [decision([1])], [decision([1, 1])], [decision([1, 99])],
    [decision([1, 6])], [decision([1, 3], 2)],
    [decision([1, 3]), decision([3, 5])],
    [decision([1, 3]), decision([3, 1])],
    [decision([1, 3], 1, False), decision([3, 5])],
    [decision([1, 3]), decision([3, 5], 3, False)],
    [decision([1, 3], 1, False), decision([3, 5], 3, False)],
    [decision([1, 2, 3, 4, 5], 1, False), decision([1, 3])],
]


@pytest.mark.parametrize('skip_invalid', [False, True])
@pytest.mark.parametrize('selections', INVALID, ids=js)
def test_overlap_repeated_singleton_foreign_cross_candidate_and_nonmember_keeper_decisions_fail_closed(selections, skip_invalid):
    with pytest.raises(Exception, match='(?i)decision'):
        export_with(ROWS, selections, skip_invalid=skip_invalid)


def test_decisions_naming_a_rejected_row_fail_closed():
    rejected = copy.deepcopy(ROWS)
    rejected[4]['url'] = 'invalid'
    with pytest.raises(Exception, match='(?i)decision'):
        export_with(rejected, [decision([1, 5])], skip_invalid=True)
