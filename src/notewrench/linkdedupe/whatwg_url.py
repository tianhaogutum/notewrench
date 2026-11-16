# SPDX-License-Identifier: AGPL-3.0-only
"""The parts of the WHATWG URL Standard that linkdedupe observes.

The original tool ran on Node.js and used ``new URL(input)`` only to *validate*
an absolute http(s) URL, plus ``url.domainToASCII()`` to map Unicode hosts.
This module reproduces those two behaviours for the inputs that reach them:

* :func:`validate_special_authority` - the host and port checks the basic URL
  parser applies to an http(s) authority without credentials;
* :func:`domain_to_ascii` - Node's ``url.domainToASCII``: the URL host parser
  (UTS #46 ToASCII with the URL Standard's settings, forbidden code points,
  IPv4/IPv6 parsing and serialization) or ``''`` on failure.

The UTS #46 mapping and Joining_Type tables live in :mod:`._idna_data`; NFC,
Bidi_Class, General_Category and Canonical_Combining_Class come from
:mod:`unicodedata`.
"""

from __future__ import annotations

import bisect
import functools
import re
import unicodedata

from . import _idna_data


class HostError(ValueError):
    """The URL host (or port) is invalid."""


# --- UTS #46 tables --------------------------------------------------------------

@functools.cache
def _mapping_table() -> tuple[list[int], str, list[str | None]]:
    starts: list[int] = []
    statuses: list[str] = []
    mappings: list[str | None] = []
    for row in re.split('[;\n]', _idna_data.MAPPING.strip()):
        first, status, *mapped = row.split(' ')
        starts.append(int(first, 16))
        statuses.append(status)
        mappings.append(''.join(chr(int(cp, 16)) for cp in mapped) if status == 'M' else None)
    return starts, ''.join(statuses), mappings


@functools.cache
def _joining_table() -> tuple[list[int], list[int], str]:
    firsts: list[int] = []
    lasts: list[int] = []
    types: list[str] = []
    for row in re.split('[;\n]', _idna_data.JOINING_TYPES.strip()):
        first, last, kind = row.split(' ')
        firsts.append(int(first, 16))
        lasts.append(int(last, 16))
        types.append(kind)
    return firsts, lasts, ''.join(types)


def _lookup(cp: int) -> tuple[str, str | None]:
    starts, statuses, mappings = _mapping_table()
    index = bisect.bisect_right(starts, cp) - 1
    return statuses[index], mappings[index]


def _joining_type(cp: int) -> str:
    firsts, lasts, types = _joining_table()
    index = bisect.bisect_right(firsts, cp) - 1
    if index >= 0 and cp <= lasts[index]:
        return types[index]
    return 'U'


# --- Punycode (RFC 3492) -----------------------------------------------------------

_BASE, _TMIN, _TMAX, _SKEW, _DAMP, _INITIAL_BIAS, _INITIAL_N = 36, 1, 26, 38, 700, 72, 128
_MAXINT = 0xFFFFFFFF


def _adapt(delta: int, numpoints: int, first: bool) -> int:
    delta = delta // _DAMP if first else delta // 2
    delta += delta // numpoints
    k = 0
    while delta > ((_BASE - _TMIN) * _TMAX) // 2:
        delta //= _BASE - _TMIN
        k += _BASE
    return k + (_BASE - _TMIN + 1) * delta // (delta + _SKEW)


def _digit_value(char: str) -> int:
    code = ord(char)
    if 0x30 <= code <= 0x39:
        return code - 22
    if 0x41 <= code <= 0x5A:
        return code - 0x41
    if 0x61 <= code <= 0x7A:
        return code - 0x61
    return -1


def punycode_decode(text: str) -> str | None:
    """RFC 3492 decoding with 32-bit overflow checks; ``None`` on failure."""
    delimiter = text.rfind('-')
    output: list[int] = []
    if delimiter > 0:
        for char in text[:delimiter]:
            if ord(char) >= 0x80:
                return None
            output.append(ord(char))
        position = delimiter + 1
    else:
        position = 0
    n, i, bias = _INITIAL_N, 0, _INITIAL_BIAS
    while position < len(text):
        old_i, w, k = i, 1, _BASE
        while True:
            if position >= len(text):
                return None
            digit = _digit_value(text[position])
            position += 1
            if digit < 0:
                return None
            if digit > (_MAXINT - i) // w:
                return None
            i += digit * w
            t = _TMIN if k <= bias else _TMAX if k >= bias + _TMAX else k - bias
            if digit < t:
                break
            if w > _MAXINT // (_BASE - t):
                return None
            w *= _BASE - t
            k += _BASE
        bias = _adapt(i - old_i, len(output) + 1, old_i == 0)
        if i // (len(output) + 1) > _MAXINT - n:
            return None
        n += i // (len(output) + 1)
        i %= len(output) + 1
        if n > 0x10FFFF or 0xD800 <= n <= 0xDFFF:
            return None
        output.insert(i, n)
        i += 1
    return ''.join(map(chr, output))


def punycode_encode(text: str) -> str:
    return text.encode('punycode').decode('ascii')


# --- UTS #46 processing -------------------------------------------------------------

_RTL = {'R', 'AL', 'AN'}
_RTL_ALLOWED = {'R', 'AL', 'AN', 'EN', 'ES', 'CS', 'ET', 'ON', 'BN', 'NSM'}
_LTR_ALLOWED = {'L', 'EN', 'ES', 'CS', 'ET', 'ON', 'BN', 'NSM'}


def _bidi(char: str) -> str:
    return unicodedata.bidirectional(char)


def _check_bidi(label: str) -> bool:
    """RFC 5893 section 2, rules 1-6."""
    if not label:
        return True
    classes = [_bidi(char) for char in label]
    first = classes[0]
    if first not in ('L', 'R', 'AL'):
        return False
    end = len(classes)
    while end > 1 and classes[end - 1] == 'NSM':
        end -= 1
    last = classes[end - 1]
    if first in ('R', 'AL'):
        if any(kind not in _RTL_ALLOWED for kind in classes):
            return False
        if last not in ('R', 'AL', 'EN', 'AN'):
            return False
        return not ('EN' in classes and 'AN' in classes)
    if any(kind not in _LTR_ALLOWED for kind in classes):
        return False
    return last in ('L', 'EN')


def _check_joiners(label: str) -> bool:
    """RFC 5892 Appendix A.1 and A.2 (ContextJ)."""
    for index, char in enumerate(label):
        if char not in '‌‍':
            continue
        if index and unicodedata.combining(label[index - 1]) == 9:
            continue
        if char == '‍':
            return False
        before = index - 1
        while before >= 0 and _joining_type(ord(label[before])) == 'T':
            before -= 1
        if before < 0 or _joining_type(ord(label[before])) not in ('L', 'D'):
            return False
        after = index + 1
        while after < len(label) and _joining_type(ord(label[after])) == 'T':
            after += 1
        if after >= len(label) or _joining_type(ord(label[after])) not in ('R', 'D'):
            return False
    return True


def _valid_label(label: str) -> bool:
    """UTS #46 section 4.1 validity criteria (nontransitional, CheckHyphens off)."""
    if unicodedata.normalize('NFC', label) != label:
        return False
    if label[:4] == 'xn--':
        return False
    if '.' in label:
        return False
    if label and unicodedata.category(label[0]).startswith('M'):
        return False
    for char in label:
        status = _lookup(ord(char))[0]
        if status not in ('V', 'D'):
            return False
    return _check_joiners(label)


def uts46_to_ascii(domain: str) -> str | None:
    """UTS #46 ToASCII with the URL Standard's non-strict options; ``None`` on error.

    CheckHyphens=false, CheckBidi=true, CheckJoiners=true, UseSTD3ASCIIRules=false,
    Transitional_Processing=false, VerifyDnsLength=false, IgnoreInvalidPunycode=false.
    """
    mapped: list[str] = []
    for char in domain:
        status, mapping = _lookup(ord(char))
        if status == 'M':
            mapped.append(mapping or '')
        elif status != 'I':
            mapped.append(char)
    labels = unicodedata.normalize('NFC', ''.join(mapped)).split('.')
    ok = True
    for index, label in enumerate(labels):
        if label[:4] == 'xn--':
            if not label.isascii():
                ok = False
                continue
            decoded = punycode_decode(label[4:])
            if decoded is None:
                ok = False
                continue
            labels[index] = decoded
            if not decoded or decoded.isascii():
                ok = False
            if not _valid_label(decoded):
                ok = False
        elif not _valid_label(label):
            ok = False
    if ok and any(_bidi(char) in _RTL for label in labels for char in label):
        ok = all(_check_bidi(label) for label in labels)
    if not ok:
        return None
    return '.'.join(label if label.isascii() else 'xn--' + punycode_encode(label) for label in labels)


# --- URL host parser ---------------------------------------------------------------

_FORBIDDEN_DOMAIN = frozenset('\x00\t\n\r #/:<>?@[\\]^|%\x7f') | frozenset(map(chr, range(0x20)))
_HEX = '0123456789abcdefABCDEF'


def _percent_decode(text: str) -> bytes:
    data = text.encode('utf-8', 'surrogatepass')
    out = bytearray()
    index = 0
    while index < len(data):
        byte = data[index]
        if byte == 0x25 and index + 2 < len(data) and chr(data[index + 1]) in _HEX and chr(data[index + 2]) in _HEX:
            out.append(int(data[index + 1:index + 3], 16))
            index += 3
        else:
            out.append(byte)
            index += 1
    return bytes(out)


def _parse_ipv4_number(part: str) -> int | None:
    if not part:
        return None
    radix = 10
    if len(part) >= 2 and part[:2] in ('0x', '0X'):
        part, radix = part[2:], 16
    elif len(part) >= 2 and part[0] == '0':
        part, radix = part[1:], 8
    if not part:
        return 0
    digits = {10: '0123456789', 16: _HEX, 8: '01234567'}[radix]
    if any(char not in digits for char in part):
        return None
    if len(part.lstrip('0')) > 24:  # far beyond any IPv4 range; avoids huge int() parses
        return 2**96
    return int(part, radix)


def _ends_in_number(text: str) -> bool:
    parts = text.split('.')
    if parts[-1] == '':
        if len(parts) == 1:
            return False
        parts.pop()
    last = parts[-1]
    if last and all('0' <= char <= '9' for char in last):
        return True
    return last[:2] in ('0x', '0X') and all(char in _HEX for char in last[2:])


def _parse_ipv4(text: str) -> str:
    parts = text.split('.')
    if parts[-1] == '' and len(parts) > 1:
        parts.pop()
    if len(parts) > 4:
        raise HostError('IPv4 too many parts')
    numbers = []
    for part in parts:
        number = _parse_ipv4_number(part)
        if number is None:
            raise HostError('IPv4 non-numeric part')
        numbers.append(number)
    if any(number > 255 for number in numbers[:-1]):
        raise HostError('IPv4 out of range part')
    if numbers[-1] >= 256 ** (5 - len(numbers)):
        raise HostError('IPv4 out of range')
    ipv4 = numbers[-1]
    for counter, number in enumerate(numbers[:-1]):
        ipv4 += number * 256 ** (3 - counter)
    return '.'.join(str((ipv4 >> shift) & 0xFF) for shift in (24, 16, 8, 0))


def _parse_ipv6(text: str) -> list[int]:
    address = [0] * 8
    piece_index, compress, pointer = 0, None, 0
    length = len(text)

    def at(index: int) -> str:
        return text[index] if index < length else ''

    if at(pointer) == ':':
        if at(pointer + 1) != ':':
            raise HostError('IPv6 invalid compression')
        pointer += 2
        piece_index += 1
        compress = piece_index
    while at(pointer):
        if piece_index == 8:
            raise HostError('IPv6 too many pieces')
        if at(pointer) == ':':
            if compress is not None:
                raise HostError('IPv6 multiple compression')
            pointer += 1
            piece_index += 1
            compress = piece_index
            continue
        value = count = 0
        while count < 4 and at(pointer) and at(pointer) in _HEX:
            value = value * 0x10 + int(at(pointer), 16)
            pointer += 1
            count += 1
        if at(pointer) == '.':
            if count == 0:
                raise HostError('IPv4 in IPv6 invalid code point')
            pointer -= count
            if piece_index > 6:
                raise HostError('IPv4 in IPv6 too many pieces')
            numbers_seen = 0
            while at(pointer):
                ipv4_piece = None
                if numbers_seen > 0:
                    if at(pointer) == '.' and numbers_seen < 4:
                        pointer += 1
                    else:
                        raise HostError('IPv4 in IPv6 invalid code point')
                if not ('0' <= at(pointer) <= '9') or not at(pointer):
                    raise HostError('IPv4 in IPv6 invalid code point')
                while at(pointer) and '0' <= at(pointer) <= '9':
                    number = int(at(pointer))
                    if ipv4_piece is None:
                        ipv4_piece = number
                    elif ipv4_piece == 0:
                        raise HostError('IPv4 in IPv6 invalid code point')
                    else:
                        ipv4_piece = ipv4_piece * 10 + number
                    if ipv4_piece > 255:
                        raise HostError('IPv4 in IPv6 out of range part')
                    pointer += 1
                address[piece_index] = address[piece_index] * 0x100 + ipv4_piece
                numbers_seen += 1
                if numbers_seen in (2, 4):
                    piece_index += 1
            if numbers_seen != 4:
                raise HostError('IPv4 in IPv6 too few parts')
            break
        if at(pointer) == ':':
            pointer += 1
            if not at(pointer):
                raise HostError('IPv6 invalid code point')
        elif at(pointer):
            raise HostError('IPv6 invalid code point')
        address[piece_index] = value
        piece_index += 1
    if compress is not None:
        swaps = piece_index - compress
        piece_index = 7
        while piece_index != 0 and swaps > 0:
            address[piece_index], address[compress + swaps - 1] = address[compress + swaps - 1], address[piece_index]
            piece_index -= 1
            swaps -= 1
    elif piece_index != 8:
        raise HostError('IPv6 too few pieces')
    return address


def _serialize_ipv6(address: list[int]) -> str:
    best_start, best_length = None, 1
    index = 0
    while index < 8:
        if address[index] == 0:
            end = index
            while end < 8 and address[end] == 0:
                end += 1
            if end - index > best_length:
                best_start, best_length = index, end - index
            index = end
        else:
            index += 1
    out = ''
    ignore0 = False
    for piece_index in range(8):
        if ignore0 and address[piece_index] == 0:
            continue
        ignore0 = False
        if best_start == piece_index:
            out += '::' if piece_index == 0 else ':'
            ignore0 = True
            continue
        out += format(address[piece_index], 'x')
        if piece_index != 7:
            out += ':'
    return out


def parse_host(text: str) -> str:
    """The URL Standard host parser for a special scheme; returns the serialized host."""
    if text[:1] == '[':
        if text[-1:] != ']':
            raise HostError('IPv6 unclosed')
        return '[' + _serialize_ipv6(_parse_ipv6(text[1:-1])) + ']'
    domain = _percent_decode(text).decode('utf-8', 'replace')
    if domain.isascii():
        # Node's parser (ada) only lowercases all-ASCII domains: it does not decode or
        # validate "xn--" labels there, unlike the URL Standard's literal text.
        ascii_domain: str | None = domain.lower()
    else:
        ascii_domain = uts46_to_ascii(domain)
    if not ascii_domain:
        raise HostError('domain to ASCII')
    if any(char in _FORBIDDEN_DOMAIN for char in ascii_domain):
        raise HostError('domain invalid code point')
    if _ends_in_number(ascii_domain):
        return _parse_ipv4(ascii_domain)
    return ascii_domain


def port_number(digits: str) -> int:
    """The decimal value of an ASCII digit string (any length, leading zeros allowed)."""
    significant = digits.lstrip('0')
    return int(significant) if len(significant) <= 6 else 10**6


def validate_special_authority(authority: str) -> None:
    """Host and port checks of ``new URL()`` for ``http(s)://<authority>``.

    ``authority`` contains no ``@``, ``/``, ``?``, ``#``, backslash, whitespace or
    control characters (the caller has already rejected those).
    """
    inside_brackets = False
    split = len(authority)
    for index, char in enumerate(authority):
        if char == ':' and not inside_brackets:
            split = index
            break
        if char == '[':
            inside_brackets = True
        elif char == ']':
            inside_brackets = False
    host, port = authority[:split], authority[split + 1:]
    if not host:
        raise HostError('host missing')
    parse_host(host)
    if split < len(authority):
        if any(not ('0' <= char <= '9') for char in port):
            raise HostError('port invalid')
        if port and port_number(port) > 65535:
            raise HostError('port out of range')


@functools.lru_cache(maxsize=4096)
def domain_to_ascii(domain: str) -> str:
    """Node's ``url.domainToASCII(domain)``: the hostname setter on ``ws://x``."""
    if not domain:
        return ''
    text = re.sub('[\t\n\r]', '', domain)
    inside_brackets = False
    end = len(text)
    for index, char in enumerate(text):
        if char in '/?#\\':
            end = index
            break
        if char == ':' and not inside_brackets:
            return ''
        if char == '[':
            inside_brackets = True
        elif char == ']':
            inside_brackets = False
    text = text[:end]
    if not text:
        return ''
    try:
        return parse_host(text)
    except HostError:
        return ''
