# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zotlog-sync contributors
import copy
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from notewrench.zoterologseq import Invalid, synchronize
from notewrench.zoterologseq.model import (MANIFEST_NAME, LOCK_NAME, block, canonical, digest,
                               identity, issued_text, load_items, new_page)
from notewrench.zoterologseq.graph import claims
from notewrench.zoterologseq.sync import Conflict, Plan, apply, atomic_write

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = json.loads((ROOT / 'examples/zotero-logseq/library.json').read_text())


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'library.json'
        self.graph = self.root / 'graph'
        self.write_source()

    def write_source(self, items=None):
        self.source.write_text(canonical(SAMPLE if items is None else items), encoding='utf-8')

    def sync(self, dry_run=False):
        return synchronize(self.source, self.graph, dry_run)

    def snapshot(self):
        if not self.graph.exists():
            return {}
        return {p.relative_to(self.graph).as_posix(): p.read_bytes()
                for p in self.graph.rglob('*') if p.is_file()}

    def assert_conflict_without_writes(self):
        before = self.snapshot()
        report = self.sync()
        self.assertEqual('conflict', report['status'], report)
        self.assertTrue(report['conflicts'])
        self.assertEqual(before, self.snapshot())
        return report

    def test_end_to_end_all_fields_and_full_source_record(self):
        report = self.sync()
        self.assertEqual('applied', report['status'])
        self.assertEqual(['pages/Doe2024.md', 'pages/Lin2025.md'], report['created'])
        page = (self.graph / 'pages/Doe2024.md').read_text()
        for expected in ('citekey:: Doe2024', 'zotlog-source:: csl-json',
                         'bibliography-title:: Local notes', 'issued:: 2024-02-29',
                         'authors:: Robin Doe; Synthetic Research Collective',
                         'doi:: 10.5555/synthetic.zotlog.2024',
                         'url:: https://example.org/papers/local-notes',
                         'keywords:: offline; notes; synthetic'):
            self.assertIn(expected, page)
        raw = page.split('    ```json\n', 1)[1].split('    ```\n', 1)[0]
        self.assertEqual(SAMPLE[0], json.loads(raw))
        self.assertIn('abstract', report['preserved_unmapped'][0]['fields'])
        manifest = json.loads((self.graph / MANIFEST_NAME).read_text())
        self.assertEqual(identity('Doe2024'), manifest['entries']['Doe2024']['identity'])
        self.assertEqual('pages/Doe2024.md', manifest['entries']['Doe2024']['path'])

    def test_documented_example_matches_generated_output(self):
        self.sync()
        self.assertEqual((ROOT / 'examples/zotero-logseq/Doe2024.md').read_bytes(),
                         (self.graph / 'pages/Doe2024.md').read_bytes())

    def test_two_syncs_byte_and_mtime_identical(self):
        self.sync()
        before = self.snapshot()
        mtimes = {p: p.stat().st_mtime_ns for p in self.graph.rglob('*') if p.is_file()}
        report = self.sync()
        self.assertEqual(before, self.snapshot())
        self.assertEqual(mtimes, {p: p.stat().st_mtime_ns for p in mtimes})
        self.assertEqual(2, len(report['unchanged']))
        self.assertFalse(report['created'] or report['updated'])

    def test_only_managed_region_changes_and_unicode_notes_survive(self):
        self.sync()
        path = self.graph / 'pages/Doe2024.md'
        page = path.read_bytes()
        begin = page.index(b'- <!-- zotlog-sync:begin')
        end = page.index(b' -->\n', page.index(b'- <!-- zotlog-sync:end')) + 5
        prefix = page[:begin] + 'custom:: 人工属性\n\n'.encode()
        suffix = '\n- Notes\r\n  - 人工笔记 [[Other]]\n\nEND (no newline)'.encode()
        path.write_bytes(prefix + page[begin:end] + suffix)
        changed = copy.deepcopy(SAMPLE)
        changed[0]['title'] = 'A new title'
        changed[0]['issued'] = {'date-parts': [[2026]]}
        self.write_source(changed)
        report = self.sync()
        result = path.read_bytes()
        self.assertTrue(result.startswith(prefix))
        self.assertTrue(result.endswith(suffix))
        self.assertIn(b'bibliography-title:: A new title', result)
        self.assertIn(b'issued:: 2026', result)
        self.assertEqual(['pages/Doe2024.md'], report['updated'])
        self.assertEqual(['pages/Lin2025.md'], report['unchanged'])
        self.assertEqual('applied', self.sync()['status'])

    def test_same_title_different_ids(self):
        self.sync()
        a, b = [self.graph / f'pages/{key}.md' for key in ('Doe2024', 'Lin2025')]
        self.assertNotEqual(a.read_bytes(), b.read_bytes())
        self.assertTrue(a.exists() and b.exists())

    def test_dry_run_new_graph_creates_nothing(self):
        report = self.sync(True)
        self.assertEqual('planned', report['status'])
        self.assertEqual(2, len(report['created']))
        self.assertFalse(self.graph.exists())

    def test_dry_run_existing_graph_changes_nothing(self):
        self.sync()
        before = self.snapshot()
        items = copy.deepcopy(SAMPLE)
        items[0]['title'] = 'Changed'
        self.write_source(items)
        report = self.sync(True)
        self.assertEqual(['pages/Doe2024.md'], report['updated'])
        self.assertEqual(before, self.snapshot())

    def test_stale_retained_repeatedly_then_reactivated(self):
        self.sync()
        page = self.graph / 'pages/Lin2025.md'
        content = page.read_bytes() + b'\n- My stale notes\n'
        page.write_bytes(content)
        self.write_source(SAMPLE[:1])
        for _ in range(2):
            report = self.sync()
            self.assertEqual([{'id': 'Lin2025', 'path': 'pages/Lin2025.md'}], report['stale'])
            self.assertEqual(content, page.read_bytes())
            self.assertIn('Lin2025', json.loads((self.graph / MANIFEST_NAME).read_text())['entries'])
        self.write_source()
        self.assertFalse(self.sync()['stale'])
        self.assertEqual(content, page.read_bytes())

    def test_changed_citekey_is_create_plus_stale(self):
        self.sync()
        items = copy.deepcopy(SAMPLE)
        items[0]['id'] = 'Doe2024New'
        self.write_source(items)
        report = self.sync()
        self.assertEqual(['pages/Doe2024New.md'], report['created'])
        self.assertEqual('Doe2024', report['stale'][0]['id'])
        self.assertTrue((self.graph / 'pages/Doe2024.md').exists())

    def test_source_order_does_not_change_graph(self):
        self.sync()
        before = self.snapshot()
        self.write_source(list(reversed(SAMPLE)))
        self.sync()
        self.assertEqual(before, self.snapshot())

    def test_empty_export_retains_everything(self):
        self.sync()
        before = self.snapshot()
        self.write_source([])
        report = self.sync()
        self.assertEqual(2, len(report['stale']))
        self.assertEqual(before, self.snapshot())

    def test_invalid_input_cases_leave_graph_absent(self):
        invalid = [
            {'items': SAMPLE}, 'not an array', [1],
            [dict(SAMPLE[0], id=1)], [dict(SAMPLE[0], id=True)],
            [dict(SAMPLE[0], id=None)], [dict(SAMPLE[0], id='')],
            [{k: v for k, v in SAMPLE[0].items() if k != 'id'}],
            [{k: v for k, v in SAMPLE[0].items() if k != 'type'}],
            [dict(SAMPLE[0], type='unknown-type')],
            [dict(SAMPLE[0], title=123)], [dict(SAMPLE[0], DOI=[])],
            [dict(SAMPLE[0], URL=None)], [dict(SAMPLE[0], keyword=['tag'])],
            [SAMPLE[0], SAMPLE[0]],
            [dict(SAMPLE[0], **{'citation-key': 'Different'})],
            [dict(SAMPLE[0], author='Doe')], [dict(SAMPLE[0], author=[{}])],
            [dict(SAMPLE[0], author=[{'family': 12}])],
            [dict(SAMPLE[0], author=[{'literal': 'Group', 'family': 'Doe'}])],
            [dict(SAMPLE[0], author=[{'family': 'Doe', 'unknown': 'x'}])],
        ]
        for value in invalid:
            with self.subTest(value=value):
                self.write_source(value)
                with self.assertRaises(Invalid):
                    self.sync()
                self.assertFalse(self.graph.exists())

    def test_unsafe_ids_rejected(self):
        for key in ('../escape', '/tmp/escape', 'a/b', 'a\\b', 'a:b', 'a?b',
                    'a b', 'a\nb', '.hidden', 'trailing.', 'CON', 'nul.txt',
                    'LPT1', 'COM9', 'e\u0301', 'x' * 121, 'a%2Fb', 'a[[b]]', 'a\x00b'):
            with self.subTest(key=key):
                self.write_source([dict(SAMPLE[0], id=key)])
                with self.assertRaises(Invalid):
                    self.sync()
                self.assertFalse(self.graph.exists())

    def test_case_and_unicode_collisions_within_input(self):
        for first, second in [('Doe', 'doe'), ('Straße', 'STRASSE'), ('Ａ', 'A')]:
            with self.subTest(first=first):
                self.write_source([dict(SAMPLE[0], id=first), dict(SAMPLE[1], id=second)])
                with self.assertRaisesRegex(Invalid, 'collision'):
                    self.sync()

    def test_unicode_filename(self):
        self.write_source([dict(SAMPLE[0], id='王2024')])
        self.sync()
        self.assertTrue((self.graph / 'pages/王2024.md').exists())

    def test_invalid_dates(self):
        dates = [None, [], '', {}, {'date-parts': []}, {'date-parts': [[2024], [2025], [2026]]},
                 {'date-parts': [2024]}, {'date-parts': [[]]}, {'date-parts': [[True]]},
                 {'date-parts': [[2024.5]]}, {'date-parts': [[2024, 13]]},
                 {'date-parts': [[2023, 2, 29]]}, {'date-parts': [[2024, 4, 31]]},
                 {'date-parts': [[2024, 1, 0]]}, {'date-parts': [[0]]},
                 {'date-parts': [[2025], [2024]]}, {'date-parts': [[10000]]}, {'date-parts': [['9' * 5000]]},
                 {'date-parts': None, 'literal': 'unknown'}, {'raw': 12}, {'literal': ''},
                 {'edtf': '2024'}, {'date-parts': [[2024]], 'season': 5},
                 {'date-parts': [[2024]], 'circa': []}]
        for date in dates:
            with self.subTest(date=date):
                self.write_source([dict(SAMPLE[0], issued=date)])
                with self.assertRaises(Invalid):
                    self.sync()
                self.assertFalse(self.graph.exists())

    def test_supported_dates_and_names(self):
        for value, expected in [({'date-parts': [['2024', '2', '29']]}, '2024-02-29'),
                                ({'date-parts': [[-44]]}, '-0044'),
                                ({'date-parts': [[2024, 1], [2024, 3]]}, '2024-01 / 2024-03'),
                                ({'literal': 'in press'}, 'literal: in press'),
                                ({'raw': '2024~'}, 'raw: 2024~'),
                                ({'date-parts': [[2024]], 'season': 2, 'circa': True},
                                 '2024; season: 2; circa: True')]:
            self.assertEqual(expected, issued_text(value))
        item = dict(SAMPLE[0], author=[{'family': 'Beispiel', 'given': 'Ada',
                    'non-dropping-particle': 'von', 'suffix': 'Jr.', 'comma-suffix': True}])
        self.write_source([item])
        self.sync()
        self.assertIn(b'authors:: Ada von Beispiel Jr.', (self.graph / 'pages/Doe2024.md').read_bytes())

    def test_special_content_inert_and_raw_lossless(self):
        item = dict(SAMPLE[0], title='<script>[[Page]] {{renderer x}}\n- BAD\u2028é',
                    abstract='```\n- <!-- zotlog-sync:end fake -->\n\u0085\u2028')
        self.write_source([item])
        self.sync()
        data = (self.graph / 'pages/Doe2024.md').read_text()
        self.assertIn('&#60;script&#62;&#91;&#91;', data)
        self.assertNotIn('\n- BAD', data)
        raw = data.split('    ```json\n', 1)[1].split('    ```\n', 1)[0]
        self.assertEqual(item, json.loads(raw))
        self.assertEqual(1, len(self.sync()['unchanged']))

    def test_bad_json(self):
        for data in (b'{', b'[{"id":"x","id":"y"}]', b'[NaN]', b'[Infinity]',
                     b'[1e999]', b'[{"id":"\\ud800"}]', b'\xff', b'[{}] trailing'):
            with self.subTest(data=data):
                self.source.write_bytes(data)
                with self.assertRaises(Invalid):
                    self.sync()
                self.assertFalse(self.graph.exists())

    def test_precision_losing_numbers_rejected(self):
        for token in ('1.234567890123456789', '1e-999', '9007199254740993.0', '0e999999999999999999999999'):
            with self.subTest(token=token):
                self.source.write_text('[{"id":"Precise","type":"book","custom":{"n":' + token + '}}]')
                with self.assertRaisesRegex(Invalid, 'precision loss'):
                    self.sync()
                self.assertFalse(self.graph.exists())
        self.source.write_text('[{"id":"Precise","type":"book","custom":{"n":0.1,"m":1e23}}]')
        self.sync()
        self.assertIn(b'"n": 0.1', (self.graph / 'pages/Precise.md').read_bytes())

    def test_nonregular_source_does_not_block(self):
        self.source.unlink()
        os.mkfifo(self.source)
        with self.assertRaisesRegex(Invalid, 'regular'):
            self.sync()
        self.assertFalse(self.graph.exists())

    def test_symlink_lock_rejected(self):
        self.sync()
        lock = self.graph / LOCK_NAME
        lock.unlink()
        outside = self.root / 'outside-lock'
        outside.write_bytes(b'untouched')
        lock.symlink_to(outside)
        with self.assertRaises(OSError):
            self.sync()
        self.assertEqual(b'untouched', outside.read_bytes())

    def test_bom_input(self):
        self.source.write_bytes(b'\xef\xbb\xbf' + self.source.read_bytes())
        self.assertEqual('applied', self.sync()['status'])

    def test_existing_untracked_file_blocks_whole_batch(self):
        (self.graph / 'pages').mkdir(parents=True)
        (self.graph / 'pages/Lin2025.md').write_text('- Human only\n')
        report = self.assert_conflict_without_writes()
        self.assertEqual('Lin2025', report['conflicts'][0]['id'])
        self.assertFalse((self.graph / 'pages/Doe2024.md').exists())

    def test_orphan_managed_file_is_not_adopted(self):
        self.sync()
        (self.graph / MANIFEST_NAME).unlink()
        self.assertEqual(2, len(self.assert_conflict_without_writes()['conflicts']))

    def test_existing_case_or_format_filename_collision(self):
        (self.graph / 'pages').mkdir(parents=True)
        for name in ('doe2024.md', 'Doe2024.org', 'DOE2024.MD', 'Doe2024.markdown'):
            with self.subTest(name=name):
                path = self.graph / 'pages' / name
                path.write_text('human')
                self.assert_conflict_without_writes()
                path.unlink()

    def test_graph_identity_alias_blocks_create_and_update_with_sources(self):
        self.sync()
        alias = self.graph / 'pages/Reading.md'
        alias.write_text('alias:: [[New2026]], Reading list\n\n- Human notes\n')
        self.write_source([dict(SAMPLE[0], title='Changed'), SAMPLE[1],
                           {'id': 'New2026', 'type': 'book'}])
        report = self.assert_conflict_without_writes()
        conflict = next(c for c in report['conflicts'] if c['code'] == 'graph-identity-collision')
        self.assertEqual('New2026', conflict['id'])
        self.assertEqual('pages/New2026.md', conflict['path'])
        self.assertEqual({'path': 'pages/Reading.md', 'property': 'alias',
                          'line': 1, 'value': 'New2026'}, conflict['sources'][1])
        self.assertEqual('citekey', conflict['sources'][0]['property'])
        self.assertIn('Reading.md:1', conflict['message'])
        self.assertFalse((self.graph / 'pages/New2026.md').exists())

    def test_graph_identity_title_and_org_alias_unicode_collisions(self):
        (self.graph / 'pages').mkdir(parents=True)
        for name, text in [('Reading.md', 'title:: Ｄｏｅ２０２４'),
                           ('Reading.markdown', 'ALIAS:: [[doe2024]]'),
                           ('Reading.org', '#+TITLE: Reading\n#+ALIAS: [[DOE2024]], Other')]:
            with self.subTest(name=name):
                path = self.graph / 'pages' / name
                path.write_text(text + '\n\n- Human notes\n')
                report = self.assert_conflict_without_writes()
                self.assertTrue(any(c['code'] == 'graph-identity-collision'
                                    for c in report['conflicts']), report)
                path.unlink()

    def test_graph_identity_legal_aliases_preserved_and_body_properties_ignored(self):
        self.sync()
        path = self.graph / 'pages/Doe2024.md'
        data = path.read_bytes().replace(b'\n\n',
            b'\nalias:: [[Reading, list]], Local reading, [[Doe2024]]\n\n', 1)
        data += b'\n- A normal block\n  alias:: Lin2025\n'
        path.write_bytes(data)
        self.write_source([dict(SAMPLE[0], title='Changed'), SAMPLE[1]])
        self.assertEqual('applied', self.sync()['status'])
        self.assertEqual(data.split(b'- <!-- zotlog-sync:begin')[0],
                         path.read_bytes().split(b'- <!-- zotlog-sync:begin')[0])
        self.assertTrue(path.read_bytes().endswith(b'  alias:: Lin2025\n'))
        self.assertEqual(2, len(self.sync()['unchanged']))

    def test_graph_identity_unsupported_properties_fail_closed(self):
        (self.graph / 'pages').mkdir(parents=True)
        cases = ['alias:: "Doe2024"', 'alias:: [[Doe2024]] trailing',
                 'alias:: [Doe2024, Other]', 'alias:: {{query x}}',
                 'alias:: Doe2024\nalias:: Other', 'aliases:: Doe2024',
                 'alias:: Other\n  continued value', 'title:: [[Doe2024]]',
                 '---\nalias: [[Doe2024]]\n---', '- alias:: Doe2024',
                 ':PROPERTIES:\n:ALIAS: Doe2024\n:END:']
        path = self.graph / 'pages/Reading.md'
        for text in cases:
            with self.subTest(text=text):
                path.write_text(text + '\n')
                report = self.assert_conflict_without_writes()
                self.assertTrue(any(c['code'] == 'graph-identity-coverage'
                                    for c in report['conflicts']), report)
                self.assertFalse((self.graph / MANIFEST_NAME).exists())

    def test_graph_identity_namespaces_reserve_parents_without_leaf_inference(self):
        (self.graph / 'pages').mkdir(parents=True)
        path = self.graph / 'pages/Topic___Sub___Child.md'
        path.write_text('- Human notes\n')
        self.write_source([{'id': 'Topic', 'type': 'book'}])
        self.assert_conflict_without_writes()
        self.write_source([{'id': 'Child', 'type': 'book'}])
        self.assertEqual('applied', self.sync()['status'])
        self.assertEqual(b'- Human notes\n', path.read_bytes())
        for prop in ('title:: Topic/Sub', 'alias:: [[Topic/Sub]]'):
            with self.subTest(prop=prop):
                path.unlink(missing_ok=True)
                (self.graph / 'pages/Reading.md').write_text(prop + '\n')
                self.write_source([{'id': 'Topic', 'type': 'book'}])
                report = self.assert_conflict_without_writes()
                conflict = next(c for c in report['conflicts']
                                if c['path'] == 'pages/Topic.md')
                self.assertEqual('Topic/Sub', conflict['sources'][1]['value'])

    def test_graph_identity_namespace_encoded_citekey_rejected(self):
        self.write_source([{'id': 'Topic___Child', 'type': 'book'}])
        with self.assertRaisesRegex(Invalid, 'namespace'):
            self.sync()
        self.assertFalse(self.graph.exists())

    def test_graph_identity_journal_alias_and_stale_target_are_checked(self):
        self.sync()
        (self.graph / 'journals').mkdir()
        (self.graph / 'journals/2026_11_11.md').write_text('alias:: Lin2025\n')
        self.write_source(SAMPLE[:1])
        report = self.assert_conflict_without_writes()
        self.assertEqual('Lin2025', report['conflicts'][0]['id'])
        self.assertIn('journals/2026_11_11.md', report['conflicts'][0]['message'])

    def test_graph_identity_unreadable_or_nested_pages_report_coverage(self):
        (self.graph / 'pages').mkdir(parents=True)
        path = self.graph / 'pages/Reading.md'
        path.write_bytes(b'\xff')
        self.assert_conflict_without_writes()
        path.unlink()
        path.mkdir()
        self.assert_conflict_without_writes()
        path.rmdir()
        nested = self.graph / 'pages/nested'
        nested.mkdir()
        (nested / 'Reading.md').write_text('alias:: Doe2024\n')
        self.assert_conflict_without_writes()

    def test_graph_identity_snapshot_covers_alias_edits_and_new_files(self):
        self.sync()
        path = self.graph / 'pages/Reading.md'
        path.write_text('alias:: Other\n')
        items = load_items(self.source.read_bytes())
        plan = Plan(self.graph, items)
        path.write_text('alias:: Doe2024\n')
        with self.assertRaisesRegex(Conflict, 'changed after preflight'):
            apply(plan)
        path.write_text('alias:: Other\n')
        plan = Plan(self.graph, items)
        (self.graph / 'pages/New.md').write_text('alias:: Doe2024\n')
        with self.assertRaisesRegex(Conflict, 'changed after preflight'):
            apply(plan)

    def test_graph_identity_tracked_alias_edit_during_plan_is_detected(self):
        self.sync()
        path = self.graph / 'pages/Doe2024.md'
        before = self.snapshot()

        def edit_after_index(relative, data):
            result = claims(relative, data)
            if relative == 'pages/Doe2024.md':
                path.write_bytes(data.replace(b'\n\n', b'\nalias:: Lin2025\n\n', 1))
            return result

        with patch('notewrench.zoterologseq.sync.claims', side_effect=edit_after_index):
            plan = Plan(self.graph, load_items(self.source.read_bytes()))
        with self.assertRaisesRegex(Conflict, 'changed after preflight'):
            apply(plan)
        self.assertEqual(before[MANIFEST_NAME], (self.graph / MANIFEST_NAME).read_bytes())
        self.assertIn(b'alias:: Lin2025', path.read_bytes())

    def test_new_id_colliding_with_stale_id_rejected(self):
        self.sync()
        self.write_source([dict(SAMPLE[0], id='doe2024')])
        self.assert_conflict_without_writes()

    def test_managed_edits_rejected(self):
        self.sync()
        path = self.graph / 'pages/Doe2024.md'
        path.write_bytes(path.read_bytes().replace(b'Local notes', b'Hand edited', 1))
        self.assert_conflict_without_writes()

    def test_damaged_markers_rejected(self):
        self.sync()
        path = self.graph / 'pages/Doe2024.md'
        original = path.read_bytes()
        for data in (original.replace(b'zotlog-sync:begin', b'broken:begin'),
                     original + block(load_items(self.source.read_bytes())[0]),
                     original.replace(b'zotlog-sync:end', b'zotlog-sync:begin'),
                     original.replace(b'citekey:: Doe2024', b'citekey:: Changed')):
            with self.subTest(data=data[:30]):
                path.write_bytes(data)
                self.assert_conflict_without_writes()
        path.write_bytes(original)
        self.assertEqual('applied', self.sync()['status'])

    def test_missing_tracked_page_not_recreated(self):
        self.sync()
        (self.graph / 'pages/Doe2024.md').unlink()
        self.assert_conflict_without_writes()

    def test_corrupt_manifest_cases(self):
        self.sync()
        path = self.graph / MANIFEST_NAME
        original = json.loads(path.read_text())
        broken = copy.deepcopy(original)
        broken['entries']['Doe2024']['path'] = '../outside.md'
        wrong_id = copy.deepcopy(original)
        wrong_id['entries']['Doe2024']['identity'] = 'a' * 64
        wrong_hash = copy.deepcopy(original)
        wrong_hash['entries']['Doe2024']['managed_sha256'] = 'x'
        for value in ([], {}, dict(original, version=2), dict(original, version=True),
                      dict(original, extra=True), dict(original, entries=[]), broken, wrong_id, wrong_hash):
            with self.subTest(value=value):
                path.write_text(canonical(value))
                self.assert_conflict_without_writes()
        path.write_text('{broken')
        self.assert_conflict_without_writes()

    def test_symlink_graph_or_ancestor_rejected(self):
        real = self.root / 'real'
        real.mkdir()
        self.graph.symlink_to(real, target_is_directory=True)
        report = self.sync()
        self.assertEqual('conflict', report['status'])
        self.assertFalse(list(real.iterdir()))
        self.graph.unlink()
        parent = self.root / 'link'
        parent.symlink_to(real, target_is_directory=True)
        report = synchronize(self.source, parent / 'child')
        self.assertEqual('conflict', report['status'])
        self.assertFalse(list(real.iterdir()))

    def test_symlink_pages_rejected(self):
        self.graph.mkdir()
        real = self.root / 'real'
        real.mkdir()
        (self.graph / 'pages').symlink_to(real, target_is_directory=True)
        self.assertEqual('conflict', self.sync()['status'])
        self.assertFalse(list(real.iterdir()))

    def test_symlink_and_hardlink_page_rejected(self):
        self.sync()
        path = self.graph / 'pages/Doe2024.md'
        outside = self.root / 'outside.md'
        path.rename(outside)
        content = outside.read_bytes()
        path.symlink_to(outside)
        self.assertEqual('conflict', self.sync()['status'])
        self.assertEqual(content, outside.read_bytes())
        path.unlink()
        os.link(outside, path)
        self.assert_conflict_without_writes()
        self.assertEqual(content, outside.read_bytes())

    def test_symlink_manifest_and_directory_target_rejected(self):
        self.sync()
        path = self.graph / MANIFEST_NAME
        outside = self.root / 'outside.json'
        path.rename(outside)
        path.symlink_to(outside)
        self.assertEqual('conflict', self.sync()['status'])
        path.unlink()
        path.mkdir()
        self.assertEqual('conflict', self.sync()['status'])

    def test_fifo_target_rejected_without_blocking(self):
        (self.graph / 'pages').mkdir(parents=True)
        os.mkfifo(self.graph / 'pages/Doe2024.md')
        self.assertEqual('conflict', self.sync()['status'])

    def test_exclusive_graph_lock(self):
        self.sync()
        with (self.graph / LOCK_NAME).open('rb') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(Conflict, 'holds the graph lock'):
                self.sync()
            self.assertEqual('planned', self.sync(True)['status'])

    def test_atomic_write_failure_rolls_back_prior_pages(self):
        self.sync()
        items = copy.deepcopy(SAMPLE)
        for item in items:
            item['title'] = 'Changed'
        self.write_source(items)
        before = self.snapshot()
        real_write = atomic_write
        calls = []

        def fail_second(path, data):
            calls.append(path)
            if len(calls) == 2:
                raise OSError('injected storage failure')
            return real_write(path, data)

        with patch('notewrench.zoterologseq.sync.atomic_write', side_effect=fail_second):
            with self.assertRaisesRegex(OSError, 'injected storage failure'):
                self.sync()
        self.assertEqual(before, self.snapshot())
        self.assertFalse(list(self.graph.rglob('.zotlog-tmp-*')))

    def test_new_pages_rolled_back_on_manifest_failure(self):
        real_write = atomic_write

        def fail_manifest(path, data):
            if path.name == MANIFEST_NAME:
                raise OSError('manifest storage failure')
            return real_write(path, data)

        with patch('notewrench.zoterologseq.sync.atomic_write', side_effect=fail_manifest):
            with self.assertRaises(OSError):
                self.sync()
        self.assertFalse(list((self.graph / 'pages').iterdir()))
        self.assertFalse((self.graph / MANIFEST_NAME).exists())
        self.assertEqual(2, len(self.sync()['created']))

    def test_post_replace_failure_rolled_back(self):
        self.sync()
        before = self.snapshot()
        self.write_source([dict(SAMPLE[0], title='Changed'), SAMPLE[1]])
        real_write = atomic_write
        count = 0

        def fail_after_replace(path, data):
            nonlocal count
            count += 1
            real_write(path, data)
            if count == 1:
                raise OSError('injected directory fsync failure')

        with patch('notewrench.zoterologseq.sync.atomic_write', side_effect=fail_after_replace):
            with self.assertRaises(OSError):
                self.sync()
        self.assertEqual(before, self.snapshot())

    def test_snapshot_change_prevents_write(self):
        self.sync()
        items = load_items(self.source.read_bytes())
        plan = Plan(self.graph, items)
        path = self.graph / 'pages/Doe2024.md'
        changed = path.read_bytes() + b'\n- Concurrent note\n'
        path.write_bytes(changed)
        with self.assertRaisesRegex(Conflict, 'changed after preflight'):
            apply(plan)
        self.assertEqual(changed, path.read_bytes())

    def test_permissions_preserved(self):
        self.sync()
        path = self.graph / 'pages/Doe2024.md'
        path.chmod(0o640)
        self.write_source([dict(SAMPLE[0], title='Changed'), SAMPLE[1]])
        self.sync()
        self.assertEqual(0o640, path.stat().st_mode & 0o777)

    def cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'notewrench', 'zotero-logseq', *map(str, args)],
                              cwd=ROOT, capture_output=True, text=True, timeout=60)

    def test_cli_success_dry_run_and_conflict_exit_codes(self):
        result = self.cli(self.source, '--graph', self.graph, '--dry-run')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('planned', json.loads(result.stdout)['status'])
        self.assertFalse(self.graph.exists())
        result = self.cli(self.source, '--graph', self.graph)
        self.assertEqual(0, result.returncode, result.stderr)
        (self.graph / 'pages/Doe2024.md').write_text('human only')
        result = self.cli(self.source, '--graph', self.graph)
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertTrue(json.loads(result.stdout)['conflicts'])
        self.assertNotIn('Traceback', result.stderr)

    def test_cli_invalid_io_and_argument_errors(self):
        result = self.cli(self.root / 'missing.json', '--graph', self.graph)
        self.assertEqual(3, result.returncode)
        self.assertEqual('error', json.loads(result.stdout)['status'])
        self.source.write_text('{')
        result = self.cli(self.source, '--graph', self.graph)
        self.assertEqual(2, result.returncode)
        self.assertEqual('error', json.loads(result.stdout)['status'])
        result = self.cli(self.source)
        self.assertEqual(2, result.returncode)
        self.assertIn('--graph', result.stderr)

    def test_help_and_version(self):
        self.assertEqual(0, self.cli('--help').returncode)
        result = self.cli('--version')
        self.assertEqual(0, result.returncode)
        from notewrench import __version__
        self.assertEqual(f'notewrench zotero-logseq {__version__}', result.stdout.strip())


if __name__ == '__main__':
    unittest.main()
