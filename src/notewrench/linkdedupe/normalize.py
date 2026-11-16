# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 linkdedupe contributors
"""Conservative, explained URL normalization (port of ``lib/normalize.js``)."""

from __future__ import annotations

import re
from typing import Any

from .jsjson import JS_WHITESPACE_CLASS, js_string, utf16_key
from .rules import SUSPICIOUS, is_protected_parameter, validate_rule_set
from .whatwg_url import HostError, domain_to_ascii, port_number, validate_special_authority


class URLError(ValueError):
    """A URL that linkdedupe refuses to normalize."""


class OptionsError(ValueError):
    """Invalid normalization options."""


TRACKING_PARAMS = (
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content', 'utm_id',
    'utm_source_platform', 'utm_creative_format', 'utm_marketing_tactic',
    'gclid', 'dclid', 'fbclid', 'msclkid', 'mc_cid', 'mc_eid',
)
_TRACKING = frozenset(TRACKING_PARAMS)
_ALLOWED_OPTIONS = ('keepTracking', 'dropParams', 'stripFragment', 'sortQuery', 'https', 'stripTrailingSlash', 'rules')
_CUSTOM_PARAM = re.compile(r'(utm_|pk_|mtm_)[a-z][a-z0-9_]{0,47}')
_BAD_CHARS = re.compile(JS_WHITESPACE_CLASS + '|[\x00-\x1f\x7f\\\\]')
_BAD_PERCENT = re.compile('%(?![a-fA-F0-9]{2})')
_URL = re.compile(r'([A-Za-z][A-Za-z0-9+.-]*)://([^/?#]+)([^?#]*)(\?[^#]*)?(#.*)?', re.S)
_AUTHORITY = re.compile(r'(\[[^\]]+\]|[^:]+)(?::([0-9]+))?')
_TRAILING_SLASHES = re.compile('/+$')


def validate_options(options: Any = None) -> dict[str, Any]:
    options = {} if options is None else options
    if not isinstance(options, dict):
        raise OptionsError('normalization options must be an object')
    for key, value in options.items():
        if key not in _ALLOWED_OPTIONS:
            raise OptionsError(f'Unknown normalization option: {key}')
        if key not in ('dropParams', 'rules') and not isinstance(value, bool):
            raise OptionsError(f'{key} must be boolean')
    if 'dropParams' in options and not isinstance(options['dropParams'], list):
        raise OptionsError('dropParams must be an array')
    drop_params = options.get('dropParams', [])
    if len(drop_params) > 32:
        raise OptionsError('At most 32 custom parameters are allowed')
    for key in drop_params:
        if (not isinstance(key, str) or SUSPICIOUS.search(key)
                or (key not in _TRACKING and not _CUSTOM_PARAM.fullmatch(key))):
            raise OptionsError(f'Unsafe custom parameter: {js_string(key)}. '
                               'Use an exact utm_, pk_ or mtm_ tracking name; no regex or wildcard.')
    if options.get('keepTracking') and drop_params:
        raise OptionsError('--keep-tracking conflicts with --drop-param')
    rules = None if options.get('rules') is None else validate_rule_set(options['rules'])
    if options.get('keepTracking') and rules and any(rule['dropParams'] for rule in rules['rules']):
        raise OptionsError('--keep-tracking conflicts with host dropParams')
    return {'keepTracking': False, 'stripFragment': False, 'sortQuery': False, 'https': False,
            'stripTrailingSlash': False, **options, 'dropParams': list(drop_params), 'rules': rules}


def decode_uri_component(text: str) -> str:
    """ECMAScript ``decodeURIComponent``; raises ``ValueError`` (URIError)."""
    out: list[str] = []
    index, length = 0, len(text)
    while index < length:
        char = text[index]
        if char != '%':
            out.append(char)
            index += 1
            continue
        octets = bytearray()
        try:
            octets.append(int(text[index + 1:index + 3], 16) if len(text[index + 1:index + 3]) == 2 else -1)
        except ValueError:
            raise ValueError('URI malformed') from None
        index += 3
        lead = octets[0]
        if lead >= 0x80:
            if lead & 0xE0 == 0xC0:
                count = 2
            elif lead & 0xF0 == 0xE0:
                count = 3
            elif lead & 0xF8 == 0xF0:
                count = 4
            else:
                raise ValueError('URI malformed')
            for _ in range(count - 1):
                part = text[index + 1:index + 3]
                if text[index:index + 1] != '%' or len(part) != 2:
                    raise ValueError('URI malformed')
                try:
                    byte = int(part, 16)
                except ValueError:
                    raise ValueError('URI malformed') from None
                if byte & 0xC0 != 0x80:
                    raise ValueError('URI malformed')
                octets.append(byte)
                index += 3
        try:
            out.append(bytes(octets).decode('utf-8'))
        except UnicodeDecodeError:
            raise ValueError('URI malformed') from None
    return ''.join(out)


def _query_key(token: str) -> str:
    return decode_uri_component(token.split('=', 1)[0].replace('+', ' '))


# Use the WHATWG URL rules only for validation. Never serialize: that would change
# dot segments, empty paths, query value encodings, and other application semantics.
def normalize_url(input: Any, options: Any = None) -> dict[str, Any]:
    config = validate_options(options)
    if not isinstance(input, str) or not input:
        raise URLError('URL must be a nonempty string')
    if _BAD_CHARS.search(input) or _BAD_PERCENT.search(input):
        raise URLError('Bad URL: whitespace, control, backslash or malformed percent escape')
    match = _URL.fullmatch(input)
    if not match:
        raise URLError('Bad URL: expected absolute http(s) URL')
    raw_scheme, authority = match.group(1), match.group(2)
    query = match.group(4) or ''
    scheme = raw_scheme.lower()
    if scheme not in ('http', 'https'):
        raise URLError(f'Unsupported protocol: {scheme}')
    if '@' in authority:
        raise URLError('Credential-bearing URLs are unsupported')
    try:
        validate_special_authority(authority)
    except HostError:
        raise URLError('Bad URL: invalid host or port') from None
    host_match = _AUTHORITY.fullmatch(authority)
    if not host_match:
        raise URLError('Bad URL: invalid authority')
    host = host_match.group(1)
    tokens = query[1:].split('&') if query else []
    try:
        keys = [_query_key(token) for token in tokens]
    except ValueError:
        raise URLError('Bad URL: query key is not valid UTF-8') from None
    guards = [key for key in keys if is_protected_parameter(key)]
    # Match the same textual host identity used by normalization. In particular,
    # do not broaden % escapes, shorthand IPs, subdomains or trailing-dot hosts.
    match_host = host.lower() if host.isascii() else domain_to_ascii(host.lower())
    rule_set = config['rules']
    matching = []
    for index, rule in enumerate(rule_set['rules'] if rule_set else []):
        if rule['host'] == match_host:
            matching.append({**rule, 'source': {**rule_set['source'], 'pointer': f'/rules/{index}'}})
    if guards:
        return {
            'url': input, 'normalizationReasons': [],
            'protections': [{'rule': 'signed-or-authenticated-url', 'parameters': list(dict.fromkeys(guards))}],
            'ruleComparisons': [{'ruleId': rule['id'], 'host': rule['host'], 'source': rule['source'],
                                 'beforeUrl': input, 'afterUrl': input, 'status': 'protected',
                                 'matchedParameters': []} for rule in matching],
        }

    # Each comparison recomputes from the original bytes so a keep decision can
    # restore a key removed by the baseline. Rules have disjoint host/key targets.
    def apply_rules(active: list[dict[str, Any]]) -> dict[str, Any]:
        scheme, path, query, fragment = match.group(1), match.group(3), match.group(4) or '', match.group(5) or ''
        host, port = host_match.group(1), host_match.group(2)
        scheme = scheme.lower()
        reasons: list[dict[str, Any]] = []

        def reason(rule: str, **details: Any) -> None:
            reasons.append({'rule': rule, **details})

        if scheme != match.group(1):
            reason('scheme-case')
        lower_host = host.lower()
        if host != lower_host:
            reason('host-case')
        host = lower_host
        if not host.isascii():
            host = domain_to_ascii(host)
            if not host:
                raise URLError('Bad URL: invalid IDNA host')
            reason('unicode-host-to-idna')
        if port is not None and port_number(port) == (443 if scheme == 'https' else 80):
            reason('default-port', port=port)
            port = None
        drop = frozenset() if config['keepTracking'] else frozenset((*TRACKING_PARAMS, *config['dropParams']))
        decisions: dict[str, tuple[dict[str, Any], str]] = {}
        for rule in active:
            for field, action in (('keepParams', 'keep'), ('dropParams', 'drop')):
                for parameter in rule[field]:
                    decisions[parameter] = (rule, action)
        remaining = []
        for index, token in enumerate(tokens):
            key = keys[index]
            decision = decisions.get(key)
            if decision:
                rule, action = decision
                reason('host-parameter', action=action, parameter=key, queryIndex=index,
                       ruleId=rule['id'], source=rule['source'])
            elif key in drop:
                reason('tracking-parameter', parameter=key, queryIndex=index)
            if (decision[1] == 'keep') if decision else key not in drop:
                remaining.append((token, key, index))
        if config['sortQuery'] and len(remaining) > 1:
            before = [item[2] for item in remaining]
            remaining.sort(key=lambda item: (utf16_key(item[1]), item[2]))
            if before != [item[2] for item in remaining]:
                reason('query-order-opt-in')
        # Preserve an existing empty '?' and empty tokens, unless all tokens were removed.
        query = '?' + '&'.join(item[0] for item in remaining) if query and remaining else ''
        if config['stripFragment'] and fragment:
            fragment = ''
            reason('fragment-opt-in')
        if config['stripTrailingSlash'] and path.endswith('/'):
            path = _TRAILING_SLASHES.sub('', path)
            reason('trailing-slash-opt-in')
        if config['https'] and scheme == 'http':
            scheme = 'https'
            reason('http-to-https-opt-in')
            # Promotion can make an originally non-default port the target default.
            # Remove it now so a second pass cannot change the key again.
            if port is not None and port_number(port) == 443:
                reason('https-default-port-opt-in', port=port)
                port = None
        url = f'{scheme}://{host}{"" if port is None else ":" + port}{path}{query}{fragment}'
        return {'url': url, 'normalizationReasons': reasons, 'protections': []}

    if not matching:
        return {**apply_rules([]), 'ruleComparisons': []}
    result = apply_rules([])
    comparisons = []
    for index, rule in enumerate(matching):
        before_url = result['url']
        result = apply_rules(matching[:index + 1])
        matched = []
        for query_index, parameter in enumerate(keys):
            action = 'keep' if parameter in rule['keepParams'] else 'drop' if parameter in rule['dropParams'] else None
            if action:
                matched.append({'parameter': parameter, 'action': action, 'queryIndex': query_index})
        comparisons.append({'ruleId': rule['id'], 'host': rule['host'], 'source': rule['source'],
                            'beforeUrl': before_url, 'afterUrl': result['url'],
                            'status': 'applied' if matched else 'no-match', 'matchedParameters': matched})
    return {**result, 'ruleComparisons': comparisons}
