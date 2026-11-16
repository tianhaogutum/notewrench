# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic static PNGs and local manifests; no network or browser required."""
import contextlib
import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

from notewrench.archive.cli import main

ROOT = Path(__file__).resolve().parents[2]


def png():
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\0\xff\0\0')) + chunk(b'IEND', b''))


class ImageTags(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.images = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == 'img':
            self.images.append(dict(attrs))


class ImageAssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='archive-images-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / 'archive'
        self.source = self.root / 'input.json'
        self.manifest = self.root / 'images.json'
        self.data = png()
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.name = f'asset-{self.digest}.png'
        (self.root / 'images').mkdir()
        (self.root / 'images' / 'red.png').write_bytes(self.data)
        (self.root / 'images' / 'copy.png').write_bytes(self.data)
        self.html = '<img src="https://saved.invalid/red" alt="red"><img src="copy.png" alt="copy">'
        self.source.write_text(json.dumps([{'url': 'https://example.org/article', 'title': 'images', 'content': self.html}]))
        self.declarations = {'schema_version': 1, 'assets': [
            {'reference': 'https://saved.invalid/red', 'file': 'images/red.png', 'sha256': self.digest},
            {'reference': 'copy.png', 'file': 'images/copy.png', 'sha256': self.digest}]}
        self.save_manifest()

    def save_manifest(self):
        self.manifest.write_text(json.dumps(self.declarations))

    def run_cli(self, *extra, command='rescue', source=None):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([command, str(source or self.source), '--assets', str(self.manifest), '-o', str(self.output), *extra])
        return code, out.getvalue(), err.getvalue()

    def rejected(self, diagnostic):
        code, out, err = self.run_cli()
        self.assertEqual(code, 2, (out, err))
        self.assertIn(diagnostic, err)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.bagkeep-*')))

    def test_images_deduplicate_offline_preserve_originals_and_hashes(self):
        before = {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        code, out, err = self.run_cli('--strict')
        self.assertEqual(code, 0, err)
        report = json.loads(out)['manifest']
        article = (self.output / report['records'][0]['file']).read_text()
        images = ImageTags(article).images
        self.assertEqual([i['src'] for i in images], [self.name, self.name])
        self.assertEqual([i['alt'] for i in images], ['red', 'copy'])
        self.assertTrue(report['records'][0]['cached_body'])
        self.assertNotIn('No readable cached body', article)
        self.assertIn("img-src 'self'", article)
        self.assertEqual(list(self.output.glob('asset-*')), [self.output / self.name])
        self.assertEqual((self.output / self.name).read_bytes(), self.data)
        provenance = json.loads((self.output / 'provenance.json').read_text())
        self.assertEqual(provenance['source'][0]['content'], self.html)
        self.assertEqual(provenance['image_assets']['source'], self.declarations)
        self.assertEqual(provenance['image_assets']['input_sha256'], hashlib.sha256(self.manifest.read_bytes()).hexdigest())
        self.assertEqual([m['file'] for m in provenance['image_assets']['mapping']], [self.name, self.name])
        self.assertEqual([m['sha256'] for m in provenance['image_assets']['mapping']], [self.digest, self.digest])
        self.assertEqual([r['file'] for r in report['records'][0]['image_references']], [self.name, self.name])
        for name, info in report['files'].items():
            payload = (self.output / name).read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), info['sha256'])
            self.assertEqual(len(payload), info['bytes'])
        for name, original in before.items():
            self.assertEqual((self.root / name).read_bytes(), original)
        snapshot = {p.name: p.read_bytes() for p in self.output.iterdir()}
        shutil.rmtree(self.output)
        self.assertEqual(self.run_cli('--strict')[0], 0)
        self.assertEqual(snapshot, {p.name: p.read_bytes() for p in self.output.iterdir()})

    def test_missing_file_and_undeclared_reference_are_explicit_losses(self):
        (self.root / 'images' / 'red.png').unlink()
        self.html += '<p>body</p><img src="https://never-read.invalid/x" alt="missing">'
        self.source.write_text(json.dumps([{'url': 'https://example.org', 'title': 'x', 'content': self.html}]))
        code, out, err = self.run_cli('--strict')
        self.assertEqual(code, 3, err)
        self.assertFalse(self.output.exists())
        report = json.loads(out)['manifest']
        self.assertIn('missing_asset_file', [i['code'] for i in report['issues']])
        self.assertIn('missing_image_asset', [i['code'] for i in report['issues']])
        references = report['records'][0]['image_references']
        self.assertEqual([r['status'] for r in references], ['missing_file', 'archived', 'undeclared'])
        code, out, err = self.run_cli()
        self.assertEqual(code, 0, err)
        article = (self.output / json.loads(out)['manifest']['records'][0]['file']).read_text()
        self.assertEqual([i['src'] for i in ImageTags(article).images], [self.name])
        self.assertIn('missing', article)
        self.assertEqual(json.loads((self.output / 'provenance.json').read_text())['source'][0]['content'], self.html)

    def test_without_manifest_existing_local_and_remote_images_stay_unread(self):
        html = '<p>body</p><img src="images/red.png" alt="local"><img src="https://never-read.invalid/x">'
        self.source.write_text(json.dumps([{'url': 'https://example.org', 'title': 'x', 'content': html}]))
        out = io.StringIO()
        with patch('notewrench.archive.assets._read', side_effect=AssertionError('no implicit reads')), patch.object(socket, 'socket', side_effect=AssertionError('no network')), contextlib.redirect_stdout(out):
            code = main(['rescue', str(self.source), '-o', str(self.output)])
        self.assertEqual(code, 0)
        report = json.loads(out.getvalue())['manifest']
        self.assertEqual([r['status'] for r in report['records'][0]['image_references']], ['undeclared', 'undeclared'])
        self.assertIn('missing_image_asset', [i['code'] for i in report['issues']])
        self.assertFalse(list(self.output.glob('asset-*')))
        self.assertEqual(ImageTags((self.output / report['records'][0]['file']).read_text()).images, [])

    def test_invalid_paths_and_all_symlinks_are_rejected_before_read(self):
        (self.root / 'alias.png').symlink_to(self.root / 'images' / 'red.png')
        (self.root / 'alias-dir').symlink_to(self.root / 'images', target_is_directory=True)
        (self.root / 'outside.png').symlink_to('/etc/passwd')
        for path in ['/etc/passwd', '../secret.png', 'images/../../secret.png', 'C:/x.png', 'https://x/x.png', 'images\\red.png', 'alias.png', 'alias-dir/red.png', 'outside.png', 'images']:
            with self.subTest(path=path):
                self.declarations['assets'][0]['file'] = path
                self.save_manifest()
                self.rejected('local_asset_path')

    def test_hash_mismatch_executable_svg_and_corrupt_png_rejected(self):
        path = self.root / 'images' / 'red.png'
        self.declarations['assets'] = self.declarations['assets'][:1]
        for data, diagnostic in [(self.data, 'asset_sha256'), (b'<svg onload="evil()"></svg>', 'unsupported_image'),
                                 (b'<script>evil()</script>', 'unsupported_image'),
                                 (self.data + b'<script>evil()</script>', 'unsupported_image'),
                                 (self.data[:-1] + b'X', 'unsupported_image')]:
            with self.subTest(diagnostic=diagnostic):
                path.write_bytes(data)
                self.declarations['assets'][0]['sha256'] = '0' * 64 if data == self.data else hashlib.sha256(data).hexdigest()
                self.save_manifest()
                self.rejected(diagnostic)

    def test_animated_oversize_and_invalid_compressed_png_are_rejected(self):
        def chunk(kind, data):
            return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
        animated = self.data[:33] + chunk(b'acTL', struct.pack('!2I', 1, 0)) + self.data[33:]
        oversized = (self.data[:8] + chunk(b'IHDR', struct.pack('!2I5B', 100000, 100000, 8, 2, 0, 0, 0)) + self.data[33:])
        bomb = self.data[:33] + chunk(b'IDAT', zlib.compress(b'\0' * 100000)) + chunk(b'IEND', b'')
        broken = self.data[:33] + chunk(b'IDAT', b'not zlib') + chunk(b'IEND', b'')
        interlaced = self.data[:8] + chunk(b'IHDR', struct.pack('!2I5B', 1, 1, 8, 2, 0, 0, 1)) + self.data[33:]
        for data in [animated, oversized, bomb, broken, interlaced]:
            with self.subTest(data=data[:40]):
                (self.root / 'images' / 'red.png').write_bytes(data)
                self.declarations['assets'][0]['sha256'] = hashlib.sha256(data).hexdigest()
                self.save_manifest()
                self.rejected('unsupported_image')

    def test_nonfiles_and_symlink_swap_never_read(self):
        os.mkfifo(self.root / 'pipe.png')
        self.declarations['assets'][0]['file'] = 'pipe.png'
        self.save_manifest()
        self.rejected('local_asset_path')
        self.declarations['assets'][0]['file'] = 'images/red.png'
        self.save_manifest()
        real_open = os.open

        def replace_with_symlink(path, flags, *args, **kwargs):
            if path == 'red.png':
                (self.root / 'images' / 'red.png').unlink()
                (self.root / 'images' / 'red.png').symlink_to(self.root / 'images' / 'copy.png')
            return real_open(path, flags, *args, **kwargs)

        with patch('notewrench.archive.assets.os.open', side_effect=replace_with_symlink):
            self.rejected('local_asset_path')
        self.manifest.unlink()
        self.manifest.symlink_to(self.source)
        self.rejected('local_asset_path')

    def test_manifest_validation_limits_and_dry_run(self):
        code, out, err = self.run_cli('--dry-run', '--strict')
        self.assertEqual(code, 0, err)
        self.assertFalse(self.output.exists())
        self.assertIn(self.name, json.loads(out)['would_write'])
        original = json.loads(json.dumps(self.declarations))
        for change, diagnostic in [(lambda d: d.update(schema_version=True), 'schema_version'),
                                   (lambda d: d['assets'].append(dict(d['assets'][0])), 'duplicate_asset_reference'),
                                   (lambda d: d['assets'][0].update(sha256='wrong'), 'sha256'),
                                   (lambda d: d.update(unknown=True), 'unknown manifest fields')]:
            self.declarations = json.loads(json.dumps(original))
            change(self.declarations)
            self.save_manifest()
            self.rejected(diagnostic)
        self.declarations = original
        self.save_manifest()
        with patch('notewrench.archive.assets.MAX_ASSET_BYTES', len(self.manifest.read_bytes()) + len(self.data) - 1):
            self.rejected('aggregate 64 MiB')

    def test_only_explicit_src_is_used_and_unsafe_attributes_removed(self):
        self.html = ('<p>body</p><img src="copy.png" srcset="https://never-read.invalid/a 2x" onerror="evil()" style="x" alt="&quot;&gt;">'
                     '<picture><source srcset="images/red.png"><img src="images/red.png" alt="undeclared local"></picture>'
                     '<svg><image href="copy.png"/></svg><img src="/etc/passwd"><img src="../outside.png">')
        self.source.write_text(json.dumps([{'url': 'https://example.org', 'title': 'x', 'content': self.html}]))
        code, out, err = self.run_cli()
        self.assertEqual(code, 0, err)
        article = (self.output / json.loads(out)['manifest']['records'][0]['file']).read_text()
        self.assertEqual(ImageTags(article).images, [{'src': self.name, 'alt': '">'}])
        for forbidden in ['srcset=', 'onerror=', 'style=', '<svg', '<source']:
            self.assertNotIn(forbidden, article)
        self.assertIn('undeclared local', article)

    def test_api_import_uses_assets_but_preserves_import_html_and_page_mapping(self):
        shutil.copytree(ROOT / 'examples' / 'archive' / 'api', self.root / 'pages')
        path = self.root / 'pages' / 'karakeep-1.json'
        page = json.loads(path.read_text())
        page['bookmarks'][0]['content']['htmlContent'] = self.html
        path.write_text(json.dumps(page))
        code, out, err = self.run_cli(command='api-import', source=self.root / 'pages' / 'karakeep-manifest.json')
        self.assertEqual(code, 0, err)
        report = json.loads(out)['manifest']
        article = (self.output / report['records'][0]['file']).read_text()
        self.assertEqual([i['src'] for i in ImageTags(article).images], [self.name, self.name])
        self.assertEqual(json.loads((self.output / 'wallabag.json').read_text())[0]['content'], self.html)
        provenance = json.loads((self.output / 'provenance.json').read_text())
        self.assertEqual(provenance['image_assets']['source'], self.declarations)
        self.assertEqual(provenance['pages'][0]['response'], page)
        self.assertEqual(report['records'][0]['source']['file'], 'karakeep-1.json')
        for name, info in report['files'].items():
            self.assertEqual(hashlib.sha256((self.output / name).read_bytes()).hexdigest(), info['sha256'])

    def test_installed_command_assets_work_from_any_directory(self):
        result = subprocess.run([sys.executable, '-m', 'notewrench', 'archive', 'rescue', str(self.source), '--assets', str(self.manifest), '-o', str(self.output), '--strict'], cwd=self.root, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.output / self.name).read_bytes(), self.data)
        report = json.loads(result.stdout)['manifest']
        self.assertEqual([i['src'] for i in ImageTags((self.output / report['records'][0]['file']).read_text()).images], [self.name, self.name])
