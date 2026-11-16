"""Original, read-only inventory and size-prefiltered SHA-256 audit."""
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
import os
from pathlib import Path, PureWindowsPath
import stat

from .fs import (FileIssue, absolute, children, classify, evidence, inspect,
                 open_read, signature)
from .snapshot import InputError, KEY, load_snapshot

CHUNK_SIZE = 1024 * 1024


def strict_absolute_linked_path(raw: str) -> Path:
    """Validate before Path can erase empty/dot components or trailing slashes."""
    if (not raw.startswith('/') or '\\' in raw or '\x00' in raw
            or (raw != '/' and any(part in ('', '.', '..') for part in raw[1:].split('/')))):
        raise FileIssue('invalid_path',
                        'Absolute linked paths require normalized POSIX components without traversal, empty components or Windows syntax')
    return Path(raw)


def validate_linked_mappings(mappings) -> list[tuple[Path, Path]]:
    """Validate all configuration without touching source OR destination trees.

    Reject overlapping domains and ranges rather than choosing by order/longest
    prefix or silently collapsing two source trees into one destination inventory.
    Cross domain/range overlap is safe: application is exactly one pass.
    """
    result = []
    try:
        if not isinstance(mappings, (tuple, list)):
            raise ValueError('Supply a sequence of (source, destination) pairs')
        for pair in mappings:
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                raise ValueError('Every mapping requires exactly one source and one destination')
            roots = []
            for root in pair:
                if not isinstance(root, (str, Path)):
                    raise ValueError('Mapping roots must be strings or pathlib Paths')
                roots.append(strict_absolute_linked_path(str(root)))
            source, destination = roots
            for old_source, old_destination in result:
                if (source.is_relative_to(old_source) or old_source.is_relative_to(source)
                        or destination.is_relative_to(old_destination) or old_destination.is_relative_to(destination)):
                    raise ValueError('Mapping source roots and destination roots must each be disjoint; duplicates and nested roots are ambiguous')
            result.append((source, destination))
    except (ValueError, FileIssue) as exc:
        raise InputError('invalid_linked_mapping', str(exc)) from exc
    return result


def absolute_linked_target(raw: str, mappings: list[tuple[Path, Path]]) -> Path:
    target = strict_absolute_linked_path(raw)
    for source, destination in mappings:
        if target == source:
            raise FileIssue('invalid_path', 'Linked path must be a file beneath the mapping source root')
        if target.is_relative_to(source):
            return destination / target.relative_to(source)
    return target


def relative_linked_target(raw: str, base_directory: str | None) -> Path:
    """Map Zotero's attachments: prefix without guessing settings or granting access.

    Validate components before Path can normalize them. Authorization and all
    descriptor-relative, no-symlink reads remain shared with absolute linked files.
    """
    relative = raw[len('attachments:'):]
    if ('\\' in relative or PureWindowsPath(relative).drive
            or any(part in ('', '.', '..') for part in relative.split('/'))):
        raise FileIssue('invalid_path',
                        'attachments: requires a nonempty forward-slash relative path without traversal')
    if base_directory is None:
        raise FileIssue('unsupported_path',
                        'attachments: needs an explicit --linked-base-directory mapping and --allow-linked-root authorization')
    if (not base_directory.startswith('/') or base_directory.startswith('//')
            or '\\' in base_directory or '\x00' in base_directory
            or any(part in ('.', '..') for part in base_directory.split('/'))):
        raise FileIssue('invalid_path',
                        'Linked base directory must be an absolute POSIX path without traversal or Windows syntax')
    base = absolute(base_directory)
    return base / relative


def hash_file(path: Path, expected: tuple, counters: dict) -> str:
    fd = open_read(path)
    counters['filesHashAttempted'] += 1
    try:
        if signature(os.fstat(fd)) != expected:
            raise FileIssue('unstable', 'File changed between inventory and hash')
        digest = hashlib.sha256()
        read_bytes = 0
        while True:
            # Read at most the inventoried size plus one byte: a growing file
            # must fail promptly rather than keep the audit reading forever.
            chunk = os.read(fd, min(CHUNK_SIZE, expected[2] + 1 - read_bytes))
            if not chunk:
                break
            counters['bytesHashed'] += len(chunk)
            read_bytes += len(chunk)
            digest.update(chunk)
            if read_bytes > expected[2]:
                raise FileIssue('unstable', 'File grew during hash')
        if signature(os.fstat(fd)) != expected or signature(inspect(path)) != expected:
            raise FileIssue('unstable', 'File changed during hash or its path was replaced')
        counters['filesHashed'] += 1
        return digest.hexdigest()
    except OSError as exc:
        raise classify(exc) from exc
    finally:
        os.close(fd)


def audit(snapshot: str | Path, storage: str | Path, *,
          allow_linked_roots: tuple[str | Path, ...] = (),
          linked_base_directory: str | Path | None = None, hash_all: bool = False,
          linked_root_mappings: tuple[tuple[str | Path, str | Path], ...] = (),
          comparison_scope: str | None = None) -> dict:
    """Return evidence; never creates, deletes, renames or writes an input file.

    All regular files beneath valid storage key directories (including companions
    and orphan candidates) and explicitly allowed linked targets enter deduplication.
    linked_base_directory maps attachments: paths; it does not authorize reads.
    linked_root_mappings relocates absolute linked descendants once; only the
    destination is authorized/inspected. No fallback reads from source paths.
    """
    mappings = validate_linked_mappings(linked_root_mappings)
    if comparison_scope is not None:
        if not isinstance(comparison_scope, str) or not comparison_scope.strip() or '\x00' in comparison_scope:
            raise InputError('invalid_comparison_scope', 'Supply a nonempty source-profile namespace, unique across unrelated databases')
        # A size-prefilter report cannot establish unchanged bytes across scans.
        hash_all = True
    items, attachments, snapshot_info = load_snapshot(Path(snapshot))
    storage = absolute(storage)
    allowed = [absolute(p) for p in allow_linked_roots]
    linked_base = str(linked_base_directory) if linked_base_directory is not None else None
    errors, refs, orphan_dirs, retained_dirs = [], [], [], []
    inventory: dict[Path, dict] = {}
    directory_stats: dict[Path, tuple] = {}
    stats = {'items': len(items), 'attachments': len(attachments),
             'filesHashAttempted': 0, 'filesHashed': 0, 'bytesHashed': 0}
    by_key = defaultdict(list)
    for item in items:
        by_key[item['key']].append(item)

    def error(code: str, path: Path | str, detail: str, **extra) -> None:
        errors.append({'code': code, 'path': str(path), 'detail': detail, **extra})

    def add_file(path: Path, st: os.stat_result, source: str) -> None:
        if path not in inventory:
            inventory[path] = {'path': str(path), 'source': source, 'stat': evidence(st),
                               'references': [], '_signature': signature(st), 'status': 'present'}
        elif inventory[path]['_signature'] != signature(st):
            inventory[path]['status'] = 'unstable'
            error('unstable', path, 'File changed between inventory and reference inspection')

    def listing(path: Path):
        try:
            fd = open_read(path, directory=True)
            try:
                directory_stats[path] = signature(os.fstat(fd))
            finally:
                os.close(fd)
            return children(path)
        except FileIssue as exc:
            error(exc.code, path, exc.detail, stage='inventory')
            return []

    for name, st in listing(storage):
        path = storage / name
        if stat.S_ISLNK(st.st_mode):
            error('symlink', path, 'Storage entry symlink refused', stage='inventory')
            continue
        if not stat.S_ISDIR(st.st_mode) or not KEY.fullmatch(name):
            error('unrecognized_storage_entry', path,
                  'Expected a directory with an uppercase eight-character item key', stage='inventory')
            continue
        directory = {'key': name, 'path': str(path), 'stat': evidence(st),
                     'itemIdentities': by_key.get(name, [])}
        (retained_dirs if name in by_key else orphan_dirs).append(directory)
        pending = [path]
        while pending:
            current = pending.pop()
            for child, child_st in listing(current):
                target = current / child
                if stat.S_ISLNK(child_st.st_mode):
                    error('symlink', target, 'Symlink refused', stage='inventory')
                elif stat.S_ISDIR(child_st.st_mode):
                    pending.append(target)
                elif stat.S_ISREG(child_st.st_mode):
                    add_file(target, child_st, 'storage')
                else:
                    error('not_regular', target, 'Non-regular storage entry refused', stage='inventory')

    for attachment in attachments:
        ref = dict(attachment)
        refs.append(ref)
        raw = attachment['path']
        mode = attachment['linkMode']
        target = None
        try:
            if type(mode) is not int or mode not in (0, 1, 2, 3, 4):
                raise FileIssue('unsupported_link_mode', f'Unsupported linkMode: {mode!r}')
            if mode == 3:
                ref['status'] = 'remote_url'
                continue
            if not isinstance(raw, str) or not raw or '\x00' in raw:
                raise FileIssue('invalid_path', 'Local attachment needs a nonempty path without NUL')
            if mode in (0, 1, 4):
                if not raw.startswith('storage:'):
                    raise FileIssue('unsupported_path', 'Stored attachments require storage:filename')
                leaf = raw[len('storage:'):]
                if not leaf or leaf in ('.', '..') or '/' in leaf or '\\' in leaf:
                    raise FileIssue('invalid_path', 'storage: must contain one filename, without traversal')
                target = storage / attachment['key'] / leaf
            else:
                if raw.startswith('attachments:'):
                    target = relative_linked_target(raw, linked_base)
                elif not raw.startswith('/'):
                    raise FileIssue('unsupported_path',
                                    'Linked paths require POSIX absolute paths or attachments:relative/path; Windows paths refused')
                else:
                    target = absolute_linked_target(raw, mappings)
                if not any(target.is_relative_to(root) and target != root for root in allowed):
                    raise FileIssue('blocked_linked', 'Linked target not read: explicitly allow its directory with --allow-linked-root')
            ref['resolvedPath'] = str(target)
            st = inspect(target)
            add_file(target, st, 'linked' if mode == 2 else 'storage')
            inventory[target]['references'].append(attachment['itemID'])
            ref['status'] = inventory[target]['status']
        except FileIssue as exc:
            ref['status'], ref['detail'] = exc.code, exc.detail
            if target is not None:
                ref['resolvedPath'] = str(target)
            if exc.code != 'missing':
                error(exc.code, target or str(raw), exc.detail, itemID=attachment['itemID'], stage='reference')

    stats['filesInventoried'] = len(inventory)
    stats['bytesInventoried'] = sum(f['stat']['size'] for f in inventory.values())
    sizes = Counter(f['stat']['size'] for f in inventory.values() if f['status'] == 'present')
    for path, record in sorted(inventory.items()):
        if record['status'] != 'present':
            continue
        if not hash_all and sizes[record['stat']['size']] < 2:
            record['hashStatus'] = 'skipped_unique_size'
            continue
        try:
            record['sha256'] = hash_file(path, record['_signature'], stats)
            record['hashStatus'] = 'hashed'
        except FileIssue as exc:
            record['status'] = 'unstable' if exc.code == 'missing' else exc.code
            record['hashStatus'] = 'failed'
            error(record['status'], path, exc.detail, stage='hash')

    # Check even unique-size files, and recheck hashes before using their evidence.
    for path, record in inventory.items():
        if record['status'] != 'present':
            continue
        try:
            if signature(inspect(path)) != record['_signature']:
                raise FileIssue('unstable', 'File changed after inventory/hash')
        except FileIssue as exc:
            record['status'] = 'unstable' if exc.code == 'missing' else exc.code
            error(record['status'], path, exc.detail, stage='final_check')
    for path, expected in directory_stats.items():
        try:
            fd = open_read(path, directory=True)
            try:
                if signature(os.fstat(fd)) != expected:
                    raise FileIssue('unstable_directory', 'Directory entries changed during audit')
            finally:
                os.close(fd)
        except FileIssue as exc:
            error('unstable_directory' if exc.code == 'missing' else exc.code,
                  path, exc.detail, stage='final_check')
    for ref in refs:
        if ref['status'] == 'present':
            ref['status'] = inventory[Path(ref['resolvedPath'])]['status']

    groups = defaultdict(list)
    for record in inventory.values():
        if record['status'] == 'present' and 'sha256' in record:
            groups[(record['stat']['size'], record['sha256'])].append(record)
        record.pop('_signature')
    duplicates = []
    for (size, digest), records in sorted(groups.items()):
        if len(records) < 2:
            continue
        inodes = {(r['stat']['device'], r['stat']['inode']) for r in records}
        duplicates.append({'sha256': digest, 'size': size, 'files': records,
                           'extraPathBytes': size * (len(records) - 1),
                           'extraDistinctInodeBytes': size * (len(inodes) - 1)})
    # Aggregate in one pass: avoid O(directories * files) work on large libraries.
    directory_totals = defaultdict(lambda: [0, 0])
    for path, record in inventory.items():
        if path.is_relative_to(storage):
            relative = path.relative_to(storage)
            if len(relative.parts) >= 2:
                total = directory_totals[relative.parts[0]]
                total[0] += 1
                total[1] += record['stat']['size']
    for directory in orphan_dirs + retained_dirs:
        directory['inventoriedFiles'], directory['inventoriedBytes'] = directory_totals[directory['key']]
    stats['filesSkippedUniqueSize'] = sum(r.get('hashStatus') == 'skipped_unique_size' for r in inventory.values())
    stats['referenceStatuses'] = dict(sorted(Counter(r['status'] for r in refs).items()))
    stats['duplicateGroups'] = len(duplicates)
    stats['duplicateExtraPathBytes'] = sum(g['extraPathBytes'] for g in duplicates)
    stats['duplicateExtraDistinctInodeBytes'] = sum(g['extraDistinctInodeBytes'] for g in duplicates)
    stats['orphanCandidateBytes'] = sum(d['inventoriedBytes'] for d in orphan_dirs)
    stats['auditErrors'] = len(errors)
    report = {'reportVersion': 1, 'tool': 'zotero-attach-audit', 'snapshot': snapshot_info,
            'storage': str(storage), 'policy': {'followSymlinks': False,
            'allowedLinkedRoots': [str(p) for p in allowed],
            'linkedBaseDirectory': linked_base,
            'linkedRootMappings': [{'source': str(source), 'destination': str(destination)}
                                   for source, destination in mappings],
            'hashStrategy': 'all' if hash_all else 'size-prefilter',
            'duplicateScope': 'all inventoried regular storage files and allowed linked targets'},
            'auditComplete': not errors, 'statistics': stats, 'attachments': refs,
            'duplicateGroups': duplicates, 'orphanCandidates': orphan_dirs,
            'retainedDirectories': retained_dirs, 'files': list(inventory.values()),
            'auditErrors': errors,
            'caveats': ['Orphans are candidates only, compared against every items.key across libraries and trash.',
                        'No deletion recommendation. Duplicate byte estimates are not reclaimable disk space.',
                        'Snapshot consistency with storage is supplied by the caller; no cross-filesystem transaction.',
                        'Metadata checks detect ordinary changes, not adversarial changes or changes after the final check.']}
    if comparison_scope is not None:
        report['comparisonEvidence'] = {
            'version': 1, 'sourceScope': comparison_scope,
            'libraryIDs': sorted({item['libraryID'] for item in items}), 'items': items,
            'scopeBasis': 'caller-declared source profile; libraryID/key are local to this namespace'}
    return report
