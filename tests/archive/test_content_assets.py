# SPDX-License-Identifier: AGPL-3.0-only
"""Contracts for explicit, scoped saved Karakeep HTML; network is forbidden."""
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from notewrench.archive.cli import main


class ContentAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='archive-content-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / 'archive'
        self.api = self.root / 'pages.json'
        self.assets = self.root / 'content.json'
        self.scope = {'format': 'karakeep-api', 'namespace': 'saved:alice:all', 'user_id': 'alice'}
        self.manifest = {'schema_version': 1, 'source': self.scope, 'pages': [
            {'file': 'response.json', 'namespace': self.scope['namespace'], 'cursor': None}]}
        self.html = '<p>Saved cached body 缓存</p>\r\n'
        self.body = self.html.encode('utf-8')
        self.digest = hashlib.sha256(self.body).hexdigest()
        self.declarations = {'schema_version': 1, 'source': copy.deepcopy(self.scope), 'assets': [
            {'contentAssetId': 'html-1', 'file': 'body.html', 'sha256': self.digest}]}
        self.bookmark = {'id': 'bookmark-1', 'userId': 'alice', 'createdAt': '2026-10-01T10:00:00Z',
                         'title': 'Saved body', 'archived': False, 'favourited': False, 'tags': [],
                         'content': {'type': 'link', 'url': 'https://example.org/article', 'contentAssetId': 'html-1'}}
        self.response = {'bookmarks': [self.bookmark], 'nextCursor': None}
        (self.root / 'body.html').write_bytes(self.body)
        self.save()

    def save(self):
        for name, value in [('pages.json', self.manifest), ('response.json', self.response),
                            ('content.json', self.declarations)]:
            (self.root / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def run_cli(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(['api-import', str(self.api), '--content-assets', str(self.assets), '-o', str(self.output), *extra])
        return code, out.getvalue(), err.getvalue()

    def rejected(self, diagnostic):
        code, out, err = self.run_cli()
        self.assertEqual(code, 2, (out, err))
        self.assertIn(diagnostic, err)
        self.assertNotIn('Traceback', err)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.bagkeep-*')))

    def test_saved_body_strict_offline_hashes_provenance_and_determinism(self):
        # Same asset may supply multiple bookmarks; never derive paths from IDs.
        second = copy.deepcopy(self.bookmark)
        second.update(id='bookmark-2')
        second['content']['htmlContent'] = None
        self.response['bookmarks'].append(second)
        self.save()
        originals = {p.name: p.read_bytes() for p in self.root.iterdir()}
        code, out, err = self.run_cli('--strict')
        self.assertEqual(code, 0, err)
        report = json.loads(out)['manifest']
        entries = json.loads((self.output / 'wallabag.json').read_text())
        self.assertEqual([e['content'] for e in entries], [self.html, self.html])
        self.assertEqual(report['content_assets']['input_sha256'], hashlib.sha256(originals['content.json']).hexdigest())
        for record in report['records']:
            self.assertTrue(record['cached_body'])
            self.assertIn(self.html, (self.output / record['file']).read_bytes().decode('utf-8'))
            self.assertEqual(record['body_source']['kind'], 'content_asset')
            self.assertEqual(record['body_source']['sha256'], self.digest)
        provenance = json.loads((self.output / 'provenance.json').read_text())
        self.assertEqual(provenance['pages'][0]['response'], self.response)
        self.assertEqual(provenance['content_assets']['source'], self.declarations)
        asset = provenance['content_assets']['mapping'][0]
        self.assertEqual((asset['sha256'], asset['expected_sha256'], asset['bytes'], asset['html']),
                         (self.digest, self.digest, len(self.body), self.html))
        self.assertEqual(provenance['mapping'][0]['mapped_fields']['content.contentAssetId'], 'content')
        self.assertNotIn('html', report['content_assets']['mapping'][0])
        self.assertEqual(json.loads(out)['loss_count'], 0)
        for name, info in report['files'].items():
            self.assertEqual(hashlib.sha256((self.output / name).read_bytes()).hexdigest(), info['sha256'])
        first = {p.name: p.read_bytes() for p in self.output.iterdir()}
        shutil.rmtree(self.output)
        code, out, err = self.run_cli('--strict')
        self.assertEqual(code, 0, err)
        self.assertEqual(first, {p.name: p.read_bytes() for p in self.output.iterdir()})
        self.assertEqual(originals, {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()})

    def test_inline_precedence_empty_is_authoritative_and_malformed_is_not_hidden(self):
        for inline in ['<p>Inline wins</p>', '']:
            with self.subTest(inline=inline):
                self.bookmark['content']['htmlContent'] = inline
                self.save()
                code, out, err = self.run_cli()
                self.assertEqual(code, 0, err)
                entries = json.loads((self.output / 'wallabag.json').read_text())
                self.assertEqual(entries[0]['content'], inline)
                self.assertEqual(json.loads(out)['manifest']['records'][0]['body_source']['kind'], 'inline')
                if not inline:
                    self.assertIn('missing_cached_body', out)
                shutil.rmtree(self.output)
        self.bookmark['content']['htmlContent'] = ['invalid']
        self.save()
        self.rejected('expected HTML string')

    def test_missing_and_undeclared_have_explicit_losses_strict_and_dry_run(self):
        (self.root / 'body.html').unlink()
        code, out, err = self.run_cli('--dry-run')
        self.assertEqual(code, 0, err)
        self.assertFalse(self.output.exists())
        report = json.loads(out)['manifest']
        self.assertEqual(report['content_assets']['mapping'][0]['status'], 'missing_file')
        self.assertIn('missing_content_asset_file', [i['code'] for i in report['issues']])
        self.assertIn('missing_cached_body', out)
        code, out, err = self.run_cli('--strict')
        self.assertEqual(code, 3, err)
        self.assertFalse(self.output.exists())
        self.bookmark['content']['contentAssetId'] = 'https://never-fetched.invalid/body'
        self.save()
        code, out, err = self.run_cli()
        self.assertEqual(code, 0, err)
        report = json.loads(out)['manifest']
        self.assertEqual(report['records'][0]['body_source']['status'], 'undeclared')
        loss = next(i for i in report['issues'] if i['code'] == 'missing_content_asset')
        self.assertEqual(loss['source']['item_id'], 'bookmark-1')
        self.assertIn('No readable cached body', (self.output / report['records'][0]['file']).read_text())

    def test_bad_scope_duplicates_and_shapes_rejected(self):
        initial = copy.deepcopy(self.declarations)
        for change, diagnostic in [
            (lambda d: d['source'].update(namespace='other'), 'source_namespace'),
            (lambda d: d['source'].update(user_id='bob'), 'source_namespace'),
            (lambda d: d['assets'].append(copy.deepcopy(d['assets'][0])), 'duplicate_content_asset'),
            (lambda d: d.update(schema_version=True), 'schema_version'),
            (lambda d: d.update(extra=True), 'unknown manifest fields'),
            (lambda d: d['assets'][0].update(sha256='A'*64), 'lowercase SHA-256'),
            (lambda d: d['assets'][0].update(contentAssetId=[]), 'contentAssetId'),
            (lambda d: d['assets'][0].update(extra=True), 'fields only'),
            (lambda d: d.update(assets={}), 'array')]:
            with self.subTest(diagnostic=diagnostic):
                self.declarations = copy.deepcopy(initial)
                change(self.declarations)
                self.save()
                self.rejected(diagnostic)
        self.declarations = initial
        self.bookmark['content']['contentAssetId'] = []
        self.save()
        self.rejected('contentAssetId')

    def test_hash_utf8_and_unused_bad_files_reject_even_with_inline_body(self):
        self.bookmark['content']['htmlContent'] = '<p>Inline available</p>'
        for data, diagnostic in [(b'changed', 'content_asset_sha256'), (b'\xff', 'content_asset_utf8')]:
            (self.root / 'body.html').write_bytes(data)
            if diagnostic.endswith('utf8'):
                self.declarations['assets'][0]['sha256'] = hashlib.sha256(data).hexdigest()
            self.save()
            self.rejected(diagnostic)
        code, out, err = self.run_cli('--dry-run')
        self.assertEqual(code, 2, err)
        self.assertFalse(self.output.exists())

    def test_confined_paths_symlinks_and_nonfiles(self):
        for name in ['../outside.html', '/tmp/body.html', 'https://example.org/body', 'a//b', './body.html', 'a\\b']:
            with self.subTest(name=name):
                self.declarations['assets'][0]['file'] = name
                self.save()
                self.rejected('local_asset_path')
        (self.root / 'link.html').symlink_to('body.html')
        (self.root / 'folder').mkdir()
        (self.root / 'link-folder').symlink_to('folder', target_is_directory=True)
        os.mkfifo(self.root / 'fifo')
        for name in ['link.html', 'folder', 'link-folder/body.html', 'fifo']:
            self.declarations['assets'][0]['file'] = name
            self.save()
            self.rejected('local_asset_path')
        self.declarations['assets'][0]['file'] = 'body.html'
        self.save()
        saved = self.assets.read_bytes()
        self.assets.unlink()
        (self.root / 'other.json').write_bytes(saved)
        self.assets.symlink_to('other.json')
        self.rejected('local_asset_path')

    def test_raw_saved_html_sanitized_and_optional_png_manifest_composes(self):
        image_root = Path(__file__).resolve().parents[2] / 'examples' / 'archive' / 'images'
        self.html = '<p onclick="bad()">Saved</p><script>bad()</script><img src="local-red.png">'
        self.body = self.html.encode()
        (self.root / 'body.html').write_bytes(self.body)
        self.declarations['assets'][0]['sha256'] = hashlib.sha256(self.body).hexdigest()
        self.save()
        code, out, err = self.run_cli('--assets', str(image_root / 'manifest.json'))
        self.assertEqual(code, 0, err)
        report = json.loads(out)['manifest']
        article = (self.output / report['records'][0]['file']).read_text()
        self.assertNotIn('bad()', article)
        self.assertIn('asset-', article)
        self.assertEqual(len(list(self.output.glob('asset-*.png'))), 1)
        self.assertEqual(json.loads((self.output / 'wallabag.json').read_text())[0]['content'], self.html)
        provenance = json.loads((self.output / 'provenance.json').read_text())
        self.assertEqual(provenance['content_assets']['mapping'][0]['html'], self.html)
        self.assertIn('image_assets', provenance)
        loss = next(i for i in report['issues'] if i['code'] == 'removed_active_elements')
        self.assertEqual(loss['source']['file'], 'response.json')
        self.assertEqual(report['records'][0]['body_source']['kind'], 'content_asset')

    def test_nonlinks_do_not_use_content_assets(self):
        self.bookmark['content'] = {'type': 'asset', 'contentAssetId': 'html-1'}
        self.save()
        code, out, err = self.run_cli()
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads((self.output / 'wallabag.json').read_text()), [])
        self.assertIn('api_non_link_not_mapped', out)

    def test_limits_count_distinct_files_and_verify_each_descriptor_hash(self):
        # Two IDs can declare one file; its bytes count once, but both pins must match.
        self.declarations['assets'].append({**self.declarations['assets'][0], 'contentAssetId': 'unused'})
        self.save()
        size = len(self.assets.read_bytes()) + len(self.body)
        with patch('notewrench.archive.content_assets.MAX_CONTENT_BYTES', size):
            code, out, err = self.run_cli('--dry-run', '--strict')
        self.assertEqual(code, 0, err)
        self.assertFalse(self.output.exists())
        with patch('notewrench.archive.content_assets.MAX_CONTENT_BYTES', size - 1):
            self.rejected('aggregate 64 MiB')
        with patch('notewrench.archive.content_assets.MAX_CONTENT_ASSETS', 1):
            self.rejected('asset descriptors')
        self.declarations['assets'][1]['sha256'] = '0' * 64
        self.save()
        self.rejected('content_asset_sha256')

    def test_symlink_swap_rejected_at_open(self):
        original_open = os.open
        def swap(name, flags, *args, **kwargs):
            if name == 'body.html' and kwargs.get('dir_fd') is not None:
                (self.root / name).unlink()
                (self.root / name).symlink_to('pages.json')
            return original_open(name, flags, *args, **kwargs)
        with patch('notewrench.archive.assets.os.open', side_effect=swap):
            self.rejected('local_asset_path')

    def test_repeated_html_expansion_is_bounded_in_provenance_and_entries(self):
        with patch('notewrench.archive.content_assets.MAX_EXPANDED_CONTENT_BYTES', len(self.body)):
            code, out, err = self.run_cli('--dry-run', '--strict')
            self.assertEqual(code, 0, err)
            second = copy.deepcopy(self.bookmark)
            second['id'] = 'bookmark-2'
            self.response['bookmarks'].append(second)
            self.save()
            self.rejected('expanded_content_limit')
            self.response['bookmarks'].pop()
            self.declarations['assets'].append({**self.declarations['assets'][0], 'contentAssetId': 'unused'})
            self.save()
            self.rejected('expanded_content_limit')

    def test_wallabag_source_rejects_content_manifest(self):
        self.manifest['source'] = {'format': 'wallabag-api', 'namespace': self.scope['namespace']}
        self.manifest['pages'][0].pop('cursor')
        self.manifest['pages'][0]['page'] = 1
        self.response = {'page': 1, 'pages': 1, 'limit': 10, 'total': 0, '_embedded': {'items': []}}
        self.save()
        self.rejected('source_namespace')

    def test_bom_byte_hash_empty_and_script_only_saved_bodies(self):
        for body, expected in [(b'\xef\xbb\xbf<p>BOM saved body</p>', 0), (b'', 3), (b'<script>bad()</script>', 3)]:
            with self.subTest(body=body):
                (self.root / 'body.html').write_bytes(body)
                digest = hashlib.sha256(body).hexdigest()
                self.declarations['assets'][0]['sha256'] = digest
                self.save()
                code, out, err = self.run_cli('--strict', '--dry-run')
                self.assertEqual(code, expected, err)
                report = json.loads(out)['manifest']
                self.assertEqual(report['content_assets']['mapping'][0]['sha256'], digest)
                self.assertEqual(report['content_assets']['mapping'][0]['bytes'], len(body))
                self.assertFalse(self.output.exists())

    def test_installed_command_from_any_directory(self):
        result = subprocess.run([sys.executable, '-m', 'notewrench', 'archive', 'api-import', str(self.api),
                                 '--content-assets', str(self.assets), '-o', str(self.output), '--strict'],
                                cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.output / 'wallabag.json').read_text())[0]['content'], self.html)
