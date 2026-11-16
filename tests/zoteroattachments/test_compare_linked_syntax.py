# SPDX-License-Identifier: AGPL-3.0-only
"""Fully observed linked attachments must have supported raw path syntax."""
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


INVALID_RAW = ('storage:a.pdf', 'relative/a.pdf', '', None, 'attachments:',
               'attachments:/a.pdf', 'attachments:a//b.pdf', 'attachments:./a.pdf',
               'attachments:a/../b.pdf', 'attachments:a/', 'attachments:C:/a.pdf',
               'attachments:a\\b.pdf', '/papers/../a.pdf', '/papers/./a.pdf',
               '/papers//a.pdf', '//papers/a.pdf', '/papers/a.pdf/',
               '/papers/a\\b.pdf', 'C:/papers/a.pdf', '\\\\host\\a.pdf',
               '/papers/a\x00.pdf', 'attachments:a\x00.pdf')


class ComparisonLinkedSyntaxTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='compare-linked-syntax-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.inputs = [create_fixture(self.root / side) for side in ('before', 'after')]
        self.roots = [db.parent / 'papers' for db, _ in self.inputs]
        self.reports = []
        for side, ((db, _), linked) in enumerate(zip(self.inputs, self.roots)):
            linked.mkdir()
            (linked / 'a.pdf').write_bytes(b'same bytes\n')
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.executemany('INSERT INTO items VALUES (?, 1, ?)',
                                       [(30, 'QQQQQQQQ'), (31, 'RRRRRRRR'), (32, 'SSSSSSSS')])
                connection.executemany('INSERT INTO itemAttachments VALUES (?, NULL, 2, ?)',
                                       [(30, str(linked / 'a.pdf')), (31, 'attachments:a.pdf'),
                                        (32, 'attachments:missing.pdf')])
            self.reports.append(self.scan(side))

    def scan(self, side, **kwargs):
        return audit(*self.inputs[side], comparison_scope='linked-syntax-profile',
                     linked_base_directory=self.roots[side],
                     allow_linked_roots=(self.roots[side], self.inputs[side][1]), **kwargs)

    def compare(self, reports, mapped=True):
        paths = [self.root / 'before.json', self.root / 'after.json']
        for path, report in zip(paths, reports):
            path.write_text(json.dumps(report, indent=2) + '\n')
        module = importlib.import_module('notewrench.zoteroattachments.compare')

        def only_reports(function):
            def checked(path):
                self.assertIn(path, paths, 'Comparison tried to access a scan source')
                return function(path)
            return checked

        with patch.object(module, 'open_read', side_effect=only_reports(module.open_read)), \
                patch.object(module, 'inspect', side_effect=only_reports(module.inspect)):
            result = compare_reports(*paths, linked_root_mappings=[tuple(self.roots)] if mapped else [])
        for role, path in zip(('before', 'after'), paths):
            self.assertEqual(result['inputs'][role]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        return result

    def test_present_and_missing_linked_observations_reject_unsupported_raw_syntax(self):
        for side, role in enumerate(('before', 'after')):
            for key in ('QQQQQQQQ', 'SSSSSSSS'):
                for raw in INVALID_RAW:
                    for mapped in (False, True):
                        with self.subTest(role=role, key=key, raw=raw, mapped=mapped):
                            reports = deepcopy(self.reports)
                            ref = next(r for r in reports[side]['attachments'] if r['key'] == key)
                            ref['path'] = raw
                            result = self.compare(reports, mapped)
                            self.assertTrue(result['fatal'], json.dumps(result))
                            self.assertFalse(result['comparisonComplete'])
                            self.assertIsNone(result['changes'])
                            self.assertEqual(result['linkedFileAlignments'], [])
                            self.assertEqual(result['comparisonErrors'], [dict(
                                code='invalid_report', input=role,
                                detail='Local linked observation requires a supported raw path')])
                            coverage = result['inputs'][role]['coverage']
                            self.assertTrue(coverage['auditComplete'])
                            self.assertEqual(coverage['filesWithSha256'], coverage['filesInventoried'])
                            self.assertIn('invalid_report', coverage['reasons'])
                            self.assertFalse(coverage['complete'])

    def test_real_invalid_paths_without_observations_remain_partial(self):
        db, _ = self.inputs[1]
        for raw in INVALID_RAW:
            with self.subTest(raw=raw):
                with closing(sqlite3.connect(db)) as connection, connection:
                    connection.execute('UPDATE itemAttachments SET path=? WHERE itemID=30', (raw,))
                partial = self.scan(1)
                ref = next(r for r in partial['attachments'] if r['itemID'] == 30)
                self.assertIn(ref['status'], ('invalid_path', 'unsupported_path'))
                self.assertNotIn('resolvedPath', ref)
                for mapped in (False, True):
                    result = self.compare([self.reports[0], partial], mapped)
                    self.assertFalse(result['fatal'], result)
                    self.assertIsNone(result['changes'])
                    self.assertFalse(result['comparisonComplete'])
                    self.assertEqual(result['linkedFileAlignments'], [])
                    self.assertEqual(result['comparisonErrors'][0]['code'], 'incomplete_evidence')

    def test_real_relocations_aliases_and_literal_paths_remain_supported_offline(self):
        leaf = 'paper %2F $HOME `literal` ü:1.pdf'
        reports = []
        for side, (db, storage) in enumerate(self.inputs):
            (self.roots[side] / 'a.pdf').rename(self.roots[side] / leaf)
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute('UPDATE itemAttachments SET path=? WHERE itemID=30', ('/old-papers/' + leaf,))
                connection.execute('UPDATE itemAttachments SET path=? WHERE itemID=31', ('attachments:' + leaf,))
                connection.executemany('INSERT INTO items VALUES (?, 1, ?)', [(33, 'TTTTTTTT'), (34, 'UUUUUUUU')])
                connection.executemany('INSERT INTO itemAttachments VALUES (?, NULL, 2, ?)',
                                       [(33, str(storage / 'ABCDEFGH/a.pdf')),
                                        (34, str(self.roots[side] / 'absent.pdf'))])
            reports.append(self.scan(side, linked_root_mappings=[('/old-papers', self.roots[side])]))
        for db, _ in self.inputs:
            shutil.rmtree(db.parent)
        result = self.compare(reports)
        self.assertTrue(result['comparisonComplete'], result)
        self.assertEqual(len(result['linkedFileAlignments']), 1)
        for category, rows in result['changes'].items():
            if category == 'attachmentsChanged':
                self.assertEqual({r['identity']['key'] for r in rows}, {'TTTTTTTT', 'UUUUUUUU'})
            else:
                self.assertEqual(rows, [], category)
        self.assertTrue(self.compare(reports, mapped=False)['comparisonComplete'])


if __name__ == '__main__':
    unittest.main()
