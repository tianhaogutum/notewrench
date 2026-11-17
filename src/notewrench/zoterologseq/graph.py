# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 tianhaogutum
"""Read-only identity reservations for a deliberately small file-graph subset.

These claims only prevent collisions. They never identify/adopt a CSL record.
See README for the supported grammar and FORMAT_SOURCES for pinned evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata

from .model import Invalid

PAGE_SUFFIXES = {'.md', '.markdown', '.org'}
MD_PROPERTY = re.compile(r'^([^\s:]+)::[ \t]*(.*)$')
ORG_PROPERTY = re.compile(r'^#\+([^\s:]+):[ \t]*(.*)$', re.IGNORECASE)
YAML_PROPERTY = re.compile(r'^([A-Za-z][A-Za-z0-9_-]*):(?: +(.*))?$')


@dataclass(frozen=True)
class Claim:
    value: str
    path: str
    property: str
    line: int | None
    declared: str

    def source(self) -> dict:
        return {'path': self.path, 'property': self.property,
                'line': self.line, 'value': self.declared}


def graph_inventory(graph: Path) -> tuple[str, ...]:
    """Include directories/links so unsupported layouts cannot silently disappear."""
    result = []
    for folder in ('pages', 'journals'):
        root = graph / folder
        if root.is_symlink() or (root.exists() and not root.is_dir()):
            raise Invalid(f'identity coverage incomplete: expected a non-linked directory: {root}')
        if root.exists():
            for child in sorted(root.iterdir()):
                if (child.is_symlink() or child.is_dir()
                        or child.suffix.casefold() in PAGE_SUFFIXES):
                    result.append(child.relative_to(graph).as_posix())
    return tuple(result)


def plain_name(value: str) -> str:
    """Literal names, not markup/EDN/YAML/escapes; slash is a namespace separator."""
    value = value.strip()
    if (not value or any(c in '[]{}#"\'`<>|\\%:;' for c in value)
            or any(unicodedata.category(c).startswith('C') for c in value)
            or any(c.isspace() and c != ' ' for c in value)
            or any(not part or part != part.strip() for part in value.split('/'))):
        raise Invalid('unsupported identity name syntax (expected literal names or slash namespaces)')
    return value


def alias_names(value: str) -> list[str]:
    """Comma lists of literal names or whole [[page refs]], including commas in refs."""
    if not value.strip():
        return []
    names = []
    rest = value.strip()
    while rest:
        if rest.startswith('[['):
            end = rest.find(']]', 2)
            if end == -1:
                raise Invalid('unclosed alias page reference')
            name, rest = rest[2:end], rest[end + 2:].lstrip()
        else:
            name, separator, rest = rest.partition(',')
            names.append(plain_name(name))
            if not separator:
                return names
            rest = rest.lstrip()
            if not rest:
                raise Invalid('empty alias in comma list')
            continue
        names.append(plain_name(name))
        if rest:
            if not rest.startswith(',') or not rest[1:].strip():
                raise Invalid('expected comma between alias names')
            rest = rest[1:].lstrip()
    return names


def yaml_value_without_comment(value: str) -> str:
    """Strip only space-separated comments outside the subset's simple quotes.

    Validate the retained value with the existing grammar afterward: a quoted
    hash, escapes, embedded quotes and attached hashes must not be hidden.
    Tabs are rejected on the original line before this helper is called.
    """
    quote = None
    for index, char in enumerate(value):
        if quote is not None:
            if char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
        elif char == '#' and (index == 0 or value[index - 1] == ' '):
            return value[:index].rstrip(' ')
    return value


def yaml_scalar(value: str) -> str:
    """A literal string only: no YAML coercion, escaping, refs or comma splitting."""
    value = value.strip(' ')
    if not value or ',' in value:
        raise Invalid('expected a nonempty YAML string without commas')
    if value[0] in {'"', "'"}:
        if len(value) < 2 or value[-1] != value[0]:
            raise Invalid('unmatched YAML string quotes')
        value = value[1:-1]
    elif (not (value[0].isalpha() or value[0] == '_')
          or value.casefold() in {'null', 'true', 'false', 'yes', 'no', 'on', 'off'}):
        raise Invalid('unsupported YAML scalar; quote literal names starting with numbers or type words')
    return plain_name(value)


def yaml_claims(lines: list[str], opening: int) -> tuple[list[tuple[str, str, int]], int]:
    """Read the README's flat YAML subset, validating even nonidentity fields.

    Returns decoded identity strings and the first body line's index. Never
    deserialize arbitrary YAML or rewrite any source bytes.
    """
    result = []
    seen = set()
    pending: tuple[str, int] | None = None
    count = 0

    def finish_list() -> None:
        if pending is not None and count == 0:
            raise Invalid(f'identity coverage incomplete at line {pending[1]}: empty YAML block list')

    for index in range(opening + 1, len(lines)):
        line = lines[index]
        number = index + 1
        try:
            if '\t' in line:
                raise Invalid('tabs are unsupported in YAML frontmatter')
            if line == '---':
                finish_list()
                return result, index + 1
            if not line.strip() or line.lstrip(' ').startswith('#'):
                continue
            if line.startswith('  - '):
                if pending is None:
                    raise Invalid('unexpected YAML list item')
                name = yaml_scalar(yaml_value_without_comment(line[4:]))
                count += 1
                if pending[0] in {'alias', 'aliases'}:
                    result.append((name, pending[0], number))
                continue
            finish_list()
            pending = None
            match = YAML_PROPERTY.fullmatch(line)
            if not match:
                raise Invalid('unsupported YAML mapping/list layout')
            prop, value = match.groups()
            value = yaml_value_without_comment(value or '').strip(' ')
            prop = prop.casefold()
            if (prop in seen or prop in {'alias', 'aliases'}
                    and seen.intersection({'alias', 'aliases'})):
                raise Invalid(f'duplicate/ambiguous YAML {prop} property')
            seen.add(prop)
            if not value:
                if prop == 'title':
                    raise Invalid('YAML title must be one scalar')
                pending, count = (prop, number), 0
                continue
            if value.startswith('['):
                if prop == 'title' or not value.endswith(']'):
                    raise Invalid('expected a flat YAML alias/metadata list, or scalar title')
                # Commas inside strings are deliberately unsupported too, so a
                # general YAML lexer (and implicit type handling) is unnecessary.
                inner = value[1:-1]
                if not inner.strip():
                    names = []
                else:
                    # Remove at most one final separator. Every remaining slot
                    # must be a nonempty scalar, including in comma-only lists.
                    inner = inner.rstrip(' ')
                    if inner.endswith(','):
                        inner = inner[:-1]
                    names = [yaml_scalar(v) for v in inner.split(',')]
            else:
                names = [yaml_scalar(value)]
            if prop in {'alias', 'aliases', 'title'}:
                result.extend((name, prop, number) for name in names)
        except Invalid as exc:
            raise Invalid(f'identity coverage incomplete at line {number} (YAML): {exc}') from exc
    raise Invalid(f'identity coverage incomplete at line {opening + 1}: missing YAML closing ---')


def claims(relative: str, data: bytes) -> list[Claim]:
    path = Path(relative)
    result = []

    def add(value: str, prop: str, line: int | None) -> None:
        # Reserve parents as well as the full name, never the unqualified leaf.
        parts = plain_name(value).split('/')
        for end in range(1, len(parts) + 1):
            result.append(Claim('/'.join(parts[:end]), relative, prop, line, value))

    try:
        # Keep the historical filename reservation even when title overrides it.
        add(path.stem.replace('___', '/'), 'filename', None)
        text = data.decode('utf-8-sig')
    except UnicodeError as exc:
        raise Invalid('identity coverage incomplete: page is not UTF-8') from exc
    except Invalid as exc:
        raise Invalid(f'identity coverage incomplete for filename: {exc}') from exc
    lines = text.splitlines()
    opening = next((i for i, line in enumerate(lines) if line.strip()), None)
    if opening is not None and re.match(r'^\s*(?:---(?:\s|$)|%(?:YAML|TAG)\b)', lines[opening]):
        if path.suffix.casefold() == '.org' or lines[opening] != '---':
            raise Invalid(f'identity coverage incomplete at line {opening + 1}: unsupported YAML opener')
    if path.suffix.casefold() != '.org' and opening is not None and lines[opening] == '---':
        identities, body = yaml_claims(lines, opening)
        for name, prop, number in identities:
            add(name, prop, number)
        first_body = next((i for i in range(body, len(lines)) if lines[i].strip()), None)
        if first_body is not None and (MD_PROPERTY.fullmatch(lines[first_body])
                                      or lines[first_body].strip().upper() == ':PROPERTIES:'):
            raise Invalid(f'identity coverage incomplete at line {first_body + 1}: mixed YAML/native headers')
        return result
    seen = set()
    in_properties = False
    parser = ORG_PROPERTY if path.suffix.casefold() == '.org' else MD_PROPERTY
    for number, line in enumerate(lines, 1):
        if not line.strip():
            if in_properties:
                break
            continue
        if line.strip() == '---' or line.strip().upper() == ':PROPERTIES:':
            raise Invalid(f'identity coverage incomplete at line {number}: unsupported YAML/drawer placement')
        match = parser.fullmatch(line)
        if not match:
            if (in_properties and line[:1].isspace()
                    or re.match(r'^\s*(?:-\s+)?(?:alias(?:es)?|title)::', line, re.I)):
                raise Invalid(f'identity coverage incomplete at line {number}: unsupported property layout')
            break  # Later block properties are not page identity claims.
        in_properties = True
        prop, value = match.groups()
        prop = prop.casefold()
        if prop not in {'alias', 'aliases', 'title'}:
            continue
        if prop == 'aliases' or prop in seen:
            raise Invalid(f'identity coverage incomplete at line {number}: unsupported/duplicate {prop} property')
        seen.add(prop)
        try:
            names = alias_names(value) if prop == 'alias' else [plain_name(value)]
            for name in names:
                add(name, prop, number)
        except Invalid as exc:
            raise Invalid(f'identity coverage incomplete at line {number} ({prop}): {exc}') from exc
    return result
