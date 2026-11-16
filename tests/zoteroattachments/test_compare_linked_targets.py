# SPDX-License-Identifier: AGPL-3.0-only
"""Linked targets must derive from each imported report's own scan policy."""
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


class ComparisonLinkedTargetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='compare-linked-target-')
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
        return audit(*self.inputs[side], comparison_scope='linked-target-profile',
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

    def assert_invalid(self, result, role):
        self.assertTrue(result['fatal'], json.dumps(result))
        self.assertFalse(result['comparisonComplete'])
        self.assertIsNone(result['changes'])
        self.assertEqual(result['linkedFileAlignments'], [])
        self.assertEqual(result['comparisonErrors'], [dict(
            code='invalid_report', input=role,
            detail='Linked attachment target mismatch with scan policy')])
        coverage = result['inputs'][role]['coverage']
        self.assertTrue(coverage['auditComplete'])
        self.assertEqual(coverage['filesWithSha256'], coverage['filesInventoried'])
        self.assertFalse(coverage['complete'])
        self.assertIn('invalid_report', coverage['reasons'])

    def test_present_and_missing_targets_must_derive_from_own_scan_policy(self):
        for side, role in enumerate(('before', 'after')):
            for key in ('QQQQQQQQ', 'SSSSSSSS'):
                for raw in ('/unmapped-papers/never-observed.pdf', 'attachments:never-observed.pdf'):
                    for scan_mapped in (False, True):
                        for mapped in (False, True):
                            with self.subTest(role=role, key=key, raw=raw,
                                              scan_mapped=scan_mapped, mapped=mapped):
                                reports = deepcopy(self.reports)
                                ref = next(r for r in reports[side]['attachments'] if r['key'] == key)
                                ref['path'] = raw
                                if scan_mapped:
                                    reports[side]['policy']['linkedRootMappings'] = [dict(
                                        source='/unmapped-papers', destination='/other-target')]
                                self.assert_invalid(self.compare(reports, mapped), role)

    def test_missing_targets_and_malformed_or_conflicting_scan_policies(self):
        # Declared policy is not a source of guessed or comparison mappings.
        policies = [None, [], {}, {'linkedRootMappings': None},
                    {'linkedRootMappings': [{}]}, {'linkedRootMappings': [['/x', '/y']]},
                    {'linkedRootMappings': [{'source': '/x', 'destination': '/y/../z'}]},
                    {'linkedRootMappings': [{'source': '/x', 'destination': '/y'},
                                            {'source': '/x/nested', 'destination': '/z'}]},
                    {'linkedBaseDirectory': None}, {'linkedBaseDirectory': 7},
                    {'linkedBaseDirectory': 'relative'}, {'linkedBaseDirectory': '/wrong-base'},
                    {'linkedBaseDirectory': '//wrong-base'}]
        for side, role in enumerate(('before', 'after')):
            for policy in policies:
                for mapped in (False, True):
                    with self.subTest(role=role, policy=policy, mapped=mapped):
                        reports = deepcopy(self.reports)
                        reports[side]['policy'] = policy
                        self.assert_invalid(self.compare(reports, mapped), role)
            for key in ('QQQQQQQQ', 'SSSSSSSS'):
                for value in (None, '/unrelated/target.pdf', 'OMIT'):
                    reports = deepcopy(self.reports)
                    ref = next(r for r in reports[side]['attachments'] if r['key'] == key)
                    if value == 'OMIT':
                        del ref['resolvedPath']
                    else:
                        ref['resolvedPath'] = value
                    self.assert_invalid(self.compare(reports), role)

    def test_real_scan_mappings_apply_once_relative_base_is_independent_offline(self):
        reports = []
        for side, (db, storage) in enumerate(self.inputs):
            root = self.roots[side]
            # Cross source/destination overlap is legal. Reapplying would point
            # the absolute reference at /never-read; applying to attachments:
            # would also incorrectly move the relative alias and missing path.
            mappings = [('/old-papers', root), (root, '/never-read')]
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute('UPDATE itemAttachments SET path=? WHERE itemID=30',
                                   ('/old-papers/a.pdf',))
                connection.executemany('INSERT INTO items VALUES (?, 1, ?)',
                                       [(33, 'TTTTTTTT'), (34, 'UUUUUUUU'), (35, 'VVVVVVVV')])
                connection.executemany('INSERT INTO itemAttachments VALUES (?, NULL, 2, ?)',
                                       [(33, str(storage / 'ABCDEFGH/a.pdf')),
                                        (34, '/old-papers/absent.pdf'),
                                        (35, str(root) + '-other/absent.pdf')])
            report = audit(db, storage, comparison_scope='linked-target-profile',
                           linked_base_directory=root, linked_root_mappings=mappings,
                           allow_linked_roots=(root.parent, storage))
            self.assertTrue(report['auditComplete'], report['auditErrors'])
            reports.append(report)
        for db, _ in self.inputs:
            shutil.rmtree(db.parent)
        mapped = self.compare(reports)
        self.assertTrue(mapped['comparisonComplete'], mapped)
        self.assertEqual(len(mapped['linkedFileAlignments']), 1)
        for category, rows in mapped['changes'].items():
            if category == 'attachmentsChanged':
                self.assertEqual({r['identity']['key'] for r in rows}, {'TTTTTTTT', 'VVVVVVVV'})
            else:
                self.assertEqual(rows, [], category)
        unmapped = self.compare(reports, mapped=False)
        self.assertTrue(unmapped['comparisonComplete'], unmapped)
        self.assertEqual(len(unmapped['changes']['filesAdded']), 1)
        self.assertEqual(len(unmapped['changes']['filesRemoved']), 1)
        # A comparison mapping never repairs a report's own target derivation.
        bad = deepcopy(reports)
        ref = next(r for r in bad[0]['attachments'] if r['key'] == 'QQQQQQQQ')
        ref['resolvedPath'] = str(self.roots[1] / 'a.pdf')
        self.assert_invalid(self.compare(bad), 'before')

    def test_legacy_mapping_field_and_normalized_base_are_supported(self):
        reports = deepcopy(self.reports)
        for report in reports:
            del report['policy']['linkedRootMappings']  # additive field, pre-round-4
            report['policy']['linkedBaseDirectory'] += '//'
        self.assertTrue(self.compare(reports)['comparisonComplete'])

    def test_real_unresolved_partial_scans_remain_incomplete(self):
        db, storage = self.inputs[1]
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute('UPDATE itemAttachments SET path=? WHERE itemID=30',
                               ('attachments:a.pdf',))
        for base in (None, 'relative-base', '/bad/../base'):
            partial = audit(db, storage, comparison_scope='linked-target-profile',
                            linked_base_directory=base, allow_linked_roots=(self.roots[1],))
            ref = next(r for r in partial['attachments'] if r['itemID'] == 30)
            self.assertNotIn('resolvedPath', ref)
            self.assertIn(ref['status'], ('invalid_path', 'unsupported_path'))
            for mapped in (False, True):
                result = self.compare([self.reports[0], partial], mapped)
                self.assertFalse(result['fatal'], result)
                self.assertFalse(result['comparisonComplete'])
                self.assertIsNone(result['changes'])
                self.assertEqual(result['linkedFileAlignments'], [])
                self.assertEqual(result['comparisonErrors'][0]['code'], 'incomplete_evidence')
        # A real blocked target still derives lexically despite incomplete IO.
        partial = audit(db, storage, comparison_scope='linked-target-profile',
                        linked_base_directory=self.roots[1])
        result = self.compare([self.reports[0], partial])
        self.assertFalse(result['fatal'], result)
        self.assertEqual(result['comparisonErrors'][0]['code'], 'incomplete_evidence')
        # Partial status does not excuse a contradictory claimed target.
        ref = next(r for r in partial['attachments'] if r['itemID'] == 30)
        ref['resolvedPath'] = '/unrelated/a.pdf'
        result = self.compare([self.reports[0], partial])
        self.assertTrue(result['fatal'], result)
        self.assertEqual(result['comparisonErrors'][0]['code'], 'invalid_report')


if __name__ == '__main__':
    unittest.main()
