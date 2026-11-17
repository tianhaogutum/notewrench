# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 tianhaogutum
"""Original, dependency-free CSL input validation and deterministic rendering."""
from __future__ import annotations

import calendar
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
from typing import Any

TYPES = frozenset("""article article-journal article-magazine article-newspaper bill book
broadcast chapter classic collection dataset document entry entry-dictionary
entry-encyclopedia event figure graphic hearing interview legal_case legislation
manuscript map motion_picture musical_score pamphlet paper-conference patent
performance periodical personal_communication post post-weblog regulation report
review review-book software song speech standard thesis treaty webpage""".split())
MAPPED = frozenset(('id', 'type', 'title', 'DOI', 'author', 'issued', 'URL', 'keyword'))
MANIFEST_NAME = '.zotlog-sync.json'
LOCK_NAME = '.zotlog-sync.lock'


class Invalid(ValueError):
    """Invalid or unsupported data; safe to display to the user."""


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(key: str) -> str:
    return digest(('zotlog-sync:v1:' + key).encode('utf-8'))


def collision_key(name: str) -> str:
    return unicodedata.normalize('NFKC', name).casefold()


def filename(key: str) -> str:
    if not isinstance(key, str) or not key:
        raise Invalid('id must be a nonempty string citekey (numeric CSL ids are unsupported)')
    if unicodedata.normalize('NFC', key) != key:
        raise Invalid('id must be NFC-normalized Unicode')
    if (len(key.encode('utf-8')) > 120 or not key[0].isalnum()
            or any(not (c.isalnum() or c in '._-') for c in key) or key.endswith('.')):
        raise Invalid('unsafe id: use letters/numbers and . _ -, start with a letter/number; max 120 UTF-8 bytes')
    if '___' in key:
        raise Invalid('id contains Logseq namespace filename encoding (___); namespaces are unsupported for citekeys')
    base = collision_key(key.split('.')[0])
    if base in {'con', 'prn', 'aux', 'nul', 'clock$'} or re.fullmatch(r'(com|lpt)[0-9¹²³]', base):
        raise Invalid('id uses a reserved device filename')
    return key + '.md'


def strict_json(data: bytes, label: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            if key in result:
                raise Invalid(f'{label}: duplicate JSON member {key!r}')
            result[key] = value
        return result

    def constant(value: str) -> Any:
        raise Invalid(f'{label}: non-finite JSON number {value}')

    def exact_float(token: str) -> float:
        number = float(token)
        try:
            exact = math.isfinite(number) and Decimal(repr(number)) == Decimal(token)
        except InvalidOperation:
            exact = False
        if not exact:
            raise Invalid(f'{label}: JSON number cannot round-trip without precision loss; use a string')
        return number

    try:
        value = json.loads(data.decode('utf-8-sig'), object_pairs_hook=pairs,
                           parse_constant=constant, parse_float=exact_float)
        # Detect escaped lone surrogates and floating-point overflow as well.
        canonical(value).encode('utf-8')
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Invalid(f'{label}: invalid UTF-8 JSON: {exc}') from exc


def issued_text(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        raise Invalid('issued must be a nonempty CSL date object')
    allowed = {'date-parts', 'literal', 'raw', 'circa', 'season'}
    if set(value) - allowed:
        raise Invalid('unsupported issued members: ' + ', '.join(sorted(set(value) - allowed)))
    for field in ('literal', 'raw'):
        if field in value and (not isinstance(value[field], str) or not value[field].strip()):
            raise Invalid(f'issued.{field} must be a nonempty string')
    if 'circa' in value and (not isinstance(value['circa'], (str, int, float, bool))):
        raise Invalid('issued.circa must be a string, number or boolean')
    if 'season' in value and str(value['season']) not in {'1', '2', '3', '4'}:
        raise Invalid('unsupported issued.season: expected 1..4')
    rendered = []
    parts = value.get('date-parts')
    if 'date-parts' in value:
        if not isinstance(parts, list) or not 1 <= len(parts) <= 2:
            raise Invalid('issued.date-parts must contain one date or two range endpoints')
        normalized = []
        for part in parts:
            if not isinstance(part, list) or not 1 <= len(part) <= 3:
                raise Invalid('each issued date must contain year[, month[, day]]')
            numbers = []
            for component in part:
                if type(component) is int:
                    numbers.append(component)
                elif isinstance(component, str) and re.fullmatch(r'-?[0-9]+', component):
                    if len(component.lstrip('-')) > 5:
                        raise Invalid('issued numeric date string is outside supported bounds')
                    numbers.append(int(component))
                else:
                    raise Invalid('issued date components must be integers or integer strings')
            year = numbers[0]
            if year == 0 or abs(year) > 9999:
                raise Invalid('supported issued years are -9999..-1 and 1..9999')
            if len(numbers) > 1 and not 1 <= numbers[1] <= 12:
                raise Invalid('issued month must be 1..12 (CSL season codes in month are unsupported)')
            if len(numbers) > 2 and not 1 <= numbers[2] <= calendar.monthrange(year, numbers[1])[1]:
                raise Invalid('issued day is outside the calendar month')
            normalized.append(numbers)
            rendered.append(('-' if year < 0 else '') + f'{abs(year):04d}'
                            + ''.join(f'-{number:02d}' for number in numbers[1:]))
        if len(normalized) == 2 and tuple(normalized[0]) > tuple(normalized[1]):
            raise Invalid('issued date range is reversed')
    elif 'literal' not in value and 'raw' not in value:
        raise Invalid('issued requires date-parts, literal or raw')
    # Preserve raw/literal text without guessing EDTF or natural language semantics.
    text = ' / '.join(rendered)
    for field in ('literal', 'raw', 'season', 'circa'):
        if field in value:
            text += ('; ' if text else '') + f'{field}: {value[field]}'
    return text


def author_text(value: Any) -> str:
    if not isinstance(value, list):
        raise Invalid('author must be an array of CSL name objects')
    names = []
    text_fields = {'family', 'given', 'literal', 'suffix', 'dropping-particle', 'non-dropping-particle'}
    flags = {'comma-suffix', 'static-ordering', 'parse-names'}
    for author in value:
        if not isinstance(author, dict) or set(author) - text_fields - flags:
            raise Invalid('unsupported author object or member')
        if any(not isinstance(v, str) for k, v in author.items() if k in text_fields):
            raise Invalid('author name components must be strings')
        if any(not isinstance(v, (str, int, float, bool)) for k, v in author.items() if k in flags):
            raise Invalid('author flags must be strings, numbers or booleans')
        if not any(author.get(k, '').strip() for k in ('family', 'given', 'literal')):
            raise Invalid('author needs family, given or literal')
        if 'literal' in author:
            if any(author.get(k) for k in text_fields - {'literal'}):
                raise Invalid('mixed literal and personal author names are unsupported')
            names.append(author['literal'])
        else:
            names.append(' '.join(author[k] for k in ('given', 'dropping-particle',
                         'non-dropping-particle', 'family', 'suffix') if author.get(k)))
    return '; '.join(names)


@dataclass(frozen=True)
class Item:
    key: str
    raw: dict[str, Any]

    @property
    def path(self) -> str:
        return 'pages/' + filename(self.key)

    @property
    def uid(self) -> str:
        return identity(self.key)

    @property
    def unmapped(self) -> list[str]:
        return sorted(set(self.raw) - MAPPED)


def load_items(data: bytes) -> list[Item]:
    value = strict_json(data, 'source')
    if not isinstance(value, list):
        raise Invalid('source must be a CSL-JSON array, not a Zotero JSON object, BibTeX or citation cluster')
    items = []
    seen = set()
    paths: dict[str, str] = {}
    for index, raw in enumerate(value):
        try:
            if not isinstance(raw, dict):
                raise Invalid('item must be an object')
            key = raw.get('id')
            name = filename(key)
            if key in seen:
                raise Invalid(f'duplicate citekey {key!r}')
            folded = collision_key(name)
            if folded in paths:
                raise Invalid(f'case/Unicode filename collision with {paths[folded]!r}')
            if not isinstance(raw.get('type'), str) or raw['type'] not in TYPES:
                raise Invalid('missing or unsupported CSL type')
            for field in ('title', 'DOI', 'URL', 'keyword'):
                if field in raw and not isinstance(raw[field], str):
                    raise Invalid(f'{field} must be a CSL string')
            if 'author' in raw:
                author_text(raw['author'])
            if 'issued' in raw:
                issued_text(raw['issued'])
            if 'citation-key' in raw and raw['citation-key'] != key:
                raise Invalid('citation-key differs from id; export stable citekeys in id explicitly')
            items.append(Item(key, raw))
            seen.add(key)
            paths[folded] = key
        except (Invalid, UnicodeError) as exc:
            raise Invalid(f'item[{index}]: {exc}') from exc
    return sorted(items, key=lambda item: item.key)


def escaped(value: str) -> str:
    """Keep metadata on one line, and inert in Logseq/Markdown/HTML."""
    special = set('&<>"\'[](){}*_`#\\|')
    return ''.join(f'&#{ord(c)};' if c in special or unicodedata.category(c).startswith('C') or (c.isspace() and c != ' ') else c
                   for c in value)


def markers(uid: str) -> tuple[bytes, bytes]:
    return (f'- <!-- zotlog-sync:begin {uid} -->'.encode(),
            f'- <!-- zotlog-sync:end {uid} -->'.encode())


def block(item: Item, newline: bytes = b'\n') -> bytes:
    begin, end = markers(item.uid)
    lines = [begin.decode(), '- Bibliography (managed by zotlog-sync)',
             '  source-format:: CSL-JSON', '  csl-type:: ' + item.raw['type']]
    fields = [('title', 'bibliography-title'), ('DOI', 'doi'), ('URL', 'url'), ('keyword', 'keywords')]
    for source, prop in fields:
        if source in item.raw:
            lines.append(f'  {prop}:: {escaped(item.raw[source])}')
    if 'author' in item.raw:
        lines.append('  authors:: ' + escaped(author_text(item.raw['author'])))
    if 'issued' in item.raw:
        lines.append('  issued:: ' + escaped(issued_text(item.raw['issued'])))
    lines.extend(['  - Source record (CSL JSON, including unmapped fields)', '    ```json'])
    # JSON strings encode newlines, so source text cannot escape the fence.
    lines.extend('    ' + line for line in canonical(item.raw).rstrip('\n').split('\n'))
    lines.extend(['    ```', end.decode()])
    return newline.join(line.encode('utf-8') for line in lines) + newline


def new_page(item: Item) -> bytes:
    header = (f'citekey:: {item.key}\nzotlog-source:: csl-json\n'
              f'zotlog-identity:: {item.uid}\n\n').encode('utf-8')
    return header + block(item) + b'\n- Notes\n'
