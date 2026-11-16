# SPDX-License-Identifier: AGPL-3.0-only
"""Reject contradictory report observations before aligning linked identities."""
from contextlib import closing
from copy import deepcopy
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from .fixture import create_fixture
from notewrench.zoteroattachments import audit, compare_reports
from notewrench.zoteroattachments.fs import FileIssue


class ComparisonConsistencyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='compare-consistency-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.roots = [self.root / 'day1/papers', self.root / 'day2/papers']
        self.reports = []
        self.inputs = []
        for side, linked in enumerate(self.roots):
            linked.mkdir(parents=True)
            (linked / 'a.pdf').write_bytes(b'same bytes\n')
            db, storage = create_fixture(self.root / str(side))
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.executemany('INSERT INTO items VALUES (?, 1, ?)',
                                       [(30, 'QQQQQQQQ'), (31, 'RRRRRRRR')])
                connection.executemany('INSERT INTO itemAttachments VALUES (?, NULL, 2, ?)',
                                       [(30, str(linked / 'a.pdf')), (31, 'attachments:a.pdf')])
            self.inputs.append((db, storage))
            self.reports.append(self.scan(side))

    def scan(self, side):
        return audit(*self.inputs[side], comparison_scope='consistency-profile',
                     linked_base_directory=self.roots[side], allow_linked_roots=(self.roots[side],))

    def compare(self, reports, mapped=True):
        paths = [self.root / 'before.json', self.root / 'after.json']
        for path, report in zip(paths, reports):
            path.write_text(json.dumps(report, indent=2) + '\n')
        # Comparisons must work without the scanned data, including on rejection.
        module = importlib.import_module('notewrench.zoteroattachments.compare')
        def only_reports(function):
            def checked(path):
                self.assertIn(path, paths, 'Comparison tried to access a scan source')
                return function(path)
            return checked

        with patch.object(module, 'open_read', side_effect=only_reports(module.open_read)), \
                patch.object(module, 'inspect', side_effect=only_reports(module.inspect)), \
                patch.object(module, 'relocate_linked_identities', wraps=module.relocate_linked_identities) as relocate:
            result = compare_reports(*paths, linked_root_mappings=[tuple(self.roots)] if mapped else [])
            if result['fatal']:
                relocate.assert_not_called()
        for role, path in zip(('before', 'after'), paths):
            data = path.read_bytes()
            self.assertEqual(result['inputs'][role]['sha256'], hashlib.sha256(data).hexdigest())
            self.assertEqual(result['inputs'][role]['bytes'], len(data))
        return result

    def assert_invalid(self, result, role, detail='File/reference status mismatch'):
        self.assertTrue(result['fatal'], result)
        self.assertFalse(result['comparisonComplete'])
        self.assertIsNone(result['changes'])
        self.assertEqual(result['linkedFileAlignments'], [])
        error, = result['comparisonErrors']
        self.assertEqual(error['code'], 'invalid_report')
        self.assertEqual(error['input'], role)
        self.assertIn(detail, error['detail'])

    def test_duplicate_member_status_must_agree_with_original_primary_observation(self):
        for side, role in enumerate(('before', 'after')):
            for source in ('storage', 'linked'):
                for mapped in (False, True):
                    for partial in (False, True):
                        with self.subTest(role=role, source=source, mapped=mapped, partial=partial):
                            # JSON projections are separate observations even
                            # though the scanner shares its in-memory records.
                            reports = json.loads(json.dumps(self.reports))
                            report = reports[side]
                            member = next(m for g in report['duplicateGroups'] for m in g['files']
                                          if m['source'] == source)
                            member['status'] = 'missing'
                            if partial:
                                report['files'][0].pop('sha256')
                            original = deepcopy(reports)
                            result = self.compare(reports, mapped)
                            self.assert_invalid(result, role, 'Duplicate member status disagrees with file inventory')
                            coverage = result['inputs'][role]['coverage']
                            self.assertFalse(coverage['complete'])
                            self.assertIn('invalid_report', coverage['reasons'])
                            self.assertEqual(coverage['filesWithSha256'], len(report['files']) - partial)
                            self.assertEqual(coverage['filesInventoried'], len(report['files']))
                            self.assertEqual(coverage['attachments'], len(report['attachments']))
                            self.assertEqual(coverage['auditErrors'], report['auditErrors'])
                            self.assertEqual(coverage['auditComplete'], report['auditComplete'])
                            self.assertEqual(reports, original)

    def test_duplicate_member_status_absence_and_null_do_not_infer_observations(self):
        for side in (0, 1):
            for source in ('storage', 'linked'):
                for mapped in (False, True):
                    for variant in ('absent', 'null', 'absent_partial', 'null_partial'):
                        with self.subTest(side=side, source=source, mapped=mapped, variant=variant):
                            reports = json.loads(json.dumps(self.reports))
                            report = reports[side]
                            member = next(m for g in report['duplicateGroups'] for m in g['files']
                                          if m['source'] == source)
                            if variant.startswith('absent'):
                                member.pop('status')
                            else:
                                member['status'] = None
                            partial = variant.endswith('_partial')
                            if partial:
                                report['files'][0].pop('sha256')
                            result = self.compare(reports, mapped)
                            self.assertFalse(result['fatal'], result)
                            self.assertEqual(result['comparisonComplete'], not partial)
                            if partial:
                                self.assertIsNone(result['changes'])
                                self.assertEqual(result['linkedFileAlignments'], [])
                                self.assertIn('file_without_stable_sha256',
                                              result['inputs'][('before', 'after')[side]]['coverage']['reasons'])

    def test_duplicate_member_status_partial_primary_observations(self):
        # An unreferenced companion can have unavailable primary status without
        # conflicting with the separate attachment/reference status contract.
        storage = self.inputs[1][1]
        (storage / 'DEFGHJKL/companion.pdf').write_bytes(b'same bytes\n')
        scanned = self.scan(1)
        for status in ('inaccessible', 'unstable', 'missing', 'future_failure', None, 'absent'):
            for mapped in (False, True):
                for contradict in (False, True):
                    with self.subTest(status=status, mapped=mapped, contradict=contradict):
                        reports = json.loads(json.dumps([self.reports[0], scanned]))
                        report = reports[1]
                        primary = next(f for f in report['files'] if f['path'].endswith('/companion.pdf'))
                        member = next(m for g in report['duplicateGroups'] for m in g['files']
                                      if m['path'] == primary['path'])
                        if status == 'absent':
                            primary.pop('status')
                        else:
                            primary['status'] = status
                        member['status'] = 'present' if contradict or status in (None, 'absent') else status
                        primary.pop('sha256')
                        report['auditComplete'] = False
                        report['auditErrors'] = [{'code': 'injected_partial_observation'}]
                        result = self.compare(reports, mapped)
                        if contradict and status not in (None, 'absent'):
                            self.assert_invalid(result, 'after', 'Duplicate member status disagrees with file inventory')
                        else:
                            self.assertFalse(result['fatal'], result)
                            self.assertEqual(result['comparisonErrors'][0]['code'], 'incomplete_evidence')
                        self.assertFalse(result['comparisonComplete'])
                        self.assertIsNone(result['changes'])
                        self.assertEqual(result['linkedFileAlignments'], [])
                        coverage = result['inputs']['after']['coverage']
                        self.assertEqual(coverage['filesWithSha256'], len(report['files']) - 1)
                        self.assertEqual(coverage['auditErrors'], report['auditErrors'])

    def test_declared_duplicate_member_status_requires_nonempty_string(self):
        for status in (False, 0, [], {}, ''):
            with self.subTest(status=status):
                reports = json.loads(json.dumps(self.reports))
                reports[1]['duplicateGroups'][0]['files'][0]['status'] = status
                self.assert_invalid(self.compare(reports), 'after', 'Invalid duplicate member status')

    def test_repeated_duplicate_member_identity_is_invalid_in_either_input(self):
        for side, role in enumerate(('before', 'after')):
            for source in ('storage', 'linked'):
                for mapped in (False, True):
                    for partial in (False, True):
                        with self.subTest(role=role, source=source, mapped=mapped, partial=partial):
                            reports = json.loads(json.dumps(self.reports))
                            report = reports[side]
                            group, = report['duplicateGroups']
                            member = next(m for m in group['files'] if m['source'] == source)
                            other = next(i for i, m in enumerate(group['files'])
                                         if m['path'] != member['path'])
                            count = len(group['files'])
                            group['files'][other] = deepcopy(member)
                            self.assertEqual(len(group['files']), count)
                            if partial:
                                report['files'][0].pop('sha256')
                            original = deepcopy(reports)
                            result = self.compare(reports, mapped)
                            self.assert_invalid(result, role, 'Repeated duplicate group member identity')
                            coverage = result['inputs'][role]['coverage']
                            self.assertFalse(coverage['complete'])
                            self.assertIn('invalid_report', coverage['reasons'])
                            self.assertEqual(coverage['filesInventoried'], len(report['files']))
                            self.assertEqual(coverage['filesWithSha256'], len(report['files']) - partial)
                            self.assertEqual(coverage['attachments'], len(report['attachments']))
                            self.assertEqual(coverage['auditErrors'], report['auditErrors'])
                            self.assertEqual(coverage['auditComplete'], report['auditComplete'])
                            self.assertEqual(reports, original)

    def test_unique_duplicate_members_do_not_require_full_enumeration(self):
        for side in (0, 1):
            for mapped in (False, True):
                for count in (0, 1, 2):
                    with self.subTest(side=side, mapped=mapped, count=count):
                        reports = json.loads(json.dumps(self.reports))
                        reports[side]['duplicateGroups'][0]['files'] = (
                            reports[side]['duplicateGroups'][0]['files'][:count])
                        result = self.compare(reports, mapped)
                        self.assertTrue(result['comparisonComplete'], result)
                        self.assertFalse(result['fatal'])

    def test_duplicate_header_observations_must_agree_with_file_evidence(self):
        for side, role in enumerate(('before', 'after')):
            for field in ('sha256', 'size'):
                for mapped in (False, True):
                    with self.subTest(role=role, field=field, mapped=mapped):
                        reports = json.loads(json.dumps(self.reports))
                        report = reports[side]
                        group, = report['duplicateGroups']
                        self.assertEqual({m['source'] for m in group['files']}, {'storage', 'linked'})
                        group[field] = '0' * 64 if field == 'sha256' else group[field] + 1
                        original = deepcopy(reports)
                        result = self.compare(reports, mapped)
                        self.assert_invalid(result, role, 'Duplicate group ' + (
                            'digest' if field == 'sha256' else 'size') + ' disagrees with file evidence')
                        coverage = result['inputs'][role]['coverage']
                        self.assertIn('invalid_report', coverage['reasons'])
                        self.assertEqual(coverage['filesInventoried'], len(report['files']))
                        self.assertEqual(coverage['filesWithSha256'], len(report['files']))
                        self.assertEqual(coverage['attachments'], len(report['attachments']))
                        self.assertEqual(coverage['auditComplete'], report['auditComplete'])
                        self.assertEqual(reports, original)

    def test_duplicate_header_checks_each_available_projection_in_partial_reports(self):
        for variant in ('primary_only_digest', 'member_only_digest', 'member_size'):
            for side, role in enumerate(('before', 'after')):
                for mapped in (False, True):
                    with self.subTest(variant=variant, role=role, mapped=mapped):
                        reports = json.loads(json.dumps(self.reports))
                        report = reports[side]
                        group, = report['duplicateGroups']
                        if variant == 'primary_only_digest':
                            for member in group['files']:
                                member.pop('sha256')
                            group['sha256'] = '0' * 64
                        elif variant == 'member_only_digest':
                            for record in report['files']:
                                record.pop('sha256', None)
                                record['hashStatus'] = 'skipped_unique_size'
                            group['sha256'] = '0' * 64
                        else:
                            group['files'][0]['stat']['size'] += 1
                        result = self.compare(reports, mapped)
                        self.assert_invalid(result, role, 'Duplicate group ' + (
                            'size' if variant == 'member_size' else 'digest') + ' disagrees with file evidence')
                        self.assertEqual(result['inputs'][role]['coverage']['filesWithSha256'],
                                         0 if variant == 'member_only_digest' else len(report['files']))

    def test_duplicate_header_absence_is_not_contradiction_and_types_are_exact(self):
        for variant in ('empty_groups', 'absent_header', 'null_header', 'missing_primary_hash',
                        'missing_member_hash', 'missing_both_hashes', 'missing_member_size'):
            for mapped in (False, True):
                with self.subTest(variant=variant, mapped=mapped):
                    reports = json.loads(json.dumps(self.reports))
                    report = reports[1]
                    group, = report['duplicateGroups']
                    if variant == 'empty_groups':
                        report['duplicateGroups'] = []
                    elif variant in ('absent_header', 'null_header'):
                        for field in ('sha256', 'size'):
                            if variant == 'null_header':
                                group[field] = None
                            else:
                                group.pop(field)
                    else:
                        if variant in ('missing_primary_hash', 'missing_both_hashes'):
                            for record in report['files']:
                                record.pop('sha256', None)
                                record['hashStatus'] = 'skipped_unique_size'
                        if variant in ('missing_member_hash', 'missing_both_hashes'):
                            for member in group['files']:
                                member.pop('sha256')
                        if variant == 'missing_member_size':
                            for member in group['files']:
                                member['stat'].pop('size')
                    original = deepcopy(reports)
                    result = self.compare(reports, mapped)
                    self.assertFalse(result['fatal'], result)
                    self.assertEqual(result['comparisonComplete'],
                                     variant not in ('missing_primary_hash', 'missing_both_hashes'))
                    self.assertEqual(reports, original)
        for field, value in (('sha256', True), ('sha256', 'z' * 64), ('size', True),
                             ('size', -1), ('size', 11.0), ('size', '11')):
            with self.subTest(field=field, value=value):
                reports = json.loads(json.dumps(self.reports))
                reports[1]['duplicateGroups'][0][field] = value
                self.assert_invalid(self.compare(reports), 'after', 'Invalid duplicate group ' + (
                    'digest' if field == 'sha256' else 'size'))

    def test_duplicate_member_observations_must_agree_with_primary_inventory(self):
        for side, role in enumerate(('before', 'after')):
            for source in ('storage', 'linked'):
                for field in ('sha256', 'references'):
                    for mapped in (False, True):
                        with self.subTest(role=role, source=source, field=field, mapped=mapped):
                            # JSON projections are independent objects, unlike the
                            # scanner's in-memory aliases of the same file record.
                            reports = json.loads(json.dumps(self.reports))
                            report = reports[side]
                            member = next(m for g in report['duplicateGroups'] for m in g['files']
                                          if m['source'] == source and m['references'])
                            primary = next(f for f in report['files'] if f['path'] == member['path'])
                            member[field] = '0' * 64 if field == 'sha256' else []
                            self.assertNotEqual(member[field], primary[field])
                            original = deepcopy(reports)
                            result = self.compare(reports, mapped)
                            self.assert_invalid(result, role, (
                                'Duplicate member digest disagrees with file inventory' if field == 'sha256'
                                else 'Duplicate member references disagree with file inventory'))
                            coverage = result['inputs'][role]['coverage']
                            self.assertFalse(coverage['complete'])
                            self.assertIn('invalid_report', coverage['reasons'])
                            self.assertEqual(coverage['filesInventoried'], len(report['files']))
                            self.assertEqual(coverage['filesWithSha256'], len(report['files']))
                            self.assertEqual(coverage['attachments'], len(report['attachments']))
                            self.assertEqual(coverage['auditComplete'], report['auditComplete'])
                            self.assertEqual(reports, original)

    def test_scanner_duplicate_projections_preserve_aliases_orphans_and_local_ids(self):
        reports = []
        for side, (db, storage) in enumerate(self.inputs):
            # Companion and orphan duplicate members legitimately have no refs.
            for relative in ('DEFGHJKL/companion.pdf', 'ZZZZZZZZ/duplicate.pdf'):
                (storage / relative).write_bytes(b'same bytes\n')
            # Same inode/bytes at distinct paths still means distinct members.
            os.link(storage / 'ABCDEFGH/a.pdf', storage / 'ABCDEFGH/hardlink.pdf')
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute("INSERT INTO items VALUES (60, 2, 'ABCDEFGH')")
                connection.execute("INSERT INTO itemAttachments VALUES (60, NULL, 0, 'storage:a.pdf')")
                connection.execute("INSERT INTO items VALUES (32, 1, 'SSSSSSSS')")
                connection.execute('INSERT INTO itemAttachments VALUES (32, NULL, 2, ?)',
                                   (str(storage / 'ABCDEFGH/a.pdf'),))
                if side:
                    connection.execute('UPDATE items SET itemID=itemID+100')
                    connection.execute('UPDATE itemAttachments SET itemID=itemID+100, parentItemID=parentItemID+100')
                    connection.execute('UPDATE deletedItems SET itemID=itemID+100')
            # Scan relocation and comparison relocation are independent.
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.execute("UPDATE itemAttachments SET path='/original-papers/a.pdf' WHERE path=?",
                                   (str(self.roots[side] / 'a.pdf'),))
            reports.append(audit(db, storage, comparison_scope='consistency-profile',
                                 linked_base_directory=self.roots[side],
                                 linked_root_mappings=[('/original-papers', self.roots[side])],
                                 allow_linked_roots=(self.roots[side], storage)))
        reports = json.loads(json.dumps(reports))
        for report in reports:
            group, = report['duplicateGroups']
            self.assertEqual(len(group['files']), 6)
            self.assertEqual(sum(not m['references'] for m in group['files']), 3)
            # Reference/group ordering is not evidence of a contradiction.
            group['files'].reverse()
            for member in group['files']:
                member['references'].reverse()
        original = deepcopy(reports)
        result = self.compare(reports)
        self.assertTrue(result['comparisonComplete'], result)
        for category in ('filesAdded', 'filesRemoved', 'filesChanged', 'duplicateGroupsAdded',
                         'duplicateGroupsRemoved', 'duplicateGroupsChanged'):
            self.assertEqual(result['changes'][category], [], category)
        # The linked alias into storage retains its changed absolute raw path.
        self.assertEqual([r['identity'] for r in result['changes']['attachmentsChanged']],
                         [dict(libraryID=1, key='SSSSSSSS')])
        self.assertEqual(len(result['linkedFileAlignments']), 1)
        self.assertEqual(reports, original)

    def test_duplicate_member_matching_and_reference_sets_are_unambiguous(self):
        variants = ('unknown_path', 'wrong_source', 'different_reference',
                    'duplicate_reference', 'boolean_reference')
        for variant in variants:
            with self.subTest(variant=variant):
                reports = json.loads(json.dumps(self.reports))
                member = next(m for g in reports[1]['duplicateGroups'] for m in g['files']
                              if m['source'] == 'storage' and m['references'])
                if variant == 'unknown_path':
                    member['path'] += '.not-in-inventory'
                elif variant == 'wrong_source':
                    member['source'] = 'linked'
                elif variant == 'different_reference':
                    member['references'] = [30]
                elif variant == 'duplicate_reference':
                    member['references'] *= 2
                else:
                    self.assertEqual(member['references'], [1])
                    member['references'] = [True]
                self.assert_invalid(self.compare(reports), 'after', (
                    'Duplicate member lacks matching file inventory' if variant in ('unknown_path', 'wrong_source')
                    else 'Duplicate member references disagree with file inventory'))

    def test_attachment_trash_flags_must_agree_with_all_item_inventory(self):
        # Both directions, including remote/local observations and a trashed
        # parent, must agree before any comparison identity relocation.
        for side in (0, 1):
            with closing(sqlite3.connect(self.inputs[side][0])) as connection, connection:
                connection.execute("INSERT INTO deletedItems VALUES (6, '2026-01-01')")
        variants = [self.reports, [self.scan(0), self.scan(1)]]
        for originals in variants:
            for side, role in enumerate(('before', 'after')):
                for original in originals[side]['attachments']:
                    for field in ('inTrash', 'parentInTrash'):
                        for mapped in (False, True):
                            with self.subTest(role=role, key=original['key'], field=field,
                                              original=original[field], mapped=mapped):
                                reports = deepcopy(originals)
                                ref = next(r for r in reports[side]['attachments']
                                           if r['itemID'] == original['itemID'])
                                ref[field] = not ref[field]
                                result = self.compare(reports, mapped)
                                detail = ('Attachment trash flag disagrees with all-item inventory'
                                          if field == 'inTrash' else
                                          'Attachment parent trash flag disagrees with all-item inventory')
                                self.assert_invalid(result, role, detail)
                                coverage = result['inputs'][role]['coverage']
                                self.assertFalse(coverage['complete'])
                                self.assertIn('invalid_report', coverage['reasons'])
                                self.assertEqual(coverage['auditComplete'], reports[side]['auditComplete'])
                                self.assertEqual(coverage['attachments'], len(reports[side]['attachments']))

    def test_referenced_item_trash_evidence_must_be_boolean(self):
        for side, role in enumerate(('before', 'after')):
            for item_id, field in ((7, 'inTrash'), (6, 'parentInTrash')):
                for value in ('absent', None, 0, 1, 'false', [], {}):
                    for mapped in (False, True):
                        with self.subTest(role=role, item_id=item_id, value=value, mapped=mapped):
                            reports = deepcopy(self.reports)
                            item = next(i for i in reports[side]['comparisonEvidence']['items']
                                        if i['itemID'] == item_id)
                            if value == 'absent':
                                item.pop('inTrash')
                            else:
                                item['inTrash'] = value
                            detail = ('Attachment trash flag disagrees with all-item inventory'
                                      if field == 'inTrash' else
                                      'Attachment parent trash flag disagrees with all-item inventory')
                            self.assert_invalid(self.compare(reports, mapped), role, detail)

    def test_scanner_trash_changes_preserve_parent_and_library_identities(self):
        for side in (0, 1):
            with closing(sqlite3.connect(self.inputs[side][0])) as connection, connection:
                # Same keys in two libraries, with independent trash membership.
                connection.executemany('INSERT INTO items VALUES (?, 2, ?)',
                                       [(60, 'DEFGHJKL'), (70, 'EFGHJKLM'), (80, 'FGHJKLMN')])
                connection.executemany('INSERT INTO itemAttachments VALUES (?, ?, ?, ?)',
                                       [(70, None, 3, None), (80, 60, 4, 'storage:image.png')])
        before = self.scan(0)
        for deleted in ((6,), (8,), (6, 8, 7, 30, 60, 70, 80), ()):
            with closing(sqlite3.connect(self.inputs[1][0])) as connection, connection:
                connection.execute('DELETE FROM deletedItems')
                connection.executemany("INSERT INTO deletedItems VALUES (?, '2026-01-01')",
                                       [(i,) for i in deleted])
            after = self.scan(1)
            # Different local row numbers in AFTER must still use AFTER's own
            # all-item lookup, then compare by library/key and parent identity.
            with closing(sqlite3.connect(self.inputs[1][0])) as connection, connection:
                connection.execute('UPDATE items SET itemID=itemID+100')
                connection.execute('UPDATE itemAttachments SET itemID=itemID+100, parentItemID=parentItemID+100')
                connection.execute('UPDATE deletedItems SET itemID=itemID+100')
            renumbered = self.scan(1)
            with closing(sqlite3.connect(self.inputs[1][0])) as connection, connection:
                connection.execute('UPDATE items SET itemID=itemID-100')
                connection.execute('UPDATE itemAttachments SET itemID=itemID-100, parentItemID=parentItemID-100')
                connection.execute('UPDATE deletedItems SET itemID=itemID-100')
            for mapped in (False, True):
                with self.subTest(deleted=deleted, mapped=mapped):
                    expected = self.compare([before, after], mapped)
                    result = self.compare([before, renumbered], mapped)
                    self.assertTrue(result['comparisonComplete'], result)
                    self.assertEqual(result['changes'], expected['changes'])
                    rows = {(r['identity']['libraryID'], r['identity']['key']): r
                            for r in result['changes']['attachmentsChanged']}
                    if 6 in deleted:
                        self.assertTrue(rows[(1, 'FGHJKLMN')]['after']['parentInTrash'])
                        self.assertEqual(rows[(1, 'FGHJKLMN')]['after']['parentIdentity'],
                                         dict(libraryID=1, key='DEFGHJKL'))
                    if 7 in deleted:
                        self.assertTrue(rows[(1, 'EFGHJKLM')]['after']['inTrash'])
                        self.assertFalse(rows[(1, 'EFGHJKLM')]['after']['parentInTrash'])
                    if 60 in deleted:
                        self.assertTrue(rows[(2, 'FGHJKLMN')]['after']['parentInTrash'])
                    if deleted == (6,):
                        self.assertNotIn((2, 'FGHJKLMN'), rows)

    def test_remote_target_is_rejected_without_any_file_reference(self):
        for side, role in enumerate(('before', 'after')):
            targets = [next(f['path'] for f in self.reports[side]['files'] if f['source'] == source)
                       for source in ('storage', 'linked')]
            targets += ['/uninventoried/remote-target.pdf', '', False, 0, []]
            for target in targets:
                for mapped in (False, True):
                    with self.subTest(role=role, target=target, mapped=mapped):
                        reports = deepcopy(self.reports)
                        report = reports[side]
                        ref = next(r for r in report['attachments'] if r['status'] == 'remote_url')
                        self.assertEqual(ref['linkMode'], 3)
                        self.assertNotIn('resolvedPath', ref)
                        ref['resolvedPath'] = target
                        self.assertTrue(all(ref['itemID'] not in f['references'] for f in report['files']))
                        result = self.compare(reports, mapped)
                        self.assert_invalid(result, role, 'Remote attachment cannot have a resolved local target')
                        coverage = result['inputs'][role]['coverage']
                        self.assertFalse(coverage['complete'])
                        self.assertIn('invalid_report', coverage['reasons'])
                        self.assertEqual(coverage['auditComplete'], report['auditComplete'])
                        self.assertEqual(coverage['filesInventoried'], len(report['files']))
                        self.assertEqual(coverage['attachments'], len(report['attachments']))

    def test_scanner_remote_raw_paths_and_null_targets_remain_comparable(self):
        # Mode 3 short-circuits before local path resolution or URL validation.
        for raw in (None, '', 'https://example.invalid/paper', '/local-looking/paper.pdf',
                    'attachments:../paper.pdf', 'not-a-url\x00'):
            for side, role in enumerate(('before', 'after')):
                with closing(sqlite3.connect(self.inputs[side][0])) as connection, connection:
                    connection.execute('UPDATE itemAttachments SET path=? WHERE linkMode=3', (raw,))
                scanned = self.scan(side)
                ref = next(r for r in scanned['attachments'] if r['linkMode'] == 3)
                self.assertEqual(ref['status'], 'remote_url')
                self.assertEqual(ref['path'], raw)
                self.assertNotIn('resolvedPath', ref)
                for null_target in (False, True):
                    for mapped in (False, True):
                        with self.subTest(raw=raw, role=role, null_target=null_target, mapped=mapped):
                            reports = deepcopy(self.reports)
                            reports[side] = deepcopy(scanned)
                            if null_target:
                                next(r for r in reports[side]['attachments']
                                     if r['linkMode'] == 3)['resolvedPath'] = None
                            result = self.compare(reports, mapped)
                            self.assertTrue(result['comparisonComplete'], result)
                            self.assertFalse(result['fatal'])

    def test_missing_target_cannot_be_present_when_omitted_from_file_references(self):
        for side, role in enumerate(('before', 'after')):
            for key in ('QQQQQQQQ', 'RRRRRRRR', 'ABCDEFGH'):
                for mode in ((2,) if key != 'ABCDEFGH' else (0, 1, 4)):
                    for scan_mapped in (False, True):
                        for mapped in (False, True):
                            with self.subTest(role=role, key=key, mode=mode,
                                              scan_mapped=scan_mapped, mapped=mapped):
                                reports = deepcopy(self.reports)
                                report = reports[side]
                                ref = next(r for r in report['attachments'] if r['key'] == key)
                                ref.update(status='missing', linkMode=mode)
                                report['statistics']['referenceStatuses']['present'] -= 1
                                report['statistics']['referenceStatuses']['missing'] += 1
                                for file in report['files'] + [f for g in report['duplicateGroups'] for f in g['files']]:
                                    if ref['itemID'] in file['references']:
                                        file['references'].remove(ref['itemID'])
                                file = next(f for f in report['files'] if f['path'] == ref['resolvedPath'])
                                self.assertEqual(file['status'], 'present')
                                self.assertNotIn(ref['itemID'], file['references'])
                                if mode != 2:
                                    self.assertEqual(file['references'], [])
                                if scan_mapped:
                                    report['policy']['linkedRootMappings'] = [dict(
                                        source='/original-papers', destination=str(self.roots[side]))]
                                    absolute_ref = next(r for r in report['attachments'] if r['key'] == 'QQQQQQQQ')
                                    absolute_ref['path'] = '/original-papers/a.pdf'
                                result = self.compare(reports, mapped)
                                self.assert_invalid(result, role, 'Missing attachment target is inventoried present')
                                coverage = result['inputs'][role]['coverage']
                                self.assertFalse(coverage['complete'])
                                self.assertIn('invalid_report', coverage['reasons'])

    def test_missing_present_contradiction_does_not_depend_on_hash_completeness(self):
        reports = deepcopy(self.reports)
        report = reports[1]
        ref = next(r for r in report['attachments'] if r['key'] == 'ABCDEFGH')
        ref['status'] = 'missing'
        report['statistics']['referenceStatuses']['present'] -= 1
        report['statistics']['referenceStatuses']['missing'] += 1
        file = next(f for f in report['files'] if f['path'] == ref['resolvedPath'])
        file['references'].remove(ref['itemID'])
        file.pop('sha256')
        self.assert_invalid(self.compare(reports), 'after', 'Missing attachment target is inventoried present')

    def test_missing_alias_cannot_still_reference_present_file_in_either_input(self):
        for side, role in enumerate(('before', 'after')):
            for mapped in (False, True):
                with self.subTest(role=role, mapped=mapped):
                    reports = deepcopy(self.reports)
                    ref = next(r for r in reports[side]['attachments'] if r['key'] == 'QQQQQQQQ')
                    ref['status'] = 'missing'
                    result = self.compare(reports, mapped)
                    self.assert_invalid(result, role)

    def test_remote_and_partial_aliases_cannot_reference_present_file(self):
        for status in ('remote_url', 'blocked_linked', 'inaccessible', 'unstable', 'symlink', 'invalid_path'):
            with self.subTest(status=status):
                reports = deepcopy(self.reports)
                ref = next(r for r in reports[1]['attachments'] if r['key'] == 'QQQQQQQQ')
                ref['status'] = status
                if status == 'remote_url':
                    ref['linkMode'] = 3
                else:
                    reports[1]['auditComplete'] = False
                    reports[1]['auditErrors'] = [{'code': status}]
                detail = ('Remote attachment cannot have a resolved local target'
                          if status == 'remote_url' else 'File/reference status mismatch')
                self.assert_invalid(self.compare(reports), 'after', detail)

    def test_storage_observation_status_must_also_agree(self):
        for changed in ('attachment', 'file'):
            with self.subTest(changed=changed):
                reports = deepcopy(self.reports)
                report = reports[1]
                ref = next(r for r in report['attachments'] if r['key'] == 'ABCDEFGH')
                file = next(f for f in report['files'] if f['references'] == [ref['itemID']])
                if changed == 'attachment':
                    ref['status'] = 'missing'
                else:
                    file['status'] = 'unstable'
                    report['auditComplete'] = False
                    report['auditErrors'] = [{'code': 'unstable'}]
                self.assert_invalid(self.compare(reports), 'after')

    def test_valid_aliases_missing_remote_and_unreferenced_files_remain_comparable(self):
        # The fixture has a genuine missing attachment, a remote URL, a parent
        # companion file and an orphan file, alongside both present linked aliases.
        originals = deepcopy(self.reports)
        for path in self.root.iterdir():
            if path.is_dir():
                shutil.rmtree(path)
        result = self.compare(self.reports)
        self.assertTrue(result['comparisonComplete'], result)
        for category in ('filesAdded', 'filesRemoved', 'filesChanged', 'duplicateGroupsChanged',
                         'duplicateGroupsAdded', 'duplicateGroupsRemoved', 'disappearedAttachments'):
            self.assertEqual(result['changes'][category], [])
        aligned, = result['linkedFileAlignments']
        self.assertEqual(len(aligned['before']['attachmentIdentities']), 2)
        self.assertEqual(self.reports, originals)

    def test_consistent_failed_observations_and_missing_sha_remain_coverage_gaps(self):
        module = importlib.import_module('notewrench.zoteroattachments.audit')
        # A real audit hash failure propagates the status to all observed aliases.
        with patch.object(module, 'hash_file', side_effect=FileIssue('inaccessible', 'injected hash read failure')):
            partial = self.scan(1)
        unhashed = deepcopy(self.reports[1])
        unhashed['files'][0].pop('sha256')
        for report in (partial, unhashed):
            with self.subTest(failed=not report['auditComplete']):
                result = self.compare([self.reports[0], report])
                self.assertFalse(result['fatal'], result)
                self.assertFalse(result['comparisonComplete'])
                self.assertIsNone(result['changes'])
                self.assertEqual(result['linkedFileAlignments'], [])
                self.assertEqual(result['comparisonErrors'][0]['code'], 'incomplete_evidence')


if __name__ == '__main__':
    unittest.main()
