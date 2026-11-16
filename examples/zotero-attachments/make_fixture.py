#!/usr/bin/env python3
"""Create a tiny, original SQLite/storage fixture; never use production paths."""
# SPDX-License-Identifier: AGPL-3.0-only
import argparse
from pathlib import Path
import sqlite3

# Deliberately reduced schema with real Zotero column names and relationships.
# This is not a Zotero profile and is not intended to be opened by Zotero.
SCHEMA = '''
CREATE TABLE items (
 itemID INTEGER PRIMARY KEY, libraryID INTEGER NOT NULL, key TEXT NOT NULL,
 UNIQUE(libraryID, key));
CREATE TABLE itemAttachments (
 itemID INTEGER PRIMARY KEY REFERENCES items(itemID),
 parentItemID INTEGER REFERENCES items(itemID), linkMode INTEGER, path TEXT);
CREATE TABLE deletedItems (
 itemID INTEGER PRIMARY KEY REFERENCES items(itemID), dateDeleted TEXT NOT NULL);
'''


def create_fixture(destination: Path) -> tuple[Path, Path]:
    destination.mkdir(parents=True, exist_ok=True)
    snapshot = destination / 'zotero.sqlite'
    storage = destination / 'storage'
    storage.mkdir()  # Fail rather than overwrite an existing fixture.
    with sqlite3.connect(snapshot) as db:
        db.executescript(SCHEMA)
        db.executemany('INSERT INTO items VALUES (?, ?, ?)', [
            (1, 1, 'ABCDEFGH'), (2, 1, 'JKLMNPQR'), (3, 1, 'STUVWXYZ'),
            (4, 1, 'BCDEFGHJ'), (5, 1, 'CDEFGHJK'), (6, 1, 'DEFGHJKL'),
            (7, 1, 'EFGHJKLM'), (8, 1, 'FGHJKLMN')])
        db.executemany('INSERT INTO itemAttachments VALUES (?, ?, ?, ?)', [
            (1, None, 0, 'storage:a.pdf'), (2, None, 1, 'storage:b.pdf'),
            (3, None, 0, 'storage:missing.pdf'), (4, None, 0, 'storage:trash.pdf'),
            (5, None, 0, 'storage:different.pdf'), (7, None, 3, None),
            (8, 6, 4, 'storage:image.png')])
        db.execute("INSERT INTO deletedItems VALUES (4, '2026-01-01T00:00:00Z')")
    contents = {'ABCDEFGH/a.pdf': b'same bytes\n', 'JKLMNPQR/b.pdf': b'same bytes\n',
                'BCDEFGHJ/trash.pdf': b'trash still retains identity\n',
                'CDEFGHJK/different.pdf': b'other data\n',
                'DEFGHJKL/nonattachment.txt': b'key belongs to a parent item\n',
                'FGHJKLMN/image.png': b'synthetic image payload\n',
                'ZZZZZZZZ/orphan.txt': b'orphan candidate only\n'}
    for relative, payload in contents.items():
        path = storage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return snapshot, storage


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path, help='New empty destination, not a production directory')
    args = parser.parse_args()
    snapshot, storage = create_fixture(args.destination)
    print(f'Created synthetic snapshot {snapshot} and storage {storage}')
