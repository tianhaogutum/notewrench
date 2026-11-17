# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 tianhaogutum
"""Contracts for the read-only YAML subset; no general YAML parser is promised."""
import json
from pathlib import Path
import tempfile
import unittest

from notewrench.zoterologseq import synchronize
from notewrench.zoterologseq.graph import claims
from notewrench.zoterologseq.model import MANIFEST_NAME, load_items
from notewrench.zoterologseq.sync import Conflict, Plan, apply


class YamlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.graph = self.root / 'graph'
        self.source = self.root / 'library.json'
        self.write_source([{'id': 'Existing', 'type': 'book', 'title': 'Original'}])
        self.assertEqual('applied', synchronize(self.source, self.graph)['status'])
        self.page = self.graph / 'pages/Reading.md'

    def write_source(self, items):
        self.source.write_text(json.dumps(items), encoding='utf-8')

    def pending(self):
        self.write_source([{'id': 'Existing', 'type': 'book', 'title': 'Pending'},
                           {'id': 'New2026', 'type': 'book'}])

    def snapshot(self):
        return {p.relative_to(self.graph).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.graph.rglob('*') if p.is_file()}

    def test_yaml_supported_scalar_and_list_contract(self):
        cases = [
            ('alias: New2026', [('New2026', 'alias', 2)]),
            ('ALIAS: "New2026"', [('New2026', 'alias', 2)]),
            ("title: 'Ｎｅｗ２０２６'", [('Ｎｅｗ２０２６', 'title', 2)]),
            ('aliases: [Other, "New2026"]', [('Other', 'aliases', 2), ('New2026', 'aliases', 2)]),
            ("alias:\n  - 'Other'\n  # comment\n\n  - New2026",
             [('Other', 'alias', 3), ('New2026', 'alias', 6)]),
            ('alias: []\ntitle: "2026 reading"', [('2026 reading', 'title', 3)]),
            ('title: "true"\ncategory: [Reading, Local]', [('true', 'title', 2)]),
            ('category:\n  - New2026', []),
            ('# only a comment', []),
            ('', []),
        ]
        for fields, expected in cases:
            with self.subTest(fields=fields):
                found = claims('pages/Reading.md', f'---\n{fields}\n---\n- Notes'.encode())
                self.assertEqual(expected, [(c.value, c.property, c.line)
                                            for c in found if c.property != 'filename'])

    def test_yaml_collisions_block_entire_batch_with_exact_sources(self):
        self.pending()
        for fields, prop, line, value in [
            ('alias: New2026', 'alias', 2, 'New2026'),
            ('aliases: [Other, "New2026"]', 'aliases', 2, 'New2026'),
            ("aliases:\n  - Other\n  - 'New2026'", 'aliases', 4, 'New2026'),
            ('title: "Ｎｅｗ２０２６"', 'title', 2, 'Ｎｅｗ２０２６'),
        ]:
            with self.subTest(fields=fields):
                self.page.write_bytes(f'---\n{fields}\n---\n- Human notes'.encode())
                before = self.snapshot()
                for dry in (True, False):
                    report = synchronize(self.source, self.graph, dry)
                    self.assertEqual('conflict', report['status'], report)
                    collision = next(c for c in report['conflicts']
                                     if c['code'] == 'graph-identity-collision')
                    self.assertEqual('New2026', collision['id'])
                    self.assertEqual({'path': 'pages/Reading.md', 'property': prop,
                                      'line': line, 'value': value}, collision['sources'][1])
                    self.assertFalse(any(c['code'] == 'graph-identity-coverage'
                                         for c in report['conflicts']), report)
                    self.assertEqual(before, self.snapshot())
                    self.assertFalse((self.graph / 'pages/New2026.md').exists())

    def test_yaml_inline_comments_supported_contract(self):
        cases = [
            ('alias: New2026 # reading', [('New2026', 'alias', 2)]),
            ('alias: "New2026"  # reading', [('New2026', 'alias', 2)]),
            ("title: 'New2026' # ignored: [\" ' #", [('New2026', 'title', 2)]),
            ('aliases: [Other, "New2026"] # reading',
             [('Other', 'aliases', 2), ('New2026', 'aliases', 2)]),
            ('alias: [] # no names', []),
            ('alias: # list follows\n  - Other # local\n  # full line\n  - New2026 # reading',
             [('Other', 'alias', 3), ('New2026', 'alias', 5)]),
            ("aliases:\n  - 'New2026' # reading", [('New2026', 'aliases', 3)]),
            ('category: Reading # ignored\nother: [Local, Notes] # ignored', []),
            ('category: # ignored list\n  - Other # ignored', []),
        ]
        for fields, expected in cases:
            with self.subTest(fields=fields):
                found = claims('pages/Reading.md', f'---\n{fields}\n---\n- Notes'.encode())
                self.assertEqual(expected, [(c.value, c.property, c.line)
                                            for c in found if c.property != 'filename'])

    def test_yaml_inline_comment_collisions_block_batch_with_original_lines(self):
        self.pending()
        for fields, prop, line in [
            ('alias: New2026 # reading', 'alias', 3),
            ('alias: "New2026" # reading', 'alias', 3),
            ('alias: [Other, New2026] # reading', 'alias', 3),
            ('title: \'New2026\' # reading', 'title', 3),
            ('aliases: # reading\n  - Other # local\n  - "New2026" # reading', 'aliases', 5),
        ]:
            with self.subTest(fields=fields):
                self.page.write_bytes(('\ufeff\r\n---\r\n' + fields.replace('\n', '\r\n')
                                       + '\r\n---\r\n- 人工笔记 (no newline)').encode())
                before = self.snapshot()
                for dry in (True, False):
                    report = synchronize(self.source, self.graph, dry)
                    self.assertEqual(['graph-identity-collision'],
                                     [c['code'] for c in report['conflicts']], report)
                    self.assertEqual('conflict', report['status'])
                    self.assertEqual('New2026', report['conflicts'][0]['id'])
                    self.assertEqual({'path': 'pages/Reading.md', 'property': prop,
                                      'line': line, 'value': 'New2026'},
                                     report['conflicts'][0]['sources'][1])
                    self.assertEqual(before, self.snapshot())
                    self.assertFalse((self.graph / 'pages/New2026.md').exists())

    def test_yaml_comments_do_not_hide_unsupported_values(self):
        self.pending()
        cases = [
            'alias: New2026#reading', 'alias: New2026#reading # outside',
            'alias: "New2026"#reading', 'alias: [Other, New2026]#reading',
            'alias: New2026\u00a0# reading',
            'alias: "New2026 # reading"', "alias: 'New2026 # reading' # outside",
            'alias: [Other, "New2026 # reading"] # outside',
            'alias: "New2026 # unclosed', "alias: 'New2026 # unclosed",
            'alias: "New2026\' # mismatched', 'alias: "New2026" junk # outside',
            "alias: New'2026 # embedded quote", "alias: 'New''2026' # escape",
            'alias: "New\\\"2026" # escape',
            'alias: [Other, [New2026]] # nested', 'metadata: {alias: New2026} # map',
            'alias: [Other, New2026 # missing bracket]', 'alias: [Other,,] # empty item',
            'alias: # empty list', 'alias: # empty item\n  - # comment',
            'alias: # quoted hash\n  - "New2026 # reading" # outside',
            'alias:\n  - New2026#reading', 'alias:\n  - "New2026 # unclosed',
            'alias: null # typed', 'title: # empty', 'title: [] # list',
            'alias: | # multiline\n  New2026', 'alias: &ref New2026 # anchor',
            'alias: *ref # alias', 'title: !!str New2026 # tag',
            'alias: Other # first\nALIAS: New2026 # duplicate',
            'alias: Other # first\naliases: New2026 # ambiguous',
            'category: Safe\n  alias: New2026 # nested',
            'alias: New2026 # tab\tstill unsupported',
        ]
        for fields in cases:
            with self.subTest(fields=fields):
                self.page.write_bytes(('\ufeff\r\n---\r\n' + fields.replace('\n', '\r\n')
                                       + '\r\n---\r\n- Notes').encode())
                before = self.snapshot()
                # Empty lists report their header; otherwise the offending item/key.
                line = 4 if '\n' in fields and not fields.startswith('alias: |') else 3
                for dry in (True, False):
                    report = synchronize(self.source, self.graph, dry)
                    self.assertEqual('conflict', report['status'], report)
                    self.assertEqual(['graph-identity-coverage'],
                                     [c['code'] for c in report['conflicts']], report)
                    coverage = report['conflicts'][0]
                    self.assertEqual('pages/Reading.md', coverage['path'])
                    self.assertIn(f'line {line}', coverage['message'])
                    self.assertEqual(before, self.snapshot())

    def test_yaml_trailing_flow_comma_supported_contract(self):
        for fields, expected in [
            ('alias: [Other,]', [('Other', 'alias', 2)]),
            ('alias: [ Other ,  ] # reading', [('Other', 'alias', 2)]),
            ('aliases: [Other, "New2026",] # reading',
             [('Other', 'aliases', 2), ('New2026', 'aliases', 2)]),
            ("ALIAS: ['Other', 'New2026' , ] # quotes: \" [ ' ,",
             [('Other', 'alias', 2), ('New2026', 'alias', 2)]),
            ('alias: ["true", "2026",] # literal strings',
             [('true', 'alias', 2), ('2026', 'alias', 2)]),
            ('alias: [Topic/Sub,]', [('Topic', 'alias', 2), ('Topic/Sub', 'alias', 2)]),
            ('category: [Other, New2026,] # metadata only', []),
            ('alias: [] # unchanged\ncategory: [ ]', []),
        ]:
            with self.subTest(fields=fields):
                found = claims('pages/Reading.md', f'---\n{fields}\n---\n- Notes'.encode())
                self.assertEqual(expected, [(c.value, c.property, c.line)
                                            for c in found if c.property != 'filename'])

    def test_yaml_trailing_flow_comma_collision_preserves_batch(self):
        self.pending()
        for fields, prop, prefix, line in [
            ('alias: [Other, New2026,] # reading', 'alias', '', 2),
            ('aliases: [Other, "New2026", ] # reading', 'aliases', '\ufeff\r\n', 3),
            ("ALIAS: ['Other', 'New2026' , ] # arbitrary: \" [ '", 'alias', '', 2),
        ]:
            with self.subTest(fields=fields):
                self.page.write_bytes((prefix + '---\r\n' + fields + '\r\n---\r\n'
                                       '- 人工笔记 (no newline)').encode())
                before = self.snapshot()
                for dry in (True, False):
                    report = synchronize(self.source, self.graph, dry)
                    self.assertEqual('conflict', report['status'], report)
                    self.assertEqual(['graph-identity-collision'],
                                     [c['code'] for c in report['conflicts']], report)
                    self.assertEqual('New2026', report['conflicts'][0]['id'])
                    self.assertEqual({'path': 'pages/Reading.md', 'property': prop,
                                      'line': line, 'value': 'New2026'},
                                     report['conflicts'][0]['sources'][1])
                    self.assertEqual(before, self.snapshot())
                    self.assertFalse((self.graph / 'pages/New2026.md').exists())

    def test_yaml_trailing_flow_comma_allows_unrelated_update(self):
        for fields in ('alias: [Other,] # reading',
                       'ALIASES: [Other, "Local reading" , ] # reading\r\n'
                       'category: [Local, Notes, ] # metadata'):
            with self.subTest(fields=fields):
                original = ('\ufeff\r\n---\r\n' + fields + '\r\n---\r\n'
                            '- 人工笔记 (no newline)').encode()
                self.page.write_bytes(original)
                preserved = (original, self.page.stat().st_mtime_ns)
                self.write_source([{'id': 'Existing', 'type': 'book', 'title': fields}])
                before = self.snapshot()
                report = synchronize(self.source, self.graph, True)
                self.assertEqual('planned', report['status'], report)
                self.assertEqual(['pages/Existing.md'], report['updated'])
                self.assertEqual(before, self.snapshot())
                report = synchronize(self.source, self.graph)
                self.assertEqual('applied', report['status'], report)
                self.assertEqual(['pages/Existing.md'], report['updated'])
                self.assertEqual(preserved, (self.page.read_bytes(), self.page.stat().st_mtime_ns))

    def test_yaml_trailing_flow_comma_does_not_allow_empty_or_unsupported_items(self):
        self.pending()
        cases = [
            'alias: [,Other]', 'alias: [ , Other, ] # leading',
            'alias: [Other,,New2026]', 'alias: [Other, , New2026,] # interior',
            'alias: [Other,,]', 'alias: [Other, , ] # repeated trailing',
            'alias: [,]', 'alias: [ , ]', 'alias: [,,]',
            'alias: [Other, [New2026],]', 'alias: [{name: New2026},]',
            'alias: [Other, "New2026,]', "alias: ['New2026\",] # mismatched",
            'alias: ["New2026" junk,]', 'alias: ["New,2026",]',
            "alias: ['New''2026',]", 'alias: ["New\\u00322026",]',
            'alias: [Other, New2026, # missing bracket]',
            'alias: [Other,]#attached', 'alias: [Other,]\u00a0# not a boundary',
            'alias: ["New2026 # quoted",] # outside', 'alias: [New2026#attached,]',
            'alias: [null,]', 'alias: [2026,]', 'alias: [&ref New2026,]',
            'alias: [*ref,]', 'alias: [!!str New2026,]',
            'title: [Other,]', 'title: [Other, New2026,]',
            'category: [Other,,]', 'category: [Other, {alias: New2026},]',
            'alias: [Other,] # tab\t', 'alias:\n  - Other, # block item',
            'alias: Other, # scalar', 'alias: [Other,\n  New2026,]',
        ]
        for fields in cases:
            with self.subTest(fields=fields):
                self.page.write_bytes(('\ufeff\r\n---\r\n' + fields.replace('\n', '\r\n')
                                       + '\r\n---\r\n- Notes').encode())
                before = self.snapshot()
                line = 4 if fields.startswith('alias:\n') else 3
                for dry in (True, False):
                    report = synchronize(self.source, self.graph, dry)
                    self.assertEqual('conflict', report['status'], report)
                    self.assertEqual(['graph-identity-coverage'],
                                     [c['code'] for c in report['conflicts']], report)
                    self.assertEqual('pages/Reading.md', report['conflicts'][0]['path'])
                    self.assertIn(f'line {line} (YAML)', report['conflicts'][0]['message'])
                    self.assertEqual(before, self.snapshot())

    def test_yaml_noncolliding_document_preserves_all_bytes_and_mtime(self):
        original = ('\ufeff\r\n---\r\n# comment\r\nALIASES: # block list\r\n  - Other # local\r\n'
                    '  - "Local reading" # notes\r\ntitle: Reading list # title\r\n'
                    'category: [Local, "Notes"] # metadata\r\n---\r\n'
                    '- Notes\r\n  alias:: New2026\r\n- 我的笔记 (no newline)').encode()
        self.page.write_bytes(original)
        mtime = self.page.stat().st_mtime_ns
        existing = self.graph / 'pages/Existing.md'
        note = '\r\n- 人工笔记 (no newline)'.encode()
        existing.write_bytes(existing.read_bytes() + note)
        self.pending()
        before = self.snapshot()
        self.assertEqual('planned', synchronize(self.source, self.graph, True)['status'])
        self.assertEqual(before, self.snapshot())
        report = synchronize(self.source, self.graph)
        self.assertEqual('applied', report['status'], report)
        self.assertEqual(['pages/Existing.md'], report['updated'])
        self.assertEqual(original, self.page.read_bytes())
        self.assertEqual(mtime, self.page.stat().st_mtime_ns)
        self.assertTrue(existing.read_bytes().endswith(note))
        self.assertIn(b'bibliography-title:: Pending', existing.read_bytes())

    def test_yaml_namespace_parents_reserved_without_leaf_inference(self):
        self.page.write_text('---\nalias: Topic/Sub/Child\n---\n- Notes')
        self.write_source([{'id': 'Topic', 'type': 'book'}])
        report = synchronize(self.source, self.graph)
        collision = next(c for c in report['conflicts'] if c['code'] == 'graph-identity-collision')
        self.assertEqual('Topic/Sub/Child', collision['sources'][1]['value'])
        self.write_source([{'id': 'Child', 'type': 'book'}])
        self.assertEqual('applied', synchronize(self.source, self.graph)['status'])

    def test_yaml_journal_stale_collision_and_bom_line_number(self):
        journal = self.graph / 'journals/2026_11_11.MARKDOWN'
        journal.parent.mkdir()
        journal.write_bytes('\ufeff\r\n---\r\nalias: existing\r\n---'.encode())
        self.write_source([])
        before = self.snapshot()
        report = synchronize(self.source, self.graph)
        collision = next(c for c in report['conflicts'] if c['code'] == 'graph-identity-collision')
        self.assertEqual({'path': 'journals/2026_11_11.MARKDOWN', 'property': 'alias',
                          'line': 3, 'value': 'existing'}, collision['sources'][1])
        self.assertEqual(before, self.snapshot())

    def test_yaml_unsupported_syntax_is_explicit_coverage_and_zero_writes(self):
        self.pending()
        cases = [
            'alias: New2026',  # no closing delimiter
            'alias: "New2026',
            'alias: [New2026,,]', 'alias: [New2026,, Other]',
            'alias: [[New2026]]', 'alias: {name: New2026}',
            'alias: [Other, [New2026]]', 'alias: [Other, {name: New2026}]',
            'alias: |\n  New2026', 'title: >\n  New2026',
            'alias: &identity New2026', 'alias: *identity', 'title: !!str New2026',
            'alias: New2026\nALIAS: Other', 'alias: Other\naliases: New2026',
            'title: [New2026]', 'title:\n  - New2026', 'alias:', 'alias: null',
            'alias: true', 'alias: YES', 'title: 2026', 'title: 2026-11-11',
            'alias: "New\\u00322026"', "title: 'New''2026'", 'alias: "New, Other"',
            'alias: New2026, Other', 'alias:\n - New2026', 'alias:\n\t- New2026',
            'alias:\n  - Other\n    - New2026', 'alias:\n  -', '"alias": New2026',
            '? alias\n: New2026', '<<: *identity',
            'metadata:\n  alias: New2026', 'metadata: {alias: New2026}',
            'metadata: Safe\nmetadata: Other', 'alias: New2026\n...',
        ]
        for index, fields in enumerate(cases):
            with self.subTest(fields=fields):
                ending = '' if index == 0 else '\n---'
                self.page.write_text(f'---\n{fields}{ending}\n')
                before = self.snapshot()
                for dry in (True, False):
                    report = synchronize(self.source, self.graph, dry)
                    self.assertEqual('conflict', report['status'], report)
                    coverage = next(c for c in report['conflicts']
                                    if c['code'] == 'graph-identity-coverage')
                    self.assertEqual('pages/Reading.md', coverage['path'])
                    self.assertIn('line ', coverage['message'])
                    self.assertEqual(before, self.snapshot())

    def test_yaml_mixed_headers_and_org_remain_unsupported(self):
        for name, text in [
            ('Reading.md', 'alias:: Other\n---\nalias: New2026\n---'),
            ('Reading.md', '---\nalias: Other\n---\n\ntitle:: New2026'),
            ('Reading.md', ' ---\nalias: New2026\n---'),
            ('Reading.md', '--- # comment\nalias: New2026\n---'),
            ('Reading.md', '%YAML 1.2\n---\nalias: New2026\n---'),
            ('Reading.md', '%TAG ! tag:example.org,2026:\n---\nalias: New2026\n---'),
            ('Reading.org', '---\nalias: New2026\n---'),
        ]:
            with self.subTest(name=name, text=text):
                path = self.graph / 'pages' / name
                path.write_text(text)
                report = synchronize(self.source, self.graph)
                self.assertTrue(any(c['code'] == 'graph-identity-coverage'
                                    for c in report['conflicts']), report)
                path.unlink()

    def test_yaml_does_not_relax_tracked_provenance(self):
        path = self.graph / 'pages/Existing.md'
        path.write_bytes(b'---\nalias: Other\n---\n' + path.read_bytes())
        before = self.snapshot()
        report = synchronize(self.source, self.graph)
        self.assertTrue(any(c['code'] == 'tracked-page' for c in report['conflicts']))
        self.assertEqual(before, self.snapshot())

    def test_yaml_edit_after_planning_is_detected(self):
        self.page.write_text('---\nalias: Other\n---\n- Notes')
        self.pending()
        plan = Plan(self.graph, load_items(self.source.read_bytes()))
        self.assertFalse(plan.report['conflicts'], plan.report)
        self.page.write_text('---\nalias: New2026\n---\n- A new note')
        before = self.snapshot()
        with self.assertRaisesRegex(Conflict, 'changed after preflight'):
            apply(plan)
        self.assertEqual(before, self.snapshot())
        self.assertIn(MANIFEST_NAME, before)
