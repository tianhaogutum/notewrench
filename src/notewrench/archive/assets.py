"""Explicit, hash-checked local static PNG/JPEG assets. No URL or implicit file reads.

SPDX-License-Identifier: AGPL-3.0-only
"""

import errno
import hashlib
import os
from pathlib import Path
import re
import stat
import struct
import zlib

from .core import InputError, decode, issue, require
from .jpeg import static_jpeg

MAX_ASSET_BYTES = 64 * 1024 * 1024
MAX_ASSETS = 10000
MAX_PIXELS = 16 * 1024 * 1024


def _relative(name):
    require(isinstance(name, str) and bool(name) and not name.startswith('/')
            and not any(c in name for c in ('\\', ':', '\0'))
            and all(part not in ('', '.', '..') for part in name.split('/'))
            and not any(ord(c) < 32 or ord(c) == 127 for c in name),
            'local_asset_path', 'expected a local relative file without traversal')
    return name.split('/')


def _read(root_fd, name, remaining):
    """Walk directory descriptors with O_NOFOLLOW, including the final file.

    Avoid resolve-then-open races; O_NONBLOCK and fstat also reject FIFOs/devices.
    Linux is the supported CLI platform for descriptor-confined asset reads.
    """
    parts = _relative(name)
    directory = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        try:
            require(stat.S_ISREG(os.fstat(descriptor).st_mode), 'local_asset_path',
                    f'not a regular file: {name!r}')
            with os.fdopen(descriptor, 'rb', closefd=False) as handle:
                return handle.read(remaining + 1)
        finally:
            os.close(descriptor)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise InputError(f'local_asset_path: symlink or non-directory component: {name!r}') from exc
        raise
    finally:
        os.close(directory)


def _static_png(raw):
    """Check chunk framing/CRCs and bounded noninterlaced PNG scanline data.

    Reject SVG/HTML, APNG, unknown critical chunks and trailing/polyglot bytes.
    This is a conservative structural validator, not a general image decoder.
    """
    def valid(condition):
        require(condition, 'unsupported_image', 'expected a valid static noninterlaced PNG (<=16 Mi pixels, <=64 MiB decoded)')

    valid(raw.startswith(b'\x89PNG\r\n\x1a\n'))
    offset, header, palette, ended, after_data = 8, None, None, False, False
    compressed = []
    while offset < len(raw):
        valid(offset + 12 <= len(raw))
        size = struct.unpack_from('!I', raw, offset)[0]
        kind = raw[offset+4:offset+8]
        valid(offset + size + 12 <= len(raw))
        payload = raw[offset+8:offset+8+size]
        crc = struct.unpack_from('!I', raw, offset+8+size)[0]
        valid(zlib.crc32(kind + payload) == crc)
        valid(all(65 <= c <= 90 or 97 <= c <= 122 for c in kind) and 65 <= kind[2] <= 90)
        valid(kind not in (b'acTL', b'fcTL', b'fdAT'))
        valid(header is not None or kind == b'IHDR')
        offset += size + 12
        if kind == b'IHDR':
            valid(header is None and size == 13)
            width, height, depth, color, compression, filtering, interlace = struct.unpack('!2I5B', payload)
            valid(width > 0 and height > 0 and width * height <= MAX_PIXELS)
            valid(color in (0, 2, 3, 4, 6))
            valid(depth in ({1, 2, 4, 8, 16} if color == 0 else {1, 2, 4, 8} if color == 3 else {8, 16}))
            valid(compression == filtering == interlace == 0)
            header = (width, height, depth, color)
        elif kind == b'PLTE':
            valid(palette is None and not compressed and color not in (0, 4)
                  and 0 < size <= 768 and size % 3 == 0)
            valid(color != 3 or size // 3 <= 2 ** depth)
            palette = payload
        elif kind == b'IDAT':
            valid(not after_data and (color != 3 or palette is not None))
            compressed.append(payload)
        elif kind == b'IEND':
            valid(size == 0 and bool(compressed) and offset == len(raw))
            ended = True
            break
        else:
            valid(kind[0] & 32)  # Unknown critical chunks cannot be decoded safely.
            if compressed:
                after_data = True
    valid(ended)
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    stride = (width * channels * depth + 7) // 8 + 1
    expected = stride * height
    valid(expected <= MAX_ASSET_BYTES)
    try:
        stream = zlib.decompressobj()
        pixels = stream.decompress(b''.join(compressed), expected + 1)
    except zlib.error as exc:
        raise InputError('unsupported_image: corrupt PNG compressed data') from exc
    valid(len(pixels) == expected and stream.eof and not stream.unused_data and not stream.unconsumed_tail)
    valid(all(pixels[row * stride] <= 4 for row in range(height)))


class ImageAssets:
    def __init__(self, source, raw, load):
        require(isinstance(source, dict), '$assets', 'expected image manifest object')
        require(not (set(source) - {'schema_version', 'assets'}), '$assets', 'unknown manifest fields')
        require(type(source.get('schema_version')) is int and source['schema_version'] == 1,
                '$assets.schema_version', 'expected 1')
        declarations = source.get('assets')
        require(isinstance(declarations, list) and len(declarations) <= MAX_ASSETS,
                '$assets.assets', 'expected array of at most 10000 asset descriptors')
        references = set()
        # Validate ALL descriptors before opening any declared resource.
        for index, item in enumerate(declarations):
            path = f'$assets.assets[{index}]'
            require(isinstance(item, dict) and set(item) == {'reference', 'file', 'sha256'}, path,
                    'expected reference, file and sha256 fields only')
            ref = item['reference']
            require(isinstance(ref, str) and bool(ref), path + '.reference', 'expected nonempty exact img src string')
            require(ref not in references, 'duplicate_asset_reference', f'repeated reference: {ref!r}')
            references.add(ref)
            _relative(item['file'])
            require(isinstance(item['sha256'], str) and re.fullmatch('[0-9a-f]{64}', item['sha256']),
                    path + '.sha256', 'expected lowercase SHA-256 hex')
        self.source = source
        self.input_sha256 = hashlib.sha256(raw).hexdigest()
        self.files, self.mapping, self.by_reference, self.issues = {}, [], {}, []
        remaining = MAX_ASSET_BYTES - len(raw)
        require(remaining >= 0, '$assets', 'assets exceed aggregate 64 MiB limit')
        cache, validated = {}, {}
        for index, item in enumerate(declarations):
            name = item['file']
            if name not in cache:
                cache[name] = load(name, remaining)
                if cache[name] is not None:
                    remaining -= len(cache[name])
                    require(remaining >= 0, '$assets', 'manifest and image files exceed aggregate 64 MiB limit')
            data = cache[name]
            record = {'asset_index': index, 'reference': item['reference'], 'source_file': name,
                      'expected_sha256': item['sha256'], 'file': None, 'sha256': None,
                      'status': 'missing_file'}
            if data is None:
                self.issues.append(issue('missing_asset_file', None,
                                         'Declared local image is missing; no fetch attempted.', asset_index=index,
                                         reference=item['reference'], source_file=name))
            else:
                digest = hashlib.sha256(data).hexdigest()
                require(digest == item['sha256'], 'asset_sha256', f'image hash mismatch: {name!r}')
                if digest not in validated:
                    if data.startswith(b'\xff\xd8'):
                        static_jpeg(data, MAX_PIXELS)
                        validated[digest] = ('jpg', 'image/jpeg')
                    else:
                        _static_png(data)
                        validated[digest] = ('png', 'image/png')
                extension, media_type = validated[digest]
                filename = f'asset-{digest}.{extension}'
                self.files[filename] = data
                record.update(file=filename, sha256=digest, bytes=len(data), media_type=media_type, status='archived')
            self.mapping.append(record)
            self.by_reference[item['reference']] = record

    def resolve(self, reference):
        record = self.by_reference.get(reference)
        return ({'reference': reference, 'status': record['status'], 'file': record['file'],
                 'sha256': record['sha256'], 'asset_index': record['asset_index']} if record else
                {'reference': reference, 'status': 'undeclared', 'file': None, 'sha256': None})

    def provenance(self):
        return {'schema_version': 1, 'input_sha256': self.input_sha256,
                'source': self.source, 'mapping': self.mapping}


def load_image_assets(path: Path):
    # The explicitly selected manifest directory is the confinement root. Refuse
    # a symlink for the manifest itself as well as every declared file component.
    root_fd = os.open(path.absolute().parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        raw = _read(root_fd, path.name, MAX_ASSET_BYTES)
        require(raw is not None, 'local_asset_path', 'image manifest is missing')
        return ImageAssets(decode(raw), raw, lambda name, remaining: _read(root_fd, name, remaining))
    finally:
        os.close(root_fd)
