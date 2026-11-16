# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import errno
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from .fixture import create_fixture
from notewrench.zoteroattachments import InputError, audit
from notewrench.zoteroattachments.cli import markdown
from notewrench.zoteroattachments.fs import FileIssue, inspect

CORE = importlib.import_module('notewrench.zoteroattachments.audit')
SNAPSHOT = importlib.import_module('notewrench.zoteroattachments.snapshot')
ROOT = Path(__file__).resolve().parents[2]


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='audit-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db, self.storage = create_fixture(self.root)

    def run_audit(self, **kwargs):
        return audit(self.db, self.storage, **kwargs)

    def sql(self, statement, values=()):
        with sqlite3.connect(self.db) as db:
            db.execute(statement, values)

    def add_link(self, target, mode=2, key='GHJKLMNP', item_id=20):
        self.sql('INSERT INTO items VALUES (?, 1, ?)', (item_id, key))
        self.sql('INSERT INTO itemAttachments VALUES (?, NULL, ?, ?)', (item_id, mode, str(target)))

    def ref(self, report, item_id):
        return next(r for r in report['attachments'] if r['itemID'] == item_id)

    def cli(self, *extra):
        return subprocess.run([sys.executable, '-m', 'notewrench', 'zotero-attachments', '--snapshot', str(self.db),
                               '--storage', str(self.storage), *extra], cwd=ROOT,
                              capture_output=True, text=True, timeout=60)

    def test_real_reduced_schema_duplicates_missing_and_orphans(self):
        report = self.run_audit()
        self.assertTrue(report['auditComplete'])
        self.assertEqual(report['snapshot']['quickCheck'], 'ok')
        group, = report['duplicateGroups']
        self.assertEqual(group['sha256'], hashlib.sha256(b'same bytes\n').hexdigest())
        self.assertEqual({Path(f['path']).name for f in group['files']}, {'a.pdf', 'b.pdf'})
        self.assertEqual(self.ref(report, 3)['status'], 'missing')
        self.assertEqual([d['key'] for d in report['orphanCandidates']], ['ZZZZZZZZ'])
        self.assertEqual(self.ref(report, 7)['status'], 'remote_url')
        self.assertEqual(self.ref(report, 8)['status'], 'present')

    def test_trash_and_nonattachment_items_retain_directories(self):
        report = self.run_audit()
        retained = {d['key']: d for d in report['retainedDirectories']}
        self.assertTrue(retained['BCDEFGHJ']['itemIdentities'][0]['inTrash'])
        self.assertIn('DEFGHJKL', retained)
        self.assertTrue(self.ref(report, 4)['inTrash'])
        self.sql("INSERT INTO deletedItems VALUES (6, '2026-01-01')")
        self.assertTrue(self.ref(self.run_audit(), 8)['parentInTrash'])

    def test_all_libraries_keys_protected(self):
        self.sql("INSERT INTO items VALUES (21, 2, 'ZZZZZZZZ')")
        self.sql("INSERT INTO items VALUES (22, 2, 'ABCDEFGH')")
        report = self.run_audit()
        self.assertEqual(report['orphanCandidates'], [])
        entry = next(d for d in report['retainedDirectories'] if d['key'] == 'ABCDEFGH')
        self.assertEqual(len(entry['itemIdentities']), 2)

    def test_same_size_different_bytes_not_merged(self):
        report = self.run_audit()
        paths = {f['path'] for g in report['duplicateGroups'] for f in g['files']}
        self.assertNotIn(str(self.storage / 'CDEFGHJK/different.pdf'), paths)
        self.assertEqual(len(b'same bytes\n'), len(b'other data\n'))

    def test_size_prefilter_equals_reference_and_reads_fewer_bytes(self):
        optimized = self.run_audit()
        reference = self.run_audit(hash_all=True)
        self.assertEqual(optimized['duplicateGroups'], reference['duplicateGroups'])
        self.assertLess(optimized['statistics']['bytesHashed'], reference['statistics']['bytesHashed'])
        self.assertEqual(reference['statistics']['bytesHashed'], reference['statistics']['bytesInventoried'])
        self.assertEqual(reference['statistics']['filesHashed'], reference['statistics']['filesInventoried'])
        self.assertGreater(optimized['statistics']['filesSkippedUniqueSize'], 0)

    def test_zero_byte_duplicates(self):
        for key in ('ABCDEFGH', 'JKLMNPQR'):
            (self.storage / key / 'empty').touch()
        groups = self.run_audit()['duplicateGroups']
        self.assertTrue(any(g['size'] == 0 and len(g['files']) == 2 for g in groups))

    def test_hardlinks_report_path_vs_inode_bytes(self):
        os.link(self.storage / 'ABCDEFGH/a.pdf', self.storage / 'JKLMNPQR/alias.pdf')
        group, = self.run_audit()['duplicateGroups']
        self.assertEqual(group['extraPathBytes'], 22)
        self.assertEqual(group['extraDistinctInodeBytes'], 11)

    def test_nested_companion_files_included(self):
        nested = self.storage / 'ABCDEFGH/resources'
        nested.mkdir()
        (nested / 'companion.txt').write_bytes(b'same bytes\n')
        group, = self.run_audit()['duplicateGroups']
        self.assertEqual(len(group['files']), 3)
        self.assertEqual(next(f for f in group['files'] if 'companion' in f['path'])['references'], [])

    def test_blocked_linked_not_statted_or_hashed(self):
        target = self.root / 'private.pdf'
        target.write_bytes(b'same bytes\n')
        self.add_link(target)
        real_inspect = CORE.inspect
        def guarded(path):
            self.assertNotEqual(path, target, 'Blocked linked target was inspected')
            return real_inspect(path)
        with patch.object(CORE, 'inspect', side_effect=guarded):
            report = self.run_audit()
        self.assertEqual(self.ref(report, 20)['status'], 'blocked_linked')
        self.assertFalse(report['auditComplete'])
        self.assertNotIn(str(target), {f['path'] for f in report['files']})

    def test_allowed_linked_and_missing_distinguished(self):
        target = self.root / 'external.pdf'
        target.write_bytes(b'same bytes\n')
        self.add_link(target)
        self.add_link(self.root / 'absent.pdf', key='HJKLMNPQ', item_id=21)
        report = self.run_audit(allow_linked_roots=(self.root,))
        self.assertTrue(report['auditComplete'])
        self.assertEqual(self.ref(report, 20)['status'], 'present')
        self.assertEqual(self.ref(report, 21)['status'], 'missing')
        self.assertEqual(len(report['duplicateGroups'][0]['files']), 3)

    def test_allowlist_component_boundary(self):
        target = self.root / 'allowed-other' / 'file.pdf'
        self.add_link(target)
        report = self.run_audit(allow_linked_roots=(self.root / 'allowed',))
        self.assertEqual(self.ref(report, 20)['status'], 'blocked_linked')

    @unittest.skipIf(os.geteuid() == 0, 'root bypasses real Unix permissions; injected EACCES covered separately')
    def test_real_linked_permission_failure_is_not_missing(self):
        target = self.root / 'secret.pdf'
        target.write_bytes(b'private bytes')
        self.add_link(target)
        target.chmod(0)
        self.addCleanup(target.chmod, 0o600)
        report = self.run_audit(allow_linked_roots=(self.root,))
        self.assertEqual(self.ref(report, 20)['status'], 'inaccessible')
        self.assertNotEqual(self.ref(report, 20)['status'], 'missing')

    def test_injected_eacces_reference_is_reported(self):
        target = self.root / 'private.pdf'
        self.add_link(target)
        real_inspect = CORE.inspect
        def denied(path):
            if path == target:
                raise FileIssue('inaccessible', 'EACCES synthetic fault injection')
            return real_inspect(path)
        with patch.object(CORE, 'inspect', side_effect=denied):
            report = self.run_audit(allow_linked_roots=(self.root,))
        self.assertEqual(self.ref(report, 20)['status'], 'inaccessible')
        self.assertTrue(any(e['code'] == 'inaccessible' for e in report['auditErrors']))

    @unittest.skipIf(os.geteuid() == 0, 'root bypasses directory permissions')
    def test_storage_permission_error_not_empty_success(self):
        self.storage.chmod(0)
        self.addCleanup(self.storage.chmod, 0o700)
        report = self.run_audit()
        self.assertFalse(report['auditComplete'])
        self.assertTrue(any(e['code'] == 'inaccessible' for e in report['auditErrors']))
        self.assertEqual(self.ref(report, 1)['status'], 'inaccessible')

    def test_symlink_file_directory_ancestor_and_dangling_refused(self):
        target = self.storage / 'ABCDEFGH/a.pdf'
        target.unlink()
        target.symlink_to(self.storage / 'JKLMNPQR/b.pdf')
        (self.storage / 'YYYYYYYY').symlink_to(self.root, target_is_directory=True)
        (self.storage / 'JKLMNPQR/dangling').symlink_to(self.root / 'not-there')
        external = self.root / 'alias'
        external.symlink_to(self.storage, target_is_directory=True)
        self.add_link(external / 'JKLMNPQR/b.pdf')
        report = self.run_audit(allow_linked_roots=(self.root,))
        self.assertEqual(self.ref(report, 1)['status'], 'symlink')
        self.assertEqual(self.ref(report, 20)['status'], 'symlink')
        self.assertEqual(report['duplicateGroups'], [])
        self.assertTrue(any(e['path'].endswith('/dangling') for e in report['auditErrors']))
        self.assertNotIn('YYYYYYYY', [d['key'] for d in report['orphanCandidates']])

    def test_symlink_storage_root_refused(self):
        alias = self.root / 'storage-link'
        alias.symlink_to(self.storage, target_is_directory=True)
        report = audit(self.db, alias)
        self.assertFalse(report['auditComplete'])
        self.assertEqual(report['files'], [])
        self.assertEqual(self.ref(report, 1)['status'], 'symlink')

    def test_fifo_refused_without_blocking(self):
        fifo = self.storage / 'ABCDEFGH/pipe'
        os.mkfifo(fifo)
        self.sql("UPDATE itemAttachments SET path='storage:pipe' WHERE itemID=1")
        result = self.cli()
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.ref(json.loads(result.stdout), 1)['status'], 'not_regular')

    def test_storage_traversal_and_wrong_prefix_fail_explicitly(self):
        for raw in ('storage:../outside', 'storage:/etc/passwd', 'storage:a/b',
                    'storage:a\\b', 'storage:', 'storage:..', '../old.pdf', '/tmp/file', 'storage:x\x00y'):
            with self.subTest(raw=raw):
                self.sql('UPDATE itemAttachments SET path=? WHERE itemID=1', (raw,))
                report = self.run_audit()
                self.assertIn(self.ref(report, 1)['status'], ('invalid_path', 'unsupported_path'))
                self.assertFalse(report['auditComplete'])

    def test_linked_relative_windows_and_traversal_rejected(self):
        for raw in ('attachments:sub/a.pdf', 'relative.pdf', 'C:\\Users\\a.pdf', '/tmp/../etc/passwd'):
            with self.subTest(raw=raw):
                self.sql('UPDATE itemAttachments SET linkMode=2,path=? WHERE itemID=1', (raw,))
                ref = self.ref(self.run_audit(allow_linked_roots=('/',)), 1)
                self.assertIn(ref['status'], ('invalid_path', 'unsupported_path'))

    def test_unknown_mode_and_empty_path_are_audit_errors(self):
        self.sql('UPDATE itemAttachments SET linkMode=99 WHERE itemID=1')
        self.sql('UPDATE itemAttachments SET path=NULL WHERE itemID=2')
        report = self.run_audit()
        self.assertEqual(self.ref(report, 1)['status'], 'unsupported_link_mode')
        self.assertEqual(self.ref(report, 2)['status'], 'invalid_path')

    def test_unknown_storage_entry_is_explicit_and_not_orphan(self):
        (self.storage / 'not-a-key').mkdir()
        (self.storage / 'random.txt').write_bytes(b'x')
        report = self.run_audit()
        self.assertFalse(report['auditComplete'])
        self.assertEqual(sum(e['code'] == 'unrecognized_storage_entry' for e in report['auditErrors']), 2)
        self.assertEqual([d['key'] for d in report['orphanCandidates']], ['ZZZZZZZZ'])

    def test_file_mutated_during_hash_excluded(self):
        target = self.storage / 'ABCDEFGH/a.pdf'
        real_read = os.read
        triggered = False
        def changing(fd, size):
            nonlocal triggered
            block = real_read(fd, size)
            if not triggered and os.fstat(fd).st_ino == target.stat().st_ino:
                triggered = True
                with target.open('ab') as stream:
                    stream.write(b'changed')
            return block
        with patch.object(CORE.os, 'read', side_effect=changing):
            report = self.run_audit()
        self.assertTrue(triggered)
        self.assertEqual(self.ref(report, 1)['status'], 'unstable')
        self.assertFalse(report['duplicateGroups'])
        self.assertTrue(any(e['code'] == 'unstable' for e in report['auditErrors']))

    def test_unique_size_mutation_checked_even_when_not_hashed(self):
        target = self.storage / 'FGHJKLMN/image.png'
        real_inspect = CORE.inspect
        calls = 0
        def changing(path):
            nonlocal calls
            if path == target:
                calls += 1
                if calls == 2:
                    target.write_bytes(b'different unique payload')
            return real_inspect(path)
        with patch.object(CORE, 'inspect', side_effect=changing):
            report = self.run_audit()
        self.assertEqual(self.ref(report, 8)['status'], 'unstable')

    def test_snapshot_missing_schema_and_views_refused(self):
        self.sql('DROP TABLE itemAttachments')
        self.sql('CREATE VIEW itemAttachments AS SELECT itemID FROM items')
        with self.assertRaises(InputError) as ctx:
            self.run_audit()
        self.assertEqual(ctx.exception.code, 'unsupported_schema')
        self.assertEqual(self.cli().returncode, 2)

    def test_snapshot_missing_column(self):
        self.sql('ALTER TABLE itemAttachments RENAME COLUMN path TO unexpected')
        with self.assertRaises(InputError) as ctx:
            self.run_audit()
        self.assertEqual(ctx.exception.code, 'unsupported_schema')

    def test_snapshot_bad_identity_and_dangling_rows(self):
        self.sql("UPDATE items SET key='../bad' WHERE itemID=1")
        with self.assertRaises(InputError) as ctx:
            self.run_audit()
        self.assertEqual(ctx.exception.code, 'inconsistent_snapshot')
        self.sql("UPDATE items SET key='ABCDEFGH' WHERE itemID=1")
        self.sql("INSERT INTO deletedItems VALUES (900, '2026-01-01')")
        with self.assertRaisesRegex(InputError, 'dangling'):
            self.run_audit()

    def test_attachment_parent_dangling_and_wrong_library(self):
        self.sql('UPDATE itemAttachments SET parentItemID=900 WHERE itemID=1')
        with self.assertRaisesRegex(InputError, 'dangling'):
            self.run_audit()
        self.sql('UPDATE itemAttachments SET parentItemID=6 WHERE itemID=1')
        self.sql('UPDATE items SET libraryID=2 WHERE itemID=6')
        with self.assertRaisesRegex(InputError, 'another library'):
            self.run_audit()

    def test_snapshot_sidecars_refused_even_empty_or_dangling(self):
        for suffix in ('-wal', '-shm', '-journal'):
            sidecar = Path(str(self.db) + suffix)
            sidecar.touch()
            with self.assertRaises(InputError) as ctx:
                self.run_audit()
            self.assertEqual(ctx.exception.code, 'snapshot_sidecar')
            sidecar.unlink()
        sidecar.symlink_to(self.root / 'absent')
        with self.assertRaisesRegex(InputError, 'standalone'):
            self.run_audit()

    def test_snapshot_symlink_refused(self):
        alias = self.root / 'alias.sqlite'
        alias.symlink_to(self.db)
        with self.assertRaises(InputError) as ctx:
            audit(alias, self.storage)
        self.assertEqual(ctx.exception.code, 'snapshot_symlink')

    def test_snapshot_modified_during_read_refused(self):
        real_inspect = SNAPSHOT.inspect
        def change_snapshot(path):
            with sqlite3.connect(self.db) as db:
                db.execute("INSERT INTO items VALUES (901, 1, 'YYYYYYYY')")
            return real_inspect(path)
        with patch.object(SNAPSHOT, 'inspect', side_effect=change_snapshot):
            with self.assertRaises(InputError) as ctx:
                self.run_audit()
        self.assertEqual(ctx.exception.code, 'inconsistent_snapshot')

    def test_corrupt_and_missing_snapshot_give_structured_fatal(self):
        self.db.write_bytes(b'not a database')
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertTrue(json.loads(result.stdout)['fatal'])
        self.db.unlink()
        result = self.cli()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['auditErrors'][0]['code'], 'snapshot_missing')
        self.assertFalse(self.db.exists())

    def test_inputs_unchanged_no_sidecars_cli_json_and_markdown(self):
        def fingerprints():
            return {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob('*') if p.is_file()}
        before = fingerprints()
        result = self.cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report['auditComplete'])
        result = self.cli('--format', 'markdown')
        self.assertEqual(result.returncode, 0)
        self.assertIn('## Complete evidence', result.stdout)
        self.assertEqual(before, fingerprints())

    def test_markdown_keeps_complete_evidence_and_escapes_fence(self):
        report = self.run_audit()
        report['auditErrors'].append({'detail': '\n```\n# fake heading\n```'})
        document = markdown(report)
        self.assertIn('````json', document)
        payload = document.split('````json\n', 1)[1].rsplit('\n````', 1)[0]
        self.assertEqual(json.loads(payload), report)

    def test_cli_error_exit_one_with_evidence(self):
        self.add_link('/external/not-approved.pdf')
        result = self.cli()
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)['auditComplete'])
        self.assertEqual(result.stderr, '')

    def test_cli_preserves_error_exit_codes(self):
        CLI = [sys.executable, '-m', 'notewrench', 'zotero-attachments']
        command = [*CLI, '--snapshot', str(self.db), '--storage', str(self.storage)]
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)['duplicateGroups']), 1)
        self.add_link('/external/not-approved.pdf')
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)['auditComplete'])
        self.db.write_bytes(b'invalid SQLite')
        result = subprocess.run(command, cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 2)
        self.assertTrue(json.loads(result.stdout)['fatal'])

    def test_blob_path_and_mode_are_structured_snapshot_errors(self):
        for column in ('path', 'linkMode'):
            with self.subTest(column=column):
                self.sql(f'UPDATE itemAttachments SET {column}=? WHERE itemID=1', (b'blob',))
                result = self.cli()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stdout)['auditErrors'][0]['code'], 'inconsistent_snapshot')
                self.sql("UPDATE itemAttachments SET path='storage:a.pdf', linkMode=0 WHERE itemID=1")

    def test_snapshot_requires_explicit_cli_argument(self):
        result = subprocess.run([sys.executable, '-m', 'notewrench', 'zotero-attachments', '--storage', str(self.storage)],
                                cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 2)
        self.assertIn('--snapshot', result.stderr)

    def test_directory_changed_during_audit_is_reported(self):
        real_hash = CORE.hash_file
        triggered = False
        def changed(path, expected, counters):
            nonlocal triggered
            if not triggered:
                triggered = True
                (self.storage / 'ABCDEFGH/new-file').write_bytes(b'new content')
            return real_hash(path, expected, counters)
        with patch.object(CORE, 'hash_file', side_effect=changed):
            report = self.run_audit()
        self.assertFalse(report['auditComplete'])
        self.assertTrue(any(e['code'] == 'unstable_directory' for e in report['auditErrors']))

    def test_io_failure_during_hash_excludes_duplicate(self):
        target = self.storage / 'ABCDEFGH/a.pdf'
        real_hash = CORE.hash_file
        def failed(path, expected, counters):
            if path == target:
                raise FileIssue('io_error', 'Synthetic read failure')
            return real_hash(path, expected, counters)
        with patch.object(CORE, 'hash_file', side_effect=failed):
            report = self.run_audit()
        self.assertEqual(self.ref(report, 1)['status'], 'io_error')
        self.assertEqual(report['duplicateGroups'], [])
        self.assertFalse(report['auditComplete'])

    def test_actual_read_oserror_classified_with_partial_byte_count(self):
        real_read = os.read
        target_inode = (self.storage / 'ABCDEFGH/a.pdf').stat().st_ino
        def failing_read(fd, size):
            if os.fstat(fd).st_ino == target_inode:
                raise OSError(errno.EIO, 'Synthetic disk read error')
            return real_read(fd, size)
        with patch.object(CORE.os, 'read', side_effect=failing_read):
            report = self.run_audit()
        self.assertEqual(self.ref(report, 1)['status'], 'io_error')
        self.assertEqual(report['duplicateGroups'], [])
        self.assertGreater(report['statistics']['filesHashAttempted'], report['statistics']['filesHashed'])

    def test_linked_existing_directory_is_not_missing(self):
        self.add_link(self.storage)
        report = self.run_audit(allow_linked_roots=(self.root,))
        self.assertEqual(self.ref(report, 20)['status'], 'not_regular')
        self.assertFalse(report['auditComplete'])


if __name__ == '__main__':
    unittest.main()
