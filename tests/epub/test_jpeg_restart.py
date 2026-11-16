"""Original DRI/RST binary contracts for the bounded opaque-entropy profile."""
import hashlib
import io
import socket
import unittest
from unittest.mock import patch

from notewrench.epub import BundleError
from notewrench.epub.epub import validate_epub
from notewrench.epub.images import validate_jpeg
from . import test_jpeg
from .test_jpeg import jpeg, jpeg_parts, segment
from .test_images import ROOT


def restart_jpeg(entropy=b'\x3f\xff\xd0\x3f', interval=1, placement=5, width=16):
    """16x8 gray by default: two zero blocks, each 00 then six one pad bits.

    The DC predictor resets at RST0. Tables are the original minimal tables in
    test_jpeg; no external encoder, downloaded photo or specification fixture.
    """
    parts = jpeg_parts(components=1, width=width)
    parts[6] = entropy
    if interval is not None:
        parts.insert(placement, segment(0xdd, interval.to_bytes(2, 'big')))
    return b''.join(parts)


def malformed_restart_jpegs():
    parts = jpeg_parts(components=1, width=16)
    prefix = b''.join(parts[:5])
    scan, eoi = parts[5], parts[7]
    dri = segment(0xdd, b'\0\1')
    header = prefix + dri + scan
    cases = {
        'orphan-rst': restart_jpeg(interval=None),
        'disabled-rst': restart_jpeg(interval=0),
        'first-rst1': restart_jpeg(b'\x3f\xff\xd1\x3f'),
        'first-rst7': restart_jpeg(b'\x3f\xff\xd7\x3f'),
        'repeat-rst0': restart_jpeg(b'\x3f\xff\xd0\x3f\xff\xd0\x3f'),
        'skip-rst1': restart_jpeg(b'\x3f\xff\xd0\x3f\xff\xd2\x3f'),
        'bad-wrap': restart_jpeg(b''.join(b'\x3f' + bytes((255, 0xd0 + n))
                                       for n in range(8)) + b'\x3f\xff\xd1\x3f', width=80),
        'empty-first': restart_jpeg(b'\xff\xd0\x3f'),
        'empty-middle': restart_jpeg(b'\x3f\xff\xd0\xff\xd1\x3f'),
        'empty-final': restart_jpeg(b'\x3f\xff\xd0'),
        'empty-only': restart_jpeg(b''),
        'truncated-rst': header + b'\x3f\xff',
        'truncated-fill': header + b'\x3f\xff\xff',
        'truncated-after-rst': header + b'\x3f\xff\xd0\xff',
        'rst-without-eoi': header + b'\x3f\xff\xd0\x3f',
        'dri-in-entropy': header + b'\x3f' + dri + b'\x3f' + eoi,
        'dri-after-rst': header + b'\x3f\xff\xd0' + dri + b'\x3f' + eoi,
        'duplicate-dri': prefix + dri * 2 + scan + b'\x3f' + eoi,
        'redefined-dri': prefix + dri + segment(0xdd, b'\0\2') + scan + b'\x3f' + eoi,
        'disabled-redefinition': prefix + dri + segment(0xdd, b'\0\0') + scan + b'\x3f' + eoi,
        'truncated-dri-length': prefix + b'\xff\xdd\0',
        'truncated-dri-interval': prefix + b'\xff\xdd\0\4\0',
        'rst-before-frame': parts[0] + b'\xff\xd0' + b''.join(parts[1:]),
        'rst-before-scan': prefix + dri + b'\xff\xd0' + scan + b'\x3f' + eoi,
        'second-scan': restart_jpeg(b'\x3f\xff\xd0\x3f' + scan + b'\x3f'),
        'scan-immediately-after-rst': restart_jpeg(b'\x3f\xff\xd0' + scan + b'\x3f'),
        'bad-stuffing-first': restart_jpeg(b'\xff\xff\0\xff\xd0\x3f'),
        'bad-stuffing-final': restart_jpeg(b'\x3f\xff\xd0\xff\xff\0'),
        'trailing-bytes': restart_jpeg() + b'x',
        'trailing-rst': restart_jpeg() + b'\xff\xd1',
        'trailing-dri': restart_jpeg() + dri,
        'concatenated-images': restart_jpeg() * 2,
        'metadata-does-not-enable-rst': parts[0] + segment(0xe1, dri)
            + restart_jpeg(interval=None)[2:],
    }
    for length in (0, 1, 2, 3, 5, 6):
        # Overlong payload also covers attempted intervals beyond 16 bits.
        cases[f'dri-length-{length}'] = (prefix + b'\xff\xdd' + length.to_bytes(2, 'big')
                                         + b'\0' * max(0, length - 2) + scan + b'\x3f' + eoi)
    return cases


class JPEGRestartTests(unittest.TestCase):
    build = test_jpeg.JPEGTests.build
    write_manifest = test_jpeg.JPEGTests.write_manifest
    rewrite = test_jpeg.JPEGTests.rewrite

    def setUp(self):
        test_jpeg.JPEGTests.setUp(self)
        self.data = restart_jpeg()
        self.image.write_bytes(self.data)
        self.digest = hashlib.sha256(self.data).hexdigest()
        for entry in self.entries:
            entry['sha256'] = self.digest
        self.write_manifest()

    def test_requested_restart_fixture(self):
        self.assertEqual((ROOT / 'examples/epub/image-resources/shared-restart.jpg').read_bytes(), self.data)
        self.assertIn(b'\xff\xdd\0\4\0\1', self.data)
        self.assertTrue(self.data.endswith(b'\x3f\xff\xd0\x3f\xff\xd9'))
        validate_jpeg(self.data)

    def test_supported_dri_placement_bounds_and_rst_wrap(self):
        for placement in range(1, 6):
            with self.subTest(placement=placement):
                validate_jpeg(restart_jpeg(placement=placement))
        for interval in (0, 1, 65535):
            with self.subTest(interval=interval):
                validate_jpeg(restart_jpeg(b'\x3f', interval=interval, width=8))
        # Interval 2: two blocks (0000 + 1111 pad), then a shorter last interval.
        validate_jpeg(restart_jpeg(b'\x0f\xff\xd0\x3f', interval=2, width=24))
        entropy = b''.join(b'\x3f' + bytes((255, 0xd0 + n % 8)) for n in range(9)) + b'\x3f'
        validate_jpeg(restart_jpeg(entropy, width=80))
        validate_jpeg(restart_jpeg(b'\x3f\xff\xff\xd0\x3f\xff'))  # RST/EOI fill
        # Stuffed FF counts as opaque data, not a restart or an empty interval.
        # This deliberately makes no claim that the resulting codes decode.
        validate_jpeg(restart_jpeg(b'\xff\0\xff\xd0\xff\0'))
        parts = jpeg_parts(width=16)
        parts.insert(5, segment(0xdd, b'\0\1'))
        parts[7] = b'\x03\xff\xd0\x03'  # two three-component MCUs
        validate_jpeg(b''.join(parts))
        # Marker-looking metadata remains opaque and cannot affect scan state.
        validate_jpeg(self.data[:2] + segment(0xfe, b'\xff\xd7\xff\xdd\0\4\0\0') + self.data[2:])
        validate_jpeg(jpeg())

    def test_malformed_restart_binary_contracts(self):
        for name, blob in malformed_restart_jpegs().items():
            with self.subTest(fixture=name), self.assertRaises(BundleError):
                validate_jpeg(blob)

    def test_shared_restart_jpeg_offline_contract(self):
        # Reuse exact one-resource/OPF/XHTML/provenance/determinism assertions.
        test_jpeg.JPEGTests.test_shared_jpeg_offline_contract(self)

    def test_matching_hash_malformed_used_and_unused_declarations(self):
        with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), \
             patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS forbidden')):
            for name, blob in malformed_restart_jpegs().items():
                self.image.write_bytes(blob)
                for unused in (False, True):
                    self.write_manifest([{**self.entries[0], 'sha256': hashlib.sha256(blob).hexdigest(),
                                          'src': '/unused.jpg' if unused else self.entries[0]['src']}])
                    with self.subTest(fixture=name, unused=unused), self.assertRaises(BundleError):
                        self.build(**({'ids': ['1:2']} if unused else {}))

    def test_validator_rechecks_restart_bytes(self):
        payload, _ = self.build()
        for name, blob in malformed_restart_jpegs().items():
            bad = self.rewrite(payload, lambda path, data: blob if path.endswith('.jpg') else data)
            with self.subTest(fixture=name), self.assertRaises(BundleError):
                validate_epub(io.BytesIO(bad))
