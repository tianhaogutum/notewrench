# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic SQLite integration cases for Zotero's attachments: path format."""
import hashlib
import importlib
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from .fixture import create_fixture
from notewrench.zoteroattachments import audit

CORE = importlib.import_module('notewrench.zoteroattachments.audit')
ROOT = Path(__file__).resolve().parents[2]


class RelativeLinkedTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='audit-relative-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db, self.storage = create_fixture(self.root)
        self.base = self.root / 'linked'
        self.target = self.base / 'papers' / '论文 a.pdf'
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b'same bytes\n')
        self.add_link(20, 'GHJKLMNP', 'attachments:papers/论文 a.pdf')

    def add_link(self, item_id, key, raw):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('INSERT INTO items VALUES (?, 1, ?)', (item_id, key))
            db.execute('INSERT INTO itemAttachments VALUES (?, NULL, 2, ?)', (item_id, raw))

    def set_path(self, raw):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('UPDATE itemAttachments SET path=? WHERE itemID=20', (raw,))

    def ref(self, report, item_id=20):
        return next(ref for ref in report['attachments'] if ref['itemID'] == item_id)

    def run_audit(self, **kwargs):
        return audit(self.db, self.storage, **kwargs)

    def test_relative_duplicate_missing_and_absolute_alias(self):
        self.add_link(21, 'HJKLMNPQ', str(self.target))
        self.add_link(22, 'JKLMNPQS', 'attachments:papers/missing.pdf')
        report = self.run_audit(linked_base_directory=self.base,
                                allow_linked_roots=(self.base,))
        self.assertTrue(report['auditComplete'], report['auditErrors'])
        self.assertEqual(self.ref(report)['status'], 'present')
        self.assertEqual(self.ref(report)['resolvedPath'], str(self.target))
        self.assertEqual(self.ref(report, 22)['status'], 'missing')
        group, = report['duplicateGroups']
        self.assertEqual(group['sha256'], hashlib.sha256(b'same bytes\n').hexdigest())
        self.assertEqual(len(group['files']), 3)
        linked, = [f for f in report['files'] if f['path'] == str(self.target)]
        self.assertEqual(linked['source'], 'linked')
        self.assertEqual(linked['references'], [20, 21])
        self.assertEqual(report['policy']['linkedBaseDirectory'], str(self.base))
        self.assertEqual(report['duplicateGroups'], self.run_audit(
            linked_base_directory=self.base, allow_linked_roots=(self.base,),
            hash_all=True)['duplicateGroups'])

    def test_mapping_and_authorization_are_both_required_without_target_io(self):
        real_inspect = CORE.inspect

        def guarded(path):
            self.assertFalse(path.is_relative_to(self.base), 'Unauthorized linked path inspected')
            return real_inspect(path)

        cases = [
            ({'allow_linked_roots': (self.base,)}, 'unsupported_path'),
            ({'linked_base_directory': self.base}, 'blocked_linked'),
            ({'linked_base_directory': self.base,
              'allow_linked_roots': (self.root / 'linked-other',)}, 'blocked_linked'),
        ]
        for kwargs, status in cases:
            with self.subTest(kwargs=kwargs), patch.object(CORE, 'inspect', side_effect=guarded):
                report = self.run_audit(**kwargs)
                self.assertEqual(self.ref(report)['status'], status)
                self.assertFalse(report['auditComplete'])
                self.assertTrue(any(e.get('itemID') == 20 for e in report['auditErrors']))
                self.assertNotIn(str(self.target), {f['path'] for f in report['files']})

    def test_authorized_subdirectory_does_not_authorize_siblings(self):
        self.add_link(21, 'HJKLMNPQ', 'attachments:private/secret.pdf')
        report = self.run_audit(linked_base_directory=self.base,
                                allow_linked_roots=(self.base / 'papers',))
        self.assertEqual(self.ref(report)['status'], 'present')
        self.assertEqual(self.ref(report, 21)['status'], 'blocked_linked')
        self.assertFalse(report['auditComplete'])

    def test_traversal_and_ambiguous_relative_conventions_refused_before_io(self):
        raw_paths = ('attachments:', 'attachments:../outside.pdf',
                     'attachments:papers/../../outside.pdf', 'attachments:/etc/passwd',
                     'attachments://server/share/a.pdf', 'attachments:C:/a.pdf',
                     'attachments:C:a.pdf', 'attachments:papers\\a.pdf',
                     'attachments:papers/./a.pdf', 'attachments:papers//a.pdf',
                     'attachments:papers/', 'attachments:.', 'attachments:..',
                     'attachments:papers/a\x00.pdf', 'relative.pdf',
                     'file:///etc/passwd', 'storage:a.pdf')
        for raw in raw_paths:
            with self.subTest(raw=raw):
                self.set_path(raw)
                # Even broad authorization must not normalize traversal into a target.
                real_inspect = CORE.inspect

                def only_storage(path):
                    self.assertTrue(path.is_relative_to(self.storage), 'Invalid linked target inspected')
                    return real_inspect(path)

                with patch.object(CORE, 'inspect', side_effect=only_storage):
                    report = self.run_audit(linked_base_directory=self.base,
                                            allow_linked_roots=('/',))
                self.assertIn(self.ref(report)['status'], ('invalid_path', 'unsupported_path'))
                self.assertFalse(report['auditComplete'])
                self.assertNotIn('resolvedPath', self.ref(report))

    def test_invalid_base_conventions_are_partial_coverage(self):
        for base in ('relative', '', 'C:/linked', '//server/share',
                     str(self.base / '..' / 'linked'), str(self.base) + '\\papers',
                     str(self.base) + '\x00'):
            with self.subTest(base=base):
                report = self.run_audit(linked_base_directory=base,
                                        allow_linked_roots=('/',))
                self.assertEqual(self.ref(report)['status'], 'invalid_path')
                self.assertFalse(report['auditComplete'])

    def test_relative_symlink_components_and_dangling_targets_refused(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.base, target_is_directory=True)
        report = self.run_audit(linked_base_directory=alias, allow_linked_roots=(self.root,))
        self.assertEqual(self.ref(report)['status'], 'symlink')
        self.assertFalse(report['auditComplete'])
        alias.unlink()
        (self.base / 'via').symlink_to(self.target.parent, target_is_directory=True)
        (self.base / 'file.pdf').symlink_to(self.target)
        (self.base / 'dangling.pdf').symlink_to(self.base / 'absent.pdf')
        for raw in ('attachments:via/论文 a.pdf', 'attachments:file.pdf', 'attachments:dangling.pdf'):
            with self.subTest(raw=raw):
                self.set_path(raw)
                report = self.run_audit(linked_base_directory=self.base, allow_linked_roots=(self.base,))
                self.assertEqual(self.ref(report)['status'], 'symlink')
                self.assertFalse(report['auditComplete'])
                self.assertNotIn(str(self.target), {f['path'] for f in report['files']})

    def test_percent_encoded_components_are_literal_not_url_decoded(self):
        literal = self.base / '%2e%2e' / 'a.pdf'
        literal.parent.mkdir()
        literal.write_bytes(b'same bytes\n')
        self.add_link(21, 'HJKLMNPQ', 'attachments:%2e%2e/a.pdf')
        report = self.run_audit(linked_base_directory=self.base, allow_linked_roots=(self.base,))
        self.assertTrue(report['auditComplete'])
        self.assertEqual(self.ref(report, 21)['resolvedPath'], str(literal))
        self.assertEqual(self.ref(report, 21)['status'], 'present')

    def test_symlink_swap_before_hash_does_not_escape_base(self):
        outside = self.root / 'outside.pdf'
        outside.write_bytes(b'same bytes\n')
        real_hash = CORE.hash_file

        def swap(path, expected, counters):
            if path == self.target:
                path.unlink()
                path.symlink_to(outside)
            return real_hash(path, expected, counters)

        with patch.object(CORE, 'hash_file', side_effect=swap):
            report = self.run_audit(linked_base_directory=self.base,
                                    allow_linked_roots=(self.base,))
        self.assertEqual(self.ref(report)['status'], 'symlink')
        self.assertFalse(report['auditComplete'])
        self.assertNotIn(str(self.target), {f['path'] for g in report['duplicateGroups'] for f in g['files']})

    def test_cli_relative_linked_and_read_only_failure_evidence(self):
        CLI = [sys.executable, '-m', 'notewrench', 'zotero-attachments']
        self.add_link(21, 'HJKLMNPQ', 'attachments:papers/missing.pdf')

        def fingerprints():
            return {str(p): (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ctime_ns)
                    for p in self.root.rglob('*') if p.is_file()}

        before = fingerprints()
        command = [*CLI, '--snapshot', str(self.db),
                   '--storage', str(self.storage), '--linked-base-directory', str(self.base)]
        for extra, code, status in (([], 1, 'blocked_linked'),
                                    (['--allow-linked-root', str(self.base)], 0, 'present')):
            result = subprocess.run(command + extra, cwd=self.root, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, code, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(self.ref(report)['status'], status)
            if code == 0:
                self.assertEqual(self.ref(report, 21)['status'], 'missing')
                self.assertEqual(len(report['duplicateGroups'][0]['files']), 3)
            else:
                self.assertFalse(report['auditComplete'])
                self.assertTrue(report['auditErrors'])
        result = subprocess.run(command + ['--allow-linked-root', str(self.base), '--format', 'markdown'],
                                cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"linkedBaseDirectory"', result.stdout)
        self.assertEqual(before, fingerprints(), 'Input changed or SQLite sidecar created')


if __name__ == '__main__':
    unittest.main()
