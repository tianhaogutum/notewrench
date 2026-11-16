# SPDX-License-Identifier: AGPL-3.0-only
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import notewrench
from notewrench.archive.cli import main, write_bundle
import notewrench.archive.cli as cli
from notewrench.archive.core import InputError, MAX_INPUT_BYTES

ROOT = Path(__file__).resolve().parents[2]


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='archive-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'input.json'
        self.source.write_bytes((ROOT / 'examples' / 'archive' / 'wallabag-v2.json').read_bytes())
        self.output = self.root / 'output'

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'notewrench', 'archive', *map(str, args)], cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=60)

    def command(self, *args):
        return self.run_cli('rescue', self.source, '-o', self.output, *args)

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def test_actual_files_manifest_and_input_unchanged(self):
        original = self.source.read_bytes()
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'written')
        manifest = json.loads((self.output / 'manifest.json').read_text())
        self.assertEqual(manifest['input_sha256'], hashlib.sha256(original).hexdigest())
        for name, info in manifest['files'].items():
            actual = (self.output / name).read_bytes()
            self.assertEqual(hashlib.sha256(actual).hexdigest(), info['sha256'])
        self.assertEqual(self.source.read_bytes(), original)
        self.assertTrue((self.output / 'index.html').is_file())

    def test_repeated_outputs_byte_identical(self):
        first = self.command()
        second_output = self.root / 'another'
        second = self.run_cli('rescue', self.source, '-o', second_output)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual({p.name: p.read_bytes() for p in self.output.iterdir()}, {p.name: p.read_bytes() for p in second_output.iterdir()})

    def test_dry_run_zero_writes(self):
        before = self.snapshot()
        result = self.command('--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'planned')
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.output.exists())
        self.assertFalse(any(p.name.startswith('.bagkeep-') for p in self.root.iterdir()))

    def test_strict_rejects_loss_before_writing_even_dry_run(self):
        data = json.loads(self.source.read_text())
        data[0]['content'] = '<p onclick="x">safe</p><script>x</script>'
        self.source.write_text(json.dumps(data))
        for flag in [[], ['--dry-run']]:
            result = self.command('--strict', *flag)
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'strict_rejected')
            self.assertGreater(json.loads(result.stdout)['loss_count'], 0)
            self.assertFalse(self.output.exists())

    def test_strict_normal_rescue_succeeds(self):
        result = self.command('--strict')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.output.is_dir())

    def test_k2w_strict_skips_and_valid_links(self):
        source = ROOT / 'examples' / 'archive' / 'karakeep-native.json'
        result = self.run_cli('k2w', source, '-o', self.output, '--strict')
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertFalse(self.output.exists())
        codes = {i['code'] for i in json.loads(result.stdout)['manifest']['issues']}
        self.assertTrue({'text_without_url', 'null_content'} <= codes)
        source = ROOT / 'examples' / 'archive' / 'karakeep-links.json'
        result = self.run_cli('k2w', source, '-o', self.output, '--strict')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.output / 'wallabag.json').read_text())[0]['url'], 'https://example.org/a')

    def test_rejects_existing_file_directory_and_symlink(self):
        for kind in ['file', 'empty-dir', 'nonempty-dir', 'symlink', 'dangling-symlink']:
            with self.subTest(kind=kind):
                if kind == 'file':
                    self.output.write_text('do not overwrite')
                elif kind in ('empty-dir', 'nonempty-dir'):
                    self.output.mkdir()
                    if kind == 'nonempty-dir':
                        (self.output / 'user-file').write_text('do not overwrite')
                else:
                    self.output.symlink_to(self.source if kind == 'symlink' else self.root / 'absent')
                before = self.snapshot()
                result = self.command()
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('already exists', result.stderr)
                self.assertEqual(self.snapshot(), before)
                if self.output.is_symlink() or self.output.is_file():
                    self.output.unlink()
                else:
                    for p in self.output.iterdir():
                        p.unlink()
                    self.output.rmdir()

    def test_bad_json_missing_file_and_missing_parent(self):
        for raw in ['{', '{}', '[{"url":"https://example.org","title":"x","created_at":"bad"}]']:
            self.source.write_text(raw)
            result = self.command()
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertNotIn('Traceback', result.stderr)
            self.assertFalse(self.output.exists())
        self.source.unlink()
        self.assertEqual(self.command().returncode, 2)
        self.source.write_bytes((ROOT / 'examples' / 'archive' / 'wallabag-v2.json').read_bytes())
        result = self.run_cli('rescue', self.source, '-o', self.root / 'missing' / 'out', '--dry-run')
        self.assertEqual(result.returncode, 2)
        self.assertIn('parent', result.stderr)
        self.assertFalse((self.root / 'missing').exists())

    def test_large_input_rejected_without_output(self):
        # A sparse file exercises the real bounded file read.
        with self.source.open('wb') as handle:
            handle.truncate(MAX_INPUT_BYTES + 1)
        result = self.command()
        self.assertEqual(result.returncode, 2)
        self.assertIn('64 MiB', result.stderr)
        self.assertFalse(self.output.exists())

    def test_no_network_or_script_execution(self):
        sentinel = self.root / 'executed'
        data = json.loads(self.source.read_text())
        data[0]['content'] = f'<script>require("fs").writeFileSync("{sentinel}","x")</script><p>缓存</p>'
        self.source.write_text(json.dumps(data))
        with patch('socket.socket', side_effect=AssertionError('network forbidden')), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['rescue', str(self.source), '-o', str(self.output)]), 0)
        self.assertFalse(sentinel.exists())
        article = next(self.output.glob('article-*.html')).read_text()
        self.assertNotIn('writeFileSync', article)

    def test_publication_failure_rolls_back_own_directory(self):
        with patch('notewrench.archive.cli._publish_directory', side_effect=OSError('synthetic disk error')):
            with self.assertRaises(OSError):
                write_bundle(self.output, {'index.html': 'test'})
        self.assertFalse(self.output.exists())
        self.assertEqual({p.name for p in self.root.iterdir()}, {'input.json'})

    def test_atomic_publication_race_preserves_existing_output(self):
        original_publish = cli._publish_directory
        output = self.output
        def race_publish(*args, **kwargs):
            output.mkdir()
            (output / 'unrelated').write_text('keep')
            return original_publish(*args, **kwargs)
        with patch('notewrench.archive.cli._publish_directory', race_publish), self.assertRaises(FileExistsError):
            write_bundle(output, {'index.html':'test'})
        self.assertEqual((output / 'unrelated').read_text(), 'keep')
        self.assertEqual({p.name for p in output.iterdir()}, {'unrelated'})

    def test_unknown_cli_command_and_help(self):
        self.assertEqual(self.run_cli('--help').returncode, 0)
        self.assertEqual(self.run_cli('unknown').returncode, 2)
        self.assertEqual(self.run_cli('--version').stdout.strip(), f'notewrench archive {notewrench.__version__}')
