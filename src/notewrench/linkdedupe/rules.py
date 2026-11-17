# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 tianhaogutum
"""Strict exact-host parameter rules (port of ``lib/rules.js``)."""

from __future__ import annotations

import re
from typing import Any

from .jsjson import JS_WHITESPACE_CLASS, UNDEFINED, get, is_number, js_keys, js_string, sha256_hex
from .strictjson import parse_json
from .whatwg_url import domain_to_ascii


class RuleError(ValueError):
    """Invalid host rules."""


_AUTH_NAMES = frozenset([
    'sig', 'signature', 'token', 'access_token', 'auth', 'authorization', 'jwt',
    'expires', 'expiry', 'policy', 'key-pair-id', 'awsaccesskeyid', 'oauth_signature',
    'hmac', 'hash', 'api_key', 'apikey', 'hdnts', 'hdnea',
])
SUSPICIOUS = re.compile('sig|token|auth|secret|password|hmac|hash|expir', re.I | re.A)
_HOST_FORBIDDEN = re.compile(JS_WHITESPACE_CLASS + r'|[/:@?#%*\\]')
_LABEL = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?')
_RULE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}')
_PARAMETER = re.compile(r'[A-Za-z0-9_.~-]{1,128}')
_SHA256 = re.compile('[a-f0-9]{64}')


def is_protected_parameter(key: str) -> bool:
    lower = key.lower()
    return lower in _AUTH_NAMES or lower.startswith(('x-amz-', 'x-goog-', 'oauth_'))


def _fields(value: Any, allowed: list[str], where: str) -> None:
    if not isinstance(value, dict):
        raise RuleError(f'{where} must be an object')
    for key in js_keys(value):
        if key not in allowed:
            raise RuleError(f'{where}: unknown field {key}')


def _canonical_host(value: Any) -> str:
    if not isinstance(value, str) or not value or _HOST_FORBIDDEN.search(value):
        raise RuleError('Rule host must be an exact DNS hostname, without scheme, port, wildcard or path')
    # Mirror normalize_url: IDNA-map Unicode only; preserve ASCII IP spellings.
    lower = value.lower()
    host = lower if lower.isascii() else domain_to_ascii(lower)
    if not host or len(host) > 253 or not all(_LABEL.fullmatch(label) for label in host.split('.')):
        raise RuleError('Rule host must be an exact DNS hostname without empty labels or trailing dot')
    return host


def validate_rule_set(value: Any) -> dict[str, Any]:
    """Validate again at the public normalization/report boundary.

    Invalid rules must fail even with no records or ``skip_invalid``; they never
    become record-level errors.
    """
    _fields(value, ['schemaVersion', 'rules', 'source'], 'Rules')
    version = get(value, 'schemaVersion')
    if not (is_number(version) and version == 1):
        raise RuleError('Rules schemaVersion must be 1')
    source = get(value, 'source')
    _fields(source, ['name', 'sha256'], 'Rules source')
    name, sha256 = get(source, 'name'), get(source, 'sha256')
    if sha256 is UNDEFINED or sha256 is None:
        sha256 = ''
    if not isinstance(name, str) or not name or not _SHA256.fullmatch(js_string(sha256)):
        raise RuleError('Rules source requires name and SHA-256; use parseRuleFile')
    rules = get(value, 'rules')
    if not isinstance(rules, list) or not rules or len(rules) > 128:
        raise RuleError('Rules must contain 1 to 128 rules')
    ids: set[str] = set()
    targets: set[str] = set()
    result_rules = []
    for index, rule in enumerate(rules):
        where = f'/rules/{index}'
        _fields(rule, ['id', 'host', 'keepParams', 'dropParams'], where)
        rule_id = get(rule, 'id')
        if not isinstance(rule_id, str) or not _RULE_ID.fullmatch(rule_id):
            raise RuleError(f'{where}: id must be 1-64 simple characters')
        if rule_id in ids:
            raise RuleError(f'{where}: duplicate rule id {rule_id}')
        ids.add(rule_id)
        host = _canonical_host(get(rule, 'host'))
        result: dict[str, Any] = {'id': rule_id, 'host': host, 'keepParams': [], 'dropParams': []}
        for field in ('keepParams', 'dropParams'):
            params = rule.get(field)
            if params is None:
                params = []
            if (field in rule and not isinstance(rule[field], list)) or len(params) > 32:
                raise RuleError(f'{where}/{field}: expected at most 32 exact parameter names')
            for parameter in params:
                if not isinstance(parameter, str) or not _PARAMETER.fullmatch(parameter):
                    raise RuleError(f'{where}/{field}: invalid exact parameter name')
                if field == 'dropParams' and (is_protected_parameter(parameter) or SUSPICIOUS.search(parameter)):
                    raise RuleError(f'{where}: cannot drop signed/authentication parameter {parameter}')
                # One decision per canonical host/key, even if repeated with the same
                # action: duplicate declarations are ambiguous rather than last-wins.
                target = f'{host}\n{parameter}'
                if target in targets:
                    raise RuleError(f'{where}: conflicting or duplicate rule for {host} parameter {parameter}')
                targets.add(target)
                result[field].append(parameter)
        if not result['keepParams'] and not result['dropParams']:
            raise RuleError(f'{where}: rule needs keepParams or dropParams')
        result_rules.append(result)
    return {'schemaVersion': 1, 'source': dict(source), 'rules': result_rules}


def parse_rule_file(text: Any, name: str = 'rules.json') -> dict[str, Any]:
    """Strict JSON rules with exact-text provenance. No scripts, globs or overrides."""
    try:
        document = parse_json(text)
        _fields(document, ['schemaVersion', 'rules'], 'Rule file')
        return validate_rule_set({**document, 'source': {'name': name, 'sha256': sha256_hex(text)}})
    except Exception as error:  # the original wraps every failure with the file name
        raise RuleError(f'{name}: {error}') from error
