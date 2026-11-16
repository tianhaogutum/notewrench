# SPDX-License-Identifier: AGPL-3.0-only
"""Filesystem safety: symlink-free paths, bounded UTF-8 reads, new private output trees."""
from __future__ import annotations

import errno
import os
import posixpath
import re
import shutil
import stat as stat_module
from typing import Any

from ._js import JSONError, parse_json, stringify, utf8
from .identity import RekeyError

MAX_FILE_BYTES = 32 * 1024 * 1024

# libuv's messages, so system errors read like the original Node.js tool's.
_UV_MESSAGES = {
    'EACCES': 'permission denied', 'EAGAIN': 'resource temporarily unavailable',
    'EBUSY': 'resource busy or locked', 'EDQUOT': 'disk quota exceeded', 'EEXIST': 'file already exists',
    'EFBIG': 'file too large', 'EINVAL': 'invalid argument', 'EIO': 'i/o error',
    'EISDIR': 'illegal operation on a directory', 'ELOOP': 'too many symbolic links encountered',
    'EMFILE': 'too many open files', 'ENAMETOOLONG': 'name too long', 'ENFILE': 'file table overflow',
    'ENOENT': 'no such file or directory', 'ENOSPC': 'no space left on device', 'ENOTDIR': 'not a directory',
    'ENOTEMPTY': 'directory not empty', 'EPERM': 'operation not permitted', 'EROFS': 'read-only file system',
    'ETXTBSY': 'text file is busy', 'EXDEV': 'cross-device link not permitted',
}


def system_error(error: OSError, syscall: str, path: str) -> RekeyError:
    """A Node.js-style system error: ``ENOENT: no such file or directory, lstat '/x'``."""
    code = errno.errorcode.get(error.errno or 0, 'ERROR')
    message = _UV_MESSAGES.get(code)
    if message is None:
        reason = os.strerror(error.errno) if error.errno else str(error)
        message = reason[:1].lower() + reason[1:]
    return RekeyError(code, f"{code}: {message}, {syscall} '{path}'")


def _call(syscall: str, path: str, function: Any, *args: Any) -> Any:
    try:
        return function(path, *args)
    except OSError as error:
        raise system_error(error, syscall, path) from None


# --- node:path (POSIX) semantics -------------------------------------------------

def _normalize_parts(path: str) -> list[str]:
    parts: list[str] = []
    for part in path.split('/'):
        if not part or part == '.':
            continue
        if part == '..':
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return parts


def resolve(*segments: str) -> str:
    """``path.resolve``: absolute, normalized, without a trailing slash."""
    if os.sep != '/':  # pragma: no cover - POSIX-only tool semantics
        return os.path.abspath(os.path.join(*segments)) if segments else os.getcwd()
    joined = ''
    for segment in reversed(segments):
        if not segment:
            continue
        joined = segment + '/' + joined
        if segment.startswith('/'):
            break
    else:
        joined = os.getcwd() + '/' + joined
    return '/' + '/'.join(_normalize_parts(joined))


def join(base: str, *segments: str) -> str:
    """``path.join`` for an absolute ``base``."""
    if os.sep != '/':  # pragma: no cover
        return os.path.normpath(os.path.join(base, *segments))
    joined = '/'.join([base, *(segment for segment in segments if segment)])
    normalized = '/' + '/'.join(_normalize_parts(joined))
    if joined.endswith('/') and normalized != '/':
        normalized += '/'
    return normalized


def relative(source: str, target: str) -> str:
    """``path.relative``: '' for the same path."""
    if os.sep != '/':  # pragma: no cover
        source, target = os.path.abspath(source), os.path.abspath(target)
        return '' if source == target else os.path.relpath(target, source)
    source_parts = _normalize_parts(resolve(source))
    target_parts = _normalize_parts(resolve(target))
    common = 0
    while common < min(len(source_parts), len(target_parts)) and source_parts[common] == target_parts[common]:
        common += 1
    return '/'.join(['..'] * (len(source_parts) - common) + target_parts[common:])


def dirname(path: str) -> str:
    return posixpath.dirname(path) if os.sep == '/' else os.path.dirname(path)


# --- checks ----------------------------------------------------------------------

def no_symlinks(absolute: str) -> None:
    """Reject symlinks in every existing component; do not follow them outside roots."""
    root = '/' if absolute.startswith('/') else os.path.splitdrive(absolute)[0] + os.sep
    current = root
    for part in absolute[len(root):].split(os.sep):
        if not part:
            continue
        current = os.path.join(current, part)
        info = _call('lstat', current, os.lstat)
        if stat_module.S_ISLNK(info.st_mode):
            raise RekeyError('SYMLINK', f'Symlinks are unsupported: {current}')


def inside(root: str, file: str) -> bool:
    rel = relative(root, file)
    return bool(rel) and not rel.startswith('..' + os.sep) and rel != '..' and not os.path.isabs(rel)


_UNSAFE_CHARS = re.compile('[\x00-\x1f\x7f]')


def safe_relative(root: str, value: str) -> dict[str, str]:
    if '..' in re.split(r'[\\/]', value) or '\\' in value or _UNSAFE_CHARS.search(value):
        raise RekeyError('UNSAFE_PATH', f'Unsupported path: {stringify(value)}')
    absolute = resolve(root, value)
    if not inside(root, absolute):
        raise RekeyError('PATH_ESCAPE', f'Input must be inside --root: {value}')
    return {'absolute': absolute, 'relative': relative(root, absolute)}


def read_utf8(file: str) -> str:
    no_symlinks(file)
    info = _call('stat', file, os.stat)
    if not stat_module.S_ISREG(info.st_mode):
        raise RekeyError('NOT_FILE', f'Expected a regular file: {file}')
    if info.st_size > MAX_FILE_BYTES:
        raise RekeyError('FILE_TOO_LARGE', f'File exceeds 32 MiB: {file}')
    try:
        with open(file, 'rb') as handle:
            data = handle.read(MAX_FILE_BYTES + 1)
    except OSError as error:
        raise system_error(error, 'open', file) from None
    if len(data) > MAX_FILE_BYTES:
        raise RekeyError('FILE_TOO_LARGE', f'File exceeds 32 MiB: {file}')
    try:
        return data.decode('utf-8')  # strict; a leading BOM is kept (ignoreBOM)
    except UnicodeDecodeError:
        raise RekeyError('INVALID_UTF8', f'Expected valid UTF-8: {file}') from None


def read_csl(file: str) -> object:
    text = read_utf8(file)
    try:
        return parse_json(text[1:] if text.startswith('﻿') else text)
    except JSONError as error:
        raise RekeyError('BAD_JSON', f'{file}: {error}') from None


def check_output(out_dir: str) -> None:
    no_symlinks(dirname(out_dir))
    try:
        os.lstat(out_dir)
    except FileNotFoundError:
        return
    except OSError as error:
        raise system_error(error, 'lstat', out_dir) from None
    raise RekeyError('OUTPUT_EXISTS', f'Output directory must not exist: {out_dir}')


def _mkdir_recursive(directory: str) -> None:
    """``fs.mkdir(directory, { recursive: true, mode: 0o700 })``: every new level is private."""
    missing = []
    current = directory
    while not os.path.isdir(current):
        if os.path.lexists(current):
            raise system_error(OSError(errno.EEXIST, 'exists'), 'mkdir', current)
        missing.append(current)
        parent = dirname(current)
        if parent == current:
            break
        current = parent
    for path in reversed(missing):
        try:
            os.mkdir(path, 0o700)
        except FileExistsError:
            if not os.path.isdir(path):
                raise system_error(OSError(errno.EEXIST, 'exists'), 'mkdir', path) from None
        except OSError as error:
            raise system_error(error, 'mkdir', path) from None


def write_outputs(out_dir: str, documents: list[dict[str, str]]) -> None:
    check_output(out_dir)
    # Exclusive mkdir reserves a new private tree; files also use exclusive creation.
    _call('mkdir', out_dir, os.mkdir, 0o700)
    try:
        for document in documents:
            destination = join(out_dir, document['path'])
            if not inside(out_dir, destination):
                raise RekeyError('PATH_ESCAPE', 'Unsafe output path')
            _mkdir_recursive(dirname(destination))
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_BINARY', 0)
            descriptor = _call('open', destination, os.open, flags, 0o600)
            try:
                view = memoryview(utf8(document['text']))
                while view:
                    view = view[os.write(descriptor, view):]
            except OSError as error:
                raise system_error(error, 'write', destination) from None
            finally:
                os.close(descriptor)
    except BaseException:
        shutil.rmtree(out_dir, ignore_errors=True)
        raise


noSymlinks = no_symlinks
safeRelative = safe_relative
readUTF8 = read_utf8
readCSL = read_csl
checkOutput = check_output
writeOutputs = write_outputs
