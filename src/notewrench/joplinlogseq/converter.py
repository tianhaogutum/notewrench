# SPDX-License-Identifier: AGPL-3.0-only
"""Two-pass graph construction and transactional publication to a fresh directory."""

from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata

from .archive import ConversionError, Limits, read_archive
from .items import TYPES, parse_item
from .render import render_body


def component(title: str, fallback: str) -> str:
    # A deliberately small portable alphabet also makes page links unambiguous.
    normalized = unicodedata.normalize('NFC', title)
    result = ''.join(c if c.isalnum() or c in ' -_.' else '_' for c in normalized)
    result = result.strip(' .') or fallback
    while '___' in result:
        result = result.replace('___', '__')
    return result[:60].rstrip(' .') or fallback


def page_filename(name):
    filename = name.replace('/', '___') + '.md'
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
    return '_' + filename if filename.split('.')[0].upper() in reserved else filename


def unique_names(candidates, key=str.casefold):
    """Resolve all case/Unicode collisions independent of archive ordering."""
    counts = Counter(key(name) for name in candidates.values())
    used = set()
    result = {}
    for item_id in sorted(candidates):
        base = candidates[item_id]
        name = base if counts[key(base)] == 1 else f'{base}--{item_id}'
        # A title may itself look like an automatically suffixed title.
        while key(name) in used or (name != base and key(name) in counts):
            name += '--' + item_id
        used.add(key(name))
        result[item_id] = name
    return result


def plan(files, *, include_organization_pages=False, include_notebook_pages=False,
         include_tag_pages=False):
    include_notebook_pages = include_organization_pages or include_notebook_pages
    include_tag_pages = include_organization_pages or include_tag_pages
    items = {}
    for name, data in sorted(files.items()):
        if name.startswith('resources/'):
            continue
        item = parse_item(name, data)
        if item.id in items:
            raise ConversionError(f'Duplicate item identity: {item.id}')
        items[item.id] = item
    if not items:
        raise ConversionError('Archive contains no item records')
    by_type = {kind: {i.id: i for i in items.values() if i.type == kind} for kind in TYPES}
    notes, folders, resources, tags, relations = (by_type[kind] for kind in TYPES)
    issues = []
    # Use an iterative parent walk to reject cycles without Python recursion limits.
    folder_paths = {}
    sibling_names = {}
    groups = defaultdict(dict)
    for folder in folders.values():
        parent = folder.metadata.get('parent_id', '').lower()
        if parent and parent not in folders:
            raise ConversionError(f'Missing parent folder {parent} for folder {folder.id}')
        groups[parent][folder.id] = component(folder.title, 'Untitled notebook')
    for group in groups.values():
        sibling_names.update(unique_names(group))
    for folder_id in sorted(folders):
        chain = []
        seen = set()
        current = folder_id
        while current and current not in folder_paths:
            if current in seen:
                raise ConversionError(f'Folder cycle involving {current}')
            seen.add(current)
            chain.append(current)
            current = folders[current].metadata.get('parent_id', '').lower()
        path = list(folder_paths.get(current, []))
        for entry in reversed(chain):
            path = path + [sibling_names[entry]]
            if len(path) > 32:
                raise ConversionError(f'Notebook hierarchy exceeds 32 levels: {entry}')
            folder_paths[entry] = path
    candidates = {}
    for note in notes.values():
        parent = note.metadata.get('parent_id', '').lower()
        if parent and parent not in folders:
            issues.append({'code': 'missing_note_folder', 'note_id': note.id, 'target': parent})
        parts = folder_paths.get(parent, ['Unfiled'] if parent else [])
        candidates[note.id] = '/'.join(parts + [component(note.title, 'Untitled')])
    # Reserve every identity in one namespace, including implicit tag pages and
    # notebooks not emitted in default mode. Toggling output must not change links.
    candidates.update({i: '/'.join(path) for i, path in folder_paths.items()})
    candidates.update({i.id: component(i.title, 'Untitled tag') for i in tags.values()})
    names = unique_names(candidates, key=lambda name: unicodedata.normalize('NFC', page_filename(name).casefold()))
    destinations = {}
    for item_id, name in names.items():
        filename = page_filename(name)
        # Keep a portable single filename component. Long hierarchies fail visibly.
        emitted = (item_id in notes or (item_id in folders and include_notebook_pages)
                   or (item_id in tags and include_tag_pages))
        if emitted and len(filename.encode('utf-8')) > 240:
            raise ConversionError(f'Page filename exceeds 240 UTF-8 bytes: {item_id}; shorten notebook/title hierarchy')
        destinations[item_id] = {'page': name, 'path': 'pages/' + filename if emitted else None}
    pages = {i: destinations[i] for i in sorted(notes)}
    # Namespace-to-filename encoding must be injective (also across case variants).
    if len({unicodedata.normalize('NFC', page_filename(name).casefold()) for name in names.values()}) != len(names):
        raise ConversionError('Encoded page filenames collide')
    assets = {}
    asset_data = {}
    remaining = {name for name in files if name.startswith('resources/')}
    resource_files = defaultdict(list)
    for name in sorted(remaining):
        resource_files[name.split('/')[1].split('.')[0].lower()].append(name)
    for resource in resources.values():
        ext = resource.metadata.get('file_extension', '')
        if ext and not re.fullmatch(r'[a-zA-Z0-9_-]{1,32}', ext):
            raise ConversionError(f'Unsafe resource file_extension: {resource.id}')
        matches = resource_files[resource.id]
        if len(matches) != 1:
            raise ConversionError(f'Missing or ambiguous resource bytes: {resource.id}')
        source = matches[0]
        actual_ext = source.split('/')[1][32:]
        if ext and actual_ext.lower() != '.' + ext.lower():
            raise ConversionError(f'Resource extension mismatch: {resource.id}')
        if actual_ext.lower() == '.crypted':
            raise ConversionError(f'Encrypted resource: {resource.id}')
        assets[resource.id] = 'assets/' + resource.id + actual_ext
        asset_data[resource.id] = files[source]
        remaining.remove(source)
    if remaining:
        raise ConversionError('Resource files without metadata: ' + ', '.join(sorted(remaining)))
    tag_names = {i: names[i] for i in tags}
    assigned_tags = defaultdict(set)
    tagged_notes = defaultdict(set)
    for relation in relations.values():
        note_id = relation.metadata.get('note_id', '').lower()
        tag_id = relation.metadata.get('tag_id', '').lower()
        if note_id not in notes or tag_id not in tags:
            raise ConversionError(f'Dangling note_tag relation: {relation.id}')
        assigned_tags[note_id].add(tag_names[tag_id])
        tagged_notes[tag_id].add(note_id)
    child_folders = defaultdict(list)
    folder_notes = defaultdict(list)
    for folder in folders.values():
        child_folders[folder.metadata.get('parent_id', '').lower()].append(folder.id)
    for note in notes.values():
        folder_notes[note.metadata.get('parent_id', '').lower()].append(note.id)
    folder_map = {i: {
        **destinations[i], 'namespace': '/'.join(folder_paths[i]),
        'source_title': folders[i].title,
        'parent_id': folders[i].metadata.get('parent_id', '').lower() or None,
        'child_ids': sorted(child_folders[i]), 'note_ids': sorted(folder_notes[i]),
    } for i in sorted(folders)}
    tag_map = {i: {**destinations[i], 'source_title': tags[i].title,
                   'note_ids': sorted(tagged_notes[i])} for i in sorted(tags)}
    link_pages = {i: entry for i, entry in destinations.items() if entry['path'] is not None}
    stats = {'note_links': 0, 'resource_links': 0, 'checkboxes': 0, 'protected_fences': 0}
    rendered = {}
    for note in notes.values():
        page = pages[note.id]
        body = render_body(note.body, note.id, link_pages, assets, issues, stats)
        header = [f"title:: {page['page']}", f'joplin-id:: {note.id}']
        parent = note.metadata.get('parent_id', '').lower()
        if include_notebook_pages and parent in folders:
            header.append('joplin-notebook:: [[' + names[parent] + ']]')
        if assigned_tags[note.id]:
            header.append('tags:: ' + ', '.join('[[' + t + ']]' for t in sorted(assigned_tags[note.id])))
        marker = ''
        if note.metadata.get('is_todo') == '1':
            marker = 'DONE ' if int(note.metadata.get('todo_completed') or '0') > 0 else 'TODO '
        # Plain escaped title is a block label; source title is retained in report.
        label = page['page'].split('/')[-1]
        content = '\n'.join(header) + '\n\n- ' + marker + label + '\n'
        if body:
            content += '  \n' + '\n'.join('  ' + line for line in body.split('\n')) + '\n'
        rendered[page['path']] = content.encode('utf-8')
    for kind, mapping in (('notebook', folder_map), ('tag', tag_map)):
        for item_id, entry in mapping.items():
            if entry['path'] is None:
                continue
            lines = [f"title:: {entry['page']}", f'joplin-id:: {item_id}', f'joplin-type:: {kind}']
            if kind == 'notebook' and entry['parent_id']:
                lines.append('joplin-parent:: [[' + names[entry['parent_id']] + ']]')
            lines.extend(['', '- ' + entry['page'].split('/')[-1]])
            groups = [('Notebooks', entry['child_ids'])] if kind == 'notebook' else []
            groups.append(('Notes', entry['note_ids']))
            for label, members in groups:
                if members:
                    lines.append('- ' + label)
                    lines.extend('  - [[' + names[i] + ']]' for i in members)
            rendered[entry['path']] = ('\n'.join(lines) + '\n').encode('utf-8')
    for item_id, destination in assets.items():
        rendered[destination] = asset_data[item_id]
    applied_keys = {
        1: {'id', 'type_', 'parent_id', 'is_todo', 'todo_completed', 'markup_language'},
        2: {'id', 'type_', 'parent_id'},
        4: {'id', 'type_', 'file_extension'},
        5: {'id', 'type_'},
        6: {'id', 'type_', 'note_id', 'tag_id'},
    }
    report = {
        'format_version': 1,
        'tool': 'jex2logseq',
        'options': {'include_organization_pages': include_organization_pages,
                    'include_notebook_pages': include_notebook_pages,
                    'include_tag_pages': include_tag_pages},
        'counts': {TYPES[k]: len(v) for k, v in by_type.items()},
        'output_pages': {'note': len(notes),
                         'folder': len(folders) if include_notebook_pages else 0,
                         'tag': len(tags) if include_tag_pages else 0},
        'rewrites': stats,
        'pages': pages,
        'folders': folder_map,
        'tags': tag_map,
        'assets': {i: {'path': assets[i], 'bytes': len(asset_data[i]), 'sha256': hashlib.sha256(asset_data[i]).hexdigest()} for i in sorted(assets)},
        'issues': issues,
        'metadata_not_applied': {i: {k: v for k, v in item.metadata.items() if k not in applied_keys[item.type]} for i, item in sorted(items.items())},
        'source_items': {i: {'type': item.type, 'title': item.title, 'body': item.body, 'metadata': item.metadata} for i, item in sorted(items.items())},
        'limitations': [
            'Targets Logseq Markdown file graphs, not DB graphs; no application UI validation.',
            'Bodies become multiline outline blocks; Markdown layout and plugin semantics may differ.',
            'Fenced code, conservatively indented code lines and paired inline code within a paragraph are not rewritten; framing line endings normalize to LF.',
            'Inline internal destinations accept plain URIs or paired angle wrappers; unmatched wrappers remain unchanged with context issues. Inline link/image titles support one-line double quotes, single quotes and parentheses with odd/even backslash escape parity; recognized labels/titles are data. Spaces/tabs after the opening parenthesis and before the closing parenthesis are preserved around the destination/title. Padded malformed destinations are not rewritten as bare URIs. An odd backslash run before an image marker leaves a literal exclamation mark followed by a normal link; even runs retain image mapping. Escaped opening brackets remain literal. Multiline titles, unescaped nested title parentheses, attached titles after an ID/angle closer and trailing junk are not parsed. Existing fragment grammar is unchanged.',
            'Internal reference definitions allow an adjacent-line destination and one-line title with backslash-escaped delimiters. A same-line title requires a space/tab after the destination; a next-line title requires no blank line. Whitespace and URI source lines are preserved; malformed destination lines remain unchanged with context issues. Multiline title contents and container definitions are not parsed. HTML tags/comments remain unchanged with context issues.',
            'All source metadata and normalized source bodies are retained here; metadata beyond identity/tags/todo is not applied to Logseq.',
            'Page and tag names are sanitized; consult source_items and the identity maps for original values.',
            'Notebook namespace prefixes are retained separately from allocated notebook page names; use explicit parent/notebook links and identity maps for hierarchy when collisions suffix a page.',
        ],
    }
    rendered['report.json'] = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode('utf-8')
    return rendered, report


def validate_destination(destination: Path):
    if destination.exists() or destination.is_symlink():
        raise ConversionError(f'Output path already exists (no overwrite): {destination}')
    if not destination.parent.is_dir():
        raise ConversionError(f'Output parent directory does not exist: {destination.parent}')


def convert(source, destination, *, limits=None, dry_run=False, include_organization_pages=False,
            include_notebook_pages=False, include_tag_pages=False):
    """Validate and convert a JEX; return its report. dry_run writes nothing.

    include_notebook_pages and include_tag_pages independently select all pages
    of that type. include_organization_pages selects both; flags combine by union.
    Their identities reserve names even when not emitted. Only emitted pages
    resolve source URIs; notebook properties appear when notebook pages are emitted.
    Destination must not exist, including an empty directory. The parent must exist.
    No archive-provided path is extracted and no script is executed.
    """
    destination = Path(os.path.abspath(destination))
    validate_destination(destination)
    rendered, report = plan(read_archive(Path(source), limits or Limits()),
                            include_organization_pages=include_organization_pages,
                            include_notebook_pages=include_notebook_pages,
                            include_tag_pages=include_tag_pages)
    if dry_run:
        return report
    # Stage everything before claiming output. mkdir is the no-overwrite race guard.
    stage = Path(tempfile.mkdtemp(prefix='.jex2logseq-', dir=destination.parent))
    claimed = False
    try:
        for name, data in rendered.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (stage / 'pages').mkdir(exist_ok=True)
        (stage / 'assets').mkdir(exist_ok=True)
        try:
            destination.mkdir()
        except FileExistsError as exc:
            raise ConversionError(f'Output path appeared during conversion: {destination}') from exc
        claimed = True
        for child in stage.iterdir():
            child.rename(destination / child.name)
    except BaseException:
        if claimed:
            shutil.rmtree(destination)
        raise
    finally:
        shutil.rmtree(stage)
    return report
