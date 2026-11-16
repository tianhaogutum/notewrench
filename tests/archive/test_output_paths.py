# SPDX-License-Identifier: AGPL-3.0-only
"""Destination selection owns one parent; retargets cannot redirect rollback."""
import contextlib
import ctypes
import errno
import io
import hashlib
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import notewrench.archive.cli as cli
from . import test_input_paths as inputs


class OutputPathTests(unittest.TestCase):
    def test_reserved_child_replacement_never_opened_for_payload_writes(self):
        # Regression: mkdir/open substitution: the new publisher must
        # never acquire the destination this way. If acquired, preserve it.
        for mode in [(), ('--strict',)]:
            with self.subTest(mode=mode), self.fixture('rescue') as (root, source):
                output, other = root / 'bundle', root / 'unrelated'
                other.mkdir()
                sentinel = b'unrelated index must survive'
                (other / 'index.html').write_bytes(sentinel)
                real_open = os.open
                swaps = []

                def opening(path, flags, *args, **kwargs):
                    if str(path) == 'bundle' and flags & os.O_DIRECTORY and not swaps:
                        output.rename(root / 'moved-reservation')
                        other.rename(output)
                        swaps.append(True)
                    return real_open(path, flags, *args, **kwargs)

                before = set(os.listdir('/proc/self/fd'))
                with patch.object(os, 'open', opening):
                    code, out, err = self.invoke(source, output, mode)
                self.assertEqual(set(os.listdir('/proc/self/fd')), before)
                preserved = output if swaps else other
                self.assertEqual(self.bundle(preserved), {'index.html': sentinel},
                                 f'unrelated replacement overwritten; exit={code}, stderr={err}')
                if swaps:
                    self.assertEqual((code, out), (2, ''))
                else:
                    self.assertEqual(code, 0, err)
                    self.assertTrue((output / 'manifest.json').is_file())
                self.assertFalse(list(root.glob('.bagkeep-*')))

    def invoke(self, source, output, mode=()):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(['rescue', str(source), '-o', str(output), *mode])
        return code, out.getvalue(), err.getvalue()

    def test_selected_parent_retarget_at_publication_preserves_unrelated_bundle(self):
        # Regression probe, with the safety contract asserted instead.
        for mode in [(), ('--strict',)]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                original, replacement = root / 'original', root / 'replacement'
                original.mkdir()
                replacement.mkdir()
                selected = root / 'selected'
                selected.symlink_to(original, target_is_directory=True)
                victim = replacement / 'bundle'
                victim.mkdir()
                sentinel = victim / 'preserve.txt'
                sentinel.write_bytes(b'pre-existing unrelated output must survive')
                source = root / 'source.json'
                source.write_text(json.dumps([{'url': 'https://example.org/a', 'title': 'A', 'content': '<p>cached</p>'}]))
                real_publish = cli._publish_directory
                swaps = []

                def retarget(*args, **kwargs):
                    if not swaps:
                        selected.unlink()
                        selected.symlink_to(replacement, target_is_directory=True)
                        swaps.append(True)
                    return real_publish(*args, **kwargs)

                before = set(os.listdir('/proc/self/fd'))
                with patch.object(cli, '_publish_directory', retarget):
                    code, out, err = self.invoke(source, selected / 'bundle', mode)
                self.assertEqual(set(os.listdir('/proc/self/fd')), before)
                self.assertEqual(swaps, [True])
                self.assertTrue(sentinel.is_file(), f'unrelated replacement bundle deleted; exit={code}, stderr={err}')
                self.assertEqual(sentinel.read_bytes(), b'pre-existing unrelated output must survive')
                self.assertEqual({p.name for p in victim.iterdir()}, {'preserve.txt'})
                self.assertEqual(code, 0, err)
                self.assertEqual(json.loads(out)['status'], 'written')
                self.assertTrue((original / 'bundle' / 'index.html').is_file())
                self.assertFalse(list(original.glob('.bagkeep-*')))
                self.assertEqual({p.name for p in replacement.iterdir()}, {'bundle'})

    fixture = inputs.SelectedInputTests.fixture
    raw = inputs.SelectedInputTests.raw
    invoke_command = inputs.SelectedInputTests.invoke

    def bundle(self, path):
        return {p.name: p.read_bytes() for p in path.iterdir()}

    def test_all_commands_held_parent_during_staging_and_publication(self):
        for command, kind in inputs.CASES:
            for mode in [(), ('--strict',)]:
                for point in ['check', 'stage', 'publish', 'publish-error']:
                    with self.subTest(command=command, kind=kind, mode=mode, point=point), self.fixture(command, kind) as (root, source):
                        original, replacement = root / 'original', root / 'replacement'
                        original.mkdir()
                        replacement.mkdir()
                        selected = root / 'selected'
                        selected.symlink_to(original, target_is_directory=True)
                        victim = replacement / 'bundle'
                        victim.mkdir()
                        (victim / 'sentinel').write_bytes(b'unrelated bundle')
                        (replacement / 'sentinel').write_bytes(b'unrelated parent')
                        baseline = root / 'baseline'
                        code, out, err = self.invoke_command(command, source, baseline)
                        self.assertEqual(code, 0, err)
                        expected = self.bundle(baseline)
                        real_check, real_mkdir, real_publish = cli.check_output, os.mkdir, cli._publish_directory
                        swaps = []

                        def retarget():
                            if not swaps:
                                selected.unlink()
                                selected.symlink_to(replacement, target_is_directory=True)
                                swaps.append(True)

                        def check(path, **kwargs):
                            if point == 'check': retarget()
                            return real_check(path, **kwargs)

                        def mkdir(path, *args, **kwargs):
                            if point == 'stage' and str(path).startswith('.bagkeep-'): retarget()
                            return real_mkdir(path, *args, **kwargs)

                        def publish(*args, **kwargs):
                            if point in ['publish', 'publish-error']: retarget()
                            if point == 'publish-error':
                                raise OSError('synthetic publication failure')
                            return real_publish(*args, **kwargs)

                        before = set(os.listdir('/proc/self/fd'))
                        with patch.object(cli, 'check_output', check), patch.object(os, 'mkdir', mkdir), patch.object(cli, '_publish_directory', publish):
                            code, out, err = self.invoke_command(command, source, selected / 'bundle', mode)
                        self.assertEqual(set(os.listdir('/proc/self/fd')), before)
                        self.assertEqual(swaps, [True])
                        self.assertEqual(self.bundle(victim), {'sentinel': b'unrelated bundle'})
                        self.assertEqual((replacement / 'sentinel').read_bytes(), b'unrelated parent')
                        self.assertEqual({p.name for p in replacement.iterdir()}, {'bundle', 'sentinel'})
                        self.assertFalse(list(original.glob('.bagkeep-*')))
                        if point == 'publish-error':
                            self.assertEqual((code, out), (2, ''))
                            self.assertIn('synthetic publication', err)
                            self.assertEqual(list(original.iterdir()), [])
                        else:
                            self.assertEqual(code, 0, err)
                            self.assertEqual(self.bundle(original / 'bundle'), expected)
                            manifest = json.loads(expected['manifest.json'])
                            for name, info in manifest['files'].items():
                                self.assertEqual(hashlib.sha256(expected[name]).hexdigest(), info['sha256'])
                            self.assertEqual((original / 'bundle').stat().st_mode & 0o777, 0o700)

    def test_renamed_parent_is_held_during_success_and_rollback(self):
        for fail in [False, True]:
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                parent = root / 'parent'
                parent.mkdir()
                held = root / 'held'
                real_publish = cli._publish_directory
                def publish(*args, **kwargs):
                    if not held.exists():
                        parent.rename(held)
                        parent.mkdir()
                        (parent / 'bundle').mkdir()
                        (parent / 'bundle' / 'sentinel').write_bytes(b'keep')
                    if fail: raise OSError('synthetic publication failure')
                    return real_publish(*args, **kwargs)
                before = set(os.listdir('/proc/self/fd'))
                with patch.object(cli, '_publish_directory', publish):
                    if fail:
                        with self.assertRaises(OSError):
                            cli.write_bundle(parent / 'bundle', {'a': b'bytes', 'b': 'texté\n'})
                    else:
                        cli.write_bundle(parent / 'bundle', {'a': b'bytes', 'b': 'texté\n'})
                self.assertEqual(set(os.listdir('/proc/self/fd')), before)
                self.assertEqual(self.bundle(parent / 'bundle'), {'sentinel': b'keep'})
                self.assertEqual(list(held.iterdir()), [] if fail else [held / 'bundle'])
                if not fail: self.assertEqual(self.bundle(held / 'bundle'), {'a': b'bytes', 'b': 'texté\n'.encode()})

    def target(self, path, kind, source):
        if kind == 'file': path.write_bytes(b'keep')
        elif kind == 'empty-dir': path.mkdir()
        elif kind == 'directory':
            path.mkdir()
            (path / 'sentinel').write_bytes(b'keep')
        else: path.symlink_to(source if kind == 'symlink' else path.parent / 'absent')

    def test_existing_and_racing_targets_never_clobbered_all_modes(self):
        for command, kind in inputs.CASES:
            for target in ['file', 'empty-dir', 'directory', 'symlink', 'dangling-symlink']:
                for race in [False, True]:
                    with self.subTest(command=command, kind=kind, target=target, race=race), self.fixture(command, kind) as (root, source):
                        output = root / 'bundle'
                        real_publish = cli._publish_directory
                        calls = []
                        def publish(*args, **kwargs):
                            if race:
                                self.target(output, target, source)
                                calls.append(True)
                            return real_publish(*args, **kwargs)
                        if not race: self.target(output, target, source)
                        before = source.read_bytes()
                        # Publication races apply to writes; pre-existing conflicts
                        # also reject strict/dry-run before printing any plan.
                        for i, mode in enumerate(([(), ('--strict',)] if race else inputs.MODES)):
                            if race and i:
                                if output.is_symlink() or output.is_file(): output.unlink()
                                else:
                                    for child in output.iterdir(): child.unlink()
                                    output.rmdir()
                            fds = set(os.listdir('/proc/self/fd'))
                            with patch.object(cli, '_publish_directory', publish):
                                code, out, err = self.invoke_command(command, source, output, mode)
                            self.assertEqual(set(os.listdir('/proc/self/fd')), fds)
                            self.assertEqual((code, out), (2, ''))
                            self.assertFalse(list(root.glob('.bagkeep-*')))
                            self.assertEqual(source.read_bytes(), before)
                            if target == 'file': self.assertEqual(output.read_bytes(), b'keep')
                            elif target == 'directory': self.assertEqual(self.bundle(output), {'sentinel': b'keep'})
                            elif target == 'empty-dir': self.assertEqual(list(output.iterdir()), [])
                            else: self.assertEqual(os.readlink(output), str(source if target == 'symlink' else root / 'absent'))
                        self.assertEqual(calls, [True, True] if race else [])

    def test_no_write_modes_retarget_held_parent_without_staging(self):
        for command, kind in inputs.CASES:
            for mode in inputs.MODES[1:]:
                with self.subTest(command=command, kind=kind, mode=mode), self.fixture(command, kind) as (root, source):
                    strict = '--strict' in mode
                    if strict:
                        data = json.loads(source.read_bytes())
                        if command == 'rescue': data[0]['content'] = None
                        elif command == 'k2w': data['bookmarks'][0]['content'] = None
                        else:
                            page = data['pages'][0]
                            path = source.parent / page['file']
                            response = json.loads(path.read_bytes())
                            if kind == 'wallabag': response['_embedded']['items'][0]['content'] = None
                            else: response['bookmarks'][0]['content']['htmlContent'] = None
                            path.write_bytes(self.raw(response))
                            page['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
                        source.write_bytes(self.raw(data))
                    original, replacement = root / 'original', root / 'replacement'
                    original.mkdir()
                    replacement.mkdir()
                    (replacement / 'bundle').mkdir()
                    (replacement / 'bundle' / 'sentinel').write_bytes(b'keep')
                    selected = root / 'selected'
                    selected.symlink_to(original, target_is_directory=True)
                    real_check = cli.check_output
                    swaps = []
                    def check(path, **kwargs):
                        selected.unlink()
                        selected.symlink_to(replacement, target_is_directory=True)
                        swaps.append(True)
                        return real_check(path, **kwargs)
                    before = set(os.listdir('/proc/self/fd'))
                    with patch.object(cli, 'check_output', check), patch.object(os, 'mkdir', side_effect=AssertionError('no writes allowed')):
                        code, out, err = self.invoke_command(command, source, selected / 'bundle', mode)
                    self.assertEqual(set(os.listdir('/proc/self/fd')), before)
                    self.assertEqual(swaps, [True])
                    self.assertEqual(code, 3 if strict else 0, err)
                    self.assertEqual(json.loads(out)['status'], 'strict_rejected' if strict else 'planned')
                    self.assertEqual(list(original.iterdir()), [])
                    self.assertEqual(self.bundle(replacement / 'bundle'), {'sentinel': b'keep'})

    def test_stage_open_write_errors_and_interrupt_close_descriptors(self):
        for point in ['stage-open', 'payload-open', 'fdopen', 'payload-write', 'interrupt', 'bad-name']:
            with self.subTest(point=point), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                real_open = os.open
                def opening(path, flags, *args, **kwargs):
                    name = str(path)
                    if ((point == 'stage-open' and name.startswith('.bagkeep-')) or
                        (point == 'payload-open' and flags & os.O_CREAT)):
                        raise OSError('synthetic open failure')
                    return real_open(path, flags, *args, **kwargs)
                real_fdopen = os.fdopen
                @contextlib.contextmanager
                def fdopen(*args, **kwargs):
                    if point == 'fdopen': raise OSError('synthetic fdopen failure')
                    with real_fdopen(*args, **kwargs) as handle:
                        if point == 'payload-write':
                            with patch.object(handle, 'write', side_effect=OSError('synthetic write failure')):
                                yield handle
                        else: yield handle
                before = set(os.listdir('/proc/self/fd'))
                with patch.object(os, 'open', opening), patch.object(os, 'fdopen', fdopen), patch.object(cli, '_publish_directory', side_effect=KeyboardInterrupt):
                    with self.assertRaises((OSError, KeyboardInterrupt, cli.InputError)):
                        cli.write_bundle(root / 'bundle', {'../escape' if point == 'bad-name' else 'index.html': 'text'})
                self.assertEqual(set(os.listdir('/proc/self/fd')), before)
                self.assertEqual(list(root.iterdir()), [])

    def test_resolved_parent_substituted_symlink_rejects_without_writes(self):
        for mode in inputs.MODES:
            with self.subTest(mode=mode), self.fixture('rescue') as (root, source):
                parent, other = root / 'parent', root / 'other'
                parent.mkdir()
                other.mkdir()
                (other / 'sentinel').write_bytes(b'keep')
                real_open = os.open
                swaps = []
                def opening(path, flags, *args, **kwargs):
                    if Path(path) == parent and flags & os.O_DIRECTORY:
                        parent.rmdir()
                        parent.symlink_to(other, target_is_directory=True)
                        swaps.append(True)
                    return real_open(path, flags, *args, **kwargs)
                before = set(os.listdir('/proc/self/fd'))
                with patch.object(os, 'open', opening):
                    code, out, err = self.invoke(source, parent / 'bundle', mode)
                self.assertEqual(set(os.listdir('/proc/self/fd')), before)
                self.assertEqual(swaps, [True])
                self.assertEqual((code, out), (2, ''))
                self.assertIn('output parent', err)
                self.assertEqual(self.bundle(other), {'sentinel': b'keep'})

    def test_atomic_primitive_unavailable_never_falls_back(self):
        for failure in ['platform', 'ffi', 'symbol', 'library', errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.EACCES]:
            with self.subTest(failure=failure), self.fixture('rescue') as (root, source):
                def rename(*args):
                    ctypes.set_errno(failure)
                    return -1
                from types import SimpleNamespace
                loader = (patch.object(ctypes, 'CDLL', return_value=SimpleNamespace(renameat2=rename))
                          if isinstance(failure, int) else
                          patch.object(ctypes, 'CDLL', side_effect=AttributeError('missing symbol')
                                       if failure == 'symbol' else OSError('missing library')))
                before = set(os.listdir('/proc/self/fd'))
                with loader, patch.object(cli, 'ctypes', None if failure == 'ffi' else ctypes), \
                     patch.object(sys, 'platform', 'other' if failure == 'platform' else 'linux'), \
                     patch.object(os, 'rename', side_effect=AssertionError('unsafe fallback')), \
                     patch.object(os, 'replace', side_effect=AssertionError('unsafe fallback')):
                    code, out, err = self.invoke(source, root / 'bundle')
                self.assertEqual((code, out), (2, ''))
                if failure != errno.EACCES: self.assertIn('requires Linux libc renameat2', err)
                self.assertEqual(set(os.listdir('/proc/self/fd')), before)
                self.assertEqual({p.name for p in root.iterdir()}, {'inputs'})

    def test_staging_identity_changes_preserve_replacement(self):
        for point in ['open', 'publish']:
            with self.subTest(point=point), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                real_open, real_publish = os.open, cli._publish_directory
                def swap(name):
                    (root / name).rename(root / 'moved-staging')
                    (root / name).mkdir()
                    (root / name / 'sentinel').write_bytes(b'keep')
                def opening(name, flags, *args, **kwargs):
                    if point == 'open' and str(name).startswith('.bagkeep-'): swap(name)
                    return real_open(name, flags, *args, **kwargs)
                def publish(parent_fd, name, stage_fd, output):
                    if point == 'publish': swap(name)
                    return real_publish(parent_fd, name, stage_fd, output)
                before = set(os.listdir('/proc/self/fd'))
                with patch.object(os, 'open', opening), patch.object(cli, '_publish_directory', publish):
                    with self.assertRaisesRegex(cli.InputError, 'staging directory changed'):
                        cli.write_bundle(root / 'bundle', {'index.html': b'complete'})
                self.assertEqual(set(os.listdir('/proc/self/fd')), before)
                self.assertFalse((root / 'bundle').exists())
                self.assertEqual(self.bundle(next(root.glob('.bagkeep-*'))), {'sentinel': b'keep'})
                self.assertEqual(self.bundle(root / 'moved-staging'), {} if point == 'open' else {'index.html': b'complete'})

    def test_exception_immediately_after_rename_preserves_complete_bundle(self):
        for exception in [KeyboardInterrupt, OSError]:
            with self.subTest(exception=exception), self.fixture('rescue') as (root, source):
                code, _, err = self.invoke(source, root / 'baseline')
                self.assertEqual(code, 0, err)
                expected = self.bundle(root / 'baseline')
                real_publish = cli._publish_directory
                def publish(*args):
                    real_publish(*args)
                    raise exception('synthetic after publication')
                before = set(os.listdir('/proc/self/fd'))
                with patch.object(cli, '_publish_directory', publish):
                    code, out, err = self.invoke(source, root / 'bundle')
                self.assertEqual((code, out), (130 if exception is KeyboardInterrupt else 2, ''))
                self.assertEqual(self.bundle(root / 'bundle'), expected)
                self.assertFalse(list(root.glob('.bagkeep-*')))
                self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_bounded_process_kill_before_after_atomic_publication(self):
        child_code = '''
import os, sys
import notewrench.archive.cli as cli
real_publish = cli._publish_directory
def publish(*args):
    if sys.argv[1] == 'after': real_publish(*args)
    print('ready', flush=True)
    os.read(0, 1)
    raise AssertionError('parent must kill child')
cli._publish_directory = publish
raise SystemExit(cli.main(sys.argv[2:]))
'''
        for command, kind in inputs.CASES:
            for mode in [(), ('--strict',)]:
                for point in ['before', 'after']:
                    with self.subTest(command=command, kind=kind, mode=mode, point=point), self.fixture(command, kind) as (root, source):
                        code, _, err = self.invoke_command(command, source, root / 'baseline', mode)
                        self.assertEqual(code, 0, err)
                        expected = self.bundle(root / 'baseline')
                        output = root / 'bundle'
                        child = subprocess.Popen([sys.executable, '-c', child_code, point, command,
                                                  str(source), '-o', str(output), *mode],
                                                 cwd=inputs.ROOT, stdin=subprocess.PIPE,
                                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                        try:
                            self.assertTrue(select.select([child.stdout], [], [], 60)[0], 'publication boundary timeout')
                            self.assertEqual(child.stdout.readline(), b'ready\n')
                            if point == 'before':
                                self.assertFalse(os.path.lexists(output))
                                self.assertEqual(self.bundle(next(root.glob('.bagkeep-*'))), expected)
                            child.kill()
                            out, err = child.communicate(timeout=60)
                            self.assertEqual(child.returncode, -signal.SIGKILL)
                            self.assertEqual((out, err), (b'', b''))
                            if point == 'after':
                                self.assertEqual(self.bundle(output), expected)
                                self.assertFalse(list(root.glob('.bagkeep-*')))
                            else: self.assertFalse(os.path.lexists(output))
                        finally:
                            if child.poll() is None: child.kill()
                            child.communicate(timeout=60)
