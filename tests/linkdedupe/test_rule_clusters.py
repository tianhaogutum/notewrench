# SPDX-License-Identifier: AGPL-3.0-only
# Port of linkdedupe tests/rule-clusters.test.js.
import hashlib

import pytest

from notewrench.linkdedupe import build_report, build_wallabag_export, parse_rule_file

from .helpers import js, read_fixture


def source(urls):
    return {'name': 'rows.json', 'format': 'wallabag-v2', 'text': js([{'url': url, 'title': None, 'tags': []} for url in urls])}


def cluster(canonical_url, *refs):
    return {'canonicalUrl': canonical_url, 'sourceRefs': list(refs)}


def change(ref, before, after, added, removed):
    return {'sourceRef': ref, 'before': before, 'after': after, 'addedPeerSourceRefs': added, 'removedPeerSourceRefs': removed}


def parse_rules(entries):
    return parse_rule_file(js({'schemaVersion': 1, 'rules': entries}), 'rules.json')


DROP = {'id': 'ref', 'host': 'catalog.example', 'dropParams': ['ref']}
KEEP = {'id': 'edition', 'host': 'catalog.example', 'keepParams': ['utm_source']}
UNUSED = {'id': 'unused', 'host': 'absent.example', 'dropParams': ['ref']}
NO_CHANGES = {'enteredSourceRefs': [], 'leftSourceRefs': [], 'changes': []}


def step_delta(step):
    return {'enteredSourceRefs': step['enteredSourceRefs'], 'leftSourceRefs': step['leftSourceRefs'], 'changes': step['changes']}


def test_exact_cumulative_rule_steps_expose_split_then_regrouping_hidden_by_equal_final_counts():
    text = read_fixture('rule-clusters.json')
    rules = parse_rule_file(text, 'rule-clusters.json')
    sources = [{'name': 'rows.json', 'text': read_fixture('rule-clusters-wallabag-v2.json')}]
    options = {'normalization': {'rules': rules}, 'compare_rule_clusters': True}
    report = build_report(sources, **options)
    c = report['clusters']['clusterComparison']
    assert c.get('ruleSteps'), 'cumulative ruleSteps missing'
    b1 = cluster('https://catalog.example/a?ref=one', 's1:r1', 's1:r2')
    b2 = cluster('https://catalog.example/a?ref=two', 's1:r3', 's1:r4')
    a1 = cluster('https://catalog.example/a?utm_source=x', 's1:r1', 's1:r3')
    a2 = cluster('https://catalog.example/a?utm_source=y', 's1:r2', 's1:r4')
    refs = ['s1:r1', 's1:r2', 's1:r3', 's1:r4']
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    provenance = lambda index: {'name': 'rule-clusters.json', 'sha256': digest, 'pointer': f'/rules/{index}'}
    assert c['ruleSteps'] == [
        {'ruleId': 'edition', 'host': 'catalog.example', 'source': provenance(0),
         'beforeClusters': [b1, b2], 'afterClusters': [], 'enteredSourceRefs': [], 'leftSourceRefs': refs,
         'changes': [change(refs[0], b1, None, [], [refs[1]]), change(refs[1], b1, None, [], [refs[0]]),
                     change(refs[2], b2, None, [], [refs[3]]), change(refs[3], b2, None, [], [refs[2]])]},
        {'ruleId': 'ref', 'host': 'catalog.example', 'source': provenance(1),
         'beforeClusters': [], 'afterClusters': [a1, a2], 'enteredSourceRefs': refs, 'leftSourceRefs': [],
         'changes': [change(refs[0], None, a1, [refs[2]], []), change(refs[1], None, a2, [refs[3]], []),
                     change(refs[2], None, a1, [refs[0]], []), change(refs[3], None, a2, [refs[1]], [])]},
        {'ruleId': 'unused', 'host': 'absent.example', 'source': provenance(2),
         'beforeClusters': [a1, a2], 'afterClusters': [a1, a2], **NO_CHANGES},
    ]
    assert c['ruleSteps'][-1]['afterClusters'] == c['ruleEnabled']['clusters']
    assert c['ruleSteps'][0]['beforeClusters'] == c['baseline']['clusters']
    # Top-level deltas retain baseline-to-final semantics; the four peers change.
    assert c['enteredSourceRefs'] == []
    assert c['leftSourceRefs'] == []
    assert c['changes'] == [change(refs[0], b1, a1, [refs[2]], [refs[1]]), change(refs[1], b1, a2, [refs[3]], [refs[0]]),
                            change(refs[2], b2, a1, [refs[0]], [refs[3]]), change(refs[3], b2, a2, [refs[1]], [refs[2]])]
    assert report == build_report(sources, **options)
    ordinary = build_report(sources, normalization={'rules': rules})
    endpoint = build_report(sources, normalization={'rules': rules}, compare_clusters=True)
    endpoint_comparison = {k: v for k, v in c.items() if k != 'ruleSteps'}
    assert endpoint_comparison == endpoint['clusters']['clusterComparison']
    assert {k: v for k, v in report['clusters'].items() if k != 'clusterComparison'} == ordinary['clusters']
    for key in ('records', 'mergePlan', 'provenance'):
        assert report[key] == ordinary[key]
    assert 'ruleSteps' not in endpoint['clusters']['clusterComparison']
    assert build_report(sources, **options, compare_clusters=True) == report


def test_each_cumulative_prefix_uses_all_global_options_and_preserves_cross_source_order_and_peer_changes():
    entries = [KEEP, UNUSED, DROP]
    rules = parse_rules(entries)
    sources = [source(['HTTP://catalog.example:80/a/?b=2&utm_source=x&ref=one&a=1&utm_custom=z#f',
                       'https://catalog.example/a?utm_source=y&b=2&ref=one&a=1#other']),
               source(['https://catalog.example/a?a=1&ref=two&utm_source=x&b=2',
                       'https://catalog.example/a?b=2&ref=two&a=1&utm_source=y'])]
    normalization = {'rules': rules, 'https': True, 'stripFragment': True, 'sortQuery': True, 'stripTrailingSlash': True,
                     'dropParams': ['utm_custom']}
    c = build_report(sources, normalization=normalization, compare_rule_clusters=True)['clusters']['clusterComparison']
    assert c.get('ruleSteps'), 'cumulative ruleSteps missing'
    previous = c['baseline']['clusters']
    for index, step in enumerate(c['ruleSteps']):
        prefix = build_report(sources, compare_clusters=True, normalization={
            **normalization, 'rules': parse_rules(entries[:index + 1])})['clusters']['clusterComparison']
        assert step['beforeClusters'] == previous
        assert step['afterClusters'] == prefix['ruleEnabled']['clusters']
        previous = step['afterClusters']
    assert c['ruleSteps'][0]['leftSourceRefs'] == ['s1:r1', 's1:r2', 's2:r1', 's2:r2']
    assert c['ruleSteps'][1]['changes'] == []
    assert previous == [cluster('https://catalog.example/a?a=1&b=2&utm_source=x', 's1:r1', 's2:r1'),
                        cluster('https://catalog.example/a?a=1&b=2&utm_source=y', 's1:r2', 's2:r2')]
    # Surviving candidates gaining peers produce peer changes, not enter/leave.
    merge = build_report([source(['https://catalog.example/a?ref=x', 'https://catalog.example/a?ref=x',
                                  'https://catalog.example/a', 'https://catalog.example/a'])],
                         compare_rule_clusters=True, normalization={'rules': parse_rules([UNUSED, DROP])})['clusters']['clusterComparison']
    assert merge['ruleSteps'][1]['enteredSourceRefs'] == []
    assert merge['ruleSteps'][1]['leftSourceRefs'] == []
    assert merge['ruleSteps'][1]['changes'][0]['addedPeerSourceRefs'] == ['s1:r3', 's1:r4']


def test_unused_no_match_url_only_already_applied_and_protected_rules_still_have_empty_change_steps():
    signed = 'HTTP://catalog.example:80/a/?ref=x&sig=fake#f'
    rules = parse_rules([UNUSED, KEEP, DROP, {'id': 'missing', 'host': 'catalog.example', 'dropParams': ['missing']},
                         {'id': 'already', 'host': 'catalog.example', 'dropParams': ['utm_medium']}])
    sources = [source([signed, 'broken', 'https://catalog.example/a/?utm_source=x&utm_medium=z#f']),
               source([signed, 'https://catalog.example/a/?utm_source=x&utm_medium=z#f']),
               {'name': 'no-url.json', 'text': js({'bookmarks': [
                   {'createdAt': 1, 'tags': [], 'title': None, 'note': None, 'content': None},
                   {'createdAt': 1, 'tags': [], 'title': None, 'note': None, 'content': {'type': 'text', 'text': 'a'}},
               ]})}]
    options = {'compare_rule_clusters': True, 'skip_invalid': True,
               'normalization': {'rules': rules, 'https': True, 'stripFragment': True, 'stripTrailingSlash': True}}
    r = build_report(sources, **options)
    c = r['clusters']['clusterComparison']
    assert len(c['ruleSteps']) == 5
    for step in c['ruleSteps']:
        assert step_delta(step) == NO_CHANGES
        assert step['afterClusters'][0] == cluster(signed, 's1:r1', 's2:r1')
    assert c['ruleSteps'][1]['beforeClusters'][1] != c['ruleSteps'][1]['afterClusters'][1]
    assert c['ruleSteps'][1]['afterClusters'][1]['sourceRefs'] == ['s1:r3', 's2:r2']
    assert c['ruleSteps'][-1]['afterClusters'] == c['ruleEnabled']['clusters']
    assert r['records']['summary']['nonUrlRecords'] == 2
    assert r['records']['summary']['skippedRecords'] == 1
    with pytest.raises(Exception, match='strict mode'):
        build_report(sources, **{**options, 'skip_invalid': False})
    kept = build_report([source(['https://catalog.example/a?utm_source=x', 'https://catalog.example/a?utm_source=x'])],
                        compare_rule_clusters=True,
                        normalization={'rules': parse_rules([KEEP]), 'keepTracking': True})['clusters']['clusterComparison']
    assert kept['ruleSteps'][0]['beforeClusters'] == kept['ruleSteps'][0]['afterClusters']
    assert kept['ruleSteps'][0]['changes'] == []


def test_rule_attribution_is_boolean_opt_in_requires_valid_rules_and_enumerates_steps_for_empty_input():
    sources = [source(['https://catalog.example/a'])]
    rules = parse_rules([KEEP, UNUSED])
    assert 'clusterComparison' not in build_report(sources, compare_rule_clusters=False, normalization={'rules': rules})['clusters']
    for value in [None, 1, 'true', {}]:
        with pytest.raises(Exception, match='compareRuleClusters must be boolean'):
            build_report(sources, compare_rule_clusters=value, normalization={'rules': rules})
    with pytest.raises(Exception, match='compareRuleClusters requires.*rules'):
        build_report(sources, compare_rule_clusters=True)
    for skip_invalid in [False, True]:
        with pytest.raises(Exception):
            build_report(sources, compare_rule_clusters=True, skip_invalid=skip_invalid, normalization={'rules': {}})
        with pytest.raises(Exception, match='conflicts'):
            build_report(sources, compare_rule_clusters=True, skip_invalid=skip_invalid,
                         normalization={'rules': parse_rules([DROP]), 'keepTracking': True})
    for item in [source([]), source(['broken'])]:
        c = build_report([item], compare_rule_clusters=True, skip_invalid=True, normalization={'rules': rules})['clusters']['clusterComparison']
        assert len(c['ruleSteps']) == 2
        for step in c['ruleSteps']:
            assert step['beforeClusters'] == []
            assert step['afterClusters'] == []
            assert step_delta(step) == NO_CHANGES


def test_rule_steps_do_not_change_native_output_approvals_or_audit():
    sources = [source(['https://catalog.example/a?ref=x', 'https://catalog.example/a'])]
    normalization = {'rules': parse_rules([DROP, UNUSED])}
    ordinary = build_wallabag_export(sources, normalization=normalization)
    compared = build_wallabag_export(sources, normalization=normalization, compare_rule_clusters=True)
    assert len(compared['clusters']['clusterComparison']['ruleSteps']) == 2
    for key in ('wallabagDecisions', 'wallabagExport', 'wallabagMergeReport'):
        assert compared[key] == ordinary[key]
