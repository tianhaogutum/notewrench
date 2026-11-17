# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 tianhaogutum
"""Read-only incomplete-attempt audit contracts; synthetic evidence, never repair."""
import copy
import fcntl
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from notewrench.zoterologseq import synchronize
from notewrench.zoterologseq.archive import apply_archive, json_bytes, plan_archive
from notewrench.zoterologseq.model import LOCK_NAME, MANIFEST_NAME, digest
from notewrench.zoterologseq.restore import apply_restore, plan_restore

ROOT = Path(__file__).resolve().parents[2]


def snapshot(graph):
    """Include directory mtimes/inventory and link identity without following links."""
    result = {}
    def visit(path):
        info = path.lstat()
        content = (path.read_bytes() if stat.S_ISREG(info.st_mode) else
                   os.readlink(path) if stat.S_ISLNK(info.st_mode) else None)
        result[path.relative_to(graph).as_posix()] = (
            info.st_mode, info.st_mtime_ns, info.st_ino, info.st_nlink, content)
        if stat.S_ISDIR(info.st_mode):
            for child in path.iterdir():
                visit(child)
    visit(graph)
    return result


def without_reasons(report):
    """Keep historical exact observation assertions independent of new arrays."""
    report = copy.deepcopy(report)
    report.pop('ambiguity_reasons', None)
    for page in report.get('pages', []):
        page.pop('ambiguity_reasons', None)
    return report


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.graph = self.root / 'graph'
        source = self.root / 'library.json'
        source.write_bytes(json_bytes([{'id': key, 'type': 'book'} for key in ('Keep', 'Gone', 'Other')]))
        synchronize(source, self.graph)
        gone = self.graph / 'pages/Gone.md'
        gone.write_bytes(gone.read_bytes() + '\r\n- 人工笔记 no final newline'.encode())
        source.write_bytes(json_bytes([{'id': 'Keep', 'type': 'book'}]))
        decision = self.root / 'archive.json'
        plan_archive(source, self.graph, decision)
        self.batch = self.graph / apply_archive(source, self.graph, decision)['archive_path']
        self.entries = json.loads((self.batch / 'plan.json').read_bytes())['entries']
        (self.graph / LOCK_NAME).unlink()
        self.set_status('prepared')

    def set_status(self, status, restore=False):
        path = self.batch / ('restore-receipt.json' if restore else 'receipt.json')
        value = json.loads(path.read_bytes())
        value['status'] = status
        path.write_bytes(json_bytes(value))

    def assert_reason_contract(self, report):
        if report['status'] == 'error':
            self.assertEqual({'status', 'error'}, set(report))
            return
        reasons = report['ambiguity_reasons']
        self.assertEqual(report['ambiguous'], bool(reasons))
        self.assertEqual(len(reasons), len({json.dumps(r, sort_keys=True) for r in reasons}))
        diagnostics = [dict(code=d['code'], path=d['path']) for field in
                       ('conflicts', 'initial_errors', 'recheck_errors') for d in report.get(field, [])]
        for reason in diagnostics:
            self.assertIn(reason, reasons)
        for page in report['pages']:
            row = page['ambiguity_reasons']
            self.assertEqual(page['ambiguous'], bool(row))
            self.assertEqual(len(row), len({json.dumps(r, sort_keys=True) for r in row}))
            self.assertTrue(all(r in reasons for r in row), 'row reasons not propagated')
            paths = {str(base / page['path']) for base in (self.graph, self.batch)}
            paths |= {str(Path(p).parent) for p in paths}
            diagnostic_codes = {d['code'] for d in diagnostics}
            wanted = []
            for reason in diagnostics:
                if reason['path'] in paths and reason not in wanted:
                    wanted.append(reason)
            self.assertEqual(wanted, [r for r in row if r['code'] in diagnostic_codes])

    def assert_observations_equal(self, expected, actual, message=None):
        self.assert_reason_contract(actual)
        self.assertEqual(without_reasons(expected), without_reasons(actual), message)

    def cli(self, expected=0, batch=None):
        before = snapshot(self.graph)
        result = subprocess.run([sys.executable, '-m', 'notewrench', 'zotero-logseq', str(batch or self.batch),
                                 '--graph', str(self.graph), '--recovery-audit'],
                                cwd=ROOT, text=True, capture_output=True, timeout=60)
        self.assertEqual(before, snapshot(self.graph), 'audit changed graph bytes/mtime/inventory')
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        self.assertEqual('', result.stderr)
        report = json.loads(result.stdout)
        self.assert_reason_contract(report)
        return report

    def test_prepared_partial_move_exact_json_contract_no_lock(self):
        entry = self.entries[0]
        (self.batch / entry['path']).rename(self.graph / entry['path'])
        report = self.cli()
        observed = {'presence': 'present', 'sha256': entry['page_sha256'],
                    'full_hash': 'match', 'ownership': 'match', 'diagnostic': None}
        missing = {'presence': 'missing', 'sha256': None, 'full_hash': 'unknown',
                   'ownership': 'unknown', 'diagnostic': None}
        self.assertEqual({
            'version': 1, 'status': 'audited', 'read_only': True, 'conflicts': [],
            'archive_path': self.batch.relative_to(self.graph).as_posix(),
            'receipts': {'archive': 'prepared', 'restore': None},
            'manifest': {'presence': 'present', 'sha256': digest((self.graph / MANIFEST_NAME).read_bytes()),
                         'classification': 'after', 'diagnostic': None},
            'pages': [
                {'id': entry['id'], 'path': entry['path'], 'expected_sha256': entry['page_sha256'],
                 'original': observed, 'archive': missing, 'locations': 'original-only', 'ambiguous': False},
                {'id': self.entries[1]['id'], 'path': self.entries[1]['path'],
                 'expected_sha256': self.entries[1]['page_sha256'], 'original': missing,
                 'archive': dict(observed, sha256=self.entries[1]['page_sha256']),
                 'locations': 'archive-only', 'ambiguous': False},
            ], 'ambiguous': True,
        }, without_reasons(report))
        self.assertFalse((self.graph / LOCK_NAME).exists())

    def test_terminal_receipts_do_not_prove_page_or_manifest_completion(self):
        self.set_status('complete')
        self.assertFalse(self.cli()['ambiguous'])
        (self.graph / MANIFEST_NAME).write_bytes((self.batch / 'manifest-before.json').read_bytes())
        self.assertTrue(self.cli()['ambiguous'])
        for entry in self.entries:
            (self.batch / entry['path']).rename(self.graph / entry['path'])
        self.assertTrue(self.cli()['ambiguous'])
        self.set_status('rolled-back')
        self.assertFalse(self.cli()['ambiguous'])

    def test_missing_duplicate_changed_notes_and_divergent_manifest(self):
        gone = self.batch / 'pages/Gone.md'
        (self.graph / 'pages/Gone.md').write_bytes(gone.read_bytes() + b'\n- New handwritten note')
        (self.batch / 'pages/Other.md').unlink()
        (self.graph / MANIFEST_NAME).write_bytes(b'not a manifest')
        self.set_status('rollback-incomplete')
        report = self.cli()
        self.assertEqual('neither', report['manifest']['classification'])
        self.assertTrue(report['ambiguous'])
        first, second = report['pages']
        self.assertEqual('both', first['locations'])
        self.assertEqual(('mismatch', 'match'), (first['original']['full_hash'], first['original']['ownership']))
        self.assertEqual('match', first['archive']['full_hash'])
        self.assertTrue(first['ambiguous'])
        self.assertEqual('missing', second['locations'])
        self.assertTrue(second['ambiguous'])
        self.assertIn('differs', first['original']['diagnostic'])

    def test_before_after_neither_and_missing_manifest(self):
        manifest = self.graph / MANIFEST_NAME
        for name, expected in [('manifest-before.json', 'before'), ('manifest-after.json', 'after')]:
            manifest.write_bytes((self.batch / name).read_bytes())
            self.assertEqual(expected, self.cli()['manifest']['classification'])
        manifest.write_bytes(manifest.read_bytes() + b'\n')
        self.assertEqual('neither', self.cli()['manifest']['classification'])
        manifest.unlink()
        report = self.cli()
        self.assertEqual({'presence': 'missing', 'sha256': None, 'classification': 'neither',
                          'diagnostic': None}, report['manifest'])
        self.assertTrue(report['ambiguous'])

    def test_ownership_is_independent_and_never_inferred_from_aliases(self):
        target = self.batch / 'pages/Gone.md'
        original = target.read_bytes()
        for data in (original.replace(b'csl-type:: book', b'csl-type:: changed'),
                     original.replace(b'citekey:: Gone', b'citekey:: Different'),
                     b'alias:: Gone\n\n- Handwritten page', b'\xff'):
            target.write_bytes(data)
            report = self.cli()
            self.assertEqual(('mismatch', 'mismatch'),
                             (report['pages'][0]['archive']['full_hash'], report['pages'][0]['archive']['ownership']))
            self.assertTrue(report['pages'][0]['ambiguous'])
        target.unlink()
        (self.graph / 'pages/Alias.md').write_bytes(b'alias:: Gone\n\n- Human notes')
        self.assertEqual('missing', self.cli()['pages'][0]['locations'])

    def test_restore_prepared_and_rollback_incomplete_use_archive_manifest_direction(self):
        self.set_status('complete')
        decision = self.root / 'restore.json'
        plan_restore(self.batch, self.graph, decision)
        apply_restore(self.batch, self.graph, decision)
        (self.graph / LOCK_NAME).unlink()
        for status in ('prepared', 'rollback-incomplete', 'rolled-back', 'complete'):
            self.set_status(status, restore=True)
            # Synthetic partial restore: one original present, the other archived.
            page, archived = self.graph / 'pages/Other.md', self.batch / 'pages/Other.md'
            if page.exists():
                page.rename(archived)
            report = self.cli()
            self.assertEqual({'archive': 'complete', 'restore': status}, report['receipts'])
            self.assertEqual('before', report['manifest']['classification'])
            self.assertEqual(['original-only', 'archive-only'], [p['locations'] for p in report['pages']])

    def test_unsafe_candidate_or_manifest_is_reported_without_following(self):
        targets = (self.batch / 'pages/Gone.md', self.graph / 'pages/Gone.md', self.graph / MANIFEST_NAME)
        outside = self.root / 'outside'
        outside.write_bytes(b'outside secret must not be read')
        outside_before = (outside.read_bytes(), outside.stat().st_mtime_ns)
        for target in targets:
            original = target.read_bytes() if target.exists() else None
            for kind in ('symlink', 'dangling', 'hardlink', 'fifo', 'directory'):
                with self.subTest(path=str(target), kind=kind):
                    if target.exists():
                        target.unlink()
                    if kind in ('symlink', 'dangling'):
                        target.symlink_to(outside if kind == 'symlink' else self.root / 'absent')
                    elif kind == 'hardlink':
                        os.link(outside, target)
                    elif kind == 'fifo':
                        os.mkfifo(target)
                    else:
                        target.mkdir()
                    report = self.cli(expected=2)
                    self.assertEqual('conflict', report['status'])
                    self.assertEqual(['unsafe-evidence'], [c['code'] for c in report['conflicts']])
                    observation = (report['manifest'] if target.name == MANIFEST_NAME else
                                   report['pages'][0]['archive' if target.is_relative_to(self.batch) else 'original'])
                    self.assertEqual('unsafe', observation['presence'])
                    self.assertIsNone(observation['sha256'])
                    self.assertTrue(report['ambiguous'])
                    target.rmdir() if kind == 'directory' else target.unlink()
            if original is not None:
                target.write_bytes(original)
        self.assertEqual(outside_before, (outside.read_bytes(), outside.stat().st_mtime_ns))

    def test_unsafe_files_are_never_opened(self):
        from notewrench.zoterologseq.recovery import audit_recovery
        target = self.batch / 'pages/Gone.md'
        target.unlink()
        real_open = os.open
        outside = self.root / 'outside'
        outside.write_bytes(b'not evidence')
        for kind in ('symlink', 'hardlink', 'fifo'):
            if kind == 'symlink':
                target.symlink_to(outside)
            elif kind == 'hardlink':
                os.link(outside, target)
            else:
                os.mkfifo(target)
            def guarded(path, *args, **kwargs):
                self.assertNotIn(Path(path), (target, outside), 'unsafe evidence was opened')
                return real_open(path, *args, **kwargs)
            before = snapshot(self.graph)
            with patch('os.open', side_effect=guarded):
                report = audit_recovery(self.batch, self.graph)
            self.assertEqual('unsafe', report['pages'][0]['archive']['presence'])
            self.assertEqual(before, snapshot(self.graph))
            target.unlink()

    def test_invalid_metadata_exact_error_shape_and_no_candidate_trust(self):
        for name in ('plan.json', 'receipt.json', 'manifest-before.json', 'manifest-after.json'):
            path = self.batch / name
            original = path.read_bytes()
            for bad in (b'{}', b'{"version":1,"version":1}', b'[]'):
                path.write_bytes(bad)
                report = self.cli(expected=2)
                self.assertEqual({'status', 'error'}, set(report))
                self.assertEqual('error', report['status'])
            path.unlink()
            self.assertEqual('error', self.cli(expected=2)['status'])
            path.write_bytes(original)
        receipt = self.batch / 'receipt.json'
        value = json.loads(receipt.read_bytes())
        for field, bad in [('status', 'unknown'), ('version', True), ('entries', []),
                           ('manifest_before_sha256', '0' * 64), ('decision_sha256', '0' * 64)]:
            changed = dict(value, **{field: bad})
            receipt.write_bytes(json_bytes(changed))
            self.assertEqual('error', self.cli(expected=2)['status'])
        receipt.write_bytes(json_bytes(value))

    def test_rehashed_arbitrary_duplicate_subset_and_reordered_paths_refused(self):
        plan_path = self.batch / 'plan.json'
        original = json.loads(plan_path.read_bytes())
        original_batch = self.batch
        for change in ('path', 'duplicate', 'subset', 'reordered', 'manifest-path'):
            value = copy.deepcopy(original)
            before_path = self.batch / 'manifest-before.json'
            before = before_path.read_bytes()
            if change == 'path':
                value['entries'][0]['path'] = '../outside.md'
            elif change == 'duplicate':
                value['entries'].append(value['entries'][0])
            elif change == 'subset':
                value['entries'].pop()
            elif change == 'reordered':
                value['entries'].reverse()
            else:
                manifest = json.loads(before)
                manifest['entries']['Gone']['path'] = '../outside.md'
                before_path.write_bytes(json_bytes(manifest))
                value['manifest_sha256'] = digest(before_path.read_bytes())
            value['decision_sha256'] = digest(json_bytes({k: v for k, v in value.items() if k != 'decision_sha256'}))
            plan_path.write_bytes(json_bytes(value))
            moved = original_batch.with_name(value['decision_sha256'])
            original_batch.rename(moved)
            self.batch = moved
            self.assertEqual('error', self.cli(expected=2)['status'])
            moved.rename(original_batch)
            self.batch = original_batch
            before_path.write_bytes(before)
        plan_path.write_bytes(json_bytes(original))

    def test_linked_metadata_ancestors_and_wrong_location_refused(self):
        for name in ('plan.json', 'receipt.json', 'manifest-before.json', 'manifest-after.json',
                     'restore-receipt.json'):
            target = self.batch / name
            old = target.read_bytes() if target.exists() else None
            if old is not None:
                target.unlink()
            outside = self.root / 'evidence'
            outside.write_bytes(old or b'{}')
            for kind in ('symlink', 'hardlink', 'fifo', 'directory'):
                if kind == 'symlink':
                    target.symlink_to(outside)
                elif kind == 'hardlink':
                    os.link(outside, target)
                elif kind == 'fifo':
                    os.mkfifo(target)
                else:
                    target.mkdir()
                self.assertEqual('error', self.cli(expected=2)['status'])
                target.rmdir() if kind == 'directory' else target.unlink()
            if old is not None:
                target.write_bytes(old)
        for target in (self.batch / 'pages', self.graph / 'pages'):
            outside = self.root / 'saved-pages'
            target.rename(outside)
            target.symlink_to(outside, target_is_directory=True)
            self.assertEqual('conflict', self.cli(expected=2)['status'])
            target.unlink()
            outside.rename(target)
        outside = self.root / 'moved'
        self.batch.rename(outside)
        self.cli(batch=outside, expected=2)
        self.batch.symlink_to(outside, target_is_directory=True)
        self.cli(expected=2)
        self.batch.unlink()
        outside.rename(self.batch)
        self.cli(batch=self.batch / '..' / self.batch.name, expected=2)

    def test_restore_receipt_hash_and_archive_completion_required(self):
        self.set_status('complete')
        decision = self.root / 'restore.json'
        plan_restore(self.batch, self.graph, decision)
        apply_restore(self.batch, self.graph, decision)
        path = self.batch / 'restore-receipt.json'
        original = json.loads(path.read_bytes())
        for field, value in [('decision_sha256', '0' * 64), ('entries', []), ('status', 'bad'),
                             ('manifest_after_sha256', original['manifest_before_sha256'])]:
            path.write_bytes(json_bytes(dict(original, **{field: value})))
            self.assertEqual('error', self.cli(expected=2)['status'])
        path.write_bytes(json_bytes(original))
        self.set_status('prepared')
        self.cli(expected=2)

    def test_existing_lock_is_not_acquired_and_unexpected_io_is_exit_three(self):
        lock = self.graph / LOCK_NAME
        lock.write_bytes(b'')
        with lock.open('rb') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual('audited', self.cli()['status'])
        from notewrench.zoterologseq.cli import main
        import io
        with patch('notewrench.zoterologseq.recovery.read_regular_snapshot', side_effect=PermissionError('injected read failure')), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            self.assertEqual(3, main([str(self.batch), '--graph', str(self.graph), '--recovery-audit']))
        self.assertEqual({'status': 'error', 'error': 'injected read failure'}, json.loads(output.getvalue()))

    def assert_changed_during_audit(self, targets, mutate, unsafe=()):
        """Inject after the final candidate read; only the injector may write."""
        from notewrench.zoterologseq.cli import main
        from notewrench.zoterologseq.recovery import audit_recovery, read_regular_snapshot
        before = snapshot(self.graph)
        expected = audit_recovery(self.batch, self.graph)
        self.assertEqual(before, snapshot(self.graph))
        expected.update(status='conflict', ambiguous=True)
        expected['conflicts'] = [
            {'code': 'changed-during-audit', 'path': str(path),
             'message': 'evidence changed during audit; initial observation retained'}
            for path in targets]
        for page in expected['pages']:
            if any(path in targets or path.parent in targets
                   for path in (self.graph / page['path'], self.batch / page['path'])):
                page['ambiguous'] = True
        injected = []
        def change(path):
            data = read_regular_snapshot(path)
            if not injected and path == self.batch / self.entries[-1]['path']:
                self.assertEqual(before, snapshot(self.graph), 'audit wrote before injection')
                mutate()
                injected.append(snapshot(self.graph))
            return data
        real_open = os.open
        def guarded_open(path, *args, **kwargs):
            if injected:
                self.assertNotIn(Path(path), unsafe, 'new unsafe evidence was opened')
            return real_open(path, *args, **kwargs)
        with patch('notewrench.zoterologseq.recovery.read_regular_snapshot', side_effect=change), \
             patch('os.open', side_effect=guarded_open), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            code = main([str(self.batch), '--graph', str(self.graph), '--recovery-audit'])
        self.assertEqual(1, len(injected), 'change was not injected exactly once')
        self.assertEqual(injected[0], snapshot(self.graph), 'audit changed injected bytes/mtime/inventory')
        self.assertFalse((self.graph / LOCK_NAME).exists())
        self.assertEqual(2, code, output.getvalue())
        actual = json.loads(output.getvalue())
        changed = [c for c in actual['conflicts'] if c['code'] == 'changed-during-audit']
        self.assertEqual(sorted(expected['conflicts'], key=lambda c: c['path']),
                         sorted(changed, key=lambda c: c['path']))
        other = [c for c in actual['conflicts'] if c['code'] != 'changed-during-audit']
        self.assertEqual(set(unsafe), {Path(c['path']) for c in other})
        for conflict in other:
            self.assertEqual({'code', 'path', 'message'}, set(conflict))
            self.assertEqual('unsafe-evidence', conflict['code'])
            self.assertTrue(conflict['message'])
        actual['conflicts'] = changed
        expected['conflicts'] = changed
        self.assert_observations_equal(expected, actual, 'initial observations were replaced or contract changed')

    def replace_identical(self, target):
        """Create before unlinking so inode reuse cannot invalidate the fixture."""
        saved, info = target.read_bytes(), target.stat()
        replacement = target.with_name(target.name + '.replacement')
        replacement.write_bytes(saved)
        os.chmod(replacement, stat.S_IMODE(info.st_mode))
        os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertNotEqual(info.st_ino, replacement.stat().st_ino)
        replacement.replace(target)
        self.assertEqual(saved, target.read_bytes())
        self.assertEqual(info.st_mtime_ns, target.stat().st_mtime_ns)
        self.assertEqual(1, target.stat().st_nlink)

    def directory_audit(self, mutate, changed=(), fail_directory=None, fail_file=None):
        """Inject immediately before completion comparison and retain exact JSON."""
        from notewrench.zoterologseq.cli import main
        from notewrench.zoterologseq.recovery import AuditReads, audit_recovery, read_regular_snapshot
        expected = audit_recovery(self.batch, self.graph)
        before, injected = snapshot(self.graph), []
        raw_recheck = AuditReads.recheck
        def recheck(reads, report):
            self.assertEqual(before, snapshot(self.graph))
            mutate()
            injected.append(snapshot(self.graph))
            return raw_recheck(reads, report)
        raw_lstat = Path.lstat
        def lstat(path, *args, **kwargs):
            if injected and path == fail_directory:
                raise PermissionError('injected directory recheck failure')
            return raw_lstat(path, *args, **kwargs)
        seen = []
        def read(path):
            if path == fail_file:
                seen.append(path)
                raise PermissionError('injected initial file failure')
            return read_regular_snapshot(path)
        with patch.object(AuditReads, 'recheck', new=recheck), \
             patch.object(Path, 'lstat', new=lstat), \
             patch('notewrench.zoterologseq.recovery.read_regular_snapshot', side_effect=read), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            code = main([str(self.batch), '--graph', str(self.graph), '--recovery-audit'])
        self.assertEqual([snapshot(self.graph)], injected)
        self.assertFalse((self.graph / LOCK_NAME).exists())
        expected['conflicts'] = [
            {'code': 'changed-during-audit', 'path': str(path),
             'message': 'evidence changed during audit; initial observation retained'} for path in changed]
        if fail_directory:
            expected['recheck_errors'] = [
                {'code': 'unreadable-during-recheck', 'path': str(fail_directory),
                 'message': 'injected directory recheck failure'}]
        if fail_file:
            self.assertEqual([fail_file], seen, 'unknown initial file was retried')
            expected['initial_errors'] = [
                {'code': 'unreadable-initial-evidence', 'path': str(fail_file),
                 'message': 'injected initial file failure'}]
            for page in expected['pages']:
                for base, location in ((self.graph, 'original'), (self.batch, 'archive')):
                    if base / page['path'] == fail_file:
                        page[location].update(presence='unreadable', sha256=None, full_hash='unknown',
                            ownership='unknown', diagnostic='injected initial file failure')
                        page.update(locations='unknown', ambiguous=True)
        uncertain = set(changed) | ({fail_directory} if fail_directory else set())
        if uncertain or fail_file:
            expected.update(status='partial' if fail_directory or fail_file else 'conflict', ambiguous=True)
            for page in expected['pages']:
                if any((base / page['path']).parent in uncertain for base in (self.graph, self.batch)):
                    page['ambiguous'] = True
        self.assertEqual(3 if fail_directory or fail_file else 2 if changed else 0, code, output.getvalue())
        self.assert_observations_equal(expected, json.loads(output.getvalue()), 'initial classifications lost')

    def replace_directory(self, target):
        before = snapshot(target)
        info = target.stat()
        moved = target.with_name(target.name + '-aside')
        target.rename(moved)
        target.mkdir()
        self.assertNotEqual(info.st_ino, target.stat().st_ino)
        for child in moved.iterdir():
            child.rename(target / child.name)
        # Child bytes, mtimes and inodes stay identical, as do candidate absences.
        after = snapshot(target)
        self.assertEqual({k: v for k, v in before.items() if k != '.'},
                         {k: v for k, v in after.items() if k != '.'})
        moved.rmdir()
        os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))

    def test_directory_replacement_archive_and_restore_with_absent_candidates(self):
        self.set_status('complete')
        for restored in (False, True):
            if restored:
                decision = self.root / 'restore.json'
                plan_restore(self.batch, self.graph, decision)
                apply_restore(self.batch, self.graph, decision)
                (self.graph / LOCK_NAME).unlink()
            for target in (self.graph / 'pages', self.batch / 'pages'):
                with self.subTest(restored=restored, target=target):
                    self.directory_audit(lambda: self.replace_directory(target), changed=[target])

    def test_directory_missing_presence_contract(self):
        self.set_status('complete')
        target = self.graph / 'pages'
        moved = self.root / 'saved-pages'
        target.rename(moved)
        self.directory_audit(lambda: None)  # Still missing: no inferred identity.
        self.directory_audit(target.mkdir, changed=[target])
        self.directory_audit(target.rmdir, changed=[target])
        moved.rename(target)
        # The archive parent may also be initially absent (candidates classify missing).
        target = self.batch / 'pages'
        target.rename(moved)
        self.directory_audit(lambda: None)
        self.directory_audit(target.mkdir, changed=[target])

    def test_directory_io_precedence_and_unknown_initial_file(self):
        self.set_status('complete')
        original, archived = self.graph / 'pages', self.batch / 'pages'
        self.directory_audit(lambda: self.replace_directory(original), changed=[original],
                             fail_directory=archived)
        self.directory_audit(lambda: self.replace_directory(original), changed=[original],
                             fail_file=self.batch / self.entries[0]['path'])

    def test_directory_initial_unsafe_retains_prepared_observations(self):
        for target in (self.graph / 'pages', self.batch / 'pages'):
            with self.subTest(target=target):
                moved = self.root / 'saved-pages'
                target.rename(moved)
                for kind in ('symlink', 'file'):
                    if kind == 'symlink':
                        target.symlink_to(moved, target_is_directory=True)
                    else:
                        target.write_bytes(b'not a directory')
                    self.assertEqual('conflict', self.cli(expected=2)['status'])
                    target.unlink()
                moved.rename(target)

    def assert_initial_directory_unsafe(self, unsafe, unknown=(), fail_file=None,
                                        recheck_file=None, change_file=None, become_safe=False):
        """Exact initial contract, independent rechecks, and no unsafe path probes."""
        from notewrench.zoterologseq.cli import main
        from notewrench.zoterologseq.recovery import AuditReads, audit_recovery, directory_snapshot, read_regular_snapshot
        expected = audit_recovery(self.batch, self.graph)
        saved = {}
        for parent, kind in unsafe.items():
            saved[parent] = parent.rename(self.root / ('saved-' + str(len(saved))))
            if kind in ('linked', 'dangling'):
                parent.symlink_to(saved[parent] if kind == 'linked' else self.root / 'absent',
                                  target_is_directory=True)
            elif kind == 'file':
                parent.write_bytes(b'not a directory')
            else:
                os.mkfifo(parent)
        blocked = set(unsafe) | set(unknown)
        before, injected, files, directories = snapshot(self.root), [], {}, {}
        raw_recheck, raw_lstat, raw_open = AuditReads.recheck, Path.lstat, os.open
        def directory(path):
            directories[path] = directories.get(path, 0) + 1
            if path in unknown:
                raise PermissionError('injected initial directory failure: ' + str(path))
            return directory_snapshot(path)
        def lstat(path, *args, **kwargs):
            self.assertNotIn(path.parent, blocked, 'lstat through blocked parent')
            return raw_lstat(path, *args, **kwargs)
        def open_file(path, flags, *args, **kwargs):
            self.assertNotIn(Path(path), unsafe, 'unsafe parent opened')
            self.assertNotIn(Path(path).parent, blocked, 'open through blocked parent')
            return raw_open(path, flags, *args, **kwargs)
        def read(path):
            self.assertNotIn(path.parent, blocked, 'read through blocked parent')
            files[path] = files.get(path, 0) + 1
            if path == fail_file or (path == recheck_file and files[path] == 2):
                raise PermissionError('injected file failure: ' + str(path))
            return read_regular_snapshot(path)
        def recheck(reads, report):
            self.assertTrue(blocked.isdisjoint(reads.directories), 'blocked parent has fingerprint')
            self.assertTrue(all(p.parent not in blocked for p in reads.observations))
            with patch.object(Path, 'lstat', new=raw_lstat), patch('os.open', new=raw_open):
                self.assertEqual(before, snapshot(self.root))
                if change_file:
                    change_file.write_bytes(change_file.read_bytes() + b'\n- independent change')
                if become_safe:
                    for parent, moved in saved.items():
                        parent.unlink()
                        moved.rename(parent)
                injected.append(snapshot(self.root))
            return raw_recheck(reads, report)
        try:
            with patch('notewrench.zoterologseq.recovery.directory_snapshot', side_effect=directory), \
                 patch.object(Path, 'lstat', new=lstat), patch('os.open', side_effect=open_file), \
                 patch('notewrench.zoterologseq.recovery.read_regular_snapshot', side_effect=read), \
                 patch.object(AuditReads, 'recheck', new=recheck), \
                 patch('sys.stdout', new_callable=io.StringIO) as output:
                code = main([str(self.batch), '--graph', str(self.graph), '--recovery-audit'])
            partial = bool(unknown or fail_file or recheck_file)
            expected.update(status='partial' if partial else 'conflict', ambiguous=True)
            expected['conflicts'] = [
                {'code': 'unsafe-initial-directory', 'path': str(parent),
                 'message': 'expected a non-linked directory: ' + str(parent)}
                for parent in (self.graph / 'pages', self.batch / 'pages') if parent in unsafe
            ] + expected['conflicts']
            if unknown:
                expected['initial_errors'] = [
                    {'code': 'unreadable-initial-directory', 'path': str(p),
                     'message': 'injected initial directory failure: ' + str(p)} for p in unknown]
            if fail_file:
                expected.setdefault('initial_errors', []).append(
                    {'code': 'unreadable-initial-evidence', 'path': str(fail_file),
                     'message': 'injected file failure: ' + str(fail_file)})
            if recheck_file:
                expected['recheck_errors'] = [{'code': 'unreadable-during-recheck', 'path': str(recheck_file),
                                               'message': 'injected file failure: ' + str(recheck_file)}]
            if change_file:
                expected['conflicts'].append({'code': 'changed-during-audit', 'path': str(change_file),
                    'message': 'evidence changed during audit; initial observation retained'})
            if fail_file == self.graph / MANIFEST_NAME:
                expected['manifest'].update(presence='unreadable', sha256=None, classification='unknown',
                                             diagnostic='injected file failure: ' + str(fail_file))
            for page in expected['pages']:
                for base, location in ((self.graph, 'original'), (self.batch, 'archive')):
                    path = base / page['path']
                    if path.parent in blocked or path == fail_file:
                        is_unsafe = path.parent in unsafe
                        diagnostic = ('initial parent directory is unsafe: ' + str(path.parent) if is_unsafe else
                                      'initial parent directory observation unavailable: ' + str(path.parent)
                                      if path.parent in unknown else 'injected file failure: ' + str(path))
                        page[location].update(presence='unsafe' if is_unsafe else 'unreadable', sha256=None,
                                              full_hash='unknown', ownership='unknown', diagnostic=diagnostic)
                        page.update(ambiguous=True)
                    self.assertEqual(0 if path.parent in blocked else 1 if path == fail_file else 2,
                                     files.get(path, 0), str(path))
                page['locations'] = 'unknown' if any(page[n]['presence'] == 'unreadable'
                                                    for n in ('original', 'archive')) else 'unsafe'
            self.assertEqual('partial' if partial else 'conflict', json.loads(output.getvalue())['status'])
            self.assertEqual(3 if partial else 2, code, output.getvalue())
            self.assert_observations_equal(expected, json.loads(output.getvalue()))
            self.assertEqual([snapshot(self.root)], injected, 'audit wrote after injection')
            self.assertFalse((self.graph / LOCK_NAME).exists())
            for parent in (self.graph / 'pages', self.batch / 'pages'):
                self.assertEqual(1 if parent in blocked else 2, directories[parent])
            for name in ('plan.json', 'receipt.json', 'manifest-before.json',
                         'manifest-after.json', 'restore-receipt.json'):
                self.assertEqual(2, files[self.batch / name])
            self.assertEqual(1 if fail_file == self.graph / MANIFEST_NAME else 2,
                             files[self.graph / MANIFEST_NAME])
        finally:
            if not become_safe or not injected:
                for parent, moved in saved.items():
                    parent.unlink()
                    moved.rename(parent)

    def test_initial_directory_unsafe_archive_restore_exact_contract(self):
        self.set_status('complete')
        for restored in (False, True):
            if restored:
                decision = self.root / 'restore.json'
                plan_restore(self.batch, self.graph, decision)
                apply_restore(self.batch, self.graph, decision)
                (self.graph / LOCK_NAME).unlink()
            for parent in (self.graph / 'pages', self.batch / 'pages'):
                for kind in ('linked', 'dangling', 'file', 'fifo'):
                    with self.subTest(restored=restored, parent=parent, kind=kind):
                        self.assert_initial_directory_unsafe({parent: kind})
            self.assert_initial_directory_unsafe({self.graph / 'pages': 'linked',
                                                  self.batch / 'pages': 'fifo'})

    def test_initial_directory_unsafe_missing_opposite_and_no_retry_when_safe(self):
        self.set_status('complete')
        for parent, opposite in ((self.graph / 'pages', self.batch / 'pages'),
                                 (self.batch / 'pages', self.graph / 'pages')):
            moved = opposite.rename(self.root / 'missing-pages')
            self.assert_initial_directory_unsafe({parent: 'dangling'})
            moved.rename(opposite)
        self.assert_initial_directory_unsafe({self.graph / 'pages': 'file'}, become_safe=True)

    def test_initial_directory_unsafe_mixed_unknown_file_io_and_changes(self):
        self.set_status('complete')
        self.assert_initial_directory_unsafe({self.graph / 'pages': 'fifo'},
                                              unknown=[self.batch / 'pages'])
        self.assert_initial_directory_unsafe({self.batch / 'pages': 'linked'},
                                              unknown=[self.graph / 'pages'])
        for restored in (False, True):
            safe, blocked = (self.batch, self.graph) if not restored else (self.graph, self.batch)
            if restored:
                decision = self.root / 'restore.json'
                plan_restore(self.batch, self.graph, decision)
                apply_restore(self.batch, self.graph, decision)
                (self.graph / LOCK_NAME).unlink()
            changed = safe / self.entries[1]['path']
            content = changed.read_bytes()
            self.assert_initial_directory_unsafe({blocked / 'pages': 'file'},
                fail_file=safe / self.entries[0]['path'], recheck_file=self.batch / 'receipt.json',
                change_file=changed)
            changed.write_bytes(content)
        self.assert_initial_directory_unsafe({self.batch / 'pages': 'fifo'},
            fail_file=self.graph / MANIFEST_NAME, recheck_file=self.graph / self.entries[0]['path'])

    def assert_initial_directory_partial(self, failed, fail_file=None, recheck_file=None,
                                         change_file=None, change_directory=None):
        """Exact retained output; failed parents and descendants are never retried."""
        from notewrench.zoterologseq.cli import main
        from notewrench.zoterologseq.recovery import AuditReads, audit_recovery, directory_snapshot, read_regular_snapshot
        expected = audit_recovery(self.batch, self.graph)
        before, injected, files, directories = snapshot(self.graph), [], {}, {}
        raw_recheck, raw_lstat = AuditReads.recheck, Path.lstat
        def directory(path):
            directories[path] = directories.get(path, 0) + 1
            if path in failed:
                raise PermissionError('injected initial directory failure: ' + str(path))
            return directory_snapshot(path)
        def lstat(path, *args, **kwargs):
            self.assertNotIn(path.parent, failed, 'observed descendant through unknown parent')
            return raw_lstat(path, *args, **kwargs)
        def read(path):
            self.assertNotIn(path.parent, failed, 'read descendant through unknown parent')
            files[path] = files.get(path, 0) + 1
            if path == fail_file or (files[path] == 2 and path == recheck_file):
                raise PermissionError('injected file failure: ' + str(path))
            return read_regular_snapshot(path)
        def recheck(reads, report):
            self.assertTrue(set(failed).isdisjoint(reads.directories), 'unknown directory has fingerprint')
            with patch.object(Path, 'lstat', new=raw_lstat):
                self.assertEqual(before, snapshot(self.graph))
                if change_file:
                    change_file.write_bytes(change_file.read_bytes() + b'\n- independent change')
                if change_directory:
                    self.replace_directory(change_directory)
                injected.append(snapshot(self.graph))
            return raw_recheck(reads, report)
        with patch('notewrench.zoterologseq.recovery.directory_snapshot', side_effect=directory), \
             patch.object(Path, 'lstat', new=lstat), \
             patch('notewrench.zoterologseq.recovery.read_regular_snapshot', side_effect=read), \
             patch.object(AuditReads, 'recheck', new=recheck), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            code = main([str(self.batch), '--graph', str(self.graph), '--recovery-audit'])
        self.assertEqual(3, code, output.getvalue())
        self.assertEqual('partial', json.loads(output.getvalue())['status'], output.getvalue())
        expected.update(status='partial', ambiguous=True, initial_errors=[
            {'code': 'unreadable-initial-directory', 'path': str(path),
             'message': 'injected initial directory failure: ' + str(path)} for path in failed])
        if fail_file:
            expected['initial_errors'].append({'code': 'unreadable-initial-evidence',
                'path': str(fail_file), 'message': 'injected file failure: ' + str(fail_file)})
            self.assertEqual(1, files[fail_file])
        if recheck_file:
            expected['recheck_errors'] = [{'code': 'unreadable-during-recheck',
                'path': str(recheck_file), 'message': 'injected file failure: ' + str(recheck_file)}]
        expected['conflicts'].extend({'code': 'changed-during-audit', 'path': str(path),
            'message': 'evidence changed during audit; initial observation retained'}
            for path in (change_file, change_directory) if path)
        if fail_file == self.graph / MANIFEST_NAME:
            expected['manifest'].update(presence='unreadable', sha256=None, classification='unknown',
                                        diagnostic='injected file failure: ' + str(fail_file))
        for page in expected['pages']:
            for base, location in ((self.graph, 'original'), (self.batch, 'archive')):
                path = base / page['path']
                if path.parent in failed or path == fail_file:
                    diagnostic = ('initial parent directory observation unavailable: ' + str(path.parent)
                                  if path.parent in failed else 'injected file failure: ' + str(path))
                    page[location].update(presence='unreadable', sha256=None, full_hash='unknown',
                                          ownership='unknown', diagnostic=diagnostic)
                    page.update(locations='unknown', ambiguous=True)
        self.assert_observations_equal(expected, json.loads(output.getvalue()))
        self.assertEqual([snapshot(self.graph)], injected, 'audit changed graph after injection')
        self.assertFalse((self.graph / LOCK_NAME).exists())
        for parent in (self.graph / 'pages', self.batch / 'pages'):
            self.assertEqual(1 if parent in failed else 2, directories[parent])
        for name in ('plan.json', 'receipt.json', 'manifest-before.json',
                     'manifest-after.json', 'restore-receipt.json'):
            self.assertEqual(2, files[self.batch / name], 'metadata not independently rechecked')
        self.assertEqual(1 if fail_file == self.graph / MANIFEST_NAME else 2,
                         files[self.graph / MANIFEST_NAME])
        for entry in self.entries:
            for base in (self.graph, self.batch):
                path = base / entry['path']
                self.assertEqual(0 if path.parent in failed else 1 if path == fail_file else 2,
                                 files.get(path, 0))

    def test_initial_directory_partial_archive_and_restore_exact_contract(self):
        self.set_status('complete')
        for restored in (False, True):
            if restored:
                decision = self.root / 'restore.json'
                plan_restore(self.batch, self.graph, decision)
                apply_restore(self.batch, self.graph, decision)
                (self.graph / LOCK_NAME).unlink()
            parents = (self.graph / 'pages', self.batch / 'pages')
            for failed in ([parents[0]], [parents[2]], list(parents)):
                with self.subTest(restored=restored, failed=failed):
                    self.assert_initial_directory_partial(failed)

    def test_initial_directory_partial_mixed_io_and_independent_changes(self):
        self.set_status('complete')
        self.assert_initial_directory_partial([self.graph / 'pages'],
            fail_file=self.batch / self.entries[0]['path'], recheck_file=self.batch / 'receipt.json',
            change_file=self.batch / self.entries[1]['path'], change_directory=self.batch / 'pages')
        self.assert_initial_directory_partial([self.batch / 'pages'],
            fail_file=self.graph / MANIFEST_NAME, recheck_file=self.graph / self.entries[0]['path'])

    def test_initial_directory_partial_restore_mixed_io_and_changes(self):
        self.set_status('complete')
        decision = self.root / 'restore.json'
        plan_restore(self.batch, self.graph, decision)
        apply_restore(self.batch, self.graph, decision)
        (self.graph / LOCK_NAME).unlink()
        self.assert_initial_directory_partial([self.batch / 'pages'],
            fail_file=self.graph / self.entries[0]['path'],
            recheck_file=self.batch / 'restore-receipt.json',
            change_file=self.graph / self.entries[1]['path'], change_directory=self.graph / 'pages')

    def test_initial_directory_partial_missing_and_unsafe_opposite(self):
        self.set_status('complete')
        for parent, failed in ((self.graph / 'pages', self.batch / 'pages'),
                               (self.batch / 'pages', self.graph / 'pages')):
            moved = parent.rename(self.root / 'saved-pages')
            self.assert_initial_directory_partial([failed])  # Safely missing stays missing.
            moved.rename(parent)
        outside = self.root / 'outside'
        outside.write_bytes(b'never read')
        target = self.graph / self.entries[0]['path']
        target.symlink_to(outside)
        self.assert_initial_directory_partial([self.batch / 'pages'])
        self.assertEqual(b'never read', outside.read_bytes())

    def assert_initial_directory_metadata_gate(self, unsafe=False):
        from notewrench.zoterologseq.cli import main
        from notewrench.zoterologseq.recovery import directory_snapshot, read_regular_snapshot
        failed = self.graph / 'pages'
        if unsafe:
            saved_parent = failed.rename(self.root / 'metadata-gate-pages')
            failed.symlink_to(saved_parent, target_is_directory=True)
        def directory(path):
            if path == failed:
                if unsafe:
                    return directory_snapshot(path)
                raise PermissionError('injected directory failure')
            return directory_snapshot(path)
        def invoke(expected_code, read_override=None):
            before = snapshot(self.graph)
            with patch('notewrench.zoterologseq.recovery.directory_snapshot', side_effect=directory), \
                 patch('notewrench.zoterologseq.recovery.read_regular_snapshot',
                       side_effect=read_override or read_regular_snapshot) as read, \
                 patch('sys.stdout', new_callable=io.StringIO) as output:
                code = main([str(self.batch), '--graph', str(self.graph), '--recovery-audit'])
            self.assertEqual(expected_code, code, output.getvalue())
            self.assertEqual({'status', 'error'}, set(json.loads(output.getvalue())))
            self.assertTrue(all(call.args[0].parent == self.batch for call in read.call_args_list))
            self.assertEqual(before, snapshot(self.graph))
            self.assertFalse((self.graph / LOCK_NAME).exists())
        for name in ('plan.json', 'receipt.json', 'manifest-before.json',
                     'manifest-after.json', 'restore-receipt.json'):
            path = self.batch / name
            saved = path.read_bytes() if path.exists() else None
            for kind in ('invalid', 'unreadable', 'linked'):
                with self.subTest(name=name, kind=kind):
                    try:
                        if kind == 'invalid':
                            path.write_bytes(b'{}')
                            invoke(2)
                        elif kind == 'unreadable':
                            def read(path_to_read):
                                if path_to_read == path:
                                    raise PermissionError('injected metadata failure')
                                return read_regular_snapshot(path_to_read)
                            invoke(3, read)
                        else:
                            path.unlink(missing_ok=True)
                            path.symlink_to(self.root / 'absent')
                            invoke(2)
                    finally:
                        path.unlink(missing_ok=True)
                        if saved is not None:
                            path.write_bytes(saved)
        # Full ordered transition and arbitrary/subset/reordered path checks must
        # still precede any derived observations, even with an unknown parent.
        original_batch = self.batch
        plan = json.loads((self.batch / 'plan.json').read_bytes())
        for kind in ('path', 'subset', 'reordered'):
            value = copy.deepcopy(plan)
            if kind == 'path':
                value['entries'][0]['path'] = '../outside.md'
            elif kind == 'subset':
                value['entries'].pop()
            else:
                value['entries'].reverse()
            value['decision_sha256'] = digest(json_bytes({k: v for k, v in value.items()
                                                         if k != 'decision_sha256'}))
            (self.batch / 'plan.json').write_bytes(json_bytes(value))
            self.batch = self.batch.rename(self.batch.with_name(value['decision_sha256']))
            invoke(2)
            self.batch = self.batch.rename(original_batch)
        (self.batch / 'plan.json').write_bytes(json_bytes(plan))
        if unsafe:
            failed.unlink()
            saved_parent.rename(failed)

    def test_initial_directory_partial_metadata_gate(self):
        self.assert_initial_directory_metadata_gate()

    def test_initial_directory_unsafe_metadata_ancestors_are_error_only(self):
        from notewrench.zoterologseq.cli import main
        for target in (self.batch, self.batch.parent, self.graph):
            moved = target.rename(self.root / 'moved-ancestor')
            for kind in ('linked', 'fifo'):
                if kind == 'linked':
                    target.symlink_to(moved, target_is_directory=True)
                else:
                    os.mkfifo(target)
                def guarded(path):
                    self.fail('unsafe metadata ancestor reached file reader: ' + str(path))
                with patch('notewrench.zoterologseq.recovery.read_regular_snapshot', side_effect=guarded), \
                     patch('sys.stdout', new_callable=io.StringIO) as output:
                    code = main([str(self.batch), '--graph', str(self.graph), '--recovery-audit'])
                self.assertEqual(2, code, output.getvalue())
                self.assertEqual({'status', 'error'}, set(json.loads(output.getvalue())))
                target.unlink()
            moved.rename(target)

    def test_initial_directory_unsafe_metadata_gate(self):
        self.assert_initial_directory_metadata_gate(unsafe=True)

    def test_directory_newly_unsafe_is_not_opened_and_keeps_initial_observations(self):
        self.set_status('complete')
        for directory in (self.graph / 'pages', self.batch / 'pages'):
            for kind in ('symlink', 'file', 'fifo'):
                with self.subTest(directory=directory, kind=kind):
                    moved = self.root / 'saved-pages'
                    targets = [directory / e['path'].split('/')[-1] for e in self.entries]
                    def change():
                        directory.rename(moved)
                        if kind == 'symlink':
                            directory.symlink_to(moved, target_is_directory=True)
                        elif kind == 'file':
                            directory.write_bytes(b'not a directory')
                        else:
                            os.mkfifo(directory)
                    self.assert_changed_during_audit(targets + [directory], change,
                                                     unsafe=targets + [directory])
                    directory.unlink()
                    moved.rename(directory)

    def test_directory_identity_is_descriptor_bound_and_open_is_no_follow(self):
        from notewrench.zoterologseq.recovery import AuditReads
        target = self.graph / 'pages'
        info = target.stat()
        raw_fstat, raw_open = os.fstat, os.open
        injected = []
        def opened(path, flags, *args, **kwargs):
            self.assertEqual(target, path)
            self.assertEqual(os.O_NOFOLLOW | os.O_DIRECTORY,
                             flags & (os.O_NOFOLLOW | os.O_DIRECTORY))
            return raw_open(path, flags, *args, **kwargs)
        def fstat(fd):
            observed = raw_fstat(fd)
            self.replace_directory(target)
            injected.append(snapshot(self.graph))
            return observed
        reads = AuditReads()
        with patch('os.open', side_effect=opened), patch('os.fstat', side_effect=fstat):
            reads.directory(target)
        self.assertEqual(('present', None, (info.st_dev, info.st_ino)), reads.directories[target])
        report = {'conflicts': []}
        self.assertEqual({target}, reads.recheck(report))
        self.assertEqual([{'code': 'changed-during-audit', 'path': str(target),
                          'message': 'evidence changed during audit; initial observation retained'}],
                         report['conflicts'])
        self.assertEqual([snapshot(self.graph)], injected)

    def test_directory_stable_identity_and_unrelated_ancestors_are_not_changes(self):
        self.set_status('complete')
        def change():
            for target in (self.graph / 'pages', self.batch / 'pages'):
                info = target.stat()
                (target / 'unrelated.txt').write_bytes(b'unobserved')
                os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
            # Replace graph itself, carrying every observed directory/file intact.
            self.replace_directory(self.graph)
        self.directory_audit(change)

    def test_identity_replacement_archive_candidates_manifest_and_metadata(self):
        self.set_status('complete')
        targets = [self.batch / self.entries[0]['path'], self.graph / MANIFEST_NAME]
        targets += [self.batch / name for name in (
            'plan.json', 'receipt.json', 'manifest-before.json', 'manifest-after.json')]
        for target in targets:
            with self.subTest(target=target):
                self.assert_changed_during_audit([target], lambda: self.replace_identical(target))

    def test_identity_replacement_restore_receipt_and_original_candidate(self):
        self.set_status('complete')
        decision = self.root / 'restore.json'
        plan_restore(self.batch, self.graph, decision)
        apply_restore(self.batch, self.graph, decision)
        (self.graph / LOCK_NAME).unlink()
        for target in (self.batch / 'restore-receipt.json', self.graph / self.entries[0]['path']):
            with self.subTest(target=target):
                self.assert_changed_during_audit([target], lambda: self.replace_identical(target))

    def test_identity_replacement_keeps_partial_io_precedence_and_unknowns(self):
        self.set_status('complete')
        target = self.batch / self.entries[-1]['path']
        self.assert_recheck_io_partial([self.batch / 'plan.json'], changed=[target],
                                      mutate=lambda: self.replace_identical(target))
        self.assert_initial_io_partial([self.graph / MANIFEST_NAME],
            recheck_failed=[self.batch / 'receipt.json'], changed=[target],
            mutate=lambda: self.replace_identical(target))
        # Failed initial evidence has no identity and must never be rechecked.
        self.assert_initial_io_partial([self.batch / self.entries[0]['path']],
            mutate=lambda: self.replace_identical(self.batch / self.entries[0]['path']))

    def test_identity_is_bound_to_descriptor_when_path_replaced_inside_read(self):
        from notewrench.zoterologseq.recovery import AuditReads
        target = self.batch / self.entries[0]['path']
        saved, info = target.read_bytes(), target.stat()
        real_fdopen = os.fdopen
        injected = []
        test = self
        class ReplacingStream:
            def __init__(self, *args, **kwargs):
                self.stream = real_fdopen(*args, **kwargs)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return self.stream.__exit__(*args)
            def fileno(self):
                return self.stream.fileno()
            def read(self):
                data = self.stream.read()
                test.replace_identical(target)
                injected.append(snapshot(test.graph))
                return data
        reads = AuditReads()
        with patch('os.fdopen', side_effect=ReplacingStream):
            self.assertEqual(saved, reads.read(target))
        # A post-read path stat would associate these bytes with the new inode
        # and miss the replacement on the following completion comparison.
        report = {'conflicts': []}
        self.assertEqual({target}, reads.recheck(report))
        self.assertEqual([{'code': 'changed-during-audit', 'path': str(target),
                          'message': 'evidence changed during audit; initial observation retained'}],
                         report['conflicts'])
        self.assertNotEqual(info.st_ino, target.stat().st_ino)
        self.assertEqual(1, len(injected))
        self.assertEqual(injected[0], snapshot(self.graph))

    def test_identity_stable_bytes_and_mtime_only_change_are_not_conflicts(self):
        from notewrench.zoterologseq.recovery import audit_recovery, read_regular_snapshot
        self.set_status('complete')
        target = self.batch / self.entries[0]['path']
        expected = audit_recovery(self.batch, self.graph)
        for rewrite in (False, True):
            injected = []
            before = snapshot(self.graph)
            def read(path):
                data = read_regular_snapshot(path)
                if not injected and path == self.batch / self.entries[-1]['path']:
                    self.assertEqual(before, snapshot(self.graph))
                    info = target.stat()
                    if rewrite:
                        target.write_bytes(target.read_bytes())
                    os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000))
                    self.assertEqual(info.st_ino, target.stat().st_ino)
                    injected.append(snapshot(self.graph))
                return data
            with patch('notewrench.zoterologseq.recovery.read_regular_snapshot', side_effect=read):
                self.assertEqual(expected, audit_recovery(self.batch, self.graph))
            self.assertEqual(1, len(injected))
            self.assertEqual(injected[0], snapshot(self.graph))
            self.assertFalse((self.graph / LOCK_NAME).exists())

    def test_changed_during_audit_candidate_notes_keep_initial_hash_and_ownership(self):
        self.set_status('complete')  # A previously unambiguous result must change too.
        target = self.batch / self.entries[0]['path']
        data = target.read_bytes()
        self.assert_changed_during_audit([target], lambda: target.write_bytes(data + b'\n- New human note'))

    def test_changed_during_audit_presence_transitions_and_multiple_paths(self):
        original = self.graph / self.entries[0]['path']
        archived = self.batch / self.entries[0]['path']
        live = self.graph / MANIFEST_NAME
        live_data, page_data = live.read_bytes(), archived.read_bytes()
        def move_and_remove_manifest():
            archived.rename(original)
            live.unlink()
        self.assert_changed_during_audit([original, archived, live], move_and_remove_manifest)
        # The next audit initially sees no manifest; even malformed new bytes
        # are a race rather than a reinterpretation of the initial evidence.
        self.assert_changed_during_audit([live], lambda: live.write_bytes(b'new manifest bytes'))
        self.assertEqual(page_data, original.read_bytes())
        self.assertNotEqual(live_data, live.read_bytes())

    def test_changed_during_audit_required_metadata_not_reinterpreted(self):
        for name in ('plan.json', 'receipt.json', 'manifest-before.json', 'manifest-after.json'):
            with self.subTest(name=name):
                target = self.batch / name
                saved = target.read_bytes()
                try:
                    self.assert_changed_during_audit([target], lambda: target.write_bytes(b'not valid metadata'))
                finally:
                    target.write_bytes(saved)

    def test_changed_during_audit_absent_restore_receipt_is_rechecked(self):
        target = self.batch / 'restore-receipt.json'
        self.assertFalse(target.exists())
        self.assert_changed_during_audit([target], lambda: target.write_bytes(b'{}'))

    def test_changed_during_audit_incomplete_restore_receipt_and_original(self):
        self.set_status('complete')
        decision = self.root / 'restore.json'
        plan_restore(self.batch, self.graph, decision)
        apply_restore(self.batch, self.graph, decision)
        (self.graph / LOCK_NAME).unlink()
        self.set_status('prepared', restore=True)
        (self.graph / 'pages/Other.md').rename(self.batch / 'pages/Other.md')
        receipt = self.batch / 'restore-receipt.json'
        page = self.graph / self.entries[0]['path']
        data = page.read_bytes()
        def change():
            self.set_status('rollback-incomplete', restore=True)
            page.write_bytes(data + b'\n- Human note during restore audit')
        self.assert_changed_during_audit([receipt, page], change)

    def test_changed_during_audit_new_unsafe_evidence_is_never_opened(self):
        for target in (self.batch / 'pages/Gone.md', self.graph / MANIFEST_NAME,
                       self.batch / 'receipt.json', self.batch / 'restore-receipt.json'):
            for kind in ('symlink', 'fifo'):
                with self.subTest(target=target, kind=kind):
                    saved = target.read_bytes() if target.exists() else None
                    def change():
                        if target.exists():
                            target.unlink()
                        if kind == 'symlink':
                            target.symlink_to(self.root / 'does-not-exist')
                        else:
                            os.mkfifo(target)
                    try:
                        self.assert_changed_during_audit([target], change, unsafe=[target])
                    finally:
                        target.unlink()
                        if saved is not None:
                            target.write_bytes(saved)

    def test_changed_during_audit_live_bytes_and_disappearing_receipt(self):
        live = self.graph / MANIFEST_NAME
        receipt = self.batch / 'receipt.json'
        def change():
            live.write_bytes(b'malformed live manifest')
            receipt.unlink()
        self.assert_changed_during_audit([live, receipt], change)

    def test_changed_during_audit_linked_ancestor_is_not_followed_on_recheck(self):
        directory = self.graph / 'pages'
        targets = [self.graph / entry['path'] for entry in self.entries]
        def change():
            moved = self.root / 'moved-pages'
            directory.rename(moved)
            directory.symlink_to(moved, target_is_directory=True)
        self.assert_changed_during_audit(targets + [directory], change, unsafe=targets + [directory])

    def assert_recheck_io_partial(self, failed, changed=None, mutate=None):
        """Fail exactly the second read; retain every other initial field."""
        from notewrench.zoterologseq.cli import main
        from notewrench.zoterologseq.recovery import audit_recovery, read_regular_snapshot
        before = snapshot(self.graph)
        initial = audit_recovery(self.batch, self.graph)
        self.assertEqual(before, snapshot(self.graph))
        seen = {}
        injected = []
        def read(path):
            seen[path] = seen.get(path, 0) + 1
            if seen[path] == 2 and path in failed:
                raise PermissionError('injected recheck failure: ' + path.name)
            data = read_regular_snapshot(path)
            if mutate and not injected and path == self.batch / self.entries[-1]['path']:
                self.assertEqual(before, snapshot(self.graph), 'audit wrote before injection')
                mutate()
                injected.append(snapshot(self.graph))
            return data
        with patch('notewrench.zoterologseq.recovery.read_regular_snapshot', side_effect=read), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            code = main([str(self.batch), '--graph', str(self.graph), '--recovery-audit'])
        self.assertEqual(3, code, output.getvalue())
        self.assertEqual(injected[0] if mutate else before, snapshot(self.graph),
                         'audit changed graph bytes/mtime/inodes/inventory')
        self.assertFalse((self.graph / LOCK_NAME).exists())
        expected_paths = {self.batch / name for name in (
            'plan.json', 'receipt.json', 'manifest-before.json', 'manifest-after.json', 'restore-receipt.json')}
        expected_paths.add(self.graph / MANIFEST_NAME)
        expected_paths.update(base / entry['path'] for base in (self.graph, self.batch) for entry in self.entries)
        self.assertEqual({path: 2 for path in expected_paths}, seen,
                         'a failed read stopped independent rechecks')
        affected = set(failed) | set(changed or [])
        for page in initial['pages']:
            if any(base / page['path'] in affected for base in (self.graph, self.batch)):
                page['ambiguous'] = True
        initial.update(status='partial', ambiguous=True, recheck_errors=[
            {'code': 'unreadable-during-recheck', 'path': str(path),
             'message': 'injected recheck failure: ' + path.name} for path in failed])
        initial['conflicts'].extend(
            {'code': 'changed-during-audit', 'path': str(path),
             'message': 'evidence changed during audit; initial observation retained'} for path in (changed or []))
        self.assert_observations_equal(initial, json.loads(output.getvalue()))

    def test_recheck_io_failure_retains_initial_archive_observations(self):
        # Covers required metadata, optional absence, live manifest and both
        # candidate locations, including the initially missing original.
        paths = [self.batch / name for name in (
            'plan.json', 'receipt.json', 'manifest-before.json', 'manifest-after.json', 'restore-receipt.json')]
        paths += [self.graph / MANIFEST_NAME, self.graph / self.entries[0]['path'],
                  self.batch / self.entries[0]['path']]
        for path in paths:
            with self.subTest(path=path):
                self.assert_recheck_io_partial([path])

    def test_recheck_io_failure_retains_initial_restore_observations(self):
        self.set_status('complete')
        decision = self.root / 'restore.json'
        plan_restore(self.batch, self.graph, decision)
        apply_restore(self.batch, self.graph, decision)
        (self.graph / LOCK_NAME).unlink()
        self.set_status('prepared', restore=True)
        (self.graph / 'pages/Other.md').rename(self.batch / 'pages/Other.md')
        self.assert_recheck_io_partial([self.batch / 'restore-receipt.json',
                                       self.graph / self.entries[0]['path']])

    def test_recheck_io_failure_continues_and_keeps_later_change_conflicts(self):
        target = self.batch / self.entries[-1]['path']
        data = target.read_bytes()
        self.assert_recheck_io_partial(
            [self.batch / 'plan.json', self.graph / MANIFEST_NAME], changed=[target],
            mutate=lambda: target.write_bytes(data + b'\n- New human note'))

    def test_recheck_io_failure_marks_previously_unambiguous_page(self):
        self.set_status('complete')
        self.assert_recheck_io_partial([self.batch / self.entries[0]['path']])

    def assert_initial_io_partial(self, failed, recheck_failed=(), changed=(), mutate=None):
        """First-read failures leave explicit unknowns; safe paths still recheck."""
        from notewrench.zoterologseq.cli import main
        from notewrench.zoterologseq.recovery import audit_recovery, read_regular_snapshot
        before = snapshot(self.graph)
        expected = audit_recovery(self.batch, self.graph)
        seen, injected = {}, []
        def read(path):
            seen[path] = seen.get(path, 0) + 1
            if seen[path] == 1 and path in failed:
                raise PermissionError('injected initial failure: ' + path.name)
            if seen[path] == 2 and path in recheck_failed:
                raise PermissionError('injected recheck failure: ' + path.name)
            data = read_regular_snapshot(path)
            if mutate and not injected and path == self.batch / self.entries[-1]['path']:
                self.assertEqual(before, snapshot(self.graph))
                mutate()
                injected.append(snapshot(self.graph))
            return data
        with patch('notewrench.zoterologseq.recovery.read_regular_snapshot', side_effect=read), \
             patch('sys.stdout', new_callable=io.StringIO) as output:
            code = main([str(self.batch), '--graph', str(self.graph), '--recovery-audit'])
        self.assertEqual(injected[0] if mutate else before, snapshot(self.graph))
        self.assertFalse((self.graph / LOCK_NAME).exists())
        self.assertEqual(3, code, output.getvalue())
        paths = {self.batch / name for name in ('plan.json', 'receipt.json',
                 'manifest-before.json', 'manifest-after.json', 'restore-receipt.json')}
        paths.add(self.graph / MANIFEST_NAME)
        paths.update(base / e['path'] for base in (self.graph, self.batch) for e in self.entries)
        self.assertEqual({p: 1 if p in failed else 2 for p in paths}, seen,
                         'independent reads/rechecks skipped or unknown initial evidence retried')
        expected.update(status='partial', ambiguous=True, initial_errors=[
            {'code': 'unreadable-initial-evidence', 'path': str(path),
             'message': 'injected initial failure: ' + path.name} for path in failed])
        if recheck_failed:
            expected['recheck_errors'] = [
                {'code': 'unreadable-during-recheck', 'path': str(path),
                 'message': 'injected recheck failure: ' + path.name} for path in recheck_failed]
        expected['conflicts'].extend(
            {'code': 'changed-during-audit', 'path': str(path),
             'message': 'evidence changed during audit; initial observation retained'} for path in changed)
        def unknown(observation, path):
            observation.update(presence='unreadable', sha256=None,
                               diagnostic='injected initial failure: ' + path.name)
        path = self.graph / MANIFEST_NAME
        if path in failed:
            unknown(expected['manifest'], path)
            expected['manifest']['classification'] = 'unknown'
        for page in expected['pages']:
            for base, location in ((self.graph, 'original'), (self.batch, 'archive')):
                path = base / page['path']
                if path in failed:
                    unknown(page[location], path)
                    page[location].update(full_hash='unknown', ownership='unknown')
                    page.update(locations='unknown', ambiguous=True)
                if path in set(recheck_failed) | set(changed):
                    page['ambiguous'] = True
        self.assert_observations_equal(expected, json.loads(output.getvalue()))

    def test_initial_io_partial_last_candidate_keeps_prior_observations(self):
        self.assert_initial_io_partial([self.batch / self.entries[-1]['path']])

    def test_initial_io_partial_continues_manifest_and_both_candidate_locations(self):
        paths = [self.graph / MANIFEST_NAME]
        paths += [base / e['path'] for e in self.entries for base in (self.graph, self.batch)]
        for path in paths:
            with self.subTest(path=path):
                self.assert_initial_io_partial([path])
        self.assert_initial_io_partial([paths[0], paths[1], paths[2]])

    def test_initial_io_partial_restore_and_mixed_recheck_change(self):
        self.set_status('complete')
        decision = self.root / 'restore.json'
        plan_restore(self.batch, self.graph, decision)
        apply_restore(self.batch, self.graph, decision)
        (self.graph / LOCK_NAME).unlink()
        self.set_status('prepared', restore=True)
        (self.graph / 'pages/Other.md').rename(self.batch / 'pages/Other.md')
        self.assert_initial_io_partial([self.graph / self.entries[0]['path']])
        target = self.batch / self.entries[-1]['path']
        data = target.read_bytes()
        self.assert_initial_io_partial(
            [self.graph / MANIFEST_NAME, self.graph / self.entries[0]['path']],
            recheck_failed=[self.batch / 'plan.json', self.batch / 'restore-receipt.json'],
            changed=[target], mutate=lambda: target.write_bytes(data + b'\n- New note'))

    def test_initial_io_partial_keeps_unsafe_and_hash_ownership_conflicts(self):
        # Unsafe counterpart must not turn an unreadable location into missing.
        target = self.batch / self.entries[0]['path']
        target.unlink()
        target.symlink_to(self.root / 'absent')
        edited = self.batch / self.entries[-1]['path']
        edited.write_bytes(edited.read_bytes().replace(b'csl-type:: book', b'csl-type:: changed'))
        self.assert_initial_io_partial([self.graph / self.entries[0]['path']])

    def test_initial_io_partial_metadata_stays_error_only_before_derived_reads(self):
        from notewrench.zoterologseq.cli import main
        from notewrench.zoterologseq.recovery import read_regular_snapshot
        for name in ('plan.json', 'receipt.json', 'manifest-before.json',
                     'manifest-after.json', 'restore-receipt.json'):
            target = self.batch / name
            seen = []
            def read(path):
                seen.append(path)
                if path == target:
                    raise PermissionError('injected metadata failure')
                return read_regular_snapshot(path)
            before = snapshot(self.graph)
            with patch('notewrench.zoterologseq.recovery.read_regular_snapshot', side_effect=read), \
                 patch('sys.stdout', new_callable=io.StringIO) as output:
                code = main([str(self.batch), '--graph', str(self.graph), '--recovery-audit'])
            self.assertEqual(3, code)
            self.assertEqual({'status': 'error', 'error': 'injected metadata failure'},
                             json.loads(output.getvalue()))
            self.assertTrue(all(path.parent == self.batch for path in seen))
            self.assertEqual(before, snapshot(self.graph))
            self.assertFalse((self.graph / LOCK_NAME).exists())


if __name__ == '__main__':
    unittest.main()
