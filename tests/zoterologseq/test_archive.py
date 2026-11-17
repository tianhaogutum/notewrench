# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 tianhaogutum
"""Synthetic file-graph integration checks for explicit orphan archival."""
import copy
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from notewrench.zoterologseq import Invalid, synchronize
from notewrench.zoterologseq.model import LOCK_NAME, MANIFEST_NAME, canonical, digest

ROOT = Path(__file__).resolve().parents[2]
RECORDS = [{'id': 'Keep', 'type': 'book', 'title': 'Original'},
           {'id': 'Gone', 'type': 'book', 'title': 'Removed'}]


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'library.json'
        self.graph = self.root / 'graph'
        self.decision = self.root / 'archive.json'
        self.write_source(RECORDS)
        synchronize(self.source, self.graph)
        self.write_source(RECORDS[:1])

    def write_source(self, records):
        self.source.write_text(canonical(records), encoding='utf-8')

    def snapshot(self):
        return {p.relative_to(self.graph).as_posix(): p.read_bytes()
                for p in self.graph.rglob('*') if p.is_file()}

    def cli(self, mode, path=None, expected=0):
        result = subprocess.run(
            [sys.executable, '-m', 'notewrench', 'zotero-logseq', str(self.source), '--graph',
             str(self.graph), mode, str(path or self.decision)],
            cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def plan(self):
        report = self.cli('--archive-plan')
        self.assertEqual('planned', report['status'])
        self.assertTrue(report['dry_run'])
        return json.loads(self.decision.read_bytes())

    def test_archive_plan_is_read_only_and_only_lists_valid_stale_pages(self):
        page = self.graph / 'pages/Gone.md'
        page.write_bytes(page.read_bytes() + '\n- 人工笔记 without final newline'.encode())
        (self.graph / 'pages/Untracked.md').write_text('- Handwritten\n')
        self.write_source([dict(RECORDS[0], title='Pending update')])
        before = self.snapshot()
        mtimes = {p: p.stat().st_mtime_ns for p in self.graph.rglob('*')}
        decision = self.plan()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(mtimes, {p: p.stat().st_mtime_ns for p in mtimes})
        self.assertEqual([{'id': 'Gone', 'path': 'pages/Gone.md',
                           'page_sha256': digest(page.read_bytes())}], decision['entries'])
        self.assertEqual(digest(before[MANIFEST_NAME]), decision['manifest_sha256'])
        self.assertEqual(digest(self.source.read_bytes()), decision['source_sha256'])
        self.assertEqual(str(self.graph), decision['graph'])
        self.assertEqual(0o600, self.decision.stat().st_mode & 0o777)
        self.cli('--archive-apply')
        self.assertEqual(before['pages/Keep.md'], (self.graph / 'pages/Keep.md').read_bytes())
        self.assertEqual(before['pages/Untracked.md'], (self.graph / 'pages/Untracked.md').read_bytes())

    def test_archive_apply_moves_with_recovery_evidence_and_manual_restore(self):
        page = self.graph / 'pages/Gone.md'
        note = '\n- 人工笔记 (no newline)'.encode()
        page.write_bytes(page.read_bytes() + note)
        page.chmod(0o640)
        inode = page.stat().st_ino
        before = self.snapshot()
        decision = self.plan()
        result = self.cli('--archive-apply')
        batch = self.graph / result['archive_path']
        archived = batch / 'pages/Gone.md'
        self.assertEqual('applied', result['status'])
        self.assertEqual(decision['entries'], result['archived'])
        self.assertFalse(page.exists())
        self.assertEqual(before['pages/Gone.md'], archived.read_bytes())
        self.assertEqual(inode, archived.stat().st_ino)
        self.assertEqual(0o640, archived.stat().st_mode & 0o777)
        self.assertEqual(before['pages/Keep.md'], (self.graph / 'pages/Keep.md').read_bytes())
        self.assertEqual(before[MANIFEST_NAME], (batch / 'manifest-before.json').read_bytes())
        current = (self.graph / MANIFEST_NAME).read_bytes()
        self.assertEqual(current, (batch / 'manifest-after.json').read_bytes())
        self.assertEqual({'Keep'}, set(json.loads(current)['entries']))
        self.assertEqual(decision, json.loads((batch / 'plan.json').read_bytes()))
        receipt = json.loads((batch / 'receipt.json').read_bytes())
        self.assertEqual('complete', receipt['status'])
        self.assertEqual(digest(current), receipt['manifest_after_sha256'])
        self.assertEqual(digest(before[MANIFEST_NAME]), receipt['manifest_before_sha256'])
        self.assertFalse(synchronize(self.source, self.graph)['stale'])
        # Documented immediate manual recovery: hash-check both snapshots and each
        # page, require original paths absent, then move back and restore manifest.
        self.assertEqual(receipt['manifest_after_sha256'], digest(current))
        self.assertEqual(decision['entries'][0]['page_sha256'], digest(archived.read_bytes()))
        self.assertFalse(page.exists())
        archived.rename(page)
        (self.graph / MANIFEST_NAME).write_bytes((batch / 'manifest-before.json').read_bytes())
        self.write_source(RECORDS)
        self.assertEqual(['pages/Gone.md', 'pages/Keep.md'],
                         synchronize(self.source, self.graph)['unchanged'])
        self.assertTrue(page.read_bytes().endswith(note))

    def test_archive_rejects_page_edits_missing_files_manifest_and_input_changes(self):
        self.plan()
        page = self.graph / 'pages/Gone.md'
        manifest = self.graph / MANIFEST_NAME
        original_page, original_manifest = page.read_bytes(), manifest.read_bytes()
        changes = ('notes', 'managed', 'provenance', 'missing', 'manifest', 'source')
        for change in changes:
            with self.subTest(change=change):
                page.write_bytes(original_page)
                manifest.write_bytes(original_manifest)
                self.write_source(RECORDS[:1])
                if change == 'notes':
                    page.write_bytes(original_page + b'\n- New handwritten note\n')
                elif change == 'managed':
                    page.write_bytes(original_page.replace(b'Removed', b'Hand edited', 1))
                elif change == 'provenance':
                    page.write_bytes(original_page.replace(b'citekey:: Gone', b'citekey:: Other'))
                elif change == 'missing':
                    page.unlink()
                elif change == 'manifest':
                    manifest.write_bytes(original_manifest + b'\n')
                else:
                    self.write_source(RECORDS)
                before = self.snapshot()
                self.cli('--archive-apply', expected=2)
                self.assertEqual(before, self.snapshot())
                self.assertFalse((self.graph / '.zotlog-sync-archive').exists())

    def test_archive_planning_rejects_missing_or_modified_managed_page(self):
        page = self.graph / 'pages/Gone.md'
        original = page.read_bytes()
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    page.unlink()
                else:
                    page.write_bytes(original.replace(b'Removed', b'Edited', 1))
                before = self.snapshot()
                self.cli('--archive-plan', expected=2)
                self.assertFalse(self.decision.exists())
                self.assertEqual(before, self.snapshot())
        page.write_bytes(original)
        (self.graph / MANIFEST_NAME).unlink()
        self.cli('--archive-plan', expected=2)
        self.assertFalse(self.decision.exists())

    def test_archive_plan_requires_existing_manifest_and_never_creates_graph(self):
        self.graph = self.root / 'absent'
        self.cli('--archive-plan', expected=2)
        self.assertFalse(self.graph.exists())
        self.assertFalse(self.decision.exists())

    def test_archive_empty_plan_is_noop_and_default_sync_retains_stale(self):
        before = self.snapshot()
        synchronize(self.source, self.graph)
        self.assertEqual(before, self.snapshot())
        self.write_source(RECORDS)
        self.assertEqual([], self.plan()['entries'])
        self.assertEqual([], self.cli('--archive-apply')['archived'])
        self.assertEqual(before, self.snapshot())

    def test_archive_tampered_decision_cannot_target_active_or_unsafe_paths(self):
        original = self.plan()
        for field, value in (('path', '../outside.md'), ('path', 'pages/Keep.md'),
                             ('id', 'Keep'), ('page_sha256', '0' * 64)):
            with self.subTest(field=field, value=value):
                changed = copy.deepcopy(original)
                changed['entries'][0][field] = value
                changed['decision_sha256'] = digest(canonical({
                    k: v for k, v in changed.items() if k != 'decision_sha256'}).encode())
                self.decision.write_text(canonical(changed))
                before = self.snapshot()
                self.cli('--archive-apply', expected=2)
                self.assertEqual(before, self.snapshot())
        changed = copy.deepcopy(original)
        changed['entries'] = []
        changed['decision_sha256'] = digest(canonical({
            k: v for k, v in changed.items() if k != 'decision_sha256'}).encode())
        self.decision.write_text(canonical(changed))
        self.cli('--archive-apply', expected=2)

    def test_archive_decision_is_bound_to_graph_and_checks_its_own_hash(self):
        decision = self.plan()
        other = self.root / 'other-graph'
        self.write_source(RECORDS)
        synchronize(self.source, other)
        self.write_source(RECORDS[:1])
        self.graph = other
        before = self.snapshot()
        self.cli('--archive-apply', expected=2)
        self.assertEqual(before, self.snapshot())
        self.graph = self.root / 'graph'
        decision['decision_sha256'] = '0' * 64
        self.decision.write_text(canonical(decision))
        self.cli('--archive-apply', expected=2)
        self.decision.write_bytes(b'{"version":1,"version":1}')
        self.cli('--archive-apply', expected=2)

    def test_archive_one_changed_candidate_blocks_the_whole_batch(self):
        self.write_source([])
        self.plan()
        page = self.graph / 'pages/Keep.md'
        page.write_bytes(page.read_bytes() + b'\n- New notes\n')
        before = self.snapshot()
        self.cli('--archive-apply', expected=2)
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.graph / '.zotlog-sync-archive').exists())

    def test_archive_nonregular_pages_and_existing_batch_are_rejected(self):
        decision = self.plan()
        page = self.graph / 'pages/Gone.md'
        outside = self.root / 'outside.md'
        page.rename(outside)
        for kind in ('symlink', 'hardlink', 'fifo'):
            with self.subTest(kind=kind):
                if kind == 'symlink':
                    page.symlink_to(outside)
                elif kind == 'hardlink':
                    os.link(outside, page)
                else:
                    os.mkfifo(page)
                self.cli('--archive-apply', expected=2)
                page.unlink()
        outside.rename(page)
        batch = self.graph / '.zotlog-sync-archive' / decision['decision_sha256']
        batch.mkdir(parents=True)
        (batch / 'handwritten').write_bytes(b'untouched')
        before = self.snapshot()
        self.cli('--archive-apply', expected=2)
        self.assertEqual(before, self.snapshot())

    def test_archive_rechecks_source_pages_and_inventory_after_preflight(self):
        from notewrench.zoterologseq.archive import apply_archive, build_decision
        self.plan()
        for change in ('source', 'page', 'inventory'):
            with self.subTest(change=change):
                self.write_source(RECORDS[:1])
                page = self.graph / 'pages/Gone.md'
                original = page.read_bytes()
                added = self.graph / 'pages/Reading.md'
                calls = 0
                def mutate_after_locked_preflight(source, graph):
                    nonlocal calls
                    calls += 1
                    result = build_decision(source, graph)
                    if calls == 2:
                        if change == 'source':
                            self.write_source(RECORDS)
                        elif change == 'page':
                            page.write_bytes(original + b'\n- Concurrent notes\n')
                        else:
                            added.write_bytes(b'- New page\n')
                    return result
                manifest_before = (self.graph / MANIFEST_NAME).read_bytes()
                with patch('notewrench.zoterologseq.archive.build_decision', side_effect=mutate_after_locked_preflight):
                    with self.assertRaisesRegex(Invalid, 'changed after'):
                        apply_archive(self.source, self.graph, self.decision)
                self.assertEqual(manifest_before, (self.graph / MANIFEST_NAME).read_bytes())
                self.assertFalse((self.graph / '.zotlog-sync-archive').exists())
                if change == 'page':
                    self.assertTrue(page.read_bytes().endswith(b'Concurrent notes\n'))
                    page.write_bytes(original)
                if change == 'inventory':
                    self.assertEqual(b'- New page\n', added.read_bytes())
                    added.unlink()

    def test_archive_plan_output_refuses_overwrite_and_graph_or_source_paths(self):
        self.decision.write_bytes(b'handwritten')
        before = self.snapshot()
        self.cli('--archive-plan', expected=2)
        self.assertEqual(b'handwritten', self.decision.read_bytes())
        for path in (self.graph / 'plan.json', self.graph / 'pages/Gone.md', self.source):
            with self.subTest(path=path):
                self.cli('--archive-plan', path, expected=2)
        self.assertEqual(before, self.snapshot())
        self.decision.unlink()
        self.decision.symlink_to(self.source)
        self.cli('--archive-plan', expected=2)

    def test_archive_apply_refuses_reuse_and_symlinked_archive_directory(self):
        self.plan()
        outside = self.root / 'outside'
        outside.mkdir()
        archive = self.graph / '.zotlog-sync-archive'
        archive.symlink_to(outside, target_is_directory=True)
        self.cli('--archive-apply', expected=2)
        self.assertEqual([], list(outside.iterdir()))
        archive.unlink()
        result = self.cli('--archive-apply')
        before = self.snapshot()
        self.cli('--archive-apply', expected=2)
        self.assertEqual(before, self.snapshot())
        self.assertTrue((self.graph / result['archive_path'] / 'pages/Gone.md').exists())

    def test_archive_apply_uses_sync_lock(self):
        self.plan()
        before = self.snapshot()
        with (self.graph / LOCK_NAME).open('rb') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.cli('--archive-apply', expected=2)
        self.assertEqual(before, self.snapshot())

    def test_archive_rollback_preserves_concurrent_notes_and_reports_incomplete(self):
        from notewrench.zoterologseq.archive import apply_archive
        from notewrench.zoterologseq.sync import atomic_write
        decision = self.plan()
        batch = self.graph / '.zotlog-sync-archive' / decision['decision_sha256']
        archived = batch / 'pages/Gone.md'
        original_manifest = (self.graph / MANIFEST_NAME).read_bytes()
        changed = None

        def edit_archived_page_then_fail(path, data):
            nonlocal changed
            if path == self.graph / MANIFEST_NAME:
                changed = archived.read_bytes() + b'\n- Concurrent handwritten note\n'
                archived.write_bytes(changed)
                raise OSError('injected manifest failure')
            atomic_write(path, data)

        with patch('notewrench.zoterologseq.archive.atomic_write', side_effect=edit_archived_page_then_fail):
            with self.assertRaisesRegex(OSError, 'rollback incomplete'):
                apply_archive(self.source, self.graph, self.decision)
        self.assertEqual(changed, archived.read_bytes())
        self.assertFalse((self.graph / 'pages/Gone.md').exists())
        self.assertEqual(original_manifest, (self.graph / MANIFEST_NAME).read_bytes())
        self.assertEqual('rollback-incomplete', json.loads((batch / 'receipt.json').read_bytes())['status'])

    def test_archive_apply_rolls_back_moves_and_manifest_on_caught_io_failure(self):
        from notewrench.zoterologseq.archive import apply_archive, move_page
        from notewrench.zoterologseq.sync import atomic_write
        self.write_source([])
        self.plan()
        original = self.snapshot()
        # Each failure uses a fresh graph, because recovery metadata is retained.
        for failure in ('second-move', 'post-move', 'manifest-before', 'manifest-after', 'receipt'):
            with self.subTest(failure=failure):
                if failure != 'second-move':
                    self.graph = self.root / failure
                    self.decision = self.root / (failure + '.json')
                    self.write_source(RECORDS)
                    synchronize(self.source, self.graph)
                    self.write_source([])
                    self.plan()
                    original = self.snapshot()
                calls = 0
                def injected_move(source, target):
                    nonlocal calls
                    calls += 1
                    if calls == 2 and failure == 'second-move':
                        raise OSError('injected second move failure')
                    move_page(source, target)
                    if calls == 1 and failure == 'post-move':
                        raise OSError('injected post-rename fsync failure')
                def injected_write(path, data):
                    if path == self.graph / MANIFEST_NAME and failure.startswith('manifest'):
                        if failure == 'manifest-after':
                            atomic_write(path, data)
                        # Fail only the first time so rollback can restore bytes.
                        if data != original[MANIFEST_NAME]:
                            raise OSError('injected manifest failure')
                    if path.name == 'receipt.json' and b'"complete"' in data and failure == 'receipt':
                        raise OSError('injected receipt failure')
                    atomic_write(path, data)
                with patch('notewrench.zoterologseq.archive.move_page', side_effect=injected_move if failure in ('second-move', 'post-move') else move_page), \
                     patch('notewrench.zoterologseq.archive.atomic_write', side_effect=injected_write):
                    with self.assertRaisesRegex(OSError, 'injected'):
                        apply_archive(self.source, self.graph, self.decision)
                for relative, data in original.items():
                    self.assertEqual(data, (self.graph / relative).read_bytes())
                self.assertFalse(list((self.graph / '.zotlog-sync-archive').rglob('*.md')))
                receipts = list((self.graph / '.zotlog-sync-archive').rglob('receipt.json'))
                self.assertEqual(1, len(receipts))
                self.assertEqual('rolled-back', json.loads(receipts[0].read_bytes())['status'])


if __name__ == '__main__':
    unittest.main()
