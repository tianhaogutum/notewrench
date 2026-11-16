"""Proposed complete-order previews preserve source evidence without building books."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from notewrench.epub import BundleError
from notewrench.epub.cli import main
from notewrench.epub.core import plan
from notewrench.epub.epub import json_bytes
from notewrench.epub.ordering import MAX_ORDER_BYTES, MAX_ORDER_ENTRIES

ROOT = Path(__file__).resolve().parents[2]
WALLABAG = ROOT / 'examples/epub/wallabag.json'
BAGKEEP = ROOT / 'tests/epub/fixtures/archive-bundle'


class PlanOrderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='epub-plan-order-')
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)

    def write(self, name, value):
        path = self.work / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return path

    def cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'notewrench', 'epub', *map(str, args)],
                              cwd=self.work, env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')},
                              capture_output=True, text=True, timeout=60)

    def invoke(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(['plan', *map(str, args)])
        return code, stdout.getvalue(), stderr.getvalue()

    def assert_reordered(self, before, actual, keys):
        expected = copy.deepcopy(before)
        entries = {e['key']: e for e in before['included']}
        expected.update(included=[entries[k] for k in keys], chapter_order=list(keys))
        if 'bagkeep_archive' in expected:
            for e in expected['bagkeep_archive']['articles']:
                e['plan_index'] = keys.index(e['key']) if e['key'] in keys else None
                self.assertIsNone(e['output_index'])
        self.assertEqual(actual, expected)

    def test_requested_cli_order_preview_without_output_path(self):
        order = self.write('order.json', ['1:102', '1:101'])
        before = {p.name: p.read_bytes() for p in self.work.iterdir()}
        result = self.cli('plan', WALLABAG, '--chapter-order', order)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assert_reordered(plan([WALLABAG]), json.loads(result.stdout), ['1:102', '1:101'])
        self.assertEqual({p.name: p.read_bytes() for p in self.work.iterdir()}, before)

    def test_mixed_native_unicode_markdown_and_selection_evidence(self):
        text = 'read_notes and **saved**.\r\n\r\n[local](#absent)'
        native = self.write('native.json', {'bookmarks': [
            {'content': {'type': 'link', 'url': 'https://example.invalid/missing'}},
            {'title': 'Native 二', 'tags': ['chosen'], 'content': {'type': 'text', 'text': text}}]})
        ids = ['中文:三', 'é', 'e\u0301', ' spaces ']
        api = self.write('api.json', {'bookmarks': [
            {'id': k, 'title': k, 'content': {'type': 'text', 'text': 'safe'}} for k in ids],
            'nextCursor': 'retained'})
        inputs = [WALLABAG, native, api]
        options = dict(skip_missing=True, text_mode='markdown')
        before = plan(inputs, **options)
        keys = list(reversed(before['chapter_order']))
        actual = plan(inputs, chapter_order=tuple(keys), **options)
        self.assert_reordered(before, actual, keys)
        entry = next(e for e in actual['included'] if e['key'] == '2:entry-2')
        self.assertEqual(entry['original_text'], text)
        self.assertEqual(entry['original_text_sha256'], hashlib.sha256(text.encode()).hexdigest())
        self.assertTrue(actual['changes']); self.assertTrue(actual['warnings'])
        for options in (dict(tags=['chosen', '中文'], ids=['1:101', '1:102', '2:entry-2']),
                        dict(ids=['3:é', '3:e\u0301'])):
            before = plan(inputs, **options)
            keys = list(reversed(before['chapter_order']))
            self.assert_reordered(before, plan(inputs, chapter_order=keys, **options), keys)
        self.assertEqual(json_bytes(plan(inputs, **options)), json_bytes(plan(inputs, chapter_order=None, **options)))

    def test_bagkeep_and_resources_change_only_requested_positions(self):
        for options in (dict(bagkeep_bundle=BAGKEEP), dict(bagkeep_bundle=BAGKEEP, ids=['1:2']),
                        dict(image_manifest=ROOT / 'examples/epub/image-manifest.json'),
                        dict(image_manifest=ROOT / 'examples/epub/image-manifest.json', ids=['1:2'])):
            inputs = [] if 'bagkeep_bundle' in options else [ROOT / 'examples/epub/wallabag-images.json']
            before = plan(inputs, **options)
            keys = list(reversed(before['chapter_order']))
            self.assert_reordered(before, plan(inputs, chapter_order=keys, **options), keys)
        order = self.write('order.json', ['1:2', '1:1'])
        result = self.cli('plan', '--archive-bundle', BAGKEEP, '--chapter-order', order)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_reordered(plan([], bagkeep_bundle=BAGKEEP), json.loads(result.stdout), ['1:2', '1:1'])

    def test_rejects_duplicate_unknown_excluded_skipped_incomplete_and_bad_sequences(self):
        native = ROOT / 'examples/epub/karakeep-export.json'
        cases = [([WALLABAG], {}, []), ([WALLABAG], {}, ['1:101']),
                 ([WALLABAG], {}, ['1:101', '1:101']), ([WALLABAG], {}, ['1:102', '1:unknown']),
                 ([WALLABAG], dict(tags=['中文']), ['1:102', '1:101']),
                 ([WALLABAG], dict(ids=['1:102']), ['1:102', '1:101']),
                 ([native], dict(skip_missing=True), ['1:entry-1', '1:entry-2'])]
        for inputs, options, keys in cases:
            with self.subTest(keys=keys, options=options):
                with self.assertRaisesRegex(BundleError, 'chapter_order'):
                    plan(inputs, chapter_order=keys, **options)
                order = self.write('order.json', keys)
                flags = []
                for k, values in options.items():
                    flags.extend(['--skip-missing'] if k == 'skip_missing' else
                                 [x for v in values for x in ['--tag' if k == 'tags' else '--id', v]])
                before = {p.name: p.read_bytes() for p in self.work.iterdir()}
                result = self.cli('plan', *inputs, '--chapter-order', order, *flags)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertIn('chapter_order', result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                self.assertEqual({p.name: p.read_bytes() for p in self.work.iterdir()}, before)
        for value in ('1:101', b'1:101', Path('order.json'), {'1:101'}, {'1:101': 0}, iter(['1:101']),
                      [True], [1], ['01:101'], ['0:101'], ['1:'], ['1:\x00'], ['1:\ud800'],
                      ['1:101'] * (MAX_ORDER_ENTRIES + 1)):
            with self.subTest(value=repr(value)[:80]), self.assertRaises(BundleError):
                plan([WALLABAG], chapter_order=value)

    def test_missing_policy_precedes_sequence_validation_and_preserves_failure_json(self):
        path = self.write('missing.json', [
            {'id': 'missing', 'url': '', 'content': None},
            {'id': 'cleaned', 'url': '', 'content': '<script>gone()</script>'},
            {'id': 'a', 'url': '', 'content': 'A'}, {'id': 'b', 'url': '', 'content': 'B'}])
        for options in ({}, dict(tags=['absent']), dict(ids=['1:missing'], skip_missing=True)):
            before = plan([path], **options)
            for keys in (['1:b', '1:a'], ['1:unknown'], ['1:a', '1:a'], [True],
                         ['1:a'] * (MAX_ORDER_ENTRIES + 1), Path('ignored.json')):
                self.assertEqual(plan([path], chapter_order=keys, **options), before)
        for keys in (['1:b', '1:a'], ['1:unknown'], ['1:a', '1:a'], [True]):
            order = self.write('order.json', keys)
            result = self.cli('plan', path, '--chapter-order', order)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout), plan([path]))
            self.assertIn('Selected entries have no usable body', result.stderr)
        actual = plan([path], chapter_order=['1:b', '1:a'], skip_missing=True)
        self.assert_reordered(plan([path], skip_missing=True), actual, ['1:b', '1:a'])
        with self.assertRaisesRegex(BundleError, 'unknown/excluded/skipped'):
            plan([path], chapter_order=['1:b', '1:a', '1:cleaned'], skip_missing=True)
        archive = self.work / 'archive'
        shutil.copytree(BAGKEEP, archive)
        manifest = json.loads((archive / 'manifest.json').read_text())
        manifest['records'][0]['cached_body'] = False
        (archive / 'manifest.json').write_text(json.dumps(manifest))
        self.assertEqual(plan([], bagkeep_bundle=archive, chapter_order=['1:unknown']),
                         plan([], bagkeep_bundle=archive))
        actual = plan([], bagkeep_bundle=archive, chapter_order=['1:2'], skip_missing=True)
        self.assertEqual([e['plan_index'] for e in actual['bagkeep_archive']['articles']], [None, 0])
        self.assertEqual([e['output_index'] for e in actual['bagkeep_archive']['articles']], [None, None])

    def test_full_input_resource_archive_validation_precedes_order_keys(self):
        bad = self.write('bad.json', [{'id': 3, 'url': '', 'content': {}}])
        with self.assertRaisesRegex(BundleError, 'content must be HTML'):
            plan([WALLABAG, bad], ids=['1:101'], chapter_order=[True])
        manifest = self.write('images.json', {'schema_version': 1, 'images': [
            {'key': '1:102', 'src': '/absent.png', 'path': 'absent.png',
             'media_type': 'image/png', 'sha256': '0' * 64}]})
        with self.assertRaises((BundleError, OSError)) as caught:
            plan([WALLABAG], ids=['1:101'], image_manifest=manifest, chapter_order=[True])
        self.assertNotIn('chapter_order', str(caught.exception))
        archive = self.work / 'archive'; shutil.copytree(BAGKEEP, archive)
        payload = archive / 'wallabag.json'
        raw = payload.read_bytes(); payload.write_bytes(b'X' + raw[1:])
        with self.assertRaisesRegex(BundleError, 'SHA-256'):
            plan([], bagkeep_bundle=archive, ids=['1:2'], chapter_order=[True])

    def test_cli_file_errors_are_fatal_before_selection(self):
        order = self.work / 'order.json'
        # A policy-failing input must not swallow file/encoding/JSON/array errors.
        for raw in (b'\xff', b'\xef\xbb\xbf[]', b'{', b'null', b'{}', b'"1:101"', b'[NaN]',
                    b'[Infinity]', b'[-Infinity]', b'[] trailing', b'[{"x":1,"x":2}]',
                    b' ' * (MAX_ORDER_BYTES + 1)):
            with self.subTest(raw=raw[:40]):
                order.write_bytes(raw)
                before = {p.name: p.read_bytes() for p in self.work.iterdir()}
                result = self.cli('plan', ROOT / 'examples/epub/karakeep-export.json', '--chapter-order', order)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
                self.assertTrue(result.stderr); self.assertNotIn('Traceback', result.stderr)
                self.assertEqual({p.name: p.read_bytes() for p in self.work.iterdir()}, before)
        order.unlink()
        code, stdout, stderr = self.invoke(WALLABAG, '--chapter-order', order)
        self.assertEqual(code, 2); self.assertEqual(stdout, ''); self.assertIn('No such file', stderr)
        order.write_bytes(b'bad JSON')
        with patch('notewrench.epub.core._select', side_effect=AssertionError('must parse file first')):
            code, stdout, stderr = self.invoke(WALLABAG, '--chapter-order', order)
        self.assertEqual(code, 2); self.assertEqual(stdout, ''); self.assertIn('chapter order JSON', stderr)

    def test_actual_cli_reads_order_once_with_bound_and_has_no_epub_or_side_effects(self):
        order = self.work / 'order.json'
        raw = b'["1:102","1:101"]'
        order.write_bytes(raw + b' ' * (MAX_ORDER_BYTES - len(raw)))
        before = order.read_bytes()
        opened, reads = [], []
        original_open = Path.open
        class Reader:
            def __enter__(self):
                self.stream = original_open(order, 'rb'); return self
            def read(self, n):
                reads.append(n); return self.stream.read(n)
            def __exit__(self, *args):
                self.stream.close()
        def tracked_open(path, *args, **kwargs):
            if path == order and args == ('rb',):
                opened.append((args, kwargs)); return Reader()
            return original_open(path, *args, **kwargs)
        with ExitStack() as stack:
            for target in ('notewrench.epub.core.make_epub', 'notewrench.epub.epub.make_epub',
                           'notewrench.epub.core.validate_epub', 'notewrench.epub.cli.validate_epub',
                           'notewrench.epub.cli.prepare', 'notewrench.epub.core.publish_new',
                           'notewrench.epub.cli.publish_new', 'notewrench.epub.cli.check_destinations',
                           'notewrench.epub.core.check_destinations', 'tempfile.NamedTemporaryFile',
                           'tempfile.TemporaryDirectory', 'os.link', 'zipfile.ZipFile',
                           'xml.etree.ElementTree.tostring', 'socket.socket', 'socket.getaddrinfo'):
                stack.enter_context(patch(target, side_effect=AssertionError('forbidden: ' + target)))
            stack.enter_context(patch.object(Path, 'open', tracked_open))
            code, stdout, stderr = self.invoke(WALLABAG, '--chapter-order', order)
            self.assertEqual(code, 0, stderr)
            self.assertEqual(json.loads(stdout)['chapter_order'], ['1:102', '1:101'])
            order.write_text('["1:2","1:1"]')
            code, stdout, stderr = self.invoke('--archive-bundle', BAGKEEP, '--chapter-order', order)
            self.assertEqual(code, 0, stderr)
            self.assertEqual([e['plan_index'] for e in json.loads(stdout)['bagkeep_archive']['articles']], [1, 0])
            order.write_text('["1:entry-1"]')
            code, stdout, stderr = self.invoke(ROOT / 'examples/epub/karakeep-markdown.json', '--text-mode', 'markdown', '--chapter-order', order)
            self.assertEqual(code, 0, stderr)
        self.assertEqual(opened, [(('rb',), {})] * 3)
        self.assertEqual(reads, [MAX_ORDER_BYTES + 1] * 3)
        order.write_bytes(before + b' ')
        result = self.cli('plan', WALLABAG, '--chapter-order', order)
        self.assertEqual(result.returncode, 2); self.assertEqual(result.stdout, '')
        self.assertIn('exceeds 1048576 bytes', result.stderr)

    def test_valid_maximum_entry_order_and_over_bound_rejection(self):
        path = self.write('many.json', [{'id': n, 'url': '', 'content': 'x'} for n in range(MAX_ORDER_ENTRIES)])
        keys = [f'1:{n}' for n in reversed(range(MAX_ORDER_ENTRIES))]
        order = self.write('order.json', keys)
        result = self.cli('plan', path, '--chapter-order', order)
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = json.loads(result.stdout)
        self.assertEqual(actual['chapter_order'], keys)
        self.assertEqual([e['key'] for e in actual['included']], keys)
        self.write('order.json', keys + ['1:extra'])
        result = self.cli('plan', path, '--chapter-order', order)
        self.assertEqual(result.returncode, 2); self.assertEqual(result.stdout, '')
        self.assertIn('exceeds 10000 entries', result.stderr)


if __name__ == '__main__':
    unittest.main()
