# SPDX-License-Identifier: AGPL-3.0-only
# Port of linkdedupe tests/report.test.js.
import hashlib
import json

import pytest

from notewrench.linkdedupe import build_report, detect_format, parse_json

from .benchmark import run_benchmark
from .helpers import js, read_fixture


def wb(url, **extras):
    return {'id': 1, 'url': url, 'title': 'same title', 'tags': [], 'created_at': '2026-11-11T09:00:00+0100',
            'is_archived': False, **extras}


def source(document, format=None, name='synthetic'):
    return {'name': name, 'text': js(document), 'format': format}


def test_native_format_cross_service_e2e_retains_provenance_and_records_all_unmerged_fields():
    sources = [{'name': name, 'text': read_fixture(name)} for name in ['karakeep.json', 'wallabag-v2.json']]
    result = build_report(sources)
    assert result['records']['summary']['acceptedRecords'] == 4
    assert result['records']['summary']['nonUrlRecords'] == 1
    assert len(result['clusters']['clusters']) == 1
    cluster = result['clusters']['clusters'][0]
    assert cluster['canonicalUrl'] == 'https://example.org/a'
    assert cluster['members'][0]['nativeId'] is None  # native Karakeep has no bookmark ID
    assert cluster['members'][1]['nativeId'] == 42
    assert cluster['tags'] == ['研究', 'offline', '阅读']
    assert cluster['notes'][0]['value'] == '保留这条手写笔记。'
    assert cluster['notes'][1]['value']['ranges'][0]['startOffset'] == 9
    assert cluster['notes'][1]['sourceRef'] == 's2:r1'
    assert cluster['titleConflict'] is True
    assert any(item['field'] == 'content' and item['conflict'] for item in cluster['unmergedFields'])
    assert any(item['field'] == 'is_archived' for item in cluster['unmergedFields'])
    assert result['records']['sources'][0]['rootMetadata']['lists'][0]['name'] == '阅读'
    assert result['provenance']['sources'][0]['originalText'] == sources[0]['text']
    assert result['provenance']['records'][0]['raw'] == json.loads(sources[0]['text'])['bookmarks'][0]
    assert result['mergePlan']['executable'] is False
    assert len(result['mergePlan']['candidates'][0]['sourceRefs']) == 2
    assert result == build_report(sources)  # no timestamps/random IDs in reports


def test_same_title_with_different_url_never_clusters_duplicates_retain_every_annotation():
    document = [wb('https://example.org/a', annotations=[{'text': '', 'quote': 'q', 'ranges': []}], tags=['A']),
                wb('https://example.org/b'),
                wb('https://example.org/a', annotations=[{'text': '', 'quote': 'q', 'ranges': []}], tags=['a', 'A'],
                   mystery={'keep': [1, 2]})]
    result = build_report([source(document)])
    assert len(result['records']['records']) == 2
    assert len(result['clusters']['clusters']) == 1
    assert len(result['clusters']['clusters'][0]['notes']) == 2
    assert result['clusters']['clusters'][0]['tags'] == ['A', 'a']
    mystery = next(item for item in result['clusters']['clusters'][0]['unmergedFields'] if item['field'] == 'mystery')
    assert mystery['values'][0]['value'] == {'keep': [1, 2]}


def test_strict_invalid_records_fail_skip_quarantines_each_raw_invalid_value_with_exact_counts():
    document = [wb('https://example.org/a'), wb('ftp://example.org/a'), wb('broken'),
                wb('https://example.org/b', tags=[{'label': 'not-native'}]), None]
    with pytest.raises(Exception, match=r'/1.*Unsupported protocol.*strict mode'):
        build_report([source(document)])
    result = build_report([source(document)], skip_invalid=True)
    assert result['records']['summary']['totalRecords'] == 5
    assert result['records']['summary']['skippedRecords'] == 4
    assert result['records']['summary']['acceptedRecords'] == 1
    assert len(result['clusters']['rejected']) == 4
    assert len(result['provenance']['records']) == 5
    assert result['provenance']['records'][3]['raw'] == document[3]
    assert result['provenance']['records'][4]['raw'] is None


def test_unknown_shapes_cannot_be_bypassed_by_skip_ambiguous_empty_arrays_need_explicit_format():
    for document in [{'items': []}, {'_embedded': {'items': []}}, [], [{'url': 'https://example.org'}]]:
        with pytest.raises(Exception, match='Unknown or ambiguous'):
            build_report([source(document)], skip_invalid=True)
    assert detect_format([], 'wallabag-v2') == 'wallabag-v2'
    assert build_report([source([], 'wallabag-v2')])['records']['summary']['totalRecords'] == 0
    assert build_report([source({'bookmarks': []})])['records']['summary']['totalRecords'] == 0
    with pytest.raises(Exception, match='Karakeep native'):
        detect_format([], 'karakeep')
    with pytest.raises(Exception, match='Wallabag v2'):
        detect_format({}, 'wallabag-v2')
    with pytest.raises(Exception, match='Unsupported format'):
        detect_format([], 'pretend')


def test_karakeep_text_and_null_records_stay_separate_even_with_equal_title_and_text():
    base = {'createdAt': 10, 'title': 'text', 'note': '', 'tags': [], 'content': {'type': 'text', 'text': 'original synthetic text'}}
    result = build_report([source({'bookmarks': [base, base, {**base, 'content': None}], 'extension': {'keep': True}})])
    assert result['records']['summary']['nonUrlRecords'] == 3
    assert len(result['records']['records']) == 3
    assert len(result['clusters']['clusters']) == 0
    assert result['records']['records'][0]['notes'][0]['value'] == ''
    assert result['records']['sources'][0]['rootMetadata']['extension'] == {'keep': True}
    with pytest.raises(Exception, match='Unsupported Karakeep content'):
        build_report([source({'bookmarks': [{**base, 'content': {'type': 'asset'}}]})])


def test_malformed_adapter_fields_fail_rather_than_disappear():
    for bad in [{'tags': None}, {'title': 10}, {'id': 1.5}, {'is_archived': 2}, {'content': 12}, {'annotations': ['note']},
                {'note': []}]:
        with pytest.raises(Exception):
            build_report([source([wb('https://example.org/a', **bad)])])
    base = {'createdAt': 1, 'title': None, 'tags': [], 'note': None, 'content': None}
    for bad in [{'createdAt': '1'}, {'note': 1}, {'archived': 1}, {'lists': [1]}, {'content': {}}]:
        with pytest.raises(Exception):
            build_report([source({'bookmarks': [{**base, **bad}]})])
    with pytest.raises(Exception, match='root lists'):
        build_report([source({'bookmarks': [], 'lists': {}})])


@pytest.mark.parametrize('text', ['{"a":1,"a":2}', '{"a":1,"\\u0061":2}', '{"id":9007199254740993}', '{"id":1e400}', '[1,]',
                                  '{"a":1,}', '{"a":01}', 'true false', '"bad\nstring"', '"\\x"', ''])
def test_strict_json_rejects_duplicate_keys_unsafe_integers_and_broken_syntax(text):
    with pytest.raises(Exception):
        parse_json(text)


def test_strict_json_nesting_proto_keys_and_bom():
    with pytest.raises(Exception, match='nesting limit'):
        parse_json('[' * 258 + ']' * 258)
    text = '{"__proto__":{"x":1},"a":[true,false,null,-2.5e-2,"escaped\\"string"]}'
    parsed = parse_json(text)
    assert parsed == json.loads(text)
    assert '__proto__' in parsed
    assert parse_json('﻿[1,2]') == [1, 2]


def test_benchmark_improves_labeled_recall_without_false_merges():
    result = run_benchmark()
    assert result['records'] == 12000
    assert result['baseline']['truePositives'] == 500
    assert result['linkdedupe']['truePositives'] == 3500
    assert result['linkdedupe']['falseMerges'] == 0
    assert result['linkdedupe']['falseNegatives'] == 0


def test_unknown_karakeep_extensions_are_explicitly_unmerged_even_with_wallabag_like_field_names():
    original = {'createdAt': 1, 'title': None, 'tags': [], 'note': None, 'content': None, 'url': 'extension-only',
                'annotations': [{'extension': 'not native annotations'}]}
    result = build_report([source({'bookmarks': [original]})])
    fields = result['records']['records'][0]['unmergedFields']
    assert next(field for field in fields if field['field'] == 'url')['values'][0]['value'] == 'extension-only'
    assert next(field for field in fields if field['field'] == 'annotations')['values'][0]['value'] == original['annotations']
    assert result['records']['records'][0]['canonicalUrl'] is None
    assert len(result['records']['records'][0]['notes']) == 0


def test_original_utf8_text_bom_whitespace_and_numeric_spelling_has_reproducible_source_digest():
    text = ('﻿[\n {"url":"https://example.org/a", "title":null, "tags":[], "id":"9007199254740993", '
            '"custom": 0.12345678901234567890123456789}\n]\n')
    result = build_report([{'name': 'original.json', 'text': text, 'format': 'wallabag-v2'}])
    assert result['provenance']['sources'][0]['originalText'] == text
    assert result['provenance']['sources'][0]['sha256'] == hashlib.sha256(text.encode('utf-8')).hexdigest()
    assert result['records']['records'][0]['members'][0]['nativeId'] == '9007199254740993'
