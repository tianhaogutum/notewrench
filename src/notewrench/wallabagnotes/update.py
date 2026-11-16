"""Opt-in complete-snapshot updates with byte-preserved personal page regions.

SPDX-License-Identifier: AGPL-3.0-only
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import difflib
import hashlib
import os
from pathlib import Path
import re
import stat
import tempfile

from .core import (BagnotesError, Export, canonical, decode, identifier, json_encode,
                   parse, parse_sources, validate_namespace)
from .render import FORMATS, input_metadata, json_text, provenance, render

BEGIN = b'<!-- bagnotes:managed:start -->\n'
END = b'<!-- bagnotes:managed:end -->\n'
STATE = 'update-state.json'
CHANGES = 'changes.json'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def conflict(name: str, message: str) -> None:
    raise BagnotesError('update_conflict', message, name)


def path_mode(path: Path) -> int:
    """Classify absent paths without hiding permission or other OS failures."""
    try:
        return path.lstat().st_mode
    except (FileNotFoundError, NotADirectoryError):
        return 0


def read_owned(output: Path, name: str) -> bytes:
    target = output
    for part in Path(name).parts:
        target = target / part
        mode = path_mode(target)
        if stat.S_ISLNK(mode):
            conflict(name, 'Managed paths must not be symbolic links')
    if not stat.S_ISREG(mode):
        conflict(name, 'Tracked file is missing or is not a regular file')
    return target.read_bytes()


def region(data: bytes, name: str) -> tuple[bytes, bytes, bytes]:
    if data.count(BEGIN) != 1 or data.count(END) != 1:
        conflict(name, 'Expected exactly one intact managed start/end marker pair')
    if data.index(END) < data.index(BEGIN):
        conflict(name, 'Managed markers are out of order')
    prefix, rest = data.split(BEGIN)
    managed, suffix = rest.split(END)
    return prefix, BEGIN + managed + END, suffix


def managed(content: bytes) -> bytes:
    return BEGIN + content + (b'' if content.endswith(b'\n') else b'\n') + END


def normalized(raw: dict) -> dict:
    result = dict(raw)
    if 'id' in result:
        result['id'] = identifier(result['id'], '$.id')
    return result


def different(before: dict, after: dict) -> list[str]:
    return sorted(k for k in before.keys() | after.keys()
                  if k not in before or k not in after or canonical(before[k]) != canonical(after[k]))


def changes_between(before: dict, after: dict) -> dict:
    """Compare stored raw records, independent of order, occurrence and ID spelling."""
    old = {(a.get('namespace'), a['source_id']): a for a in before['articles']}
    new = {(a.get('namespace'), a['source_id']): a for a in after['articles']}
    result = dict(schema_version=1, added=[], modified=[], deleted=[], articles=[])
    for key in dict.fromkeys([*old, *new]):
        previous, current = old.get(key), new.get(key)
        context = dict(namespace=key[0], source_id=key[1])
        if previous is None or current is None:
            result['articles'].append(dict(**context, change='added' if previous is None else 'deleted'))
        else:
            fields = different(normalized(previous['metadata']), normalized(current['metadata']))
            if fields:
                result['articles'].append(dict(**context, change='modified', differing_fields=fields,
                                               before=previous['metadata'], after=current['metadata']))
        old_annotations = {a['identity']: a for a in previous['annotations']} if previous else {}
        new_annotations = {a['identity']: a for a in current['annotations']} if current else {}
        for identity in dict.fromkeys([*old_annotations, *new_annotations]):
            a, b = old_annotations.get(identity), new_annotations.get(identity)
            reference = b if b is not None else a
            record = dict(**context, identity=identity, anchor=reference['anchor'], block_id=reference['block_id'])
            if a is None:
                result['added'].append(dict(**record, after=b['raw']))
            elif b is None:
                result['deleted'].append(dict(**record, before=a['raw']))
            else:
                fields = different(normalized(a['raw']), normalized(b['raw']))
                if fields:
                    result['modified'].append(dict(**record, differing_fields=fields,
                                                   before=a['raw'], after=b['raw']))
    result['counts'] = {kind: len(result[kind]) for kind in ('added', 'modified', 'deleted')}
    return result


def load_state(output: Path, format_name: str | None = None,
               namespaces: list[str] | None = None) -> tuple[dict, bytes]:
    """Validate the manifest; when a format is supplied, also enforce update scope."""
    if not stat.S_ISDIR(path_mode(output)):
        raise BagnotesError('update_state', 'Update output must be a real directory', str(output))
    path = output / STATE
    if not stat.S_ISREG(path_mode(path)):
        raise BagnotesError('update_state', 'Only directories initialized with --update can be updated', str(path))
    data = path.read_bytes()
    try:
        state = decode(data)
        if not isinstance(state, dict) or state.get('schema_version') != 1 or state.get('tool') != 'bagnotes-update':
            raise ValueError('Unsupported update state')
        if format_name is not None and (state.get('format') != format_name or state.get('namespaces') != namespaces):
            raise BagnotesError('update_scope', 'Update format and account namespaces must match initialization', str(path))
        format_name = state['format']
        if format_name not in FORMATS:
            raise ValueError('Invalid format')
        namespaces = state['namespaces']
        if namespaces is not None:
            if not isinstance(namespaces, list) or not namespaces:
                raise ValueError('Invalid namespaces')
            for namespace in namespaces:
                validate_namespace(namespace, str(path))
            if namespaces != sorted(set(namespaces)):
                raise ValueError('Invalid namespace order or duplicates')
        pages, artifacts = state['pages'], state['artifacts']
        expected_artifacts = {'provenance.json', 'report.json', CHANGES}
        if format_name in ('all', 'csv'):
            expected_artifacts.add('annotations.csv')
        if not isinstance(pages, dict) or not isinstance(artifacts, dict) or set(artifacts) != expected_artifacts:
            raise ValueError('Invalid tracked files')
        stem = r'[1-9][0-9]{0,127}' if namespaces is None else r'[0-9a-f]{64}'
        kinds = ('markdown', 'logseq') if format_name == 'all' else (format_name,)
        for name in pages:
            if not any(re.fullmatch(kind + r'/article-' + stem + r'\.md', name)
                       for kind in kinds if kind != 'csv'):
                raise ValueError('Invalid tracked page path')
        if not all(isinstance(h, str) and re.fullmatch(r'[0-9a-f]{64}', h)
                   for h in [*pages.values(), *artifacts.values()]):
            raise ValueError('Invalid tracked hash')
        return state, data
    except (KeyError, TypeError, ValueError, BagnotesError) as exc:
        if isinstance(exc, BagnotesError) and exc.diagnostic['code'] == 'update_scope':
            raise
        raise BagnotesError('update_state', 'Invalid update state; restore the last complete bundle', str(path)) from exc


def retired(name: str) -> bytes:
    notice = b'Article absent from current snapshot; highlights removed.\n'
    return managed((b'- ' if name.startswith('logseq/') else b'') + notice)


def expected_regions(state: dict, snapshot: bytes) -> dict[str, bytes]:
    """Recover prior generated bytes from provenance, then verify every region hash.

    Old manifests store hashes only. Provenance retains every renderer input
    (article content is omitted but is not used by the page renderers).
    Never treat a newly supplied snapshot as the previous baseline.
    """
    try:
        previous = decode(snapshot)
        articles = []
        for article in previous['articles']:
            raw = dict(article['metadata'], annotations=[a['raw'] for a in article['annotations']])
            articles.append((article.get('namespace'), raw))
        namespaces = state['namespaces']
        if namespaces is None:
            if any(ns is not None for ns, _ in articles):
                raise ValueError('Unexpected namespace in legacy provenance')
            export = parse(json_encode([raw for _, raw in articles]).encode())
        else:
            if any(ns not in namespaces for ns, _ in articles):
                raise ValueError('Unknown namespace in provenance')
            export = parse_sources([(ns, json_encode([raw for label, raw in articles if label == ns]).encode())
                                    for ns in namespaces])
        files, _ = render(export, state['format'])
        active = {name: managed(data) for name, data in files.items() if name.endswith('.md')}
        if active.keys() - state['pages'].keys():
            raise ValueError('State omits active pages')
        expected = {name: active[name] if name in active else retired(name) for name in state['pages']}
        if any(sha(data) != state['pages'][name] for name, data in expected.items()):
            raise ValueError('Reconstructed baseline does not match saved region hashes')
        return expected
    except (KeyError, TypeError, ValueError, BagnotesError) as exc:
        raise BagnotesError('update_state', 'Cannot reconstruct the verified page baseline; restore the last complete bundle',
                            STATE) from exc


def byte_description(data: bytes) -> dict:
    return dict(sha256=sha(data), bytes=len(data), base64=base64.b64encode(data).decode('ascii'))


def region_diff(name: str, expected: bytes, current: bytes, *,
                labels: tuple[str, str] = ('expected', 'current')) -> str:
    # Compare bytes before decoding: invalid UTF-8 must not hide a difference.
    lines = difflib.diff_bytes(difflib.unified_diff, expected.splitlines(keepends=True),
                              current.splitlines(keepends=True),
                              fromfile=(name + ':' + labels[0]).encode(),
                              tofile=(name + ':' + labels[1]).encode())
    return ''.join(line.decode('utf-8', errors='backslashreplace') +
                   ('' if line.endswith(b'\n') else '\n\\ No newline at end of file\n') for line in lines)


def rendered_regions(export: Export, format_name: str) -> dict[str, bytes]:
    files, _ = render(export, format_name)
    return {name: managed(data) for name, data in files.items() if name.endswith('.md')}


def proposed_regions(expected: dict[str, bytes], active: dict[str, bytes], *, metadata_only: bool = False) -> list[dict]:
    """Compare verified saved regions to rendered next regions, without local bytes.

    Retirement notices are distinct from every active renderer output. Saved paths
    persist on retirement and return; new paths have an empty, absent before side.
    """
    changes = []
    for name in sorted(expected.keys() | active.keys()):
        before, after = expected.get(name, b''), active.get(name, retired(name))
        if before == after:
            continue
        before_state = ('absent' if name not in expected else
                        'retired' if before == retired(name) else 'active')
        after_state = 'active' if name in active else 'retired'
        change = ('added' if before_state == 'absent' else 'retired' if after_state == 'retired' else
                  'reappeared' if before_state == 'retired' else 'modified')
        describe = region_fingerprint if metadata_only else byte_description
        item = dict(path=name, change=change, before_state=before_state, after_state=after_state,
                    before=describe(before), after=describe(after))
        if not metadata_only:
            item['unified_diff'] = region_diff(name, before, after, labels=('saved', 'proposed'))
        changes.append(item)
    return changes


def region_fingerprint(data: bytes) -> dict:
    return dict(sha256=sha(data), bytes=len(data))


def region_relationship(name: str, saved: bytes, local: bytes | None, proposed: bytes,
                        *, proposed_active: bool, reason: dict | None) -> dict:
    """Classify exact region bytes only; an unavailable local never becomes page bytes."""
    if local is None:
        relationship = 'unavailable'
    elif local == saved:
        relationship = 'unchanged' if proposed == saved else 'proposed-only'
    elif proposed == saved:
        relationship = 'local-only'
    elif local == proposed:
        relationship = 'identical-local-and-proposed'
    else:
        relationship = 'divergent'

    return dict(path=name, saved_state='retired' if saved == retired(name) else 'active',
                proposed_state='active' if proposed_active else 'retired', relationship=relationship,
                saved=region_fingerprint(saved), local=region_fingerprint(local) if local is not None else None,
                proposed=region_fingerprint(proposed), unavailable_reason=reason)


def proposed_paths(output: Path, state: dict, export: Export) -> dict:
    """Inspect only untracked next-page destinations, never through a linked parent."""
    kinds = ('markdown', 'logseq') if state['format'] == 'all' else (state['format'],)
    candidates = {f'{kind}/{article.file_stem}.md': article
                  for article in export.articles for kind in kinds if kind != 'csv'}
    candidates = {name: article for name, article in candidates.items() if name not in state['pages']}
    collisions = []
    for name, article in sorted(candidates.items()):
        parent = Path(name).parent.as_posix()
        mode = path_mode(output / parent)
        if mode and not stat.S_ISDIR(mode):
            code, blocking_path = 'unsafe_page_parent', parent
        elif not mode:
            continue  # Absent format directories are allowed; no target lookup needed.
        else:
            mode = path_mode(output / name)
            if not mode:
                continue
            code, blocking_path = 'new_page_collision', name
        kind = ('symlink' if stat.S_ISLNK(mode) else 'directory' if stat.S_ISDIR(mode) else
                'file' if stat.S_ISREG(mode) else 'other')
        collisions.append(dict(path=name, code=code, namespace=article.namespace, source_id=article.source_id,
                               blocking_path=blocking_path, kind=kind))
    return dict(checked_new_pages=len(candidates), path_collisions=collisions)


def inspect_managed(output: Path, *, next_export: Export | None = None, format_name: str = 'all',
                    include_paths: bool = False, include_regions: bool = False,
                    include_relationships: bool = False, include_local_regions: bool = False,
                    managed_only: bool = False, metadata_only: bool = False) -> dict:
    """Read all tracked pages; return every conflict without changing the bundle.

    Invalid baseline files fail before a report is returned. By default damaged
    markers use whole-page diagnostics. managed_only suppresses that fallback
    with explicit unavailable reasons; boundaries are never inferred.
    Non-baseline artifact issues are separate from page diffs. Missing/symlinked
    tracked paths are reported without reading through symlinks. OS errors propagate.
    Optional next input is a complete snapshot in the initialized scope; its changes
    are independent of local conflicts and never replace the saved baseline.
    Opt-in destination collisions are separate; they also set the conflict status.
    Opt-in previews/relationships compare region bytes and do not affect status.
    metadata_only implies managed_only; retain fingerprints/summaries but omit
    encoded bytes, diffs and raw proposed change values from every enabled list.
    """
    managed_only = managed_only or metadata_only
    describe = region_fingerprint if metadata_only else byte_description
    if include_local_regions and (not include_relationships or next_export is None):
        raise BagnotesError('update_scope', 'Local-to-proposed previews require region relationships and an explicit next snapshot')
    if include_paths and next_export is None:
        raise BagnotesError('update_scope', 'Proposed path inspection requires an explicit next snapshot')
    if include_regions and next_export is None:
        raise BagnotesError('update_scope', 'Proposed region previews require an explicit next snapshot')
    if include_relationships and next_export is None:
        raise BagnotesError('update_scope', 'Region relationships require an explicit next snapshot')
    if next_export is not None and any(w['code'] == 'absent_annotations' for w in next_export.warnings):
        raise BagnotesError('incomplete_snapshot', 'Updates require an explicit annotations array on every article')
    # Check lexical ancestors before load_state can look through them. resolve()
    # or normalizing away '..' first could conceal a traversed symbolic link.
    absolute = output.absolute()
    for component in [*reversed(absolute.parents), absolute]:
        if stat.S_ISLNK(path_mode(component)):
            raise BagnotesError('update_state', 'Inspection bundle path and parents must not be symbolic links',
                                str(component))
    namespaces = (sorted({i['namespace'] for i in next_export.inputs})
                  if next_export is not None and next_export.inputs is not None else None)
    state, _ = load_state(output, format_name if next_export is not None else None, namespaces)
    # State/provenance establish the baseline; no other artifact is needed to
    # reconstruct pages. Never return a report if that baseline cannot be verified.
    snapshot = read_owned(output, 'provenance.json')
    if sha(snapshot) != state['artifacts']['provenance.json']:
        conflict('provenance.json', 'Tool-owned baseline was edited; restore it before inspection')
    expected = expected_regions(state, snapshot)
    proposed = None
    active = {}
    if next_export is not None:
        try:
            changes = changes_between(decode(snapshot), provenance(next_export))
        except (KeyError, TypeError, ValueError, BagnotesError) as exc:
            raise BagnotesError('update_state', 'Cannot compare the verified snapshot baseline; restore the last complete bundle',
                                'provenance.json') from exc
        if metadata_only:
            for kind in ('added', 'modified', 'deleted', 'articles'):
                changes[kind] = [{k: v for k, v in item.items() if k not in ('before', 'after')}
                                 for item in changes[kind]]
        proposed = dict(format=format_name, namespaces=namespaces, snapshot=input_metadata(next_export),
                        changes=changes, warnings=next_export.warnings)
        if include_regions or include_relationships:
            active = rendered_regions(next_export, format_name)
        if include_regions:
            proposed['region_changes'] = proposed_regions(expected, active, metadata_only=metadata_only)
        if include_paths:
            proposed.update(proposed_paths(output, state, next_export))
    artifact_conflicts = []
    for name in sorted(state['artifacts'].keys() - {'provenance.json'}):
        checksum = state['artifacts'][name]
        try:
            data = read_owned(output, name)
        except BagnotesError as exc:
            artifact_conflicts.append(dict(path=name, code='unreadable_artifact',
                                           message=exc.diagnostic['message'],
                                           expected_sha256=checksum, current_sha256=None))
            continue
        current = sha(data)
        if current != checksum:
            artifact_conflicts.append(dict(path=name, code='artifact_edit',
                                           message='Tool-owned artifact differs from the saved baseline',
                                           expected_sha256=checksum, current_sha256=current))
    conflicts = []
    relationships = []
    local_changes = []
    for name in sorted(expected):
        baseline = expected[name]
        def add_unavailable(code: str, message: str) -> None:
            item = dict(path=name, code=code, message=message, comparison='unavailable',
                        expected=describe(baseline), current=None)
            if not metadata_only:
                item['unified_diff'] = None
            if managed_only:
                item['unavailable_reason'] = dict(code=code, message=message)
            conflicts.append(item)

        # Populate relationships from the very same safe local read as diagnostics.
        # Never substitute whole-page data when extraction fails.
        def add_relationship(local: bytes | None, reason: dict | None = None) -> None:
            if include_relationships:
                next_region = active.get(name, retired(name))
                relationships.append(region_relationship(
                    name, baseline, local, next_region,
                    proposed_active=name in active, reason=reason))
                if include_local_regions and local is not None and local != next_region:
                    item = dict(path=name, local=describe(local), proposed=describe(next_region))
                    if not metadata_only:
                        item['unified_diff'] = region_diff(name, local, next_region, labels=('local', 'proposed'))
                    local_changes.append(item)

        try:
            data = read_owned(output, name)
        except BagnotesError as exc:
            add_relationship(None, dict(code='unreadable_page', message=exc.diagnostic['message']))
            add_unavailable('unreadable_page', exc.diagnostic['message'])
            continue
        try:
            _, current, _ = region(data, name)
            add_relationship(current)
            code, comparison = 'managed_edit', 'managed-region'
            message = 'Managed region differs from the saved baseline'
        except BagnotesError as exc:
            add_relationship(None, dict(code='invalid_markers', message=exc.diagnostic['message']))
            if managed_only:
                add_unavailable('invalid_markers', exc.diagnostic['message'])
                continue
            current = data
            code, comparison = 'invalid_markers', 'whole-page'
            message = exc.diagnostic['message']
        if comparison == 'whole-page' or current != baseline:
            item = dict(path=name, code=code, message=message, comparison=comparison,
                        expected=describe(baseline), current=describe(current))
            if not metadata_only:
                item['unified_diff'] = region_diff(name, baseline, current)
            if managed_only:
                item['unavailable_reason'] = None
            conflicts.append(item)
    report = dict(schema_version=1, tool='bagnotes-managed-conflicts', output=str(output),
                  status='conflicts' if conflicts or artifact_conflicts else 'clean',
                  checked_pages=len(expected), conflicts=conflicts,
                  checked_artifacts=len(state['artifacts']), artifact_conflicts=artifact_conflicts)
    if managed_only:
        report['managed_only'] = True
    if metadata_only:
        report['metadata_only'] = True
    if proposed is not None:
        if include_relationships:
            proposed['region_relationships'] = relationships
        if include_local_regions:
            proposed['local_region_changes'] = local_changes
        report['proposed_update'] = proposed
        if proposed.get('path_collisions'):
            report['status'] = 'conflicts'
    return report


@dataclass
class UpdatePlan:
    files: dict[str, bytes]
    expected: dict[str, bytes | None]
    changes: dict
    report: dict
    action: str


def plan_update(output: Path, export: Export, format_name: str, *,
                max_deleted_annotations: int | None = None) -> UpdatePlan:
    """Verify and plan a complete snapshot; optionally bound deleted identities.

    The budget is per call, including dry-run planning, and is never persisted.
    Initialization has no previous identities and therefore zero deletions.
    """
    if max_deleted_annotations is not None and (type(max_deleted_annotations) is not int or
                                                max_deleted_annotations < 0):
        raise BagnotesError('invalid_deletion_budget', 'Deletion limit must be a nonnegative integer or None',
                            '$.max_deleted_annotations')
    if any(w['code'] == 'absent_annotations' for w in export.warnings):
        raise BagnotesError('incomplete_snapshot', 'Updates require an explicit annotations array on every article')
    files, report = render(export, format_name)
    report['update_mode'] = 'complete-snapshot'
    report['files'] = sorted([*files, CHANGES, STATE])
    files['report.json'] = json_text(report).encode()
    namespaces = sorted({i['namespace'] for i in export.inputs}) if export.inputs is not None else None
    old_state = None
    expected: dict[str, bytes | None] = {}
    previous = dict(articles=[])
    if output.exists() or output.is_symlink():
        old_state, state_data = load_state(output, format_name, namespaces)
        expected[STATE] = state_data
        for name, checksum in old_state['artifacts'].items():
            data = read_owned(output, name)
            if sha(data) != checksum:
                conflict(name, 'Tool-owned artifact was edited; restore it before updating')
            expected[name] = data
        previous = decode(expected['provenance.json'])
        for name, checksum in old_state['pages'].items():
            data = read_owned(output, name)
            _, block, _ = region(data, name)
            if sha(block) != checksum:
                conflict(name, 'Managed region was edited; update refused without overwriting')
            expected[name] = data
    else:
        # Use the same new-output contract as ordinary exports, including dry runs.
        from .cli import check_output
        check_output(output)

    changes = changes_between(previous, provenance(export))
    pages = {}
    page_names = [name for name in files if name.endswith('.md')]
    if old_state:
        page_names = list(dict.fromkeys([*old_state['pages'], *page_names]))
    for name in page_names:
        if name in files:
            block = managed(files[name])
        else:
            # Retire rather than unlink a page: handwritten content and its path survive.
            block = retired(name)
        if name in expected:
            prefix, _, suffix = region(expected[name], name)
            files[name] = prefix + block + suffix
        else:
            if output.exists() and ((output / name).exists() or (output / name).is_symlink()):
                conflict(name, 'New managed page would collide with an untracked path')
            # Check a pre-existing format directory before any mutation, including dry-run.
            parent = output / Path(name).parent
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                conflict(name, 'Page parent must be a real directory')
            heading = b'- Personal notes\n' if name.startswith('logseq/') else b'## Personal notes\n'
            files[name] = block + b'\n' + heading
            expected[name] = None
        pages[name] = sha(block)

    if max_deleted_annotations is not None and changes['counts']['deleted'] > max_deleted_annotations:
        raise BagnotesError('deletion_budget_exceeded',
                            'Planned annotation deletions exceed the selected limit', str(output),
                            max_deleted_annotations=max_deleted_annotations,
                            deleted_annotations=changes['counts']['deleted'])

    # A replay does not rewrite even changes.json, state, or pages with new personal notes.
    if old_state and all(expected.get(name) == data for name, data in files.items()):
        return UpdatePlan({}, expected, changes, report, 'unchanged')
    files[CHANGES] = json_text(changes).encode()
    state = dict(schema_version=1, tool='bagnotes-update', format=format_name, namespaces=namespaces,
                 pages=pages, artifacts={name: sha(data) for name, data in files.items() if name not in pages})
    files[STATE] = json_text(state).encode()
    if old_state:
        for name in files:
            expected.setdefault(name, None)
        files = {name: data for name, data in files.items() if expected[name] != data}
    # State is the last completion marker. Existing publication uses staged replacements.
    files = {name: data for name, data in files.items() if name != STATE} | {STATE: files[STATE]}
    return UpdatePlan(files, expected, changes, report, 'updated' if old_state else 'initialized')


def publish_update(output: Path, plan: UpdatePlan) -> None:
    """Stage all writes/backups, recheck the read set, replace; roll back ordinary errors.

    No directory-level crash atomicity or concurrent-editor guarantee is claimed.
    Backups only live in this transaction's temporary directory.
    """
    if not plan.files:
        return
    with tempfile.TemporaryDirectory(prefix='.bagnotes-update-', dir=output.parent) as temporary:
        staging = Path(temporary)
        replacements, backups = {}, {}
        for index, (name, data) in enumerate(plan.files.items()):
            staged = staging / f'new-{index}'
            staged.write_bytes(data)
            replacements[name] = staged
            if plan.expected[name] is not None:
                backup = staging / f'old-{index}'
                backup.write_bytes(plan.expected[name])
                backups[name] = backup
        # Catch edits between planning and staging before touching the bundle.
        if not stat.S_ISDIR(path_mode(output)):
            conflict(str(output), 'Output directory changed while preparing update')
        for name, data in plan.expected.items():
            target = output / name
            if data is None:
                parent = target.parent
                if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                    conflict(name, 'Page parent changed while preparing update')
                if target.exists() or target.is_symlink():
                    conflict(name, 'Untracked path appeared while preparing update')
            elif read_owned(output, name) != data:
                conflict(name, 'Tracked file changed while preparing update')
        replaced, directories = [], []
        try:
            for name, staged in replacements.items():
                target = output / name
                if not target.parent.exists():
                    target.parent.mkdir()
                    directories.append(target.parent)
                os.replace(staged, target)
                replaced.append(name)
        except BaseException:
            for name in reversed(replaced):
                if name in backups:
                    os.replace(backups[name], output / name)
                else:
                    (output / name).unlink()
            for directory in reversed(directories):
                directory.rmdir()
            raise
