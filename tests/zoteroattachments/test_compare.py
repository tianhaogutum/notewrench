# SPDX-License-Identifier: AGPL-3.0-only
"""Controlled snapshot/report integration tests; no Zotero instance or credentials."""
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from .fixture import create_fixture
import notewrench.zoteroattachments as package

ROOT = Path(__file__).resolve().parents[2]


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='audit-compare-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db, self.storage = create_fixture(self.root / 'before')
        shutil.copytree(self.root / 'before', self.root / 'after')
        self.next_db = self.root / 'after/zotero.sqlite'
        self.next_storage = self.root / 'after/storage'

    def sql(self, statement, values=(), *, db=None):
        with closing(sqlite3.connect(db or self.next_db)) as connection, connection:
            connection.execute(statement, values)

    def scan(self, after=False, **kwargs):
        return package.audit(self.next_db if after else self.db,
                             self.next_storage if after else self.storage,
                             comparison_scope='synthetic-profile-100', **kwargs)

    def save(self, report, name):
        path = self.root / name
        path.write_text(json.dumps(report, ensure_ascii=True, indent=2) + '\n')
        return path

    def compare(self, before, after):
        return package.compare_reports(self.save(before, 'before.json'), self.save(after, 'after.json'))

    def alter(self):
        restored = self.next_storage / 'STUVWXYZ/missing.pdf'
        restored.parent.mkdir()
        restored.write_bytes(b'same bytes\n')
        (self.next_storage / 'JKLMNPQR/b.pdf').unlink()
        changed = self.next_storage / 'FGHJKLMN/image.png'
        stamp = changed.stat()
        changed.write_bytes(b'X' * stamp.st_size)
        os.utime(changed, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.sql('DELETE FROM deletedItems WHERE itemID=4')
        self.sql('DELETE FROM itemAttachments WHERE itemID=4')
        self.sql('DELETE FROM items WHERE itemID=4')
        self.sql("INSERT INTO items VALUES (20, 1, 'YYYYYYYY')")
        self.sql("INSERT INTO itemAttachments VALUES (20, NULL, 0, 'storage:new.txt')")
        added = self.next_storage / 'YYYYYYYY/new.txt'
        added.parent.mkdir()
        added.write_bytes(b'new attachment')
        # itemID is a local row identifier, never the cross-report identity.
        self.sql('UPDATE items SET itemID=itemID+100')
        self.sql('UPDATE itemAttachments SET itemID=itemID+100, parentItemID=parentItemID+100')
        self.sql('UPDATE deletedItems SET itemID=itemID+100')

    def test_comparison_scope_forces_fresh_hashes_including_unique_sizes(self):
        before = self.scan()
        self.assertEqual(before['policy']['hashStrategy'], 'all')
        self.assertTrue(all(f['hashStatus'] == 'hashed' for f in before['files']))
        self.assertEqual(before['statistics']['bytesHashed'], before['statistics']['bytesInventoried'])
        self.alter()
        after = self.scan(True)
        old = next(f for f in before['files'] if f['path'].endswith('/image.png'))
        new = next(f for f in after['files'] if f['path'].endswith('/image.png'))
        self.assertEqual(old['stat']['mtimeNs'], new['stat']['mtimeNs'])
        self.assertEqual(old['stat']['size'], new['stat']['size'])
        self.assertNotEqual(old['sha256'], new['sha256'])

    def test_two_snapshots_changes_use_stable_identities_and_relocated_roots(self):
        before = self.scan()
        self.alter()
        after = self.scan(True)
        original = deepcopy((before, after))
        delta = self.compare(before, after)
        self.assertTrue(delta['comparisonComplete'], delta)
        changes = delta['changes']
        self.assertEqual(changes['restoredAttachments'][0]['identity'], {'libraryID': 1, 'key': 'STUVWXYZ'})
        self.assertEqual(changes['disappearedAttachments'][0]['identity']['key'], 'JKLMNPQR')
        self.assertEqual([x['identity']['key'] for x in changes['attachmentsAdded']], ['YYYYYYYY'])
        self.assertEqual([x['identity']['key'] for x in changes['attachmentsRemoved']], ['BCDEFGHJ'])
        self.assertEqual([x['identity']['key'] for x in changes['attachmentsChanged']],
                         ['FGHJKLMN', 'JKLMNPQR', 'STUVWXYZ'])
        orphan, = changes['newOrphans']
        self.assertEqual(orphan['key'], 'BCDEFGHJ')
        self.assertEqual(orphan['previousItemIdentities'], [{'libraryID': 1, 'key': 'BCDEFGHJ'}])
        self.assertEqual(changes['filesRemoved'][0]['identity'], {'source': 'storage', 'relativePath': 'JKLMNPQR/b.pdf'})
        self.assertEqual(changes['filesChanged'][0]['after']['attachmentIdentities'], [{'libraryID': 1, 'key': 'FGHJKLMN'}])
        group, = changes['duplicateGroupsChanged']
        self.assertEqual(group['removedMembers'][0]['attachmentIdentities'], [{'libraryID': 1, 'key': 'JKLMNPQR'}])
        self.assertEqual(group['addedMembers'][0]['attachmentIdentities'], [{'libraryID': 1, 'key': 'STUVWXYZ'}])
        for role in ('before', 'after'):
            entry = delta['inputs'][role]
            self.assertEqual(entry['sha256'], hashlib.sha256((self.root / (role + '.json')).read_bytes()).hexdigest())
            self.assertTrue(entry['coverage']['complete'])
        self.assertEqual((before, after), original)

    def test_unchanged_relocated_storage_has_no_changes(self):
        delta = self.compare(self.scan(), self.scan(True))
        self.assertTrue(delta['comparisonComplete'])
        self.assertTrue(all(not entries for entries in delta['changes'].values()), delta)

    def test_duplicate_groups_appear_disappear_and_orphans_resolve(self):
        before = self.scan()
        (self.next_storage / 'JKLMNPQR/b.pdf').write_bytes(b'new unique bytes')
        (self.next_storage / 'CDEFGHJK/different.pdf').write_bytes(b'synthetic image payload\n')
        self.sql("INSERT INTO items VALUES (20, 1, 'ZZZZZZZZ')")
        changes = self.compare(before, self.scan(True))['changes']
        self.assertEqual(len(changes['duplicateGroupsAdded']), 1)
        self.assertEqual(len(changes['duplicateGroupsRemoved']), 1)
        self.assertEqual(changes['resolvedOrphans'][0]['currentItemIdentities'], [{'libraryID': 1, 'key': 'ZZZZZZZZ'}])

    def test_same_key_in_two_libraries_stays_distinct(self):
        for db in (self.db, self.next_db):
            self.sql("INSERT INTO items VALUES (30, 2, 'ABCDEFGH')", db=db)
            self.sql("INSERT INTO itemAttachments VALUES (30, NULL, 0, 'storage:absent.pdf')", db=db)
        self.sql("UPDATE itemAttachments SET path='storage:a.pdf' WHERE itemID=30")
        changes = self.compare(self.scan(), self.scan(True))['changes']
        self.assertEqual(changes['restoredAttachments'][0]['identity'], {'libraryID': 2, 'key': 'ABCDEFGH'})
        self.assertEqual(len(changes['attachmentsChanged']), 1)

    def test_scope_or_library_set_mismatch_refuses_changes(self):
        before = self.scan()
        after = package.audit(self.next_db, self.next_storage, comparison_scope='different-profile')
        for different in (after,):
            delta = self.compare(before, different)
            self.assertTrue(delta['fatal'])
            self.assertIsNone(delta['changes'])
            self.assertEqual(delta['comparisonErrors'][0]['code'], 'comparison_scope_mismatch')
        self.sql("INSERT INTO items VALUES (40, 9, 'QQQQQQQQ')")
        delta = self.compare(before, self.scan(True))
        self.assertTrue(delta['fatal'])
        self.assertEqual(delta['comparisonErrors'][0]['code'], 'comparison_library_mismatch')

    def test_linked_alias_membership_change_and_real_blocked_coverage(self):
        linked = self.root / 'linked.pdf'
        linked.write_bytes(b'same bytes\n')
        for db in (self.db, self.next_db):
            self.sql("INSERT INTO items VALUES (30, 1, 'QQQQQQQQ')", db=db)
            self.sql('INSERT INTO itemAttachments VALUES (30, NULL, 2, ?)', (str(linked),), db=db)
        before = self.scan(allow_linked_roots=(self.root,))
        self.sql("INSERT INTO items VALUES (31, 1, 'RRRRRRRR')")
        self.sql('INSERT INTO itemAttachments VALUES (31, NULL, 2, ?)', (str(linked),))
        delta = self.compare(before, self.scan(True, allow_linked_roots=(self.root,)))
        self.assertTrue(delta['comparisonComplete'])
        group, = delta['changes']['duplicateGroupsChanged']
        member, = group['changedMembers']
        self.assertEqual(member['identity'], {'source': 'linked', 'path': str(linked)})
        self.assertEqual(member['after']['attachmentIdentities'],
                         [{'libraryID': 1, 'key': 'QQQQQQQQ'}, {'libraryID': 1, 'key': 'RRRRRRRR'}])
        blocked = self.compare(before, self.scan(True))
        self.assertFalse(blocked['fatal'])
        self.assertIsNone(blocked['changes'])
        self.assertIn('audit_incomplete', blocked['inputs']['after']['coverage']['reasons'])

    def test_empty_scope_rejected_and_empty_inventory_comparable(self):
        for scope in ('', '  ', '\x00'):
            with self.assertRaises(package.InputError):
                package.audit(self.db, self.storage, comparison_scope=scope)
        for db, storage in ((self.db, self.storage), (self.next_db, self.next_storage)):
            for table in ('deletedItems', 'itemAttachments', 'items'):
                self.sql(f'DELETE FROM {table}', db=db)
            shutil.rmtree(storage)
            storage.mkdir()
        delta = self.compare(self.scan(), self.scan(True))
        self.assertTrue(delta['comparisonComplete'])
        self.assertEqual(delta['libraryIDs'], [])
        self.assertTrue(all(not value for value in delta['changes'].values()))

    def test_missing_symlink_duplicate_json_and_changed_reports_fail_safely(self):
        good = self.save(self.scan(), 'good.json')
        missing = self.root / 'absent.json'
        delta = package.compare_reports(missing, good)
        self.assertTrue(delta['fatal'])
        self.assertIsNone(delta['inputs']['before']['sha256'])
        self.assertTrue(delta['inputs']['after']['sha256'])
        missing.symlink_to(good)
        self.assertTrue(package.compare_reports(good, missing)['fatal'])
        missing.unlink()
        missing.write_text('{"tool":"one","tool":"two"}')
        delta = package.compare_reports(good, missing)
        self.assertTrue(delta['fatal'])
        self.assertIn('Duplicate JSON field', delta['comparisonErrors'][0]['detail'])
        from notewrench.zoteroattachments import compare as comparison_module
        real_inspect = comparison_module.inspect

        def replace_after_read(path):
            if path == good:
                path.write_bytes(path.read_bytes() + b' ')
            return real_inspect(path)

        with patch.object(comparison_module, 'inspect', side_effect=replace_after_read):
            delta = package.compare_reports(good, missing)
        self.assertTrue(delta['fatal'])
        self.assertIsNone(delta['changes'])
        self.assertIn('changed while reading', delta['comparisonErrors'][0]['detail'])

    def test_partial_legacy_and_unhashed_evidence_never_claims_complete_delta(self):
        good = self.scan()
        partial = deepcopy(good)
        partial['auditComplete'] = False
        partial['auditErrors'] = [{'code': 'inaccessible', 'path': '/not/read'}]
        unhashed = deepcopy(good)
        unhashed['files'][0].pop('sha256')
        unhashed['files'][0]['hashStatus'] = 'skipped_unique_size'
        legacy = package.audit(self.db, self.storage, hash_all=True)
        for incomplete in (partial, unhashed, legacy):
            for before, after in ((good, incomplete), (incomplete, good)):
                with self.subTest(incomplete=incomplete.get('auditComplete')):
                    delta = self.compare(before, after)
                    self.assertFalse(delta['comparisonComplete'])
                    self.assertFalse(delta['fatal'])
                    self.assertIsNone(delta['changes'])
                    self.assertTrue(delta['comparisonErrors'])
                    self.assertTrue(all(v['sha256'] for v in delta['inputs'].values()))

    def test_malformed_reports_return_structured_failure_with_both_hashes(self):
        good = self.scan()
        malformed = deepcopy(good)
        malformed['attachments'].append(malformed['attachments'][0])
        bad_refs = deepcopy(good)
        bad_refs['files'][0]['references'] = [99999]
        for bad in (malformed, bad_refs, {'tool': 'wrong'}, [], {'files': None}):
            delta = self.compare(good, bad)
            self.assertTrue(delta['fatal'])
            self.assertFalse(delta['comparisonComplete'])
            self.assertIsNone(delta['changes'])
            self.assertTrue(delta['inputs']['after']['sha256'])
        invalid = self.root / 'invalid.json'
        invalid.write_bytes(b'{ invalid json')
        delta = package.compare_reports(self.save(good, 'before.json'), invalid)
        self.assertTrue(delta['fatal'])
        self.assertEqual(delta['inputs']['after']['sha256'], hashlib.sha256(invalid.read_bytes()).hexdigest())

    def test_cli_compare_readonly_markdown_and_failure_exits(self):
        CLI = [sys.executable, '-m', 'notewrench', 'zotero-attachments']

        def run(*args):
            return subprocess.run([*CLI, *map(str, args)],
                                  cwd=self.root, capture_output=True, text=True, timeout=60)

        before_cli = run('--snapshot', self.db, '--storage', self.storage, '--comparison-scope', 'profile100')
        self.assertEqual(before_cli.returncode, 0, before_cli.stderr)
        self.alter()
        after_cli = run('--snapshot', self.next_db, '--storage', self.next_storage, '--comparison-scope', 'profile100')
        self.assertEqual(after_cli.returncode, 0, after_cli.stderr)
        before = self.root / 'before.json'
        after = self.root / 'after.json'
        before.write_text(before_cli.stdout)
        after.write_text(after_cli.stdout)

        def fingerprints():
            return {str(p): (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ctime_ns)
                    for p in self.root.rglob('*') if p.is_file()}

        original = fingerprints()
        result = run('compare', before, after)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['comparisonComplete'])
        result = run('compare', before, after, '--format', 'markdown')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Comparison complete: **True**', result.stdout)
        self.assertEqual(original, fingerprints())
        partial = json.loads(after_cli.stdout)
        partial['auditComplete'] = False
        partial['auditErrors'] = [{'code': 'io_error'}]
        after.write_text(json.dumps(partial))
        result = run('compare', before, after)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIsNone(json.loads(result.stdout)['changes'])
        after.write_text('not JSON')
        result = run('compare', before, after)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertTrue(json.loads(result.stdout)['fatal'])
        self.assertEqual(result.stderr, '')


if __name__ == '__main__':
    unittest.main()
