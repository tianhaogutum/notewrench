"""Original baseline JPEG fixtures and explicit offline-manifest contracts."""
import hashlib
import io
import json
import socket
import struct
import subprocess
import sys
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
import zipfile

from notewrench.epub import BundleError
from notewrench.epub.epub import OPF, XHTML, q, validate_epub
from . import test_images


def segment(marker, value):
    return bytes((255, marker)) + struct.pack('>H', len(value) + 2) + value


def jpeg_parts(components=3, width=8, height=8):
    """Constant 128 samples: DC difference zero + AC EOB for each 8x8 block.

    Original minimal tables: sole one-bit code 0 for category 0 / EOB;
    all-one quantization. The unused all-one Huffman code is never assigned.
    """
    frame = struct.pack('>BHHB', 8, height, width, components)
    frame += b''.join(bytes((n, 0x11, 0)) for n in range(1, components + 1))
    scan = bytes((components,)) + b''.join(bytes((n, 0)) for n in range(1, components + 1)) + b'\0\x3f\0'
    table = b'\x01' + b'\0' * 15 + b'\0'
    return [b'\xff\xd8', segment(0xe0, b'JFIF\0\x01\x01\0\0\x01\0\x01\0\0'),
            segment(0xdb, b'\0' + b'\x01' * 64), segment(0xc0, frame),
            segment(0xc4, b'\0' + table + b'\x10' + table), segment(0xda, scan),
            b'\x03' if components == 3 else b'\x3f', b'\xff\xd9']


def jpeg(**options):
    return b''.join(jpeg_parts(**options))


def malformed_jpegs():
    parts = jpeg_parts()

    def changed(index, blob):
        return b''.join(parts[:index] + [blob] + parts[index + 1:])

    frame = parts[3][4:]
    scan = parts[5][4:]
    cases = {
        'signature-only': b'\xff\xd8\xff\xd9',
        'missing-soi': b''.join(parts[1:]),
        'duplicate-soi': parts[0] + jpeg(),
        'truncated-length': parts[0] + b'\xff\xe0\0',
        'short-length': changed(1, b'\xff\xe0\0\x01'),
        'overrun-length': changed(1, b'\xff\xe0\xff\xffx'),
        'non-marker-data': changed(1, b'junk'),
        'stuffing-outside-scan': changed(1, b'\xff\0'),
        'unsupported-marker': changed(1, segment(0xf0, b'')),
        'duplicate-frame': changed(3, parts[3] * 2),
        'progressive': changed(3, segment(0xc2, frame)),
        'arithmetic': changed(3, segment(0xc9, frame)),
        'precision': changed(3, segment(0xc0, b'\x0c' + frame[1:])),
        'frame-length': changed(3, segment(0xc0, frame[:-1])),
        'zero-width': jpeg(width=0),
        'zero-height': jpeg(height=0),
        'oversized-dimensions': jpeg(width=65535, height=65535),
        'four-components': jpeg(components=4),
        'duplicate-component': changed(3, segment(0xc0, frame[:9] + b'\x01' + frame[10:])),
        'zero-sampling': changed(3, segment(0xc0, frame[:7] + b'\0' + frame[8:])),
        'huge-sampling': changed(3, segment(0xc0, frame[:7] + b'\x44' + frame[8:])),
        'missing-quantizer': changed(2, b''),
        'empty-quantizer': changed(2, segment(0xdb, b'')),
        'zero-quantizer': changed(2, segment(0xdb, b'\0' * 65)),
        '16bit-quantizer': changed(2, segment(0xdb, b'\x10' + b'\1' * 128)),
        'truncated-quantizer': changed(2, segment(0xdb, b'\0' + b'\1' * 63)),
        'missing-huffman': changed(4, b''),
        'empty-huffman': changed(4, segment(0xc4, b'')),
        'truncated-huffman': changed(4, segment(0xc4, b'\0\1')),
        'oversubscribed-huffman': changed(4, segment(0xc4, b'\0\3' + b'\0' * 15 + b'\0\1\2')),
        'all-ones-huffman-code': changed(4, segment(0xc4, b'\0\2' + b'\0' * 15 + b'\0\1')),
        'bad-dc-symbol': changed(4, segment(0xc4, b'\0\1' + b'\0' * 15 + b'\x0c')),
        'bad-ac-symbol': changed(4, segment(0xc4, b'\x10\1' + b'\0' * 15 + b'\x0b')),
        'invalid-table-selector': changed(4, segment(0xc4, b'\2\1' + b'\0' * 16)),
        'duplicate-huffman-symbols': changed(4, segment(0xc4, b'\0\0\2' + b'\0' * 14 + b'\0\0')),
        'bad-ac-zero-size': changed(4, segment(0xc4, b'\x10\1' + b'\0' * 15 + b'\x10')),
        'invalid-entropy-stuffing': changed(6, b'\3\xff\xff\0'),
        'scan-before-frame': b''.join(parts[:3] + [parts[5], parts[3], parts[4]] + parts[6:]),
        'missing-scan': changed(5, b''),
        'scan-length': changed(5, segment(0xda, scan[:-1])),
        'scan-missing-component': changed(5, segment(0xda, b'\1\1\0\0\x3f\0')),
        'scan-duplicate-component': changed(5, segment(0xda, scan[:3] + b'\1' + scan[4:])),
        'scan-unknown-table': changed(5, segment(0xda, scan[:2] + b'\x11' + scan[3:])),
        'nonsequential-scan': changed(5, segment(0xda, scan[:-3] + b'\1\x3f\0')),
        'empty-scan-data': changed(6, b''),
        'restart-without-dri': changed(6, b'\3\xff\xd0\3'),
        'invalid-dri-length': changed(1, segment(0xdd, b'\1')),
        'truncated-entropy-marker': changed(6, b'\3\xff')[:-2],
        'second-scan': changed(6, b'\3' + parts[5] + b'\3'),
        'missing-eoi': jpeg()[:-2],
        'trailing-html': jpeg() + b'<script>evil</script>',
        'concatenated-images': jpeg() * 2,
    }
    return cases


class JPEGTests(unittest.TestCase):
    build = test_images.ImageTests.build
    write_manifest = test_images.ImageTests.write_manifest
    rewrite = test_images.ImageTests.rewrite

    def setUp(self):
        test_images.ImageTests.setUp(self)
        self.data = jpeg()
        self.image.write_bytes(self.data)
        self.digest = hashlib.sha256(self.data).hexdigest()
        for entry in self.entries:
            entry.update(media_type='image/jpeg', sha256=self.digest)
        self.write_manifest()

    def test_shared_jpeg_offline_contract(self):
        # Different paths and remote source labels still mean one content resource.
        (self.assets / 'copy.data').write_bytes(self.data)
        self.entries[1].update(path='copy.data', src='https://cdn.invalid/photo.jpg')
        self.rows[1]['content'] = '<img src="https://cdn.invalid/photo.jpg" alt="图 2">'
        self.source.write_text(json.dumps(self.rows))
        self.write_manifest()
        with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), \
             patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS forbidden')):
            payload, report = self.build()
            self.write_manifest(list(reversed(self.entries)))
            self.assertEqual(self.build()[0], payload)
            self.assertEqual(validate_epub(io.BytesIO(payload))['chapters'], 2)
        href = f'images/{self.digest}.jpg'
        with zipfile.ZipFile(io.BytesIO(payload)) as book:
            self.assertEqual(len(book.namelist()), 7)
            self.assertEqual(book.read('EPUB/' + href), self.data)
            opf = ET.fromstring(book.read('EPUB/package.opf'))
            images = [i for i in opf.find(q(OPF, 'manifest')) if i.get('media-type') == 'image/jpeg']
            self.assertEqual([i.get('href') for i in images], [href])
            for n in (1, 2):
                chapter = ET.fromstring(book.read(f'EPUB/chapter-{n:04d}.xhtml'))
                self.assertEqual(chapter.find('.//' + q(XHTML, 'img')).attrib, {'src': href, 'alt': f'图 {n}'})
        resource, = report['resources']
        self.assertEqual((resource['media_type'], resource['href'], resource['sha256'], resource['bytes']),
                         ('image/jpeg', href, self.digest, len(self.data)))
        self.assertEqual(len(resource['declarations']), 2)

    def test_baseline_marker_segment_and_dimension_contracts(self):
        from notewrench.epub.images import validate_jpeg
        for components in (1, 3):
            with self.subTest(components=components):
                validate_jpeg(jpeg(components=components))
        # Metadata is bounded but opaque; FF bytes there must not become markers.
        parts = jpeg_parts()
        validate_jpeg(parts[0] + segment(0xe1, b'Exif\0\0\xff\xd9') + b''.join(parts[1:]))
        validate_jpeg(parts[0] + b'\xff' + b''.join(parts[1:]))  # legal marker fill
        # One 4:2:0 MCU has six zero blocks (12 bits), then four one pad bits.
        subsampled = jpeg_parts()
        frame = subsampled[3][4:]
        subsampled[3] = segment(0xc0, frame[:7] + b'\x22' + frame[8:])
        subsampled[6] = b'\0\x0f'
        validate_jpeg(b''.join(subsampled))
        with patch('notewrench.epub.images.MAX_IMAGE_BYTES', len(jpeg()) - 1):
            with self.assertRaisesRegex(BundleError, '8 MiB'):
                validate_jpeg(jpeg())
        with patch('notewrench.epub.images.MAX_DECODED_BYTES', 191):
            with self.assertRaisesRegex(BundleError, '16 MiB'):
                validate_jpeg(jpeg())
        with patch('notewrench.epub.images.MAX_DECODED_BYTES', 192):
            validate_jpeg(jpeg())
        for name, data in malformed_jpegs().items():
            with self.subTest(fixture=name), self.assertRaises(BundleError):
                validate_jpeg(data)

    def test_forged_media_and_cached_declarations_reject(self):
        for blob, media_type in ((self.data, 'image/png'), (test_images.png(), 'image/jpeg'),
                                 (b'\xff\xd8<svg/>\xff\xd9', 'image/jpeg')):
            self.image.write_bytes(blob)
            digest = hashlib.sha256(blob).hexdigest()
            for same_path in (True, False):
                (self.assets / 'copy.data').write_bytes(blob)
                self.write_manifest([{**self.entries[0], 'sha256': digest},
                                     {**self.entries[1], 'sha256': digest, 'media_type': media_type,
                                      'path': 'shared.bin' if same_path else 'copy.data'}])
                with self.subTest(media_type=media_type, same_path=same_path), self.assertRaises(BundleError):
                    self.build()

    def test_unused_malformed_jpeg_declarations_also_reject(self):
        for name, blob in malformed_jpegs().items():
            self.image.write_bytes(blob)
            self.write_manifest([{**self.entries[0], 'src': '/unused.jpg',
                                  'sha256': hashlib.sha256(blob).hexdigest()}])
            with self.subTest(fixture=name), self.assertRaises(BundleError):
                self.build(ids=['1:2'])

    def test_png_and_jpeg_resources_coexist(self):
        png_bytes = test_images.png()
        (self.assets / 'other.data').write_bytes(png_bytes)
        png_digest = hashlib.sha256(png_bytes).hexdigest()
        self.entries[1].update(path='other.data', media_type='image/png', sha256=png_digest)
        self.write_manifest()
        payload, report = self.build()
        self.assertEqual({r['media_type'] for r in report['resources']}, {'image/png', 'image/jpeg'})
        with zipfile.ZipFile(io.BytesIO(payload)) as book:
            self.assertEqual(len(book.namelist()), 8)
            self.assertEqual(book.read(f'EPUB/images/{self.digest}.jpg'), self.data)
            self.assertEqual(book.read(f'EPUB/images/{png_digest}.png'), png_bytes)
        self.assertTrue(validate_epub(io.BytesIO(payload))['valid'])

    def test_validator_rechecks_jpeg_bytes_and_declared_type(self):
        payload, _ = self.build()
        for name, bad_bytes in malformed_jpegs().items():
            bad = self.rewrite(payload, lambda path, blob: bad_bytes if path.endswith('.jpg') else blob)
            with self.subTest(fixture=name), self.assertRaises(BundleError):
                validate_epub(io.BytesIO(bad))
        for new_type in (b'image/png', b'image/gif'):
            bad = self.rewrite(payload, lambda path, blob: blob.replace(b'image/jpeg', new_type)
                               if path.endswith('.opf') else blob)
            with self.subTest(new_type=new_type), self.assertRaises(BundleError):
                validate_epub(io.BytesIO(bad))

    def test_cli_rejects_matching_hash_forgery_without_outputs(self):
        self.image.write_bytes(jpeg(width=0))
        self.write_manifest([{**self.entries[0], 'sha256': hashlib.sha256(self.image.read_bytes()).hexdigest()}])
        output = self.work / 'bad.epub'
        result = subprocess.run([sys.executable, '-m', 'notewrench', 'epub', 'build', str(self.source),
                                 '--image-manifest', str(self.manifest), '-o', str(output)],
                                cwd=test_images.ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 2)
        self.assertIn('JPEG dimensions', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse(output.exists())
        self.assertFalse(output.with_suffix('.report.json').exists())
