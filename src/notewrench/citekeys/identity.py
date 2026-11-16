# SPDX-License-Identifier: AGPL-3.0-only
"""Identity matching between two CSL JSON exports (DOI first, then exact URL)."""
from __future__ import annotations

import ipaddress
import re
from typing import Any

from ._js import NON_WS, WS, stringify

# Deliberately smaller than Pandoc's full key grammar: no repeated punctuation.
KEY_SOURCE = '[A-Za-z0-9_]+(?:[.:-][A-Za-z0-9_]+)*'
_KEY = re.compile(KEY_SOURCE + r'\Z')


def is_key(value: object) -> bool:
    return isinstance(value, str) and _KEY.match(value) is not None


class RekeyError(Exception):
    """A refusal with a stable machine-readable ``code`` (reported as stderr JSON)."""

    def __init__(self, code: str, message: str, details: list[Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = [] if details is None else details


_RESOLVER = re.compile(r'https?://(?:dx\.)?doi\.org/', re.IGNORECASE | re.ASCII)
_DOI = re.compile(r'10\.[0-9]+/' + NON_WS + r'+\Z')


def normalize_doi(value: object) -> str:
    if not isinstance(value, str):
        raise RekeyError('INVALID_DOI', 'DOI must be a string')
    resolver = _RESOLVER.match(value)
    doi = (value[resolver.end():] if resolver else value).lower()
    if not _DOI.match(doi):
        raise RekeyError('INVALID_DOI', f'Unsupported DOI spelling: {stringify(value)}')
    return doi


# --- WHATWG URL host validation (the parts that decide whether `new URL()` throws) ---

_FORBIDDEN_HOST = set('\0\t\n\r #/:<>?@[\\]^|')
_FORBIDDEN_DOMAIN = _FORBIDDEN_HOST | {chr(c) for c in range(0x20)} | {'%', '\x7f'}
_HEX = re.compile('[0-9A-Fa-f]{2}')
_C0_OR_SPACE = ''.join(chr(c) for c in range(0x21))


def _percent_decode(text: str) -> str:
    data = text.encode('utf-8', 'surrogatepass')
    out = bytearray()
    i = 0
    while i < len(data):
        if data[i] == 0x25 and _HEX.fullmatch(data[i + 1:i + 3].decode('latin-1')):
            out.append(int(data[i + 1:i + 3], 16))
            i += 3
        else:
            out.append(data[i])
            i += 1
    return out.decode('utf-8', 'replace')


def _ipv4_number(part: str) -> int | None:
    if not part:
        return None
    radix = 10
    if part[:2] in ('0x', '0X'):
        radix, part = 16, part[2:]
    elif len(part) >= 2 and part[0] == '0':
        radix, part = 8, part[1:]
    if not part:
        return 0
    digits = {10: '[0-9]+', 16: '[0-9A-Fa-f]+', 8: '[0-7]+'}[radix]
    if not re.fullmatch(digits, part):
        return None
    return int(part, radix)


def _ends_in_number(host: str) -> bool:
    parts = host.split('.')
    if parts[-1] == '':
        if len(parts) == 1:
            return False
        parts.pop()
    last = parts[-1]
    if last and re.fullmatch('[0-9]+', last):
        return True
    return _ipv4_number(last) is not None


def _valid_ipv4(host: str) -> bool:
    parts = host.split('.')
    if parts[-1] == '' and len(parts) > 1:
        parts.pop()
    if len(parts) > 4:
        return False
    numbers = [_ipv4_number(part) for part in parts]
    if any(number is None for number in numbers):
        return False
    if any(number > 255 for number in numbers[:-1]):  # type: ignore[operator]
        return False
    return numbers[-1] < 256 ** (5 - len(numbers))  # type: ignore[operator]


def _domain_to_ascii(domain: str) -> str | None:
    if domain.isascii():
        # Node's URL parser does not reject undecodable ASCII "xn--" labels either.
        return domain.lower()
    try:  # IDNA 2003 approximation of UTS #46 processing.
        return '.'.join(label.encode('idna').decode('ascii') if label else '' for label in domain.split('.'))
    except (UnicodeError, ValueError):
        return None


def _valid_host(host: str) -> bool:
    if not host:
        return False
    if host[0] == '[':
        if host[-1] != ']' or '%' in host:
            return False
        try:
            ipaddress.IPv6Address(host[1:-1])
        except ValueError:
            return False
        return True
    ascii_domain = _domain_to_ascii(_percent_decode(host))
    if not ascii_domain or any(char in _FORBIDDEN_DOMAIN for char in ascii_domain):
        return False
    if _ends_in_number(ascii_domain):
        return _valid_ipv4(ascii_domain)
    return True


def _valid_http_url(url: str) -> bool:
    """``new URL(url)`` succeeds with an http(s) protocol and a nonempty hostname.

    ``url`` already starts with ``http://`` or ``https://`` and has no whitespace.
    """
    # The parser first strips trailing C0 controls/spaces (none of the latter remain).
    url = url.rstrip(_C0_OR_SPACE)
    rest = url[url.index(':') + 1:]
    rest = rest.lstrip('/\\')
    authority = re.split(r'[/?#\\]', rest, maxsplit=1)[0]
    if '@' in authority:
        authority = authority[authority.rindex('@') + 1:]
        if not authority:
            return False
    host, port = authority, ''
    bracket = False
    for index, char in enumerate(authority):
        if char == '[':
            bracket = True
        elif char == ']':
            bracket = False
        elif char == ':' and not bracket:
            host, port = authority[:index], authority[index + 1:]
            break
    if port and (not re.fullmatch('[0-9]+', port) or int(port) > 65535):
        return False
    return _valid_host(host)


_HTTP_PREFIX = re.compile(r'https?://')
_HAS_WS = re.compile(WS)


def _records(value: object, side: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise RekeyError('INVALID_CSL', f'{side}: expected a CSL JSON array')
    ids: set[str] = set()
    items = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not is_key(item.get('id')):
            raise RekeyError('INVALID_ID', f'{side}[{index}]: id must be a string matching {KEY_SOURCE}')
        key = item['id']
        if key in ids:
            raise RekeyError('DUPLICATE_ID', f'{side}: duplicate id {key}')
        ids.add(key)
        doi = normalize_doi(item['DOI']) if 'DOI' in item else None
        url = None
        if 'URL' in item:
            raw = item['URL']
            if not isinstance(raw, str) or _HAS_WS.search(raw):
                raise RekeyError('INVALID_URL', f'{side}[{index}]: URL must be an absolute HTTP(S) string without whitespace')
            if not _HTTP_PREFIX.match(raw) or not _valid_http_url(raw):
                raise RekeyError('INVALID_URL', f'{side}[{index}]: unsupported URL {stringify(raw)}')
            url = raw  # No URL normalization, redirects, trimming, or case folding.
        items.append({'id': key, 'doi': doi, 'url': url})
    return items


def _index_identity(items: list[dict[str, Any]], field: str, side: str, conflicts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in items:
        if not item[field]:
            continue
        existing = index.get(item[field])
        if existing:
            conflicts.append({'code': 'DUPLICATE_IDENTITY', 'side': side, 'field': field,
                              'identity': item[field], 'ids': [existing['id'], item['id']]})
        else:
            index[item[field]] = item
    return index


def build_mapping(old_input: object, new_input: object) -> dict[str, list[dict[str, Any]]]:
    """No mapping is usable if *any* conflict exists. A mapping includes unchanged keys."""
    old_items = _records(old_input, 'old')
    new_items = _records(new_input, 'new')
    conflicts: list[dict[str, Any]] = []
    _index_identity(old_items, 'doi', 'old', conflicts)
    _index_identity(old_items, 'url', 'old', conflicts)
    doi_index = _index_identity(new_items, 'doi', 'new', conflicts)
    url_index = _index_identity(new_items, 'url', 'new', conflicts)
    mapping: list[dict[str, str]] = []
    unmatched: list[dict[str, str]] = []
    claimed: dict[str, str] = {}
    for old in old_items:
        by_doi = doi_index.get(old['doi']) if old['doi'] else None
        by_url = url_index.get(old['url']) if old['url'] else None
        if by_doi and by_url and by_doi['id'] != by_url['id']:
            conflicts.append({'code': 'IDENTITY_DISAGREEMENT', 'old': old['id'],
                              'doiTarget': by_doi['id'], 'urlTarget': by_url['id']})
            continue
        target = by_doi if by_doi is not None else by_url
        if not target:
            reason = 'no_identity_match' if old['doi'] or old['url'] else 'missing_identity'
            unmatched.append({'key': old['id'], 'reason': reason})
            continue
        if old['doi'] and target['doi'] and old['doi'] != target['doi']:
            conflicts.append({'code': 'CONTRADICTORY_DOI', 'old': old['id'], 'target': target['id']})
            continue
        if target['id'] in claimed:
            conflicts.append({'code': 'MANY_TO_ONE', 'old': [claimed[target['id']], old['id']], 'target': target['id']})
            continue
        claimed[target['id']] = old['id']
        mapping.append({'old': old['id'], 'new': target['id'], 'via': 'DOI' if by_doi else 'URL'})
    # A leftover key may otherwise silently acquire another item's identity.
    targets = {entry['new']: entry['old'] for entry in mapping}
    for entry in unmatched:
        if entry['key'] in targets:
            conflicts.append({'code': 'UNMATCHED_KEY_CAPTURE', 'key': entry['key'], 'claimedBy': targets[entry['key']]})
    return {'mapping': [] if conflicts else mapping, 'conflicts': conflicts, 'unmatched': unmatched}


# camelCase aliases matching the original JavaScript API.
buildMapping = build_mapping
normalizeDOI = normalize_doi
