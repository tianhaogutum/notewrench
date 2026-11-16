# SPDX-License-Identifier: AGPL-3.0-only
"""Baseline JPEG contracts: complete entropy syntax, unchanged bytes, offline CLI."""
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import unittest
from unittest.mock import patch

from . import test_assets
from .test_assets import ImageTags, ROOT


def segment(marker, payload):
    return bytes([255, marker]) + struct.pack('!H', len(payload) + 2) + payload


def gray_jpeg(restart=False, stuffed=False):
    # Original 16x8 grayscale; DC=0, AC=EOB. Custom one-bit Huffman codes.
    # A second code lets the stuffed case exercise byte stuffing in DC amplitude.
    dc = bytes([0, 2] + [0] * 14) + bytes([0, 11]) if stuffed else bytes([1] + [0] * 15) + b'\0'
    dht = segment(0xc4, b'\0' + dc + b'\x10' + bytes([1] + [0] * 15) + b'\0')
    headers = (b'\xff\xd8' + segment(0xdb, b'\0' + bytes([1] * 64))
               + segment(0xc0, struct.pack('!BHHB3B', 8, 8, 16, 1, 1, 0x11, 0)) + dht)
    scan = segment(0xda, bytes([1, 1, 0, 0, 63, 0]))
    if stuffed:
        # Block 1: 00/0; block 2: 01/11111111111/0; pad with seven ones.
        return headers + scan + b'\x0f\xff\x00\x7f\xff\xd9'
    if restart:
        return headers + segment(0xdd, b'\0\1') + scan + b'\x3f\xff\xd0\x3f\xff\xd9'
    return headers + scan + b'\x0f\xff\xd9'


def color_jpeg(sampling):
    gray = gray_jpeg()
    start, end = gray.index(b'\xff\xc0'), gray.index(b'\xff\xc4')
    width, height = 19, 17  # Partial edge MCUs exercise padded sample counts.
    h, v = sampling >> 4, sampling & 15
    count = ((width + 8*h - 1) // (8*h)) * ((height + 8*v - 1) // (8*v))
    bits = '00' * (h*v + 2) * count
    bits += '1' * (-len(bits) % 8)
    entropy = int(bits, 2).to_bytes(len(bits) // 8, 'big').replace(b'\xff', b'\xff\0')
    frame = segment(0xc0, struct.pack('!BHHB', 8, height, width, 3)
                    + bytes([1, sampling, 0, 2, 0x11, 0, 3, 0x11, 0]))
    scan = segment(0xda, bytes([3, 1, 0, 2, 0, 3, 0, 0, 63, 0]))
    return gray[:start] + frame + gray[end:gray.index(b'\xff\xda')] + scan + entropy + b'\xff\xd9'


class JPEGAssetTests(unittest.TestCase):
    save_manifest = test_assets.ImageAssetTests.save_manifest
    run_cli = test_assets.ImageAssetTests.run_cli
    rejected = test_assets.ImageAssetTests.rejected

    def setUp(self):
        test_assets.ImageAssetTests.setUp(self)
        self.jpeg = (ROOT / 'examples/archive/jpeg/landscape.jpg').read_bytes()
        self.use_jpeg(self.jpeg)

    def use_jpeg(self, data):
        self.data = data
        self.digest = hashlib.sha256(data).hexdigest()
        self.name = f'asset-{self.digest}.jpg'
        # Deliberately misleading extension: classify bytes, never the path.
        (self.root / 'images/photo.png').write_bytes(data)
        (self.root / 'images/copy.bin').write_bytes(data)
        self.html = '<img src="https://saved.invalid/photo.jpeg" alt="red"><img src="copy" alt="copy">'
        self.source.write_text(json.dumps([{'url': 'https://example.org/photo', 'title': 'images', 'content': self.html}]))
        self.declarations = {'schema_version': 1, 'assets': [
            {'reference': 'https://saved.invalid/photo.jpeg', 'file': 'images/photo.png', 'sha256': self.digest},
            {'reference': 'copy', 'file': 'images/copy.bin', 'sha256': self.digest}]}
        self.save_manifest()

    def test_jpeg_bytes_deduplicate_preserve_provenance_and_hashes(self):
        # The previous implementation rejects this real baseline JPEG as PNG.
        test_assets.ImageAssetTests.test_images_deduplicate_offline_preserve_originals_and_hashes(self)
        mapping = json.loads((self.output / 'provenance.json').read_text())['image_assets']['mapping']
        self.assertEqual([m['media_type'] for m in mapping], ['image/jpeg'] * 2)

    def test_grayscale_stuffing_restarts_and_opaque_metadata(self):
        for data in [gray_jpeg(), gray_jpeg(restart=True), gray_jpeg(stuffed=True),
                     color_jpeg(0x11), color_jpeg(0x21), color_jpeg(0x22),
                     self.jpeg[:2] + segment(0xe1, b'Exif\0\0saved metadata') + segment(0xfe, b'original comment') + self.jpeg[2:]]:
            with self.subTest(sha256=hashlib.sha256(data).hexdigest()):
                self.use_jpeg(data)
                code, out, err = self.run_cli('--strict')
                self.assertEqual(code, 0, err)
                self.assertEqual((self.output / self.name).read_bytes(), data)
                shutil.rmtree(self.output)

    def test_invalid_markers_tables_frame_and_scan_reject_even_unused(self):
        gray = gray_jpeg()
        sof = gray.index(b'\xff\xc0')
        sos = gray.index(b'\xff\xda')
        dht = gray.index(b'\xff\xc4')
        cases = {
            'svg': b'<svg onload="alert(1)"></svg>', 'html': b'<html><script>evil()</script></html>',
            'executable': b'\x7fELF' + gray, 'trailing script': gray + b'<script>evil()</script>',
            'second image': gray + gray, 'truncated segment': gray[:10],
            'truncated scan': gray[:-3] + b'\xff\xd9', 'missing EOI': gray[:-2],
            'bad length': gray[:4] + b'\xff\xff' + gray[6:],
            'progressive': gray[:sof+1] + b'\xc2' + gray[sof+2:],
            '12-bit': gray[:sof+4] + b'\x0c' + gray[sof+5:],
            'zero quantizer': gray[:7] + b'\0' + gray[8:],
            'missing tables': gray[:dht] + gray[sos:],
            'oversubscribed Huffman': gray[:dht+5] + b'\x03' + gray[dht+6:],
            'bad DC symbol': gray[:dht+21] + b'\x0c' + gray[dht+22:],
            'undefined component': gray[:sos+5] + b'\x02' + gray[sos+6:],
            'undefined Huffman selector': gray[:sos+6] + b'\x11' + gray[sos+7:],
            'bad sequential scan': gray[:sos+8] + b'\x3e' + gray[sos+9:],
            'zero dimension': gray[:sof+5] + b'\0\0' + gray[sof+7:],
            'excessive dimensions': gray[:sof+5] + b'\xff\xff\xff\xff' + gray[sof+9:],
        }
        for label, data in cases.items():
            with self.subTest(case=label):
                self.use_jpeg(data)
                self.source.write_text('[{"url":"https://example.org","title":"Valid body","content":"<p>no images</p>"}]')
                self.rejected('unsupported_image')

    def test_corrupt_entropy_padding_and_restart_reject(self):
        plain, restart = gray_jpeg(), gray_jpeg(restart=True)
        for data in [plain[:-3] + b'\x8f\xff\xd9', plain[:-3] + b'\x00\xff\xd9',
                     plain[:-2] + b'\0\xff\xd9', plain[:-3] + b'\xff\x00\xff\xd9',
                     restart.replace(b'\xff\xd0', b'\xff\xd1'),
                     restart.replace(b'\xff\xd0', b''),
                     restart.replace(b'\x3f\xff\xd0', b'\x00\xff\xd0')]:
            with self.subTest(data=data[-10:]):
                self.use_jpeg(data)
                self.rejected('unsupported_image')

    def test_jpeg_dry_run_hash_mismatch_and_missing_strict(self):
        code, out, err = self.run_cli('--dry-run', '--strict')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)['status'], 'planned')
        self.assertFalse(self.output.exists())
        (self.root / 'images/photo.png').write_bytes(self.jpeg + b'x')
        self.rejected('asset_sha256')
        self.use_jpeg(self.jpeg + b'x')
        for flags in [('--dry-run',), ('--strict',)]:
            code, out, err = self.run_cli(*flags)
            self.assertEqual(code, 2, (out, err))
            self.assertIn('unsupported_image', err)
            self.assertFalse(self.output.exists())
        self.use_jpeg(self.jpeg)
        (self.root / 'images/photo.png').unlink()
        code, out, err = self.run_cli('--strict')
        self.assertEqual(code, 3, err)
        self.assertFalse(self.output.exists())
        self.assertIn('missing_image_asset', [i['code'] for i in json.loads(out)['manifest']['issues']])

    def test_jpeg_pixel_and_decoded_sample_limits(self):
        with patch('notewrench.archive.assets.MAX_PIXELS', 1):
            self.rejected('unsupported_image')
        with patch('notewrench.archive.assets.MAX_ASSET_BYTES', 10000), patch('notewrench.archive.jpeg.MAX_DECODED_BYTES', 100):
            self.rejected('unsupported_image')

    def test_api_cached_body_composes_with_png_and_jpeg(self):
        (self.root / 'images/copy.bin').write_bytes((ROOT / 'examples/archive/images/red.png').read_bytes())
        self.declarations['assets'][1]['sha256'] = hashlib.sha256((self.root / 'images/copy.bin').read_bytes()).hexdigest()
        self.save_manifest()
        api = self.root / 'api'
        shutil.copytree(ROOT / 'examples/archive/content', api)
        (api / 'body.html').write_text(self.html)
        content = json.loads((api / 'content-manifest.json').read_text())
        content['assets'][0]['sha256'] = hashlib.sha256((api / 'body.html').read_bytes()).hexdigest()
        (api / 'content-manifest.json').write_text(json.dumps(content))
        code, out, err = self.run_cli('--strict', '--content-assets', str(api / 'content-manifest.json'),
                                      command='api-import', source=api / 'api-manifest.json')
        self.assertEqual(code, 0, err)
        report = json.loads(out)['manifest']
        self.assertEqual([m['media_type'] for m in report['image_assets']['mapping']], ['image/jpeg', 'image/png'])
        self.assertEqual(json.loads((self.output / 'wallabag.json').read_text())[0]['content'], self.html)
        article = (self.output / report['records'][0]['file']).read_text()
        self.assertEqual(len(ImageTags(article).images), 2)
        self.assertEqual(report['records'][0]['body_source']['kind'], 'content_asset')

    def test_shared_jpeg_across_articles_has_one_payload(self):
        self.source.write_text(json.dumps([
            {'url': f'https://example.org/{i}', 'title': 'Shared photo', 'content': self.html}
            for i in range(2)]))
        code, out, err = self.run_cli('--strict')
        self.assertEqual(code, 0, err)
        report = json.loads(out)['manifest']
        self.assertEqual(len(list(self.output.glob('asset-*'))), 1)
        for record in report['records']:
            images = ImageTags((self.output / record['file']).read_text()).images
            self.assertEqual([i['src'] for i in images], [self.name, self.name])

    def test_isolated_installed_cli_jpeg(self):
        result = subprocess.run([sys.executable, '-m', 'notewrench', 'archive', 'rescue', str(self.source), '--assets',
                                 str(self.manifest), '-o', str(self.output), '--strict'], cwd=self.root,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.output / self.name).read_bytes(), self.jpeg)
        self.assertEqual(len(list(self.output.glob('asset-*'))), 1)
