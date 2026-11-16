"""Exact SOF0/SOS identifier-order contracts; entropy remains opaque."""
import hashlib
import io
from itertools import permutations
import socket
import unittest
from unittest.mock import patch

from notewrench.epub import BundleError
from notewrench.epub.epub import validate_epub
from notewrench.epub.images import validate_jpeg
from . import test_jpeg
from .test_jpeg import jpeg_parts, segment


def interleaved_jpeg(frame_ids=(1, 2, 3), scan_ids=None, restart=False):
    """Original constant-128 fixture: one MCU, or two with interval-1/RST0.

    Each MCU is three 1x1 zero blocks: six zero bits, then two one pad bits.
    Only identifiers differ from jpeg_parts(); all table references stay zero.
    """
    if scan_ids is None:
        scan_ids = frame_ids
    parts = jpeg_parts(width=16 if restart else 8)
    parts[3] = segment(0xc0, parts[3][4:10]
                       + b''.join(bytes((n, 0x11, 0)) for n in frame_ids))
    parts[5] = segment(0xda, bytes((len(scan_ids),))
                       + b''.join(bytes((n, 0)) for n in scan_ids) + b'\0\x3f\0')
    if restart:
        parts[6] = b'\x03\xff\xd0\x03'
        parts.insert(5, segment(0xdd, b'\0\1'))
    return b''.join(parts)


def malformed_order_jpegs():
    cases = {}
    for ids in ((1, 2, 3), (9, 2, 7)):
        scans = {f'permutation-{scan}': scan for scan in permutations(ids) if scan != ids}
        scans.update(duplicate=(ids[0], ids[0], ids[2]),
                     unknown=(ids[0], ids[1], 255), missing=ids[:2])
        for restart in (False, True):
            for name, scan in scans.items():
                cases[f'{ids}-restart-{restart}-{name}'] = interleaved_jpeg(ids, scan, restart)
    return cases


class JPEGOrderTests(unittest.TestCase):
    build = test_jpeg.JPEGTests.build
    write_manifest = test_jpeg.JPEGTests.write_manifest
    rewrite = test_jpeg.JPEGTests.rewrite
    setUp = test_jpeg.JPEGTests.setUp

    def test_requested_reversed_scan_rejects(self):
        malformed = interleaved_jpeg(scan_ids=(3, 2, 1))
        self.assertEqual(hashlib.sha256(malformed).hexdigest(),
                         'd5a2174382b79d20a33d2fad6d889366d8995f990fae299b9bc9e8b7d2733c80')
        self.assertIn(segment(0xda, bytes.fromhex('03030002000100003f00')), malformed)
        with self.assertRaisesRegex(BundleError, 'JPEG scan component order'):
            validate_jpeg(malformed)

    def test_frame_insertion_order_not_numeric_sort(self):
        for ids in ((1, 2, 3), (9, 2, 7), (255, 0, 1)):
            for scan in permutations(ids):
                for restart in (False, True):
                    with self.subTest(frame=ids, scan=scan, restart=restart):
                        blob = interleaved_jpeg(ids, scan, restart)
                        if scan == ids:
                            validate_jpeg(blob)
                        else:
                            with self.assertRaisesRegex(BundleError, 'JPEG scan component order'):
                                validate_jpeg(blob)

    def test_duplicate_unknown_missing_components_reject(self):
        for name, blob in malformed_order_jpegs().items():
            with self.subTest(fixture=name), self.assertRaises(BundleError):
                validate_jpeg(blob)

    def test_matching_hash_used_unused_and_filtered_declarations_reject(self):
        with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), \
             patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS forbidden')):
            for name, blob in malformed_order_jpegs().items():
                self.image.write_bytes(blob)
                for mode in ('used', 'unused-src', 'filtered-article'):
                    self.write_manifest([{**self.entries[0], 'sha256': hashlib.sha256(blob).hexdigest(),
                                          'src': '/unused.jpg' if mode == 'unused-src' else self.entries[0]['src']}])
                    with self.subTest(fixture=name, mode=mode), self.assertRaises(BundleError):
                        self.build(**({'ids': ['1:2']} if mode == 'filtered-article' else {}))

    def test_independent_epub_resource_recheck(self):
        payload, _ = self.build()
        for name, blob in malformed_order_jpegs().items():
            # Keep the old resource name: validation must parse bytes itself.
            bad = self.rewrite(payload, lambda path, data: blob if path.endswith('.jpg') else data)
            with self.subTest(fixture=name), self.assertRaises(BundleError):
                validate_epub(io.BytesIO(bad))

    def test_ordered_arbitrary_ids_shared_offline_deterministic(self):
        for restart in (False, True):
            with self.subTest(restart=restart):
                self.data = interleaved_jpeg((9, 2, 7), restart=restart)
                self.image.write_bytes(self.data)
                self.digest = hashlib.sha256(self.data).hexdigest()
                for entry in self.entries:
                    entry['sha256'] = self.digest
                self.write_manifest()
                # Exact bytes, one OPF item, two local XHTML references, provenance,
                # reversed declaration order determinism and blocked socket/DNS.
                test_jpeg.JPEGTests.test_shared_jpeg_offline_contract(self)

    def test_single_all_component_scan_and_opaque_entropy_bounds(self):
        for restart in (False, True):
            blob = interleaved_jpeg((9, 2, 7), restart=restart)
            # FF00 is opaque data; this does not assert coefficient validity.
            validate_jpeg(blob.replace(b'\x03\xff', b'\xff\0\xff'))
            extra_scan = segment(0xda, bytes.fromhex('03090002000700003f00'))
            with self.subTest(restart=restart), self.assertRaises(BundleError):
                validate_jpeg(blob[:-2] + extra_scan + b'\x03\xff\xd9')
