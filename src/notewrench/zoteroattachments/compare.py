"""Conservative offline comparison of evidence-bearing JSON audit reports."""
# SPDX-License-Identifier: AGPL-3.0-only
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import re

from .audit import absolute_linked_target, relative_linked_target, validate_linked_mappings
from .fs import FileIssue, absolute, inspect, open_read, signature
from .snapshot import InputError, KEY

DIGEST = re.compile(r'[0-9a-f]{64}\Z')


def token(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':'))


def identity(item: dict) -> dict:
    return {'libraryID': item['libraryID'], 'key': item['key']}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSON field: {key}')
        result[key] = value
    return result


def read_report(path: Path, entry: dict) -> dict:
    fd = open_read(path)
    try:
        before = os.fstat(fd)
        chunks, count, digest = [], 0, hashlib.sha256()
        while True:
            chunk = os.read(fd, min(1024 * 1024, before.st_size + 1 - count))
            if not chunk:
                break
            count += len(chunk)
            if count > before.st_size:
                raise ValueError('Input report grew while reading')
            digest.update(chunk)
            chunks.append(chunk)
        entry['sha256'] = digest.hexdigest()
        entry['bytes'] = count
        if signature(before) != signature(os.fstat(fd)) or signature(before) != signature(inspect(path)):
            raise ValueError('Input report changed while reading')
        return json.loads(b''.join(chunks), object_pairs_hook=unique_object,
                          parse_constant=lambda value: invalid(f'Non-JSON constant: {value}'))
    finally:
        os.close(fd)


def invalid(detail):
    raise ValueError(detail)


def require(condition, detail):
    if not condition:
        invalid(detail)


def normalized_path(raw):
    require(isinstance(raw, str) and raw.startswith('/') and '\x00' not in raw,
            'Expected an absolute report path')
    path = Path(raw)
    require(str(path) == raw and '..' not in path.parts, 'Ambiguous report path')
    return path


def stable_hash(record):
    return (isinstance(record, dict) and record.get('status') == 'present'
            and record.get('hashStatus') == 'hashed'
            and isinstance(record.get('sha256'), str) and bool(DIGEST.fullmatch(record['sha256'])))


def linked_scan_target(raw: str, policy: dict) -> Path:
    """Derive a target lexically from this report, never comparison mappings."""
    detail = 'Linked attachment target mismatch with scan policy'
    require(isinstance(raw, str) and bool(raw) and '\x00' not in raw
            and isinstance(policy, dict), detail)
    # Older version-1 scans predate the additive linkedRootMappings field.
    declared = policy.get('linkedRootMappings', [])
    require(isinstance(declared, list) and all(
        isinstance(pair, dict) and isinstance(pair.get('source'), str)
        and isinstance(pair.get('destination'), str) for pair in declared), detail)
    try:
        mappings = validate_linked_mappings([(p['source'], p['destination']) for p in declared])
        if raw.startswith('attachments:'):
            base = policy.get('linkedBaseDirectory')
            require(isinstance(base, str), detail)
            return relative_linked_target(raw, base)
        return absolute_linked_target(raw, mappings)
    except (FileIssue, InputError) as exc:
        raise ValueError(detail) from exc


def index_report(report: dict, coverage: dict) -> dict | None:
    require(isinstance(report, dict) and report.get('tool') == 'zotero-attach-audit'
            and type(report.get('reportVersion')) is int and report['reportVersion'] == 1
            and not report.get('fatal'), 'Expected a nonfatal version-1 audit JSON report')
    for field in ('attachments', 'files', 'duplicateGroups', 'orphanCandidates', 'retainedDirectories', 'auditErrors'):
        require(isinstance(report.get(field), list), f'Missing or invalid {field}')
    require(type(report.get('auditComplete')) is bool, 'Invalid auditComplete')
    coverage.update(auditComplete=report['auditComplete'], auditErrors=report['auditErrors'],
                    filesInventoried=len(report['files']), filesWithSha256=sum(stable_hash(f) for f in report['files']),
                    attachments=len(report['attachments']))
    reasons = coverage['reasons']
    if not report['auditComplete'] or report['auditErrors']:
        reasons.append('audit_incomplete')
    evidence = report.get('comparisonEvidence')
    if evidence is None:
        reasons.append('missing_comparison_evidence: rescan with --comparison-scope')
        return None
    require(isinstance(evidence, dict) and type(evidence.get('version')) is int
            and evidence['version'] == 1, 'Unsupported comparison evidence')
    scope = evidence.get('sourceScope')
    libraries = evidence.get('libraryIDs')
    require(isinstance(scope, str) and bool(scope.strip()) and '\x00' not in scope, 'Invalid sourceScope')
    require(isinstance(libraries, list) and all(type(i) is int for i in libraries)
            and libraries == sorted(set(libraries)), 'Invalid libraryIDs')
    coverage.update(sourceScope=scope, libraryIDs=libraries, identityBasis=evidence.get('scopeBasis'))
    require(isinstance(evidence.get('items'), list), 'Missing all-item identity inventory')
    items, owners, seen = {}, defaultdict(list), set()
    for item in evidence['items']:
        require(isinstance(item, dict) and type(item.get('itemID')) is int
                and type(item.get('libraryID')) is int and isinstance(item.get('key'), str)
                and bool(KEY.fullmatch(item['key'])), 'Invalid item identity')
        ident = token(identity(item))
        require(item['itemID'] not in items and ident not in seen, 'Duplicate item identity')
        items[item['itemID']] = item
        seen.add(ident)
        owners[item['key']].append(identity(item))
    require(libraries == sorted({i['libraryID'] for i in items.values()}), 'Library coverage does not match items')
    stats = report.get('statistics')
    require(isinstance(stats, dict) and stats.get('items') == len(items)
            and stats.get('attachments') == len(report['attachments'])
            and stats.get('filesInventoried') == len(report['files']), 'Inventory counts do not match evidence')
    storage = normalized_path(report.get('storage'))
    refs, refs_by_id = {}, {}
    for ref in report['attachments']:
        require(isinstance(ref, dict) and type(ref.get('itemID')) is int
                and ref['itemID'] in items and identity(ref) == identity(items[ref['itemID']]),
                'Invalid attachment identity')
        ident = token(identity(ref))
        require(ident not in refs and ref['itemID'] not in refs_by_id, 'Duplicate attachment identity')
        require(isinstance(ref.get('status'), str) and (ref.get('linkMode') is None or type(ref['linkMode']) is int)
                and (ref.get('path') is None or isinstance(ref['path'], str))
                and type(ref.get('inTrash')) is bool and type(ref.get('parentInTrash')) is bool,
                'Invalid attachment evidence')
        parent = ref.get('parentItemID')
        require(parent is None or (type(parent) is int and parent in items
                and items[parent]['libraryID'] == ref['libraryID']), 'Invalid attachment parent')
        # The scan derives both flags from the same all-item trash inventory.
        # Identity checks on booleans also reject absent/nonboolean inventory
        # evidence (0/1 must not compare equal to False/True). No source IO.
        require(ref['inTrash'] is items[ref['itemID']].get('inTrash'),
                'Attachment trash flag disagrees with all-item inventory')
        require(ref['parentInTrash'] is (items[parent].get('inTrash') if parent is not None else False),
                'Attachment parent trash flag disagrees with all-item inventory')
        if ref['status'] not in ('present', 'missing', 'remote_url'):
            reasons.append('attachment_not_fully_observed')
        if ref['status'] == 'remote_url':
            require(ref['linkMode'] == 3, 'Invalid remote attachment')
            # The scan returns remote observations before resolving any local
            # target, even when no inventoried file lists this attachment.
            require(ref.get('resolvedPath') is None,
                    'Remote attachment cannot have a resolved local target')
        if ref['status'] in ('present', 'missing'):
            # Unknown modes and remote URLs never establish a local observation
            # in an audit, even if imported file/reference evidence agrees.
            require(ref['linkMode'] in (0, 1, 2, 4),
                    'Local attachment observation requires linkMode 0/1/2/4')
            if ref['linkMode'] == 2:
                raw = ref['path']
                require(isinstance(raw, str) and bool(raw) and '\x00' not in raw,
                        'Local linked observation requires a supported raw path')
                try:
                    # Reuse the scan's lexical rules only. The placeholder base
                    # and empty mappings do not resolve or authorize a target.
                    if raw.startswith('attachments:'):
                        relative_linked_target(raw, '/')
                    else:
                        absolute_linked_target(raw, [])
                except FileIssue:
                    invalid('Local linked observation requires a supported raw path')
        if ref['linkMode'] == 2 and ('resolvedPath' in ref or ref['status'] in ('present', 'missing')):
            # Failed reads can still claim a resolved target. Unresolved partial
            # scans have no target to contradict and remain coverage gaps.
            require(ref.get('resolvedPath') == str(linked_scan_target(ref['path'], report.get('policy'))),
                    'Linked attachment target mismatch with scan policy')
        if ref['linkMode'] in (0, 1, 4) and ('resolvedPath' in ref or ref['status'] in ('present', 'missing')):
            # A stored target is fixed by this report's storage root, item key
            # and raw filename. Agreement with an edited file references list
            # alone cannot establish that relationship. No source path IO.
            raw = ref['path']
            leaf = raw[len('storage:'):] if isinstance(raw, str) and raw.startswith('storage:') else ''
            require(bool(leaf) and leaf not in ('.', '..')
                    and not any(c in leaf for c in ('/', '\\', '\x00'))
                    and ref.get('resolvedPath') == str(storage / ref['key'] / leaf),
                    'Stored attachment target mismatch')
        refs[ident] = ref
        refs_by_id[ref['itemID']] = ref
    local_linked = [ref for ref in refs.values()
                    if ref['linkMode'] == 2 and ref['status'] in ('present', 'missing')]
    if local_linked:
        detail = 'Local linked observation outside declared allowed roots'
        declared = report['policy'].get('allowedLinkedRoots')
        require(isinstance(declared, list), detail)
        try:
            allowed = [normalized_path(root) for root in declared]
        except ValueError as exc:
            raise ValueError(detail) from exc
        # Match the scan's strict component boundary, using report strings only.
        # A mapping, storage inventory or another alias never grants allowance.
        # Blocked/unresolved observations do not claim a completed local check.
        for ref in local_linked:
            target = Path(ref['resolvedPath'])
            require(any(target != root and target.is_relative_to(root) for root in allowed), detail)
    files, by_path = {}, {}
    for record in report['files']:
        require(isinstance(record, dict), 'Invalid file evidence')
        path = normalized_path(record.get('path'))
        source = record.get('source')
        require(source in ('storage', 'linked'), 'Invalid file source')
        if source == 'storage':
            require(path.is_relative_to(storage) and len(path.relative_to(storage).parts) >= 2,
                    'Storage file outside storage key directory')
            relative = path.relative_to(storage)
            require(bool(KEY.fullmatch(relative.parts[0])), 'Invalid storage key')
            file_id = {'source': source, 'relativePath': str(relative)}
            item_owners = owners.get(relative.parts[0], [])
        else:
            file_id = {'source': source, 'path': str(path)}
            item_owners = []
        ident = token(file_id)
        require(ident not in files and str(path) not in by_path, 'Duplicate file identity')
        stat = record.get('stat')
        require(isinstance(stat, dict) and type(stat.get('size')) is int and stat['size'] >= 0,
                'Invalid file size')
        references = record.get('references')
        require(isinstance(references, list) and all(type(i) is int for i in references)
                and len(set(references)) == len(references) and all(i in refs_by_id for i in references),
                'Dangling or duplicate file reference')
        require(source != 'linked' or bool(references), 'Linked file has no attachment reference')
        for i in references:
            require(refs_by_id[i].get('resolvedPath') == str(path), 'File/reference path mismatch')
            # Observed aliases share the final file status, including failed
            # reads. A missing/remote alias cannot also observe a present file.
            # Matching nonpresent observations remain ordinary coverage gaps.
            require(refs_by_id[i]['status'] == record.get('status'), 'File/reference status mismatch')
        if not stable_hash(record):
            reasons.append('file_without_stable_sha256')
        view = {'identity': file_id, 'path': str(path), 'size': stat['size'],
                'sha256': record.get('sha256'),
                'attachmentIdentities': sorted((identity(refs_by_id[i]) for i in references), key=token),
                'itemIdentities': sorted(item_owners, key=token)}
        files[ident] = view
        by_path[str(path)] = (record, view)
    attachment_views = {}
    for ident, ref in refs.items():
        file = by_path.get(ref.get('resolvedPath'))
        if ref['status'] == 'present':
            require(file is not None and ref['itemID'] in file[0]['references'], 'Present attachment lacks file evidence')
        if ref['status'] == 'missing':
            # Check the target inventory independently of the file's references:
            # omitting an alias cannot make an inventoried present target absent.
            require(file is None or file[0].get('status') != 'present',
                    'Missing attachment target is inventoried present')
        attachment_views[ident] = {
            'identity': identity(ref), 'status': ref['status'], 'linkMode': ref['linkMode'],
            'path': ref['path'], 'parentIdentity': identity(items[ref['parentItemID']]) if ref.get('parentItemID') is not None else None,
            'inTrash': ref['inTrash'], 'parentInTrash': ref['parentInTrash'],
            'size': file[1]['size'] if ref['status'] == 'present' else None,
            'sha256': file[1]['sha256'] if ref['status'] == 'present' else None}
    directories, orphans = {}, {}
    for field in ('orphanCandidates', 'retainedDirectories'):
        for directory in report[field]:
            require(isinstance(directory, dict) and isinstance(directory.get('key'), str)
                    and bool(KEY.fullmatch(directory['key'])), 'Invalid directory identity')
            key = directory['key']
            require(key not in directories and directory.get('path') == str(storage / key), 'Duplicate/invalid directory path')
            require((key not in owners) == (field == 'orphanCandidates'), 'Orphan classification disagrees with all-item inventory')
            directories[key] = {'key': key, 'itemIdentities': sorted(owners.get(key, []), key=token)}
            if field == 'orphanCandidates':
                orphans[key] = directories[key]
    require(all(Path(f['identity']['relativePath']).parts[0] in directories
                for f in files.values() if f['identity']['source'] == 'storage'), 'File missing directory inventory')
    for group in report['duplicateGroups']:
        require(isinstance(group, dict) and isinstance(group.get('files'), list),
                'Invalid duplicate group members')
        digest, size = group.get('sha256'), group.get('size')
        require(digest is None or (isinstance(digest, str) and bool(DIGEST.fullmatch(digest))),
                'Invalid duplicate group digest')
        require(size is None or (type(size) is int and size >= 0), 'Invalid duplicate group size')
        member_identities = set()
        for member in group['files']:
            require(isinstance(member, dict), 'Invalid duplicate member')
            path = normalized_path(member.get('path'))
            primary = by_path.get(str(path))
            require(primary is not None and member.get('source') == primary[0]['source'],
                    'Duplicate member lacks matching file inventory')
            # Uniqueness is per declared group in original report coordinates,
            # independent of equal bytes, inodes, aliases or later relocation.
            member_id = (str(path), member['source'])
            require(member_id not in member_identities, 'Repeated duplicate group member identity')
            member_identities.add(member_id)
            record = primary[0]
            status = member.get('status')
            require(status is None or (isinstance(status, str) and bool(status)),
                    'Invalid duplicate member status')
            # Missing/null projections do not establish an observation. Compare
            # available statuses before relocation, including in partial reports;
            # agreeing failed observations still lack stable-hash coverage.
            if status is not None and record.get('status') is not None:
                require(status == record['status'],
                        'Duplicate member status disagrees with file inventory')
            # Compare observations in this report's original coordinates. Missing
            # primary hash evidence remains a coverage gap, not a contradiction.
            if record.get('sha256') is not None and member.get('sha256') is not None:
                require(member['sha256'] == record['sha256'],
                        'Duplicate member digest disagrees with file inventory')
            references = member.get('references')
            require(isinstance(references, list) and all(type(i) is int for i in references)
                    and len(set(references)) == len(references)
                    and set(references) == set(record['references']),
                    'Duplicate member references disagree with file inventory')
            # A group header is another observation of each member's bytes.
            # Check both projections independently: one may lack evidence in a
            # partial report. Never fill gaps or relocate paths to make a match.
            for observation in (member, record):
                if digest is not None and observation.get('sha256') is not None:
                    require(digest == observation['sha256'],
                            'Duplicate group digest disagrees with file evidence')
                stat = observation.get('stat')
                if size is not None and isinstance(stat, dict) and stat.get('size') is not None:
                    require(type(stat['size']) is int and size == stat['size'],
                            'Duplicate group size disagrees with file evidence')
    coverage['reasons'] = sorted(set(reasons))
    coverage['complete'] = not reasons
    groups = defaultdict(dict)
    for ident, file in files.items():
        groups[(file['size'], file['sha256'])][ident] = file
    return {'scope': scope, 'libraries': libraries, 'attachments': attachment_views,
            'files': files, 'owners': owners, 'orphans': orphans,
            'groups': {k: v for k, v in groups.items() if len(v) > 1}}


def changes_between(before: dict, after: dict) -> dict:
    changes = {}
    for category in ('attachments', 'files'):
        old, new = before[category], after[category]
        changes[category + 'Added'] = [new[i] for i in sorted(new.keys() - old.keys())]
        changes[category + 'Removed'] = [old[i] for i in sorted(old.keys() - new.keys())]
        changed = []
        for ident in sorted(old.keys() & new.keys()):
            left, right = old[ident], new[ident]
            different = (left != right) if category == 'attachments' else (
                (left['size'], left['sha256']) != (right['size'], right['sha256']))
            if different:
                changed.append({'identity': left['identity'], 'before': left, 'after': right})
        changes[category + 'Changed'] = changed
    for name, left_status, right_status in (('restoredAttachments', 'missing', 'present'),
                                           ('disappearedAttachments', 'present', 'missing')):
        changes[name] = [row for row in changes['attachmentsChanged']
                         if row['before']['status'] == left_status and row['after']['status'] == right_status]
    changes['newOrphans'] = [dict(after['orphans'][key], previousItemIdentities=sorted(before['owners'].get(key, []), key=token))
                             for key in sorted(after['orphans'].keys() - before['orphans'].keys())]
    changes['resolvedOrphans'] = [dict(before['orphans'][key], currentItemIdentities=sorted(after['owners'].get(key, []), key=token),
                                     reason='retained' if key in after['owners'] else 'directory_absent')
                                  for key in sorted(before['orphans'].keys() - after['orphans'].keys())]
    old, new = before['groups'], after['groups']
    for name, keys, source in (('duplicateGroupsAdded', new.keys() - old.keys(), new),
                               ('duplicateGroupsRemoved', old.keys() - new.keys(), old)):
        changes[name] = [{'size': size, 'sha256': digest, 'members': [source[(size, digest)][i] for i in sorted(source[(size, digest)])]}
                         for size, digest in sorted(keys)]
    changes['duplicateGroupsChanged'] = []
    for size, digest in sorted(old.keys() & new.keys()):
        left, right = old[(size, digest)], new[(size, digest)]
        changed_members = [
            {'identity': left[i]['identity'], 'before': left[i], 'after': right[i]}
            for i in sorted(left.keys() & right.keys())
            if (left[i]['attachmentIdentities'], left[i]['itemIdentities'])
            != (right[i]['attachmentIdentities'], right[i]['itemIdentities'])]
        if left.keys() != right.keys() or changed_members:
            changes['duplicateGroupsChanged'].append({
                'size': size, 'sha256': digest,
                'beforeMembers': [left[i] for i in sorted(left)], 'afterMembers': [right[i] for i in sorted(right)],
                'removedMembers': [left[i] for i in sorted(left.keys() - right.keys())],
                'addedMembers': [right[i] for i in sorted(right.keys() - left.keys())],
                'changedMembers': changed_members})
    return changes


def relocate_linked_identities(index: dict, mappings: list[tuple[Path, Path]]) -> None:
    """Reindex BEFORE only, preserving original paths, references and byte evidence.

    Configuration is injective across mapped roots, but an unmapped BEFORE file
    may already occupy a destination identity. Never merge those observations.
    """
    files, groups = {}, defaultdict(dict)
    for file in index['files'].values():
        if file['identity']['source'] == 'linked':
            try:
                target = absolute_linked_target(file['path'], mappings)
            except FileIssue as exc:
                raise InputError('invalid_linked_mapping', exc.detail) from exc
            if str(target) != file['path']:
                file = dict(file, originalIdentity=file['identity'],
                            identity={'source': 'linked', 'path': str(target)})
        ident = token(file['identity'])
        if ident in files:
            raise InputError('ambiguous_linked_identity',
                             f"Mapping collapses distinct before files: {files[ident]['path']} and {file['path']}")
        files[ident] = file
        groups[(file['size'], file['sha256'])][ident] = file
    index['files'] = files
    index['groups'] = {key: members for key, members in groups.items() if len(members) > 1}


def compare_reports(before: str | Path, after: str | Path, *,
                    linked_root_mappings: tuple[tuple[str | Path, str | Path], ...] = ()) -> dict:
    """Read exact input bytes without modifying reports; withhold changes on coverage gaps.

    sourceScope is the caller's assertion of source database identity, not a
    discovered account identifier. Reject scope or observed library-set changes.
    Optional root pairs map BEFORE linked identities to AFTER once, without IO
    against either root. Raw attachment paths remain independently comparable.
    """
    result = {'comparisonVersion': 1, 'tool': 'zotero-attach-audit', 'comparisonComplete': False,
              'fatal': False, 'inputs': {}, 'comparisonErrors': [], 'changes': None,
              'policy': {'linkedRootMappings': []}, 'linkedFileAlignments': [],
              'caveats': ['Source identity is caller-declared; never reuse a scope for unrelated profiles.',
                          'libraryID/key identify items only within that source scope. itemID is not stable.',
                          'Storage files use key/relative path; linked files use absolute paths with optional explicit before-to-after root mapping. Unmapped renames are removal/addition.',
                          'All bytes are rehashed during comparison-ready scans. Reports are evidence, not authenticated attestations.',
                          'No deletion recommendation; snapshot/storage consistency remains caller-supplied.']}
    try:
        mappings = validate_linked_mappings(linked_root_mappings)
    except InputError as exc:
        result['fatal'] = True
        result['comparisonErrors'].append({'code': exc.code, 'detail': exc.detail})
        return result
    result['policy']['linkedRootMappings'] = [
        {'source': str(source), 'destination': str(destination)} for source, destination in mappings]
    indexes = {}
    for role, raw in (('before', before), ('after', after)):
        path = absolute(raw)
        entry = {'path': str(path), 'sha256': None, 'coverage': {'complete': False, 'reasons': []}}
        result['inputs'][role] = entry
        try:
            report = read_report(path, entry)
            indexes[role] = index_report(report, entry['coverage'])
        except (FileIssue, OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            result['fatal'] = True
            entry['coverage']['complete'] = False
            entry['coverage']['reasons'].append('invalid_report')
            result['comparisonErrors'].append({'code': 'invalid_report', 'input': role, 'detail': str(exc)})
    if not result['fatal'] and all(indexes.values()):
        left, right = indexes['before'], indexes['after']
        for code, field in (('comparison_scope_mismatch', 'scope'), ('comparison_library_mismatch', 'libraries')):
            if left[field] != right[field]:
                result['fatal'] = True
                result['comparisonErrors'].append({'code': code, 'detail': f'Different {field}; no change conclusions'})
                break
    if not result['fatal']:
        for role, entry in result['inputs'].items():
            if not entry['coverage']['complete']:
                result['comparisonErrors'].append({'code': 'incomplete_evidence', 'input': role,
                                                   'detail': entry['coverage']['reasons']})
        if not result['comparisonErrors']:
            if mappings:
                try:
                    relocate_linked_identities(indexes['before'], mappings)
                except InputError as exc:
                    result['fatal'] = True
                    result['comparisonErrors'].append({'code': exc.code, 'detail': exc.detail})
                    return result
            result['comparisonComplete'] = True
            result['sourceScope'] = indexes['before']['scope']
            result['libraryIDs'] = indexes['before']['libraries']
            result['changes'] = changes_between(indexes['before'], indexes['after'])
            old, new = indexes['before']['files'], indexes['after']['files']
            result['linkedFileAlignments'] = [
                {'identity': old[i]['identity'], 'before': old[i], 'after': new[i]}
                for i in sorted(old.keys() & new.keys()) if 'originalIdentity' in old[i]]
    return result
