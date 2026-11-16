# SPDX-License-Identifier: AGPL-3.0-only
"""Local linked observations must agree with their report's declared scan roots."""
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


DETAIL = 'Local linked observation outside declared allowed roots'


class ComparisonLinkedAllowlistTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='compare-linked-allowlist-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.inputs = [create_fixture(self.root / side) for side in ('before', 'after')]
        self.roots = [db.parent / 'papers' for db, _ in self.inputs]
        self.reports = []
        for side, ((db, storage), linked) in enumerate(zip(self.inputs, self.roots)):
            (linked / 'nested').mkdir(parents=True)
            (linked / 'nested/a.pdf').write_bytes(b'same bytes\n')
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.executemany('INSERT INTO items VALUES (?, 1, ?)',
                                       [(30, 'QQQQQQQQ'), (31, 'RRRRRRRR'),
                                        (32, 'SSSSSSSS'), (33, 'TTTTTTTT'), (34, 'UUUUUUUU')])
                connection.executemany('INSERT INTO itemAttachments VALUES (?, NULL, 2, ?)',
                                       [(30, str(linked / 'nested/a.pdf')),
                                        (31, 'attachments:nested/a.pdf'),
                                        (32, str(linked / 'nested/missing.pdf')),
                                        (33, 'attachments:nested/missing.pdf'),
                                        (34, str(storage / 'ABCDEFGH/a.pdf'))])
            self.reports.append(self.scan(side, (linked / 'nested', storage / 'ABCDEFGH')))

    def scan(self, side, allowed):
        return audit(*self.inputs[side], comparison_scope='linked-allowlist-profile',
                     linked_base_directory=self.roots[side], allow_linked_roots=allowed)

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
        for role, path, report in zip(('before', 'after'), paths, reports):
            entry = result['inputs'][role]
            self.assertEqual(entry['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(entry['bytes'], len(path.read_bytes()))
            coverage = entry['coverage']
            self.assertEqual(coverage['auditComplete'], report['auditComplete'])
            self.assertEqual(coverage['auditErrors'], report['auditErrors'])
            self.assertEqual(coverage['filesInventoried'], len(report['files']))
            self.assertEqual(coverage['filesWithSha256'], len(report['files']))
            self.assertEqual(coverage['attachments'], len(report['attachments']))
        return result

    def assert_invalid(self, result, role):
        self.assertTrue(result['fatal'], result)
        self.assertFalse(result['comparisonComplete'])
        self.assertIsNone(result['changes'])
        self.assertEqual(result['linkedFileAlignments'], [])
        self.assertEqual(result['comparisonErrors'], [dict(code='invalid_report', input=role, detail=DETAIL)])
        self.assertFalse(result['inputs'][role]['coverage']['complete'])
        self.assertIn('invalid_report', result['inputs'][role]['coverage']['reasons'])

    def test_each_absolute_relative_present_missing_observation_requires_strict_root(self):
        for side, role in enumerate(('before', 'after')):
            for key in ('QQQQQQQQ', 'RRRRRRRR', 'SSSSSSSS', 'TTTTTTTT', 'UUUUUUUU'):
                for kind in ('empty', 'equal', 'prefix', 'other-alias-only'):
                    for scan_mapped in (False, True):
                        for mapped in (False, True):
                            with self.subTest(role=role, key=key, kind=kind,
                                              scan_mapped=scan_mapped, mapped=mapped):
                                reports = deepcopy(self.reports)
                                report = reports[side]
                                ref = next(r for r in report['attachments'] if r['key'] == key)
                                # Put this observation first to independently exercise
                                # relative and missing aliases, not only QQQQQQQQ.
                                report['attachments'].remove(ref)
                                report['attachments'].insert(0, ref)
                                target = Path(ref['resolvedPath'])
                                roots = {'empty': [], 'equal': [str(target)],
                                         'prefix': [str(target.parent)[:-1]],
                                         'other-alias-only': [str(self.inputs[side][1] if key != 'UUUUUUUU'
                                                                  else self.roots[side])]}
                                report['policy']['allowedLinkedRoots'] = roots[kind]
                                if scan_mapped:
                                    report['policy']['linkedRootMappings'] = [dict(
                                        source='/original-papers', destination=str(self.roots[side]))]
                                    for alias in report['attachments']:
                                        raw = alias.get('path')
                                        if alias['linkMode'] == 2 and raw.startswith(str(self.roots[side]) + '/'):
                                            alias['path'] = '/original-papers/' + str(Path(raw).relative_to(self.roots[side]))
                                self.assert_invalid(self.compare(reports, mapped), role)

    def test_missing_or_malformed_allowlist_cannot_establish_local_observation(self):
        for side, role in enumerate(('before', 'after')):
            for roots in ('OMIT', None, {}, '/papers', [None], [7], ['relative'],
                          ['/x/../papers'], ['/x//papers'], ['/x\x00papers']):
                with self.subTest(role=role, roots=roots):
                    reports = deepcopy(self.reports)
                    if roots == 'OMIT':
                        del reports[side]['policy']['allowedLinkedRoots']
                    else:
                        reports[side]['policy']['allowedLinkedRoots'] = roots
                    self.assert_invalid(self.compare(reports), role)

    def test_real_allowed_subdirectories_storage_aliases_and_relocation_offline(self):
        for db, _ in self.inputs:
            shutil.rmtree(db.parent)
        for mapped in (False, True):
            result = self.compare(self.reports, mapped)
            self.assertTrue(result['comparisonComplete'], result)
            self.assertFalse(result['fatal'])
            self.assertEqual(len(result['linkedFileAlignments']), int(mapped))
        # / is a declared root, duplicates and nested allowed roots are legal.
        reports = deepcopy(self.reports)
        for report in reports:
            report['policy']['allowedLinkedRoots'] += ['/', '/']
        self.assertTrue(self.compare(reports)['comparisonComplete'])
        # Removing storage from the allowlist invalidates its linked alias,
        # even though the stored alias and file inventory remain present.
        for side, role in enumerate(('before', 'after')):
            reports = deepcopy(self.reports)
            reports[side]['policy']['allowedLinkedRoots'] = [str(self.roots[side])]
            self.assert_invalid(self.compare(reports), role)

    def test_real_blocked_and_unresolved_observations_remain_incomplete(self):
        for side, role in enumerate(('before', 'after')):
            for allowed in ((), (self.roots[side] / 'nested/a.pdf',),
                            (self.inputs[side][1],)):
                partial = self.scan(side, allowed)
                self.assertTrue(any(r['status'] == 'blocked_linked' for r in partial['attachments']))
                for mapped in (False, True):
                    reports = deepcopy(self.reports)
                    reports[side] = partial
                    result = self.compare(reports, mapped)
                    self.assertFalse(result['fatal'], result)
                    self.assertFalse(result['comparisonComplete'])
                    self.assertIsNone(result['changes'])
                    self.assertEqual(result['linkedFileAlignments'], [])
                    self.assertEqual(result['comparisonErrors'][0]['code'], 'incomplete_evidence')
            db, storage = self.inputs[side]
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute('UPDATE itemAttachments SET path=? WHERE itemID=30', ('attachments:',))
            reports = deepcopy(self.reports)
            reports[side] = self.scan(side, ())
            result = self.compare(reports)
            self.assertFalse(result['fatal'], result)
            self.assertEqual(result['comparisonErrors'][0]['code'], 'incomplete_evidence')


if __name__ == '__main__':
    unittest.main()
