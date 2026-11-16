# SPDX-License-Identifier: AGPL-3.0-only
"""Local observations require a local linkMode; unsupported scans stay partial."""
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


class ComparisonModeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='compare-modes-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.inputs = [create_fixture(self.root / side) for side in ('before', 'after')]
        self.roots = [self.root / side / 'papers' for side in ('before', 'after')]
        self.reports = []
        for side, ((db, storage), linked) in enumerate(zip(self.inputs, self.roots)):
            linked.mkdir()
            (linked / 'a.pdf').write_bytes(b'same bytes\n')
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.executemany('INSERT INTO items VALUES (?, 1, ?)',
                                       [(30, 'QQQQQQQQ'), (31, 'RRRRRRRR'), (32, 'SSSSSSSS')])
                connection.executemany('INSERT INTO itemAttachments VALUES (?, NULL, 2, ?)',
                                       [(30, str(linked / 'a.pdf')), (31, 'attachments:a.pdf'),
                                        (32, 'attachments:missing.pdf')])
            self.reports.append(self.scan(side))

    def scan(self, side):
        return audit(*self.inputs[side], comparison_scope='mode-profile',
                     linked_base_directory=self.roots[side], allow_linked_roots=(self.roots[side],))

    def compare(self, reports, mapped=True):
        paths = [self.root / 'before.json', self.root / 'after.json']
        for path, report in zip(paths, reports):
            path.write_text(json.dumps(report, indent=2) + '\n')
        module = importlib.import_module('notewrench.zoteroattachments.compare')
        real_open, real_inspect = module.open_read, module.inspect

        def only_reports(function):
            def checked(path):
                self.assertIn(path, paths, 'Comparison tried to inspect a scan source')
                return function(path)
            return checked

        with patch.object(module, 'open_read', side_effect=only_reports(real_open)), \
                patch.object(module, 'inspect', side_effect=only_reports(real_inspect)):
            result = compare_reports(*paths, linked_root_mappings=[tuple(self.roots)] if mapped else [])
        for role, path in zip(('before', 'after'), paths):
            self.assertEqual(result['inputs'][role]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        return result

    def assert_invalid(self, result, role):
        self.assertTrue(result['fatal'], json.dumps(result))
        self.assertFalse(result['comparisonComplete'])
        self.assertIsNone(result['changes'])
        self.assertEqual(result['linkedFileAlignments'], [])
        self.assertEqual(result['comparisonErrors'], [dict(
            code='invalid_report', input=role,
            detail='Local attachment observation requires linkMode 0/1/2/4')])
        coverage = result['inputs'][role]['coverage']
        self.assertTrue(coverage['auditComplete'])
        self.assertEqual(coverage['filesWithSha256'], coverage['filesInventoried'])
        self.assertFalse(coverage['complete'])
        self.assertIn('invalid_report', coverage['reasons'])

    def test_present_local_observation_cannot_claim_remote_null_or_unknown_mode(self):
        # Matching file/reference status, SHA and paths cannot make mode 3 local.
        for side, role in enumerate(('before', 'after')):
            for key in ('ABCDEFGH', 'QQQQQQQQ', 'RRRRRRRR'):
                for mode in (99, -1, None, 3):
                    for mapped in (False, True):
                        with self.subTest(role=role, key=key, mode=mode, mapped=mapped):
                            reports = deepcopy(self.reports)
                            ref = next(r for r in reports[side]['attachments'] if r['key'] == key)
                            self.assertEqual(ref['status'], 'present')
                            ref['linkMode'] = mode
                            self.assert_invalid(self.compare(reports, mapped), role)

    def test_missing_local_observation_also_requires_supported_local_mode(self):
        for side, role in enumerate(('before', 'after')):
            for key in ('STUVWXYZ', 'SSSSSSSS'):
                for mode in (99, None, 3):
                    for mapped in (False, True):
                        with self.subTest(role=role, key=key, mode=mode, mapped=mapped):
                            reports = deepcopy(self.reports)
                            ref = next(r for r in reports[side]['attachments'] if r['key'] == key)
                            self.assertEqual(ref['status'], 'missing')
                            ref['linkMode'] = mode
                            self.assert_invalid(self.compare(reports, mapped), role)

    def test_real_unsupported_modes_without_observations_remain_partial(self):
        db, storage = self.inputs[1]
        for mode in (99, -1, None):
            with self.subTest(mode=mode):
                with closing(sqlite3.connect(db)) as connection, connection:
                    connection.execute('UPDATE itemAttachments SET linkMode=? WHERE itemID=1', (mode,))
                partial = self.scan(1)
                ref = next(r for r in partial['attachments'] if r['itemID'] == 1)
                self.assertEqual(ref['status'], 'unsupported_link_mode')
                self.assertNotIn('resolvedPath', ref)
                for mapped in (False, True):
                    result = self.compare([self.reports[0], partial], mapped)
                    self.assertFalse(result['fatal'], result)
                    self.assertFalse(result['comparisonComplete'])
                    self.assertIsNone(result['changes'])
                    self.assertEqual(result['linkedFileAlignments'], [])
                    self.assertEqual(result['comparisonErrors'][0]['code'], 'incomplete_evidence')

    def test_real_supported_modes_missing_remote_and_linked_aliases_remain_valid(self):
        # Real scans cover present/missing for every supported local mode.
        for mode in (0, 1, 4):
            for side, (db, storage) in enumerate(self.inputs):
                with closing(sqlite3.connect(db)) as connection, connection:
                    connection.execute('UPDATE itemAttachments SET linkMode=? WHERE itemID IN (1, 3)', (mode,))
                self.reports[side] = self.scan(side)
            for report in self.reports:
                refs = {r['key']: r for r in report['attachments']}
                self.assertEqual((refs['ABCDEFGH']['linkMode'], refs['ABCDEFGH']['status']), (mode, 'present'))
                self.assertEqual((refs['STUVWXYZ']['linkMode'], refs['STUVWXYZ']['status']), (mode, 'missing'))
                self.assertEqual((refs['QQQQQQQQ']['linkMode'], refs['QQQQQQQQ']['status']), (2, 'present'))
                self.assertEqual((refs['SSSSSSSS']['linkMode'], refs['SSSSSSSS']['status']), (2, 'missing'))
                self.assertEqual((refs['EFGHJKLM']['linkMode'], refs['EFGHJKLM']['status']), (3, 'remote_url'))
                self.assertNotIn('resolvedPath', refs['EFGHJKLM'])
            result = self.compare(self.reports)
            self.assertTrue(result['comparisonComplete'], result)
            for category, rows in result['changes'].items():
                if category == 'attachmentsChanged':
                    self.assertEqual([r['identity']['key'] for r in rows], ['QQQQQQQQ'])
                else:
                    self.assertEqual(rows, [], category)
            aligned, = result['linkedFileAlignments']
            self.assertEqual(len(aligned['after']['attachmentIdentities']), 2)
        for db, storage in self.inputs:
            shutil.rmtree(db.parent)
        self.assertTrue(self.compare(self.reports)['comparisonComplete'])


if __name__ == '__main__':
    unittest.main()
