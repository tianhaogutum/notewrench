"""Bounded baseline/progressive JPEG syntax validation; no pixel reconstruction or execution.

SPDX-License-Identifier: AGPL-3.0-only
"""
from array import array

from .core import require

MAX_DECODED_BYTES = 64 * 1024 * 1024
MAX_SCANS = 128
MAX_COEFFICIENT_BYTES = 8 * 1024 * 1024
MAX_COEFFICIENT_WORK = 64 * 1024 * 1024


def _valid(condition):
    require(condition, 'unsupported_image',
            'expected a complete supported 8-bit baseline/progressive grayscale/three-component JPEG '
            '(<=16 Mi pixels, <=64 MiB decoded samples)')


class _Entropy:
    """Read stuffed scan bytes without consuming markers as coefficient bits."""
    def __init__(self, raw, offset):
        self.raw, self.offset = raw, offset
        self.buffer, self.bits = 0, 0

    def take(self, count):
        while self.bits < count:
            _valid(self.offset < len(self.raw))
            value = self.raw[self.offset]
            self.offset += 1
            if value == 255:
                _valid(self.offset < len(self.raw) and self.raw[self.offset] == 0)
                self.offset += 1
            self.buffer = (self.buffer << 8) | value
            self.bits += 8
        self.bits -= count
        value = self.buffer >> self.bits
        self.buffer &= (1 << self.bits) - 1
        return value

    def symbol(self, table):
        code = 0
        for length in range(1, 17):
            code = (code << 1) | self.take(1)
            symbol = table[length].get(code)
            if symbol is not None:
                return symbol
        _valid(False)

    def finish(self):
        _valid(self.buffer == (1 << self.bits) - 1)
        self.buffer, self.bits = 0, 0
        return self.offset

    def marker(self, expected):
        # Only 1-fill bits may remain after the last coefficient of an interval.
        self.finish()
        _valid(self.offset < len(self.raw) and self.raw[self.offset] == 255)
        while self.offset < len(self.raw) and self.raw[self.offset] == 255:
            self.offset += 1
        _valid(self.offset < len(self.raw) and self.raw[self.offset] == expected)
        self.offset += 1


def _scan(raw, offset, width, height, components, selectors, tables, restart):
    hmax = max(c[0] for c in components.values())
    vmax = max(c[1] for c in components.values())
    columns = (width + 8 * hmax - 1) // (8 * hmax)
    rows = (height + 8 * vmax - 1) // (8 * vmax)
    blocks = [(tables[(0, dc)], tables[(1, ac)])
              for identity, dc, ac in selectors
              for _ in range(components[identity][0] * components[identity][1])]
    count = columns * rows
    _valid(count * len(blocks) * 64 <= MAX_DECODED_BYTES)
    stream = _Entropy(raw, offset)
    restart_index = 0
    for mcu in range(count):
        if restart and mcu and mcu % restart == 0:
            stream.marker(0xd0 + restart_index % 8)
            restart_index += 1
        for dc, ac in blocks:
            stream.take(stream.symbol(dc))  # DC difference amplitude (0..11 bits).
            position = 1
            while position < 64:
                symbol = stream.symbol(ac)
                run, size = symbol >> 4, symbol & 15
                if size == 0:
                    if run == 0:  # End of block.
                        break
                    _valid(run == 15)
                    position += 16  # The only other size-zero symbol is ZRL.
                    _valid(position <= 64)
                else:
                    position += run
                    _valid(position < 64)
                    stream.take(size)
                    position += 1
    stream.marker(0xd9)
    _valid(stream.offset == len(raw))


class _Progressive:
    """Track band precision and one nonzero bit per AC coefficient per block.

    Refinement entropy depends on zero history, not coefficient values/signs.
    Compact masks avoid allocating a Python object for every coefficient.
    """
    def __init__(self, width, height, components):
        self.components = components
        hmax = max(c[0] for c in components.values())
        vmax = max(c[1] for c in components.values())
        self.columns = (width + 8 * hmax - 1) // (8 * hmax)
        self.rows = (height + 8 * vmax - 1) // (8 * vmax)
        self.layout = {}
        total = 0
        for identity, (h, v, _) in components.items():
            stride, rows = self.columns * h, self.rows * v
            actual_columns = (width * h + 8 * hmax - 1) // (8 * hmax)
            actual_rows = (height * v + 8 * vmax - 1) // (8 * vmax)
            self.layout[identity] = stride, actual_columns, actual_rows
            total += stride * rows
        _valid(total * 64 <= MAX_DECODED_BYTES)
        _valid(total * array('Q').itemsize <= MAX_COEFFICIENT_BYTES)
        self.masks = {c: array('Q', [0]) * (self.columns * h * self.rows * v)
                      for c, (h, v, _) in components.items()}
        self.precision = {c: [-1] * 64 for c in components}
        self.scans, self.work = 0, 0

    def complete(self):
        return all(all(bit == 0 for bit in bands) for bands in self.precision.values())

    def blocks(self, mcu, selectors):
        if len(selectors) == 1:
            c, dc, ac = selectors[0]
            stride, columns, _ = self.layout[c]
            yield c, (mcu // columns) * stride + mcu % columns, dc, ac
        else:
            for c, dc, ac in selectors:
                h, v, _ = self.components[c]
                stride = self.layout[c][0]
                for y in range(v):
                    for x in range(h):
                        yield c, (mcu // self.columns * v + y) * stride + mcu % self.columns * h + x, dc, ac

    def scan(self, raw, offset, selectors, tables, restart, start, end, approximation):
        high, low = approximation >> 4, approximation & 15
        _valid(0 <= start <= end <= 63 and high <= 13 and low <= 13)
        _valid((start == 0 and end == 0) or (start > 0 and len(selectors) == 1))
        _valid(high == 0 or high == low + 1)
        for c, dc, ac in selectors:
            bands = self.precision[c]
            _valid(start == 0 or bands[0] >= 0)
            _valid(all(bands[k] == (-1 if high == 0 else high) for k in range(start, end + 1)))
            # Unused selector nibbles must be zero (T.81 scan header contract).
            _valid((ac == 0 and (high != 0 or (0, dc) in tables)) if start == 0
                   else (dc == 0 and (1, ac) in tables))
            _valid(start != 0 or high == 0 or dc == 0)
        if len(selectors) == 1:
            _, columns, rows = self.layout[selectors[0][0]]
            count, blocks = columns * rows, 1
        else:
            count = self.columns * self.rows
            blocks = sum(self.components[c][0] * self.components[c][1] for c, _, _ in selectors)
        self.scans += 1
        self.work += count * blocks * (end - start + 1)
        _valid(self.scans <= MAX_SCANS and self.work <= MAX_COEFFICIENT_WORK)
        for c, _, _ in selectors:
            self.precision[c][start:end+1] = [low] * (end - start + 1)
        stream, eob, restart_index = _Entropy(raw, offset), 0, 0
        for mcu in range(count):
            if restart and mcu and mcu % restart == 0:
                _valid(eob == 0)
                stream.marker(0xd0 + restart_index % 8)
                restart_index += 1
            for c, block, dc, ac in self.blocks(mcu, selectors):
                if start == 0:
                    stream.take(1 if high else stream.symbol(tables[(0, dc)]))
                else:
                    mask, eob = _progressive_ac(stream, tables[(1, ac)], self.masks[c][block],
                                                start, end, bool(high), eob)
                    self.masks[c][block] = mask
        _valid(eob == 0)
        return stream.finish()


def _progressive_ac(stream, table, mask, start, end, refine, eob):
    position = start
    if not eob:
        while position <= end:
            symbol = stream.symbol(table)
            run, size = symbol >> 4, symbol & 15
            if size:
                _valid(not refine or size == 1)
                stream.take(size)  # Sign/amplitude; only zero history is needed.
            elif run != 15:
                eob = (1 << run) + stream.take(run)
                break
            else:
                run = 16
            while position <= end:
                if mask & (1 << position):
                    _valid(refine)
                    stream.take(1)  # Correction for an already nonzero coefficient.
                    position += 1
                elif run:
                    run -= 1
                    position += 1
                    if not size and run == 0:
                        break
                else:
                    break
            _valid(run == 0)
            if size:
                _valid(position <= end)
                mask |= 1 << position
                position += 1
    if eob:
        if refine:
            while position <= end:
                if mask & (1 << position):
                    stream.take(1)
                position += 1
        eob -= 1
    return mask, eob


def static_jpeg(raw, max_pixels):
    """Validate SOF0 single-scan or bounded SOF2 progressive Huffman syntax.

    Annex B/G of ITU-T T.81 governs progressive scan and entropy syntax.
    All progressive bands must reach full precision, an explicit stricter subset.
    APP/COM are opaque; neither path reconstructs pixels or interprets metadata.
    """
    _valid(raw.startswith(b'\xff\xd8'))
    offset, frame, restart = 2, None, 0
    quantizers, tables = set(), {}
    progressive, used_quantizers = None, set()
    while offset < len(raw):
        _valid(raw[offset] == 255)
        while offset < len(raw) and raw[offset] == 255:
            offset += 1
        _valid(offset < len(raw))
        marker = raw[offset]
        offset += 1
        if marker == 0xd9:
            _valid(progressive is not None and progressive.complete() and offset == len(raw))
            return
        _valid(marker in (0xc0, 0xc2, 0xc4, 0xda, 0xdb, 0xdd, 0xfe) or 0xe0 <= marker <= 0xef)
        _valid(offset + 2 <= len(raw))
        length = int.from_bytes(raw[offset:offset+2], 'big')
        _valid(length >= 2 and offset + length <= len(raw))
        payload = raw[offset+2:offset+length]
        offset += length
        if marker == 0xdb:
            _valid(len(payload) > 0)
            cursor = 0
            while cursor < len(payload):
                identity = payload[cursor]
                _valid(identity <= 3 and identity not in used_quantizers and cursor + 65 <= len(payload))
                _valid(all(payload[cursor+1:cursor+65]))
                quantizers.add(identity)
                cursor += 65
        elif marker == 0xc4:
            _valid(len(payload) > 0)
            cursor = 0
            while cursor < len(payload):
                _valid(cursor + 17 <= len(payload))
                kind, identity = payload[cursor] >> 4, payload[cursor] & 15
                _valid(kind in (0, 1) and identity <= 3)
                counts = payload[cursor+1:cursor+17]
                size = sum(counts)
                _valid(0 < size <= 256 and cursor + 17 + size <= len(payload))
                symbols = payload[cursor+17:cursor+17+size]
                _valid(len(set(symbols)) == len(symbols))
                _valid(all(s <= 11 if kind == 0 else
                           (s & 15 <= 10) for s in symbols))
                table = [{} for _ in range(17)]
                code, index = 0, 0
                for bits, count in enumerate(counts, 1):
                    # Oversubscribed trees and an all-ones code are invalid.
                    _valid(code + count < 1 << bits)
                    for _ in range(count):
                        table[bits][code] = symbols[index]
                        code, index = code + 1, index + 1
                    code <<= 1
                tables[(kind, identity)] = table
                cursor += 17 + size
        elif marker in (0xc0, 0xc2):
            _valid(frame is None and len(payload) >= 6)
            precision = payload[0]
            height = int.from_bytes(payload[1:3], 'big')
            width = int.from_bytes(payload[3:5], 'big')
            count = payload[5]
            _valid(precision == 8 and width > 0 and height > 0 and width * height <= max_pixels)
            _valid(count in (1, 3) and len(payload) == 6 + 3 * count)
            _valid(width * height * count <= MAX_DECODED_BYTES)
            components = {}
            for cursor in range(6, len(payload), 3):
                identity, sampling, quantizer = payload[cursor:cursor+3]
                h, v = sampling >> 4, sampling & 15
                _valid(identity not in components and h in (1, 2) and v in (1, 2) and quantizer <= 3)
                components[identity] = (h, v, quantizer)
            _valid(sum(c[0] * c[1] for c in components.values()) <= 10)
            _valid(count != 1 or next(iter(components.values()))[:2] == (1, 1))
            frame = width, height, components
            if marker == 0xc2:
                progressive = _Progressive(*frame)
        elif marker == 0xdd:
            _valid(len(payload) == 2)
            restart = int.from_bytes(payload, 'big')
        elif marker == 0xda:
            _valid(frame is not None and len(payload) >= 1)
            width, height, components = frame
            count = payload[0]
            _valid(1 <= count <= len(components) and len(payload) == 4 + count * 2)
            selectors = [(payload[c], payload[c+1] >> 4, payload[c+1] & 15)
                         for c in range(1, 1 + count * 2, 2)]
            _valid(len({c[0] for c in selectors}) == count)
            _valid(all(c in components and dc <= 3 and ac <= 3 for c, dc, ac in selectors))
            _valid(all(components[c][2] in quantizers for c, _, _ in selectors))
            used_quantizers.update(components[c][2] for c, _, _ in selectors)
            if progressive is not None:
                offset = progressive.scan(raw, offset, selectors, tables, restart, *payload[-3:])
            else:
                _valid(count == len(components) and payload[-3:] == b'\0\x3f\0')
                _valid(all((0, dc) in tables and (1, ac) in tables for _, dc, ac in selectors))
                _valid(all(symbol & 15 or symbol in (0, 0xf0)
                           for (kind, _), table in tables.items() if kind == 1
                           for codes in table for symbol in codes.values()))
                _scan(raw, offset, width, height, components, selectors, tables, restart)
                return
    _valid(False)  # No scan/EOI, including a file containing only SOI.
