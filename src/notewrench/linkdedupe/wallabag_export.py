# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 tianhaogutum
"""Approved Wallabag v2 native-field export (port of ``lib/wallabag-export.js``)."""

from __future__ import annotations

import hashlib
from typing import Any

from .jsjson import UNDEFINED, deep_strict_equal, get, is_number, js_keys, js_string, stable, stringify, utf8
from .report import build_report


class WallabagExportError(ValueError):
    """Invalid native export request or decisions."""


# EntriesExport::prepareSerializingContent uses entries_for_user, not export_all.
# Field evidence: docs/links.md (pinned Wallabag adapter).
WALLABAG_EXPORT_ADAPTER = 'wallabag-v2@2c15fef98033c43d86cf3dcdd97bfe53f3b4f22d'
_FIELDS = frozenset(['id', 'uid', 'title', 'url', 'origin_url', 'given_url', 'is_archived',
                     'archived_at', 'is_starred', 'content', 'created_at', 'updated_at', 'published_at',
                     'published_by', 'starred_at', 'annotations', 'mimetype', 'language', 'reading_time',
                     'domain_name', 'preview_picture', 'http_status', 'headers', 'is_public', 'tags'])
_ANNOTATION_FIELDS = frozenset(['text', 'quote', 'ranges'])
_MISSING: Any = UNDEFINED


def _pointer_key(key: str) -> str:
    return key.replace('~', '~0').replace('/', '~1')


def _hash(value: Any) -> str:
    return hashlib.sha256(utf8(stable(value))).hexdigest()


def _keys(value: Any, expected: list[str]) -> bool:
    return isinstance(value, dict) and len(value) == len(expected) and all(key in value for key in expected)


def _validate_decisions(document: Any, template: dict[str, Any], candidates: dict[str, Any]) -> list[dict[str, Any]]:
    version = get(document, 'schemaVersion')
    if (not _keys(document, ['schemaVersion', 'adapter', 'basisSha256', 'decisions'])
            or not (is_number(version) and version == 1) or document['adapter'] != WALLABAG_EXPORT_ADAPTER
            or not isinstance(document['adapter'], str) or not isinstance(document['decisions'], list)):
        raise WallabagExportError('Invalid Wallabag decisions schema/adapter')
    if not (isinstance(document['basisSha256'], str) and document['basisSha256'] == template['basisSha256']):
        raise WallabagExportError('Wallabag decisions basis mismatch: source bytes or normalization policy changed')
    approvals = []
    selected: set[str] = set()
    for decision in document['decisions']:
        if (not _keys(decision, ['sourceRefs', 'keepSourceRef', 'approved']) or not isinstance(decision['approved'], bool)
                or not isinstance(decision['sourceRefs'], list)
                or any(not isinstance(ref, str) for ref in decision['sourceRefs'])
                or not isinstance(decision['keepSourceRef'], str)):
            raise WallabagExportError('Invalid Wallabag decision')
        source_refs = decision['sourceRefs']
        refs = set(source_refs)
        candidate = candidates.get(source_refs[0]) if source_refs else None
        if (len(refs) < 2 or len(refs) != len(source_refs) or candidate is None
                or any(candidates.get(ref) is not candidate for ref in source_refs)
                or decision['keepSourceRef'] not in refs):
            raise WallabagExportError('Wallabag decision must select two or more distinct members of one current '
                                      'candidate and a keeper within that selection')
        if any(ref in selected for ref in source_refs):
            raise WallabagExportError('Wallabag decisions have overlapping sourceRefs')
        selected.update(source_refs)
        # Decision order is operator-controlled; aggregation always uses source order.
        approvals.append({**decision, 'sourceRefs': [member['sourceRef'] for member in candidate['members']
                                                     if member['sourceRef'] in refs]})
    return approvals


def _annotation_error(raw: dict[str, Any]) -> bool:
    return any(not isinstance(get(note, 'text'), str) or not isinstance(get(note, 'quote'), str)
               or not isinstance(get(note, 'ranges'), list) for note in raw.get('annotations') or [])


def _project(raw: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in raw.items() if key in _FIELDS}
    if 'annotations' in result:
        result['annotations'] = [{key: value for key, value in note.items() if key in _ANNOTATION_FIELDS}
                                 for note in raw['annotations']]
    return result


def build_wallabag_export(inputs: Any, *, decisions: Any = _MISSING, **options: Any) -> dict[str, Any]:
    """One native dump per run: position refs + exact source hash avoid cross-instance ID guesses.

    No IO. Missing/false approvals retain separate entries. Unsupported fields live in
    the audit report and exact provenance, never masquerade as native export fields.
    ``options`` are :func:`build_report`'s keyword options.
    """
    if not isinstance(inputs, list) or len(inputs) != 1:
        raise WallabagExportError('Native export requires exactly one Wallabag input')
    report = build_report(inputs, **options)
    if report['records']['sources'][0]['format'] != 'wallabag-v2':
        raise WallabagExportError('Native export requires a Wallabag v2 input')
    source = report['records']['sources'][0]
    basis = _hash({'adapter': WALLABAG_EXPORT_ADAPTER, 'sourceSha256': source['sha256'],
                   'normalization': report['records']['normalization']})
    template = {
        'schemaVersion': 1, 'adapter': WALLABAG_EXPORT_ADAPTER, 'basisSha256': basis,
        'decisions': [{'sourceRefs': [member['sourceRef'] for member in cluster['members']],
                       'keepSourceRef': cluster['members'][0]['sourceRef'], 'approved': False}
                      for cluster in report['clusters']['clusters']],
    }
    candidates = {member['sourceRef']: cluster for cluster in report['clusters']['clusters'] for member in cluster['members']}
    approvals = [] if decisions is _MISSING else _validate_decisions(decisions, template, candidates)
    originals = {row['sourceRef']: row for row in report['provenance']['records']}
    ambiguous: set[str] = set()
    # Include rejected rows in identity checks; integer 10 and string "10" are
    # deliberately considered ambiguous, even if their URLs lie in other clusters.
    for field in ('id', 'uid'):
        seen: dict[str, str] = {}
        for row in originals.values():
            value = get(row['raw'], field)
            if not isinstance(row['raw'], dict) or value is UNDEFINED or value is None or value == '':
                continue
            key = js_string(value)
            if key in seen:
                ambiguous.add(seen[key])
                ambiguous.add(row['sourceRef'])
            else:
                seen[key] = row['sourceRef']
    groups: dict[str, dict[str, Any]] = {}
    reasons: dict[str, str] = {ref: 'not-approved' for ref in candidates}
    outcomes: list[dict[str, Any]] = []
    approved_groups = blocked_groups = 0
    for decision_index, decision in enumerate(approvals):
        outcome = {'decisionIndex': decision_index, 'sourceRefs': decision['sourceRefs'],
                   'keepSourceRef': decision['keepSourceRef'], 'status': 'not-approved', 'reason': 'not-approved',
                   'outputPointer': None}
        outcomes.append(outcome)
        if not decision['approved']:
            continue
        refs = decision['sourceRefs']
        approved_groups += 1
        keeper = originals[decision['keepSourceRef']]
        reason = None
        if any(ref in ambiguous for ref in refs):
            reason = 'ambiguous-native-identity'
        elif any(_annotation_error(originals[ref]['raw']) for ref in refs):
            reason = 'unrepresentable-annotation'
        elif any(ref != keeper['sourceRef'] and originals[ref]['raw'].get('annotations')
                 and (not isinstance(keeper['raw'].get('content'), str)
                      or originals[ref]['raw'].get('content', UNDEFINED) != keeper['raw']['content'])
                 for ref in refs):
            reason = 'annotation-content-mismatch'
        if reason:
            outcome['status'], outcome['reason'] = 'blocked', reason
            blocked_groups += 1
            for ref in refs:
                reasons[ref] = reason
            continue
        outcome['status'], outcome['reason'] = 'applied', 'explicitly-approved'
        group = {'refs': refs, 'keeper': keeper}
        for ref in refs:
            groups[ref] = group

    entries: list[dict[str, Any]] = []
    mapping: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    unrepresented: list[dict[str, Any]] = []
    # Report every unsupported field, including on merged-away entries. In
    # particular non-native note extensions cannot be encoded as native notes.
    for row in originals.values():
        if row['status'] != 'accepted':
            continue
        raw = row['raw']
        if _annotation_error(raw):
            unrepresented.append({'sourceRef': row['sourceRef'], 'pointer': '/annotations', 'value': raw['annotations'],
                                  'reason': 'invalid-native-annotation-shape; entire record unprocessed'})
        for field, value in raw.items():
            if field not in _FIELDS:
                unrepresented.append({'sourceRef': row['sourceRef'], 'pointer': f'/{_pointer_key(field)}',
                                      'value': value, 'reason': 'outside-pinned-native-fields'})
        for index, note in enumerate(raw.get('annotations') or []):
            for field, value in note.items():
                if field not in _ANNOTATION_FIELDS:
                    unrepresented.append({'sourceRef': row['sourceRef'],
                                          'pointer': f'/annotations/{index}/{_pointer_key(field)}', 'value': value,
                                          'reason': 'outside-pinned-native-annotation-fields'})
    emitted: dict[str, str] = {}
    for row in originals.values():
        if row['status'] != 'accepted' or _annotation_error(row['raw']):
            mapping.append({'sourceRef': row['sourceRef'], 'status': 'unprocessed', 'outputPointer': None,
                            'reason': row.get('error') or 'annotation requires string text, string quote and array '
                                                          'ranges; full record is in provenance'})
            continue
        group = groups.get(row['sourceRef'])
        keeper = group['keeper'] if group else row
        if keeper['sourceRef'] not in emitted:
            output = _project(keeper['raw'])
            if group:
                output['tags'] = list(dict.fromkeys(tag for ref in group['refs'] for tag in originals[ref]['raw']['tags']))
                if any('annotations' in originals[ref]['raw'] for ref in group['refs']):
                    output['annotations'] = [note for ref in group['refs']
                                             for note in _project(originals[ref]['raw']).get('annotations', [])]
                fields = list(dict.fromkeys(key for ref in group['refs'] for key in js_keys(originals[ref]['raw'])))
                for field in fields:
                    if field in ('tags', 'annotations'):
                        continue
                    values = []
                    for source_ref in group['refs']:
                        raw = originals[source_ref]['raw']
                        values.append({'sourceRef': source_ref, 'present': True, 'value': raw[field]} if field in raw
                                      else {'sourceRef': source_ref, 'present': False})
                    if len({stable({'present': v['present'], 'value': v.get('value', UNDEFINED)}) for v in values}) > 1:
                        conflicts.append({'field': field, 'keptSourceRef': keeper['sourceRef'],
                                          'resolution': 'keeper-value-or-absence' if field in _FIELDS else 'audit-only',
                                          'values': values})
            emitted[keeper['sourceRef']] = f'/{len(entries)}'
            entries.append(output)
        mapping.append({'sourceRef': row['sourceRef'], 'status': 'retained' if row is keeper else 'merged',
                        'keptSourceRef': keeper['sourceRef'], 'outputPointer': emitted[keeper['sourceRef']],
                        'reason': 'explicitly-approved' if group else reasons.get(row['sourceRef'], 'standalone')})
    # Output position follows the first selected source row, not decision or keeper order.
    for outcome in outcomes:
        if outcome['status'] == 'applied':
            outcome['outputPointer'] = emitted.get(outcome['keepSourceRef'])
    # Read the actual serialized JSON through the same v2 adapter, then project
    # again. This checks the written data model, not an application import claim.
    serialized = stringify(entries, 2) + '\n'
    reread = build_report([{'name': 'wallabag-export.json', 'text': serialized, 'format': 'wallabag-v2'}])
    readback = [_project(row['raw']) for row in reread['provenance']['records']]
    if not deep_strict_equal(entries, readback):
        raise WallabagExportError('Wallabag export round-trip mismatch')
    summary = {
        'inputRecords': len(mapping), 'outputRecords': len(entries),
        'mergedRecords': sum(1 for row in mapping if row['status'] == 'merged'),
        'unprocessedRecords': sum(1 for row in mapping if row['status'] == 'unprocessed'),
        'approvedGroups': approved_groups, 'blockedGroups': blocked_groups,
        'conflicts': len(conflicts), 'unrepresentedFields': len(unrepresented),
    }
    return {
        **report, 'wallabagExport': entries, 'wallabagDecisions': template,
        'wallabagMergeReport': {
            'schemaVersion': 1, 'adapter': WALLABAG_EXPORT_ADAPTER, 'basisSha256': basis,
            'source': {'name': source['name'], 'sha256': source['sha256']}, 'summary': summary, 'mapping': mapping,
            'conflicts': conflicts, 'unrepresentedFields': unrepresented,
            'appliedDecisions': None if decisions is _MISSING else decisions, 'decisionOutcomes': outcomes,
            'roundTrip': {'verified': True, 'adapter': WALLABAG_EXPORT_ADAPTER, 'records': len(entries)},
            'warning': 'Offline native-field projection; not lossless. Keep this audit and provenance with the export. '
                       'Application import and annotation reattachment are not verified.',
        },
    }
