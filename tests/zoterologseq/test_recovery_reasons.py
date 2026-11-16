# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zotlog-sync contributors
"""Exact machine-readable ambiguity contracts; no repair or extra observations."""
import copy
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from . import test_recovery
from .test_recovery import snapshot
from notewrench.zoterologseq.model import LOCK_NAME, MANIFEST_NAME
from notewrench.zoterologseq.restore import apply_restore, plan_restore



class ReasonTests(unittest.TestCase):
    setUp = test_recovery.RecoveryTests.setUp
    set_status = test_recovery.RecoveryTests.set_status
    cli = test_recovery.RecoveryTests.cli
    assert_reason_contract = test_recovery.RecoveryTests.assert_reason_contract

    def active(self, code, status, restore=False, **context):
        return dict(code=code, path=str(self.batch / ('restore-receipt.json' if restore else 'receipt.json')),
                    receipt='restore' if restore else 'archive', status=status, **context)

    def direction(self, code, status, expected, observed, restore=False, page=None):
        result = self.active(code, status, restore, expected=expected, observed=observed)
        result.pop('path')
        if page is None:
            result['path'] = str(self.graph / MANIFEST_NAME)
        else:
            result.update(self.copies(code, page))
        return result

    def copies(self, code, page):
        return dict(code=code, id=page['id'], original_path=str(self.graph / page['path']),
                    archive_path=str(self.batch / page['path']))

    def mismatch(self, code, page, location, **context):
        return dict(code=code, id=page['id'], location=location,
                    path=str((self.graph if location == 'original' else self.batch) / page['path']), **context)

    def test_reasons_prepared_exact_and_no_added_reads(self):
        from notewrench.zoterologseq import recovery
        files, directories = {}, {}
        raw_read, raw_directory = recovery.read_regular_snapshot, recovery.directory_snapshot
        def read(path):
            files[path] = files.get(path, 0) + 1
            return raw_read(path)
        def directory(path):
            directories[path] = directories.get(path, 0) + 1
            return raw_directory(path)
        before = snapshot(self.graph)
        with patch.object(recovery, 'read_regular_snapshot', side_effect=read), \
             patch.object(recovery, 'directory_snapshot', side_effect=directory):
            report = recovery.audit_recovery(self.batch, self.graph)
        self.assertEqual([self.active('incomplete-receipt', 'prepared'),
                          self.direction('manifest-mismatch', 'prepared', 'before', 'after'),
                          *[self.direction('location-mismatch', 'prepared', 'original-only', 'archive-only', page=e)
                            for e in self.entries]], report['ambiguity_reasons'])
        self.assertEqual([[], []], [p['ambiguity_reasons'] for p in report['pages']])
        paths = [self.batch / name for name in ('plan.json', 'receipt.json', 'manifest-before.json',
                                               'manifest-after.json', 'restore-receipt.json')]
        paths += [self.graph / MANIFEST_NAME]
        paths += [base / e['path'] for e in self.entries for base in (self.graph, self.batch)]
        self.assertEqual(dict.fromkeys(paths, 2), files)
        self.assertEqual({self.graph / 'pages': 2, self.batch / 'pages': 2}, directories)
        self.assertEqual(before, snapshot(self.graph))
        self.assertFalse((self.graph / LOCK_NAME).exists())
        # The projection itself must not touch any filesystem primitive.
        expected = copy.deepcopy(report)
        with patch.object(Path, 'lstat', side_effect=AssertionError('reason lstat')), \
             patch('os.open', side_effect=AssertionError('reason open')), \
             patch.object(recovery, 'read_regular_snapshot', side_effect=AssertionError('reason read')):
            recovery.ambiguity_reasons(report, self.batch, self.graph)
            recovery.ambiguity_reasons(report, self.batch, self.graph)
        self.assertEqual(expected, report, 'projection must be deterministic and idempotent')

    def test_reasons_all_receipt_directions_exact(self):
        for restore in (False, True):
            if restore:
                self.set_status('complete')
                decision = self.root / 'restore.json'
                plan_restore(self.batch, self.graph, decision)
                apply_restore(self.batch, self.graph, decision)
                (self.graph / LOCK_NAME).unlink()
            for status in ('prepared', 'rollback-incomplete', 'complete', 'rolled-back'):
                expected_location = 'original-only' if restore == (status == 'complete') else 'archive-only'
                expected_manifest = 'before' if expected_location == 'original-only' else 'after'
                for correct in (True, False):
                    with self.subTest(restore=restore, status=status, correct=correct):
                        self.set_status(status, restore)
                        location = expected_location if correct else ('archive-only' if expected_location == 'original-only' else 'original-only')
                        manifest = expected_manifest if correct else ('after' if expected_manifest == 'before' else 'before')
                        for entry in self.entries:
                            source = self.batch / entry['path'] if location == 'original-only' else self.graph / entry['path']
                            dest = self.graph / entry['path'] if location == 'original-only' else self.batch / entry['path']
                            if source.exists():
                                source.rename(dest)
                        (self.graph / MANIFEST_NAME).write_bytes((self.batch / ('manifest-' + manifest + '.json')).read_bytes())
                        report = self.cli()
                        reasons = [] if status in ('complete', 'rolled-back') else [self.active('incomplete-receipt', status, restore)]
                        if not correct:
                            reasons.append(self.direction('manifest-mismatch', status, expected_manifest, manifest, restore))
                            reasons += [self.direction('location-mismatch', status, expected_location, location, restore, e) for e in self.entries]
                        self.assertEqual(reasons, report['ambiguity_reasons'])
                        self.assertEqual(bool(reasons), report['ambiguous'])
                        self.assertEqual([False, False], [p['ambiguous'] for p in report['pages']])
                        self.assertEqual([[], []], [p['ambiguity_reasons'] for p in report['pages']])
            # Restore planning needs the actual completed archive state.
            if not restore:
                self.set_status('complete')
                for e in self.entries:
                    path = self.graph / e['path']
                    if path.exists():
                        path.rename(self.batch / e['path'])
                (self.graph / MANIFEST_NAME).write_bytes((self.batch / 'manifest-after.json').read_bytes())

    def test_reasons_simultaneous_missing_both_full_and_ownership_exact(self):
        self.set_status('complete')
        first, second = self.entries
        original, archived = self.graph / first['path'], self.batch / first['path']
        original.write_bytes(archived.read_bytes() + b'\n- independent handwritten note')
        archived.write_bytes(archived.read_bytes().replace(b'citekey:: Gone', b'citekey:: Different'))
        (self.batch / second['path']).unlink()
        (self.graph / MANIFEST_NAME).unlink()
        report = self.cli()
        row = [self.copies('both-copies', first),
               self.mismatch('full-hash-mismatch', first, 'original', expected=first['page_sha256'], observed=report['pages'][0]['original']['sha256']),
               self.mismatch('full-hash-mismatch', first, 'archive', expected=first['page_sha256'], observed=report['pages'][0]['archive']['sha256']),
               self.mismatch('ownership-mismatch', first, 'archive')]
        missing = [self.copies('missing-copies', second)]
        expected = [self.direction('manifest-mismatch', 'complete', 'after', 'neither'),
                    self.direction('location-mismatch', 'complete', 'archive-only', 'both', page=first), *row,
                    self.direction('location-mismatch', 'complete', 'archive-only', 'missing', page=second), *missing]
        self.assertEqual(expected, report['ambiguity_reasons'])
        self.assertEqual([row, missing], [p['ambiguity_reasons'] for p in report['pages']])

    def test_reasons_unknown_unsafe_and_recheck_context_exact(self):
        from notewrench.zoterologseq import recovery
        from notewrench.zoterologseq.cli import main
        self.set_status('complete')
        first, second = self.entries
        parent, unknown = self.graph / 'pages', self.batch / second['path']
        moved = parent.rename(self.root / 'saved-pages')
        parent.symlink_to(moved, target_is_directory=True)
        changed, failed = self.batch / 'receipt.json', self.batch / first['path']
        before = snapshot(self.root)
        files, dirs, injected = {}, {}, []
        raw_read, raw_dir, raw_recheck, raw_lstat, raw_open = (recovery.read_regular_snapshot,
            recovery.directory_snapshot, recovery.AuditReads.recheck, Path.lstat, os.open)
        def lstat(path, *args, **kwargs):
            self.assertNotEqual(parent, path.parent, 'unsafe descendant probe')
            return raw_lstat(path, *args, **kwargs)
        def open_file(path, *args, **kwargs):
            self.assertNotEqual(parent, Path(path))
            self.assertNotEqual(parent, Path(path).parent)
            return raw_open(path, *args, **kwargs)
        def read(path):
            files[path] = files.get(path, 0) + 1
            if path == unknown or (path == failed and files[path] == 2):
                raise PermissionError('synthetic unavailable evidence')
            return raw_read(path)
        def directory(path):
            dirs[path] = dirs.get(path, 0) + 1
            return raw_dir(path)
        def recheck(reads, report):
            with patch.object(Path, 'lstat', new=raw_lstat), patch('os.open', new=raw_open):
                self.assertEqual(before, snapshot(self.root))
                changed.write_bytes(b'new invalid receipt bytes are never interpreted')
                injected.append(snapshot(self.root))
            return raw_recheck(reads, report)
        with patch.object(Path, 'lstat', new=lstat), patch('os.open', side_effect=open_file), \
             patch.object(recovery, 'read_regular_snapshot', side_effect=read), \
             patch.object(recovery, 'directory_snapshot', side_effect=directory), \
             patch.object(recovery.AuditReads, 'recheck', new=recheck), \
             patch('sys.stdout', new_callable=io.StringIO) as out:
            code = main([str(self.batch), '--graph', str(self.graph), '--recovery-audit'])
        report = json.loads(out.getvalue())
        diagnostic = lambda code, path: dict(code=code, path=str(path))
        unsafe = diagnostic('unsafe-initial-directory', parent)
        change = diagnostic('changed-during-audit', changed)
        unreadable = diagnostic('unreadable-initial-evidence', unknown)
        recheck_error = diagnostic('unreadable-during-recheck', failed)
        self.assertEqual(3, code)
        self.assertEqual('partial', report['status'])
        self.assertEqual([self.direction('location-mismatch', 'complete', 'archive-only', 'unsafe', page=first),
                          self.direction('location-mismatch', 'complete', 'archive-only', 'unknown', page=second),
                          unsafe, change, unreadable, recheck_error], report['ambiguity_reasons'])
        self.assertEqual([[unsafe, recheck_error], [unsafe, unreadable]], [p['ambiguity_reasons'] for p in report['pages']])
        self.assertEqual({'archive': 'complete', 'restore': None}, report['receipts'])
        self.assertEqual([snapshot(self.root)], injected)
        self.assertEqual({parent: 1, self.batch / 'pages': 2}, dirs)
        self.assertEqual(1, files[unknown])
        self.assertTrue(all(n == 2 for p, n in files.items() if p != unknown))
        self.assertFalse(any(p.parent == parent for p in files))

    def test_reasons_ownership_mismatch_even_when_full_hash_matches(self):
        from notewrench.zoterologseq.archive import json_bytes
        from notewrench.zoterologseq.model import digest
        # Hash consistency is not authenticity: even self-consistently rewritten
        # metadata cannot make invalid managed provenance pass ownership checks.
        first = self.entries[0]
        path = self.batch / first['path']
        path.write_bytes(path.read_bytes().replace(b'citekey:: Gone', b'citekey:: Different'))
        decision = json.loads((self.batch / 'plan.json').read_bytes())
        decision['entries'][0]['page_sha256'] = digest(path.read_bytes())
        decision['decision_sha256'] = digest(json_bytes({k: v for k, v in decision.items() if k != 'decision_sha256'}))
        receipt = json.loads((self.batch / 'receipt.json').read_bytes())
        receipt.update(status='complete', entries=decision['entries'], decision_sha256=decision['decision_sha256'])
        (self.batch / 'receipt.json').write_bytes(json_bytes(receipt))
        (self.batch / 'plan.json').write_bytes(json_bytes(decision))
        self.batch = self.batch.rename(self.batch.with_name(decision['decision_sha256']))
        report = self.cli()
        expected = [self.mismatch('ownership-mismatch', first, 'archive')]
        self.assertEqual(('match', 'mismatch'), (report['pages'][0]['archive']['full_hash'], report['pages'][0]['archive']['ownership']))
        self.assertEqual(expected, report['ambiguity_reasons'])
        self.assertEqual([expected, []], [p['ambiguity_reasons'] for p in report['pages']])

    def test_reasons_deduplicate_same_diagnostic_without_losing_distinct_codes(self):
        from notewrench.zoterologseq import recovery
        self.set_status('complete')
        report = recovery.audit_recovery(self.batch, self.graph)
        parent = self.batch / 'pages'
        changed = dict(code='changed-during-audit', path=str(parent), message='retained change')
        unreadable = dict(code='unreadable-during-recheck', path=str(parent), message='retained IO')
        report.update(status='partial', ambiguous=True, conflicts=[changed, dict(changed)],
                      recheck_errors=[unreadable, dict(unreadable)])
        for page in report['pages']:
            page['ambiguous'] = True
        recovery.ambiguity_reasons(report, self.batch, self.graph)
        expected = [dict(code='changed-during-audit', path=str(parent)),
                    dict(code='unreadable-during-recheck', path=str(parent))]
        self.assertEqual(expected, report['ambiguity_reasons'])
        self.assertEqual([expected, expected], [p['ambiguity_reasons'] for p in report['pages']])
        self.assertEqual([changed, changed], report['conflicts'])
        self.assertEqual([unreadable, unreadable], report['recheck_errors'])
