# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic JEX integration cases for organization pages and shared identities."""

import io
import json
import subprocess
import sys
import tarfile
import unicodedata

from .test_conversion import FixtureTests
from .synthetic import (CHILD, FOLDER, NOTE_A, NOTE_B, NOTE_C,
    RELATION, TAG, record, write_jex)
from notewrench.joplinlogseq import ConversionError, convert

UNUSED = '7' * 32


def organization_files():
    return {
        FOLDER + '.md': record(FOLDER, 2, title='Archive'),
        CHILD + '.md': record(CHILD, 2, title='Empty', parent_id=FOLDER),
        NOTE_A + '.md': record(NOTE_A, 1, title='Entry', parent_id=FOLDER,
            body=f'[empty](:/{CHILD}) [tag](:/{UNUSED}) [peer](:/{NOTE_B})'),
        NOTE_B + '.md': record(NOTE_B, 1, title='Peer', parent_id=FOLDER),
        TAG + '.md': record(TAG, 5, title='Used'),
        UNUSED + '.md': record(UNUSED, 5, title='Unused'),
        RELATION + '.md': record(RELATION, 6, note_id=NOTE_A, tag_id=TAG),
    }


class OrganizationTests(FixtureTests):
    def text(self, entry):
        return (self.output / entry['path']).read_text(encoding='utf-8')

    def test_organization_default_has_only_notes_and_explicit_unemitted_maps(self):
        report = self.run_conversion(organization_files())
        self.assertEqual(len(list((self.output / 'pages').iterdir())), 2)
        self.assertIsNone(report['folders'][CHILD]['path'])
        self.assertIsNone(report['tags'][UNUSED]['path'])
        self.assertEqual(report['folders'][CHILD]['parent_id'], FOLDER)
        self.assertNotIn('joplin-notebook::', self.text(report['pages'][NOTE_A]))
        self.assertEqual(report['output_pages'], {'note': 2, 'folder': 0, 'tag': 0})

    def test_organization_opt_in_preserves_empty_entities_and_membership_links(self):
        report = self.run_conversion(organization_files(), include_organization_pages=True)
        self.assertEqual(report['output_pages'], {'note': 2, 'folder': 2, 'tag': 2})
        self.assertEqual(len(list((self.output / 'pages').iterdir())), 6)
        folder, child = report['folders'][FOLDER], report['folders'][CHILD]
        note = report['pages'][NOTE_A]
        self.assertEqual(folder['namespace'], 'Archive')
        self.assertEqual(folder['child_ids'], [CHILD])
        self.assertEqual(folder['note_ids'], [NOTE_A, NOTE_B])
        self.assertEqual(report['tags'][TAG]['note_ids'], [NOTE_A])
        for kind, entries in [('notebook', report['folders']), ('tag', report['tags'])]:
            for item_id, entry in entries.items():
                text = self.text(entry)
                self.assertIn(f'joplin-id:: {item_id}', text)
                self.assertIn(f'joplin-type:: {kind}', text)
                self.assertIn('title:: ' + entry['page'], text)
        self.assertIn('joplin-parent:: [[' + folder['page'] + ']]', self.text(child))
        self.assertIn('joplin-notebook:: [[' + folder['page'] + ']]', self.text(note))
        self.assertIn('  - [[' + child['page'] + ']]', self.text(folder))
        self.assertIn('  - [[' + note['page'] + ']]', self.text(folder))
        self.assertIn('  - [[' + note['page'] + ']]', self.text(report['tags'][TAG]))
        self.assertIn('[empty]([[' + child['page'] + ']])', self.text(note))
        self.assertIn('[tag]([[' + report['tags'][UNUSED]['page'] + ']])', self.text(note))
        self.assertEqual(report['issues'], [])
        self.assertEqual(json.loads((self.output / 'report.json').read_text()), report)

    def test_organization_shared_unicode_case_and_suffix_collision_names(self):
        files = {
            NOTE_A + '.md': record(NOTE_A, 1, title='Café', body=f'[peer](:/{NOTE_B})'),
            NOTE_B + '.md': record(NOTE_B, 1, title='café'),
            NOTE_C + '.md': record(NOTE_C, 1, title='Café--' + NOTE_A),
            FOLDER + '.md': record(FOLDER, 2, title='CAFÉ'),
            CHILD + '.md': record(CHILD, 2, title='Empty', parent_id=FOLDER),
            TAG + '.md': record(TAG, 5, title='Cafe\u0301'),
            RELATION + '.md': record(RELATION, 6, note_id=NOTE_A, tag_id=TAG),
        }
        default = self.run_conversion(files)
        # An implicit tag must not alias a note even without extra output files.
        self.assertEqual(default['tags'][TAG]['page'], 'Café--' + TAG)
        self.assertIn('tags:: [[Café--' + TAG + ']]', self.text(default['pages'][NOTE_A]))
        self.assertEqual(default['pages'][NOTE_A]['page'], 'Café--' + NOTE_A + '--' + NOTE_A)
        output = self.root / 'organized'
        first = convert(self.source, output, include_organization_pages=True)
        for kind in ('pages', 'folders', 'tags'):
            for item_id, entry in first[kind].items():
                self.assertEqual(default[kind][item_id]['page'], entry['page'])
        entries = [entry for kind in ('pages', 'folders', 'tags') for entry in first[kind].values()]
        keys = [unicodedata.normalize('NFC', e['path'].casefold()) for e in entries]
        self.assertEqual(len(set(keys)), len(entries))
        note = (output / first['pages'][NOTE_A]['path']).read_text()
        self.assertIn('[peer]([[café--' + NOTE_B + ']])', note)
        self.assertIn('joplin-parent:: [[CAFÉ--' + FOLDER + ']]',
            (output / first['folders'][CHILD]['path']).read_text())
        # Reverse actual tar order, not just a dictionary that the serializer sorts.
        with tarfile.open(self.source, 'w', format=tarfile.USTAR_FORMAT) as archive:
            for name, data in reversed(list(files.items())):
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        again = self.root / 'again'
        second = convert(self.source, again, include_organization_pages=True)
        self.assertEqual(first, second)
        for path in output.rglob('*'):
            if path.is_file():
                self.assertEqual(path.read_bytes(), (again / path.relative_to(output)).read_bytes())

    def test_organization_shared_encoded_and_reserved_filename_collisions(self):
        files = {
            FOLDER + '.md': record(FOLDER, 2, title='CON'),
            TAG + '.md': record(TAG, 5, title='_CON'),
            CHILD + '.md': record(CHILD, 2, title='a_'),
            UNUSED + '.md': record(UNUSED, 2, title='a'),
            NOTE_A + '.md': record(NOTE_A, 1, title='b', parent_id=CHILD),
            NOTE_B + '.md': record(NOTE_B, 2, title='_b', parent_id=UNUSED),
        }
        report = self.run_conversion(files, include_organization_pages=True)
        entries = [e for kind in ('pages', 'folders', 'tags') for e in report[kind].values()]
        self.assertEqual(len({e['path'].casefold() for e in entries}), len(entries))
        for kind, item_id in [('folders', FOLDER), ('tags', TAG), ('pages', NOTE_A), ('folders', NOTE_B)]:
            self.assertIn('--' + item_id, report[kind][item_id]['page'])
            self.assertTrue((self.output / report[kind][item_id]['path']).is_file())

    def test_organization_only_archive_and_emitted_filename_limit(self):
        files = {FOLDER + '.md': record(FOLDER, 2, title='本' * 60),
                 CHILD + '.md': record(CHILD, 2, title='本' * 60, parent_id=FOLDER),
                 UNUSED + '.md': record(UNUSED, 5, title='Unused')}
        report = self.run_conversion(files, dry_run=True)
        self.assertEqual(report['counts']['note'], 0)
        self.assertFalse(self.output.exists())
        with self.assertRaisesRegex(ConversionError, '240 UTF-8 bytes'):
            convert(self.source, self.output, include_organization_pages=True)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.jex2logseq-*')))
        files[CHILD + '.md'] = record(CHILD, 2, title='Empty', parent_id=FOLDER)
        report = self.run_conversion(files, include_organization_pages=True)
        self.assertEqual(report['output_pages'], {'note': 0, 'folder': 2, 'tag': 1})

    def test_organization_cli_flag_dry_run_and_output_conflict(self):
        write_jex(self.source, organization_files())
        input_before = self.source.read_bytes()
        args = [sys.executable, '-m', 'notewrench', 'joplin-logseq', str(self.source), str(self.output), '--include-organization-pages']
        preview = subprocess.run([*args, '--dry-run'], cwd=self.root, text=True, capture_output=True, timeout=60)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertFalse(self.output.exists())
        run = subprocess.run(args, cwd=self.root, text=True, capture_output=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(self.source.read_bytes(), input_before)
        report_bytes = (self.output / 'report.json').read_bytes()
        report = json.loads(report_bytes)
        self.assertEqual(report, json.loads(preview.stdout))
        self.assertTrue(report['options']['include_organization_pages'])
        self.assertEqual(len(list((self.output / 'pages').iterdir())), 6)
        self.assertIn('joplin-parent:: [[Archive]]', self.text(report['folders'][CHILD]))
        conflict = subprocess.run(args, cwd=self.root, text=True, capture_output=True, timeout=60)
        self.assertEqual(conflict.returncode, 2)
        self.assertIn('already exists', conflict.stderr)
        self.assertEqual((self.output / 'report.json').read_bytes(), report_bytes)
