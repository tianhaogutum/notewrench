# SPDX-License-Identifier: AGPL-3.0-only
# Port of linkdedupe tests/wallabag-export.test.js.
import copy
import json

import pytest

from notewrench.linkdedupe import build_report, build_wallabag_export

from .helpers import js, read_fixture


def source():
    return {'name': 'synthetic.json', 'text': read_fixture('wallabag-merge-v2.json'), 'format': 'wallabag-v2'}


def approve(result):
    template = result['wallabagDecisions']
    return {**template, 'decisions': [{**d, 'approved': True} for d in template['decisions']]}


def test_approved_native_export_preserves_tags_every_annotation_and_reports_scalar_extension_losses():
    item = source()
    preview = build_wallabag_export([item])
    assert len(preview['wallabagExport']) == 3, 'candidates are never implicit approval'
    result = build_wallabag_export([item], decisions=approve(preview))
    assert result['wallabagExport'] == json.loads(read_fixture('wallabag-merge-expected-v2.json'))
    assert len(result['wallabagExport']) == 2
    keeper = result['wallabagExport'][0]
    assert keeper['id'] == 10
    assert keeper['url'] == 'https://example.org/a?utm_source=mail'
    assert keeper['title'] == 'Keeper'
    assert keeper['tags'] == ['研究', 'offline']
    assert keeper['annotations'] == [
        {'text': '', 'quote': 'synthetic', 'ranges': [{'start': '/p[1]', 'end': '/p[1]', 'startOffset': 0, 'endOffset': 9}]},
        {'text': '', 'quote': 'synthetic', 'ranges': [{'start': '/p[1]', 'end': '/p[1]', 'startOffset': 0, 'endOffset': 9}]},
    ]
    audit = result['wallabagMergeReport']
    assert [[m['sourceRef'], m['status'], m['outputPointer']] for m in audit['mapping']] == [
        ['s1:r1', 'retained', '/0'], ['s1:r2', 'merged', '/0'], ['s1:r3', 'retained', '/1']]
    assert any(c['field'] == 'title' and c['keptSourceRef'] == 's1:r1' and c['values'][1]['value'] == 'Other title'
               for c in audit['conflicts'])
    assert any(c['field'] == 'id' for c in audit['conflicts'])
    assert any(f['sourceRef'] == 's1:r2' and f['pointer'] == '/note' and f['value'] == 'extension note'
               for f in audit['unrepresentedFields'])
    assert any(f['pointer'] == '/annotations/0/id' and f['value'] == 101 for f in audit['unrepresentedFields'])
    assert any(f['pointer'] == '/custom' and f['value']['keep'] is True for f in audit['unrepresentedFields'])
    assert 'note' not in keeper
    reread = build_report([{'name': 'new-export', 'format': 'wallabag-v2', 'text': js(result['wallabagExport'])}])
    assert [r['raw'] for r in reread['provenance']['records']] == result['wallabagExport']
    assert sum(len(r['notes']) for r in reread['records']['records']) == 2
    assert result == build_wallabag_export([item], decisions=approve(preview))
    assert item['text'] == source()['text']


def test_decisions_bind_exact_source_bytes_and_url_policy_malformed_stale_singleton_approvals_fail_closed():
    item = source()
    decisions = approve(build_wallabag_export([item]))
    first = decisions['decisions'][0]
    for bad in [
        {**decisions, 'adapter': 'unversioned'},
        {**decisions, 'basisSha256': '0' * 64},
        {**decisions, 'extra': True},
        {**decisions, 'decisions': [*decisions['decisions'], *decisions['decisions']]},
        {**decisions, 'decisions': [{**first, 'approved': 'true'}]},
        {**decisions, 'decisions': [{**first, 'sourceRefs': ['s1:r1']}]},
        {**decisions, 'decisions': [{**first, 'keepSourceRef': 's1:r3'}]},
    ]:
        with pytest.raises(Exception, match='(?i)decision'):
            build_wallabag_export([item], decisions=bad)
    with pytest.raises(Exception, match='(?i)decision.*basis'):
        build_wallabag_export([{**item, 'text': item['text'] + '\n'}], decisions=decisions)
    with pytest.raises(Exception, match='(?i)decision.*basis'):
        build_wallabag_export([item], decisions=decisions, normalization={'keepTracking': True})
    with pytest.raises(Exception, match='(?i)one Wallabag'):
        build_wallabag_export([item, item])
    with pytest.raises(Exception, match='(?i)Wallabag'):
        build_wallabag_export([{'text': '{"bookmarks":[]}'}])
    none = build_wallabag_export([item], decisions={**decisions, 'decisions': []})
    assert len(none['wallabagExport']) == 3


def _set_id(rows):
    rows[1]['id'] = '10'


def _same_uid(rows):
    rows[0]['uid'] = rows[2]['uid'] = 'same-uid'


def _different_content(rows):
    rows[1]['content'] = '<p>different</p>'


def _no_content(rows):
    del rows[1]['content']


@pytest.mark.parametrize('mutate', [_set_id, _same_uid, _different_content, _no_content])
def test_ambiguous_native_identities_and_mismatched_annotation_content_leave_approved_groups_unmerged(mutate):
    item = source()
    rows = json.loads(item['text'])
    mutate(rows)
    changed = {**item, 'text': js(rows)}
    result = build_wallabag_export([changed], decisions=approve(build_wallabag_export([changed])))
    assert len(result['wallabagExport']) == 3
    assert result['wallabagMergeReport']['summary']['mergedRecords'] == 0
    assert any(word in result['wallabagMergeReport']['mapping'][0]['reason'] for word in ('ambiguous', 'annotation-content'))


def test_every_rejected_row_has_an_explicit_unprocessed_mapping_and_exact_original_provenance():
    item = source()
    rows = json.loads(item['text'])
    rows += [{**rows[0], 'url': 'javascript:bad'}, None]
    changed = {**item, 'text': js(rows)}
    with pytest.raises(Exception, match='strict mode'):
        build_wallabag_export([changed])
    result = build_wallabag_export([changed], skip_invalid=True)
    assert len(result['wallabagMergeReport']['mapping']) == 5
    assert [[m['status'], m['outputPointer']] for m in result['wallabagMergeReport']['mapping'][3:]] == [
        ['unprocessed', None], ['unprocessed', None]]
    assert result['provenance']['sources'][0]['originalText'] == changed['text']
    assert result['wallabagMergeReport']['summary']['unprocessedRecords'] == 2


def test_a_later_keeper_keeps_scalar_absence_order_is_deterministic_and_missing_ids_use_bound_positions():
    item = source()
    rows = json.loads(item['text'])
    del rows[0]['id']
    del rows[1]['id']
    del rows[1]['created_at']
    changed = {**item, 'text': js(rows)}
    decisions = approve(build_wallabag_export([changed]))
    decisions['decisions'][0]['keepSourceRef'] = 's1:r2'
    result = build_wallabag_export([changed], decisions=decisions)
    assert result['wallabagExport'][0]['title'] == 'Other title'
    assert 'created_at' not in result['wallabagExport'][0]
    assert result['wallabagMergeReport']['mapping'][0]['status'] == 'merged'
    assert result['wallabagMergeReport']['mapping'][1]['status'] == 'retained'
    conflict = next(c for c in result['wallabagMergeReport']['conflicts'] if c['field'] == 'created_at')
    assert conflict['values'][1] == {'sourceRef': 's1:r2', 'present': False}
    assert len(result['wallabagExport'][0]['annotations']) == 2


def test_malformed_native_annotations_are_explicitly_unprocessed_approved_companions_stay_separate():
    item = source()
    rows = json.loads(item['text'])
    rows[1]['annotations'] = [{'text': 'has no quote or ranges'}]
    changed = {**item, 'text': js(rows)}
    result = build_wallabag_export([changed], decisions=approve(build_wallabag_export([changed])))
    audit = result['wallabagMergeReport']
    assert audit['summary']['blockedGroups'] == 1
    assert len(result['wallabagExport']) == 2
    assert audit['mapping'][0]['reason'] == 'unrepresentable-annotation'
    assert audit['mapping'][1]['status'] == 'unprocessed'
    assert audit['mapping'][1]['outputPointer'] is None
    assert any(f['pointer'] == '/annotations' and f['value'][0]['text'] == rows[1]['annotations'][0]['text']
               for f in audit['unrepresentedFields'])
    assert result['provenance']['records'][1]['raw'] == rows[1]


def test_empty_native_dump_and_omitted_decisions_produce_a_consistent_empty_export():
    result = build_wallabag_export([{'text': '[]', 'format': 'wallabag-v2'}])
    assert result['wallabagExport'] == []
    assert result['wallabagDecisions']['decisions'] == []
    assert result['wallabagMergeReport']['mapping'] == []
    assert result['wallabagMergeReport']['roundTrip']['verified'] is True


def test_negative_zero_in_a_native_field_fails_the_round_trip_like_node():
    # util.isDeepStrictEqual(-0, 0) is false, so Node reports a mismatch here.
    text = '[{"url":"https://a.example/x","title":null,"tags":[],"reading_time":-0}]'
    with pytest.raises(Exception, match='round-trip mismatch'):
        build_wallabag_export([{'text': text, 'format': 'wallabag-v2'}])
    assert copy.deepcopy(build_report([{'text': text, 'format': 'wallabag-v2'}]))['records']['summary']['acceptedRecords'] == 1
