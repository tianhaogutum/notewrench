"""Descriptor-relative, non-following reads. No writable file descriptors are opened."""
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import errno
import os
import stat
from pathlib import Path


class FileIssue(Exception):
    def __init__(self, code: str, detail: str):
        self.code, self.detail = code, detail
        super().__init__(detail)


def absolute(path: str | Path) -> Path:
    return Path(os.path.abspath(path))


def signature(st: os.stat_result) -> tuple[int, ...]:
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def evidence(st: os.stat_result) -> dict:
    return dict(zip(('device', 'inode', 'size', 'mtimeNs', 'ctimeNs'), signature(st)))


def classify(exc: OSError) -> FileIssue:
    if exc.errno == errno.ENOENT:
        return FileIssue('missing', str(exc))
    if exc.errno in (errno.EACCES, errno.EPERM):
        return FileIssue('inaccessible', str(exc))
    if exc.errno == errno.ELOOP:
        return FileIssue('symlink', str(exc))
    return FileIssue('io_error', str(exc))


def open_read(path: Path, *, directory: bool = False) -> int:
    """Reject symlinks in *every* component, including supplied root ancestors.

    O_NOFOLLOW closes the stat/open race; directory fds pin ancestors during open.
    O_NONBLOCK prevents a swapped FIFO from blocking before the regular-file check.
    """
    path = absolute(path)
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, part in enumerate(path.parts[1:]):
            final = index == len(path.parts) - 2
            try:
                st = os.stat(part, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISLNK(st.st_mode):
                    raise FileIssue('symlink', f'Symlink component refused: {path}')
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if not final or directory:
                    flags |= os.O_DIRECTORY
                child = os.open(part, flags, dir_fd=fd)
            except OSError as exc:
                raise classify(exc) from exc
            os.close(fd)
            fd = child
        st = os.fstat(fd)
        valid = stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode)
        if not valid:
            raise FileIssue('not_regular' if not directory else 'not_directory',
                            f'Unexpected file type: {path}')
        result, fd = fd, -1
        return result
    except OSError as exc:
        raise classify(exc) from exc
    finally:
        if fd >= 0:
            os.close(fd)


def inspect(path: Path) -> os.stat_result:
    fd = open_read(path)
    try:
        return os.fstat(fd)
    except OSError as exc:
        raise classify(exc) from exc
    finally:
        os.close(fd)


def children(path: Path) -> list[tuple[str, os.stat_result]]:
    fd = open_read(path, directory=True)
    try:
        result = []
        for name in sorted(os.listdir(fd)):
            try:
                result.append((name, os.stat(name, dir_fd=fd, follow_symlinks=False)))
            except OSError as exc:
                # Do not silently lose a child that disappeared during enumeration.
                raise classify(exc) from exc
        return result
    except OSError as exc:
        raise classify(exc) from exc
    finally:
        os.close(fd)
