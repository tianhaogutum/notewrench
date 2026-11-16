# SPDX-License-Identifier: AGPL-3.0-only
"""Block structure: frontmatter, list/quote containers, fences, HTML and math.

All functions take a UTF-16 *unit string* (see :mod:`._js`), so every index is a
JavaScript string offset.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from ._js import DOT, NON_WS, WS, candidate_test
from .identity import RekeyError

BLOCK_BOUNDARY = 1
INDENTED_LINE = 2
CONTAINER_PREFIX = 3


def fail(message: str) -> None:
    raise RekeyError('UNSUPPORTED_MARKDOWN', message)


def metadata_fail() -> None:
    raise RekeyError('UNSUPPORTED_FRONTMATTER', 'Only initial flat frontmatter with opaque scalars and bounded quoted '
                     'bare-key scalar/flow/block nocite forms is supported')


def escaped(text: str, index: int) -> bool:
    count = 0
    while index > 0:
        index -= 1
        if text[index] != '\\':
            break
        count += 1
    return count % 2 == 1


def _at(text: str, index: int) -> str:
    return text[index] if 0 <= index < len(text) else ''


def _fill(mask: bytearray, value: int, start: int, end: int) -> None:
    if end > start:
        mask[start:end] = bytes((value,)) * (end - start)


# Opaque regions are deliberately conservative, not interpreted as Markdown.
def math_end(text: str, start: int, mask: bytearray | None = None) -> int | None:
    char = _at(text, start)
    if char == '$':
        delimiter = '$$' if _at(text, start + 1) == '$' else '$'
        if _at(text, start + len(delimiter)) == '$':
            fail('Ambiguous dollar delimiter')
        closer = delimiter
    elif char == '\\' and _at(text, start + 1) in ('(', '['):
        delimiter = text[start:start + 2]
        closer = '\\)' if text[start + 1] == '(' else '\\]'
    else:
        return None
    i = start + len(delimiter)
    length = len(text)
    first = closer[0]
    while i < length:
        if mask is not None and mask[i] == BLOCK_BOUNDARY:
            break
        if text[i] == first and text.startswith(closer, i) and not escaped(text, i):
            if first == '$' and (_at(text, i - 1) == '$' or _at(text, i + len(closer)) == '$'):
                i += 1
                continue
            return i + len(closer)
        i += 1
    # Preserve the pre-existing escaped bracket literal form, e.g. \\[@A].
    if delimiter == '\\[' and text.find(']', start + 2) != -1:
        return None
    fail('Unclosed or cross-block math delimiter')
    return None  # unreachable


def _tag_end(text: str, start: int) -> int:
    quote = None
    for i in range(start + 1, len(text)):
        char = text[i]
        if quote:
            if char == quote:
                quote = None
        elif char == '"' or char == "'":
            quote = char
        elif char == '>':
            return i + 1
    fail('Unclosed HTML tag')
    return 0  # unreachable


_AUTOLINK = re.compile('<(?:https?://|mailto:)[^<>' + WS[1:-1] + ']*>')
_EMAIL_AUTOLINK = re.compile(r'<[A-Za-z0-9_.+-]+@[A-Za-z0-9_.-]+>')
_CLOSING_TAG = re.compile('</[A-Za-z]')
_OPENING_TAG = re.compile('<([A-Za-z][A-Za-z0-9_:-]*)(?=[' + WS[1:-1] + '/>])')
_ANY_TAG = re.compile('<(/?)([A-Za-z][A-Za-z0-9_:-]*)(?=[' + WS[1:-1] + '/>])')
_SELF_CLOSING = re.compile('/' + WS + r'*>\Z')
_VOID = frozenset('area base br col embed hr img input link meta param source track wbr'.split())
_RAW = frozenset(('script', 'style', 'pre', 'textarea'))
_RAW_CLOSERS = {name: re.compile('</' + name + WS + '*>', re.IGNORECASE | re.ASCII) for name in _RAW}


def html_end(text: str, start: int) -> int | None:
    for opener, closer in (('<!--', '-->'), ('<![CDATA[', ']]>'), ('<?', '?>')):
        if text.startswith(opener, start):
            end = text.find(closer, start + len(opener))
            if end == -1:
                fail('Unclosed HTML comment/declaration')
            return end + len(closer)
    if _AUTOLINK.match(text, start) or _EMAIL_AUTOLINK.match(text, start):
        return text.find('>', start) + 1
    if text.startswith('<!', start) or _CLOSING_TAG.match(text, start):
        return _tag_end(text, start)
    opening = _OPENING_TAG.match(text, start)
    if not opening:
        return None
    name = opening.group(1).lower()
    first_end = _tag_end(text, start)
    if _SELF_CLOSING.search(text[start:first_end]) or name in _VOID:
        return first_end
    if name in _RAW:
        match = _RAW_CLOSERS[name].search(text, first_end)
        if not match:
            fail('Unclosed raw HTML element')
        return match.end()
    depth = 1
    i = first_end
    length = len(text)
    while i < length:
        following = text.find('<', i)
        if following == -1:
            break
        if text.startswith(('<!', '<?'), following):
            i = html_end(text, following)  # type: ignore[assignment]
            continue
        tag = _ANY_TAG.match(text, following)
        if not tag:
            i = following + 1
            continue
        end = _tag_end(text, following)
        if tag.group(2).lower() == name:
            if tag.group(1):
                depth -= 1
            elif not _SELF_CLOSING.search(text[following:end]):
                depth += 1
            if not depth:
                return end
        # Raw elements may contain apparent tags, including the enclosing closer.
        if not tag.group(1) and tag.group(2).lower() in _RAW:
            i = html_end(text, following)  # type: ignore[assignment]
        else:
            i = end
    fail('Unclosed HTML element')
    return None  # unreachable


_OPENER = re.compile('﻿?---[ \t]*')
_MALFORMED_OPENER = re.compile(WS + '*---')
_BLANK_OR_COMMENT = re.compile('[ \t]*(?:#[^\r\n]*)?')
_BLOCK_ITEM = re.compile('( +)- +([^ \t][^\r\n]*)')
_CLOSER = re.compile(r'(?:---|\.\.\.)[ \t]*')
_NOCITE_HEADER = re.compile('nocite:(?:[ \t]+(?:#[^\r\n]*)?)?')
_FIELD = re.compile('([A-Za-z][A-Za-z0-9_-]*):[ \t]+([^ \t#][^\r\n]*)')
_SINGLE = re.compile("'(?:[^']|'')*'[ \t]*(?:#[^\r\n]*)?")
_SINGLE_CONTENT = re.compile("'((?:[^']|'')*)'")
_DOUBLE = re.compile(r'"((?:[^"\\]|\\' + DOT + ')*)"[ \t]*(?:#[^\r\n]*)?')
# JSON.parse('"..."') succeeds exactly for this string body.
_JSON_STRING_BODY = re.compile(r'(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9A-Fa-f]{4}))*')
_PLAIN_COMMENT = re.compile('[ \t]#')
_PLAIN_STRUCTURE = re.compile(r'^[!&*|>\[\]{}?\'"%@`-]|:[ \t]|[\[\]{}]')

NociteParser = Callable[[str, Any], 'list[dict[str, Any]] | None']


def _frontmatter(lines: list[tuple[int, int, str]], mask: bytearray, parse_nocite: NociteParser,
                 nocite_tokens: list[dict[str, Any]]) -> int:
    opening = lines[0][2] if lines else ''
    if not _OPENER.fullmatch(opening):
        # A malformed initial marker must not expose metadata as editable prose.
        # Only the valid form above may consume one BOM and ASCII trailing padding.
        if _MALFORMED_OPENER.match(opening):
            metadata_fail()
        return 0
    seen: set[str] = set()
    sequence: dict[str, Any] | None = None
    for n, (start, end, body) in enumerate(lines):
        _fill(mask, BLOCK_BOUNDARY, start, end)
        if not n:
            continue
        if _BLANK_OR_COMMENT.fullmatch(body):
            continue
        if sequence is not None:
            if body[:1] in (' ', '\t'):
                # The first item fixes positive space-only indentation for the block.
                # Blank/comments above do not reset it. Never decode a quoted item.
                item = _BLOCK_ITEM.fullmatch(body)
                if not item or (sequence['indent'] is not None and len(item.group(1)) != sequence['indent']):
                    metadata_fail()
                tokens = parse_nocite(item.group(2), 'block')  # type: ignore[union-attr]
                if tokens is None:
                    metadata_fail()
                sequence['indent'] = len(item.group(1))  # type: ignore[union-attr]
                offset = start + len(body) - len(item.group(2))  # type: ignore[union-attr]
                for token in tokens:  # type: ignore[union-attr]
                    nocite_tokens.append({'key': token['key'], 'offset': offset + token['offset']})
                continue
            if sequence['indent'] is None:
                metadata_fail()
            sequence = None  # Only a top-level field or closer can end a sequence.
        if _CLOSER.fullmatch(body):
            return n + 1
        # Like every field, a colon needs ASCII padding before a comment; "nocite:#c" is not a block header.
        if _NOCITE_HEADER.fullmatch(body):
            if 'nocite' in seen:
                metadata_fail()
            seen.add('nocite')
            sequence = {'indent': None}
            continue
        # Require a scalar after the separator; backtracking must not turn trailing
        # whitespace or a comment-only empty value into an opaque plain scalar.
        field = _FIELD.fullmatch(body)
        if not field or field.group(1) in seen:
            metadata_fail()
        name = field.group(1)  # type: ignore[union-attr]
        seen.add(name)
        raw = field.group(2)  # type: ignore[union-attr]
        quoted = False
        flow = name == 'nocite' and raw[0] == '['
        if flow:
            # Only nocite has a bounded collection grammar; validate it literally in
            # the citation lexer, including the suffix. Other fields remain scalar.
            content = raw
        elif raw[0] == "'":
            if not _SINGLE.fullmatch(raw):
                metadata_fail()
            # Last quote before an optional comment, without interpreting YAML escapes.
            content = _SINGLE_CONTENT.match(raw).group(1)  # type: ignore[union-attr]
            quoted = True
        elif raw[0] == '"':
            scalar = _DOUBLE.fullmatch(raw)
            if not scalar:
                metadata_fail()
            # Nocite is validated literally below, with no YAML/JSON decoding; its
            # grammar admits tabs but no escapes. Validate unknown scalars using a
            # tab-as-space copy: literal tabs are allowed, but backslash + tab remains
            # an invalid JSON escape. Never decode or rewrite the original content.
            if name != 'nocite' and not _JSON_STRING_BODY.fullmatch(scalar.group(1).replace('\t', ' ')):  # type: ignore[union-attr]
                metadata_fail()
            content = scalar.group(1)  # type: ignore[union-attr]
            quoted = True
        else:
            # No collections, aliases, tags, block scalars, implicit nested mappings or
            # multi-line scalars. Unknown fields are opaque, never searched for keys.
            comment = _PLAIN_COMMENT.search(raw)
            # Keep the boundary whitespace: "child: # comment" is still structural.
            content = raw if comment is None else raw[:comment.start() + 1]
            if _PLAIN_STRUCTURE.search(content):
                metadata_fail()
        # Unknown scalars and comments stay opaque on their physical LF/CRLF line.
        # U+2028/U+2029 are literal content, never nocite separators.
        if name != 'nocite':
            continue
        tokens = parse_nocite(content, flow) if quoted or flow else None
        if tokens is None:
            metadata_fail()
        offset = start + len(body) - len(raw) + (0 if flow else 1)
        for token in tokens:  # type: ignore[union-attr]
            nocite_tokens.append({'key': token['key'], 'offset': offset + token['offset']})
        # Leave the whole metadata block masked, including validated nocite text.
    metadata_fail()
    return 0  # unreachable


def split_lines(text: str) -> list[tuple[int, int, str]]:
    """``[...text.matchAll(/[^\\n]*(?:\\n|$)/g)]`` without empty matches: (start, end, body)."""
    lines = []
    position = 0
    length = len(text)
    while position < length:
        newline = text.find('\n', position)
        end = length if newline == -1 else newline + 1
        if newline == -1:
            body = text[position:end]
        elif newline > position and text[newline - 1] == '\r':
            body = text[position:newline - 1]
        else:
            body = text[position:newline]
        lines.append((position, end, body))
        position = end
    return lines


_QUOTE = re.compile(' {0,3}> ?')
_BLANK = re.compile('[ \t]*')
_NONBLANK = re.compile(NON_WS)
_LAZY_ALLOWED = re.compile(r' {0,3}(?:>|[-+*][ \t]|[0-9]+[.)][ \t]|#{1,6} |`{3,}|~{3,}|<|\$|\[)')
_INDENTED = re.compile('(?: {4}|\t)')
_LIST_TAB = re.compile(r' {0,3}(?:[-+*]|[0-9]+[.)])[ \t]*\t')
_LIST = re.compile(r'( {0,3})([-+*]|[0-9]{1,9}[.)])( +)(?=' + NON_WS + r'|\Z)')
_FENCE_CLOSE = re.compile(' {0,3}(`+|~+)[ \t]*')
_FENCE_OPEN = re.compile(' {0,3}(`{3,}|~{3,})(' + DOT + '*)')
_METADATA_LINE = re.compile(r' {0,3}(?:---|\+\+\+)(?:[ \t]' + DOT + '*)?')
_FENCED_DIV = re.compile(' {0,3}:::')
_REFERENCE = re.compile(r' {0,3}\[[^\]]+\]:')
_LEADING = re.compile(' {0,3}')
_MATH_START = re.compile(r'(?:\$\$|\\\[)')
_HTML_BOUNDARY = re.compile(r'\r?\n[ \t>]*(?:\r?\n|\Z)')


def block_mask(text: str, parse_nocite: NociteParser) -> tuple[bytearray, list[dict[str, Any]]]:
    """Resolve explicit quote/list prefixes, retaining offsets in the original text."""
    mask = bytearray(len(text))
    lines = split_lines(text)
    nocite_tokens: list[dict[str, Any]] = []
    first_body = _frontmatter(lines, mask, parse_nocite, nocite_tokens)
    frames: list[dict[str, Any]] = []
    fence: dict[str, Any] | None = None
    blank = True
    opaque_until = 0
    for start, end, body in lines[first_body:]:
        if start < opaque_until:
            _fill(mask, BLOCK_BOUNDARY, start, end)
            continue
        offset = 1 if start == 0 and body[:1] == '﻿' else 0
        kept = 0
        for frame in frames:
            if frame['type'] == 'quote':
                marker = _QUOTE.match(body, offset)
                if not marker:
                    break
                offset = marker.end()
            else:
                if not body.startswith(' ' * frame['indent'], offset):
                    break
                offset += frame['indent']
            kept += 1
        rest = body[offset:]
        # Blank list lines need not carry the item's indentation, even in a fence.
        # Quote markers remain mandatory: an unmarked blank ends a quote container.
        if _BLANK.fullmatch(rest) and all(frame['type'] == 'list' for frame in frames[kept:]):
            _fill(mask, BLOCK_BOUNDARY, start, end)
            blank = True
            continue
        changed = kept != len(frames)
        if changed and not blank and fence is None and _NONBLANK.search(rest) and not _LAZY_ALLOWED.match(rest):
            fail('Lazy container continuations require explicit quote markers or list indentation')
        # A fence cannot leak out of its container. Missing quote/list indentation
        # ends that fence conservatively; a later backtick is a separate construct.
        if changed:
            fence = None
        frames = frames[:kept]
        new_container = False
        if fence is None:
            while True:
                rest = body[offset:]
                if _INDENTED.match(rest):
                    break
                quote = _QUOTE.match(rest)
                if quote:
                    offset += quote.end()
                    frames.append({'type': 'quote'})
                    new_container = True
                    continue
                if _LIST_TAB.match(rest):
                    fail('Tabs in list markers are unsupported')
                item = _LIST.match(rest)
                if not item:
                    break
                # Five or more spaces after the marker start indented code, not prose.
                padding = 1 if len(item.group(3)) > 4 else len(item.group(3))
                indent = len(item.group(1)) + len(item.group(2)) + padding
                offset += indent
                frames.append({'type': 'list', 'indent': indent})
                new_container = True
        line = body[offset:]
        content_start = start + offset
        _fill(mask, BLOCK_BOUNDARY if new_container or changed else CONTAINER_PREFIX, start, content_start)
        # A container exit without any prefix still needs an inline-span boundary.
        if changed and offset == 0 and start > 0:
            mask[start - 1] = BLOCK_BOUNDARY
        if fence is not None:
            _fill(mask, BLOCK_BOUNDARY, start, end)
            close = _FENCE_CLOSE.fullmatch(line)
            if close and close.group(1)[0] == fence['char'] and len(close.group(1)) >= fence['length']:
                fence = None
            continue
        if _BLANK.fullmatch(line):
            _fill(mask, BLOCK_BOUNDARY, start, end)
            blank = True
            continue
        if _INDENTED.match(line):
            _fill(mask, INDENTED_LINE, content_start, end)
            continue
        opening = _FENCE_OPEN.fullmatch(line)
        if opening and not (opening.group(1)[0] == '`' and '`' in opening.group(2)):
            blank = False
            fence = {'char': opening.group(1)[0], 'length': len(opening.group(1))}
            _fill(mask, BLOCK_BOUNDARY, start, end)
            continue
        if _METADATA_LINE.fullmatch(line) or (start == 0 and line[:1] == '%'):
            metadata_fail()
        if _FENCED_DIV.match(line):
            fail('Fenced div containers are unsupported')
        if _REFERENCE.match(line):
            if candidate_test(line):
                fail('Citations in reference/footnote definitions are unsupported')
            _fill(mask, BLOCK_BOUNDARY, start, end)
            continue
        # Protect line-start HTML/math before interpreting subsequent block-looking
        # lines. Prefix bytes remain in the source and are never reconstructed.
        leading = _LEADING.match(line).end()  # type: ignore[union-attr]
        opaque_start = content_start + leading
        html = html_end(text, opaque_start) if _at(text, opaque_start) == '<' else None
        math = math_end(text, opaque_start) if _MATH_START.match(line, leading) else None
        if html is not None or math is not None:
            opaque_until = html if html is not None else math  # type: ignore[assignment]
            if html is not None:
                # Raw HTML blocks can include text after the final tag (or a void tag).
                # Keep that entire block opaque through a physical/quote-only blank line.
                boundary = _HTML_BOUNDARY.search(text, html)
                opaque_until = boundary.end() if boundary else len(text)
            _fill(mask, BLOCK_BOUNDARY, start, opaque_until)
        blank = html is not None or math is not None
    return mask, nocite_tokens
