# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 tianhaogutum
"""Completed-batch restore contract; no merge, adoption, or handwritten overwrite."""
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
from notewrench.zoterologseq.archive import apply_archive, plan_archive
from notewrench.zoterologseq.model import LOCK_NAME, MANIFEST_NAME, canonical, digest

ROOT = Path(__file__).resolve().parents[2]
RECORDS = [{'id': 'Keep', 'type': 'book'}, {'id': 'Gone', 'type': 'book'},
           {'id': 'Other', 'type': 'book'}]


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.graph = self.root / 'graph'
        self.source = self.root / 'library.json'
        self.source.write_text(canonical(RECORDS))
        synchronize(self.source, self.graph)
        page = self.graph / 'pages/Gone.md'
        page.write_bytes(page.read_bytes() + '\r\n- 人工笔记 without final newline'.encode())
        page.chmod(0o640)
        self.inode = page.stat().st_ino
        self.before = self.snapshot()
        self.source.write_text(canonical(RECORDS[:1]))
        archive_plan = self.root / 'archive.json'
        plan_archive(self.source, self.graph, archive_plan)
        result = apply_archive(self.source, self.graph, archive_plan)
        self.batch = self.graph / result['archive_path']
        self.decision = self.root / 'restore.json'

    def snapshot(self):
        return {p.relative_to(self.graph).as_posix(): p.read_bytes()
                for p in self.graph.rglob('*') if p.is_file()}

    def cli(self, mode, expected=0, path=None, batch=None):
        result = subprocess.run([sys.executable, '-m', 'notewrench', 'zotero-logseq', str(batch or self.batch),
                                 '--graph', str(self.graph), mode, str(path or self.decision)],
                                cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_cli_plan_zero_graph_writes_apply_exact_notes_manifest_mode_inode_and_reuse(self):
        before = self.snapshot()
        mtimes = {p: p.stat().st_mtime_ns for p in self.graph.rglob('*')}
        result = self.cli('--restore-plan')
        self.assertEqual('planned', result['status'])
        self.assertTrue(result['dry_run'])
        self.assertEqual(['Gone', 'Other'], [e['id'] for e in result['restore_candidates']])
        self.assertEqual(before, self.snapshot())
        self.assertEqual(mtimes, {p: p.stat().st_mtime_ns for p in mtimes})
        self.assertEqual(0o600, self.decision.stat().st_mode & 0o777)
        # Restoring needs no source export at all.
        self.source.unlink()
        result = self.cli('--restore-apply')
        self.assertEqual('applied', result['status'])
        self.assertEqual(['Gone', 'Other'], [e['id'] for e in result['restored']])
        for name, data in self.before.items():
            self.assertEqual(data, (self.graph / name).read_bytes())
        page = self.graph / 'pages/Gone.md'
        self.assertEqual(self.inode, page.stat().st_ino)
        self.assertEqual(0o640, page.stat().st_mode & 0o777)
        self.assertFalse(list((self.batch / 'pages').iterdir()))
        self.assertEqual('complete', json.loads((self.batch / 'restore-receipt.json').read_bytes())['status'])
        for name, data in before.items():
            if '/pages/' not in name:
                self.assertEqual(data if name != MANIFEST_NAME else self.before[name],
                                 (self.graph / name).read_bytes())
        after = self.snapshot()
        self.cli('--restore-apply', expected=2)
        self.cli('--restore-plan', expected=2, path=self.root / 'again.json')
        self.assertEqual(after, self.snapshot())
        self.source.write_text(canonical(RECORDS))
        self.assertEqual(3, len(synchronize(self.source, self.graph)['unchanged']))

    def test_apply_rejects_all_obsolete_evidence_pages_manifest_and_destinations(self):
        self.cli('--restore-plan')
        changes = ('notes', 'missing', 'manifest', 'saved-before', 'saved-after', 'plan', 'receipt',
                   'handwritten', 'dangling-link', 'alias', 'yaml', 'namespace')
        for change in changes:
            with self.subTest(change=change):
                target = self.batch / 'pages/Gone.md'
                original = target.read_bytes()
                extra = None
                if change == 'notes':
                    target.write_bytes(original + b'\n- New note')
                elif change == 'missing':
                    target.unlink()
                elif change in ('saved-before', 'saved-after', 'plan', 'receipt', 'manifest'):
                    target = {'saved-before': self.batch / 'manifest-before.json',
                              'saved-after': self.batch / 'manifest-after.json',
                              'plan': self.batch / 'plan.json', 'receipt': self.batch / 'receipt.json',
                              'manifest': self.graph / MANIFEST_NAME}[change]
                    original = target.read_bytes()
                    target.write_bytes(original + b'\n')
                else:
                    extra = self.graph / ('pages/Gone.md' if change in ('handwritten', 'dangling-link')
                                         else 'pages/Reading.md')
                    if change == 'handwritten':
                        extra.write_bytes(b'- Handwritten content')
                    elif change == 'dangling-link':
                        extra.symlink_to(self.root / 'absent')
                    elif change == 'alias':
                        extra.write_bytes(b'alias:: Gone\n\n- Notes')
                    elif change == 'yaml':
                        extra.write_bytes(b'---\naliases: [Gone, Local]\n---\n')
                    else:
                        extra = self.graph / 'pages/Gone___Child.md'
                        extra.write_bytes(b'- Namespace')
                before = self.snapshot()
                self.cli('--restore-apply', expected=2)
                self.assertEqual(before, self.snapshot())
                self.assertFalse((self.batch / 'restore-receipt.json').exists())
                target.write_bytes(original)
                if extra is not None:
                    extra.unlink()

    def test_planning_rejects_incomplete_or_inconsistent_batch(self):
        receipt = self.batch / 'receipt.json'
        original = receipt.read_bytes()
        for status in ('prepared', 'rolled-back', 'rollback-incomplete'):
            data = json.loads(original)
            data['status'] = status
            receipt.write_text(canonical(data))
            before = self.snapshot()
            self.cli('--restore-plan', expected=2)
            self.assertEqual(before, self.snapshot())
            self.assertFalse(self.decision.exists())
        receipt.write_bytes(original)
        target = self.batch / 'manifest-before.json'
        data = json.loads(target.read_bytes())
        data['entries']['Gone']['path'] = '../outside.md'
        target.write_text(canonical(data))
        self.cli('--restore-plan', expected=2)

    def test_recomputed_restore_decision_cannot_select_paths_subset_or_other_graph(self):
        self.cli('--restore-plan')
        original = json.loads(self.decision.read_bytes())
        for change in ('path', 'subset', 'graph', 'hash'):
            changed = copy.deepcopy(original)
            if change == 'path':
                changed['entries'][0]['path'] = '../outside.md'
            elif change == 'subset':
                changed['entries'].pop()
            elif change == 'graph':
                changed['graph'] = str(self.root / 'other')
            else:
                changed['entries'][0]['page_sha256'] = '0' * 64
            changed['decision_sha256'] = digest(canonical({k: v for k, v in changed.items()
                                                          if k != 'decision_sha256'}).encode())
            self.decision.write_text(canonical(changed))
            before = self.snapshot()
            self.cli('--restore-apply', expected=2)
            self.assertEqual(before, self.snapshot())
        self.decision.write_bytes(b'{"version":1,"version":1}')
        self.cli('--restore-apply', expected=2)

    def test_output_paths_batch_location_symlinks_and_nonregular_archived_files(self):
        self.decision.write_bytes(b'handwritten decision')
        self.cli('--restore-plan', expected=2)
        self.assertEqual(b'handwritten decision', self.decision.read_bytes())
        self.decision.unlink()
        for path in (self.graph / 'plan.json', self.batch / 'plan.json'):
            self.cli('--restore-plan', path=path, expected=2)
        self.decision.symlink_to(self.source)
        self.cli('--restore-plan', expected=2)
        self.decision.unlink()
        outside = self.root / 'moved-batch'
        self.batch.rename(outside)
        self.cli('--restore-plan', batch=outside, expected=2)
        self.batch.symlink_to(outside, target_is_directory=True)
        self.cli('--restore-plan', expected=2)
        self.batch.unlink()
        outside.rename(self.batch)
        page = self.batch / 'pages/Gone.md'
        outside = self.root / 'page.md'
        page.rename(outside)
        for kind in ('link', 'hardlink', 'fifo'):
            if kind == 'link':
                page.symlink_to(outside)
            elif kind == 'hardlink':
                os.link(outside, page)
            else:
                os.mkfifo(page)
            self.cli('--restore-plan', expected=2)
            page.unlink()
        outside.rename(page)

    def test_combined_identity_checks_include_archived_aliases_and_ownership(self):
        # Recomputed evidence is not an authorization to bypass graph checks.
        page = self.batch / 'pages/Gone.md'
        original = page.read_bytes()
        plan_file = self.batch / 'plan.json'
        receipt_file = self.batch / 'receipt.json'
        plan = json.loads(plan_file.read_bytes())
        old_batch = self.batch
        for change in ('alias', 'managed', 'provenance'):
            with self.subTest(change=change):
                if change == 'alias':
                    changed = original.replace(b'\n\n', b'\nalias:: Keep\n\n', 1)
                elif change == 'managed':
                    changed = original.replace(b'csl-type:: book', b'csl-type:: edited')
                else:
                    changed = original.replace(b'citekey:: Gone', b'citekey:: Bad')
                page.write_bytes(changed)
                plan['entries'][0]['page_sha256'] = digest(changed)
                plan['decision_sha256'] = digest(canonical({k: v for k, v in plan.items()
                                                            if k != 'decision_sha256'}).encode())
                plan_file.write_text(canonical(plan))
                receipt = json.loads(receipt_file.read_bytes())
                receipt['decision_sha256'] = plan['decision_sha256']
                receipt['entries'] = plan['entries']
                receipt_file.write_text(canonical(receipt))
                renamed = old_batch.with_name(plan['decision_sha256'])
                old_batch.rename(renamed)
                self.batch = renamed
                before = self.snapshot()
                report = self.cli('--restore-plan', expected=2)
                self.assertIn('collision' if change == 'alias' else
                              'edited' if change == 'managed' else 'frontmatter', report['error'])
                self.assertEqual(before, self.snapshot())
                renamed.rename(old_batch)
                self.batch = old_batch

    def test_restore_modes_are_mutually_exclusive_and_absent_graph_stays_absent(self):
        result = subprocess.run([sys.executable, '-m', 'notewrench', 'zotero-logseq', str(self.batch),
                                 '--graph', str(self.graph), '--restore-plan', str(self.decision),
                                 '--dry-run'], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(2, result.returncode)
        self.assertIn('not allowed with argument', result.stderr)
        self.graph = self.root / 'absent-graph'
        self.cli('--restore-plan', expected=2)
        self.assertFalse(self.graph.exists())
        self.assertFalse(self.decision.exists())

    def test_apply_uses_shared_lock(self):
        self.cli('--restore-plan')
        before = self.snapshot()
        with (self.graph / LOCK_NAME).open('rb') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.cli('--restore-apply', expected=2)
        self.assertEqual(before, self.snapshot())

    def test_rechecks_snapshots_inventory_and_archive_after_locked_preflight(self):
        from notewrench.zoterologseq.restore import apply_restore, build_restore_decision
        self.cli('--restore-plan')
        for change in ('page', 'inventory', 'evidence', 'live-note', 'live-alias'):
            target = (self.batch / 'pages/Gone.md' if change == 'page' else
                      self.batch / 'receipt.json' if change == 'evidence' else
                      self.graph / 'pages/Keep.md' if change in ('live-note', 'live-alias') else
                      self.graph / 'pages/Reading.md')
            original = target.read_bytes() if target.exists() else None
            calls = 0
            def mutate(batch, graph):
                nonlocal calls
                calls += 1
                result = build_restore_decision(batch, graph)
                if calls == 2:
                    target.write_bytes((original or b'') + b'\n- Concurrent note' if change != 'live-alias'
                                       else original.replace(b'\n\n', b'\nalias:: Gone\n\n', 1))
                return result
            manifest = (self.graph / MANIFEST_NAME).read_bytes()
            with patch('notewrench.zoterologseq.restore.build_restore_decision', side_effect=mutate):
                with self.assertRaisesRegex(Invalid, 'changed after'):
                    apply_restore(self.batch, self.graph, self.decision)
            self.assertEqual(manifest, (self.graph / MANIFEST_NAME).read_bytes())
            self.assertFalse((self.batch / 'restore-receipt.json').exists())
            if original is None:
                target.unlink()
            else:
                target.write_bytes(original)

    def test_caught_io_failures_roll_back_pages_manifest_and_record_status(self):
        from notewrench.zoterologseq.restore import apply_restore, move_page, atomic_write
        self.cli('--restore-plan')
        before = self.snapshot()
        for failure in ('second-move', 'post-move', 'manifest-before', 'manifest-after', 'receipt'):
            with self.subTest(failure=failure):
                calls = 0
                def fail_move(source, target):
                    nonlocal calls
                    calls += 1
                    if calls == 2 and failure == 'second-move':
                        raise OSError('injected move failure')
                    move_page(source, target)
                    if calls == 1 and failure == 'post-move':
                        raise OSError('injected post-rename failure')
                def fail_write(path, data):
                    if path == self.graph / MANIFEST_NAME and data == self.before[MANIFEST_NAME]:
                        if failure == 'manifest-after':
                            atomic_write(path, data)
                        if failure.startswith('manifest'):
                            raise OSError('injected manifest failure')
                    if path.name == 'restore-receipt.json' and b'"complete"' in data and failure == 'receipt':
                        raise OSError('injected receipt failure')
                    atomic_write(path, data)
                with patch('notewrench.zoterologseq.restore.move_page', side_effect=fail_move), \
                     patch('notewrench.zoterologseq.restore.atomic_write', side_effect=fail_write):
                    with self.assertRaisesRegex(OSError, 'injected'):
                        apply_restore(self.batch, self.graph, self.decision)
                for name, data in before.items():
                    self.assertEqual(data, (self.graph / name).read_bytes())
                self.assertFalse((self.graph / 'pages/Gone.md').exists())
                receipt = self.batch / 'restore-receipt.json'
                self.assertEqual('rolled-back', json.loads(receipt.read_bytes())['status'])
                self.cli('--restore-apply', expected=2)
                receipt.unlink()  # Test fixture only: production retries are deliberately refused.

    def test_rollback_preserves_concurrent_handwritten_notes_and_reports_incomplete(self):
        from notewrench.zoterologseq.restore import apply_restore, atomic_write
        self.cli('--restore-plan')
        page = self.graph / 'pages/Gone.md'
        changed = self.before['pages/Gone.md'] + b'\n- Concurrent handwritten note'
        def fail(path, data):
            if path == self.graph / MANIFEST_NAME:
                page.write_bytes(changed)
                raise OSError('injected manifest failure')
            atomic_write(path, data)
        with patch('notewrench.zoterologseq.restore.atomic_write', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'rollback incomplete'):
                apply_restore(self.batch, self.graph, self.decision)
        self.assertEqual(changed, page.read_bytes())
        self.assertFalse((self.batch / 'pages/Gone.md').exists())
        self.assertEqual('rollback-incomplete', json.loads((self.batch / 'restore-receipt.json').read_bytes())['status'])


if __name__ == '__main__':
    unittest.main()
