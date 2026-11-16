# SPDX-License-Identifier: AGPL-3.0-only
"""Stored targets must agree with their own key and raw storage: filename."""
from contextlib import closing
from copy import deepcopy
import hashlib
import importlib
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from .fixture import create_fixture
from notewrench.zoteroattachments import audit, compare_reports


class StoredTargetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='compare-stored-targets-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.inputs = [create_fixture(self.root / side) for side in ('before', 'after')]
        self.reports = [audit(*inputs, comparison_scope='stored-target-profile') for inputs in self.inputs]

    def compare(self, reports, mapped=False):
        paths = [self.root / 'before.json', self.root / 'after.json']
        for path, report in zip(paths, reports):
            path.write_text(json.dumps(report, indent=2) + '\n')
        module = importlib.import_module('notewrench.zoteroattachments.compare')
        real_open = module.open_read

        def only_reports(path):
            self.assertIn(path, paths)
            return real_open(path)

        with patch.object(module, 'open_read', side_effect=only_reports):
            result = compare_reports(*paths, linked_root_mappings=[('/old-linked', '/new-linked')] if mapped else [])
        for role, path in zip(('before', 'after'), paths):
            self.assertEqual(result['inputs'][role]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        return result

    def assert_invalid(self, result, role):
        # Include the prior false attribution in a failing regression's raw log.
        self.assertTrue(result['fatal'], json.dumps(result))
        self.assertFalse(result['comparisonComplete'])
        self.assertIsNone(result['changes'])
        self.assertEqual(result['linkedFileAlignments'], [])
        error, = result['comparisonErrors']
        self.assertEqual(error, dict(code='invalid_report', input=role, detail='Stored attachment target mismatch'))

    def test_reference_transfer_cannot_reassign_stored_attachment_to_another_key(self):
        # Round-6 retained reproducer: equal bytes, matching statuses and fully
        # consistent file references still contradict raw storage:a.pdf + key.
        for side, role in enumerate(('before', 'after')):
            for mode in (0, 1, 4):
                for mapped in (False, True):
                    with self.subTest(role=role, mode=mode, mapped=mapped):
                        reports = deepcopy(self.reports)
                        report = reports[side]
                        ref = next(r for r in report['attachments'] if r['key'] == 'ABCDEFGH')
                        source = next(f for f in report['files'] if f['path'] == ref['resolvedPath'])
                        target = next(f for f in report['files'] if f['path'].endswith('/JKLMNPQR/b.pdf'))
                        self.assertEqual(source['sha256'], target['sha256'])
                        source['references'].remove(ref['itemID'])
                        target['references'].append(ref['itemID'])
                        ref.update(linkMode=mode, resolvedPath=target['path'])
                        self.assert_invalid(self.compare(reports, mapped), role)

    def test_raw_filename_and_missing_target_must_agree(self):
        for key in ('ABCDEFGH', 'STUVWXYZ'):
            for raw in ('storage:other.pdf', 'storage:../a.pdf', 'storage:', 'storage:a/b.pdf',
                        'storage:a\\b.pdf', 'storage:.', 'storage:..', 'storage:a\x00.pdf',
                        '/absolute/a.pdf', None):
                with self.subTest(key=key, raw=raw):
                    reports = deepcopy(self.reports)
                    ref = next(r for r in reports[1]['attachments'] if r['key'] == key)
                    ref['path'] = raw
                    self.assert_invalid(self.compare(reports), 'after')
            for target in (None, '/another/storage/' + key + '/a.pdf'):
                with self.subTest(key=key, target=target):
                    reports = deepcopy(self.reports)
                    ref = next(r for r in reports[1]['attachments'] if r['key'] == key)
                    ref['resolvedPath'] = target
                    self.assert_invalid(self.compare(reports), 'after')

    def test_partial_observation_cannot_claim_an_impossible_resolved_target(self):
        reports = deepcopy(self.reports)
        ref = next(r for r in reports[1]['attachments'] if r['key'] == 'STUVWXYZ')
        ref.update(status='inaccessible', resolvedPath='/other/key/file.pdf')
        reports[1].update(auditComplete=False, auditErrors=[{'code': 'inaccessible'}])
        self.assert_invalid(self.compare(reports), 'after')

    def test_real_unsupported_paths_without_targets_remain_partial(self):
        db, storage = self.inputs[1]
        for raw in ('storage:../a.pdf', 'storage:', 'ancient-relative-path', None):
            with self.subTest(raw=raw):
                with closing(sqlite3.connect(db)) as connection, connection:
                    connection.execute('UPDATE itemAttachments SET path=? WHERE itemID=1', (raw,))
                partial = audit(db, storage, comparison_scope='stored-target-profile')
                ref = next(r for r in partial['attachments'] if r['itemID'] == 1)
                self.assertNotIn('resolvedPath', ref)
                result = self.compare([self.reports[0], partial])
                self.assertFalse(result['fatal'], result)
                self.assertIsNone(result['changes'])
                self.assertEqual(result['comparisonErrors'][0]['code'], 'incomplete_evidence')

    def test_real_stored_modes_cross_library_and_linked_aliases_inside_storage(self):
        reports = []
        for db, storage in self.inputs:
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.executemany('INSERT INTO items VALUES (?, ?, ?)',
                                       [(30, 1, 'QQQQQQQQ'), (31, 1, 'RRRRRRRR'), (32, 2, 'ABCDEFGH')])
                connection.executemany('INSERT INTO itemAttachments VALUES (?, NULL, ?, ?)',
                                       [(30, 2, str(storage / 'ABCDEFGH/a.pdf')),
                                        (31, 2, 'attachments:ABCDEFGH/a.pdf'), (32, 4, 'storage:a.pdf')])
            reports.append(audit(db, storage, comparison_scope='stored-target-profile',
                                 linked_base_directory=storage, allow_linked_roots=(storage,),
                                 linked_root_mappings=(('/unused-original', '/unused-backup'),)))
        # Source paths can be unavailable on both success and failure.
        for db, storage in self.inputs:
            shutil.rmtree(db.parent)
        originals = deepcopy(reports)
        result = self.compare(reports, mapped=True)
        self.assertTrue(result['comparisonComplete'], result)
        for category, rows in result['changes'].items():
            if category == 'attachmentsChanged':
                self.assertEqual([r['identity']['key'] for r in rows], ['QQQQQQQQ'])
            else:
                self.assertEqual(rows, [], category)
        self.assertEqual(reports, originals)
        target = next(f for f in reports[1]['files'] if f['path'].endswith('/ABCDEFGH/a.pdf'))
        self.assertEqual(target['source'], 'storage')
        self.assertEqual(set(target['references']), {1, 30, 31, 32})

    def test_real_literal_filename_is_preserved_without_decoding_or_expansion(self):
        leaf = 'paper %2F $HOME `literal` ü:1.pdf'
        for side, (db, storage) in enumerate(self.inputs):
            (storage / 'ABCDEFGH/a.pdf').rename(storage / 'ABCDEFGH' / leaf)
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute('UPDATE itemAttachments SET path=? WHERE itemID=1', ('storage:' + leaf,))
            self.reports[side] = audit(db, storage, comparison_scope='stored-target-profile')
        result = self.compare(self.reports)
        self.assertTrue(result['comparisonComplete'], result)
        self.assertTrue(all(not rows for rows in result['changes'].values()))


if __name__ == '__main__':
    unittest.main()
