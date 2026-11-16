# SPDX-License-Identifier: AGPL-3.0-only
"""Response resources use a no-symlink, regular-file-only local path contract."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import unittest
from unittest.mock import patch

from notewrench.archive.cli import main

ROOT = Path(__file__).resolve().parents[2]
MODES = [(), ('--strict',), ('--dry-run',), ('--strict', '--dry-run')]


class PagePathTests(unittest.TestCase):
    @contextlib.contextmanager
    def fixture(self, kind, pinned=True):
        with tempfile.TemporaryDirectory(prefix='archive-page-path-') as td:
            root = Path(td)
            pages = root / 'pages'
            shutil.copytree(ROOT / 'examples/archive/api', pages)
            nested = pages / 'nested' / 'responses'
            nested.mkdir(parents=True)
            manifest = json.loads((pages / f'{kind}-manifest.json').read_bytes())
            for page in manifest['pages']:
                original = pages / page['file']
                response = json.loads(original.read_bytes())
                items = response['_embedded']['items'] if kind == 'wallabag' else response['bookmarks']
                for item in items:
                    if kind == 'karakeep':
                        for key in ['modifiedAt', 'taggingStatus', 'summarizationStatus', 'embeddingStatus', 'assets']:
                            item.pop(key, None)
                    tag_key = 'label' if kind == 'wallabag' else 'name'
                    item['tags'] = [{tag_key: tag[tag_key]} for tag in item['tags']]
                original.write_text(json.dumps(response))
                target = nested / original.name
                original.rename(target)
                page['file'] = 'nested/responses/' + target.name
                if pinned:
                    page['sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
            # Exercise locators independently from pagination assembly order.
            manifest['pages'].reverse()
            source = pages / 'manifest.json'
            source.write_text(json.dumps(manifest))
            yield root, pages, source, manifest

    def run_cli(self, source, output, mode):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(['api-import', str(source), '-o', str(output), *mode])
        return code, out.getvalue(), err.getvalue()

    def reject(self, source, output, mode, filename):
        code, out, err = self.run_cli(source, output, mode)
        self.assertEqual(code, 2, (out, err))
        self.assertEqual(out, '')
        self.assertIn('local_page_path:', err)
        self.assertIn('$.pages[0].file', err)
        self.assertIn(repr(filename), err)
        self.assertNotIn('Traceback', err)
        self.assertFalse(output.exists())
        self.assertFalse(list(output.parent.glob('.bagkeep-*')))

    def test_nested_regular_pages_preserve_hashes_modes_and_determinism(self):
        for kind in ['wallabag', 'karakeep']:
            for pins in [0, 1, 2]:
                with self.subTest(kind=kind, pins=pins), self.fixture(kind) as (root, pages, source, manifest):
                    for page in manifest['pages'][pins:]:
                        page.pop('sha256')
                    source.write_text(json.dumps(manifest))
                    original = {str(p.relative_to(pages)): p.read_bytes() for p in pages.rglob('*') if p.is_file()}
                    first = None
                    for i, mode in enumerate(MODES + [()]):
                        output = root / f'bundle-{i}'
                        code, out, err = self.run_cli(source, output, mode)
                        self.assertEqual(code, 0, err)
                        report = json.loads(out)['manifest']
                        for j, record in enumerate(report['pagination']['pages']):
                            page = manifest['pages'][j]
                            self.assertEqual(record['page_index'], j)
                            self.assertEqual(record['file'], page['file'])
                            self.assertEqual(record['actual_sha256'], hashlib.sha256(original[page['file']]).hexdigest())
                            self.assertEqual(record['expected_sha256'], page.get('sha256'))
                            self.assertEqual(record['status'], 'verified' if 'sha256' in page else 'unpinned')
                        if '--dry-run' in mode:
                            self.assertFalse(output.exists())
                            self.assertEqual(report, json.loads(first['manifest.json']))
                        else:
                            files = {p.name: p.read_bytes() for p in output.iterdir()}
                            if first is None:
                                first = files
                            self.assertEqual(files, first)
                            for name, meta in report['files'].items():
                                self.assertEqual(hashlib.sha256(files[name]).hexdigest(), meta['sha256'])
                    self.assertEqual(original, {str(p.relative_to(pages)): p.read_bytes() for p in pages.rglob('*') if p.is_file()})

    def test_unsafe_lexical_paths_and_missing_files_all_modes(self):
        for kind in ['wallabag', 'karakeep']:
            with self.fixture(kind) as (root, pages, source, manifest):
                for name in ['../outside.json', '/outside.json', 'https://example.org/p', 'C:/p', 'nested\\p', 'nested//p', './p', 'nested/./p', 'nested/../p', 'nested/p/', 'p\x00.json', 'p\n.json', 'p\x7f.json', 'missing.json']:
                    manifest['pages'][0]['file'] = name
                    source.write_text(json.dumps(manifest))
                    for mode in MODES:
                        with self.subTest(kind=kind, name=name, mode=mode):
                            self.reject(source, root / 'bundle', mode, name)

    def test_existing_internal_external_and_broken_symlinks_reject(self):
        for kind in ['wallabag', 'karakeep']:
            for internal in [False, True, None]:
                for parent in [False, True]:
                    with self.subTest(kind=kind, internal=internal, parent=parent), self.fixture(kind) as (root, pages, source, manifest):
                        name = manifest['pages'][0]['file']
                        target = pages / name
                        resource = target.parent if parent else target
                        moved = (pages if internal else root) / 'relocated'
                        resource.rename(moved)
                        resource.symlink_to(moved if internal is not None else root / 'absent', target_is_directory=parent)
                        for mode in MODES:
                            self.reject(source, root / 'bundle', mode, name)

    def test_outside_symlink_swaps_at_open_reject_even_with_matching_pin(self):
        for kind in ['wallabag', 'karakeep']:
            for pinned in [False, True]:
                for parent in [False, True]:
                    for mode in MODES:
                        with self.subTest(kind=kind, pinned=pinned, parent=parent, mode=mode), self.fixture(kind, pinned) as (root, pages, source, manifest):
                            name = manifest['pages'][0]['file']
                            target = pages / name
                            resource = target.parent if parent else target
                            outside = root / 'outside'
                            if parent:
                                shutil.copytree(resource, outside)
                            else:
                                shutil.copyfile(resource, outside)
                            # Outside bytes match the pin: hashes alone cannot confine paths.
                            swaps = []
                            def swap():
                                resource.rename(root / 'previous')
                                resource.symlink_to(outside, target_is_directory=parent)
                                swaps.append(str(resource))
                            def restore():
                                resource.unlink()
                                (root / 'previous').rename(resource)
                            real_path_open, real_os_open = Path.open, os.open
                            def path_open(path, *args, **kwargs):
                                if path == target and args and args[0] == 'rb' and not swaps:
                                    swap()
                                    try:
                                        return real_path_open(path, *args, **kwargs)
                                    finally:
                                        restore()
                                return real_path_open(path, *args, **kwargs)
                            def fd_open(path, flags, *args, **kwargs):
                                if kwargs.get('dir_fd') is not None and path == resource.name and not swaps:
                                    swap()
                                    try:
                                        return real_os_open(path, flags, *args, **kwargs)
                                    finally:
                                        restore()
                                return real_os_open(path, flags, *args, **kwargs)
                            # Both hooks express the same swap immediately before the actual
                            # resource open, across old Path and new descriptor readers.
                            with patch.object(Path, 'open', path_open), patch.object(os, 'open', fd_open):
                                self.reject(source, root / 'bundle', mode + (('--require-page-hashes',) if pinned else ()), name)
                            self.assertEqual(len(swaps), 1)

    def test_non_regular_resources_and_parent_components_do_not_block(self):
        for kind in ['wallabag', 'karakeep']:
            for form in ['directory', 'fifo', 'socket', 'parent-file', 'parent-fifo']:
                with self.subTest(kind=kind, form=form), self.fixture(kind) as (root, pages, source, manifest):
                    name = manifest['pages'][0]['file']
                    target = pages / name
                    if form.startswith('parent-'):
                        target = target.parent
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                    if form == 'directory':
                        target.mkdir()
                    elif form in ['fifo', 'parent-fifo']:
                        os.mkfifo(target)
                    elif form == 'parent-file':
                        target.write_bytes(b'ordinary file')
                    else:
                        local = socket.socket(socket.AF_UNIX)
                        self.addCleanup(local.close)
                        local.bind(str(target))
                    real_open = os.open
                    def guarded_open(path, flags, *args, **kwargs):
                        if kwargs.get('dir_fd') is not None:
                            # Fail promptly if implementation could block on a FIFO.
                            self.assertTrue(flags & (os.O_NONBLOCK | os.O_DIRECTORY))
                        return real_open(path, flags, *args, **kwargs)
                    with patch.object(os, 'open', guarded_open):
                        for mode in MODES:
                            self.reject(source, root / 'bundle', mode, name)

    def test_descriptors_close_after_success_and_failures(self):
        with self.fixture('wallabag') as (root, pages, source, manifest):
            name = manifest['pages'][0]['file']
            target = pages / name
            original = target.read_bytes()
            before = set(os.listdir('/proc/self/fd'))
            for i in range(12):
                for action in ['valid', 'missing', 'directory', 'invalid-json', 'bad-pin']:
                    if action == 'missing':
                        target.unlink()
                    elif action == 'directory':
                        target.unlink()
                        target.mkdir()
                    elif action == 'invalid-json':
                        target.write_bytes(b'{')
                    elif action == 'bad-pin':
                        target.write_bytes(original + b' ')
                    code, out, err = self.run_cli(source, root / 'bundle', ('--dry-run',))
                    self.assertEqual(code, 0 if action == 'valid' else 2, err)
                    if target.is_dir():
                        target.rmdir()
                    target.write_bytes(original)
                    self.assertEqual(set(os.listdir('/proc/self/fd')), before)
