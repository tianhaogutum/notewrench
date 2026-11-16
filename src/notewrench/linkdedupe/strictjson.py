# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 linkdedupe contributors
"""Strict JSON with duplicate-key and unsafe-integer detection (port of ``lib/json.js``).

``JSON.parse`` alone would silently overwrite fields or round source IDs before
provenance exists. Error offsets are UTF-16 code unit offsets, like the original.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from .jsjson import MAX_SAFE_INTEGER, js_ordered, quote


class JSONInputError(ValueError):
    """Malformed or unsafe JSON input."""


_WS = re.compile('[\t\n\r ]*')
_STRING_CHUNK = re.compile(r'[^"\\]*')
_NUMBER = re.compile(r'-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?')
_WORDS = (('true', True), ('false', False), ('null', None))


def _utf16_offset(text: str, at: int) -> int:
    head = text[:at]
    offset = len(head) + sum(1 for char in head if ord(char) > 0xFFFF) if not head.isascii() else len(head)
    return offset + max(0, at - len(text))


def parse_json(text: Any) -> Any:
    if not isinstance(text, str):
        raise JSONInputError('JSON input must be text')
    length = len(text)
    at = 1 if text[:1] == '﻿' else 0

    def fail(message: str) -> Any:
        raise JSONInputError(f'{message} at JSON offset {_utf16_offset(text, at)}')

    def ws() -> None:
        nonlocal at
        if at < length:
            at = _WS.match(text, at).end()

    def string() -> str:
        nonlocal at
        start = at
        at += 1
        while at < length:
            at = _STRING_CHUNK.match(text, at).end()
            if at >= length:
                break
            char = text[at]
            at += 1
            if char == '\\':
                at += 1
                continue
            try:
                return json.loads(text[start:at])
            except ValueError:
                fail('Invalid JSON string')
        fail('Unterminated JSON string')

    def char_at(index: int) -> str:
        return text[index] if index < length else ''

    def value(depth: int) -> Any:
        nonlocal at
        if depth > 256:
            fail('JSON nesting limit (256) exceeded')
        ws()
        char = char_at(at)
        if char == '"':
            return string()
        if char == '{':
            at += 1
            ws()
            result: dict[str, Any] = {}
            if char_at(at) == '}':
                at += 1
                return result
            while True:
                ws()
                if char_at(at) != '"':
                    fail('Expected object key')
                key = string()
                if key in result:
                    fail(f'Duplicate JSON key {quote(key)}')
                ws()
                colon = char_at(at)
                at += 1
                if colon != ':':
                    fail('Expected colon')
                result[key] = value(depth + 1)
                ws()
                end = char_at(at)
                at += 1
                if end == '}':
                    return js_ordered(result)
                if end != ',':
                    fail('Expected comma or closing brace')
        if char == '[':
            at += 1
            ws()
            items: list[Any] = []
            if char_at(at) == ']':
                at += 1
                return items
            while True:
                items.append(value(depth + 1))
                ws()
                end = char_at(at)
                at += 1
                if end == ']':
                    return items
                if end != ',':
                    fail('Expected comma or closing bracket')
        for word, result_value in _WORDS:
            if text.startswith(word, at):
                at += len(word)
                return result_value
        token = _NUMBER.match(text, at) if at < length else None
        if not token:
            fail('Expected JSON value')
        at = token.end()
        number = float(token.group(0))
        if not math.isfinite(number) or (number.is_integer() and abs(number) > MAX_SAFE_INTEGER):
            fail('Unsafe JSON number; export large IDs as strings')
        if number.is_integer() and not (number == 0 and math.copysign(1, number) < 0):
            return int(number)
        return number

    result = value(0)
    ws()
    if at != length:
        fail('Unexpected trailing JSON data')
    return result
