# SPDX-License-Identifier: AGPL-3.0-only
"""Relocated-library contract, exercised with real SQLite and filesystem inputs.

--map-linked-root SOURCE DESTINATION / linked_root_mappings=((source, dest),)
rewrites proper descendants once, before destination-only authorization. Sources
are never inspected. Unmatched absolute paths keep their normal authorization.
Overlapping source OR destination roots are fatal configuration errors before IO.
Invalid attachment paths are partial errors; missing mapped files stay missing;
destination symlinks (including swaps before hash) never enter duplicate evidence.
"""
from contextlib import closing
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from .fixture import create_fixture
from notewrench.zoteroattachments import InputError, audit, compare_reports

CORE = importlib.import_module('notewrench.zoteroattachments.audit')
FS = importlib.import_module('notewrench.zoteroattachments.fs')
ROOT = Path(__file__).resolve().parents[2]


class LinkedRelocationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='audit-relocation-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.db, self.storage = create_fixture(self.root)
        self.source = self.root / 'original papers'
        self.destination = self.root / 'backup papers'
        self.target = self.destination / 'nested' / '论文 a.pdf'
        self.target.parent.mkdir(parents=True)
        self.target.write_bytes(b'same bytes\n')
        self.raw = str(self.source / 'nested' / '论文 a.pdf')
        self.mappings = ((self.source, self.destination),)
        self.add_link(20, 'GHJKLMNP', self.raw)

    def add_link(self, item_id, key, raw):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('INSERT INTO items VALUES (?, 1, ?)', (item_id, key))
            db.execute('INSERT INTO itemAttachments VALUES (?, NULL, 2, ?)', (item_id, raw))

    def set_path(self, raw):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('UPDATE itemAttachments SET path=? WHERE itemID=20', (raw,))

    def ref(self, report, item_id=20):
        return next(r for r in report['attachments'] if r['itemID'] == item_id)

    def scan(self, **kwargs):
        options = dict(linked_root_mappings=self.mappings,
                       allow_linked_roots=(self.destination,))
        options.update(kwargs)
        return audit(self.db, self.storage, **options)

    def test_destination_only_mapping_duplicates_missing_and_aliases(self):
        self.add_link(21, 'HJKLMNPQ', str(self.target))
        self.add_link(22, 'JKLMNPQS', 'attachments:nested/论文 a.pdf')
        self.add_link(23, 'KLMNPQST', str(self.source / 'missing.pdf'))
        # A source symlink is irrelevant: the tool must not touch the source tree.
        self.source.symlink_to(self.storage, target_is_directory=True)
        real_open = FS.open_read

        def guarded(path, **kwargs):
            self.assertFalse(path.is_relative_to(self.source), 'Source path opened')
            return real_open(path, **kwargs)

        with patch.object(FS, 'open_read', side_effect=guarded), patch.object(CORE, 'open_read', side_effect=guarded):
            report = self.scan(linked_base_directory=self.destination)
        self.assertTrue(report['auditComplete'], report['auditErrors'])
        self.assertEqual(self.ref(report)['path'], self.raw)
        self.assertEqual(self.ref(report)['resolvedPath'], str(self.target))
        self.assertEqual(self.ref(report)['status'], 'present')
        self.assertEqual(self.ref(report, 23)['status'], 'missing')
        self.assertEqual(self.ref(report, 23)['resolvedPath'], str(self.destination / 'missing.pdf'))
        linked, = [f for f in report['files'] if f['path'] == str(self.target)]
        self.assertEqual(linked['references'], [20, 21, 22])
        group, = report['duplicateGroups']
        self.assertEqual(group['sha256'], hashlib.sha256(b'same bytes\n').hexdigest())
        self.assertEqual(len(group['files']), 3)
        self.assertEqual(report['policy']['linkedRootMappings'], [
            {'source': str(self.source), 'destination': str(self.destination)}])

    def test_mapping_grants_no_access_and_never_falls_back_to_source(self):
        real_inspect = CORE.inspect

        def only_storage(path):
            self.assertTrue(path.is_relative_to(self.storage), 'Unauthorized linked path inspected')
            return real_inspect(path)

        for allowed in ((), (self.source,), (self.root / 'backup papers-other',)):
            with self.subTest(allowed=allowed), patch.object(CORE, 'inspect', side_effect=only_storage):
                report = self.scan(allow_linked_roots=allowed)
            self.assertEqual(self.ref(report)['status'], 'blocked_linked')
            self.assertEqual(self.ref(report)['resolvedPath'], str(self.target))
            self.assertFalse(report['auditComplete'])
        original = Path(self.raw)
        original.parent.mkdir(parents=True)
        original.write_bytes(b'source must never be used')
        self.target.unlink()
        report = self.scan(allow_linked_roots=(self.source, self.destination))
        self.assertEqual(self.ref(report)['status'], 'missing')
        self.assertNotIn(str(original), {f['path'] for f in report['files']})

    def test_multiple_disjoint_mappings_boundaries_and_no_cascade(self):
        second = self.root / 'second backup'
        second.mkdir()
        (second / 'second.pdf').write_bytes(b'another file')
        # Destination of the first is also a source. Mapping is exactly one pass.
        self.add_link(21, 'HJKLMNPQ', str(self.destination / 'second.pdf'))
        self.add_link(22, 'JKLMNPQS', str(self.source) + '-other/absent.pdf')
        self.add_link(23, 'KLMNPQST', str(self.target.parent / 'unmapped.pdf'))
        mappings = self.mappings + ((self.destination, second),)
        report = self.scan(linked_root_mappings=mappings, allow_linked_roots=(self.destination, second))
        self.assertEqual(self.ref(report)['resolvedPath'], str(self.target))
        self.assertEqual(self.ref(report)['status'], 'present')
        self.assertEqual(self.ref(report, 21)['resolvedPath'], str(second / 'second.pdf'))
        self.assertEqual(self.ref(report, 21)['status'], 'present')
        self.assertEqual(self.ref(report, 22)['status'], 'blocked_linked')
        self.assertEqual(self.ref(report, 22)['resolvedPath'], str(self.source) + '-other/absent.pdf')
        self.assertEqual(self.ref(report, 23)['status'], 'missing')
        self.assertEqual(self.ref(report, 23)['resolvedPath'], str(second / 'nested/unmapped.pdf'))

    def test_overlapping_source_or_destination_roots_refused_before_io(self):
        other = self.root / 'other'
        cases = (self.mappings * 2,
                 self.mappings + ((self.source, other),),
                 self.mappings + ((self.source / 'nested', other),),
                 self.mappings + ((self.root, other),),
                 self.mappings + ((other, self.destination),),
                 self.mappings + ((other, self.destination / 'nested'),),
                 self.mappings + ((other, self.root),))
        for mappings in cases:
            for ordering in (mappings, tuple(reversed(mappings))):
                with self.subTest(mappings=ordering), patch.object(CORE, 'load_snapshot') as load:
                    with self.assertRaises(InputError) as caught:
                        self.scan(linked_root_mappings=ordering)
                    self.assertEqual(caught.exception.code, 'invalid_linked_mapping')
                    load.assert_not_called()

    def test_invalid_mapping_syntax_and_shape_refused_before_io(self):
        roots = ('', 'relative', 'C:/papers', '//host/share', '/a/../b', '/a/./b',
                 '/a//b', '/a/', '/a\\b', '/a\x00b')
        malformed = [((bad, self.destination),) for bad in roots]
        malformed += [((self.source, bad),) for bad in roots]
        malformed += [('single-string',), ((self.source,),), ((self.source, self.destination, 'extra'),),
                      ((None, self.destination),), 'not-pairs', None]
        for mappings in malformed:
            with self.subTest(mappings=mappings), patch.object(CORE, 'load_snapshot') as load:
                with self.assertRaises(InputError) as caught:
                    self.scan(linked_root_mappings=mappings)
                self.assertEqual(caught.exception.code, 'invalid_linked_mapping')
                load.assert_not_called()

    def test_traversal_and_ambiguous_absolute_paths_refused_before_io(self):
        raw_paths = (str(self.source) + '/../outside.pdf',
                     str(self.source) + '/nested/../../outside.pdf',
                     str(self.source) + '/./nested/a.pdf',
                     str(self.source) + '//nested/a.pdf',
                     str(self.source) + '/nested/', str(self.source) + '/nested\\a.pdf',
                     '/' + self.raw, str(self.source), self.raw + '\x00')
        real_inspect = CORE.inspect

        def only_storage(path):
            self.assertTrue(path.is_relative_to(self.storage), 'Invalid linked path inspected')
            return real_inspect(path)

        for raw in raw_paths:
            with self.subTest(raw=raw):
                self.set_path(raw)
                with patch.object(CORE, 'inspect', side_effect=only_storage):
                    report = self.scan(allow_linked_roots=('/',))
                self.assertEqual(self.ref(report)['status'], 'invalid_path')
                self.assertNotIn('resolvedPath', self.ref(report))
                self.assertFalse(report['auditComplete'])

    def test_destination_root_ancestor_subdirectory_file_and_dangling_symlinks(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.destination, target_is_directory=True)
        mappings = (((self.source, alias),), ((self.source, alias / 'nested'),))
        for mapping in mappings:
            with self.subTest(mapping=mapping):
                report = self.scan(linked_root_mappings=mapping, allow_linked_roots=(self.root,))
                self.assertEqual(self.ref(report)['status'], 'symlink')
                self.assertFalse(report['auditComplete'])
        (self.destination / 'via').symlink_to(self.target.parent, target_is_directory=True)
        (self.destination / 'file.pdf').symlink_to(self.target)
        (self.destination / 'dangling.pdf').symlink_to(self.destination / 'absent.pdf')
        for suffix in ('via/论文 a.pdf', 'file.pdf', 'dangling.pdf'):
            with self.subTest(suffix=suffix):
                self.set_path(str(self.source / suffix))
                report = self.scan()
                self.assertEqual(self.ref(report)['status'], 'symlink')
                self.assertFalse(report['auditComplete'])

    def test_symlink_swap_before_hash_excludes_mapped_file(self):
        outside = self.root / 'outside.pdf'
        outside.write_bytes(b'same bytes\n')
        real_hash = CORE.hash_file

        def swap(path, expected, counters):
            if path == self.target:
                path.unlink()
                path.symlink_to(outside)
            return real_hash(path, expected, counters)

        with patch.object(CORE, 'hash_file', side_effect=swap):
            report = self.scan()
        self.assertEqual(self.ref(report)['status'], 'symlink')
        self.assertFalse(report['auditComplete'])
        self.assertNotIn(str(self.target), {f['path'] for g in report['duplicateGroups'] for f in g['files']})

    def test_relative_mapping_is_not_remapped_and_percent_components_are_literal(self):
        self.set_path('attachments:nested/论文 a.pdf')
        report = self.scan(linked_base_directory=self.source)
        self.assertEqual(self.ref(report)['resolvedPath'], self.raw)
        self.assertEqual(self.ref(report)['status'], 'blocked_linked')
        literal = self.destination / '%2e%2e' / '$HOME~.pdf'
        literal.parent.mkdir()
        literal.write_bytes(b'literal path')
        self.set_path(str(self.source / '%2e%2e' / '$HOME~.pdf'))
        report = self.scan()
        self.assertTrue(report['auditComplete'])
        self.assertEqual(self.ref(report)['resolvedPath'], str(literal))

    def test_comparison_ready_mapped_reports_keep_original_and_resolved_evidence(self):
        before = self.scan(comparison_scope='relocation-test')
        after = self.scan(comparison_scope='relocation-test')
        paths = [self.root / 'before.json', self.root / 'after.json']
        for path, report in zip(paths, (before, after)):
            path.write_text(json.dumps(report))
        result = compare_reports(*paths)
        self.assertTrue(result['comparisonComplete'], result)
        self.assertTrue(all(not values for values in result['changes'].values()))
        # Scan mappings do not claim comparison-root migration support.
        self.assertEqual(self.ref(before)['path'], self.raw)
        self.assertEqual(self.ref(before)['resolvedPath'], str(self.target))

    def test_cli_json_markdown_partial_fatal_and_read_only(self):
        CLI = [sys.executable, '-m', 'notewrench', 'zotero-attachments']
        self.add_link(21, 'HJKLMNPQ', str(self.source / 'missing.pdf'))

        def fingerprints():
            return {str(p): (p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ctime_ns)
                    for p in self.root.rglob('*') if p.is_file()}

        before = fingerprints()
        command = [*CLI, '--snapshot', str(self.db), '--storage', str(self.storage),
                   '--map-linked-root', str(self.source), str(self.destination)]
        for extra, code, status in (([], 1, 'blocked_linked'),
                                    (['--allow-linked-root', str(self.source)], 1, 'blocked_linked'),
                                    (['--allow-linked-root', str(self.destination)], 0, 'present')):
            result = subprocess.run(command + extra, cwd=self.root, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, code, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(self.ref(report)['status'], status)
            if code == 0:
                self.assertEqual(self.ref(report, 21)['status'], 'missing')
                self.assertEqual(len(report['duplicateGroups'][0]['files']), 3)
        result = subprocess.run(command + ['--allow-linked-root', str(self.destination), '--format', 'markdown'],
                                cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"linkedRootMappings"', result.stdout)
        result = subprocess.run(command + ['--map-linked-root', str(self.source / 'nested'), str(self.root / 'other')],
                                cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)['auditErrors'][0]['code'], 'invalid_linked_mapping')
        self.assertEqual(before, fingerprints(), 'Inputs changed or sidecars created')


if __name__ == '__main__':
    unittest.main()
