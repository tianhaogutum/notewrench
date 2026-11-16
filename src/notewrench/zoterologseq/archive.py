# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zotlog-sync contributors
"""Explicit, reviewable orphan archival; ordinary sync never calls this module."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .graph import graph_inventory
from .model import Invalid, MANIFEST_NAME, canonical, digest, load_items, strict_json
from .sync import (Conflict, Plan, atomic_write, graph_lock, load_manifest,
                   read_regular, safe_ancestors)

ARCHIVE_NAME = '.zotlog-sync-archive'


def absolute(path: Path) -> Path:
    # Do not resolve symlinks: they must be rejected, not silently followed.
    return Path(os.path.abspath(path))


def json_bytes(value: Any) -> bytes:
    return canonical(value).encode('utf-8')


def required_file(path: Path) -> bytes:
    data = read_regular(path)
    if data is None:
        raise Conflict(f'required file is missing: {path}')
    return data


def build_decision(source: Path, graph: Path) -> tuple[Plan, dict]:
    source_data = required_file(source)
    plan = Plan(graph, load_items(source_data))
    if plan.report['conflicts']:
        raise Conflict('archive preflight failed: ' + '; '.join(
            conflict['message'] for conflict in plan.report['conflicts']))
    manifest_data = plan.snapshots.get(MANIFEST_NAME)
    if manifest_data is None:
        raise Conflict('archive requires an existing ownership manifest; sync first')
    decision = {
        'version': 1, 'kind': 'zotlog-sync-archive', 'graph': str(graph),
        'source_sha256': digest(source_data), 'manifest_sha256': digest(manifest_data),
        'entries': [dict(entry, page_sha256=digest(plan.snapshots[entry['path']]))
                    for entry in plan.report['stale']],
    }
    decision['decision_sha256'] = digest(json_bytes(decision))
    return plan, decision


def plan_archive(source: Path, graph: Path, output: Path) -> dict:
    """Write a new decision file, with no graph writes or lock acquisition."""
    source, graph, output = absolute(source), absolute(graph), absolute(output)
    if output == source or output.is_relative_to(graph):
        raise Conflict('archive decision output must be outside the graph and differ from the source')
    safe_ancestors(output.parent)
    if output.exists() or output.is_symlink():
        raise Conflict(f'archive decision output already exists: {output}')
    _, decision = build_decision(source, graph)
    # O_EXCL also refuses dangling links and a file that appeared after preflight.
    try:
        fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    except FileExistsError as exc:
        raise Conflict(f'archive decision output already exists: {output}') from exc
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(json_bytes(decision))
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        output.unlink()
        raise
    return {'status': 'planned', 'dry_run': True, 'conflicts': [],
            'decision_file': str(output), 'decision_sha256': decision['decision_sha256'],
            'archive_candidates': decision['entries']}


def read_decision(path: Path) -> dict:
    value = strict_json(required_file(path), 'archive decision')
    fields = {'version', 'kind', 'graph', 'source_sha256', 'manifest_sha256',
              'entries', 'decision_sha256'}
    if (not isinstance(value, dict) or set(value) != fields
            or type(value['version']) is not int or value['version'] != 1
            or value['kind'] != 'zotlog-sync-archive'):
        raise Conflict('invalid archive decision format')
    body = {key: item for key, item in value.items() if key != 'decision_sha256'}
    if digest(json_bytes(body)) != value['decision_sha256']:
        raise Conflict('archive decision hash mismatch; create a new plan')
    return value


def validate_decision(expected: dict, current: dict) -> None:
    for field in ('graph', 'source_sha256', 'manifest_sha256', 'entries'):
        if json_bytes(expected[field]) != json_bytes(current[field]):
            raise Conflict(f'archive decision is obsolete or modified ({field}); create a new plan')
    # The fields above plus the strict version/kind/hash check bind the entire
    # document. No path or candidate supplied by the file is trusted independently.


def fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def move_page(source: Path, target: Path) -> None:
    """Same-filesystem rename preserves all bytes, mode, and inode."""
    if target.exists() or target.is_symlink():
        raise Conflict(f'archive move destination already exists: {target}')
    os.rename(source, target)
    fsync_directory(target.parent)
    fsync_directory(source.parent)


def check_current(plan: Plan, source: Path, decision: dict) -> None:
    if graph_inventory(plan.graph) != plan.inventory:
        raise Conflict('graph page inventory changed after archive preflight')
    for relative in plan.snapshots:
        plan.check_snapshot(relative)
    if digest(required_file(source)) != decision['source_sha256']:
        raise Conflict('source changed after archive preflight')


def apply_archive(source: Path, graph: Path, decision_file: Path) -> dict:
    """Revalidate an explicit plan under the sync lock, move pages, commit manifest."""
    source, graph, decision_file = map(absolute, (source, graph, decision_file))
    expected = read_decision(decision_file)
    _, current = build_decision(source, graph)
    validate_decision(expected, current)
    with graph_lock(graph):
        plan, current = build_decision(source, graph)
        validate_decision(expected, current)
        report = {'status': 'applied', 'dry_run': False, 'conflicts': [],
                  'decision_sha256': current['decision_sha256'],
                  'archived': current['entries'], 'archive_path': None}
        if not current['entries']:
            return report
        archive = graph / ARCHIVE_NAME
        batch = archive / current['decision_sha256']
        safe_ancestors(batch)
        if batch.exists():
            raise Conflict(f'archive batch already exists (inspect recovery evidence): {batch}')
        if source.is_relative_to(archive):
            raise Conflict('archive source must be outside the archive directory')
        before = plan.snapshots[MANIFEST_NAME]
        manifest = load_manifest(before)
        for entry in current['entries']:
            del manifest['entries'][entry['id']]
        after = json_bytes(manifest)
        receipt = {'version': 1, 'kind': 'zotlog-sync-archive-receipt',
                   'status': 'prepared', 'decision_sha256': current['decision_sha256'],
                   'manifest_before_sha256': digest(before),
                   'manifest_after_sha256': digest(after), 'entries': current['entries']}
        check_current(plan, source, current)
        archive.mkdir(mode=0o700, exist_ok=True)
        batch.mkdir(mode=0o700)  # Exclusive: never reuse/overwrite another batch.
        moved = []
        manifest_attempted = False
        try:
            (batch / 'pages').mkdir(mode=0o700)
            for name, data in (('plan.json', json_bytes(current)),
                               ('manifest-before.json', before), ('manifest-after.json', after),
                               ('receipt.json', json_bytes(receipt))):
                atomic_write(batch / name, data)
            fsync_directory(batch)
            fsync_directory(archive)
            fsync_directory(graph)
            check_current(plan, source, current)
            for entry in current['entries']:
                relative = entry['path']
                plan.check_snapshot(relative)
                # Track before rename to cover errors after the rename (fsync).
                moved.append(relative)
                move_page(graph / relative, batch / relative)
            plan.check_snapshot(MANIFEST_NAME)
            manifest_attempted = True
            atomic_write(graph / MANIFEST_NAME, after)
            receipt['status'] = 'complete'
            atomic_write(batch / 'receipt.json', json_bytes(receipt))
        except (OSError, Invalid) as exc:
            errors = []
            if manifest_attempted:
                try:
                    actual = read_regular(graph / MANIFEST_NAME)
                    if actual != before:
                        if actual != after:
                            raise Conflict('concurrent manifest edit prevents safe rollback')
                        atomic_write(graph / MANIFEST_NAME, before)
                except (OSError, Invalid) as rollback_exc:
                    errors.append(f'manifest: {rollback_exc}')
            for relative in reversed(moved):
                try:
                    original, archived = read_regular(graph / relative), read_regular(batch / relative)
                    old = plan.snapshots[relative]
                    if original == old and archived is None:
                        continue
                    if original is not None or archived != old:
                        raise Conflict('concurrent page edit prevents safe rollback')
                    move_page(batch / relative, graph / relative)
                except (OSError, Invalid) as rollback_exc:
                    errors.append(f'{relative}: {rollback_exc}')
            receipt['status'] = 'rollback-incomplete' if errors else 'rolled-back'
            try:
                atomic_write(batch / 'receipt.json', json_bytes(receipt))
            except (OSError, Invalid) as rollback_exc:
                errors.append(f'receipt: {rollback_exc}')
            if errors:
                raise OSError(f'archive failed ({exc}); rollback incomplete; evidence at {batch}: '
                              + '; '.join(errors)) from exc
            raise
        report['archive_path'] = batch.relative_to(graph).as_posix()
        return report
