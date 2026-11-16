# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 linkdedupe contributors
"""Candidate clusters, merge plan and provenance reports (port of ``lib/report.js``)."""

from __future__ import annotations

from typing import Any

from .adapters import adapt_document, adapt_record, detect_format
from .jsjson import js_string, sha256_hex, stable
from .normalize import normalize_url, validate_options
from .strictjson import parse_json


class ReportError(ValueError):
    """Invalid inputs or options for a report."""


_WALLABAG_MAPPED = frozenset(['title', 'tags', 'note', 'annotations', 'url'])
_KARAKEEP_MAPPED = frozenset(['title', 'tags', 'note'])
_MISSING: Any = object()


def _name(input: dict[str, Any], source_id: str) -> str:
    name = input.get('name')
    return source_id if name is None else js_string(name)


def _unmerged_fields(members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields: dict[str, list[dict[str, Any]]] = {}
    for member in members:
        mapped = _KARAKEEP_MAPPED if member['format'] == 'karakeep' else _WALLABAG_MAPPED
        for field, value in member['raw'].items():
            if field in mapped:
                continue
            fields.setdefault(field, []).append({'sourceRef': member['sourceRef'], 'value': value})
    return [{'field': field, 'values': values,
             'conflict': len({stable(item['value']) for item in values}) > 1 or len(values) != len(members)}
            for field, values in fields.items()]


class _Membership:
    __slots__ = ('cluster', 'refs', 'key')

    def __init__(self, cluster: dict[str, Any], refs: set[str] | None, key: str) -> None:
        self.cluster, self.refs, self.key = cluster, refs, key


def _candidate_memberships(members: list[dict[str, Any]], url_for: Any, compact: bool = False) -> dict[str, Any]:
    groups: dict[str, list[str]] = {}
    for member in members:
        url = url_for(member)
        if url is None:
            continue
        groups.setdefault(url, []).append(member['sourceRef'])
    clusters: list[dict[str, Any]] = []
    by_ref: dict[str, _Membership] = {}
    for canonical_url, source_refs in groups.items():
        if len(source_refs) < 2:
            continue
        cluster = {'canonicalUrl': canonical_url, 'sourceRefs': source_refs}
        # Source refs have a fixed sN:rN grammar. A membership key lets unchanged
        # clusters be compared once per record without scanning every peer again.
        membership = _Membership(cluster, None if compact else set(source_refs), ','.join(source_refs))
        clusters.append(cluster)
        for ref in source_refs:
            by_ref[ref] = membership
    return {'clusters': clusters, 'byRef': by_ref}


def _membership_delta(members: list[dict[str, Any]], baseline: dict[str, Any], enabled: dict[str, Any]) -> dict[str, Any]:
    entered, left, changes = [], [], []
    for member in members:
        source_ref = member['sourceRef']
        before, after = baseline['byRef'].get(source_ref), enabled['byRef'].get(source_ref)
        if not before and after:
            entered.append(source_ref)
        if before and not after:
            left.append(source_ref)
        if (before.key if before else None) == (after.key if after else None):
            continue
        changes.append({
            'sourceRef': source_ref,
            'before': before.cluster if before else None,
            'after': after.cluster if after else None,
            'addedPeerSourceRefs': [ref for ref in (after.cluster['sourceRefs'] if after else [])
                                    if ref != source_ref and not (before and ref in before.refs)],
            'removedPeerSourceRefs': [ref for ref in (before.cluster['sourceRefs'] if before else [])
                                      if ref != source_ref and not (after and ref in after.refs)],
        })
    return {'enteredSourceRefs': entered, 'leftSourceRefs': left, 'changes': changes}


def _candidate_counts(membership: dict[str, Any]) -> dict[str, int]:
    return {'candidateClusters': len(membership['clusters']), 'clusteredRecords': len(membership['byRef'])}


def _membership_counts(members: list[dict[str, Any]], baseline: dict[str, Any], enabled: dict[str, Any]) -> dict[str, int]:
    entered = left = changed = 0
    unchanged: dict[_Membership, bool] = {}
    for member in members:
        source_ref = member['sourceRef']
        before, after = baseline['byRef'].get(source_ref), enabled['byRef'].get(source_ref)
        if not before and after:
            entered += 1
        if before and not after:
            left += 1
        if not before:
            if after:
                changed += 1
            continue
        # If a candidate's complete membership survives, every member has the same
        # peers. Otherwise every member's peers changed, including surviving splits
        # and merges. Compare the shared signatures once per baseline candidate;
        # do not construct per-record before/after objects or added/removed lists.
        if before not in unchanged:
            unchanged[before] = bool(after) and before.key == after.key
        if not unchanged[before]:
            changed += 1
    return {'enteredRecords': entered, 'leftRecords': left, 'changedPeerRecords': changed}


def _compare_memberships(members: list[dict[str, Any]], config: dict[str, Any], compare_rule_clusters: bool,
                         compact: bool) -> dict[str, Any]:
    baseline_config = {**config, 'rules': None}
    current_urls = {member['sourceRef']: None if member['originalUrl'] is None
                    else normalize_url(member['originalUrl'], baseline_config)['url'] for member in members}
    baseline = _candidate_memberships(members, lambda member: current_urls[member['sourceRef']], compact)
    enabled = _candidate_memberships(members, lambda member: member['url'], compact)
    comparison: dict[str, Any] = {
        'schemaVersion': 1,
        **({'mode': 'compact'} if compact else {}),
        'baseline': {'normalization': baseline_config,
                     **(_candidate_counts(baseline) if compact else {'clusters': baseline['clusters']})},
        'ruleEnabled': {'normalization': config,
                        **(_candidate_counts(enabled) if compact else {'clusters': enabled['clusters']})},
        **(_membership_counts(members, baseline, enabled) if compact else _membership_delta(members, baseline, enabled)),
    }
    if compare_rule_clusters:
        # normalize_url already computed cumulative results from the original bytes,
        # including keep restoration and signed freezes. Only matching-host rules
        # have URL comparisons, but every declared rule gets a membership step.
        updates: dict[str, list[tuple[str, str]]] = {rule['id']: [] for rule in config['rules']['rules']}
        for member in members:
            for entry in member['ruleComparisons']:
                updates[entry['ruleId']].append((member['sourceRef'], entry['afterUrl']))
        previous = baseline
        steps = []
        for index, rule in enumerate(config['rules']['rules']):
            for source_ref, url in updates[rule['id']]:
                current_urls[source_ref] = url
            current = _candidate_memberships(members, lambda member: current_urls[member['sourceRef']], compact)
            step = {'ruleId': rule['id'], 'host': rule['host'],
                    'source': {**config['rules']['source'], 'pointer': f'/rules/{index}'}}
            if compact:
                step.update({'before': _candidate_counts(previous), 'after': _candidate_counts(current),
                             **_membership_counts(members, previous, current)})
            else:
                step.update({'beforeClusters': previous['clusters'], 'afterClusters': current['clusters'],
                             **_membership_delta(members, previous, current)})
            previous = current
            steps.append(step)
        comparison['ruleSteps'] = steps
    return comparison


def build_report(inputs: Any, *, normalization: Any = _MISSING, skip_invalid: Any = False, compare_clusters: Any = False,
                 compare_rule_clusters: Any = False, compact_rule_clusters: Any = False) -> dict[str, Any]:
    """Each input is ``{'name', 'text', 'format'?}``. No network or filesystem side effects."""
    if not isinstance(inputs, list) or not inputs:
        raise ReportError('At least one input export is required')
    if not isinstance(skip_invalid, bool):
        raise ReportError('skipInvalid must be boolean')
    if not isinstance(compare_clusters, bool):
        raise ReportError('compareClusters must be boolean')
    if not isinstance(compare_rule_clusters, bool):
        raise ReportError('compareRuleClusters must be boolean')
    if not isinstance(compact_rule_clusters, bool):
        raise ReportError('compactRuleClusters must be boolean')
    if normalization is None:
        raise ReportError('Cannot convert undefined or null to object')
    config = validate_options({} if normalization is _MISSING else normalization)
    if compact_rule_clusters and not config['rules']:
        raise ReportError('compactRuleClusters requires normalization.rules')
    if compare_rule_clusters and not config['rules']:
        raise ReportError('compareRuleClusters requires normalization.rules')
    compare_rules = compare_rule_clusters or compact_rule_clusters
    compare = compare_clusters or compare_rules
    if compare_clusters and not config['rules']:
        raise ReportError('compareClusters requires normalization.rules')
    sources, originals, rejected = [], [], []
    grouped: dict[str, list[dict[str, Any]]] = {}
    comparison_members = []
    total_records = non_url_records = 0
    for source_index, input in enumerate(inputs):
        source_id = f's{source_index + 1}'
        name = _name(input, source_id)
        name_value = source_id if input.get('name') is None else input['name']
        try:
            document = parse_json(input.get('text'))
            fmt = input.get('format')
            format = detect_format(document, 'auto' if fmt is None else fmt)
            rows, root_metadata = adapt_document(document, format)
        except Exception as error:
            raise ReportError(f'{name}: {error}') from error
        sources.append({'sourceId': source_id, 'name': name_value, 'format': format, 'sha256': sha256_hex(input['text']),
                        'recordCount': len(rows), 'rootMetadata': root_metadata})
        for index, raw in enumerate(rows):
            total_records += 1
            source_ref = f'{source_id}:r{index + 1}'
            pointer = f'/bookmarks/{index}' if format == 'karakeep' else f'/{index}'
            try:
                adapted = adapt_record(raw, format)
                if adapted['originalUrl'] is None:
                    normalized = {'url': None, 'normalizationReasons': [], 'ruleComparisons': [],
                                  'protections': [{'rule': 'no-url-not-clustered'}]}
                else:
                    normalized = normalize_url(adapted['originalUrl'], config)
                member = {**adapted, 'sourceRef': source_ref, 'sourceId': source_id, 'pointer': pointer,
                          'format': format, **normalized}
                if normalized['url'] is None:
                    non_url_records += 1
                # Prefix prevents any URL from colliding with a standalone no-URL key.
                key = f'record:{source_ref}' if normalized['url'] is None else f'url:{normalized["url"]}'
                grouped.setdefault(key, []).append(member)
                if compare:
                    comparison_members.append({'sourceRef': source_ref, 'originalUrl': member['originalUrl'],
                                               'url': member['url'],
                                               **({'ruleComparisons': member['ruleComparisons']} if compare_rules else {})})
                originals.append({'sourceRef': source_ref, 'sourceId': source_id, 'pointer': pointer,
                                  'status': 'accepted', 'raw': raw})
            except Exception as error:
                if not skip_invalid:
                    raise ReportError(f'{name}{pointer}: {error} (strict mode; use --skip-invalid to quarantine)') from error
                failure = {'sourceRef': source_ref, 'sourceId': source_id, 'pointer': pointer, 'error': str(error)}
                rejected.append(failure)
                originals.append({**failure, 'status': 'rejected', 'raw': raw})
    records = []
    for index, members in enumerate(grouped.values()):
        titles = [{'sourceRef': member['sourceRef'], 'value': member['title']} for member in members]
        records.append({
            'id': f'c{index + 1}',
            'canonicalUrl': members[0]['url'],
            'title': members[0]['title'],
            'titles': titles,
            'tags': list(dict.fromkeys(tag for member in members for tag in member['tags'])),
            'notes': [{'sourceRef': member['sourceRef'], **note} for member in members for note in member['notes']],
            'members': [{'sourceRef': member['sourceRef'], 'sourceId': member['sourceId'], 'pointer': member['pointer'],
                         'nativeId': member['nativeId'], 'originalUrl': member['originalUrl'],
                         'normalizationReasons': member['normalizationReasons'], 'protections': member['protections'],
                         'ruleComparisons': member['ruleComparisons']} for member in members],
            'unmergedFields': _unmerged_fields(members),
            'titleConflict': len({title['value'] for title in titles}) > 1,
        })
    clusters = [record for record in records if len(record['members']) > 1]
    summary = {'inputFiles': len(inputs), 'totalRecords': total_records,
               'acceptedRecords': total_records - len(rejected), 'skippedRecords': len(rejected),
               'nonUrlRecords': non_url_records, 'canonicalRecords': len(records),
               'candidateClusters': len(clusters),
               'clusteredRecords': sum(len(cluster['members']) for cluster in clusters)}
    common = {'schemaVersion': 1, 'tool': 'linkdedupe', 'normalization': config, 'summary': summary}
    clusters_report = {**common, 'clusters': clusters, 'rejected': rejected}
    if compare:
        clusters_report['clusterComparison'] = _compare_memberships(comparison_members, config, compare_rules,
                                                                    compact_rule_clusters)
    return {
        'records': {**common, 'sources': sources, 'records': records},
        'clusters': clusters_report,
        'mergePlan': {
            **common, 'mode': 'candidate-only', 'executable': False,
            'warning': 'Same normalized URL does not establish identical content. '
                       'Review provenance and unmerged fields before any manual action.',
            'candidates': [{'canonicalRecordId': record['id'], 'proposedCanonicalUrl': record['canonicalUrl'],
                            'sourceRefs': [member['sourceRef'] for member in record['members']],
                            'tags': record['tags'], 'notes': record['notes'], 'titleConflict': record['titleConflict'],
                            'unmergedFields': record['unmergedFields']} for record in clusters],
            'rejected': rejected,
        },
        'provenance': {'schemaVersion': 1,
                       'sources': [{**source, 'originalText': inputs[index]['text']} for index, source in enumerate(sources)],
                       'records': originals},
    }
