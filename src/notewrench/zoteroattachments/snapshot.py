"""Read the supplied standalone Zotero SQLite snapshot, never the live database."""
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from .fs import FileIssue, absolute, evidence, inspect, open_read, signature

KEY = re.compile(r'[A-Z0-9]{8}\Z')
REQUIRED = {'items': {'itemID', 'libraryID', 'key'},
            'itemAttachments': {'itemID', 'parentItemID', 'linkMode', 'path'},
            'deletedItems': {'itemID'}}


class InputError(Exception):
    def __init__(self, code: str, detail: str):
        self.code, self.detail = code, detail
        super().__init__(detail)


def no_sidecars(path: Path) -> None:
    for suffix in ('-wal', '-shm', '-journal'):
        # lexists deliberately includes dangling symlinks and empty sidecars.
        if os.path.lexists(str(path) + suffix):
            raise InputError('snapshot_sidecar',
                             f'Refusing {path}{suffix}; supply a standalone, consistent snapshot '
                             'after closing Zotero or using SQLite backup. Never copy only a live DB.')


def load_snapshot(path: Path) -> tuple[list[dict], list[dict], dict]:
    path = absolute(path)
    fd = -1
    try:
        if not os.path.isdir('/proc/self/fd'):
            raise InputError('unsupported_platform',
                             'Snapshot audits need Linux: the snapshot is opened through /proc/self/fd. '
                             'Comparing saved reports works on any platform.')
        no_sidecars(path)
        fd = open_read(path)
        before = os.fstat(fd)
        # The pinned fd prevents path substitution. immutable avoids creating SHM/journals;
        # it is valid ONLY for the explicitly supplied frozen, sidecar-free snapshot.
        uri = f'file:/proc/self/fd/{fd}?mode=ro&immutable=1'
        db = sqlite3.connect(uri, uri=True, timeout=0)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            db.execute('PRAGMA trusted_schema=OFF')
            db.execute('BEGIN')
            if [r[0] for r in db.execute('PRAGMA quick_check')] != ['ok']:
                raise InputError('inconsistent_snapshot', 'SQLite quick_check failed; recreate snapshot')
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, needed in REQUIRED.items():
                if table not in tables:
                    raise InputError('unsupported_schema', f'Missing real table: {table}')
                columns = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
                if not needed <= columns:
                    raise InputError('unsupported_schema', f'{table} missing columns: {sorted(needed - columns)}')
            items = [dict(r) for r in db.execute('SELECT itemID, libraryID, key FROM items ORDER BY itemID')]
            attachments = [dict(r) for r in db.execute(
                'SELECT itemID, parentItemID, linkMode, path FROM itemAttachments ORDER BY itemID')]
            deleted = [r[0] for r in db.execute('SELECT itemID FROM deletedItems')]
            by_id, identities = {}, set()
            for item in items:
                if (type(item['itemID']) is not int or type(item['libraryID']) is not int
                        or not isinstance(item['key'], str) or not KEY.fullmatch(item['key'])):
                    raise InputError('inconsistent_snapshot', f'Invalid item identity: {item!r}')
                identity = (item['libraryID'], item['key'])
                if item['itemID'] in by_id or identity in identities:
                    raise InputError('inconsistent_snapshot', 'Duplicate item identity')
                by_id[item['itemID']] = item
                identities.add(identity)
            if len(deleted) != len(set(deleted)) or any(i not in by_id for i in deleted):
                raise InputError('inconsistent_snapshot', 'deletedItems has duplicate or dangling identities')
            deleted = set(deleted)
            seen = set()
            for attachment in attachments:
                if (attachment['path'] is not None and not isinstance(attachment['path'], str)):
                    raise InputError('inconsistent_snapshot', 'Attachment path is not TEXT or NULL')
                if attachment['linkMode'] is not None and type(attachment['linkMode']) is not int:
                    raise InputError('inconsistent_snapshot', 'Attachment linkMode is not INTEGER or NULL')
                ident = attachment['itemID']
                parent = attachment['parentItemID']
                if ident in seen or ident not in by_id or (parent is not None and parent not in by_id):
                    raise InputError('inconsistent_snapshot', 'Attachment has duplicate or dangling identity')
                if parent is not None and by_id[parent]['libraryID'] != by_id[ident]['libraryID']:
                    raise InputError('inconsistent_snapshot', 'Attachment parent belongs to another library')
                seen.add(ident)
                attachment.update(by_id[ident])
                attachment['inTrash'] = ident in deleted
                attachment['parentInTrash'] = parent in deleted
            for item in items:
                item['inTrash'] = item['itemID'] in deleted
            db.rollback()
        finally:
            db.close()
        no_sidecars(path)
        if signature(before) != signature(os.fstat(fd)) or signature(before) != signature(inspect(path)):
            raise InputError('inconsistent_snapshot', 'Snapshot changed while reading; recreate a frozen snapshot')
        return items, attachments, {'path': str(path), 'openMode': 'mode=ro&immutable=1',
                                     'quickCheck': 'ok', 'stat': evidence(before)}
    except FileIssue as exc:
        raise InputError('snapshot_' + exc.code, exc.detail) from exc
    except (sqlite3.Error, OSError) as exc:
        raise InputError('invalid_snapshot', f'{exc}; supply a consistent standalone Zotero SQLite snapshot') from exc
    finally:
        if fd >= 0:
            os.close(fd)
