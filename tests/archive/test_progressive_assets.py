# SPDX-License-Identifier: AGPL-3.0-only
"""Progressive JPEG contracts; checked-in synthetic fixtures need no image library."""
import hashlib
import json
import shutil
import unittest
from unittest.mock import patch

from . import test_assets
from .test_assets import ROOT
from . import test_jpeg_assets as jpeg_tests
from .test_jpeg_assets import gray_jpeg, segment


def scans(raw):
    """Locate SOS/entropy/end offsets in our test fixtures, skipping stuffed/RST bytes."""
    result = []
    offset = 2
    while offset < len(raw):
        start = offset
        assert raw[offset] == 255
        marker = raw[offset+1]
        if marker == 0xd9:
            break
        length = int.from_bytes(raw[offset+2:offset+4], 'big')
        offset += 2 + length
        if marker == 0xda:
            header_end = offset
            while True:
                offset = raw.index(b'\xff', offset)
                if raw[offset+1] == 0 or 0xd0 <= raw[offset+1] <= 0xd7:
                    offset += 2
                else:
                    break
            result.append((start, header_end, offset))
    return result


class ProgressiveAssetTests(unittest.TestCase):
    save_manifest = test_assets.ImageAssetTests.save_manifest
    run_cli = test_assets.ImageAssetTests.run_cli
    use_jpeg = jpeg_tests.JPEGAssetTests.use_jpeg

    def setUp(self):
        test_assets.ImageAssetTests.setUp(self)
        self.jpeg = (ROOT / 'examples/archive/progressive/photo-420.jpg').read_bytes()
        self.use_jpeg(self.jpeg)

    def test_progressive_photo_preserves_bytes_dedup_provenance_and_hashes(self):
        test_assets.ImageAssetTests.test_images_deduplicate_offline_preserve_originals_and_hashes(self)
        mapping = json.loads((self.output / 'provenance.json').read_text())['image_assets']['mapping']
        self.assertEqual([m['media_type'] for m in mapping], ['image/jpeg'] * 2)

    def test_progressive_grayscale_sampling_partial_edges_and_restarts(self):
        for path in sorted((ROOT / 'examples/archive/progressive').glob('*.jpg')):
            with self.subTest(fixture=path.name):
                self.use_jpeg(path.read_bytes())
                code, out, err = self.run_cli('--strict')
                self.assertEqual(code, 0, err)
                self.assertEqual((self.output / self.name).read_bytes(), path.read_bytes())
                shutil.rmtree(self.output)

    def test_progressive_saved_html_png_composition(self):
        jpeg_tests.JPEGAssetTests.test_api_cached_body_composes_with_png_and_jpeg(self)

    def assert_invalid(self, data, flags=()):
        self.use_jpeg(data)
        # Declared but unused: validators must run independently of HTML references.
        self.source.write_text('[{"url":"https://example.org","content":"<p>saved</p>"}]')
        code, out, err = self.run_cli(*flags)
        self.assertEqual((code, out), (2, ''), err)
        self.assertIn('unsupported_image', err)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.bagkeep-*')))

    def test_invalid_order_bands_refinements_modes_and_trailing_bytes(self):
        raw = self.jpeg
        parts = scans(raw)
        def header(index, tail):
            start, end, _ = parts[index]
            return raw[:end-3] + bytes(tail) + raw[end:]
        a, b, c = parts[1]
        sof = raw.index(b'\xff\xc2')
        cases = {
            'AC before DC': raw[:parts[0][0]] + raw[parts[0][2]:],
            'duplicate first scan': raw[:c] + raw[a:c] + raw[c:],
            'missing final refinement': raw[:parts[-1][0]] + raw[parts[-1][2]:],
            'missing AC band': raw[:parts[1][0]] + raw[parts[1][2]:],
            'skip precision': header(0, [0, 0, 0x21]),
            'refine uninitialized': header(1, [1, 5, 0x10]),
            'overlap DC and AC': header(1, [0, 5, 0]),
            'reverse band': header(1, [5, 1, 0]),
            'outside band': header(1, [1, 64, 0]),
            'too high approximation': header(0, [0, 0, 14]),
            'interleaved AC': header(0, [1, 63, 0]),
            '12-bit': raw[:sof+4] + b'\x0c' + raw[sof+5:],
            'arithmetic': raw[:sof+1] + b'\xca' + raw[sof+2:],
            'lossless': raw[:sof+1] + b'\xc3' + raw[sof+2:],
            'missing EOI': raw[:-2], 'second JPEG': raw + raw,
            'executable tail': raw + b'<script>evil()</script>',
            'extra entropy': raw[:b] + b'\x00' + raw[b:],
            'truncated entropy': raw[:b] + raw[c-1:],
            'undefined AC selector': raw[:a+6] + b'\x03' + raw[a+7:],
            'undefined component': raw[:a+5] + b'\xff' + raw[a+6:],
        }
        # Header dimension/sample limits apply before any coefficient-state allocation.
        cases['oversized dimensions'] = raw[:sof+5] + b'\xff\xff\xff\xff' + raw[sof+9:]
        for label, data in cases.items():
            for flags in [(), ('--strict', '--dry-run')]:
                with self.subTest(case=label, flags=flags):
                    self.assert_invalid(data, flags)

    def test_progressive_entropy_runs_corrections_and_restart_boundaries(self):
        # Independent handcrafted two-block stream exercises EOB runs, ZRL,
        # introducing coefficients, correction bits and noninterleaved DC scans.
        data = progressive_gray()
        for valid in [data, progressive_gray(ac='0111000000', refine='0001000'),
                      progressive_gray(ac='010000000', refine='010000000')]:
            self.use_jpeg(valid)
            code, out, err = self.run_cli('--strict')
            self.assertEqual(code, 0, err)
            shutil.rmtree(self.output)
        cases = {
            'EOB beyond scan': progressive_gray(ac='0011'),  # EOB run 3 for 2 blocks.
            'first AC run beyond band': progressive_gray(ac='010'*4),
            'invalid refinement size': progressive_gray(refine='10011'),
            'short correction bits': progressive_gray(refine='011100000'),
            'zero pad': data[:-3] + bytes([data[-3] & 0xfe]) + data[-2:],
            'stuffed garbage': data[:-2] + b'\xff\x00' + data[-2:],
            'quantizer redefined after use': data[:-2] + segment(0xdb, b'\0' + bytes([2]*64)) + data[-2:],
        }
        restart = (ROOT / 'examples/archive/progressive/restart.jpg').read_bytes()
        index = restart.index(b'\xff\xd0')
        cases.update({
            'wrong restart': restart[:index+1] + b'\xd1' + restart[index+2:],
            'missing restart': restart[:index] + restart[index+2:],
            'extra restart': data[:-2] + b'\xff\xd0' + data[-2:],
            'EOB across restart': progressive_gray(ac='0010', restart=True),
        })
        for label, invalid in cases.items():
            with self.subTest(case=label):
                self.assert_invalid(invalid, ('--dry-run',))

    def test_progressive_limits_reject_before_publication(self):
        for constant, value in [('MAX_SCANS', 1), ('MAX_COEFFICIENT_BYTES', 1),
                                ('MAX_COEFFICIENT_WORK', 1), ('MAX_DECODED_BYTES', 1)]:
            with self.subTest(limit=constant), patch('notewrench.archive.jpeg.' + constant, value):
                self.assert_invalid(self.jpeg, ('--strict', '--dry-run'))
        with patch('notewrench.archive.assets.MAX_PIXELS', 1):
            self.assert_invalid(self.jpeg)

    def test_progressive_exact_scan_state_work_and_sample_caps(self):
        # The independent 16x8 grayscale stream has 2 blocks, 4 scans,
        # 128 padded samples, 16 mask bytes and 2*(1+63+1+63)=256 visits.
        data = progressive_gray()
        for constant, exact in [('MAX_SCANS', 4), ('MAX_COEFFICIENT_BYTES', 16),
                                ('MAX_COEFFICIENT_WORK', 256), ('MAX_DECODED_BYTES', 128)]:
            with self.subTest(limit=constant):
                self.use_jpeg(data)
                with patch('notewrench.archive.jpeg.' + constant, exact):
                    code, out, err = self.run_cli('--strict', '--dry-run')
                    self.assertEqual(code, 0, err)
                    self.assertFalse(self.output.exists())
                with patch('notewrench.archive.jpeg.' + constant, exact - 1):
                    self.assert_invalid(data, ('--dry-run',))

    def test_progressive_hash_missing_and_all_cli_modes(self):
        for flags in [(), ('--strict',), ('--dry-run',), ('--strict', '--dry-run')]:
            with self.subTest(flags=flags):
                self.assert_invalid(self.jpeg + b'<script>x</script>', flags)
        self.use_jpeg(self.jpeg)
        (self.root / 'images/photo.png').write_bytes(self.jpeg + b'x')
        code, out, err = self.run_cli('--dry-run')
        self.assertEqual((code, out), (2, ''))
        self.assertIn('asset_sha256', err)
        (self.root / 'images/photo.png').unlink()
        code, out, err = self.run_cli('--strict')
        self.assertEqual(code, 3, err)
        self.assertIn('missing_image_asset', [i['code'] for i in json.loads(out)['manifest']['issues']])
        self.assertFalse(self.output.exists())


def progressive_gray(ac='0010', refine='0111000000', restart=False):
    # 2 blocks; first DC/AC at Al=1, then DC and AC refinement to Al=0.
    base = gray_jpeg()
    base = base[:base.index(b'\xff\xc4')].replace(b'\xff\xc0', b'\xff\xc2')
    base += segment(0xc4, b'\0' + bytes([1]+[0]*15) + b'\0'
                    + b'\x10' + bytes([0, 0, 5]+[0]*13) + bytes([0, 0x10, 0xf0, 1, 2]))
    def scan(start, end, approximation, bits):
        bits += '1' * (-len(bits) % 8)
        entropy = int(bits, 2).to_bytes(len(bits)//8, 'big').replace(b'\xff', b'\xff\0')
        return segment(0xda, bytes([1, 1, 0, start, end, approximation])) + entropy
    result = base + scan(0, 0, 1, '00')
    if restart:
        result += segment(0xdd, b'\0\1')
    result += scan(1, 63, 1, ac) + scan(0, 0, 0x10, '00')
    # First block: introduce +1 at k=1 then EOB; second block: EOB.
    return result + scan(1, 63, 0x10, refine) + b'\xff\xd9'
