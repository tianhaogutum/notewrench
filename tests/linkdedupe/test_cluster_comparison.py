# SPDX-License-Identifier: AGPL-3.0-only
# Port of linkdedupe tests/cluster-comparison.test.js.
import pytest

from notewrench.linkdedupe import build_report, build_wallabag_export, parse_rule_file

from .helpers import js, read_fixture


def rules(entries):
    return parse_rule_file(js({'schemaVersion': 1, 'rules': entries}), 'rules.json')


def row(url):
    return {'id': 1, 'url': url, 'title': None, 'tags': [], 'created_at': '2026-11-11', 'is_archived': 0}


def source(urls):
    return {'name': 'synthetic.json', 'text': js([row(url) for url in urls])}


def cluster(canonical_url, *refs):
    return {'canonicalUrl': canonical_url, 'sourceRefs': list(refs)}


def change(ref, before, after, added, removed):
    return {'sourceRef': ref, 'before': before, 'after': after, 'addedPeerSourceRefs': added, 'removedPeerSourceRefs': removed}


def options(entries):
    return {'compare_clusters': True, 'normalization': {'rules': rules(entries)}}


KEEP = {'id': 'edition', 'host': 'catalog.example', 'keepParams': ['utm_source']}
DROP = {'id': 'ref', 'host': 'catalog.example', 'dropParams': ['ref']}


# Contract: inventories contain candidates only (>=2 accepted URL records).
# Identity is source-position membership, never cN, native ID, URL, or counts.
# Global enter/leave means singleton <-> candidate. Per-record peer deltas
# also expose regrouping where a record remains in a candidate on both sides.
def test_shipped_rule_fixture_lists_exact_source_records_entering_and_leaving_candidates():
    sources = [{'name': 'fixture.json', 'text': read_fixture('host-rules-wallabag-v2.json')}]
    config = {'rules': parse_rule_file(read_fixture('host-rules.json'), 'rules.json')}
    report = build_report(sources, compare_clusters=True, normalization=config)
    comparison = report['clusters']['clusterComparison']
    catalog = cluster('https://catalog.example/a', 's1:r1', 's1:r2')
    news = cluster('https://news.example/a', 's1:r3', 's1:r4')
    assert comparison == {
        'schemaVersion': 1,
        'baseline': {'normalization': {**report['records']['normalization'], 'rules': None}, 'clusters': [catalog]},
        'ruleEnabled': {'normalization': report['records']['normalization'], 'clusters': [news]},
        'enteredSourceRefs': ['s1:r3', 's1:r4'],
        'leftSourceRefs': ['s1:r1', 's1:r2'],
        'changes': [
            change('s1:r1', catalog, None, [], ['s1:r2']),
            change('s1:r2', catalog, None, [], ['s1:r1']),
            change('s1:r3', None, news, ['s1:r4'], []),
            change('s1:r4', None, news, ['s1:r3'], []),
        ],
    }
    assert report == build_report(sources, compare_clusters=True, normalization=config)
    ordinary = build_report(sources, normalization=config)
    candidate_report = {key: value for key, value in report['clusters'].items() if key != 'clusterComparison'}
    assert candidate_report == ordinary['clusters']
    for key in ('records', 'mergePlan', 'provenance'):
        assert report[key] == ordinary[key]


def test_equal_cluster_counts_and_sizes_still_expose_every_peer_replacement():
    report = build_report([source([
        'https://catalog.example/a?utm_source=x&ref=one',
        'https://catalog.example/a?utm_source=y&ref=one',
        'https://catalog.example/a?utm_source=x&ref=two',
        'https://catalog.example/a?utm_source=y&ref=two',
    ])], **options([KEEP, DROP]))
    c = report['clusters']['clusterComparison']
    b1 = cluster('https://catalog.example/a?ref=one', 's1:r1', 's1:r2')
    b2 = cluster('https://catalog.example/a?ref=two', 's1:r3', 's1:r4')
    a1 = cluster('https://catalog.example/a?utm_source=x', 's1:r1', 's1:r3')
    a2 = cluster('https://catalog.example/a?utm_source=y', 's1:r2', 's1:r4')
    assert c['baseline']['clusters'] == [b1, b2]
    assert c['ruleEnabled']['clusters'] == [a1, a2]
    assert c['enteredSourceRefs'] == []
    assert c['leftSourceRefs'] == []
    assert c['changes'] == [
        change('s1:r1', b1, a1, ['s1:r3'], ['s1:r2']),
        change('s1:r2', b1, a2, ['s1:r4'], ['s1:r1']),
        change('s1:r3', b2, a1, ['s1:r1'], ['s1:r4']),
        change('s1:r4', b2, a2, ['s1:r2'], ['s1:r3']),
    ]


def test_merging_existing_candidates_and_splitting_into_smaller_candidates_are_membership_changes():
    merge = build_report([source(['https://catalog.example/a?ref=x', 'https://catalog.example/a?ref=x',
                                  'https://catalog.example/a', 'https://catalog.example/a'])],
                         **options([DROP]))['clusters']['clusterComparison']
    assert merge['enteredSourceRefs'] == []
    assert merge['leftSourceRefs'] == []
    assert len(merge['changes']) == 4
    assert merge['changes'][0]['addedPeerSourceRefs'] == ['s1:r3', 's1:r4']
    assert merge['changes'][0]['removedPeerSourceRefs'] == []
    split = build_report([source(['https://catalog.example/a?utm_source=x', 'https://catalog.example/a?utm_source=x',
                                  'https://catalog.example/a?utm_source=y', 'https://catalog.example/a?utm_source=y'])],
                         **options([KEEP]))['clusters']['clusterComparison']
    assert split['enteredSourceRefs'] == []
    assert split['leftSourceRefs'] == []
    assert len(split['changes']) == 4
    assert split['changes'][0]['addedPeerSourceRefs'] == []
    assert split['changes'][0]['removedPeerSourceRefs'] == ['s1:r3', 's1:r4']


def test_one_record_can_enter_or_leave_a_surviving_cross_source_candidate():
    sources = [source(['https://catalog.example/a?ref=x']), source(['https://catalog.example/a', 'https://catalog.example/a'])]
    expanded = build_report(sources, **options([DROP]))['clusters']['clusterComparison']
    assert expanded['enteredSourceRefs'] == ['s1:r1']
    assert expanded['leftSourceRefs'] == []
    assert [c['sourceRef'] for c in expanded['changes']] == ['s1:r1', 's2:r1', 's2:r2']
    assert expanded['changes'][1]['addedPeerSourceRefs'] == ['s1:r1']
    assert expanded['changes'][1]['removedPeerSourceRefs'] == []
    contracted = build_report([source(['https://catalog.example/a?utm_source=x']),
                               source(['https://catalog.example/a', 'https://catalog.example/a'])],
                              **options([KEEP]))['clusters']['clusterComparison']
    assert contracted['enteredSourceRefs'] == []
    assert contracted['leftSourceRefs'] == ['s1:r1']
    assert contracted['changes'][1]['addedPeerSourceRefs'] == []
    assert contracted['changes'][1]['removedPeerSourceRefs'] == ['s1:r1']


def test_url_changes_alone_are_not_membership_changes_and_all_global_options_apply_to_baseline():
    sources = [source(['HTTP://catalog.example:80/a/?b=2&utm_source=x&a=1#first',
                       'https://catalog.example/a?a=1&b=2&utm_source=x#second'])]
    normalization = {'rules': rules([KEEP]), 'https': True, 'stripFragment': True, 'sortQuery': True,
                     'stripTrailingSlash': True, 'dropParams': ['utm_source']}
    c = build_report(sources, normalization=normalization, compare_clusters=True)['clusters']['clusterComparison']
    assert c['baseline']['clusters'] == [cluster('https://catalog.example/a?a=1&b=2', 's1:r1', 's1:r2')]
    assert c['ruleEnabled']['clusters'] == [cluster('https://catalog.example/a?a=1&b=2&utm_source=x', 's1:r1', 's1:r2')]
    assert c['changes'] == []
    assert c['enteredSourceRefs'] == []
    assert c['leftSourceRefs'] == []
    kept = build_report(sources, normalization={**normalization, 'dropParams': [], 'keepTracking': True},
                        compare_clusters=True)['clusters']['clusterComparison']
    assert kept['baseline']['clusters'] == kept['ruleEnabled']['clusters']
    assert kept['changes'] == []


def test_signed_urls_unused_hosts_no_url_records_and_quarantined_rows_do_not_create_false_changes():
    signed = 'HTTP://catalog.example:80/a?sig=synthetic&ref=x'
    sources = [source([signed, 'https://unknown.example/a', 'broken']), {
        'name': 'karakeep.json', 'text': js({'bookmarks': [
            {'createdAt': 1, 'title': None, 'tags': [], 'note': None, 'content': {'type': 'link', 'url': signed}},
            {'createdAt': 1, 'title': None, 'tags': [], 'note': None, 'content': {'type': 'link', 'url': 'https://unknown.example/a'}},
            {'createdAt': 1, 'title': None, 'tags': [], 'note': None, 'content': None},
            {'createdAt': 1, 'title': None, 'tags': [], 'note': None, 'content': None},
        ]}),
    }]
    config = options([DROP, {'id': 'unused', 'host': 'absent.example', 'dropParams': ['ref']}])
    report = build_report(sources, **config, skip_invalid=True)
    c = report['clusters']['clusterComparison']
    assert c['baseline']['clusters'] == [cluster(signed, 's1:r1', 's2:r1'), cluster('https://unknown.example/a', 's1:r2', 's2:r2')]
    assert c['ruleEnabled']['clusters'] == c['baseline']['clusters']
    assert c['changes'] == []
    assert c['enteredSourceRefs'] == []
    assert c['leftSourceRefs'] == []
    assert report['records']['summary']['skippedRecords'] == 1
    assert report['records']['summary']['nonUrlRecords'] == 2
    with pytest.raises(Exception, match='strict mode'):
        build_report(sources, **config)


def test_comparison_is_opt_in_boolean_requires_valid_rules_and_handles_empty_accepted_input():
    sources = [source(['https://catalog.example/a'])]
    assert 'clusterComparison' not in build_report(sources)['clusters']
    assert 'clusterComparison' not in build_report(sources, compare_clusters=False)['clusters']
    with pytest.raises(Exception, match='compareClusters requires.*rules'):
        build_report(sources, compare_clusters=True)
    for value in [None, 'true', 1, {}]:
        with pytest.raises(Exception, match='compareClusters must be boolean'):
            build_report(sources, **{**options([KEEP]), 'compare_clusters': value})
    with pytest.raises(Exception):
        build_report(sources, compare_clusters=True, skip_invalid=True, normalization={'rules': {}})
    for item in [{'name': 'empty', 'text': '[]', 'format': 'wallabag-v2'}, source(['broken'])]:
        c = build_report([item], **options([KEEP]), skip_invalid=True)['clusters']['clusterComparison']
        assert c['baseline']['clusters'] == []
        assert c['ruleEnabled']['clusters'] == []
        assert c['changes'] == []
        assert c['enteredSourceRefs'] == []
        assert c['leftSourceRefs'] == []


def test_native_export_exposes_candidate_comparison_without_changing_approval_basis_or_output():
    sources = [source(['https://catalog.example/a?ref=x', 'https://catalog.example/a'])]
    config = options([DROP])
    ordinary = build_wallabag_export(sources, normalization=config['normalization'])
    compared = build_wallabag_export(sources, **config)
    assert compared['clusters']['clusterComparison']
    assert compared['wallabagDecisions'] == ordinary['wallabagDecisions']
    assert compared['wallabagExport'] == ordinary['wallabagExport']
    assert compared['wallabagMergeReport'] == ordinary['wallabagMergeReport']
    assert compared['clusters']['clusterComparison']['enteredSourceRefs'] == ['s1:r1', 's1:r2']
