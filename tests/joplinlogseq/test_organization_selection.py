# SPDX-License-Identifier: AGPL-3.0-only
"""Independent notebook/tag emission contracts; all identities still reserve names."""

import json
from urllib.parse import quote

from .test_conversion import FixtureTests
from .test_organization import UNUSED, organization_files
from .synthetic import (CHILD, FOLDER, MISSING, NOTE_A, NOTE_B,
    NOTE_C, RELATION, TAG, record, write_jex)
from notewrench.joplinlogseq import ConversionError, convert


MODES = (
    ({}, False, False),
    ({'include_notebook_pages': True}, True, False),
    ({'include_tag_pages': True}, False, True),
    ({'include_notebook_pages': True, 'include_tag_pages': True}, True, True),
    ({'include_organization_pages': True}, True, True),
    ({'include_organization_pages': True, 'include_notebook_pages': True,
      'include_tag_pages': True}, True, True),
)


class OrganizationSelectionTests(FixtureTests):
    def test_selection_paths_memberships_source_uris_and_shared_names(self):
        files = organization_files()
        body = (f'[empty](:/{CHILD}) [tag](:/{UNUSED}) [peer](:/{NOTE_B})\n'
                f'[notebook]: <:/{CHILD}#part> "notebook title"\n'
                f'[tag]: :/{UNUSED}\n'
                f':/{FOLDER}\n'
                f'[missing](:/{MISSING})\n'
                f'`[literal](:/{CHILD}) :/{UNUSED}`')
        files[NOTE_A + '.md'] = record(NOTE_A, 1, title='Entry', parent_id=FOLDER, body=body)
        # Case collisions span all three entity types, plus a suffix-looking title.
        files[NOTE_B + '.md'] = record(NOTE_B, 1, title='archive')
        files[UNUSED + '.md'] = record(UNUSED, 5, title='ARCHIVE')
        files[NOTE_C + '.md'] = record(NOTE_C, 1, title='Archive--' + FOLDER)
        write_jex(self.source, files)
        baseline = None
        for index, (options, notebooks, tags) in enumerate(MODES):
            with self.subTest(options=options):
                output = self.root / str(index)
                preview = convert(self.source, output, dry_run=True, **options)
                self.assertFalse(output.exists())
                report = convert(self.source, output, **options)
                self.assertEqual(report, preview)
                self.assertEqual(report, json.loads((output / 'report.json').read_text()))
                self.assertEqual(report['options'], {
                    'include_organization_pages': options.get('include_organization_pages', False),
                    'include_notebook_pages': notebooks, 'include_tag_pages': tags})
                self.assertEqual(report['output_pages'], {
                    'note': 3, 'folder': 2 if notebooks else 0, 'tag': 2 if tags else 0})
                identities = {kind: {i: {k: v for k, v in entry.items() if k != 'path'}
                                    for i, entry in report[kind].items()}
                              for kind in ('pages', 'folders', 'tags')}
                if baseline is None:
                    baseline = identities
                self.assertEqual(identities, baseline)
                folder, child = report['folders'][FOLDER], report['folders'][CHILD]
                unused, used = report['tags'][UNUSED], report['tags'][TAG]
                self.assertEqual(folder['page'], 'Archive--' + FOLDER + '--' + FOLDER)
                self.assertEqual(unused['page'], 'ARCHIVE--' + UNUSED)
                self.assertEqual(folder['note_ids'], [NOTE_A])
                self.assertEqual(folder['child_ids'], [CHILD])
                self.assertEqual(child['note_ids'], [])
                self.assertEqual(child['parent_id'], FOLDER)
                self.assertEqual(used['note_ids'], [NOTE_A])
                self.assertEqual(unused['note_ids'], [])
                expected_paths = set()
                for kind, emitted in (('pages', True), ('folders', notebooks), ('tags', tags)):
                    for item_id, entry in report[kind].items():
                        expected_path = 'pages/' + entry['page'].replace('/', '___') + '.md'
                        self.assertEqual(entry['path'], expected_path if emitted else None)
                        if emitted:
                            expected_paths.add(expected_path)
                            text = (output / expected_path).read_text()
                            self.assertIn('joplin-id:: ' + item_id, text)
                            if kind != 'pages':
                                self.assertIn('joplin-type:: ' + ('notebook' if kind == 'folders' else 'tag'), text)
                self.assertEqual({str(p.relative_to(output)) for p in (output / 'pages').iterdir()}, expected_paths)
                note = (output / report['pages'][NOTE_A]['path']).read_text()
                self.assertIn('tags:: [[' + used['page'] + ']]', note)
                self.assertEqual('joplin-notebook:: [[' + folder['page'] + ']]' in note, notebooks)
                issues = []
                for target, label, emitted, entry in ((CHILD, 'empty', notebooks, child),
                                                       (UNUSED, 'tag', tags, unused)):
                    self.assertIn(f'[{label}]([[{entry["page"]}]])' if emitted else f'[{label}](:/{target})', note)
                    if not emitted:
                        issues.append({'code': 'broken_link', 'note_id': NOTE_A, 'target': target, 'line': 1})
                for line, label, target, emitted, entry in (
                        (2, 'notebook', CHILD, notebooks, child), (3, 'tag', UNUSED, tags, unused)):
                    destination = quote('../' + entry['path'], safe='/') if emitted else ':/' + target
                    definition = (f'[notebook]: <{destination}#part> "notebook title"' if label == 'notebook'
                                  else '[tag]: ' + destination)
                    self.assertIn(definition, note)
                    issues.append({'code': 'note_link_uses_file_path' if emitted else 'broken_link',
                                   'note_id': NOTE_A, 'target': target, 'line': line})
                self.assertIn('  [[' + folder['page'] + ']]\n' if notebooks else '  :/' + FOLDER + '\n', note)
                if not notebooks:
                    issues.append({'code': 'broken_link', 'note_id': NOTE_A, 'target': FOLDER, 'line': 4})
                issues.append({'code': 'broken_link', 'note_id': NOTE_A, 'target': MISSING, 'line': 5})
                self.assertEqual(report['issues'], issues)
                self.assertEqual(report['rewrites']['note_links'], 1 + 3 * notebooks + 2 * tags)
                self.assertEqual(report['source_items'][NOTE_A]['body'], body)
                self.assertIn(f'`[literal](:/{CHILD}) :/{UNUSED}`', note)
                self.assertIn(f'[missing](:/{MISSING})', note)
                if notebooks:
                    self.assertIn('joplin-parent:: [[' + folder['page'] + ']]', (output / child['path']).read_text())
                    parent_text = (output / folder['path']).read_text()
                    for member in (child['page'], report['pages'][NOTE_A]['page']):
                        self.assertIn('  - [[' + member + ']]', parent_text)
                if tags:
                    self.assertIn('  - [[' + report['pages'][NOTE_A]['page'] + ']]', (output / used['path']).read_text())
                    self.assertNotIn('- Notes', (output / unused['path']).read_text())

    def test_selection_filename_limit_checks_only_emitted_entities(self):
        files = {FOLDER + '.md': record(FOLDER, 2, title='本' * 60),
                 CHILD + '.md': record(CHILD, 2, title='本' * 60, parent_id=FOLDER),
                 UNUSED + '.md': record(UNUSED, 5, title='Unused')}
        write_jex(self.source, files)
        report = convert(self.source, self.output, include_tag_pages=True)
        self.assertEqual(report['output_pages'], {'note': 0, 'folder': 0, 'tag': 1})
        self.assertIsNone(report['folders'][CHILD]['path'])
        self.assertEqual([p.name for p in (self.output / 'pages').iterdir()], ['Unused.md'])
        for options in ({'include_notebook_pages': True},
                        {'include_notebook_pages': True, 'include_tag_pages': True}):
            for dry_run in (False, True):
                with self.subTest(options=options, dry_run=dry_run):
                    output = self.root / 'rejected'
                    with self.assertRaisesRegex(ConversionError, '240 UTF-8 bytes'):
                        convert(self.source, output, dry_run=dry_run, **options)
                    self.assertFalse(output.exists())
                    self.assertFalse(list(self.root.glob('.jex2logseq-*')))

    def test_selection_does_not_skip_unemitted_hierarchy_or_relation_validation(self):
        cases = (
            ({FOLDER + '.md': record(FOLDER, 2, title='Bad', parent_id=CHILD)}, 'Missing parent folder'),
            ({FOLDER + '.md': record(FOLDER, 2, title='Cycle', parent_id=FOLDER)}, 'Folder cycle'),
            ({RELATION + '.md': record(RELATION, 6, note_id=NOTE_A, tag_id=TAG)}, 'Dangling note_tag'),
        )
        for files, message in cases:
            write_jex(self.source, files)
            for options in ({'include_notebook_pages': True}, {'include_tag_pages': True}):
                with self.subTest(message=message, options=options):
                    with self.assertRaisesRegex(ConversionError, message):
                        convert(self.source, self.output, **options)
                    self.assertFalse(self.output.exists())
                    self.assertFalse(list(self.root.glob('.jex2logseq-*')))
