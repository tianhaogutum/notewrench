# SPDX-License-Identifier: AGPL-3.0-only
"""Published bundle verification, independent of the original inputs."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from notewrench.archive.cli import main
from notewrench.archive.core import build


class VerifyTests(unittest.TestCase):
    def invoke(self, bundle):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = main(['verify', str(bundle)])
            except SystemExit as exc:
                code = exc.code
        self.assertEqual(err.getvalue(), '')
        return code, json.loads(out.getvalue())

    def bundle(self, root):
        directory = root / 'bundle'
        directory.mkdir()
        files, _ = build('rescue', b'[{"url":"https://example.org","title":"fixture","content":"<p>saved body</p>"}]')
        for name, data in files.items():
            (directory / name).write_bytes(data if isinstance(data, bytes) else data.encode())
        return directory

    def test_intact_then_changed_without_original_input(self):
        with tempfile.TemporaryDirectory() as td:
            bundle = self.bundle(Path(td))
            code, result = self.invoke(bundle)
            self.assertEqual((code, result['status']), (0, 'valid'))
            self.assertFalse(result['manifest_self_hashed'])
            self.assertFalse(result['hashes_prove_authenticity'])
            (bundle / 'index.html').write_bytes((bundle / 'index.html').read_bytes().replace(b'archive', b'ARCHIVE'))
            code, result = self.invoke(bundle)
            self.assertEqual((code, result['status']), (2, 'invalid'))
            row = next(r for r in result['files'] if r['file'] == 'index.html')
            self.assertEqual(row['issues'], ['hash_mismatch'])

    def write_manifest(self, bundle, manifest):
        (bundle / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    def snapshot(self, bundle):
        return {p.name: (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns)
                for p in bundle.iterdir() if p.is_file() and not p.is_symlink()}

    def test_real_producers_deterministic_read_only_without_sources(self):
        import os
        import shutil
        import socket
        from unittest.mock import patch
        root = Path(__file__).resolve().parents[2]
        cases = [('rescue', 'examples/wallabag-v2.json', []),
                 ('k2w', 'examples/karakeep-native.json', []),
                 ('api-import', 'examples/api/wallabag-manifest.json', []),
                 ('api-import', 'examples/content/pinned-api-manifest.json',
                  ['--content-assets', 'examples/content/content-manifest.json', '--assets', 'examples/images/manifest.json', '--require-page-hashes']),
                 ('rescue', 'examples/progressive/wallabag.json', ['--assets', 'examples/progressive/manifest.json'])]
        for command, source, options in cases:
            with self.subTest(source=source), tempfile.TemporaryDirectory() as td:
                temporary = Path(td)
                shutil.copytree(root / 'examples' / 'archive', temporary / 'examples')
                options = [str(temporary / v) if v.startswith('examples/') else v for v in options]
                bundle = temporary / 'bundle'
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main([command, str(temporary / source), '-o', str(bundle), *options]), 0)
                shutil.rmtree(temporary / 'examples')
                before = self.snapshot(bundle)
                real_open = os.open
                def opening(path, flags, *args, **kwargs):
                    self.assertFalse(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
                    return real_open(path, flags, *args, **kwargs)
                fds = set(os.listdir('/proc/self/fd'))
                with patch.object(os, 'open', opening), patch.object(os, 'write', side_effect=AssertionError('write')), \
                     patch.object(os, 'mkdir', side_effect=AssertionError('mkdir')), \
                     patch.object(os, 'unlink', side_effect=AssertionError('unlink')), \
                     patch.object(socket, 'socket', side_effect=AssertionError('network')):
                    code, report = self.invoke(bundle)
                    self.assertEqual((code, report['status']), (0, 'valid'))
                    self.assertEqual(self.invoke(bundle), (code, report))
                self.assertEqual(set(os.listdir('/proc/self/fd')), fds)
                self.assertEqual(self.snapshot(bundle), before)
                self.assertEqual([r['file'] for r in report['files']], sorted(before.keys() - {'manifest.json'}))
                self.assertTrue(all(r['actual_bytes'] == r['expected_bytes'] and r['actual_sha256'] == r['expected_sha256'] for r in report['files']))

    def test_wrong_sizes_hash_missing_unexpected(self):
        import os
        from unittest.mock import patch
        for mutation in ['truncated', 'grown', 'size', 'hash', 'missing', 'extra', 'empty']:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as td:
                bundle = self.bundle(Path(td))
                manifest = json.loads((bundle / 'manifest.json').read_bytes())
                payload = bundle / 'index.html'
                expected = ['size_mismatch', 'hash_mismatch']
                if mutation == 'truncated': payload.write_bytes(payload.read_bytes()[:-1])
                elif mutation == 'grown': payload.write_bytes(payload.read_bytes() + b'!')
                elif mutation == 'empty': payload.write_bytes(b'')
                elif mutation == 'size':
                    manifest['files'][payload.name]['bytes'] += 1
                    expected = ['size_mismatch']
                elif mutation == 'hash':
                    manifest['files'][payload.name]['sha256'] = '0' * 64
                    expected = ['hash_mismatch']
                elif mutation == 'missing':
                    payload.unlink()
                    expected = ['missing']
                else:
                    payload = bundle / 'extra'
                    os.mkfifo(payload)
                    expected = ['unexpected']
                self.write_manifest(bundle, manifest)
                before = self.snapshot(bundle)
                with patch.object(os, 'write', side_effect=AssertionError('write')):
                    code, report = self.invoke(bundle)
                self.assertEqual((code, report['status']), (2, 'invalid'))
                row = next(r for r in report['files'] if r['file'] == payload.name)
                self.assertEqual(row['issues'], expected)
                self.assertEqual(self.snapshot(bundle), before)
                self.assertEqual(self.invoke(bundle), (code, report))

    def test_malformed_manifest_preflight_never_opens_payload(self):
        import os
        from unittest.mock import patch
        cases = [b'{', b'\xff', b'{"files":{},"files":{}}', b'NaN', b'"\\ud800"', b'[' * 140 + b']' * 140]
        for raw in cases:
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as td:
                bundle = self.bundle(Path(td))
                (bundle / 'manifest.json').write_bytes(raw)
                code, report = self.invoke(bundle)
                self.assertEqual((code, report['issues'][0]['code'], report['files']), (2, 'manifest_json', []))
        declarations = [None, [], {}, {'index.html': None}, {'index.html': {'bytes': True, 'sha256': '0'*64}},
                        {'index.html': {'bytes': -1, 'sha256': '0'*64}},
                        {'index.html': {'bytes': 0.5, 'sha256': '0'*64}},
                        {'index.html': {'bytes': 1, 'sha256': 'A'*64}},
                        {'index.html': {'bytes': 1, 'sha256': '0'*63}},
                        {'index.html': {'bytes': 1, 'sha256': '0'*64, 'extra': 1}},
                        {'index.html': {'sha256': '0'*64}}]
        for name in ['', '.', '..', '../outside', '/absolute', 'sub/file', 'sub\\file', 'x\0z', 'x\nz', 'manifest.json', 'x'*256]:
            declarations.append({name: {'bytes': 0, 'sha256': '0'*64}})
        for files in declarations:
            with self.subTest(files=files), tempfile.TemporaryDirectory() as td:
                bundle = self.bundle(Path(td))
                manifest = json.loads((bundle / 'manifest.json').read_bytes())
                manifest['files'] = files
                self.write_manifest(bundle, manifest)
                real_open = os.open
                def opening(path, *args, **kwargs):
                    self.assertIn(str(path), [str(bundle), 'manifest.json'])
                    return real_open(path, *args, **kwargs)
                with patch.object(os, 'open', opening):
                    code, report = self.invoke(bundle)
                self.assertEqual((code, report['files']), (2, []))
                self.assertTrue(report['issues'])
        for key, value in [('schema_version', True), ('schema_version', 2), ('tool', 'other'), ('mode', 'other')]:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as td:
                bundle = self.bundle(Path(td))
                manifest = json.loads((bundle / 'manifest.json').read_bytes())
                manifest[key] = value
                self.write_manifest(bundle, manifest)
                code, report = self.invoke(bundle)
                self.assertEqual((code, report['issues'][0]['code']), (2, 'manifest_schema'))

    def test_symlink_directory_fifo_manifest_and_payload_do_not_hang(self):
        import os
        import subprocess
        import sys
        for name in ['manifest.json', 'index.html']:
            for kind in ['symlink', 'dangling', 'directory', 'fifo']:
                with self.subTest(name=name, kind=kind), tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    bundle = self.bundle(root)
                    outside = root / 'outside'
                    original_bytes = (bundle / name).read_bytes()
                    outside.write_bytes(original_bytes)
                    path = bundle / name
                    path.unlink()
                    if kind in ['symlink', 'dangling']: path.symlink_to(outside if kind == 'symlink' else root / 'absent')
                    elif kind == 'directory': path.mkdir()
                    else: os.mkfifo(path)
                    result = subprocess.run([sys.executable, '-m', 'notewrench', 'archive', 'verify', str(bundle)],
                                            capture_output=True, text=True, timeout=60)
                    self.assertEqual((result.returncode, result.stderr), (2, ''))
                    report = json.loads(result.stdout)
                    issues = report['issues'] if name == 'manifest.json' else next(r for r in report['files'] if r['file'] == name)['issues']
                    self.assertIn('not_regular', str(issues))
                    self.assertEqual(outside.read_bytes(), original_bytes)

    def test_root_leaf_symlink_missing_file_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = self.bundle(root)
            link = root / 'link'
            link.symlink_to(bundle, target_is_directory=True)
            for path in [link, root / 'missing', bundle / 'index.html']:
                code, report = self.invoke(path)
                self.assertEqual((code, report['status']), (2, 'invalid'))
                self.assertEqual(report['issues'][0]['phase'], 'bundle')

    def test_held_root_survives_rename_and_replacement(self):
        import os
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundle = self.bundle(root)
            expected = self.invoke(bundle)
            real_open = os.open
            def opening(path, flags, *args, **kwargs):
                fd = real_open(path, flags, *args, **kwargs)
                if str(path) == str(bundle):
                    bundle.rename(root / 'held')
                    bundle.mkdir()
                    (bundle / 'manifest.json').write_bytes(b'{')
                return fd
            fds = set(os.listdir('/proc/self/fd'))
            with patch.object(os, 'open', opening):
                self.assertEqual(self.invoke(bundle), expected)
            self.assertEqual(set(os.listdir('/proc/self/fd')), fds)
            self.assertEqual((bundle / 'manifest.json').read_bytes(), b'{')

    def test_open_time_substitutions_and_descriptor_cleanup(self):
        import os
        from unittest.mock import patch
        for name in ['manifest.json', 'index.html']:
            for kind in ['symlink', 'fifo', 'directory', 'regular']:
                with self.subTest(name=name, kind=kind), tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    bundle = self.bundle(root)
                    real_open = os.open
                    def opening(path, flags, *args, **kwargs):
                        if str(path) == name:
                            self.assertTrue(flags & os.O_NOFOLLOW)
                            self.assertTrue(flags & os.O_NONBLOCK)
                            (bundle / name).rename(root / 'original')
                            if kind == 'symlink': (bundle / name).symlink_to(root / 'original')
                            elif kind == 'fifo': os.mkfifo(bundle / name)
                            elif kind == 'directory': (bundle / name).mkdir()
                            else: (bundle / name).write_bytes((root / 'original').read_bytes())
                        return real_open(path, flags, *args, **kwargs)
                    fds = set(os.listdir('/proc/self/fd'))
                    with patch.object(os, 'open', opening):
                        code, report = self.invoke(bundle)
                    self.assertEqual((code, report['status']), (2, 'invalid'))
                    self.assertEqual(set(os.listdir('/proc/self/fd')), fds)
                    if kind == 'regular': self.assertIn('file_changed', str(report))

    def test_io_errors_and_interrupt_close_every_descriptor(self):
        import errno
        import os
        from unittest.mock import patch
        import notewrench.archive.verify as verify
        for point in ['root', 'manifest-open', 'payload-open', 'manifest-read', 'payload-read', 'inventory', 'fstat', 'interrupt']:
            with self.subTest(point=point), tempfile.TemporaryDirectory() as td:
                bundle = self.bundle(Path(td))
                real_open, real_read, real_fstat = os.open, os.read, os.fstat
                opened = {}
                def opening(path, flags, *args, **kwargs):
                    if ((point == 'root' and flags & os.O_DIRECTORY) or
                        (point == 'manifest-open' and str(path) == 'manifest.json') or
                        (point == 'payload-open' and str(path) == 'index.html')):
                        raise OSError(errno.EACCES, 'synthetic')
                    fd = real_open(path, flags, *args, **kwargs)
                    opened[fd] = str(path)
                    return fd
                def reading(fd, count):
                    if point == 'interrupt': raise KeyboardInterrupt
                    if ((point == 'manifest-read' and opened[fd] == 'manifest.json') or
                        (point == 'payload-read' and opened[fd] == 'index.html')):
                        raise OSError(errno.EIO, 'synthetic')
                    return real_read(fd, count)
                fds = set(os.listdir('/proc/self/fd'))
                with patch.object(os, 'open', opening), patch.object(os, 'read', reading), \
                     patch.object(os, 'fstat', side_effect=OSError(errno.EIO, 'synthetic') if point == 'fstat' else real_fstat), \
                     patch.object(verify, '_inventory', side_effect=OSError(errno.EIO, 'synthetic') if point == 'inventory' else verify._inventory):
                    if point == 'interrupt':
                        with self.assertRaises(KeyboardInterrupt): verify.verify_bundle(bundle)
                    else:
                        code, report = self.invoke(bundle)
                        self.assertEqual((code, report['status']), (2, 'invalid'))
                self.assertEqual(set(os.listdir('/proc/self/fd')), fds)

    def test_bounds_declared_observed_count_and_stream_growth(self):
        import hashlib
        import os
        from unittest.mock import patch
        import notewrench.archive.verify as verify
        for case in ['manifest', 'file', 'declared-file', 'declared-total', 'observed-total', 'declarations', 'entries', 'growth']:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as td:
                bundle = Path(td) / 'bundle'
                bundle.mkdir()
                files = {'a': b'abc', 'b': b'abc'}
                for name, data in files.items(): (bundle / name).write_bytes(data)
                manifest = {'schema_version': 1, 'tool': 'bagkeep', 'mode': 'rescue',
                            'files': {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()} for name, data in files.items()}}
                limits = {}
                if case == 'manifest': limits['MAX_MANIFEST_BYTES'] = 8
                elif case in ['file', 'declared-file', 'growth']:
                    limits['MAX_FILE_BYTES'] = 4
                    if case == 'file': (bundle / 'a').write_bytes(b'12345')
                    elif case == 'declared-file': manifest['files']['a']['bytes'] = 5
                elif case in ['declared-total', 'observed-total']:
                    limits['MAX_TOTAL_BYTES'] = 5
                    if case == 'observed-total': manifest['files']['b']['bytes'] = 2
                elif case == 'declarations': limits['MAX_FILES'] = 1
                else:
                    limits['MAX_FILES'] = 2
                    (bundle / 'extra').write_bytes(b'')
                self.write_manifest(bundle, manifest)
                real_read = os.read
                grew = []
                total_read = []
                def reading(fd, count):
                    if case == 'growth' and os.readlink('/proc/self/fd/' + str(fd)) == str(bundle / 'a'):
                        if not grew:
                            (bundle / 'a').write_bytes(b'123456789')
                            grew.append(True)
                    data = real_read(fd, count)
                    total_read.append(len(data))
                    return data
                fds = set(os.listdir('/proc/self/fd'))
                with contextlib.ExitStack() as stack:
                    for key, value in limits.items(): stack.enter_context(patch.object(verify, key, value))
                    stack.enter_context(patch.object(os, 'read', reading))
                    code, report = self.invoke(bundle)
                self.assertEqual((code, report['status']), (2, 'invalid'))
                expected = {'manifest': 'manifest_size_limit', 'file': 'file_size_limit', 'declared-file': 'file_size_limit',
                            'declared-total': 'aggregate_size_limit', 'observed-total': 'aggregate_size_limit',
                            'declarations': 'file_count_limit', 'entries': 'entry_count_limit', 'growth': 'file_size_limit'}[case]
                self.assertIn(expected, str(report))
                if case == 'growth': self.assertLessEqual(sum(total_read), len((bundle / 'manifest.json').read_bytes()) + 8)
                self.assertEqual(set(os.listdir('/proc/self/fd')), fds)

    def test_zero_byte_payload_bom_and_undecodable_unexpected_name(self):
        import hashlib
        import os
        with tempfile.TemporaryDirectory() as td:
            bundle = self.bundle(Path(td))
            manifest = json.loads((bundle / 'manifest.json').read_bytes())
            (bundle / 'empty').write_bytes(b'')
            manifest['files']['empty'] = {'bytes': 0, 'sha256': hashlib.sha256(b'').hexdigest()}
            self.write_manifest(bundle, manifest)
            (bundle / 'manifest.json').write_bytes(b'\xef\xbb\xbf' + (bundle / 'manifest.json').read_bytes())
            self.assertEqual(self.invoke(bundle)[0], 0)
            fd = os.open(os.fsencode(bundle) + b'/\xff', os.O_WRONLY | os.O_CREAT, 0o600)
            os.close(fd)
            code, report = self.invoke(bundle)
            self.assertEqual(code, 2)
            self.assertIn({'file_bytes_hex': 'ff', 'status': 'invalid', 'issues': ['unsafe_name', 'unexpected']}, report['files'])

    def test_missing_manifest_and_unsupported_platform(self):
        import os
        from unittest.mock import patch
        import notewrench.archive.verify as verify
        with tempfile.TemporaryDirectory() as td:
            bundle = self.bundle(Path(td))
            (bundle / 'manifest.json').unlink()
            fds = set(os.listdir('/proc/self/fd'))
            code, report = self.invoke(bundle)
            self.assertEqual((code, report['issues'][0]['code'], report['issues'][0]['phase']), (2, 'missing', 'manifest'))
            self.assertEqual(set(os.listdir('/proc/self/fd')), fds)
            with patch.object(verify.sys, 'platform', 'other'), patch.object(os, 'open', side_effect=AssertionError('unsupported')):
                code, report = self.invoke(bundle)
            self.assertEqual((code, report['issues']), (2, [{'code': 'unsupported_platform'}]))
