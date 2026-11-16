# SPDX-License-Identifier: AGPL-3.0-only
# Port of linkdedupe tests/compact-rule-clusters.test.js.
import copy

import pytest

from notewrench.linkdedupe import build_report, build_wallabag_export, parse_rule_file

from .helpers import js, read_fixture

KEEP = {'id': 'edition', 'host': 'catalog.example', 'keepParams': ['utm_source']}
DROP = {'id': 'ref', 'host': 'catalog.example', 'dropParams': ['ref']}
UNUSED = {'id': 'unused', 'host': 'absent.example', 'dropParams': ['ref']}


def source(urls):
    return {'name': 'rows.json', 'format': 'wallabag-v2', 'text': js([{'url': url, 'tags': [], 'title': None} for url in urls])}


def rules(entries=(KEEP, DROP, UNUSED)):
    return parse_rule_file(js({'schemaVersion': 1, 'rules': list(entries)}), 'rules.json')


def counts(candidate_clusters, clustered_records):
    return {'candidateClusters': candidate_clusters, 'clusteredRecords': clustered_records}


def delta(entered=0, left=0, changed=0):
    return {'enteredRecords': entered, 'leftRecords': left, 'changedPeerRecords': changed}


def inventory_counts(clusters):
    return counts(len(clusters), sum(len(c['sourceRefs']) for c in clusters))


def delta_counts(full):
    return delta(len(full['enteredSourceRefs']), len(full['leftSourceRefs']), len(full['changes']))


# The existing full format is an independent oracle, including URL-only changes
# and records that remain clustered but gain/lose peers.
def summarize(full):
    return {'schemaVersion': 1, 'mode': 'compact',
            'baseline': {'normalization': full['baseline']['normalization'], **inventory_counts(full['baseline']['clusters'])},
            'ruleEnabled': {'normalization': full['ruleEnabled']['normalization'], **inventory_counts(full['ruleEnabled']['clusters'])},
            **delta_counts(full),
            'ruleSteps': [{'ruleId': s['ruleId'], 'host': s['host'], 'source': s['source'],
                           'before': inventory_counts(s['beforeClusters']), 'after': inventory_counts(s['afterClusters']),
                           **delta_counts(s)} for s in full['ruleSteps']]}


def parity(sources, **options):
    full = build_report(sources, **options, compare_rule_clusters=True)
    compact = build_report(sources, **options, compact_rule_clusters=True)
    assert compact['clusters']['clusterComparison'], 'compact comparison missing'
    assert compact['clusters']['clusterComparison'] == summarize(full['clusters']['clusterComparison'])
    strip = lambda clusters: {k: v for k, v in clusters.items() if k != 'clusterComparison'}
    assert strip(compact['clusters']) == strip(full['clusters'])
    for key in ('records', 'mergePlan', 'provenance'):
        assert compact[key] == full[key]
    return compact['clusters']['clusterComparison']


def test_compact_split_regroup_contract_has_exact_endpoint_and_cumulative_counts_with_full_provenance():
    normalization = {'rules': parse_rule_file(read_fixture('rule-clusters.json'), 'fixture-rules.json')}
    sources = [{'name': 'split.json', 'text': read_fixture('rule-clusters-wallabag-v2.json')}]
    c = parity(sources, normalization=normalization)
    assert c['baseline'] == {'normalization': {**c['ruleEnabled']['normalization'], 'rules': None}, **counts(2, 4)}
    assert c['ruleEnabled'] == {'normalization': c['ruleEnabled']['normalization'], **counts(2, 4)}
    provenance = lambda i: {**normalization['rules']['source'], 'pointer': f'/rules/{i}'}
    assert c['ruleSteps'] == [
        {'ruleId': 'edition', 'host': 'catalog.example', 'source': provenance(0), 'before': counts(2, 4), 'after': counts(0, 0), **delta(0, 4, 4)},
        {'ruleId': 'ref', 'host': 'catalog.example', 'source': provenance(1), 'before': counts(0, 0), 'after': counts(2, 4), **delta(4, 0, 4)},
        {'ruleId': 'unused', 'host': 'absent.example', 'source': provenance(2), 'before': counts(2, 4), 'after': counts(2, 4), **delta()},
    ]
    assert delta(c['enteredRecords'], c['leftRecords'], c['changedPeerRecords']) == delta(0, 0, 4)
    assert build_report(sources, normalization=normalization, compact_rule_clusters=True, compare_clusters=True,
                        compare_rule_clusters=True)['clusters']['clusterComparison'] == c


def test_compact_counts_surviving_candidate_merges_splits_and_signed_pairs_without_false_entries_exits():
    sources = [{'name': 'merge.json', 'text': read_fixture('compact-merge-wallabag-v2.json')}]
    c = parity(sources, normalization={'rules': rules()})
    assert c['baseline']['candidateClusters'] == 3
    assert c['baseline']['clusteredRecords'] == 6
    assert c['ruleEnabled']['candidateClusters'] == 2
    assert c['ruleEnabled']['clusteredRecords'] == 6
    assert [delta(s['enteredRecords'], s['leftRecords'], s['changedPeerRecords']) for s in c['ruleSteps']] == [delta(), delta(0, 0, 4), delta()]
    split = parity([source(['https://catalog.example/a?utm_source=x', 'https://catalog.example/a?utm_source=x',
                            'https://catalog.example/a?utm_source=y', 'https://catalog.example/a?utm_source=y'])],
                   normalization={'rules': rules([KEEP])})
    assert split['ruleSteps'][0]['before'] == counts(1, 4)
    assert split['ruleSteps'][0]['after'] == counts(2, 4)
    assert split['changedPeerRecords'] == 4
    assert split['enteredRecords'] + split['leftRecords'] == 0


def test_compact_cross_source_surviving_entry_exit_counts_and_all_global_normalization_options_match_full():
    for entry, entries, expected in [('https://catalog.example/a?ref=x', [DROP], delta(1, 0, 3)),
                                     ('https://catalog.example/a?utm_source=x', [KEEP], delta(0, 1, 3))]:
        c = parity([source([entry]), source(['https://catalog.example/a', 'https://catalog.example/a'])],
                   normalization={'rules': rules(entries)})
        assert delta(c['enteredRecords'], c['leftRecords'], c['changedPeerRecords']) == expected
    sources = [source(['HTTP://catalog.example:80/a/?b=2&utm_source=x&a=1&utm_custom=z#first']),
               source(['https://catalog.example/a?a=1&b=2&utm_source=x#second'])]
    normalization = {'rules': rules([KEEP, UNUSED]), 'https': True, 'stripFragment': True, 'sortQuery': True,
                     'stripTrailingSlash': True, 'dropParams': ['utm_source', 'utm_custom']}
    c = parity(sources, normalization=normalization)
    assert c['ruleSteps'][0]['before'] == counts(1, 2)
    assert c['ruleSteps'][0]['after'] == counts(1, 2)
    assert c['changedPeerRecords'] == 0  # URL changed, peers did not.
    parity(sources, normalization={**normalization, 'rules': rules([KEEP]), 'dropParams': [], 'keepTracking': True})


def test_compact_quarantined_no_url_protected_no_op_and_empty_inputs_match_full_without_inventing_counts():
    signed = 'HTTP://catalog.example:80/a/?ref=x&sig=fake#f'
    sources = [source(['broken', signed]), source([signed]), {'name': 'notes.json', 'text': js({'bookmarks': [
        {'createdAt': 1, 'tags': [], 'title': None, 'note': None, 'content': None},
        {'createdAt': 1, 'tags': [], 'title': None, 'note': None, 'content': {'type': 'text', 'text': 'a'}},
    ]})}]
    normalization = {'rules': rules([KEEP, UNUSED, DROP, {'id': 'missing', 'host': 'catalog.example', 'dropParams': ['missing']},
                                     {'id': 'already', 'host': 'catalog.example', 'dropParams': ['utm_medium']}]),
                     'https': True, 'stripFragment': True, 'sortQuery': True, 'stripTrailingSlash': True}
    c = parity(sources, normalization=normalization, skip_invalid=True)
    for s in c['ruleSteps']:
        assert s['before'] == counts(1, 2)
        assert s['after'] == counts(1, 2)
        assert delta(s['enteredRecords'], s['leftRecords'], s['changedPeerRecords']) == delta()
    for item in [source([]), source(['broken'])]:
        empty = parity([item], normalization=normalization, skip_invalid=True)
        for s in empty['ruleSteps']:
            assert s['before'] == counts(0, 0)
            assert s['after'] == counts(0, 0)
            assert delta(s['enteredRecords'], s['leftRecords'], s['changedPeerRecords']) == delta()


def test_compact_is_opt_in_and_boolean_requires_rules_and_cannot_quarantine_configuration_errors():
    sources = [source(['https://catalog.example/a'])]
    normalization = {'rules': rules()}
    assert build_report(sources, normalization=normalization, compact_rule_clusters=False) == build_report(sources, normalization=normalization)
    assert 'mode' not in build_report(sources, normalization=normalization, compare_rule_clusters=True,
                                      compact_rule_clusters=False)['clusters']['clusterComparison']
    for value in [None, 1, 'true', {}, []]:
        with pytest.raises(Exception, match='compactRuleClusters must be boolean'):
            build_report(sources, normalization=normalization, compact_rule_clusters=value)
    with pytest.raises(Exception, match='compactRuleClusters requires normalization.rules'):
        build_report(sources, compact_rule_clusters=True)
    for skip_invalid in [False, True]:
        with pytest.raises(Exception):
            build_report(sources, compact_rule_clusters=True, skip_invalid=skip_invalid, normalization={'rules': {}})
        with pytest.raises(Exception, match='conflicts'):
            build_report(sources, compact_rule_clusters=True, skip_invalid=skip_invalid,
                         normalization={'rules': rules([DROP]), 'keepTracking': True})
    with pytest.raises(Exception, match='strict mode'):
        build_report([source(['broken'])], compact_rule_clusters=True, normalization=normalization)


def test_compact_leaves_native_approvals_and_audit_unchanged():
    # The original also replaced Array.prototype.filter to prove that the compact path
    # builds no per-record peer lists; that JavaScript-only guard is not ported.
    sources = [source(['https://catalog.example/a?ref=x', 'https://catalog.example/a?ref=x', 'https://catalog.example/a',
                       'https://catalog.example/a'])]
    normalization = {'rules': rules([DROP])}
    full = build_wallabag_export(sources, normalization=normalization, compare_rule_clusters=True)
    compact = build_wallabag_export(sources, normalization=normalization, compact_rule_clusters=True)
    assert compact['clusters']['clusterComparison'] == summarize(full['clusters']['clusterComparison'])
    for key in ('wallabagExport', 'wallabagDecisions', 'wallabagMergeReport'):
        assert compact[key] == full[key]
    decisions = copy.deepcopy(full['wallabagDecisions'])
    decisions['decisions'][0]['approved'] = True
    approved = build_wallabag_export(sources, normalization=normalization, decisions=decisions, compact_rule_clusters=True)
    ordinary = build_wallabag_export(sources, normalization=normalization, decisions=decisions)
    for key in ('wallabagExport', 'wallabagDecisions', 'wallabagMergeReport'):
        assert approved[key] == ordinary[key]
