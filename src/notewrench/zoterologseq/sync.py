# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 zotlog-sync contributors
"""Conservative file-graph planning and atomic per-file application (Linux/POSIX)."""
from __future__ import annotations

import fcntl
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Any

from .graph import claims, graph_inventory
from .model import (Invalid, Item, LOCK_NAME, MANIFEST_NAME, block, canonical,
                    collision_key, digest, filename, identity, load_items, markers,
                    new_page, strict_json)


class Conflict(Invalid):
    pass


def safe_ancestors(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink():
            raise Conflict(f'symbolic link is unsupported: {part}')
        if part.exists() and not part.is_dir():
            raise Conflict(f'expected a directory: {part}')


def read_regular(path: Path) -> bytes | None:
    return read_regular_snapshot(path)[0]


def read_regular_snapshot(path: Path) -> tuple[bytes | None, tuple[int, int] | None]:
    """Return bytes and device/inode from the same safely opened descriptor."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None, None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise Conflict(f'expected a regular, non-linked file: {path}')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise Conflict(f'expected a regular, non-linked file: {path}')
        if (opened.st_ino, opened.st_dev) != (info.st_ino, info.st_dev):
            raise Conflict(f'file changed during read: {path}')
        return stream.read(), (opened.st_dev, opened.st_ino)


def empty_manifest() -> dict[str, Any]:
    return {'version': 1, 'entries': {}}


def load_manifest(data: bytes | None) -> dict[str, Any]:
    if data is None:
        return empty_manifest()
    value = strict_json(data, 'manifest')
    if (not isinstance(value, dict) or set(value) != {'version', 'entries'}
            or type(value['version']) is not int or value['version'] != 1
            or not isinstance(value['entries'], dict)):
        raise Invalid('manifest must contain version=1 and an entries object only')
    seen = set()
    for key, entry in value['entries'].items():
        expected_path = 'pages/' + filename(key)
        if (not isinstance(entry, dict) or set(entry) != {'identity', 'path', 'managed_sha256'}
                or entry['identity'] != identity(key) or entry['path'] != expected_path
                or not isinstance(entry['managed_sha256'], str)
                or not re.fullmatch('[0-9a-f]{64}', entry['managed_sha256'])):
            raise Invalid(f'corrupt manifest entry for {key!r}')
        folded = collision_key(expected_path)
        if folded in seen:
            raise Invalid(f'case/Unicode path collision in manifest: {key!r}')
        seen.add(folded)
    return value


def region(data: bytes, key: str, expected_hash: str) -> tuple[int, int, bytes]:
    try:
        data.decode('utf-8')
    except UnicodeError as exc:
        raise Conflict('tracked page must be UTF-8') from exc
    begin, end = markers(identity(key))
    lines = data.splitlines(keepends=True)
    starts, ends = [], []
    offset = 0
    for line in lines:
        content = line.rstrip(b'\r\n')
        if content == begin:
            starts.append((offset, line))
        elif content == end:
            ends.append(offset + len(line))
        elif content.startswith(b'- <!-- zotlog-sync:'):
            raise Conflict('unexpected or damaged managed marker')
        offset += len(line)
    if len(starts) != 1 or len(ends) != 1 or starts[0][0] >= ends[0]:
        raise Conflict('expected exactly one matching begin/end managed marker pair')
    start, begin_line = starts[0]
    finish = ends[0]
    managed = data[start:finish]
    if digest(managed) != expected_hash:
        raise Conflict('managed block was edited or an earlier sync was interrupted; restore it before syncing')
    # Stable frontmatter is outside the replaceable region and never rewritten.
    header = (f'citekey:: {key}\nzotlog-source:: csl-json\n'
              f'zotlog-identity:: {identity(key)}\n').encode('utf-8')
    if not data.replace(b'\r\n', b'\n').startswith(header):
        raise Conflict('tracked page identity/source frontmatter was changed')
    return start, finish, b'\r\n' if begin_line.endswith(b'\r\n') else b'\n'


class Plan:
    def __init__(self, graph: Path, items: list[Item]):
        self.graph = graph
        self.items = items
        self.snapshots: dict[str, bytes | None] = {}
        self.writes: dict[str, bytes] = {}
        self.inventory: tuple[str, ...] = ()
        self.report: dict[str, Any] = {
            'status': 'planned', 'created': [], 'updated': [], 'unchanged': [],
            'stale': [], 'conflicts': [],
            'preserved_unmapped': [{'id': item.key, 'fields': item.unmapped}
                                   for item in items if item.unmapped],
        }
        self._build()

    def conflict(self, code: str, message: str, key: str | None = None,
                 path: str | None = None, sources: list[dict] | None = None) -> None:
        entry = {'code': code, 'id': key, 'path': path, 'message': message}
        if sources is not None:
            entry['sources'] = sources
        self.report['conflicts'].append(entry)

    def read(self, relative: str) -> bytes | None:
        if relative in self.snapshots:
            return self.snapshots[relative]
        data = read_regular(self.graph / relative)
        self.snapshots[relative] = data
        return data

    def _build(self) -> None:
        try:
            safe_ancestors(self.graph)
            safe_ancestors(self.graph / 'pages')
            manifest = load_manifest(self.read(MANIFEST_NAME))
        except Invalid as exc:
            self.conflict('manifest-or-path', str(exc))
            self.report['status'] = 'conflict'
            return
        entries = manifest['entries']
        active = {item.key for item in self.items}
        desired = {item.path: item.key for item in self.items}
        desired.update({entry['path']: key for key, entry in entries.items()})
        by_title = {}
        try:
            self.inventory = graph_inventory(self.graph)
        except Invalid as exc:
            self.conflict('graph-identity-coverage', str(exc))
        for relative in self.inventory:
            try:
                data = self.read(relative)
                if data is None:
                    raise Conflict('page disappeared during identity preflight')
                for claim in claims(relative, data):
                    by_title.setdefault(collision_key(claim.value), []).append(claim)
            except Invalid as exc:
                self.conflict('graph-identity-coverage',
                              f'{relative}: identity coverage incomplete: {exc}', path=relative)
        reserved: dict[str, str] = {}
        for relative, key in sorted(desired.items()):
            folded = collision_key(key)
            if folded in reserved and reserved[folded] != key:
                self.conflict('identity-collision', f'identity collides with {reserved[folded]!r}', key, relative)
            reserved[folded] = key
            for claim in by_title.get(folded, []):
                if claim.path == relative:
                    continue  # A page may alias its own citekey.
                location = claim.path + (f':{claim.line}' if claim.line else '')
                code = ('filename-collision' if claim.property == 'filename'
                        and '/' not in claim.value else 'graph-identity-collision')
                self.conflict(code,
                              f'citekey {key!r} ({relative}) collides with '
                              f'{location} {claim.property} identity {claim.value!r}', key, relative,
                              [{'path': relative, 'property': 'citekey', 'line': None, 'value': key},
                               claim.source()])
        regions = {}
        for key, entry in sorted(entries.items()):
            relative = entry['path']
            if key not in active:
                self.report['stale'].append({'id': key, 'path': relative})
            try:
                data = self.read(relative)
                if data is None:
                    raise Conflict('tracked page is missing; restore it (pages are never silently recreated)')
                regions[key] = region(data, key, entry['managed_sha256'])
            except Invalid as exc:
                self.conflict('tracked-page', str(exc), key, relative)
        for item in self.items:
            relative = item.path
            try:
                if item.key not in entries:
                    if self.read(relative) is not None:
                        raise Conflict('untracked existing file; adoption/overwrite is unsupported, even with markers')
                    content = new_page(item)
                    managed = block(item)
                    self.report['created'].append(relative)
                else:
                    if item.key not in regions:
                        continue
                    start, finish, newline = regions[item.key]
                    data = self.snapshots[relative]
                    managed = block(item, newline)
                    content = data[:start] + managed + data[finish:]
                    self.report['unchanged' if content == data else 'updated'].append(relative)
                if content != self.snapshots.get(relative):
                    self.writes[relative] = content
                entries[item.key] = {'identity': item.uid, 'path': relative,
                                     'managed_sha256': digest(managed)}
            except Invalid as exc:
                self.conflict('existing-file', str(exc), item.key, relative)
        manifest_data = canonical(manifest).encode('utf-8')
        if manifest_data != self.snapshots.get(MANIFEST_NAME):
            self.writes[MANIFEST_NAME] = manifest_data
        if self.report['conflicts']:
            self.report['status'] = 'conflict'
            self.writes.clear()

    def check_snapshot(self, relative: str) -> None:
        if read_regular(self.graph / relative) != self.snapshots[relative]:
            raise Conflict(f'file changed after preflight: {relative}')


def atomic_write(path: Path, data: bytes) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix='.zotlog-tmp-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply(plan: Plan) -> None:
    if plan.report['conflicts']:
        raise Conflict('cannot apply a plan with conflicts')
    if graph_inventory(plan.graph) != plan.inventory:
        raise Conflict('graph page inventory changed after preflight')
    for relative in plan.snapshots:
        plan.check_snapshot(relative)
    changed = []
    try:
        # Manifest is the commit record and is always written last.
        for relative in sorted(plan.writes, key=lambda p: (p == MANIFEST_NAME, p)):
            plan.check_snapshot(relative)
            # Track before writing, including failures after os.replace (e.g. fsync).
            changed.append(relative)
            atomic_write(plan.graph / relative, plan.writes[relative])
    except (OSError, Invalid) as exc:
        rollback_errors = []
        for relative in reversed(changed):
            path = plan.graph / relative
            try:
                current = read_regular(path)
                old = plan.snapshots[relative]
                if current == old:
                    continue
                if current != plan.writes[relative]:
                    raise Conflict('concurrent edit prevents safe rollback')
                if old is None:
                    path.unlink()
                else:
                    atomic_write(path, old)
            except (OSError, Invalid) as rollback_exc:
                rollback_errors.append(f'{relative}: {rollback_exc}')
        if rollback_errors:
            raise OSError(f'write failed ({exc}); rollback incomplete: ' + '; '.join(rollback_errors)) from exc
        raise


@contextmanager
def graph_lock(graph: Path):
    """Share one advisory lock between sync and explicit archival."""
    fd = os.open(graph / LOCK_NAME, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise Conflict('lock must be a regular, non-linked file')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Conflict('another notewrench zotero-logseq process holds the graph lock') from exc
        yield
    finally:
        os.close(fd)


def synchronize(source: Path, graph: Path, dry_run: bool = False) -> dict[str, Any]:
    source = Path(os.path.abspath(source))
    graph = Path(os.path.abspath(graph))
    source_data = read_regular(source)
    if source_data is None:
        raise FileNotFoundError(f'source file does not exist: {source}')
    items = load_items(source_data)
    first = Plan(graph, items)
    first.report['dry_run'] = dry_run
    first.report['source_sha256'] = digest(source_data)
    if dry_run or first.report['conflicts']:
        return first.report
    protected = {graph / relative for relative in first.snapshots}
    protected.add(graph / LOCK_NAME)
    if source in protected:
        raise Conflict('source path overlaps a managed page, manifest or lock')
    graph.mkdir(parents=True, exist_ok=True)
    safe_ancestors(graph)
    with graph_lock(graph):
        plan = Plan(graph, items)
        plan.report['dry_run'] = False
        plan.report['source_sha256'] = digest(source_data)
        if plan.report['conflicts']:
            return plan.report
        (graph / 'pages').mkdir(exist_ok=True)
        apply(plan)
        plan.report['status'] = 'applied'
        return plan.report
