# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zotlog-sync contributors
"""Read-only observations of archive/restore attempts. No repair authorization."""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat

from .archive import ARCHIVE_NAME, absolute, json_bytes
from .model import Invalid, MANIFEST_NAME, digest, strict_json
from .restore import RESTORE_RECEIPT
from .sync import Conflict, load_manifest, read_regular_snapshot, region, safe_ancestors

STATUSES = {'prepared', 'complete', 'rolled-back', 'rollback-incomplete'}


def directory_snapshot(path: Path) -> tuple[int, int] | None:
    """Observe only this directory's identity; never list it or follow links."""
    safe_ancestors(path.parent)
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(info.st_mode):
        raise Conflict(f'expected a non-linked directory: {path}')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    try:
        opened = os.fstat(fd)
        if (not stat.S_ISDIR(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)):
            raise Conflict(f'directory changed during observation: {path}')
        return opened.st_dev, opened.st_ino
    finally:
        os.close(fd)


class AuditReads:
    """Remember safety, byte hashes and opened identity; include optional absence."""

    def __init__(self):
        self.observations: dict[Path, tuple[str, str | None, tuple[int, int] | None]] = {}
        self.directories: dict[Path, tuple[str, str | None, tuple[int, int] | None]] = {}
        self.initial_directory_errors: dict[Path, str] = {}
        self.initial_unsafe_directories: dict[Path, str] = {}

    def read(self, path: Path) -> bytes | None:
        try:
            safe_ancestors(path.parent)
            data, identity = read_regular_snapshot(path)
        except Invalid as exc:
            self.observations[path] = ('unsafe', str(exc), None)
            raise
        self.observations[path] = (('missing', None, None) if data is None else
                                   ('present', digest(data), identity))
        return data

    def directory(self, path: Path) -> None:
        try:
            identity = directory_snapshot(path)
        except Invalid as exc:
            self.directories[path] = ('unsafe', str(exc), None)
            raise
        self.directories[path] = (('missing', None, None) if identity is None else
                                  ('present', None, identity))

    def recheck(self, report: dict) -> set[Path]:
        """Compare only; return paths whose stability could not be confirmed."""
        current = AuditReads()
        uncertain = set()
        checks = [(path, initial, current.read, current.observations)
                  for path, initial in self.observations.items()]
        checks += [(path, initial, current.directory, current.directories)
                   for path, initial in self.directories.items()]
        for path, initial, observe_again, observations in checks:
            try:
                observe_again(path)
            except Invalid as exc:
                if observations[path] != initial:
                    report['conflicts'].append(
                        {'code': 'unsafe-evidence', 'path': str(path), 'message': str(exc)})
            except OSError as exc:
                report.setdefault('recheck_errors', []).append(
                    {'code': 'unreadable-during-recheck', 'path': str(path), 'message': str(exc)})
                uncertain.add(path)
                continue  # Unreadable is neither missing nor an observed change.
            if observations[path] != initial:
                uncertain.add(path)
                report['conflicts'].append(
                    {'code': 'changed-during-audit', 'path': str(path),
                     'message': 'evidence changed during audit; initial observation retained'})
        return uncertain


def required(path: Path, reads: AuditReads) -> bytes:
    data = reads.read(path)
    if data is None:
        raise Conflict(f'required audit evidence is missing: {path}')
    return data


def receipt_status(data: bytes, kind: str, decision_hash: str,
                   before: bytes, after: bytes, entries: list) -> str:
    value = strict_json(data, f'{kind} receipt')
    expected = {'version': 1, 'kind': f'zotlog-sync-{kind}-receipt',
                'decision_sha256': decision_hash, 'manifest_before_sha256': digest(before),
                'manifest_after_sha256': digest(after), 'entries': entries}
    if (not isinstance(value, dict) or set(value) != set(expected) | {'status'}
            or type(value['version']) is not int or not isinstance(value['status'], str)
            or value['status'] not in STATUSES
            or json_bytes({k: v for k, v in value.items() if k != 'status'}) != json_bytes(expected)):
        raise Conflict(f'inconsistent {kind} receipt')
    return value['status']


def evidence(batch: Path, graph: Path, reads: AuditReads) -> tuple[dict, dict, bytes, bytes, dict]:
    """Validate metadata and derive all paths before observing any candidate."""
    # Failed bounded parents have no trusted fingerprint. Defer diagnostics
    # until independent metadata validates; never retry them or their children.
    for path in (graph / 'pages', batch / 'pages'):
        try:
            reads.directory(path)
        except Invalid as exc:
            reads.initial_unsafe_directories[path] = str(exc)
            reads.directories.pop(path, None)
        except OSError as exc:
            reads.initial_directory_errors[path] = str(exc)
    if batch.parent != graph / ARCHIVE_NAME or not re.fullmatch('[0-9a-f]{64}', batch.name):
        raise Conflict('audit requires a batch at its original graph archive location')
    saved = {name: required(batch / name, reads) for name in
             ('plan.json', 'receipt.json', 'manifest-before.json', 'manifest-after.json')}
    archive = strict_json(saved['plan.json'], 'archive decision')
    fields = {'version', 'kind', 'graph', 'source_sha256', 'manifest_sha256',
              'entries', 'decision_sha256'}
    if (not isinstance(archive, dict) or set(archive) != fields
            or type(archive['version']) is not int or archive['version'] != 1
            or archive['kind'] != 'zotlog-sync-archive'):
        raise Conflict('invalid archive decision format')
    if digest(json_bytes({k: v for k, v in archive.items() if k != 'decision_sha256'})) != archive['decision_sha256']:
        raise Conflict('archive decision hash mismatch')
    if (archive['graph'] != str(graph) or archive['decision_sha256'] != batch.name
            or not isinstance(archive['source_sha256'], str)
            or not re.fullmatch('[0-9a-f]{64}', archive['source_sha256'])):
        raise Conflict('archive decision does not match this graph/batch or source hash')
    before, after = saved['manifest-before.json'], saved['manifest-after.json']
    original, remaining = load_manifest(before), load_manifest(before)
    load_manifest(after)
    if digest(before) != archive['manifest_sha256']:
        raise Conflict('archive pre-manifest hash mismatch')
    entries = archive['entries']
    if not isinstance(entries, list) or not entries:
        raise Conflict('audit requires a nonempty archive batch')
    ids = []
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {'id', 'path', 'page_sha256'}
                or not isinstance(entry['id'], str) or entry['id'] not in remaining['entries']
                or entry['path'] != remaining['entries'][entry['id']]['path']
                or not isinstance(entry['page_sha256'], str)
                or not re.fullmatch('[0-9a-f]{64}', entry['page_sha256'])):
            raise Conflict('invalid archive candidate; arbitrary paths or duplicates are unsupported')
        remaining['entries'].pop(entry['id'])
        ids.append(entry['id'])
    if ids != sorted(ids) or json_bytes(remaining) != after:
        raise Conflict('archive manifest transition does not match the entire ordered batch')
    archive_status = receipt_status(saved['receipt.json'], 'archive', archive['decision_sha256'],
                                    before, after, entries)
    restore_status = None
    restore_data = reads.read(batch / RESTORE_RECEIPT)
    if restore_data is not None:
        if archive_status != 'complete':
            raise Conflict('restore receipt requires a complete archive receipt')
        # Reconstruct the original restore decision without requiring either page
        # location or the current manifest to be in its completed state.
        restore = {
            'version': 1, 'kind': 'zotlog-sync-restore', 'graph': str(graph),
            'archive_path': batch.relative_to(graph).as_posix(),
            'archive_decision_sha256': archive['decision_sha256'],
            'manifest_current_sha256': digest(after), 'manifest_restored_sha256': digest(before),
            'evidence_sha256': {name: digest(data) for name, data in saved.items()},
            'entries': entries,
        }
        restore_status = receipt_status(restore_data, 'restore', digest(json_bytes(restore)),
                                        after, before, entries)
    return archive, original, before, after, {'archive': archive_status, 'restore': restore_status}


def observe(path: Path, report: dict, reads: AuditReads) -> tuple[bytes | None, dict]:
    observation = {'presence': 'missing', 'sha256': None, 'diagnostic': None}
    try:
        data = reads.read(path)
    except Invalid as exc:
        observation.update(presence='unsafe', diagnostic=str(exc))
        report['conflicts'].append({'code': 'unsafe-evidence', 'path': str(path), 'message': str(exc)})
        return None, observation
    except OSError as exc:
        # Metadata has already validated. This path has no initial fingerprint
        # to compare: retain an explicit unknown and continue independent reads.
        observation.update(presence='unreadable', diagnostic=str(exc))
        report.setdefault('initial_errors', []).append(
            {'code': 'unreadable-initial-evidence', 'path': str(path), 'message': str(exc)})
        return None, observation
    if data is not None:
        observation.update(presence='present', sha256=digest(data))
    return data, observation


def observe_page(path: Path, key: str, full_hash: str, managed_hash: str,
                 report: dict, reads: AuditReads) -> dict:
    if path.parent in reads.initial_unsafe_directories:
        return {'presence': 'unsafe', 'sha256': None,
                'diagnostic': f'initial parent directory is unsafe: {path.parent}',
                'full_hash': 'unknown', 'ownership': 'unknown'}
    if path.parent in reads.initial_directory_errors:
        # No descendant observation is safe through an unknown parent. This is
        # neither evidence of absence nor an individual file-read failure.
        return {'presence': 'unreadable', 'sha256': None,
                'diagnostic': f'initial parent directory observation unavailable: {path.parent}',
                'full_hash': 'unknown', 'ownership': 'unknown'}
    data, result = observe(path, report, reads)
    result.update(full_hash='unknown', ownership='unknown')
    if data is not None:
        result['full_hash'] = 'match' if result['sha256'] == full_hash else 'mismatch'
        result['ownership'] = 'match'
        try:
            region(data, key, managed_hash)
        except Invalid:
            result.update(ownership='mismatch', diagnostic='managed-region/provenance does not match saved ownership')
        if result['full_hash'] == 'mismatch' and result['diagnostic'] is None:
            result['diagnostic'] = 'full-file hash differs from saved candidate; content outside ownership may have changed'
    return result


def ambiguity_reasons(report: dict, batch: Path, graph: Path) -> None:
    """Project retained observations/diagnostics only; preserve all classifications.

    Receipt direction is report context, not a new claim of row ambiguity or
    completion. Diagnostic paths propagate only to a candidate or its parent.
    """
    reasons = report['ambiguity_reasons'] = []
    seen = set()

    def append(items, known, reason):
        identity = tuple(sorted(reason.items()))
        if identity not in known:
            known.add(identity)
            items.append(reason)

    receipt = 'restore' if report['receipts']['restore'] is not None else 'archive'
    status = report['receipts'][receipt]
    context = {'receipt': receipt, 'status': status}
    expected_location = ('original-only' if (receipt == 'restore') == (status == 'complete')
                         else 'archive-only')
    expected_manifest = 'before' if expected_location == 'original-only' else 'after'
    if status not in {'complete', 'rolled-back'}:
        append(reasons, seen, {'code': 'incomplete-receipt',
                              'path': str(batch / (RESTORE_RECEIPT if receipt == 'restore' else 'receipt.json')),
                              **context})
    if report['manifest']['classification'] != expected_manifest:
        append(reasons, seen, {'code': 'manifest-mismatch', 'path': str(graph / MANIFEST_NAME),
                              **context, 'expected': expected_manifest,
                              'observed': report['manifest']['classification']})
    rows = []
    for page in report['pages']:
        original, archived = graph / page['path'], batch / page['path']
        copies = {'id': page['id'], 'original_path': str(original), 'archive_path': str(archived)}
        row, row_seen = [], set()
        page['ambiguity_reasons'] = row
        rows.append((row, row_seen, {str(original), str(archived),
                                     str(original.parent), str(archived.parent)}))
        if page['locations'] != expected_location:
            append(reasons, seen, {'code': 'location-mismatch', **copies, **context,
                                  'expected': expected_location, 'observed': page['locations']})
        if page['locations'] in {'both', 'missing'}:
            append(row, row_seen, {'code': 'both-copies' if page['locations'] == 'both' else 'missing-copies',
                                   **copies})
        for location, path in (('original', original), ('archive', archived)):
            observed = page[location]
            candidate = {'id': page['id'], 'path': str(path), 'location': location}
            if observed['full_hash'] == 'mismatch':
                append(row, row_seen, {'code': 'full-hash-mismatch', **candidate,
                                      'expected': page['expected_sha256'], 'observed': observed['sha256']})
            if observed['ownership'] == 'mismatch':
                append(row, row_seen, {'code': 'ownership-mismatch', **candidate})
        for reason in row:
            append(reasons, seen, reason)
    for field in ('conflicts', 'initial_errors', 'recheck_errors'):
        for diagnostic in report.get(field, []):
            reason = {'code': diagnostic['code'], 'path': diagnostic['path']}
            append(reasons, seen, reason)
            for row, row_seen, paths in rows:
                if diagnostic['path'] in paths:
                    append(row, row_seen, reason)


def audit_recovery(batch: Path, graph: Path) -> dict:
    """Inspect only; do not construct Plan, lock, or call any writing primitive."""
    if '..' in batch.parts or '..' in graph.parts:
        raise Conflict('parent traversal is unsupported in audit paths')
    batch, graph = map(absolute, (batch, graph))
    reads = AuditReads()
    archive, original, before, after, receipts = evidence(batch, graph, reads)
    report = {'version': 1, 'status': 'audited', 'read_only': True, 'conflicts': [],
              'archive_path': batch.relative_to(graph).as_posix(), 'receipts': receipts,
              'pages': [], 'ambiguous': False}
    report['conflicts'] = [
        {'code': 'unsafe-initial-directory', 'path': str(path), 'message': message}
        for path, message in reads.initial_unsafe_directories.items()]
    if reads.initial_directory_errors:
        report['initial_errors'] = [
            {'code': 'unreadable-initial-directory', 'path': str(path), 'message': message}
            for path, message in reads.initial_directory_errors.items()]
    live, manifest = observe(graph / MANIFEST_NAME, report, reads)
    manifest['classification'] = ('unknown' if manifest['presence'] == 'unreadable' else
                                  'before' if live == before else 'after' if live == after else 'neither')
    report['manifest'] = manifest
    for entry in archive['entries']:
        key, relative = entry['id'], entry['path']
        managed = original['entries'][key]['managed_sha256']
        current = observe_page(graph / relative, key, entry['page_sha256'], managed, report, reads)
        archived = observe_page(batch / relative, key, entry['page_sha256'], managed, report, reads)
        presences = (current['presence'], archived['presence'])
        locations = ('unknown' if 'unreadable' in presences else
                     'unsafe' if 'unsafe' in presences else
                     'both' if presences == ('present', 'present') else
                     'original-only' if presences[0] == 'present' else
                     'archive-only' if presences[1] == 'present' else 'missing')
        ambiguous = (locations in {'unknown', 'unsafe', 'both', 'missing'} or any(
            item[field] == 'mismatch' for item in (current, archived) for field in ('full_hash', 'ownership')))
        report['pages'].append({'id': key, 'path': relative, 'expected_sha256': entry['page_sha256'],
                                'original': current, 'archive': archived,
                                'locations': locations, 'ambiguous': ambiguous})
    status = receipts['restore'] or receipts['archive']
    expected_location = ('original-only' if (receipts['restore'] is not None) == (status == 'complete')
                         else 'archive-only')
    expected_manifest = 'before' if expected_location == 'original-only' else 'after'
    report['ambiguous'] = (status not in {'complete', 'rolled-back'} or
                           manifest['classification'] != expected_manifest or any(
                               page['ambiguous'] or page['locations'] != expected_location
                               for page in report['pages']))
    uncertain = reads.recheck(report)
    if uncertain:
        report['ambiguous'] = True
        for page in report['pages']:
            if any(path in uncertain or path.parent in uncertain
                   for path in (graph / page['path'], batch / page['path'])):
                page['ambiguous'] = True
    if report.get('initial_errors') or report.get('recheck_errors'):
        report['ambiguous'] = True
        report['status'] = 'partial'
    elif report['conflicts']:
        report['status'] = 'conflict'
    ambiguity_reasons(report, batch, graph)
    return report
