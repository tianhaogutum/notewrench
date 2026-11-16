# SPDX-License-Identifier: AGPL-3.0-only
"""JavaScript value semantics that are observable in linkdedupe's output.

The original tool ran on Node.js; reports are ``JSON.stringify`` output and
several checks use ECMAScript ``String()``, sort order and deep equality. Values
are represented as plain JSON-like Python data: ``dict`` (object), ``list``
(array), ``str``, ``bool``, ``None`` (null), ``int`` for integral numbers and
``float`` for the rest (``-0`` is kept as ``-0.0``). :data:`UNDEFINED` stands for
an absent property.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

MAX_SAFE_INTEGER = 2**53 - 1


class _Undefined:
    _instance: _Undefined | None = None

    def __new__(cls) -> _Undefined:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return 'UNDEFINED'

    def __bool__(self) -> bool:
        return False


UNDEFINED: Any = _Undefined()

# ECMAScript WhiteSpace + LineTerminator: the ``\s`` class.
JS_WHITESPACE = ('\t\n\v\f\r          '
                 '        　﻿')
JS_WHITESPACE_CLASS = '[' + re.escape(JS_WHITESPACE) + ']'


class TypeErrorJS(Exception):
    """A JavaScript ``TypeError`` the original would have thrown."""


# --- type predicates -------------------------------------------------------------

def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_finite(value: Any) -> bool:
    """``Number.isFinite``."""
    return is_number(value) and math.isfinite(value)


def is_safe_integer(value: Any) -> bool:
    """``Number.isSafeInteger``."""
    if not is_number(value):
        return False
    if isinstance(value, float) and not (math.isfinite(value) and value.is_integer()):
        return False
    return abs(value) <= MAX_SAFE_INTEGER


def get(value: Any, key: str) -> Any:
    """``value[key]`` on a JSON object (``UNDEFINED`` when absent)."""
    if isinstance(value, dict):
        return value.get(key, UNDEFINED)
    return UNDEFINED


# --- object key order ------------------------------------------------------------

_INDEX_KEY = re.compile(r'(?:0|[1-9][0-9]{0,9})\Z')


def _is_index_key(key: str) -> bool:
    return _INDEX_KEY.match(key) is not None and int(key) < 4294967295


def js_keys(obj: dict) -> list[str]:
    """``Object.keys`` order: array-index keys ascending, then insertion order."""
    indices = [key for key in obj if key[:1].isdigit() and _is_index_key(key)]
    if not indices:
        return list(obj)
    indices.sort(key=int)
    chosen = set(indices)
    return indices + [key for key in obj if key not in chosen]


def js_ordered(obj: dict) -> dict:
    """A copy of ``obj`` in JavaScript property order (identity if already ordered)."""
    keys = js_keys(obj)
    if keys == list(obj):
        return obj
    return {key: obj[key] for key in keys}


# --- strings ---------------------------------------------------------------------

_SURROGATE = re.compile('[\ud800-\udfff]')


def utf16_length(text: str) -> int:
    if text.isascii():
        return len(text)
    return len(text) + sum(1 for char in text if ord(char) > 0xFFFF)


def utf16_key(text: str) -> bytes:
    """Sort key for JavaScript's default (UTF-16 code unit) string order."""
    return text.encode('utf-16-be', 'surrogatepass')


def well_formed(text: str) -> str:
    """Replace lone surrogates by U+FFFD (how Node encodes strings as UTF-8)."""
    return _SURROGATE.sub('�', text)


def utf8(text: str) -> bytes:
    return well_formed(text).encode('utf-8')


def sha256_hex(text: str) -> str:
    """``createHash('sha256').update(text).digest('hex')`` for a string."""
    return hashlib.sha256(utf8(text)).hexdigest()


# --- numbers and String() ---------------------------------------------------------

def format_number(value: int | float) -> str:
    """``Number.prototype.toString`` (and ``JSON.stringify`` for finite numbers)."""
    if isinstance(value, int):
        if abs(value) <= MAX_SAFE_INTEGER + 1:
            return str(value)
        value = float(value)
    if value != value:
        return 'NaN'
    if value in (math.inf, -math.inf):
        return 'Infinity' if value > 0 else '-Infinity'
    if value == 0:
        return '0'
    sign = '-' if value < 0 else ''
    mantissa, _, exponent = repr(abs(value)).partition('e')
    whole, _, fraction = mantissa.partition('.')
    raw = whole + fraction
    stripped = raw.lstrip('0')
    point = len(whole) - (len(raw) - len(stripped)) + (int(exponent) if exponent else 0)
    digits = stripped.rstrip('0')
    k = len(digits)
    if k <= point <= 21:
        body = digits + '0' * (point - k)
    elif 0 < point <= 21:
        body = digits[:point] + '.' + digits[point:]
    elif -6 < point <= 0:
        body = '0.' + '0' * -point + digits
    else:
        e = point - 1
        body = (digits if k == 1 else digits[0] + '.' + digits[1:]) + ('e+' if e >= 0 else 'e-') + str(abs(e))
    return sign + body


def js_string(value: Any) -> str:
    """ECMAScript ``String(value)`` for JSON values."""
    if value is UNDEFINED:
        return 'undefined'
    if value is None:
        return 'null'
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    if isinstance(value, str):
        return value
    if is_number(value):
        return format_number(value)
    if isinstance(value, list):
        return ','.join('' if item is None or item is UNDEFINED else js_string(item) for item in value)
    if isinstance(value, dict):
        if 'toString' in value:
            raise TypeErrorJS('Cannot convert object to primitive value')
        return '[object Object]'
    return str(value)


# --- JSON.stringify ---------------------------------------------------------------

_ESCAPES = {'"': '\\"', '\\': '\\\\', '\b': '\\b', '\f': '\\f', '\n': '\\n', '\r': '\\r', '\t': '\\t'}
_NEEDS_ESCAPE = re.compile('[\x00-\x1f"\\\\\ud800-\udfff]')


def _escape(match: re.Match[str]) -> str:
    char = match.group(0)
    return _ESCAPES.get(char) or f'\\u{ord(char):04x}'


def quote(text: str) -> str:
    """``JSON.stringify`` for a string."""
    return '"' + _NEEDS_ESCAPE.sub(_escape, text) + '"'


def stringify(value: Any, indent: int | None = None) -> Any:
    """``JSON.stringify(value)`` / ``JSON.stringify(value, null, indent)``.

    Returns :data:`UNDEFINED` for an undefined top-level value, like JavaScript.
    """
    if value is UNDEFINED:
        return UNDEFINED
    parts: list[str] = []
    _encode(value, parts, '\n' if indent else '', ' ' * indent if indent else '')
    return ''.join(parts)


def _encode(value: Any, out: list[str], newline: str, step: str) -> None:
    if value is None:
        out.append('null')
    elif value is True:
        out.append('true')
    elif value is False:
        out.append('false')
    elif isinstance(value, str):
        out.append(quote(value))
    elif isinstance(value, (int, float)):
        out.append(format_number(value) if math.isfinite(value) else 'null')
    elif isinstance(value, dict):
        keys = [key for key in js_keys(value) if value[key] is not UNDEFINED]
        if not keys:
            out.append('{}')
            return
        inner = newline + step
        out.append('{')
        for index, key in enumerate(keys):
            if index:
                out.append(',')
            out.append(inner)
            out.append(quote(key))
            out.append(': ' if step else ':')
            _encode(value[key], out, inner, step)
        out.append(newline)
        out.append('}')
    elif isinstance(value, (list, tuple)):
        if not value:
            out.append('[]')
            return
        inner = newline + step
        out.append('[')
        for index, item in enumerate(value):
            if index:
                out.append(',')
            out.append(inner)
            if item is UNDEFINED:
                out.append('null')
            else:
                _encode(item, out, inner, step)
        out.append(newline)
        out.append(']')
    else:
        raise TypeError(f'cannot serialize {type(value).__name__}')


def stable(value: Any) -> str:
    """linkdedupe's canonical key-sorted serialization (used for hashes and equality)."""
    if isinstance(value, list):
        return '[' + ','.join(stable(item) for item in value) + ']'
    if isinstance(value, dict):
        keys = sorted(value, key=utf16_key)
        return '{' + ','.join(f'{quote(key)}:{stable(value[key])}' for key in keys) + '}'
    text = stringify(value)
    return 'undefined' if text is UNDEFINED else text


# --- util.isDeepStrictEqual --------------------------------------------------------

def deep_strict_equal(a: Any, b: Any) -> bool:
    """``util.isDeepStrictEqual`` for JSON values (``-0`` differs from ``0``)."""
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None or a is UNDEFINED or b is UNDEFINED:
        return a is b
    if is_number(a) and is_number(b):
        if a != b:
            return a != a and b != b
        return math.copysign(1, a) == math.copysign(1, b) if a == 0 else True
    if isinstance(a, str) and isinstance(b, str):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(deep_strict_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(deep_strict_equal(a[key], b[key]) for key in a)
    return False
