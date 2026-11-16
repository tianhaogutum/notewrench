# SPDX-License-Identifier: AGPL-3.0-only
"""Offline integration fixtures follow the API revisions pinned in README."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from notewrench.archive.cli import main

ROOT = Path(__file__).resolve().parents[2]


class APIImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='archive-api-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'examples' / 'archive' / 'api', self.root / 'pages')
        self.pages = self.root / 'pages'
        self.output = self.root / 'output'

    def edit(self, name, change):
        path = self.pages / name
        data = json.loads(path.read_text())
        change(data)
        path.write_text(json.dumps(data, ensure_ascii=False))

    def run_import(self, kind='wallabag', *extra):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(socket, 'socket', side_effect=AssertionError('no network')), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(['api-import', str(self.pages / f'{kind}-manifest.json'), '-o', str(self.output), *extra])
        return code, out.getvalue(), err.getvalue()

    def rejected(self, kind, diagnostic):
        code, out, err = self.run_import(kind)
        self.assertEqual(code, 2, (out, err))
        self.assertIn(diagnostic, err)
        self.assertNotIn('Traceback', err)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.bagkeep-*')))

    def test_two_pages_bodies_tags_flags_provenance_and_hashes(self):
        for kind in ['wallabag', 'karakeep']:
            with self.subTest(kind=kind):
                before = {p.name: p.read_bytes() for p in self.pages.iterdir()}
                code, out, err = self.run_import(kind)
                self.assertEqual(code, 0, err)
                manifest = json.loads(out)['manifest']
                self.assertTrue(manifest['pagination']['complete'])
                self.assertEqual(manifest['pagination']['page_count'], 2)
                entries = json.loads((self.output / 'wallabag.json').read_text())
                self.assertEqual(len(entries), 2)
                for i, entry in enumerate(entries, 1):
                    self.assertIn(f'{kind.capitalize()} cached body {i}', entry['content'])
                    self.assertEqual(entry['tags'], ['offline'])
                    self.assertTrue(entry['is_starred'])
                    record = manifest['records'][i-1]
                    self.assertEqual(record['source']['page_index'], i-1)
                    self.assertEqual(record['source']['item_index'], 0)
                    self.assertIn(entry['content'], (self.output / record['file']).read_text())
                provenance = json.loads((self.output / 'provenance.json').read_text())
                for page in provenance['pages']:
                    self.assertEqual(page['response'], json.loads(before[page['file']]))
                    self.assertEqual(page['sha256'], hashlib.sha256(before[page['file']]).hexdigest())
                self.assertEqual(len(provenance['mapping']), 2)
                self.assertIn('mapped_fields', provenance['mapping'][0])
                self.assertTrue(any(i['code'] == 'api_fields_not_mapped' for i in manifest['issues']))
                for name, info in manifest['files'].items():
                    self.assertEqual(hashlib.sha256((self.output/name).read_bytes()).hexdigest(), info['sha256'])
                self.assertEqual(before, {p.name:p.read_bytes() for p in self.pages.iterdir()})
                shutil.rmtree(self.output)

    def test_duplicate_pages_and_items_have_distinct_errors(self):
        for kind in ['wallabag', 'karakeep']:
            path = self.pages / f'{kind}-manifest.json'
            original = path.read_bytes()
            self.edit(path.name, lambda d: d['pages'].append(copy.deepcopy(d['pages'][0])))
            self.rejected(kind, 'duplicate_page')
            path.write_bytes(original)
            name = f'{kind}-2.json'
            def repeat_id(d):
                if kind == 'wallabag': d['_embedded']['items'][0]['id'] = 1
                else: d['bookmarks'][0]['id'] = 'bookmark-1'
            self.edit(name, repeat_id)
            self.rejected(kind, 'duplicate_item')

    def test_missing_page_and_unfinished_cursor_rejected(self):
        self.edit('wallabag-manifest.json', lambda d:d['pages'].pop())
        self.rejected('wallabag', 'incomplete_pagination')
        self.edit('karakeep-manifest.json', lambda d:d['pages'].pop())
        self.rejected('karakeep', 'incomplete_pagination')

    def test_wrong_namespace_and_wrong_account_rejected(self):
        self.edit('wallabag-manifest.json', lambda d:d['pages'][1].update(namespace='another-account'))
        self.rejected('wallabag', 'source_namespace')
        self.edit('karakeep-2.json', lambda d:d['bookmarks'][0].update(userId='bob'))
        self.rejected('karakeep', 'source_namespace')

    def test_page_identity_cursor_gap_cycle_and_orphan(self):
        self.edit('wallabag-manifest.json', lambda d:d['pages'][1].update(page=3))
        self.rejected('wallabag', 'page_identity')
        self.edit('karakeep-manifest.json', lambda d:d['pages'][1].update(cursor='wrong'))
        self.rejected('karakeep', 'incomplete_pagination')
        self.edit('karakeep-manifest.json', lambda d:d['pages'][1].update(cursor='saved-next-1'))
        self.edit('karakeep-2.json', lambda d:d.update(nextCursor='saved-next-1'))
        self.rejected('karakeep', 'cursor_cycle')
        self.edit('karakeep-1.json', lambda d:d.update(nextCursor=None))
        self.rejected('karakeep', 'incomplete_pagination')

    def test_inconsistent_wallabag_counts_and_missing_file(self):
        self.edit('wallabag-2.json', lambda d:d.update(total=3))
        self.rejected('wallabag', 'incomplete_pagination')
        (self.pages / 'karakeep-2.json').unlink()
        self.rejected('karakeep', 'karakeep-2.json')

    def test_paths_cannot_escape_manifest_directory(self):
        outside = self.root / 'outside.json'
        outside.write_text('{}')
        self.edit('wallabag-manifest.json', lambda d:d['pages'][0].update(file='../outside.json'))
        self.rejected('wallabag', 'local_page_path')
        (self.pages/'escape.json').symlink_to(outside)
        self.edit('wallabag-manifest.json', lambda d:d['pages'][0].update(file='escape.json'))
        self.rejected('wallabag', 'local_page_path')
        self.edit('wallabag-manifest.json', lambda d:d['pages'][0].update(file='https://example.org/page'))
        self.rejected('wallabag', 'local_page_path')

    def test_missing_body_loss_strict_and_dry_run_no_writes(self):
        code, out, err = self.run_import('karakeep', '--dry-run')
        self.assertEqual(code, 0, err)
        self.assertFalse(self.output.exists())
        self.edit('karakeep-1.json', lambda d:d['bookmarks'][0]['content'].pop('htmlContent'))
        code, out, err = self.run_import('karakeep', '--strict')
        self.assertEqual(code, 3, err)
        self.assertIn('missing_cached_body', out)
        self.assertFalse(self.output.exists())

    def test_non_link_and_unmapped_note_are_retained_without_invented_body(self):
        def change(data):
            data['bookmarks'][0]['content'] = {'type': 'text', 'text': 'saved text'}
        self.edit('karakeep-1.json', change)
        self.edit('karakeep-2.json', lambda d:d['bookmarks'][0].update(note='private note'))
        code, out, err = self.run_import('karakeep')
        self.assertEqual(code, 0, err)
        manifest = json.loads(out)['manifest']
        self.assertEqual((manifest['input_count'], manifest['output_count'], manifest['skipped_count']), (2, 1, 1))
        entries = json.loads((self.output/'wallabag.json').read_text())
        self.assertEqual(entries[0]['content'], '<p>Karakeep cached body 2</p>')
        provenance = json.loads((self.output/'provenance.json').read_text())
        self.assertIn('saved text', json.dumps(provenance))
        self.assertIn('private note', json.dumps(provenance))
        self.assertIsNone(provenance['mapping'][0]['output_index'])
        self.assertIn('note', provenance['mapping'][1]['provenance_only_fields'])
        self.assertEqual(manifest['records'][0]['source']['page_index'], 1)

    def test_raw_html_and_sanitizer_losses_trace_to_original_page(self):
        raw = '<p onclick="x">cache</p><script>unsafe()</script>'
        self.edit('karakeep-2.json', lambda d:d['bookmarks'][0]['content'].update(htmlContent=raw))
        code, out, err = self.run_import('karakeep')
        self.assertEqual(code, 0, err)
        manifest = json.loads(out)['manifest']
        article = (self.output/manifest['records'][1]['file']).read_text()
        self.assertNotIn('unsafe()', article)
        self.assertIn('cache', article)
        self.assertEqual(json.loads((self.output/'wallabag.json').read_text())[1]['content'], raw)
        provenance = json.loads((self.output/'provenance.json').read_text())
        self.assertEqual(provenance['pages'][1]['response']['bookmarks'][0]['content']['htmlContent'], raw)
        loss = next(i for i in provenance['issues'] if i['code'] == 'removed_active_elements')
        self.assertEqual(loss['source']['file'], 'karakeep-2.json')
        self.assertEqual(loss['source']['item_index'], 0)

    def test_aggregate_limit_duplicate_json_keys_and_bad_field_types(self):
        size = sum((self.pages/name).stat().st_size for name in ['wallabag-manifest.json', 'wallabag-1.json', 'wallabag-2.json'])
        with patch('notewrench.archive.api.MAX_INPUT_BYTES', size-1):
            self.rejected('wallabag', 'aggregate 64 MiB')
        (self.pages/'wallabag-2.json').write_text('{"page":2,"page":2}')
        self.rejected('wallabag', 'duplicate JSON key')
        self.edit('karakeep-2.json', lambda d:d['bookmarks'][0]['content'].update(htmlContent=['bad']))
        self.rejected('karakeep', 'expected HTML string')

    def test_response_metadata_links_are_inert_and_repeated_runs_deterministic(self):
        self.edit('wallabag-1.json', lambda d:d.update(_links={'next':{'href':'https://not-fetched.invalid/api/entries?page=2'}}))
        code, out, err = self.run_import()
        self.assertEqual(code, 0, err)
        first = {p.name:p.read_bytes() for p in self.output.iterdir()}
        shutil.rmtree(self.output)
        code, out, err = self.run_import()
        self.assertEqual(code, 0, err)
        self.assertEqual(first, {p.name:p.read_bytes() for p in self.output.iterdir()})
        code, out, err = self.run_import()
        self.assertEqual(code, 2)
        self.assertIn('already exists', err)
        self.assertEqual(first, {p.name:p.read_bytes() for p in self.output.iterdir()})

    def test_empty_complete_sequence_and_absent_terminal_cursor(self):
        self.edit('wallabag-manifest.json', lambda d:d['pages'].pop())
        self.edit('wallabag-1.json', lambda d:d.update(pages=1, total=0, _embedded={'items':[]}))
        code, out, err = self.run_import()
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(out)['manifest']['pagination']['complete'])
        self.assertEqual(json.loads((self.output/'wallabag.json').read_text()), [])
        shutil.rmtree(self.output)
        self.edit('karakeep-2.json', lambda d:d.pop('nextCursor'))
        self.rejected('karakeep', 'incomplete_pagination')

    def test_unordered_descriptors_preserve_input_page_locators(self):
        for kind in ['wallabag', 'karakeep']:
            self.edit(f'{kind}-manifest.json', lambda d:d['pages'].reverse())
            code, out, err = self.run_import(kind)
            self.assertEqual(code, 0, err)
            report = json.loads(out)['manifest']
            self.assertEqual([r['source']['page_index'] for r in report['records']], [1, 0])
            self.assertEqual([r['source']['file'] for r in report['records']], [f'{kind}-1.json', f'{kind}-2.json'])
            shutil.rmtree(self.output)

    def test_same_url_different_ids_retained_and_identical_item_copy_rejected(self):
        self.edit('karakeep-2.json', lambda d:d['bookmarks'][0]['content'].update(url='https://example.org/article-1'))
        code, out, err = self.run_import('karakeep')
        self.assertEqual(code, 0, err)
        report = json.loads(out)['manifest']
        self.assertEqual(report['output_count'], 2)
        self.assertIn('duplicate_url', [i['code'] for i in report['issues']])
        shutil.rmtree(self.output)
        first = json.loads((self.pages/'karakeep-1.json').read_text())['bookmarks'][0]
        self.edit('karakeep-2.json', lambda d:d.update(bookmarks=[first]))
        self.rejected('karakeep', 'duplicate_item')

    def test_wrong_manifest_and_known_field_shapes_fail_without_tracebacks(self):
        path = self.pages/'wallabag-manifest.json'
        original = path.read_bytes()
        for change in [lambda d:d.update(schema_version=True),
                       lambda d:d['source'].update(format=[]),
                       lambda d:d['pages'][0].update(page=False),
                       lambda d:d['pages'][0].pop('page'),
                       lambda d:d.update(unexpected=True)]:
            path.write_bytes(original)
            self.edit(path.name, change)
            code, out, err = self.run_import()
            self.assertEqual(code, 2, (out, err))
            self.assertNotIn('Traceback', err)
            self.assertFalse(self.output.exists())

    def test_installed_command_runs_from_any_directory(self):
        result = subprocess.run([sys.executable, '-m', 'notewrench', 'archive', 'api-import', str(self.pages/'karakeep-manifest.json'), '-o', str(self.output)], cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads((self.output/'wallabag.json').read_text())), 2)
        self.assertIn('Karakeep cached body 2', next(p.read_text() for p in self.output.glob('article-*') if 'Karakeep cached body 2' in p.read_text()))
