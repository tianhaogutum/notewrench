"""Explicit, bounded local PNG/JPEG resources; never resolve source URLs on the network."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
from urllib.parse import urlsplit
import zlib

from . import BundleError
from .htmlclean import check_text, safe_url
from .inputs import _unique_object

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_TOTAL = 32 * 1024 * 1024
MAX_DECODED_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_IMAGES = 1000


def _require(condition, message):
    if not condition:
        raise BundleError(message)


def validate_png(data):
    """Bounded PNG static profile: non-interlaced, legal depths, CRCs and scanlines.

    Based on W3C PNG Second Edition (2003-11-10), not a full PNG conformance test.
    Ancillary chunks are CRC-checked but not interpreted. Animation is refused.
    """
    _require(len(data) <= MAX_IMAGE_BYTES, 'image exceeds 8 MiB')
    _require(data.startswith(b'\x89PNG\r\n\x1a\n'), 'image/png requires PNG bytes')
    position, header, palette, ended, idat_ended = 8, None, False, False, False
    compressed = []
    depths = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
    while position < len(data):
        _require(position + 12 <= len(data), 'truncated PNG chunk')
        length = struct.unpack_from('>I', data, position)[0]
        kind = data[position + 4:position + 8]
        _require(re.fullmatch(b'[A-Za-z]{4}', kind) and not kind[2] & 32, 'invalid PNG chunk type')
        end = position + 12 + length
        _require(end <= len(data), 'truncated PNG chunk')
        value = data[position + 8:end - 4]
        _require(zlib.crc32(kind + value) == struct.unpack_from('>I', data, end - 4)[0], 'invalid PNG CRC')
        _require(header is not None or kind == b'IHDR', 'PNG must start with IHDR')
        _require(kind not in {b'acTL', b'fcTL', b'fdAT'}, 'animated PNG is unsupported')
        if kind == b'IHDR':
            _require(header is None and length == 13, 'invalid/duplicate PNG IHDR')
            header = struct.unpack('>IIBBBBB', value)
            width, height, depth, color, compression, filtering, interlace = header
            _require(0 < width <= 0x7fffffff and 0 < height <= 0x7fffffff, 'invalid PNG dimensions')
            _require(color in depths and depth in depths[color], 'invalid PNG depth/color type')
            _require(compression == filtering == interlace == 0, 'PNG profile requires non-interlaced standard compression/filtering')
            channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
            stride = 1 + (width * channels * depth + 7) // 8
            expected = stride * height
            _require(expected <= MAX_DECODED_BYTES, 'PNG decoded scanlines exceed 16 MiB')
        elif kind == b'PLTE':
            _require(not palette and not compressed and color not in {0, 4}
                     and 0 < length <= 768 and length % 3 == 0, 'invalid PNG palette')
            _require(color != 3 or length // 3 <= 2 ** depth, 'PNG palette exceeds bit depth')
            palette = True
        elif kind == b'IDAT':
            _require(not idat_ended and (color != 3 or palette), 'invalid PNG IDAT order/palette')
            compressed.append(value)
        elif kind == b'IEND':
            _require(length == 0 and compressed and end == len(data), 'invalid PNG end/trailing bytes')
            ended = True
        else:
            _require(kind[0] & 32, 'unsupported critical PNG chunk')
            if compressed:
                idat_ended = True
        position = end
    _require(ended, 'missing PNG IEND')
    try:
        decoder = zlib.decompressobj()
        scanlines = decoder.decompress(b''.join(compressed), expected + 1)
        _require(len(scanlines) == expected and decoder.eof and not decoder.unused_data
                 and not decoder.unconsumed_tail, 'invalid PNG compressed scanline size/stream')
    except zlib.error as exc:
        raise BundleError(f'invalid PNG compression: {exc}') from exc
    _require(all(scanlines[i] <= 4 for i in range(0, expected, stride)), 'invalid PNG scanline filter')


def validate_jpeg(data):
    """T.81 baseline structural profile, not an entropy decoder or pixel check.

    One 8-bit SOF0, one all-component sequential Huffman scan in frame order,
    optional single pre-scan DRI and ordered RST markers, no DNL. MCU spacing cannot be
    certified without decoding. APPn/COM stay opaque. No bytes may follow EOI.
    """
    _require(len(data) <= MAX_IMAGE_BYTES, 'image exceeds 8 MiB')
    _require(data.startswith(b'\xff\xd8'), 'image/jpeg requires JPEG SOI bytes')
    position, frame, quantizers, huffman = 2, None, set(), set()
    restart_interval = None
    while position < len(data):
        _require(data[position] == 255, 'JPEG requires a marker')
        while position < len(data) and data[position] == 255:
            position += 1  # marker fill, never segment payload
        _require(position < len(data), 'truncated JPEG marker')
        marker = data[position]
        position += 1
        _require(marker in {0xc0, 0xc4, 0xda, 0xdb, 0xdd, 0xfe} or 0xe0 <= marker <= 0xef,
                 f'unsupported/out-of-order JPEG marker: {marker:02x}')
        _require(position + 2 <= len(data), 'truncated JPEG segment length')
        length = int.from_bytes(data[position:position + 2], 'big')
        _require(length >= 2 and position + length <= len(data), 'invalid/truncated JPEG segment')
        value = data[position + 2:position + length]
        position += length
        if marker == 0xdd:
            _require(length == 4 and restart_interval is None, 'invalid/duplicate JPEG DRI')
            # The exact two-byte payload bounds Ri to 0..65535; zero disables RST.
            restart_interval = int.from_bytes(value, 'big')
        elif marker == 0xdb:
            _require(bool(value), 'empty JPEG quantization segment')
            offset = 0
            while offset < len(value):
                selector = value[offset]
                _require(selector <= 3 and offset + 65 <= len(value),
                         'JPEG requires complete 8-bit quantization tables 0-3')
                _require(all(value[offset + 1:offset + 65]), 'zero JPEG quantization value')
                quantizers.add(selector)
                offset += 65
        elif marker == 0xc4:
            _require(bool(value), 'empty JPEG Huffman segment')
            offset = 0
            while offset < len(value):
                _require(offset + 17 <= len(value), 'truncated JPEG Huffman table')
                selector = value[offset]
                _require(selector in {0, 1, 0x10, 0x11}, 'invalid baseline JPEG Huffman selector')
                counts = value[offset + 1:offset + 17]
                count = sum(counts)
                _require(0 < count <= 256 and offset + 17 + count <= len(value),
                         'invalid/truncated JPEG Huffman symbols')
                # A complete tree assigns an all-ones code, forbidden by T.81.
                slots = 1
                for number in counts:
                    slots = 2 * slots - number
                    _require(slots > 0, 'oversubscribed/all-ones JPEG Huffman code')
                symbols = value[offset + 17:offset + 17 + count]
                _require(len(set(symbols)) == count, 'duplicate JPEG Huffman symbols')
                if selector < 0x10:
                    _require(all(s <= 11 for s in symbols), 'invalid baseline JPEG DC symbol')
                else:
                    _require(all(s in {0, 0xf0} or 1 <= (s & 15) <= 10 for s in symbols),
                             'invalid baseline JPEG AC symbol')
                huffman.add(selector)
                offset += 17 + count
        elif marker == 0xc0:
            _require(frame is None and len(value) >= 6, 'invalid/duplicate JPEG frame')
            precision, height, width, count = struct.unpack('>BHHB', value[:6])
            _require(precision == 8 and count in {1, 3} and len(value) == 6 + 3 * count,
                     'JPEG requires 8-bit baseline grayscale or three-component frame')
            _require(width > 0 and height > 0, 'invalid JPEG dimensions')
            _require(width * height * count <= MAX_DECODED_BYTES,
                     'JPEG dimensions exceed 16 MiB sample bound')
            frame, blocks = {}, 0
            for offset in range(6, len(value), 3):
                component, sampling, table = value[offset:offset + 3]
                horizontal, vertical = sampling >> 4, sampling & 15
                _require(component not in frame and 1 <= horizontal <= 4 and 1 <= vertical <= 4
                         and table <= 3, 'invalid JPEG component/sampling/quantizer')
                frame[component] = table
                blocks += horizontal * vertical
            _require(blocks <= 10, 'JPEG sampling exceeds ten blocks per MCU')
        elif marker == 0xda:
            _require(frame is not None and bool(value), 'JPEG scan requires a frame')
            count = value[0]
            _require(count == len(frame) and len(value) == 4 + 2 * count
                     and value[-3:] == b'\0\x3f\0', 'JPEG requires one all-component sequential scan')
            selected = []
            for offset in range(1, 1 + 2 * count, 2):
                component, selector = value[offset:offset + 2]
                _require(component in frame and component not in selected,
                         'invalid/duplicate JPEG scan component')
                _require(selector >> 4 in {0, 1} and selector & 15 in {0, 1}
                         and selector >> 4 in huffman and 0x10 | (selector & 15) in huffman,
                         'missing/invalid JPEG scan Huffman table')
                _require(frame[component] in quantizers, 'missing JPEG quantization table')
                selected.append(component)
            # T.81 B.2.3: interleaved scan order follows frame insertion order,
            # which need not be numeric component-identifier order.
            _require(selected == list(frame), 'JPEG scan component order must match frame')
            # Entropy stays opaque: check interval boundaries, never MCU spacing.
            entropy_bytes, next_restart = 0, 0
            while position < len(data):
                if data[position] != 255:
                    entropy_bytes += 1
                    position += 1
                    continue
                start = position
                while position < len(data) and data[position] == 255:
                    position += 1
                _require(position < len(data), 'truncated JPEG entropy marker')
                marker = data[position]
                position += 1
                if marker == 0:
                    _require(position - start == 2, 'invalid JPEG entropy stuffing')
                    entropy_bytes += 1
                elif 0xd0 <= marker <= 0xd7:
                    _require(restart_interval is not None and restart_interval > 0,
                             'JPEG restart requires a positive DRI interval')
                    _require(entropy_bytes > 0, 'empty JPEG restart entropy interval')
                    _require(marker == 0xd0 + next_restart, 'out-of-sequence JPEG restart marker')
                    entropy_bytes = 0
                    next_restart = (next_restart + 1) % 8
                else:
                    _require(marker == 0xd9 and entropy_bytes > 0 and position == len(data),
                             'JPEG scan requires nonempty final interval and EOI without extra scans/trailing bytes')
                    return
            raise BundleError('missing JPEG EOI')
    raise BundleError('missing JPEG scan/EOI')


def validate_image(data, media_type):
    _require(media_type in {'image/png', 'image/jpeg'}, 'only image/png and image/jpeg are supported')
    (validate_png if media_type == 'image/png' else validate_jpeg)(data)


def _local_bytes(root, name):
    """Open below root via anchored descriptors; never follow symlinks or FIFOs."""
    _require(os.name == 'posix', 'local image loading requires Linux/POSIX')
    _require(name and not name.startswith('/') and '\\' not in name
             and all(p not in {'', '.', '..'} for p in name.split('/')),
             'image path must stay below the manifest directory')
    _require(not any(ord(c) < 32 or ord(c) == 127 for c in name), 'control in image path')
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        parts = name.split('/')
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(child, 'rb') as stream:
            info = os.fstat(stream.fileno())
            _require(stat.S_ISREG(info.st_mode), 'image path must be a regular file')
            _require(info.st_size <= MAX_IMAGE_BYTES, 'image exceeds 8 MiB')
            data = stream.read(MAX_IMAGE_BYTES + 1)
            _require(len(data) <= MAX_IMAGE_BYTES, 'image exceeds 8 MiB')
            return data
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class Image:
    sha256: str
    data: bytes
    media_type: str

    @property
    def href(self):
        suffix = 'png' if self.media_type == 'image/png' else 'jpg'
        return f'images/{self.sha256}.{suffix}'


class ImageManifest:
    def __init__(self, path, article_keys):
        path = Path(path)
        with path.open('rb') as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        _require(len(raw) <= MAX_MANIFEST_BYTES, 'image manifest exceeds 1 MiB')
        try:
            data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object,
                              parse_constant=lambda x: (_ for _ in ()).throw(BundleError(f'invalid JSON constant: {x}')))
        except (ValueError, RecursionError) as exc:
            raise BundleError(f'invalid image manifest JSON: {exc}') from exc
        self._load(data, article_keys, lambda name: _local_bytes(path.resolve().parent, name))

    @classmethod
    def from_entries(cls, entries, article_keys, load_bytes):
        """Reuse declaration/media validation for an already verified archive snapshot."""
        instance = cls.__new__(cls)
        instance._load({'schema_version': 1, 'images': entries}, article_keys, load_bytes)
        return instance

    def _load(self, data, article_keys, load_bytes):
        self.declarations, self.images, self.used = {}, {}, set()
        _require(isinstance(data, dict) and set(data) == {'schema_version', 'images'}
                 and type(data['schema_version']) is int and data['schema_version'] == 1,
                 'image manifest requires schema_version 1 and images')
        entries = data['images']
        _require(isinstance(entries, list) and len(entries) <= MAX_IMAGES, 'image manifest requires at most 1000 images')
        cache, total = {}, 0
        for entry in entries:
            _require(isinstance(entry, dict) and set(entry) == {'key', 'src', 'path', 'media_type', 'sha256'}
                     and all(isinstance(v, str) for v in entry.values()), 'invalid image declaration fields')
            for value in entry.values():
                check_text(value)
            key, src = entry['key'], entry['src']
            _require(key in article_keys, f'unknown image article key: {key}')
            _require(src and not any(ord(c) <= 32 or ord(c) == 127 for c in src) and '\\' not in src,
                     'invalid image source reference')
            try:
                parts = urlsplit(src)
                _require(not parts.fragment and ((not parts.scheme and not parts.netloc)
                         or safe_url(src, mailto=False)), 'image src must be relative or HTTP(S), without fragment')
            except ValueError as exc:
                raise BundleError(f'invalid image source reference: {src}') from exc
            _require((key, src) not in self.declarations, f'duplicate image declaration: {key} {src}')
            _require(entry['media_type'] in {'image/png', 'image/jpeg'},
                     'only image/png and image/jpeg declarations are supported')
            _require(re.fullmatch(r'[0-9a-f]{64}', entry['sha256']), 'image SHA-256 must be 64 lowercase hex digits')
            name = entry['path']
            if name not in cache:
                blob = load_bytes(name)
                digest = hashlib.sha256(blob).hexdigest()
                _require(digest == entry['sha256'], f'image SHA-256 mismatch: {name}')
                validate_image(blob, entry['media_type'])
                if digest not in self.images:
                    total += len(blob)
                    _require(total <= MAX_IMAGE_TOTAL, 'images exceed 32 MiB total')
                    self.images[digest] = Image(digest, blob, entry['media_type'])
                cache[name] = digest
            _require(cache[name] == entry['sha256'], f'image SHA-256 mismatch: {name}')
            _require(self.images[entry['sha256']].media_type == entry['media_type'],
                     f'image media type mismatch: {name}')
            self.declarations[key, src] = dict(entry)

    def resolve(self, key, src):
        entry = self.declarations.get((key, src))
        if entry is None:
            return None
        self.used.add((key, src))
        return self.images[entry['sha256']].href

    def selected_images(self):
        digests = {self.declarations[key]['sha256'] for key in self.used}
        return [self.images[d] for d in sorted(digests)]

    def report(self):
        resources = [{'href': image.href, 'sha256': image.sha256, 'media_type': image.media_type,
                      'bytes': len(image.data), 'declarations': [entry for key, entry in self.declarations.items()
                             if key in self.used and entry['sha256'] == image.sha256]}
                     for image in self.selected_images()]
        unused = [entry for key, entry in self.declarations.items() if key not in self.used]
        return resources, unused
