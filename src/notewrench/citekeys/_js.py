# SPDX-License-Identifier: AGPL-3.0-only
"""JavaScript string/JSON semantics needed for byte-identical reports.

The original tool ran on Node.js: offsets, lengths and columns are counted in
UTF-16 code units and regular expressions see a string of code units. The scanner
therefore works on a *unit string*: a Python ``str`` in which every astral code
point is replaced by its two surrogate code points. Indexing, slicing and
character classes then behave exactly like JavaScript ``String`` operations.
"""
from __future__ import annotations

import json
import re
import unicodedata

# ECMAScript WhiteSpace + LineTerminator, i.e. the members of JavaScript's \s.
JS_WS = '\t\n\v\f\r    -     　﻿'
WS = '[' + JS_WS + ']'
NON_WS = '[^' + JS_WS + ']'
# JavaScript '.' without the s flag: anything except a line terminator.
DOT = '[^\n\r  ]'

_ASTRAL = re.compile('[\U00010000-\U0010ffff]')
_SURROGATE = re.compile('[\ud800-\udfff]')
_SURROGATE_PAIR = re.compile('[\ud800-\udbff][\udc00-\udfff]')


def _split_astral(match: re.Match[str]) -> str:
    value = ord(match.group()) - 0x10000
    return chr(0xD800 + (value >> 10)) + chr(0xDC00 + (value & 0x3FF))


def to_units(text: str) -> str:
    """Python string -> UTF-16 unit string (astral code points become surrogate pairs)."""
    return _ASTRAL.sub(_split_astral, text)


def _join_pair(match: re.Match[str]) -> str:
    high, low = match.group()
    return chr(0x10000 + ((ord(high) - 0xD800) << 10) + (ord(low) - 0xDC00))


def from_units(units: str) -> str:
    """UTF-16 unit string -> Python string (valid surrogate pairs are recombined)."""
    if not _SURROGATE.search(units):
        return units
    return _SURROGATE_PAIR.sub(_join_pair, units)


def u16len(text: str) -> int:
    """``String.prototype.length`` of a Python string."""
    return len(text) + len(_ASTRAL.findall(text))


def utf8(text: str) -> bytes:
    """Node's UTF-8 encoding of a JavaScript string: lone surrogates become U+FFFD."""
    return _SURROGATE.sub('�', from_units(text)).encode('utf-8')


def byte_length(units: str) -> int:
    """``Buffer.byteLength`` of a unit string."""
    return len(utf8(units))


def email_local(char: str) -> bool:
    r"""``/[\p{L}\p{N}_@.!#$%&'*+\/=?^`{|}~-]/u`` for one code point (or unit)."""
    if not char:
        return False
    return char in "_@.!#$%&'*+/=?^`{|}~-" or unicodedata.category(char)[0] in 'LN'


_WS_SET = frozenset('\t\n\v\f\r       　﻿'
                    + ''.join(chr(c) for c in range(0x2000, 0x200B)))


def is_ws(char: str) -> bool:
    return char in _WS_SET


def candidate_test(units: str) -> bool:
    r"""``/(^|[^\p{L}\p{N}_@.!#$%&'*+\/=?^`{|}~-])(-?@)(?=[^\s@\]])/u.test(...)``.

    The u flag makes the pattern see code points, so astral pairs are recombined first.
    """
    text = from_units(units)
    index = text.find('@')
    while index != -1:
        following = text[index + 1:index + 2]
        if following and not is_ws(following) and following not in '@]':
            if index == 0 or not email_local(text[index - 1]):
                return True
            if text[index - 1] == '-' and (index == 1 or not email_local(text[index - 2])):
                return True
        index = text.find('@', index + 1)
    return False


def _escape_surrogate(match: re.Match[str]) -> str:
    return '\\u%04x' % ord(match.group())


def stringify(value: object, indent: int | None = None) -> str:
    """``JSON.stringify`` for the JSON-compatible values used in reports."""
    text = json.dumps(value, ensure_ascii=False, indent=indent, separators=(',', ': ') if indent else (',', ':'))
    # Well-formed JSON.stringify escapes lone surrogates; json.dumps leaves them raw.
    return _SURROGATE.sub(_escape_surrogate, text)


class JSONError(ValueError):
    pass


def _reject_constant(name: str) -> object:
    raise JSONError(f'Unexpected token {name[0]!r}, not valid JSON')


def parse_json(text: str) -> object:
    """``JSON.parse``: like :func:`json.loads` but without NaN/Infinity extensions."""
    try:
        return json.loads(text, parse_constant=_reject_constant)
    except RecursionError:
        raise JSONError('Maximum nesting depth exceeded') from None
    except json.JSONDecodeError as error:
        raise JSONError(str(error)) from None
