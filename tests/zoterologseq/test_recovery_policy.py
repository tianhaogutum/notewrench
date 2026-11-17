# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 tianhaogutum
"""Opt-in CLI exit policy: identical evidence, JSON, reads and no writes."""
from collections import Counter
import fcntl
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from . import test_recovery
from .test_recovery import snapshot
from notewrench.zoterologseq.model import LOCK_NAME, MANIFEST_NAME
from notewrench.zoterologseq.restore import apply_restore, plan_restore


class PolicyTests(unittest.TestCase):
    setUp = test_recovery.RecoveryTests.setUp
    set_status = test_recovery.RecoveryTests.set_status
    assert_reason_contract = test_recovery.RecoveryTests.assert_reason_contract

    def argv(self, strict=False):
        return [str(self.batch), '--graph', str(self.graph), '--recovery-audit',
                *(['--fail-on-ambiguity'] if strict else [])]

    def cli_pair(self, expected_default=0, expected_strict=2):
        before = snapshot(self.root)
        results = [subprocess.run([sys.executable, '-m', 'notewrench', 'zotero-logseq', *self.argv(strict)],
                                 cwd=test_recovery.ROOT, capture_output=True, text=True, timeout=60)
                   for strict in (False, True)]
        self.assertEqual([expected_default, expected_strict], [r.returncode for r in results])
        self.assertEqual(['', ''], [r.stderr for r in results])
        self.assertEqual(results[0].stdout, results[1].stdout)
        self.assertEqual(before, snapshot(self.root))
        self.assertFalse((self.graph / LOCK_NAME).exists())
        report = json.loads(results[0].stdout)
        self.assert_reason_contract(report)
        return report

    def place(self, original):
        for entry in self.entries:
            src, dest = ((self.batch, self.graph) if original else (self.graph, self.batch))
            src, dest = src / entry['path'], dest / entry['path']
            if src.exists():
                src.rename(dest)
        name = 'manifest-before.json' if original else 'manifest-after.json'
        (self.graph / MANIFEST_NAME).write_bytes((self.batch / name).read_bytes())

    def test_prepared_intact_original_only_regression(self):
        self.place(True)
        report = self.cli_pair()
        self.assertEqual(('audited', True, []), (report['status'], report['ambiguous'], report['conflicts']))
        self.assertEqual(['incomplete-receipt'], [r['code'] for r in report['ambiguity_reasons']])
        self.assertTrue(all(not p['ambiguous'] for p in report['pages']))

    def test_archive_restore_receipt_and_direction_matrix(self):
        for restore in (False, True):
            if restore:
                self.set_status('complete')
                self.place(False)
                decision = self.root / 'restore.json'
                plan_restore(self.batch, self.graph, decision)
                apply_restore(self.batch, self.graph, decision)
                (self.graph / LOCK_NAME).unlink()
            for status in ('prepared', 'rollback-incomplete', 'complete', 'rolled-back'):
                for correct in (True, False):
                    with self.subTest(restore=restore, status=status, correct=correct):
                        self.set_status(status, restore)
                        original = restore == (status == 'complete')
                        self.place(original if correct else not original)
                        ambiguous = status in ('prepared', 'rollback-incomplete') or not correct
                        report = self.cli_pair(expected_strict=2 if ambiguous else 0)
                        self.assertEqual('audited', report['status'])
                        self.assertEqual([], report['conflicts'])
                        self.assertEqual(ambiguous, report['ambiguous'])
                        self.assertTrue(all(not p['ambiguous'] for p in report['pages']))
            # End with a complete archive so restore planning can start.

    def test_missing_both_hash_ownership_and_terminal_manifest_only(self):
        self.set_status('complete')
        first, second = [self.batch / e['path'] for e in self.entries]
        pristine = first.read_bytes()
        second_pristine = second.read_bytes()
        # Full-hash mismatch alone; provenance/managed ownership still matches.
        first.write_bytes(pristine + b'\n- new human note')
        report = self.cli_pair()
        self.assertEqual(('mismatch', 'match'), (report['pages'][0]['archive']['full_hash'],
                                                report['pages'][0]['archive']['ownership']))
        first.write_bytes(pristine.replace(b'citekey:: Gone', b'citekey:: Different'))
        report = self.cli_pair()
        self.assertEqual('mismatch', report['pages'][0]['archive']['ownership'])
        (self.graph / self.entries[0]['path']).write_bytes(pristine)
        second.unlink()
        report = self.cli_pair()
        self.assertEqual(['both', 'missing'], [p['locations'] for p in report['pages']])
        (self.graph / self.entries[0]['path']).unlink()
        first.write_bytes(pristine)
        second.write_bytes(second_pristine)
        (self.graph / MANIFEST_NAME).write_bytes((self.batch / 'manifest-before.json').read_bytes())
        report = self.cli_pair()
        self.assertEqual(['manifest-mismatch'], [r['code'] for r in report['ambiguity_reasons']])
        self.assertTrue(all(not p['ambiguous'] for p in report['pages']))
        self.place(True)
        (self.graph / MANIFEST_NAME).write_bytes((self.batch / 'manifest-after.json').read_bytes())
        report = self.cli_pair()
        self.assertEqual(['location-mismatch', 'location-mismatch'],
                         [r['code'] for r in report['ambiguity_reasons']])
        self.assertTrue(all(not p['ambiguous'] for p in report['pages']))

    def test_ownership_only_even_when_rewritten_full_hash_matches(self):
        from notewrench.zoterologseq.archive import json_bytes
        from notewrench.zoterologseq.model import digest
        self.set_status('complete')
        path = self.batch / self.entries[0]['path']
        path.write_bytes(path.read_bytes().replace(b'citekey:: Gone', b'citekey:: Different'))
        decision = json.loads((self.batch / 'plan.json').read_bytes())
        decision['entries'][0]['page_sha256'] = digest(path.read_bytes())
        decision['decision_sha256'] = digest(json_bytes({k: v for k, v in decision.items()
                                                       if k != 'decision_sha256'}))
        receipt = json.loads((self.batch / 'receipt.json').read_bytes())
        receipt.update(entries=decision['entries'], decision_sha256=decision['decision_sha256'])
        (self.batch / 'receipt.json').write_bytes(json_bytes(receipt))
        (self.batch / 'plan.json').write_bytes(json_bytes(decision))
        self.batch = self.batch.rename(self.batch.with_name(decision['decision_sha256']))
        report = self.cli_pair()
        self.assertEqual(('match', 'mismatch'), (report['pages'][0]['archive']['full_hash'],
                                                report['pages'][0]['archive']['ownership']))
        self.assertEqual(['ownership-mismatch'], [r['code'] for r in report['ambiguity_reasons']])

    def invoke(self, strict, failures=(), directory_failures=()):
        """Guard all runtime observations and writes, without counting snapshots."""
        from notewrench.zoterologseq import recovery
        from notewrench.zoterologseq.cli import main
        files, directories, rechecks = Counter(), Counter(), []
        raw_read, raw_directory, raw_recheck, raw_open = (recovery.read_regular_snapshot,
            recovery.directory_snapshot, recovery.AuditReads.recheck, os.open)
        def read(path):
            files[path] += 1
            if (path, files[path]) in failures:
                raise PermissionError('synthetic file unavailable')
            return raw_read(path)
        def directory(path):
            directories[path] += 1
            if (path, directories[path]) in directory_failures:
                raise PermissionError('synthetic directory unavailable')
            return raw_directory(path)
        def recheck(reads, report):
            rechecks.append(True)
            return raw_recheck(reads, report)
        def open_file(path, flags, *args, **kwargs):
            self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
            return raw_open(path, flags, *args, **kwargs)
        before = snapshot(self.root)
        with patch.object(recovery, 'read_regular_snapshot', side_effect=read), \
             patch.object(recovery, 'directory_snapshot', side_effect=directory), \
             patch.object(recovery.AuditReads, 'recheck', new=recheck), \
             patch('os.open', side_effect=open_file), \
             patch.object(fcntl, 'flock', side_effect=AssertionError('audit lock')), \
             patch.object(Path, 'write_bytes', side_effect=AssertionError('audit write')), \
             patch.object(Path, 'write_text', side_effect=AssertionError('audit write')), \
             patch('os.replace', side_effect=AssertionError('audit replace')), \
             patch('os.rename', side_effect=AssertionError('audit rename')), \
             patch('sys.stdout', new_callable=io.StringIO) as out, \
             patch('sys.stderr', new_callable=io.StringIO) as err:
            code = main(self.argv(strict))
        self.assertEqual('', err.getvalue())
        self.assertEqual(before, snapshot(self.root))
        self.assertFalse((self.graph / LOCK_NAME).exists())
        return code, out.getvalue(), (files, directories, rechecks)

    def observed_pair(self, default=0, strict=2, **injections):
        baseline = self.invoke(False, **injections)
        opted = self.invoke(True, **injections)
        self.assertEqual((default, strict), (baseline[0], opted[0]))
        self.assertEqual(baseline[1:], opted[1:], 'policy changed JSON or reads/rechecks')
        report = json.loads(opted[1])
        self.assert_reason_contract(report)
        return report, opted[2]

    def test_same_bounded_reads_and_single_recheck_no_locks_writes(self):
        report, (files, dirs, rechecks) = self.observed_pair()
        self.assertTrue(report['ambiguous'])
        paths = [self.batch / n for n in ('plan.json', 'receipt.json', 'manifest-before.json',
                                         'manifest-after.json', 'restore-receipt.json')]
        paths += [self.graph / MANIFEST_NAME]
        paths += [base / e['path'] for e in self.entries for base in (self.graph, self.batch)]
        self.assertEqual(dict.fromkeys(paths, 2), files)
        self.assertEqual({self.graph / 'pages': 2, self.batch / 'pages': 2}, dirs)
        self.assertEqual([True], rechecks)

    def test_initial_recheck_io_precedence_with_and_without_unsafe_conflict(self):
        self.set_status('complete')
        failed = self.batch / self.entries[1]['path']
        parent = self.batch / 'pages'
        for unsafe in (False, True):
            if unsafe:
                (self.graph / self.entries[0]['path']).symlink_to(self.root / 'missing-target')
            for phase in (1, 2):
                for directory in (False, True):
                    with self.subTest(unsafe=unsafe, phase=phase, directory=directory):
                        injections = ({'directory_failures': {(parent, phase)}} if directory else
                                      {'failures': {(failed, phase)}})
                        report, counts = self.observed_pair(3, 3, **injections)
                        self.assertEqual('partial', report['status'])
                        self.assertTrue(report['ambiguous'])
                        self.assertEqual(unsafe, bool(report['conflicts']))
                        self.assertIn('initial_errors' if phase == 1 else 'recheck_errors', report)
                        files, dirs, rechecks = counts
                        self.assertEqual([True], rechecks)
                        if directory and phase == 1:
                            self.assertEqual(1, dirs[parent])
                            self.assertFalse(any(p.parent == parent for p in files))
                        elif not directory:
                            self.assertEqual(phase, files[failed])
        # Unsafe parent remains conflict/2 and is not traversed or rechecked.
        (self.graph / self.entries[0]['path']).unlink()
        moved = (self.graph / 'pages').rename(self.root / 'saved-pages')
        (self.graph / 'pages').symlink_to(moved, target_is_directory=True)
        report, (files, dirs, _) = self.observed_pair(2, 2)
        self.assertEqual('conflict', report['status'])
        self.assertEqual(1, dirs[self.graph / 'pages'])
        self.assertFalse(any(p.parent == self.graph / 'pages' for p in files))

    def test_metadata_errors_stay_error_only(self):
        metadata = self.batch / 'receipt.json'
        pristine = metadata.read_bytes()
        for data in (None, b'not JSON'):
            if data is None:
                metadata.unlink()
            else:
                metadata.write_bytes(data)
            report, (_, _, rechecks) = self.observed_pair(2, 2)
            self.assertEqual({'status', 'error'}, set(report))
            self.assertEqual([], rechecks)
        metadata.write_bytes(pristine)
        for path in (metadata, self.batch / 'restore-receipt.json'):
            report, (files, _, rechecks) = self.observed_pair(3, 3, failures={(path, 1)})
            self.assertEqual({'status': 'error', 'error': 'synthetic file unavailable'}, report)
            self.assertFalse(any(p.parent.name == 'pages' for p in files))
            self.assertEqual([], rechecks)
        metadata.unlink()
        metadata.symlink_to(self.root / 'missing-target')
        report, _ = self.observed_pair(2, 2)
        self.assertEqual({'status', 'error'}, set(report))

    def test_flag_requires_recovery_audit_before_any_dispatch(self):
        from notewrench.zoterologseq.cli import main
        for mode in ([], ['--dry-run'], ['--archive-plan', 'decision.json'],
                     ['--archive-apply', 'decision.json'], ['--restore-plan', 'decision.json'],
                     ['--restore-apply', 'decision.json']):
            with self.subTest(mode=mode), \
                 patch('notewrench.zoterologseq.cli.synchronize', side_effect=AssertionError('sync dispatch')), \
                 patch('notewrench.zoterologseq.cli.plan_archive', side_effect=AssertionError('plan dispatch')), \
                 patch('notewrench.zoterologseq.cli.apply_archive', side_effect=AssertionError('apply dispatch')), \
                 patch('notewrench.zoterologseq.cli.plan_restore', side_effect=AssertionError('plan dispatch')), \
                 patch('notewrench.zoterologseq.cli.apply_restore', side_effect=AssertionError('apply dispatch')), \
                 patch('notewrench.zoterologseq.cli.audit_recovery', side_effect=AssertionError('audit dispatch')), \
                 patch.object(Path, 'lstat', side_effect=AssertionError('argument filesystem probe')), \
                 patch('sys.stdout', new_callable=io.StringIO) as out, \
                 patch('sys.stderr', new_callable=io.StringIO) as err:
                with self.assertRaises(SystemExit) as caught:
                    main(['source', '--graph', 'graph', '--fail-on-ambiguity', *mode])
                self.assertEqual(2, caught.exception.code)
                self.assertEqual('', out.getvalue())
                self.assertIn('--fail-on-ambiguity requires --recovery-audit', err.getvalue())
