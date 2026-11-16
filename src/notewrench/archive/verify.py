"""Bounded, read-only consistency checking of published flat bundles (Linux).

SPDX-License-Identifier: AGPL-3.0-only
"""
from contextlib import contextmanager
import hashlib
import os
import re
import stat
import sys

from .core import InputError, decode

MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_FILES = 10000
CHUNK_BYTES = 64 * 1024


class _Invalid(Exception):
    def __init__(self, code, errno=None):
        self.code = code
        self.errno = errno


def _safe_name(name):
    if not isinstance(name, str) or not name or name in {'.', '..', 'manifest.json'}:
        return False
    try:
        return (len(name.encode('utf-8')) <= 255 and
                not any(c in '/\\' or ord(c) < 32 or ord(c) == 127 for c in name))
    except UnicodeError:
        return False


@contextmanager
def _regular(root_fd, name):
    """Reject static special files before open; recheck the actual opened object."""
    before = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise _Invalid('not_regular')
    fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=root_fd)
    try:
        held = os.fstat(fd)
        if not stat.S_ISREG(held.st_mode):
            raise _Invalid('not_regular')
        if (before.st_dev, before.st_ino) != (held.st_dev, held.st_ino):
            raise _Invalid('file_changed')
        yield fd, held
    finally:
        os.close(fd)


def _stamp(info):
    return info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _inventory(root_fd):
    names = []
    # scandir(fd) owns its iterator descriptor; the held root remains open.
    with os.scandir(root_fd) as entries:
        for entry in entries:
            if len(names) >= MAX_FILES + 1:
                raise _Invalid('entry_count_limit')
            names.append(entry.name)
    return sorted(names)


def _declarations(raw):
    try:
        manifest = decode(raw)
    except InputError as exc:
        raise _Invalid('manifest_json') from exc
    if (not isinstance(manifest, dict) or type(manifest.get('schema_version')) is not int or
            manifest['schema_version'] != 1 or manifest.get('tool') != 'bagkeep' or
            manifest.get('mode') not in ('rescue', 'k2w', 'api-import')):
        raise _Invalid('manifest_schema')
    files = manifest.get('files')
    if not isinstance(files, dict) or not files:
        raise _Invalid('manifest_files')
    if len(files) > MAX_FILES:
        raise _Invalid('file_count_limit')
    issues = []
    total = 0
    for name, info in sorted(files.items()):
        code = None
        if not _safe_name(name):
            code = 'unsafe_name'
        elif (not isinstance(info, dict) or set(info) != {'bytes', 'sha256'} or
              type(info['bytes']) is not int or info['bytes'] < 0 or
              not isinstance(info['sha256'], str) or
              re.fullmatch('[0-9a-f]{64}', info['sha256']) is None):
            code = 'invalid_declaration'
        elif info['bytes'] > MAX_FILE_BYTES:
            code = 'file_size_limit'
        else:
            total += info['bytes']
        if code:
            issues.append({'file': name, 'code': code})
    if total > MAX_TOTAL_BYTES:
        issues.append({'code': 'aggregate_size_limit'})
    return files, issues


def _error(exc):
    if isinstance(exc, _Invalid):
        return exc.code, exc.errno
    if isinstance(exc, FileNotFoundError):
        return 'missing', exc.errno
    return 'io_error', exc.errno


def _inspect(root_fd, files, names):
    rows = []
    remaining = MAX_TOTAL_BYTES
    for name in sorted(set(files) | (set(names) - {'manifest.json'})):
        # Undecodable directory bytes cannot be emitted as UTF-8 JSON strings.
        try:
            name.encode('utf-8')
        except UnicodeError:
            rows.append({'file_bytes_hex': os.fsencode(name).hex(), 'status': 'invalid',
                         'issues': ['unsafe_name', 'unexpected']})
            continue
        row = {'file': name, 'issues': []}
        rows.append(row)
        if name not in files:
            row['issues'] = ['unexpected'] if _safe_name(name) else ['unsafe_name', 'unexpected']
            row['status'] = 'invalid'
            continue  # Never open undeclared entries, including special files.
        info = files[name]
        row.update(expected_bytes=info['bytes'], expected_sha256=info['sha256'])
        try:
            with _regular(root_fd, name) as (fd, before):
                if before.st_size > MAX_FILE_BYTES:
                    raise _Invalid('file_size_limit')
                if before.st_size > remaining:
                    raise _Invalid('aggregate_size_limit')
                digest = hashlib.sha256()
                count = 0
                limit = min(MAX_FILE_BYTES, remaining)
                while True:
                    block = os.read(fd, min(CHUNK_BYTES, limit - count + 1))
                    if not block:
                        break
                    count += len(block)
                    remaining -= len(block)
                    if count > limit:
                        raise _Invalid('file_size_limit' if limit == MAX_FILE_BYTES else 'aggregate_size_limit')
                    digest.update(block)
                row.update(actual_bytes=count, actual_sha256=digest.hexdigest())
                if _stamp(before) != _stamp(os.fstat(fd)):
                    row['issues'].append('file_changed')
                if count != info['bytes']:
                    row['issues'].append('size_mismatch')
                if row['actual_sha256'] != info['sha256']:
                    row['issues'].append('hash_mismatch')
        except (OSError, _Invalid) as exc:
            code, number = _error(exc)
            row['issues'].append(code)
            if number is not None:
                row['errno'] = number
        row['status'] = 'invalid' if row['issues'] else 'valid'
    return rows


def verify_bundle(directory):
    """Return JSON-ready diagnostics; never repair, rebuild, write or use network.

    Hashes only compare bytes against the supplied, unauthenticated manifest.
    Reads are anchored, not snapshotted against concurrent in-place mutation.
    """
    result = {'schema_version': 1, 'status': 'invalid', 'manifest_self_hashed': False,
              'hashes_prove_authenticity': False, 'issues': [], 'files': []}
    if sys.platform != 'linux':
        result['issues'].append({'code': 'unsupported_platform'})
        return result
    root_fd = None
    phase = 'bundle'
    try:
        root_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
        phase = 'manifest'
        with _regular(root_fd, 'manifest.json') as (fd, before):
            if before.st_size > MAX_MANIFEST_BYTES:
                raise _Invalid('manifest_size_limit')
            raw = bytearray()
            while True:
                block = os.read(fd, min(CHUNK_BYTES, MAX_MANIFEST_BYTES - len(raw) + 1))
                if not block:
                    break
                raw.extend(block)
                if len(raw) > MAX_MANIFEST_BYTES:
                    raise _Invalid('manifest_size_limit')
            if _stamp(before) != _stamp(os.fstat(fd)):
                raise _Invalid('file_changed')
        files, issues = _declarations(raw)
        result['issues'].extend(issues)
        if not issues:
            phase = 'inventory'
            names = _inventory(root_fd)
            result['files'] = _inspect(root_fd, files, names)
    except (OSError, _Invalid, ValueError) as exc:
        code, number = _error(exc) if not isinstance(exc, ValueError) else ('invalid_path', None)
        issue = {'code': code, 'phase': phase}
        if number is not None:
            issue['errno'] = number
        result['issues'].append(issue)
    finally:
        if root_fd is not None:
            os.close(root_fd)
    if not result['issues'] and all(row['status'] == 'valid' for row in result['files']):
        result['status'] = 'valid'
    return result
