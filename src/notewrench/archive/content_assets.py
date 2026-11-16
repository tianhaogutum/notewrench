"""Explicit, scoped, hash-checked saved Karakeep HTML; never fetch resources.

SPDX-License-Identifier: AGPL-3.0-only
"""

import hashlib
import os
from pathlib import Path
import re

from .assets import _read, _relative
from .core import InputError, decode, issue, require

MAX_CONTENT_BYTES = 64 * 1024 * 1024
MAX_CONTENT_ASSETS = 10000
MAX_EXPANDED_CONTENT_BYTES = 64 * 1024 * 1024


class ContentAssets:
    def __init__(self, source, raw, load):
        require(isinstance(source, dict), '$content_assets', 'expected content manifest object')
        require(not (set(source) - {'schema_version', 'source', 'assets'}),
                '$content_assets', 'unknown manifest fields')
        require(type(source.get('schema_version')) is int and source['schema_version'] == 1,
                '$content_assets.schema_version', 'expected 1')
        scope = source.get('source')
        require(isinstance(scope, dict) and set(scope) == {'format', 'namespace', 'user_id'}
                and scope['format'] == 'karakeep-api'
                and all(isinstance(scope[k], str) and scope[k] for k in ('namespace', 'user_id')),
                'source_namespace', 'expected Karakeep format, namespace and user_id')
        declarations = source.get('assets')
        require(isinstance(declarations, list) and len(declarations) <= MAX_CONTENT_ASSETS,
                '$content_assets.assets', 'expected array of at most 10000 content asset descriptors')
        identities = set()
        for index, item in enumerate(declarations):
            path = f'$content_assets.assets[{index}]'
            require(isinstance(item, dict) and set(item) == {'contentAssetId', 'file', 'sha256'},
                    path, 'expected contentAssetId, file and sha256 fields only')
            identity = item['contentAssetId']
            require(isinstance(identity, str) and bool(identity), path + '.contentAssetId',
                    'expected nonempty exact contentAssetId string')
            require(identity not in identities, 'duplicate_content_asset', f'repeated contentAssetId: {identity!r}')
            identities.add(identity)
            _relative(item['file'])
            require(isinstance(item['sha256'], str) and re.fullmatch('[0-9a-f]{64}', item['sha256']),
                    path + '.sha256', 'expected lowercase SHA-256 hex')
        self.source = source
        self.input_sha256 = hashlib.sha256(raw).hexdigest()
        self.mapping, self.by_id, self.issues = [], {}, []
        remaining = MAX_CONTENT_BYTES - len(raw)
        require(remaining >= 0, '$content_assets', 'manifest and HTML files exceed aggregate 64 MiB limit')
        cache, hashes, texts = {}, {}, {}
        expanded = 0
        for index, item in enumerate(declarations):
            name = item['file']
            if name not in cache:
                cache[name] = load(name, remaining)
                if cache[name] is not None:
                    remaining -= len(cache[name])
                    require(remaining >= 0, '$content_assets', 'manifest and HTML files exceed aggregate 64 MiB limit')
            data = cache[name]
            record = {'asset_index': index, 'contentAssetId': item['contentAssetId'], 'source_file': name,
                      'expected_sha256': item['sha256'], 'sha256': None, 'html': None, 'status': 'missing_file'}
            if data is None:
                self.issues.append(issue('missing_content_asset_file', None,
                                         'Declared saved HTML is missing; no fetch attempted.',
                                         asset_index=index, contentAssetId=item['contentAssetId'], source_file=name))
            else:
                expanded += len(data)
                self.check_expansion(expanded)
                if name not in hashes:
                    hashes[name] = hashlib.sha256(data).hexdigest()
                digest = hashes[name]
                require(digest == item['sha256'], 'content_asset_sha256', f'HTML hash mismatch: {name!r}')
                if name not in texts:
                    try:
                        texts[name] = data.decode('utf-8-sig')
                    except UnicodeError as exc:
                        raise InputError(f'content_asset_utf8: expected saved UTF-8 HTML: {name!r}') from exc
                record.update(sha256=digest, bytes=len(data), html=texts[name], status='available')
            self.mapping.append(record)
            self.by_id[item['contentAssetId']] = record

    def check_scope(self, source):
        require(source == self.source['source'], 'source_namespace',
                'content manifest must match API Karakeep format, namespace and user_id exactly')

    @staticmethod
    def check_expansion(size):
        require(size <= MAX_EXPANDED_CONTENT_BYTES, 'expanded_content_limit',
                'repeated saved HTML exceeds 64 MiB expanded body limit')

    def resolve(self, identity):
        record = self.by_id.get(identity)
        if record is None:
            return None, {'kind': 'content_asset', 'contentAssetId': identity, 'status': 'undeclared',
                          'sha256': None, 'asset_index': None}
        return record['html'], {'kind': 'content_asset', 'bytes': record.get('bytes'),
                               **{key: record[key] for key in
                                  ('contentAssetId', 'status', 'sha256', 'asset_index', 'source_file')}}

    def summary(self):
        return {'input_sha256': self.input_sha256,
                'mapping': [{k: v for k, v in record.items() if k != 'html'} for record in self.mapping]}

    def provenance(self):
        return {'schema_version': 1, 'input_sha256': self.input_sha256,
                'source': self.source, 'mapping': self.mapping}


def load_content_assets(path: Path):
    # Reuse the PNG loader's descriptor-confined, regular-file-only reader.
    root_fd = os.open(path.absolute().parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        raw = _read(root_fd, path.name, MAX_CONTENT_BYTES)
        require(raw is not None, 'local_asset_path', 'content manifest is missing')
        return ContentAssets(decode(raw), raw, lambda name, remaining: _read(root_fd, name, remaining))
    finally:
        os.close(root_fd)
