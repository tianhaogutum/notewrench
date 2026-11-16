# SPDX-License-Identifier: AGPL-3.0-only
# Port of linkdedupe tests/host-rules.test.js.
import hashlib

import pytest

from notewrench.linkdedupe import build_report, normalize_url, parse_rule_file

from .helpers import js, read_fixture

KEEP = {'id': 'catalog-business-source', 'host': 'catalog.example', 'keepParams': ['utm_source']}
DROP = {'id': 'news-tracking', 'host': 'news.example', 'dropParams': ['utm_source', 'ref']}


def document(rules):
    return js({'schemaVersion': 1, 'rules': rules})


def parse(text, name='rules.json'):
    return parse_rule_file(text, name)


def test_host_rules_keep_remove_the_same_key_differently_and_explain_cumulative_comparisons():
    text = document([KEEP, DROP])
    rules = parse(text)
    catalog = normalize_url('https://CATALOG.example/a?%75tm_source=edition&x=%2f', {'rules': rules})
    assert catalog['url'] == 'https://catalog.example/a?%75tm_source=edition&x=%2f'
    trace = catalog['ruleComparisons'][0]
    assert trace['ruleId'] == KEEP['id']
    assert trace['beforeUrl'] == 'https://catalog.example/a?x=%2f'
    assert trace['afterUrl'] == catalog['url']
    assert trace['status'] == 'applied'
    assert trace['source'] == {'name': 'rules.json', 'sha256': hashlib.sha256(text.encode()).hexdigest(), 'pointer': '/rules/0'}
    assert trace['matchedParameters'] == [{'parameter': 'utm_source', 'action': 'keep', 'queryIndex': 0}]
    assert next(r for r in catalog['normalizationReasons'] if r.get('ruleId') == KEEP['id'])['source']['pointer'] == '/rules/0'
    news = normalize_url('https://news.example/a?utm_source=x&ref=front&ref=back&x=%2F', {'rules': rules})
    assert news['url'] == 'https://news.example/a?x=%2F'
    assert normalize_url('https://catalog.example/a?utm_source=x',
                         {'rules': rules, 'dropParams': ['utm_source'], 'sortQuery': True})['url'] == 'https://catalog.example/a?utm_source=x'
    assert news['ruleComparisons'][0]['source']['pointer'] == '/rules/1'
    assert news['ruleComparisons'][0]['beforeUrl'] == 'https://news.example/a?ref=front&ref=back&x=%2F'
    for result in [catalog, news]:
        assert normalize_url(result['url'], {'rules': rules})['url'] == result['url']


def test_host_matching_is_exact_and_unknown_hosts_retain_the_conservative_baseline():
    rules = parse(document([KEEP, {'id': 'idna', 'host': 'bücher.example', 'keepParams': ['utm_source']}]))
    for host in ['unknown.example', 'sub.catalog.example', 'catalog.example.', 'catalog%2eexample']:
        url = f'https://{host}/a?utm_source=x&ref=front'
        result = normalize_url(url, {'rules': rules})
        assert result['url'] == normalize_url(url)['url']
        assert result['ruleComparisons'] == []
    ip_rules = parse(document([{**KEEP, 'host': '127.1'}]))
    assert normalize_url('https://127.1/a?utm_source=x', {'rules': ip_rules})['url'] == 'https://127.1/a?utm_source=x'
    assert normalize_url('https://127.0.0.1/a?utm_source=x', {'rules': ip_rules})['url'] == 'https://127.0.0.1/a'
    assert (normalize_url('https://xn--bcher-kva.example:8443/a?utm_source=x', {'rules': rules})['url']
            == 'https://xn--bcher-kva.example:8443/a?utm_source=x')


def test_multiple_disjoint_rules_on_one_host_compare_in_file_order_including_no_match():
    rules = parse(document([KEEP, {'id': 'catalog-ref', 'host': KEEP['host'], 'dropParams': ['ref']},
                            {'id': 'absent', 'host': KEEP['host'], 'keepParams': ['utm_medium']}]))
    result = normalize_url('https://catalog.example/a?utm_source=x&ref=1', {'rules': rules})
    assert result['url'] == 'https://catalog.example/a?utm_source=x'
    assert result['ruleComparisons'][0]['afterUrl'] == result['ruleComparisons'][1]['beforeUrl']
    assert result['ruleComparisons'][1]['afterUrl'] == result['ruleComparisons'][2]['beforeUrl']
    assert result['ruleComparisons'][2]['status'] == 'no-match'


@pytest.mark.parametrize('key', ['%73ig', 'X-Amz-Signature', 'ToKeN', 'oauth_nonce'])
def test_signed_urls_remain_byte_for_byte_frozen_and_host_rules_explain_their_blocked_status(key):
    rules = parse(document([DROP]))
    url = f'HTTP://NEWS.example:80/a/?{key}=opaque&utm_source=x&ref=front#x'
    result = normalize_url(url, {'rules': rules, 'https': True, 'stripFragment': True})
    assert result['url'] == url
    assert result['normalizationReasons'] == []
    assert result['protections'][0]['rule'] == 'signed-or-authenticated-url'
    assert result['ruleComparisons'][0]['status'] == 'protected'
    assert result['ruleComparisons'][0]['beforeUrl'] == url
    assert result['ruleComparisons'][0]['afterUrl'] == url
    assert result['ruleComparisons'][0]['matchedParameters'] == []


INVALID = [
    'not JSON', '{"schemaVersion":1,"rules":[],"rules":[]}',
    '{"schemaVersion":1,"rules":[{"id":"a","host":"x.example","keepParams":["utm_source"],"keepParams":["ref"]}]}',
    js({'schemaVersion': 2, 'rules': [KEEP]}), document([]), document([None]),
    document([{**KEEP, 'allowSigned': True}]), document([{**KEEP, 'dropParams': ['utm_source']}]),
    document([KEEP, {**KEEP, 'id': 'duplicate-target', 'host': 'CATALOG.example', 'dropParams': ['utm_source']}]),
    document([KEEP, {**DROP, 'id': KEEP['id']}]), document([{**KEEP, 'keepParams': ['ref', 'ref']}]),
    document([{**KEEP, 'keepParams': 'ref'}]), document([{**KEEP, 'keepParams': None}]),
    document([{**KEEP, 'keepParams': [f'p{i}' for i in range(33)]}]), document([{'id': 'empty', 'host': 'x.example'}]),
    document([{**DROP, 'dropParams': ['token']}]), document([{**DROP, 'dropParams': ['X-Amz-Signature']}]),
    document([{**DROP, 'dropParams': ['utm_signature']}]), document([{**DROP, 'dropParams': ['*']}]),
    *[document([{**KEEP, 'host': host}]) for host in ['*.example', 'https://catalog.example', 'catalog.example:443',
                                                       'catalog.example/a', 'catalog%2eexample', 'catalog.example.',
                                                       ' catalog.example', '']],
    document([{**KEEP, 'keepParams': ['']}]), document([{**KEEP, 'keepParams': ['ref'], 'script': 'ignored?'}]),
    js({'schemaVersion': 1, 'rules': [KEEP], 'allowSigned': True}),
    document([{**KEEP, 'id': f'r{i}', 'host': f'h{i}.example'} for i in range(129)]),
]


@pytest.mark.parametrize('text', INVALID)
def test_malformed_ambiguous_conflicting_and_authentication_deleting_rules_fail_closed(text):
    with pytest.raises(Exception):
        parse(text)


def test_keep_tracking_conflicts_and_unvalidated_rules_fail():
    with pytest.raises(Exception, match='conflict'):
        normalize_url('https://news.example', {'keepTracking': True, 'rules': parse(document([DROP]))})
    with pytest.raises(Exception):
        build_report([{'name': 'empty', 'text': '[]', 'format': 'wallabag-v2'}], skip_invalid=True,
                     normalization={'rules': {'schemaVersion': 1, 'rules': [KEEP]}})


def test_synthetic_native_export_proves_per_host_separation_removal_and_source_reporting():
    text = read_fixture('host-rules.json')
    data = read_fixture('host-rules-wallabag-v2.json')
    baseline = build_report([{'name': 'synthetic', 'text': data}])
    assert baseline['clusters']['clusters'][0]['canonicalUrl'] == 'https://catalog.example/a'
    report = build_report([{'name': 'synthetic', 'text': data}], normalization={'rules': parse(text, 'host-rules.json')})
    assert report['records']['summary']['totalRecords'] == 7
    assert report['records']['summary']['canonicalRecords'] == 6
    assert len(report['clusters']['clusters']) == 1
    assert report['clusters']['clusters'][0]['canonicalUrl'] == 'https://news.example/a'
    catalogs = [record for record in report['records']['records'] if record['canonicalUrl'].startswith('https://catalog.example/')]
    assert len(catalogs) == 2
    assert catalogs[0]['members'][0]['ruleComparisons'][0]['source']['name'] == 'host-rules.json'
    assert report['records']['normalization']['rules']['source']['sha256'] == hashlib.sha256(text.encode()).hexdigest()
