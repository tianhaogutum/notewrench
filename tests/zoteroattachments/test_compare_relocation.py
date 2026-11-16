# SPDX-License-Identifier: AGPL-3.0-only
"""Report-only relocation contracts using independently hashed real backups."""
from contextlib import closing
from copy import deepcopy
import hashlib
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


class ComparisonRelocationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='compare-relocation-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.old = self.root / 'day1 papers'
        self.new = self.root / 'day2 papers'
        self.suffix = 'nested/论文 %2e%2e $HOME.pdf'
        self.old_file = self.old / self.suffix
        self.new_file = self.new / self.suffix
        self.old_file.parent.mkdir(parents=True)
        self.old_file.write_bytes(b'same bytes\n')
        shutil.copytree(self.old, self.new, copy_function=shutil.copy2)
        self.inputs = []
        for label, linked in (('before', self.old), ('after', self.new)):
            db, storage = create_fixture(self.root / label)
            with closing(sqlite3.connect(db)) as connection, connection:
                connection.executemany('INSERT INTO items VALUES (?, 1, ?)',
                                       [(30, 'QQQQQQQQ'), (31, 'RRRRRRRR')])
                connection.executemany('INSERT INTO itemAttachments VALUES (?, NULL, 2, ?)',
                                       [(30, str(linked / self.suffix)),
                                        (31, 'attachments:' + self.suffix)])
            self.inputs.append((db, storage, linked))

    def scan(self, side):
        db, storage, linked = self.inputs[side]
        return audit(db, storage, comparison_scope='relocation-profile',
                     linked_base_directory=str(linked), allow_linked_roots=(linked,))

    def compare(self, before=None, after=None, mappings=None):
        paths = []
        for label, report in (('before', before if before is not None else self.scan(0)),
                              ('after', after if after is not None else self.scan(1))):
            path = self.root / (label + '.json')
            path.write_text(json.dumps(report, ensure_ascii=True, indent=2) + '\n')
            paths.append(path)
        return compare_reports(*paths, linked_root_mappings=(
            [(self.old, self.new)] if mappings is None else mappings))

    def assert_rejected(self, result, code):
        self.assertTrue(result['fatal'], result)
        self.assertFalse(result['comparisonComplete'])
        self.assertIsNone(result['changes'])
        self.assertEqual(result['linkedFileAlignments'], [])
        self.assertEqual(result['comparisonErrors'][0]['code'], code)

    def test_move_preserves_file_and_duplicate_identity_aliases_and_exact_report_hashes(self):
        before, after = self.scan(0), self.scan(1)
        originals = deepcopy((before, after))
        delta = self.compare(before, after)
        self.assertTrue(delta['comparisonComplete'], delta)
        for name in ('filesAdded', 'filesRemoved', 'filesChanged', 'duplicateGroupsAdded',
                     'duplicateGroupsRemoved', 'duplicateGroupsChanged'):
            self.assertEqual(delta['changes'][name], [], name)
        # Raw absolute reference changed; the attachments: alias did not.
        self.assertEqual([x['identity']['key'] for x in delta['changes']['attachmentsChanged']], ['QQQQQQQQ'])
        aligned, = delta['linkedFileAlignments']
        self.assertEqual(aligned['identity'], {'source': 'linked', 'path': str(self.new_file)})
        self.assertEqual(aligned['before']['originalIdentity'], {'source': 'linked', 'path': str(self.old_file)})
        self.assertEqual(aligned['before']['path'], str(self.old_file))
        self.assertEqual(aligned['after']['path'], str(self.new_file))
        digest = hashlib.sha256(b'same bytes\n').hexdigest()
        self.assertEqual(aligned['before']['sha256'], digest)
        self.assertEqual(aligned['after']['sha256'], digest)
        self.assertEqual(aligned['after']['attachmentIdentities'],
                         [{'libraryID': 1, 'key': 'QQQQQQQQ'}, {'libraryID': 1, 'key': 'RRRRRRRR'}])
        self.assertEqual(delta['policy']['linkedRootMappings'],
                         [{'source': str(self.old), 'destination': str(self.new)}])
        for role in ('before', 'after'):
            path = self.root / (role + '.json')
            self.assertEqual(delta['inputs'][role]['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertTrue(delta['inputs'][role]['coverage']['complete'])
        self.assertEqual((before, after), originals)
        # Optional mapping: without it, the original add/remove behavior remains.
        unmapped = self.compare(before, after, mappings=[])
        self.assertEqual(len(unmapped['changes']['filesAdded']), 1)
        self.assertEqual(len(unmapped['changes']['filesRemoved']), 1)

    def test_equal_size_and_mtime_edit_still_changes_sha_and_duplicate_membership(self):
        before = self.scan(0)
        stamp = self.new_file.stat()
        self.new_file.write_bytes(b'X' * stamp.st_size)
        os.utime(self.new_file, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        after = self.scan(1)
        delta = self.compare(before, after)
        self.assertTrue(delta['comparisonComplete'], delta)
        changed, = delta['changes']['filesChanged']
        self.assertEqual(changed['identity']['path'], str(self.new_file))
        self.assertEqual(changed['before']['size'], changed['after']['size'])
        self.assertNotEqual(changed['before']['sha256'], changed['after']['sha256'])
        self.assertEqual(delta['changes']['filesAdded'], [])
        self.assertEqual(delta['changes']['filesRemoved'], [])
        group, = delta['changes']['duplicateGroupsChanged']
        self.assertEqual(group['removedMembers'][0]['identity']['path'], str(self.new_file))
        self.assertEqual(group['removedMembers'][0]['path'], str(self.old_file))

    def test_alias_reference_changes_remain_visible_in_duplicate_group(self):
        before = self.scan(0)
        db = self.inputs[1][0]
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute('DELETE FROM itemAttachments WHERE itemID=31')
            connection.execute('DELETE FROM items WHERE itemID=31')
        delta = self.compare(before, self.scan(1))
        self.assertTrue(delta['comparisonComplete'], delta)
        group, = delta['changes']['duplicateGroupsChanged']
        self.assertEqual(group['addedMembers'], [])
        self.assertEqual(group['removedMembers'], [])
        member, = group['changedMembers']
        self.assertEqual(len(member['before']['attachmentIdentities']), 2)
        self.assertEqual(len(member['after']['attachmentIdentities']), 1)

    def test_component_boundaries_unmatched_paths_and_one_pass_mapping(self):
        before, after = self.scan(0), self.scan(1)
        # A prefix lacking a component boundary cannot match.
        delta = self.compare(before, after, [(str(self.old)[:-1], self.new)])
        self.assertEqual(len(delta['changes']['filesRemoved']), 1)
        self.assertEqual(delta['linkedFileAlignments'], [])
        # Domain/range cross-overlap is legal, but neither side is cascaded.
        delta = self.compare(before, after, [(self.old, self.new), (self.new, '/never-read/third')])
        self.assertTrue(delta['comparisonComplete'], delta)
        self.assertEqual(delta['changes']['filesAdded'], [])
        self.assertEqual(delta['changes']['filesRemoved'], [])
        self.assertEqual(delta['linkedFileAlignments'][0]['identity']['path'], str(self.new_file))
        # Root '/' maps descendants; a no-op root is legal and does not fabricate changes.
        delta = self.compare(before, before, [('/', '/')])
        self.assertTrue(delta['comparisonComplete'], delta)
        self.assertTrue(all(not entries for entries in delta['changes'].values()))
        self.assertEqual(delta['linkedFileAlignments'], [])

    def test_invalid_mapping_config_is_rejected_before_report_io(self):
        malformed = [None, {}, 'bad', [('/a',)], [('/a', '/b', '/c')], [(3, '/b')]]
        for raw in ('relative', '/a/../b', '/a/./b', '/a//b', '/a/', '//a', '/a\\b', '/a\x00b', 'C:/a'):
            malformed.extend([[(raw, '/b')], [('/a', raw)]])
        for pairs in ([('/a', '/x'), ('/a', '/y')], [('/a', '/x'), ('/a/sub', '/y')],
                      [('/a', '/x'), ('/b', '/x')], [('/a', '/x'), ('/b', '/x/sub')]):
            malformed.extend([pairs, list(reversed(pairs))])
        from notewrench.zoteroattachments import compare as module
        with patch.object(module, 'read_report', side_effect=AssertionError('must not read')):
            for mappings in malformed:
                with self.subTest(mappings=mappings):
                    result = compare_reports('/absent-before.json', '/absent-after.json',
                                             linked_root_mappings=mappings)
                    self.assert_rejected(result, 'invalid_linked_mapping')

    def test_mapped_and_unmapped_identity_collision_is_rejected_even_with_equal_hash(self):
        # The same suffix exists at both roots in BEFORE, as two distinct files.
        db = self.inputs[0][0]
        with closing(sqlite3.connect(db)) as connection, connection:
            connection.execute("INSERT INTO items VALUES (32, 1, 'SSSSSSSS')")
            connection.execute('INSERT INTO itemAttachments VALUES (32, NULL, 2, ?)', (str(self.new_file),))
        db, storage, _ = self.inputs[0]
        before = audit(db, storage, comparison_scope='relocation-profile',
                       linked_base_directory=str(self.old), allow_linked_roots=(self.old, self.new))
        for reverse in (False, True):
            if reverse:
                before['files'].reverse()
            result = self.compare(before, self.scan(1))
            self.assert_rejected(result, 'ambiguous_linked_identity')
            self.assertTrue(all(entry['sha256'] for entry in result['inputs'].values()))

    def test_file_equal_to_source_root_is_rejected_not_mapped_to_directory(self):
        result = self.compare(mappings=[(self.old_file, self.new)])
        self.assert_rejected(result, 'invalid_linked_mapping')

    def test_mapping_does_not_weaken_report_sha_scope_or_coverage_requirements(self):
        before, good = self.scan(0), self.scan(1)
        for problem in ('unhashed', 'partial', 'legacy', 'scope', 'libraries', 'malformed'):
            after = deepcopy(good)
            if problem == 'unhashed':
                after['files'][0]['sha256'] = None
            elif problem == 'partial':
                after['auditComplete'] = False
                after['auditErrors'] = [{'code': 'inaccessible'}]
            elif problem == 'legacy':
                del after['comparisonEvidence']
            elif problem == 'scope':
                after['comparisonEvidence']['sourceScope'] = 'another-profile'
            elif problem == 'libraries':
                after['comparisonEvidence']['items'].append({'itemID': 99, 'libraryID': 2, 'key': 'TTTTTTTT'})
                after['comparisonEvidence']['libraryIDs'].append(2)
                after['statistics']['items'] += 1
            else:
                after['files'][0]['path'] += '/..'
            with self.subTest(problem=problem):
                delta = self.compare(before, after)
                self.assertFalse(delta['comparisonComplete'])
                self.assertIsNone(delta['changes'])
                self.assertEqual(delta['linkedFileAlignments'], [])
                self.assertEqual(delta['fatal'], problem in ('scope', 'libraries', 'malformed'))
                self.assertTrue(all(entry['sha256'] for entry in delta['inputs'].values()))

    def test_comparison_only_reads_reports_after_all_scan_inputs_are_removed(self):
        before, after = self.scan(0), self.scan(1)
        for label in ('before', 'after'):
            shutil.rmtree(self.root / label)
        shutil.rmtree(self.old)
        shutil.rmtree(self.new)
        from notewrench.zoteroattachments import compare as module
        real_open = module.open_read

        def only_reports(path):
            self.assertIn(path, (self.root / 'before.json', self.root / 'after.json'))
            return real_open(path)

        with patch.object(module, 'open_read', side_effect=only_reports):
            delta = self.compare(before, after)
        self.assertTrue(delta['comparisonComplete'], delta)
        self.assertEqual(delta['changes']['filesAdded'], [])
        self.assertEqual(delta['changes']['filesRemoved'], [])


if __name__ == '__main__':
    unittest.main()
