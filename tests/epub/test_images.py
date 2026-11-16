"""Synthetic PNG and local manifest integration regressions (no downloaded media)."""
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
import zipfile
import zlib

from notewrench.epub import BundleError
from notewrench.epub.core import prepare
from notewrench.epub.epub import OPF, XHTML, q, validate_epub

ROOT = Path(__file__).resolve().parents[2]


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))


def png(pixel=b'\x40\x80\xff', width=1, height=1):
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\0' + pixel)) + chunk(b'IEND', b''))


class ImageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='epub-images-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.assets = self.work / 'assets'
        self.assets.mkdir()
        self.data = png()
        self.image = self.assets / 'shared.bin'  # content, never extension, decides media type
        self.image.write_bytes(self.data)
        self.digest = hashlib.sha256(self.data).hexdigest()
        self.source = self.work / 'articles.json'
        self.rows = [{'id': n, 'title': f'Article {n}', 'url': f'https://example.invalid/{n}',
                      'content': f'<p>Article {n}<img src="/shared.png" alt="图 {n}" onerror="bad()" '
                                 'srcset="https://undeclared.invalid/large.png 2x"></p>'} for n in (1, 2)]
        self.source.write_text(json.dumps(self.rows), encoding='utf-8')
        self.entries = [dict(key=f'1:{n}', src='/shared.png', path='shared.bin',
                             media_type='image/png', sha256=self.digest) for n in (1, 2)]
        self.manifest = self.assets / 'images.json'
        self.write_manifest()

    def write_manifest(self, entries=None):
        self.manifest.write_text(json.dumps({'schema_version': 1, 'images': self.entries if entries is None else entries}), encoding='utf-8')

    def build(self, **options):
        return prepare([self.source], image_manifest=self.manifest, **options)

    def rewrite(self, payload, mutate):
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(payload)) as original, zipfile.ZipFile(output, 'w') as result:
            for info in original.infolist():
                value = mutate(info.filename, original.read(info))
                if value is not None:
                    result.writestr(info, value)
        return output.getvalue()

    def test_shared_image_manifest_xhtml_and_provenance_agree(self):
        with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), \
             patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS forbidden')):
            payload, report = self.build()
            self.assertEqual(self.build()[0], payload)
            self.assertEqual(validate_epub(io.BytesIO(payload))['chapters'], 2)
        href = f'images/{self.digest}.png'
        with zipfile.ZipFile(io.BytesIO(payload)) as book:
            self.assertEqual([n for n in book.namelist() if n.endswith('.png')], ['EPUB/' + href])
            self.assertEqual(book.read('EPUB/' + href), self.data)
            opf = ET.fromstring(book.read('EPUB/package.opf'))
            images = [i for i in opf.find(q(OPF, 'manifest')) if i.get('media-type') == 'image/png']
            self.assertEqual([i.get('href') for i in images], [href])
            self.assertEqual(len(opf.find(q(OPF, 'spine'))), 2)
            for n in (1, 2):
                chapter = ET.fromstring(book.read(f'EPUB/chapter-{n:04d}.xhtml'))
                img = chapter.find('.//' + q(XHTML, 'img'))
                self.assertEqual(img.attrib, {'src': href, 'alt': f'图 {n}'})
                self.assertIn('EPUB/' + img.get('src'), book.namelist())
        self.assertEqual(len(report['resources']), 1)
        self.assertEqual(report['resources'][0]['sha256'], self.digest)
        self.assertEqual(report['resources'][0]['href'], href)
        self.assertEqual(len(report['resources'][0]['declarations']), 2)
        self.assertEqual(sum(e['code'] == 'embedded_resource' for e in report['changes']), 2)
        self.assertEqual(sum(e['code'] == 'missing_resource' and e.get('attribute') == 'srcset'
                             for e in report['changes']), 2)

    def test_default_unchanged_undeclared_and_scoped_references_omitted(self):
        implicit, report = prepare([self.source])
        self.assertEqual(implicit, prepare([self.source], image_manifest=None)[0])
        self.assertNotIn('resources', report)
        self.write_manifest(self.entries[:1])
        payload, report = self.build()
        with zipfile.ZipFile(io.BytesIO(payload)) as book:
            self.assertIn(b'<img', book.read('EPUB/chapter-0001.xhtml'))
            self.assertNotIn(b'<img', book.read('EPUB/chapter-0002.xhtml'))
        missing = [e for e in report['changes'] if e['code'] == 'missing_resource' and e.get('attribute') != 'srcset']
        self.assertEqual(missing[0]['key'], '1:2')
        self.assertEqual(missing[0]['reason'], 'image_not_declared')

    def test_distinct_local_paths_and_source_urls_deduplicate_by_bytes(self):
        (self.assets / 'copy.png').write_bytes(self.data)
        self.rows[1]['content'] = '<img src="https://cdn.invalid/shared.png" alt="">'
        self.source.write_text(json.dumps(self.rows))
        self.entries[1].update(path='copy.png', src='https://cdn.invalid/shared.png')
        self.write_manifest()
        first, report = self.build()
        self.assertEqual(len(report['resources']), 1)
        self.assertEqual(len(report['resources'][0]['declarations']), 2)
        self.write_manifest(list(reversed(self.entries)))
        self.assertEqual(self.build()[0], first)
        # A missing declaration for a remote image is an omission, never a fetch.
        self.write_manifest(self.entries[:1])
        _, report = self.build()
        self.assertTrue(any(e['code'] == 'missing_resource' and e.get('url') == 'https://cdn.invalid/shared.png'
                            for e in report['changes']))

    def test_paths_hashes_media_and_nonregular_files_reject(self):
        outside = self.work / 'outside.png'
        outside.write_bytes(self.data)
        (self.assets / 'escape.png').symlink_to(outside)
        (self.assets / 'escape-dir').symlink_to(self.work, target_is_directory=True)
        (self.assets / 'directory').mkdir()
        os.mkfifo(self.assets / 'pipe')
        cases = [('path', '../outside.png'), ('path', str(outside)), ('path', 'escape.png'),
                 ('path', 'escape-dir/outside.png'), ('path', 'pipe'),
                 ('path', 'directory'), ('path', 'missing.png'), ('path', 'sub\\image.png'),
                 ('media_type', 'image/jpeg'), ('sha256', '0' * 64), ('sha256', 'bad')]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                self.write_manifest([{**self.entries[0], field: value}])
                with self.assertRaises((BundleError, OSError)):
                    self.build()
        self.write_manifest()
        for bad in (b'<svg xmlns="http://www.w3.org/2000/svg"/>', self.data[:24],
                    self.data + b'<script>evil</script>', png(width=100000, height=100000),
                    png(pixel=b'\0' * 10000)):
            with self.subTest(bad=bad[:20]):
                self.image.write_bytes(bad)
                self.write_manifest([{**self.entries[0], 'sha256': hashlib.sha256(bad).hexdigest()}])
                with self.assertRaises(BundleError):
                    self.build()

    def test_size_bound_and_strict_manifest_schema(self):
        with self.image.open('wb') as stream:
            stream.truncate(8 * 1024 * 1024 + 1)
        with self.assertRaisesRegex(BundleError, '8 MiB'):
            self.build()
        self.image.write_bytes(self.data)
        with patch('notewrench.epub.images.MAX_IMAGE_TOTAL', len(self.data) - 1):
            with self.assertRaisesRegex(BundleError, '32 MiB'):
                self.build()
        with patch('notewrench.epub.images.MAX_MANIFEST_BYTES', 1):
            with self.assertRaisesRegex(BundleError, '1 MiB'):
                self.build()
        for entries in ([self.entries[0], self.entries[0]], [{**self.entries[0], 'key': '1:absent'}],
                        [{**self.entries[0], 'src': 'javascript:evil'}], [{**self.entries[0], 'typo': True}]):
            self.write_manifest(entries)
            with self.subTest(entries=entries), self.assertRaises(BundleError):
                self.build()
        for raw in ('{"schema_version":1,"images":[],"images":[]}', '{"schema_version":true,"images":[]}',
                    '{"schema_version":1,"images":NaN}', '[]', '{'):
            self.manifest.write_text(raw)
            with self.subTest(raw=raw), self.assertRaises(BundleError):
                self.build()

    def test_png_crc_stream_filter_and_interlace_checks(self):
        from notewrench.epub.images import validate_png
        # Valid signature alone is insufficient, even with a matching declared hash.
        header = chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
        bad_crc = bytearray(self.data)
        bad_crc[-1] ^= 1
        cases = [bytes(bad_crc), self.data[:8] + header + chunk(b'IEND', b''),
                 self.data[:8] + header + chunk(b'IDAT', zlib.compress(b'\x05abc')) + chunk(b'IEND', b''),
                 self.data[:8] + header + chunk(b'IDAT', zlib.compress(b'\0abc') + b'extra') + chunk(b'IEND', b''),
                 self.data[:8] + header + chunk(b'acTL', struct.pack('>II', 1, 0)) + self.data[33:],
                 self.data[:8] + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 1)) + self.data[33:]]
        for bad in cases:
            with self.subTest(bad=bad[:40]), self.assertRaises(BundleError):
                validate_png(bad)
        # Legal grayscale, indexed and alpha variants, not only the RGB fixture.
        for color, depth, pixels in ((0, 1, b'\x80'), (0, 16, b'\x80\0'), (3, 1, b'\0'),
                                     (4, 8, b'\x80\xff'), (6, 8, b'abcd')):
            palette = chunk(b'PLTE', b'\0\0\0') if color == 3 else b''
            data = (self.data[:8] + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, depth, color, 0, 0, 0))
                    + palette + chunk(b'IDAT', zlib.compress(b'\0' + pixels)) + chunk(b'IEND', b''))
            with self.subTest(color=color, depth=depth):
                validate_png(data)

    def test_image_only_chapter_and_unused_assets_not_archived(self):
        self.rows[0]['content'] = '<img src="/shared.png" alt="">'
        self.source.write_text(json.dumps(self.rows))
        self.write_manifest([self.entries[0], {**self.entries[1], 'src': '/unused.png'}])
        payload, report = self.build(ids=['1:1'])
        self.assertIsNotNone(payload)
        self.assertEqual(len(report['included']), 1)
        self.assertEqual(len(report['resources']), 1)
        self.assertEqual(len(report['resources'][0]['declarations']), 1)
        self.assertEqual(report['unused_images'][0]['key'], '1:2')

    def test_validator_rejects_broken_external_mistyped_and_unused_images(self):
        payload, _ = self.build()
        cases = [
            ('EPUB/chapter-0001.xhtml', f'images/{self.digest}.png'.encode(), b'images/absent.png'),
            ('EPUB/chapter-0001.xhtml', f'images/{self.digest}.png'.encode(), b'https://evil.invalid/x.png'),
            ('EPUB/chapter-0001.xhtml', b'<img ', b'<img onclick="evil()" '),
            ('EPUB/package.opf', b'media-type="image/png"', b'media-type="image/jpeg"'),
            ('EPUB/package.opf', b'idref="chapter-0001"', f'idref="image-{self.digest}"'.encode()),
        ]
        for path, old, new in cases:
            with self.subTest(path=path, new=new):
                bad = self.rewrite(payload, lambda name, blob: blob.replace(old, new) if name == path else blob)
                with self.assertRaises(BundleError):
                    validate_epub(io.BytesIO(bad))
        for mutate in (
            lambda name, blob: None if name.endswith('.png') else blob,
            lambda name, blob: b'forged' if name.endswith('.png') else blob,
            lambda name, blob: blob.replace(b'<img ', b'<span ').replace(b' src=', b' title=').replace(b' alt=', b' lang=')
                               if name.endswith('.xhtml') else blob,
        ):
            with self.assertRaises(BundleError):
                validate_epub(io.BytesIO(self.rewrite(payload, mutate)))

    def test_cli_fails_before_publication_and_dry_run_is_offline(self):
        output = self.work / 'out.epub'
        command = [sys.executable, '-m', 'notewrench', 'epub', 'build', str(self.source),
                   '--image-manifest', str(self.manifest), '-o', str(output)]
        result = subprocess.run([*command, '--dry-run'], cwd=ROOT, text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)['resources']), 1)
        self.assertFalse(output.exists())
        self.image.write_bytes(b'forged')
        result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 2)
        self.assertIn('SHA-256', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(output.exists())
        self.assertFalse(output.with_suffix('.report.json').exists())
