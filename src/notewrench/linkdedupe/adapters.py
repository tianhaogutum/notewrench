# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 tianhaogutum
"""Karakeep native and Wallabag v2 record adapters (port of ``lib/adapters.js``)."""

from __future__ import annotations

from typing import Any

from .jsjson import UNDEFINED, get, is_finite, is_number, is_safe_integer, js_string


class AdapterError(ValueError):
    """An export or record does not match its adapter's contract."""


def _object(value: Any) -> bool:
    return isinstance(value, dict)


def _strings(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _nullable_string(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _require(ok: bool, message: str) -> None:
    if not ok:
        raise AdapterError(message)


def detect_format(document: Any, format: Any = 'auto') -> str:
    if format not in ('auto', 'karakeep', 'wallabag-v2') or not isinstance(format, str):
        raise AdapterError(f'Unsupported format: {js_string(format)}')
    if format == 'karakeep':
        _require(_object(document) and isinstance(document.get('bookmarks'), list),
                 'Karakeep native must be an object with bookmarks array')
        return format
    if format == 'wallabag-v2':
        _require(isinstance(document, list), 'Wallabag v2 must be a JSON array (not an API response)')
        return format
    if _object(document) and isinstance(document.get('bookmarks'), list):
        return 'karakeep'
    # An arbitrary array of URL objects is ambiguous. Native v2 export markers are
    # required for automatic detection; --format handles deliberately minimal data.
    if isinstance(document, list) and document and any(
            _object(row) and 'url' in row and 'created_at' in row and 'is_archived' in row for row in document):
        return 'wallabag-v2'
    raise AdapterError('Unknown or ambiguous JSON shape; specify --format karakeep|wallabag-v2 for supported exports')


def adapt_document(document: Any, format: str) -> tuple[list[Any], dict[str, Any] | None]:
    """Returns ``(rows, rootMetadata)``."""
    if format == 'karakeep':
        if 'lists' in document:
            _require(isinstance(document['lists'], list), 'Karakeep root lists must be an array')
        return document['bookmarks'], {key: value for key, value in document.items() if key != 'bookmarks'}
    return document, None


def adapt_record(raw: Any, format: str) -> dict[str, Any]:
    _require(_object(raw), 'Record must be an object')
    _require(_strings(get(raw, 'tags')), 'tags must be an array of strings')
    _require(_nullable_string(get(raw, 'title')), 'title must be a string or null')
    notes: list[dict[str, Any]] = []
    if format == 'karakeep':
        _require(is_finite(get(raw, 'createdAt')), 'createdAt must be finite Unix seconds')
        _require(_nullable_string(get(raw, 'note')), 'note must be a string or null')
        if 'lists' in raw:
            _require(_strings(raw['lists']), 'lists must be an array of string IDs')
        if 'archived' in raw:
            _require(isinstance(raw['archived'], bool), 'archived must be boolean')
        if raw['note'] is not None:
            notes.append({'field': 'note', 'value': raw['note']})
        content = get(raw, 'content')
        if content is None:
            url = None
        else:
            _require(_object(content), 'content must be a link/text object or null')
            kind = get(content, 'type')
            if kind == 'link' and isinstance(kind, str):
                _require(isinstance(get(content, 'url'), str), 'link content.url must be a string')
                url = content['url']
            elif kind == 'text' and isinstance(kind, str):
                _require(isinstance(get(content, 'text'), str), 'text content.text must be a string')
                url = None
            else:
                raise AdapterError(f'Unsupported Karakeep content type: {js_string(kind)}')
    else:
        _require(isinstance(get(raw, 'url'), str), 'url must be a string')
        url = raw['url']
        if 'id' in raw:
            _require(isinstance(raw['id'], str) or is_safe_integer(raw['id']), 'id must be a string or safe integer')
        for field in ('is_archived', 'is_starred'):
            if field in raw:
                value = raw[field]
                _require(isinstance(value, bool) or (is_number(value) and value in (0, 1)),
                         f'{field} must be boolean or integer 0/1')
        for field in ('created_at', 'updated_at', 'content'):
            if field in raw:
                _require(_nullable_string(raw[field]), f'{field} must be a string or null')
        if 'annotations' in raw:
            _require(isinstance(raw['annotations'], list) and all(_object(note) for note in raw['annotations']),
                     'annotations must be an array of objects')
            for index, value in enumerate(raw['annotations']):
                notes.append({'field': 'annotations', 'index': index, 'value': value})
        # Not part of the native v2 contract; retain extensions explicitly as notes.
        if 'note' in raw:
            _require(_nullable_string(raw['note']), 'note extension must be a string or null')
            if raw['note'] is not None:
                notes.append({'field': 'note', 'value': raw['note']})
    native_id = raw.get('id', UNDEFINED)
    return {'originalUrl': url, 'title': raw['title'], 'tags': raw['tags'], 'notes': notes,
            'nativeId': None if native_id is UNDEFINED else native_id, 'raw': raw}
