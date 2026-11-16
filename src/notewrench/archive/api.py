"""Import user-saved API response pages; never make network requests.

SPDX-License-Identifier: AGPL-3.0-only
"""

import hashlib
import re

from .core import (MAX_INPUT_BYTES, InputError, build, decode, issue, json_text, payload_metadata, require,
                   wallabag)

MAX_PAGES = 10000


def _string(value, path):
    require(isinstance(value, str) and bool(value), path, 'expected nonempty string')


def _cursor(value, path):
    require(value is None or (isinstance(value, str) and bool(value)), path,
            'expected null or nonempty cursor string')


def _integer(value, path, minimum=0):
    require(type(value) is int and value >= minimum, path,
            f'expected integer >= {minimum}')


def _keys(value, allowed, path):
    require(not (set(value) - set(allowed)), path, 'unknown manifest fields')


def _responses(raw, load_page, require_page_hashes=False):
    """Validate one declared namespace and load a bounded local page set."""
    manifest = decode(raw)
    require(isinstance(manifest, dict), '$', 'expected API pagination manifest object')
    _keys(manifest, ('schema_version', 'source', 'pages'), '$')
    require(type(manifest.get('schema_version')) is int and manifest['schema_version'] == 1,
            '$.schema_version', 'expected 1')
    source = manifest.get('source')
    require(isinstance(source, dict), '$.source', 'expected source object')
    _keys(source, ('format', 'namespace', 'user_id'), '$.source')
    kind = source.get('format')
    require(kind in ('wallabag-api', 'karakeep-api'), '$.source.format',
            'expected wallabag-api or karakeep-api')
    _string(source.get('namespace'), '$.source.namespace')
    if kind == 'karakeep-api':
        _string(source.get('user_id'), '$.source.user_id')
    pages = manifest.get('pages')
    require(isinstance(pages, list) and 1 <= len(pages) <= MAX_PAGES, '$.pages',
            'expected 1..10000 saved pages')
    identity_key = 'page' if kind == 'wallabag-api' else 'cursor'
    identities, filenames = set(), set()
    for i, page in enumerate(pages):
        path = f'$.pages[{i}]'
        require(isinstance(page, dict), path, 'expected page descriptor')
        _keys(page, ('file', 'namespace', identity_key, 'sha256'), path)
        if 'sha256' in page:
            require(isinstance(page['sha256'], str) and re.fullmatch('[0-9a-f]{64}', page['sha256']),
                    'page_sha256', f'{path}.sha256: expected lowercase SHA-256 hex (64 characters)')
        else:
            require(not require_page_hashes, 'missing_page_pin',
                    f'{path}.sha256: required by --require-page-hashes')
        require(page.get('namespace') == source['namespace'], 'source_namespace',
                f'{path} does not match the manifest namespace')
        _string(page.get('file'), path + '.file')
        require(identity_key in page, 'page_identity', f'{path}.{identity_key} is required')
        identity = page[identity_key]
        if kind == 'wallabag-api':
            _integer(identity, path + '.page', 1)
        else:
            _cursor(identity, path + '.cursor')
        require(identity not in identities and page['file'] not in filenames,
                'duplicate_page', f'repeated {identity_key} or response file at {path}')
        identities.add(identity)
        filenames.add(page['file'])
    remaining = MAX_INPUT_BYTES - len(raw)
    loaded = []
    for i, page in enumerate(pages):
        try:
            page_raw = load_page(page['file'], remaining)
        except InputError as exc:
            if str(exc).startswith('local_page_path:'):
                raise InputError(f"local_page_path: $.pages[{i}].file ({page['file']!r}): "
                                 f"{str(exc).removeprefix('local_page_path: ')}") from exc
            raise
        remaining -= len(page_raw)
        require(remaining >= 0, '$.pages', 'manifest and pages exceed the aggregate 64 MiB limit')
        digest = hashlib.sha256(page_raw).hexdigest()
        expected = page.get('sha256')
        require(expected is None or digest == expected, 'page_sha256',
                f"$.pages[{i}] ({page['file']!r}): hash mismatch; expected {expected}, actual {digest}")
        response = decode(page_raw)
        require(isinstance(response, dict), f'$.pages[{i}]', 'expected API response object')
        loaded.append({'page_index': i, 'file': page['file'], identity_key: page[identity_key],
                       'sha256': digest, 'expected_sha256': expected, 'actual_sha256': digest,
                       'status': 'verified' if expected is not None else 'unpinned', 'response': response})
    return manifest, loaded


def _ordered_pages(kind, loaded):
    if kind == 'wallabag-api':
        ordered = sorted(loaded, key=lambda p: p['page'])
        expected = None
        for page in ordered:
            response = page['response']
            for key in ('page', 'pages', 'limit', 'total'):
                _integer(response.get(key), page['file'] + '.' + key, 0 if key == 'total' else 1)
            require(page['page'] == response['page'], 'page_identity',
                    f"{page['file']}: response page differs from declared page")
            counts = (response['pages'], response['limit'], response['total'])
            require(expected is None or counts == expected, 'incomplete_pagination',
                    'Wallabag pages disagree on pages, limit or total')
            expected = counts
            embedded = response.get('_embedded')
            require(isinstance(embedded, dict) and isinstance(embedded.get('items'), list),
                    page['file'], 'expected _embedded.items array')
        pages, limit, total = expected
        require(pages == max(1, (total + limit - 1) // limit), 'incomplete_pagination',
                'Wallabag pages/limit/total are inconsistent')
        require(len(ordered) == pages and all(p['page'] == i for i, p in enumerate(ordered, 1)),
                'incomplete_pagination', 'missing Wallabag page(s); require exactly pages 1..pages')
        for page in ordered:
            count = max(0, min(limit, total - (page['page'] - 1) * limit))
            require(len(page['response']['_embedded']['items']) == count,
                    'incomplete_pagination', f"{page['file']}: item count differs from declared pagination")
        return ordered
    by_cursor = {page['cursor']: page for page in loaded}
    ordered, visited, cursor = [], set(), None
    while True:
        require(cursor not in visited, 'cursor_cycle', 'saved nextCursor chain revisits a request cursor')
        require(cursor in by_cursor, 'incomplete_pagination',
                'missing initial page or unfinished nextCursor chain')
        visited.add(cursor)
        page = by_cursor[cursor]
        response = page['response']
        require(isinstance(response.get('bookmarks'), list), page['file'], 'expected bookmarks array')
        require('nextCursor' in response, 'incomplete_pagination', f"{page['file']}: nextCursor is required")
        _cursor(response['nextCursor'], page['file'] + '.nextCursor')
        ordered.append(page)
        cursor = response['nextCursor']
        if cursor is None:
            break
    require(len(ordered) == len(loaded), 'incomplete_pagination',
            'unreachable/orphan response pages after terminal nextCursor')
    return ordered


def _normalize(kind, original, location, user_id, content_assets=None):
    """Return an import-shaped record and explicit source-to-output field paths."""
    require(isinstance(original, dict), str(location), 'expected API entry object')
    identity = original.get('id')
    if kind == 'wallabag-api':
        _integer(identity, 'entry.id')
    else:
        _string(identity, 'bookmark.id')
        require(original.get('userId') == user_id, 'source_namespace',
                'bookmark userId differs from manifest source.user_id')
    location['item_id'] = identity
    mapped, omitted = {}, []
    target, body_source = {}, None
    if kind == 'wallabag-api':
        for key in ('id', 'url', 'title', 'content', 'created_at', 'updated_at', 'published_at',
                    'is_archived', 'is_starred', 'annotations'):
            if key in original:
                target[key] = original[key]
                mapped[key] = key
        consumed = set(mapped) | {'tags'}
        tag_name = 'label'
    else:
        content = original.get('content')
        require(isinstance(content, dict), 'bookmark.content', 'expected API content object')
        kind_value = content.get('type')
        require(kind_value in ('link', 'text', 'asset', 'unknown'), 'bookmark.content.type',
                'unknown API content type')
        if kind_value != 'link':
            return None, mapped, sorted(original), 'api_non_link_not_mapped', None
        require(isinstance(content.get('url'), str), 'bookmark.content.url', 'expected string')
        require('createdAt' in original, 'bookmark.createdAt', 'required API date missing')
        target = {'url': content['url'], 'content': content.get('htmlContent'),
                  'title': original.get('title'), 'created_at': original['createdAt']}
        mapped = {'content.url': 'url', 'createdAt': 'created_at'}
        if 'htmlContent' in content:
            mapped['content.htmlContent'] = 'content'
        identity = content.get('contentAssetId')
        require(identity is None or (isinstance(identity, str) and bool(identity)),
                'bookmark.content.contentAssetId', 'expected null or nonempty contentAssetId string')
        if target['content'] is not None:
            body_source = {'kind': 'inline', 'field': 'content.htmlContent'}
        elif content_assets is not None and identity is not None:
            target['content'], body_source = content_assets.resolve(identity)
            if target['content'] is not None:
                mapped.pop('content.htmlContent', None)
                mapped['content.contentAssetId'] = 'content'
        else:
            body_source = {'kind': 'missing', 'contentAssetId': identity}
        if 'title' in original:
            mapped['title'] = 'title'
        if target['title'] is None:
            target['title'] = content.get('title')
            if target['title'] is not None:
                mapped['content.title'] = 'title'
        if original.get('modifiedAt') is not None:
            target['updated_at'] = original['modifiedAt']
            mapped['modifiedAt'] = 'updated_at'
        for source_key, output_key in [('archived', 'is_archived'), ('favourited', 'is_starred')]:
            require(type(original.get(source_key)) is bool, 'bookmark.' + source_key, 'expected boolean')
            target[output_key] = original[source_key]
            mapped[source_key] = output_key
        consumed = {path for path in mapped if '.' not in path} | {'content', 'tags', 'id', 'userId'}
        # A null inline body is an explicitly handled absence when an asset
        # supplies content; it does not represent an additional metadata loss.
        omitted.extend('content.' + key for key in content
                       if 'content.' + key not in mapped and key not in ('type', 'htmlContent'))
        tag_name = 'name'
    title = target.get('title')
    require(title is None or isinstance(title, str), 'entry.title', 'expected string or null')
    fallback = title is None
    if fallback:
        target['title'] = target.get('url')
    tags = original.get('tags')
    require(isinstance(tags, list), 'entry.tags', 'expected API tag objects array')
    target['tags'] = []
    for i, tag in enumerate(tags):
        require(isinstance(tag, dict) and isinstance(tag.get(tag_name), str),
                f'entry.tags[{i}]', f'expected tag object with {tag_name} string')
        target['tags'].append(tag[tag_name])
        mapped[f'tags[{i}].{tag_name}'] = f'tags[{i}]'
        omitted.extend(f'tags[{i}].{key}' for key in tag if key != tag_name)
    omitted.extend(key for key in original if key not in consumed)
    wallabag([target])  # Validate all mapped HTML, dates, flags and annotations before publishing.
    return target, mapped, sorted(omitted), 'api_null_title_fallback' if fallback else None, body_source


def build_api(raw, load_page, image_assets=None, content_assets=None, *, require_page_hashes=False):
    """Build an offline bundle, optionally requiring pins on every descriptor."""
    source_manifest, loaded = _responses(raw, load_page, require_page_hashes)
    source = source_manifest['source']
    kind = source['format']
    if content_assets is not None:
        content_assets.check_scope(source)
    ordered = _ordered_pages(kind, loaded)
    entries, mapping, issues, seen = [], [], [], {}
    expanded = 0
    if content_assets is not None:
        issues.extend(content_assets.issues)
    for page in ordered:
        items = (page['response']['_embedded']['items'] if kind == 'wallabag-api'
                 else page['response']['bookmarks'])
        for i, original in enumerate(items):
            location = {'namespace': source['namespace'], 'page_index': page['page_index'],
                        'file': page['file'], 'item_index': i}
            target, mapped, omitted, code, body_source = _normalize(
                kind, original, location, source.get('user_id'), content_assets)
            identity = location['item_id']
            require(identity not in seen, 'duplicate_item',
                    f"source ID {identity!r} occurs at {seen.get(identity)} and {location}; no body selected")
            seen[identity] = dict(location)
            index = len(entries) if target is not None else None
            record = {'source': location, 'output_index': index, 'mapped_fields': mapped,
                      'provenance_only_fields': omitted}
            if body_source is not None:
                record['body_source'] = body_source
                if body_source['kind'] == 'content_asset' and body_source['status'] == 'available':
                    expanded += body_source['bytes']
                    content_assets.check_expansion(expanded)
                if body_source['kind'] == 'content_asset' and body_source['status'] != 'available':
                    issues.append(issue('missing_content_asset', index,
                                        'Saved HTML asset unavailable; no fetch attempted.',
                                        source=location, body_source=body_source))
            mapping.append(record)
            if code:
                issues.append(issue(code, index, 'API record skipped or title defaulted; original retained in provenance.',
                                    'loss' if target is None else 'info', source=location))
            if omitted:
                issues.append(issue('api_fields_not_mapped', index,
                                    'API fields retained only in provenance; not represented in Wallabag output.',
                                    fields=omitted, source=location))
            if target is not None:
                entries.append(target)
    files, report = build('rescue', json_text(entries).encode('utf-8'), image_assets)
    for record in mapping:
        index = record['output_index']
        if index is not None:
            report['records'][index]['source'] = record['source']
            if 'body_source' in record:
                report['records'][index]['body_source'] = record['body_source']
    for diagnostic in report['issues']:
        if diagnostic['entry'] is not None:
            diagnostic['source'] = report['records'][diagnostic['entry']]['source']
    report.update(mode='api-import', source=source, input_sha256=hashlib.sha256(raw).hexdigest(),
                  input_count=len(mapping), skipped_count=len(mapping) - len(entries),
                  pagination={'complete': True, 'page_count': len(loaded),
                              'pages': [{k: v for k, v in page.items() if k != 'response'}
                                        for page in loaded],
                              'scope': 'supplied response sequence; not a verified live account snapshot'})
    report['issues'] = issues + report['issues']
    if not entries:
        report['issues'].append(issue('no_convertible_links', None, 'No URL entries emitted; originals retained in provenance.'))
    provenance = {'schema_version': 1, 'source_format': kind,
                  'input_sha256': report['input_sha256'], 'source': source_manifest,
                  'pages': loaded, 'mapping': mapping, 'issues': report['issues']}
    if image_assets:
        provenance['image_assets'] = image_assets.provenance()
    if content_assets is not None:
        provenance['content_assets'] = content_assets.provenance()
        report['content_assets'] = content_assets.summary()
    files['provenance.json'] = json_text(provenance)
    files['wallabag.json'] = json_text(entries)
    report['files'] = payload_metadata(files)
    files['manifest.json'] = json_text(report)
    return files, report
