# SPDX-License-Identifier: AGPL-3.0-only
"""Build a deterministic, fictitious JEX using the public raw serialization format."""

import argparse
import io
from pathlib import Path
import struct
import tarfile
import zlib

FOLDER = 'a' * 32
CHILD = 'b' * 32
NOTE_A = '1' * 32
NOTE_B = '2' * 32
NOTE_C = '3' * 32
RESOURCE = '4' * 32
TAG = '5' * 32
RELATION = '6' * 32
MISSING = 'f' * 32


def png_bytes():
    """An original 1x1 RGB PNG; CRC and zlib stream are real, not a fake header."""
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00\x12\x34\x56')) + chunk(b'IEND', b''))


def record(item_id, kind, *, title=None, body='', **metadata):
    """Serialize synthetic records independently; title/body are not properties."""
    props = {'id': item_id, **metadata, 'type_': str(kind)}
    sections = []
    if title is not None:
        sections.append(title)
    if body:
        sections.append(body)
    sections.append('\n'.join(f'{key}: {value}' for key, value in props.items()))
    return '\n\n'.join(sections).encode('utf-8')


def sample_files():
    return {
        FOLDER + '.md': record(FOLDER, 2, title='Notebook', parent_id=''),
        CHILD + '.md': record(CHILD, 2, title='Projects', parent_id=FOLDER),
        NOTE_A + '.md': record(NOTE_A, 1, title='Plan', parent_id=FOLDER, is_todo='1', todo_completed='0',
            markup_language='1', future_field='preserve: this unknown value',
            body=f'See [other plan](:/{NOTE_B}) and [project](:/{NOTE_C}).\n\n'
                 f'![swatch](:/{RESOURCE})\n- [ ] Draft\n- [x] Reviewed\n\n'
                 f'```python\nurl = ":/{NOTE_B}"\n- [ ] code is not a task\n```\n\n'
                 f'Inline `:/{NOTE_C}` stays literal.\n[missing](:/{MISSING})'),
        NOTE_B + '.md': record(NOTE_B, 1, title='Plan', parent_id=FOLDER, is_todo='1',
            todo_completed='1700000000000', body=f'[Back](:/{NOTE_A})\n\nBody key: value\nid: this is prose'),
        NOTE_C + '.md': record(NOTE_C, 1, title='Release', parent_id=CHILD,
            body=f'Cycle: [first](:/{NOTE_A})\nRaw attachment: :/{RESOURCE}'),
        RESOURCE + '.md': record(RESOURCE, 4, title='swatch.png', mime='image/png', file_extension='png'),
        TAG + '.md': record(TAG, 5, title='work in progress'),
        RELATION + '.md': record(RELATION, 6, note_id=NOTE_A, tag_id=TAG),
        'resources/' + RESOURCE + '.png': png_bytes(),
    }


def write_jex(path, files=None):
    with tarfile.open(path, 'w', format=tarfile.USTAR_FORMAT) as archive:
        for name, data in sorted((sample_files() if files is None else files).items()):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mtime = 0
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    write_jex(args.output)
    print(args.output)


if __name__ == '__main__':
    main()
