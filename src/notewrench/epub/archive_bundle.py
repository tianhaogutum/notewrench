"""Explicit `notewrench archive api-import` bundle adapter (manifest tool "bagkeep", version 0.1.0,
schema 1). No discovery or fetching."""
from collections import Counter
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from . import BundleError
from .images import ImageManifest, MAX_IMAGE_BYTES, MAX_IMAGE_TOTAL, validate_image
from .inputs import MAX_ENTRIES, MAX_INPUT_BYTES, _unique_object, load_input_bytes

MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_PAYLOADS = 20000


def require(condition, message):
    if not condition:
        raise BundleError('archive bundle: ' + message)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def relative(name):
    require(isinstance(name, str) and name and not name.startswith('/') and '\\' not in name
            and all(p not in {'', '.', '..'} for p in name.split('/'))
            and not any(ord(c) < 32 or ord(c) == 127 for c in name), 'unsafe payload path')


def open_root(path):
    require(os.name == 'posix', 'bundle loading requires Linux/POSIX')
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        # Walk the chosen root too: neither it nor a parent may be a symlink.
        for part in Path(path).absolute().parts[1:]:
            require(part != '..', 'bundle root must not contain ..')
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read_payload(root, name, limit):
    relative(name)
    descriptor = os.dup(root)
    try:
        parts = name.split('/')
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(child, 'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode), f'payload must be a regular file: {name}')
            require(info.st_size <= limit, f'payload exceeds byte limit: {name}')
            raw = stream.read(limit + 1)
            require(len(raw) <= limit, f'payload exceeds byte limit: {name}')
            return raw
    finally:
        os.close(descriptor)


def decode(raw):
    try:
        value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object,
                          parse_constant=lambda x: (_ for _ in ()).throw(BundleError(f'invalid JSON constant: {x}')))
        # Reject escaped lone surrogates before filesystem use or report encoding.
        json.dumps(value, ensure_ascii=False).encode('utf-8')
        return value
    except (ValueError, RecursionError) as exc:
        raise BundleError(f'archive bundle: invalid JSON: {exc}') from exc


def load_bundle(path):
    root = open_root(path)
    try:
        manifest_raw = read_payload(root, 'manifest.json', MAX_MANIFEST_BYTES)
        manifest = decode(manifest_raw)
        require(isinstance(manifest, dict) and type(manifest.get('schema_version')) is int
                and manifest['schema_version'] == 1 and manifest.get('tool') == 'bagkeep'
                and manifest.get('version') == '0.1.0' and manifest.get('mode') == 'api-import',
                'requires schema_version 1, bagkeep version 0.1.0, mode api-import')
        required = {'schema_version', 'tool', 'version', 'mode', 'input_sha256', 'input_count',
                    'output_count', 'skipped_count', 'issues', 'records', 'files', 'source', 'pagination'}
        require(required <= set(manifest) <= required | {'image_assets', 'content_assets'},
                'missing or unknown manifest fields')
        files = manifest['files']
        require(isinstance(files, dict) and len(files) <= MAX_PAYLOADS
                and {'wallabag.json', 'provenance.json', 'index.html'} <= set(files)
                and 'manifest.json' not in files, 'invalid/missing declared payloads')
        # Validate every path/descriptor before reading any payload.
        total = len(manifest_raw)
        for name, info in files.items():
            relative(name)
            require(isinstance(info, dict) and set(info) == {'bytes', 'sha256'}
                    and type(info['bytes']) is int and 0 <= info['bytes'] <= MAX_INPUT_BYTES
                    and sha(info['sha256']), f'invalid payload metadata: {name}')
            total += info['bytes']
        require(total <= MAX_ARCHIVE_BYTES, 'declared payloads exceed 64 MiB aggregate')
        payloads = {}
        for name, info in files.items():
            raw = read_payload(root, name, info['bytes'])
            require(len(raw) == info['bytes'] and digest(raw) == info['sha256'],
                    f'payload byte count/SHA-256 mismatch: {name}')
            payloads[name] = raw
    finally:
        os.close(root)
    # All conversion uses the verified in-memory snapshot; no second path reads.
    articles = load_input_bytes(payloads['wallabag.json'], Path(path)/'wallabag.json', 1, 'wallabag')
    records = manifest['records']
    require(isinstance(records, list) and len(records) == len(articles) <= MAX_ENTRIES,
            'records must match wallabag output order/count')
    require(all(type(manifest[k]) is int and manifest[k] >= 0
                for k in ('input_count', 'output_count', 'skipped_count'))
            and manifest['output_count'] == len(articles)
            and manifest['input_count'] == len(articles) + manifest['skipped_count']
            and sha(manifest['input_sha256']), 'inconsistent manifest counts/input hash')
    require(isinstance(manifest['source'], dict)
            and manifest['source'].get('format') in ('wallabag-api', 'karakeep-api')
            and isinstance(manifest['source'].get('namespace'), str)
            and isinstance(manifest['pagination'], dict), 'invalid API source/pagination')
    issues = manifest['issues']
    require(isinstance(issues, list) and all(isinstance(i, dict)
            and i.get('severity') in ('loss', 'info') and isinstance(i.get('code'), str) for i in issues),
            'invalid upstream issues')
    image_assets = manifest.get('image_assets', {})
    require(isinstance(image_assets, dict), 'invalid image_assets')
    mapping = image_assets.get('mapping', [])
    require(isinstance(mapping, list) and len(mapping) <= 10000, 'invalid image source mapping')
    assets, image_media, image_total = {}, {}, 0
    for index, item in enumerate(mapping):
        require(isinstance(item, dict) and type(item.get('asset_index')) is int and item['asset_index'] == index
                and isinstance(item.get('reference'), str) and item['reference']
                and item['reference'] not in assets, 'invalid/duplicate image source mapping')
        require(item.get('status') in ('archived', 'missing_file'), 'unknown image status')
        if item['status'] == 'archived':
            name = item.get('file')
            require(isinstance(name, str) and name in payloads and sha(item.get('sha256'))
                    and item['sha256'] == files[name]['sha256'] and type(item.get('bytes')) is int
                    and item['bytes'] == files[name]['bytes'] and len(payloads[name]) <= MAX_IMAGE_BYTES
                    and isinstance(item.get('media_type'), str),
                    'image mapping does not match declared payload')
            if item['sha256'] in image_media:
                require(image_media[item['sha256']] == item['media_type'], 'conflicting image media types')
            else:
                validate_image(payloads[name], item['media_type'])
                image_total += len(payloads[name])
                require(image_total <= MAX_IMAGE_TOTAL, 'images exceed 32 MiB total')
                image_media[item['sha256']] = item['media_type']
        else:
            require(item.get('file') is None and item.get('sha256') is None, 'invalid missing image mapping')
        assets[item['reference']] = item
    declarations, source_articles = [], []
    for index, (record, article) in enumerate(zip(records, articles)):
        require(isinstance(record, dict) and type(record.get('entry')) is int and record['entry'] == index
                and type(record.get('cached_body')) is bool and isinstance(record.get('source'), dict),
                'invalid article record/order/cached_body/source')
        name = record.get('file')
        require(isinstance(name, str) and name in payloads and name not in {'wallabag.json', 'provenance.json', 'index.html'}
                and isinstance(record.get('title'), str)
                and (record['title'].strip() or article.url or f'Untitled {article.source_id}') == article.title
                and record.get('url') == article.url,
                'article record does not match declared payload/Wallabag metadata')
        refs = record.get('image_references', [])
        require(isinstance(refs, list), 'invalid article image references')
        seen = set()
        for ref in refs:
            require(isinstance(ref, dict) and isinstance(ref.get('reference'), str), 'invalid image reference')
            src, status = ref['reference'], ref.get('status')
            require(status in ('archived', 'missing_file', 'undeclared'), 'unknown article image status')
            if status == 'undeclared':
                require(src not in assets and ref.get('file') is None and ref.get('sha256') is None,
                        'inconsistent undeclared image reference')
                continue
            item = assets.get(src)
            require(item is not None and all(ref.get(k) == item.get(k)
                    for k in ('status', 'file', 'sha256', 'asset_index')), 'inconsistent image reference/mapping')
            if status == 'archived' and src not in seen:
                declarations.append(dict(key=article.key, src=src, path=item['file'],
                                         sha256=item['sha256'], media_type=item['media_type']))
                seen.add(src)
        if not record['cached_body']:
            articles[index] = replace(article, body=None, missing_reason='bagkeep_missing_cached_body')
        source_articles.append({'key': article.key, 'bagkeep_entry': index, 'wallabag_index': index,
                                'article_file': name, 'article_sha256': files[name]['sha256'],
                                'source': record['source'], 'cached_body': record['cached_body'],
                                'body_source': record.get('body_source'), 'image_references': refs,
                                'output_index': None})
    images = ImageManifest.from_entries(declarations, {a.key for a in articles}, payloads.__getitem__)
    provenance = {'directory': str(path), 'schema_version': 1, 'version': '0.1.0',
                  'manifest_sha256': digest(manifest_raw), 'wallabag_sha256': digest(payloads['wallabag.json']),
                  'verified_payloads': files, 'source': manifest['source'], 'pagination': manifest['pagination'],
                  'upstream_issues': issues, 'upstream_loss_count': sum(i['severity'] == 'loss' for i in issues),
                  'upstream_issue_counts': dict(Counter(i['code'] for i in issues)),
                  'upstream_skipped_count': manifest['skipped_count'],
                  'image_source_mapping': mapping, 'content_assets': manifest.get('content_assets'),
                  'articles': source_articles}
    return articles, images, provenance
