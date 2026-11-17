# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 tianhaogutum
"""Explicit whole-batch recovery; exact manifest restoration, never a merge."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

from .archive import (ARCHIVE_NAME, absolute, fsync_directory, json_bytes,
                      move_page, read_decision, required_file)
from .graph import claims, graph_inventory
from .model import Invalid, MANIFEST_NAME, collision_key, digest, strict_json
from .sync import (Conflict, Plan, atomic_write, graph_lock, load_manifest,
                   read_regular, region, safe_ancestors)

RESTORE_RECEIPT = 'restore-receipt.json'


def new_file(path: Path, data: bytes) -> None:
    """Private exclusive evidence/decision creation; never replace another file."""
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    except FileExistsError as exc:
        raise Conflict(f'restore output already exists: {path}') from exc
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    fsync_directory(path.parent)


@dataclass
class RestoreState:
    live: Plan
    batch: Path
    evidence: dict[str, bytes]
    pages: dict[str, bytes]
    before: bytes

    def check_current(self) -> None:
        if graph_inventory(self.live.graph) != self.live.inventory:
            raise Conflict('graph page inventory changed after restore preflight')
        for relative in self.live.snapshots:
            self.live.check_snapshot(relative)
        for relative, expected in {**self.evidence, **self.pages}.items():
            if read_regular(self.batch / relative) != expected:
                raise Conflict(f'archive file changed after restore preflight: {relative}')
        for relative in self.pages:
            if read_regular(self.live.graph / relative) is not None:
                raise Conflict(f'restore destination changed after preflight: {relative}')


def build_restore_decision(batch: Path, graph: Path) -> tuple[RestoreState, dict]:
    safe_ancestors(batch)
    safe_ancestors(batch / 'pages')
    safe_ancestors(graph / 'pages')
    if batch.parent != graph / ARCHIVE_NAME or not re.fullmatch('[0-9a-f]{64}', batch.name):
        raise Conflict('restore requires a batch at its original graph archive location')
    if (batch / RESTORE_RECEIPT).exists() or (batch / RESTORE_RECEIPT).is_symlink():
        raise Conflict('restore already attempted; inspect restore receipt and recovery evidence')
    evidence = {name: required_file(batch / name) for name in
                ('plan.json', 'receipt.json', 'manifest-before.json', 'manifest-after.json')}
    archive = read_decision(batch / 'plan.json')
    if archive['graph'] != str(graph) or archive['decision_sha256'] != batch.name:
        raise Conflict('archive decision does not match this graph/batch')
    before = evidence['manifest-before.json']
    after = evidence['manifest-after.json']
    original = load_manifest(before)
    load_manifest(after)
    if digest(before) != archive['manifest_sha256']:
        raise Conflict('archive pre-manifest hash mismatch')
    entries = archive['entries']
    if not isinstance(entries, list) or not entries:
        raise Conflict('restore requires a nonempty completed archive batch')
    remaining = load_manifest(before)
    pages = {}
    ids = []
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {'id', 'path', 'page_sha256'}
                or not isinstance(entry['id'], str) or entry['id'] not in remaining['entries']
                or entry['path'] != remaining['entries'][entry['id']]['path']
                or not isinstance(entry['page_sha256'], str)
                or not re.fullmatch('[0-9a-f]{64}', entry['page_sha256'])):
            raise Conflict('invalid archive candidate; cannot restore arbitrary paths or duplicates')
        key, relative = entry['id'], entry['path']
        owned = remaining['entries'].pop(key)
        data = required_file(batch / relative)
        if digest(data) != entry['page_sha256']:
            raise Conflict(f'archived page hash mismatch: {relative}')
        region(data, key, owned['managed_sha256'])
        pages[relative] = data
        ids.append(key)
    if ids != sorted(ids) or json_bytes(remaining) != after:
        raise Conflict('archive manifest transition does not match the entire ordered batch')
    expected_receipt = {
        'version': 1, 'kind': 'zotlog-sync-archive-receipt', 'status': 'complete',
        'decision_sha256': archive['decision_sha256'], 'manifest_before_sha256': digest(before),
        'manifest_after_sha256': digest(after), 'entries': entries,
    }
    if json_bytes(strict_json(evidence['receipt.json'], 'archive receipt')) != json_bytes(expected_receipt):
        raise Conflict('restore requires an intact complete archive receipt')
    live = Plan(graph, [])
    if live.snapshots.get(MANIFEST_NAME) != after:
        raise Conflict('live manifest differs from archived post-manifest; automatic merge is unsupported')
    if live.report['conflicts']:
        raise Conflict('restore graph preflight failed: ' + '; '.join(
            item['message'] for item in live.report['conflicts']))
    for relative in pages:
        if read_regular(graph / relative) is not None:
            raise Conflict(f'restore destination already exists: {relative}')
    # Check the combined graph, including aliases from pages about to return.
    # A filename/title/alias never establishes ownership or authorizes overwrite.
    reservations = {}
    combined = {relative: live.snapshots[relative] for relative in live.inventory}
    combined.update(pages)
    for relative, data in combined.items():
        for claim in claims(relative, data):
            reservations.setdefault(collision_key(claim.value), []).append(claim)
    for key, owned in original['entries'].items():
        for claim in reservations.get(collision_key(key), []):
            if claim.path != owned['path']:
                raise Conflict(f'restore graph identity collision: {key!r} at {owned["path"]} '
                               f'with {claim.path}:{claim.line} {claim.property} {claim.declared!r}')
    decision = {
        'version': 1, 'kind': 'zotlog-sync-restore', 'graph': str(graph),
        'archive_path': batch.relative_to(graph).as_posix(),
        'archive_decision_sha256': archive['decision_sha256'],
        'manifest_current_sha256': digest(after), 'manifest_restored_sha256': digest(before),
        'evidence_sha256': {name: digest(data) for name, data in evidence.items()},
        'entries': entries,
    }
    decision['decision_sha256'] = digest(json_bytes(decision))
    return RestoreState(live, batch, evidence, pages, before), decision


def plan_restore(batch: Path, graph: Path, output: Path) -> dict:
    batch, graph, output = map(absolute, (batch, graph, output))
    if output.is_relative_to(graph):
        raise Conflict('restore decision output must be outside the graph')
    safe_ancestors(output.parent)
    if output.exists() or output.is_symlink():
        raise Conflict(f'restore decision output already exists: {output}')
    _, decision = build_restore_decision(batch, graph)
    new_file(output, json_bytes(decision))
    return {'status': 'planned', 'dry_run': True, 'conflicts': [],
            'decision_file': str(output), 'decision_sha256': decision['decision_sha256'],
            'archive_path': decision['archive_path'], 'restore_candidates': decision['entries']}


def validate_restore_decision(expected: dict, current: dict) -> None:
    # Compare the whole reconstructed document, never trust paths from the input.
    if json_bytes(expected) != json_bytes(current):
        raise Conflict('restore decision is obsolete or modified; create a new plan')


def apply_restore(batch: Path, graph: Path, decision_file: Path) -> dict:
    batch, graph, decision_file = map(absolute, (batch, graph, decision_file))
    safe_ancestors(decision_file.parent)
    expected = strict_json(required_file(decision_file), 'restore decision')
    _, current = build_restore_decision(batch, graph)
    validate_restore_decision(expected, current)
    with graph_lock(graph):
        state, current = build_restore_decision(batch, graph)
        validate_restore_decision(expected, current)
        receipt = {'version': 1, 'kind': 'zotlog-sync-restore-receipt', 'status': 'prepared',
                   'decision_sha256': current['decision_sha256'],
                   'manifest_before_sha256': current['manifest_current_sha256'],
                   'manifest_after_sha256': current['manifest_restored_sha256'],
                   'entries': current['entries']}
        state.check_current()
        moved = []
        manifest_attempted = False
        # A partially written prepared receipt is retained on creation/fsync error.
        new_file(batch / RESTORE_RECEIPT, json_bytes(receipt))
        after = state.evidence['manifest-after.json']
        try:
            state.check_current()
            for relative, data in state.pages.items():
                if required_file(batch / relative) != data:
                    raise Conflict(f'archive page changed after restore preflight: {relative}')
                moved.append(relative)  # Include post-rename fsync failures.
                move_page(batch / relative, graph / relative)
            state.live.check_snapshot(MANIFEST_NAME)
            manifest_attempted = True
            atomic_write(graph / MANIFEST_NAME, state.before)
            receipt['status'] = 'complete'
            atomic_write(batch / RESTORE_RECEIPT, json_bytes(receipt))
        except (OSError, Invalid) as exc:
            errors = []
            if manifest_attempted:
                try:
                    actual = read_regular(graph / MANIFEST_NAME)
                    if actual != after:
                        if actual != state.before:
                            raise Conflict('concurrent manifest edit prevents safe rollback')
                        atomic_write(graph / MANIFEST_NAME, after)
                except (OSError, Invalid) as rollback_exc:
                    errors.append(f'manifest: {rollback_exc}')
            for relative in reversed(moved):
                try:
                    live = read_regular(graph / relative)
                    archived = read_regular(batch / relative)
                    old = state.pages[relative]
                    if live is None and archived == old:
                        continue
                    if live != old or archived is not None:
                        raise Conflict('concurrent page edit prevents safe rollback')
                    move_page(graph / relative, batch / relative)
                except (OSError, Invalid) as rollback_exc:
                    errors.append(f'{relative}: {rollback_exc}')
            receipt['status'] = 'rollback-incomplete' if errors else 'rolled-back'
            try:
                atomic_write(batch / RESTORE_RECEIPT, json_bytes(receipt))
            except (OSError, Invalid) as rollback_exc:
                errors.append(f'receipt: {rollback_exc}')
            if errors:
                raise OSError(f'restore failed ({exc}); rollback incomplete; evidence at {batch}: '
                              + '; '.join(errors)) from exc
            raise
        return {'status': 'applied', 'dry_run': False, 'conflicts': [],
                'decision_sha256': current['decision_sha256'],
                'restored': current['entries'], 'archive_path': current['archive_path']}
