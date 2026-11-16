# SPDX-License-Identifier: AGPL-3.0-only
"""Explicit CLI inputs follow symlinks, but only read opened regular files."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from notewrench.archive.cli import main
from notewrench.archive.core import MAX_INPUT_BYTES

ROOT = Path(__file__).resolve().parents[2]
CASES = [('rescue', None), ('k2w', None), ('api-import', 'wallabag'), ('api-import', 'karakeep')]
MODES = [(), ('--strict',), ('--dry-run',), ('--strict', '--dry-run')]

# Run swaps in a bounded child too: a broken implementation must not hang tests.
SWAP = '''
import os, socket, sys
from pathlib import Path
from unittest.mock import patch
from notewrench.archive.cli import main
source, kind = Path(sys.argv[2]), sys.argv[1]
original_os_open, original_path_open = os.open, Path.open
swapped, sockets = [], []
def swap():
    source.rename(source.with_suffix('.original'))
    if kind == 'fifo': os.mkfifo(source)
    elif kind == 'directory': source.mkdir()
    else:
        local = socket.socket(socket.AF_UNIX)
        sockets.append(local)
        local.bind(str(source))
    swapped.append(True)
def fd_open(path, flags, *args, **kwargs):
    if not swapped and (Path(path) == source or
                        (kwargs.get('dir_fd') is not None and Path(path) == Path(source.name))): swap()
    return original_os_open(path, flags, *args, **kwargs)
def path_open(path, *args, **kwargs):
    if path == source and not swapped: swap()
    return original_path_open(path, *args, **kwargs)
try:
    with patch.object(os, 'open', fd_open), patch.object(Path, 'open', path_open):
        code = main(sys.argv[3:])
    assert swapped, 'swap hook was not reached'
finally:
    for local in sockets: local.close()
raise SystemExit(code)
'''


class SelectedInputTests(unittest.TestCase):
    @contextlib.contextmanager
    def fixture(self, command, kind=None):
        with tempfile.TemporaryDirectory(prefix='archive-input-') as td:
            root = Path(td)
            inputs = root / 'inputs'
            inputs.mkdir()
            source = inputs / 'input.json'
            if command != 'api-import':
                name = 'wallabag-v2.json' if command == 'rescue' else 'karakeep-links.json'
                data = json.loads((ROOT / 'examples' / 'archive' / name).read_bytes())
            else:
                shutil.copytree(ROOT / 'examples/archive/api', inputs, dirs_exist_ok=True)
                data = json.loads((inputs / f'{kind}-manifest.json').read_bytes())
                for page in data['pages']:
                    target = inputs / page['file']
                    response = json.loads(target.read_bytes())
                    items = response['_embedded']['items'] if kind == 'wallabag' else response['bookmarks']
                    for item in items:
                        if kind == 'karakeep':
                            for key in ['modifiedAt', 'taggingStatus', 'summarizationStatus', 'embeddingStatus', 'assets']:
                                item.pop(key, None)
                        tag_key = 'label' if kind == 'wallabag' else 'name'
                        item['tags'] = [{tag_key: tag[tag_key]} for tag in item['tags']]
                    target.write_bytes(self.raw(response))
                    page['sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
            source.write_bytes(self.raw(data))
            yield root, source

    def raw(self, data):
        return b'\xef\xbb\xbf' + json.dumps(data, ensure_ascii=False, indent=2).replace('\n', '\r\n').encode() + b'\r\n'

    def bounded_child(self, argv):
        child = subprocess.Popen([sys.executable, *map(str, argv)], cwd=ROOT,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            out, err = child.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            child.kill()
            out, err = child.communicate()  # always reap intentionally blocked children
            self.fail(f'input blocked for 60s; child killed/reaped (exit {child.returncode}); stdout={out!r}, stderr={err!r}')
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()
        return child.returncode, out.decode(), err.decode()

    def invoke(self, command, source, output, mode=()):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main([command, str(source), '-o', str(output), *mode])
        return code, out.getvalue(), err.getvalue()

    def reject(self, result, source, output, diagnostic='input_path:'):
        code, out, err = result
        self.assertEqual(code, 2, (out, err))
        self.assertEqual(out, '')
        self.assertIn(diagnostic, err)
        if diagnostic == 'input_path:':
            self.assertIn(str(source), err)
        self.assertNotIn('Traceback', err)
        self.assertFalse(os.path.lexists(output))
        self.assertFalse(list(output.parent.glob('.bagkeep-*')))

    def test_fifo_without_writer_rejects_all_commands_modes(self):
        for command, kind in CASES:
            with self.fixture(command, kind) as (root, source):
                source.unlink()
                os.mkfifo(source)
                for mode in MODES:
                    with self.subTest(command=command, kind=kind, mode=mode):
                        output = root / 'bundle'
                        result = self.bounded_child(['-m', 'notewrench', 'archive', command, source, '-o', output, *mode])
                        self.reject(result, source, output)
                        self.assertIn('not a regular file', result[2])

    def test_other_nonregular_missing_and_symlink_inputs_all_modes(self):
        for command, kind in CASES:
            for form in ['directory', 'socket', 'symlink-fifo', 'symlink-directory', 'missing', 'broken-symlink']:
                with self.subTest(command=command, kind=kind, form=form), self.fixture(command, kind) as (root, source):
                    source.unlink()
                    local = None
                    if form == 'directory': source.mkdir()
                    elif form == 'socket':
                        local = socket.socket(socket.AF_UNIX)
                        local.bind(str(source))
                    elif form.startswith('symlink-'):
                        target = root / 'nonregular'
                        if form == 'symlink-fifo': os.mkfifo(target)
                        else: target.mkdir()
                        source.symlink_to(target)
                    elif form == 'broken-symlink': source.symlink_to(root / 'absent')
                    try:
                        for mode in MODES:
                            output = root / 'bundle'
                            self.reject(self.bounded_child(['-m', 'notewrench', 'archive', command, source, '-o', output, *mode]), source, output)
                    finally:
                        if local: local.close()

    def test_at_open_nonregular_substitution_rejects_all_modes(self):
        for command, kind in CASES:
            for form in ['fifo', 'directory', 'socket']:
                for mode in MODES:
                    with self.subTest(command=command, kind=kind, form=form, mode=mode), self.fixture(command, kind) as (root, source):
                        output = root / 'bundle'
                        result = self.bounded_child(['-c', SWAP, form, source, command, source, '-o', output, *mode])
                        self.reject(result, source, output)
                        self.assertTrue(source.with_suffix('.original').is_file())

    def test_regular_bytes_symlinks_hashes_and_determinism(self):
        for command, kind in CASES:
            with self.subTest(command=command, kind=kind), self.fixture(command, kind) as (root, source):
                originals = {p.name: p.read_bytes() for p in source.parent.iterdir()}
                leaf = root / 'selected.json'
                leaf.symlink_to(source)
                directory = root / 'selected-directory'
                directory.symlink_to(source.parent, target_is_directory=True)
                expected = None
                for selected in [source, leaf, directory / source.name]:
                    for mode in MODES:
                        output = root / f'bundle-{len(list(root.glob("bundle-*")))}'
                        code, out, err = self.invoke(command, selected, output, mode)
                        self.assertEqual(code, 0, err)
                        report = json.loads(out)['manifest']
                        self.assertEqual(report['input_sha256'], hashlib.sha256(originals[source.name]).hexdigest())
                        if '--dry-run' in mode:
                            self.assertFalse(output.exists())
                            self.assertEqual(report, json.loads(expected['manifest.json']))
                        else:
                            files = {p.name: p.read_bytes() for p in output.iterdir()}
                            if expected is None: expected = files
                            self.assertEqual(files, expected)
                            for name, info in report['files'].items():
                                self.assertEqual(hashlib.sha256(files[name]).hexdigest(), info['sha256'])
                            if kind:
                                for page in report['pagination']['pages']:
                                    self.assertEqual(page['status'], 'verified')
                                    self.assertEqual(page['actual_sha256'], hashlib.sha256(originals[page['file']]).hexdigest())
                self.assertEqual(originals, {p.name: p.read_bytes() for p in source.parent.iterdir()})

    def test_regular_size_decode_and_format_errors_remain_errors(self):
        for command, kind in CASES:
            with self.fixture(command, kind) as (root, source):
                for raw, message in [(b'\xff', 'UTF-8'), (b'{', 'JSON'), (b'null', '$'), (b'{"x":1,"x":2}', 'duplicate JSON key')]:
                    source.write_bytes(raw)
                    for mode in MODES:
                        with self.subTest(command=command, kind=kind, raw=raw, mode=mode):
                            self.reject(self.invoke(command, source, root / 'bundle', mode), source, root / 'bundle', message)
                with source.open('wb') as handle:
                    handle.truncate(MAX_INPUT_BYTES + 1)
                self.reject(self.invoke(command, source, root / 'bundle', ('--strict', '--dry-run')), source, root / 'bundle', '64 MiB')

    def test_descriptors_close_on_success_and_io_validation_failures(self):
        for command, kind in CASES:
            with self.fixture(command, kind) as (root, source):
                original = source.read_bytes()
                before = set(os.listdir('/proc/self/fd'))
                for i in range(4):
                    for failure in ['none', 'invalid-json', 'fstat', 'fdopen', 'read']:
                        source.write_bytes(b'{' if failure == 'invalid-json' else original)
                        with contextlib.ExitStack() as stack:
                            if failure in ['fstat', 'fdopen']:
                                stack.enter_context(patch('notewrench.archive.cli.os.' + failure, side_effect=OSError('synthetic ' + failure)))
                            elif failure == 'read':
                                handle = io.BytesIO(original)
                                handle.read = lambda *args: (_ for _ in ()).throw(OSError('synthetic read'))
                                stack.enter_context(patch('notewrench.archive.cli.os.fdopen', return_value=handle))
                            code, out, err = self.invoke(command, source, root / 'bundle', ('--dry-run',))
                        self.assertEqual(code, 0 if failure == 'none' else 2, err)
                        self.assertEqual(set(os.listdir('/proc/self/fd')), before)
