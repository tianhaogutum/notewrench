# SPDX-License-Identifier: AGPL-3.0-only
"""Pandoc bracket-citation scanner: replaces only original key spans."""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ._js import byte_length, candidate_test, email_local, from_units, is_ws, to_units
from .blocks import BLOCK_BOUNDARY, block_mask, escaped, html_end, math_end
from .identity import KEY_SOURCE, RekeyError, is_key

# Metadata has its own bare-key grammar; never expose scalars to prose scanning.
_METADATA_GROUP = re.compile(r'[ \t]*\[[ \t]*-?@' + KEY_SOURCE + r'(?:[ \t]*;[ \t]*-?@' + KEY_SOURCE + r')*[ \t]*\][ \t]*')
_METADATA_LIST = re.compile('[ \t]*-?@' + KEY_SOURCE + '(?:[ \t]*,[ \t]*-?@' + KEY_SOURCE + ')*[ \t]*')
_FLOW_ITEM = "(?:'-?@" + KEY_SOURCE + "'|\"-?@" + KEY_SOURCE + '")'
_METADATA_FLOW = re.compile(r'(\[[ \t]*' + _FLOW_ITEM + '(?:[ \t]*,[ \t]*' + _FLOW_ITEM + r')*[ \t]*\])[ \t]*(?:#[^\r\n]*)?')
_METADATA_BLOCK_ITEM = re.compile('(' + _FLOW_ITEM + ')[ \t]*(?:#[^\r\n]*)?')
_WILDCARD = re.compile('[ \t]*@\\*[ \t]*')
_MARKED_KEY = re.compile('@(' + KEY_SOURCE + ')')


def _metadata_tokens(content: str, flow: Any = False) -> list[dict[str, Any]] | None:
    if flow == 'block':
        item = _METADATA_BLOCK_ITEM.fullmatch(content)
        if not item:
            return None
        content = item.group(1)
    elif flow:
        # Validate the whole physical line first, then exclude its opaque comment.
        # Match literal keys in the original sequence; never decode quoted items.
        sequence = _METADATA_FLOW.fullmatch(content)
        if not sequence:
            return None
        content = sequence.group(1)
    else:
        # Only literal ASCII padding around the complete standalone wildcard is opaque.
        if _WILDCARD.fullmatch(content):
            return []
        if not _METADATA_GROUP.fullmatch(content) and not _METADATA_LIST.fullmatch(content):
            return None
    return [{'key': match.group(1), 'offset': match.start() + 1} for match in _MARKED_KEY.finditer(content)]


_CITATION_KEY = re.compile(KEY_SOURCE)
_PUNCTUATION = re.compile(r'[!-/:-@\[-`{-~]')
_AFFIX_FORBIDDEN = frozenset('\r\n[]{}<>`$*_')
_SPECIAL = re.compile(r'[<$\\`\[@]')
_ATTACHMENT = re.compile(r'[ \t]*(?:\r?\n[ \t]*)?([(\[{])')
_ATTRIBUTE = re.compile(r'[ \t]*(?:\r?\n[ \t]*)?\{')
_TEX_COMMAND = re.compile('[A-Za-z]')


def _position(text: str, offset: int) -> dict[str, int]:
    last = text.rfind('\n', 0, offset)
    return {'offset': offset, 'line': text.count('\n', 0, offset) + 1, 'column': offset - last}


# A bounded lexical grammar, not an affix/locator interpreter. Never return
# partial tokens: a later ambiguous item invalidates the entire bracket group.
def _citation_tokens(group: str) -> list[dict[str, Any]] | None:
    if not group or group[0] != '[' or group[-1] != ']':
        return None
    tokens: list[dict[str, Any]] = []
    item_start = 1
    has_marker = False
    last = len(group) - 1
    i = 1
    while i < last:
        char = group[i]
        if char == '\\':
            if not _PUNCTUATION.fullmatch(group[i + 1:i + 2]):
                return None
            i += 2
        elif char == ';':
            if not has_marker:
                return None
            has_marker = False
            i += 1
            item_start = i
        elif char == '@':
            marker = i - 1 if group[i - 1] == '-' and not escaped(group, i - 1) else i
            if has_marker or (marker != item_start and group[marker - 1] not in (' ', '\t')):
                return None
            match = _CITATION_KEY.match(group, i + 1)
            if not match:
                return None
            end = match.end()
            # Do not interpret a valid key prefix of an unsupported identifier as a key.
            if end != last and group[end] not in (' ', '\t', ',', ';'):
                return None
            tokens.append({'key': match.group(), 'offset': i + 1})
            has_marker = True
            i = end
        else:
            if char in _AFFIX_FORBIDDEN:
                return None
            i += 1
    return tokens if has_marker else None


def _code_span_end(text: str, start: int, mask: bytearray) -> int | None:
    length = len(text)
    end_run = start
    while end_run < length and text[end_run] == '`':
        end_run += 1
    size = end_run - start
    cursor = end_run
    # Indentation alone does not end a paragraph: an inline code span can cross
    # these conservatively protected lines, or even close on one of them.
    while cursor < length and mask[cursor] != BLOCK_BOUNDARY:
        if text[cursor] != '`':
            cursor += 1
            continue
        end = cursor
        while end < length and text[end] == '`' and mask[end] != BLOCK_BOUNDARY:
            end += 1
        if end - cursor == size:
            return end
        cursor = end
    return None


# Protect delimiters inside quoted link titles / attributes as well as escapes.
def _delimited_end(text: str, start: int, opener: str) -> int:
    closer = {'(': ')', '[': ']', '{': '}'}[opener]
    depth = 1
    quote = None
    cursor = start + 1
    length = len(text)
    while cursor < length and depth:
        if escaped(text, cursor):
            cursor += 1
            continue
        char = text[cursor]
        if quote:
            if char == quote:
                quote = None
            cursor += 1
            continue
        if opener != '[' and (char == '"' or char == "'"):
            quote = char
            cursor += 1
            continue
        if char == opener:
            depth += 1
        if char == closer:
            depth -= 1
        cursor += 1
    return cursor


def _mapping_entries(mapping: object) -> list[tuple[object, object]]:
    if isinstance(mapping, Mapping):
        return list(mapping.items())
    if isinstance(mapping, (list, tuple)):
        return [(entry.get('old'), entry.get('new')) if isinstance(entry, Mapping) else (None, None) for entry in mapping]
    raise RekeyError('INVALID_MAPPING', 'Expected mapping array or Map')


def rekey_markdown(text: str, mapping: object) -> dict[str, Any]:
    """Replace only original key spans. Unsupported citation groups are reported intact.

    ``mapping`` is a list of ``{"old": ..., "new": ...}`` entries (as returned by
    :func:`build_mapping`) or a ``{old: new}`` dict. Offsets, lengths and columns
    in the result count UTF-16 code units, like the original JavaScript tool.
    """
    if not isinstance(text, str):
        raise TypeError('Markdown must be a string')
    if '\0' in text or re.search('\r(?!\n)', text):
        raise RekeyError('UNSUPPORTED_TEXT', 'NUL and bare CR are unsupported')
    entries = _mapping_entries(mapping)
    keys: dict[str, str] = {}
    for old_key, new_key in entries:
        if not is_key(old_key) or not is_key(new_key) or old_key in keys:
            raise RekeyError('INVALID_MAPPING', 'Mapping keys must be unique supported citekeys')
        keys[old_key] = new_key  # type: ignore[index]
    text = to_units(text)
    mask, nocite_tokens = block_mask(text, _metadata_tokens)
    edits: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []
    # Scanning and edit offsets are monotone: locate without rescanning the document
    # for every key, and without allocating a line index proportional to line count.
    state = {'located': 0, 'byte_located': 0, 'byte_offset': 0, 'line': 1, 'line_start': 0}

    def locate(offset: int) -> dict[str, int]:
        if offset < state['located']:
            return _position(text, offset)
        newline = text.find('\n', state['located'])
        while newline != -1 and newline < offset:
            state['line'] += 1
            state['line_start'] = newline + 1
            newline = text.find('\n', state['line_start'])
        state['located'] = offset
        return {'offset': offset, 'line': state['line'], 'column': offset - state['line_start'] + 1}

    def diagnose(offset: int, reason: str, length: int = 1) -> None:
        unsupported.append({**locate(offset), 'length': length, 'reason': reason})

    def record_key(key: str, offset: int) -> None:
        if key not in keys:
            unresolved.append({'key': key, **locate(offset), 'reason': 'no_mapping'})
        elif keys[key] != key:
            state['byte_offset'] += byte_length(text[state['byte_located']:offset])
            state['byte_located'] = offset
            edits.append({'key': key, 'replacement': keys[key], **locate(offset), 'length': len(key),
                          'byteOffset': state['byte_offset'], 'byteLength': len(key.encode('utf-8'))})

    for token in nocite_tokens:
        record_key(token['key'], token['offset'])
    length = len(text)
    i = 0
    while i < length:
        if mask[i]:
            following = mask.find(0, i)
            i = length if following == -1 else following
            continue
        char = text[i]
        if char not in '<$\\`[@':
            # Nothing else is significant outside masked regions: jump to the next candidate.
            special = _SPECIAL.search(text, i)
            i = length if special is None else special.start()
            continue
        opaque = html_end(text, i) if char == '<' else math_end(text, i, mask)
        if opaque is not None:
            i = opaque
            continue
        if char == '\\':
            following_char = text[i + 1:i + 2]
            if _TEX_COMMAND.fullmatch(following_char):
                raise RekeyError('UNSUPPORTED_MARKDOWN', 'Raw TeX commands are unsupported')
            if following_char in ('(', '[') and following_char:
                # Escaped bracket literal is protected. TeX math delimiters fail closed.
                if following_char == '(':
                    raise RekeyError('UNSUPPORTED_MARKDOWN', 'TeX math delimiters are unsupported')
                close = text.find(']', i + 2)
                if close != -1:
                    i = close + 1
                    continue
            i += 2
            continue
        if char == '`':
            end = _code_span_end(text, i, mask)
            if end is not None:
                i = end
            else:  # Unmatched backtick runs are literal in Markdown.
                i += 1
                while i < length and text[i] == '`':
                    i += 1
            continue
        if char != '[' or escaped(text, i):
            if char == '@':
                marker = i - 1 if i > 0 and text[i - 1] == '-' else i
                following_char = text[i + 1:i + 2]
                if ((marker == 0 or not email_local(text[marker - 1]))
                        and following_char and not is_ws(following_char) and following_char not in '@]'):
                    diagnose(i, 'author_in_text_citation_unsupported')
            i += 1
            continue
        # Consume a whole balanced bracket region, including nested brackets and code.
        start = i
        end = i + 1
        depth = 1
        protected_content = False
        while end < length and depth:
            if mask[end]:
                protected_content = True
                end += 1
                continue
            if escaped(text, end):
                end += 1
                continue
            current = text[end]
            opaque = html_end(text, end) if current == '<' else math_end(text, end, mask)
            if opaque is not None:
                protected_content = True
                end = opaque
                continue
            if current == '`':
                code_end = _code_span_end(text, end, mask)
                if code_end is not None:
                    protected_content = True
                    end = code_end
                    continue
            if current == '[':
                depth += 1
            if current == ']':
                depth -= 1
            end += 1
        closed = depth == 0
        group = text[start:end]
        # Inline links/images, reference uses and attributed spans are unsupported.
        suffix = _ATTACHMENT.match(text, end)
        attachment = suffix is not None
        image = start > 0 and text[start - 1] in ('!', '^') and not escaped(text, start - 1)
        # Consume attachments before diagnosing so ranges cover the full context.
        i = end
        if suffix:
            i = _delimited_end(text, suffix.end() - 1, suffix.group(1))
            attribute = _ATTRIBUTE.match(text, i)
            if attribute:
                i = _delimited_end(text, attribute.end() - 1, '{')
        tokens = _citation_tokens(group) if closed and not attachment and not image and not protected_content else None
        if tokens is not None:
            for token in tokens:
                record_key(token['key'], start + token['offset'])
        else:
            range_start = start - 1 if image else start
            if candidate_test(group):
                diagnose(range_start, 'unsupported_citation_group_or_context' if closed else 'unclosed_citation_group',
                         i - range_start)
            elif suffix and candidate_test(text[end:i]):
                diagnose(range_start, 'citation_in_link_or_attribute_unsupported', i - range_start)
    parts = []
    cursor = 0
    for edit in edits:
        parts.append(text[cursor:edit['offset']] if edit['offset'] > cursor else '')
        parts.append(edit['replacement'])
        cursor = edit['offset'] + edit['length']
    parts.append(text[cursor:])
    return {'text': from_units(''.join(parts)), 'edits': edits, 'unresolved': unresolved, 'unsupported': unsupported}


rekeyMarkdown = rekey_markdown
