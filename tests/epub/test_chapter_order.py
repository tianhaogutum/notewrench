"""Explicit book order must preserve source identity and all keyed evidence."""
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
import zipfile

from notewrench.epub import BundleError
from notewrench.epub.core import prepare
from notewrench.epub.epub import OPF, XHTML, q, validate_epub

ROOT = Path(__file__).resolve().parents[2]
WALLABAG = ROOT / 'examples/epub/wallabag.json'
FIXTURE = ROOT / 'tests/epub/fixtures/archive-bundle'


class ChapterOrderTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='epub-order-')
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        self.output = self.work / 'out.epub'
        self.report_path = self.work / 'out.report.json'
        self.order_path = self.work / 'order.json'

    def write(self, name, data):
        path = self.work / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return path

    def cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'notewrench', 'epub', *map(str, args)],
                              cwd=ROOT, capture_output=True, text=True, timeout=60)

    def assert_book(self, payload, report, keys):
        self.assertEqual([e['key'] for e in report['included']], keys)
        self.assertEqual(validate_epub(io.BytesIO(payload))['chapters'], len(keys))
        with zipfile.ZipFile(io.BytesIO(payload)) as book:
            opf = ET.fromstring(book.read('EPUB/package.opf'))
            items = {i.get('id'): i.get('href') for i in opf.find(q(OPF, 'manifest'))}
            hrefs = [items[i.get('idref')] for i in opf.find(q(OPF, 'spine'))]
            self.assertEqual(hrefs, [f'chapter-{n:04d}.xhtml' for n in range(1, len(keys) + 1)])
            self.assertEqual([n for n in book.namelist() if n.startswith('EPUB/chapter-')],
                             ['EPUB/' + h for h in hrefs])
            nav = ET.fromstring(book.read('EPUB/nav.xhtml'))
            links = list(nav.iter(q(XHTML, 'a')))
            self.assertEqual([a.get('href') for a in links], hrefs)
            for key, entry, link, href in zip(keys, report['included'], links, hrefs):
                chapter = ET.fromstring(book.read('EPUB/' + href))
                self.assertEqual(chapter.findtext('.//' + q(XHTML, 'h1')), entry['title'])
                self.assertEqual(link.text, entry['title'])
                self.assertIn(f'Source ({key}):', ''.join(chapter.itertext()))

    def test_requested_mixed_native_permutation(self):
        text = 'read_notes and **saved**.\r\n\r\n[local](#absent)'
        native = self.write('native.json', {'bookmarks': [
            {'title': 'Missing', 'content': {'type': 'link', 'url': 'https://example.invalid/missing'}},
            {'title': 'Native 二', 'content': {'type': 'text', 'text': text}},
            {'title': 'Native 三', 'content': {'type': 'text', 'text': 'Third'}}]})
        inputs = [WALLABAG, native, ROOT / 'examples/epub/karakeep-api.json']
        keys = ['2:entry-3', '1:101', '2:entry-2', '1:102', '3:api-synthetic-1']
        options = dict(skip_missing=True, text_mode='markdown')
        baseline, before = prepare(inputs, **options)
        with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), \
             patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS forbidden')):
            payload, report = prepare(inputs, chapter_order=keys, **options)
        self.assert_book(payload, report, keys)
        self.assertEqual(report['chapter_order'], keys)
        self.assertEqual(report['inputs'], before['inputs'])
        self.assertEqual(report['skipped'][0]['key'], '2:entry-1')
        for field in ('excluded', 'skipped', 'changes', 'warnings'):
            self.assertEqual(report[field], before[field])
        self.assertEqual({e['key']: e for e in report['included']}, {e['key']: e for e in before['included']})
        entry = report['included'][2]
        self.assertEqual(entry['original_text'], text)
        self.assertEqual(entry['original_text_sha256'], hashlib.sha256(text.encode()).hexdigest())
        self.assertNotEqual(payload, baseline)
        self.assertEqual((payload, report), prepare(inputs, chapter_order=tuple(keys), **options))

    def test_omission_and_identity_order_preserve_original_bytes(self):
        baseline, before = prepare([WALLABAG])
        self.assertEqual((baseline, before), prepare([WALLABAG], chapter_order=None))
        self.assertNotIn('chapter_order', before)
        ordered, after = prepare([WALLABAG], chapter_order=['1:101', '1:102'])
        self.assertEqual(ordered, baseline)
        after.pop('chapter_order')
        self.assertEqual(after, before)
        self.assertEqual(hashlib.sha256(baseline).hexdigest(),
                         '5918677e994cb19c1eeffd3f3518578be5bf0c8db9a10c51d6d10c9c3dc3498b')

    def test_selection_keys_are_not_renumbered(self):
        for options, keys in (({'tags': ['中文']}, ['1:101']),
                              ({'ids': ['1:102']}, ['1:102']),
                              ({'tags': ['weekend'], 'ids': ['1:102']}, ['1:102'])):
            with self.subTest(options=options):
                payload, report = prepare([WALLABAG], chapter_order=keys, **options)
                self.assert_book(payload, report, keys)
                for bad in (['1:101', '1:102'], ['1:unknown']):
                    with self.assertRaisesRegex(BundleError, 'chapter_order'):
                        prepare([WALLABAG], chapter_order=bad, **options)

    def test_unicode_keys_exact_without_normalization(self):
        ids = ['中文:三', 'é', 'e\u0301', ' spaces ']
        path = self.write('unicode.json', [{'id': key, 'url': '', 'title': key, 'content': '<p>x</p>'} for key in ids])
        keys = ['1:' + key for key in reversed(ids)]
        payload, report = prepare([path], chapter_order=keys)
        self.assert_book(payload, report, keys)
        self.write('order.json', keys)
        result = self.cli('build', path, '--chapter-order', self.order_path, '-o', self.output, '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['chapter_order'], keys)
        self.assertFalse(self.output.exists())

    def test_api_requires_sequence_and_exact_qualified_keys(self):
        for order in ('1:101', self.order_path, {'1:101': 1}, {'1:101'}, iter(['1:101']),
                      b'1:101', [None], [True], [1], [''], ['101'], ['0:101'], ['01:101'],
                      ['+1:101'], ['１:101'], ['1:'], ['1:\x00'], ['1:\ud800'], [['1:101']],
                      ['1:101', '1:101'], [], ['1:101'], ['1:101', '1:102', '1:extra']):
            with self.subTest(order=repr(order)):
                with self.assertRaises(BundleError):
                    prepare([WALLABAG], chapter_order=order)

    def test_missing_policy_precedes_permutation_checks(self):
        native = ROOT / 'examples/epub/karakeep-export.json'
        for order in ([], ['1:entry-2'], ['1:unknown'], ['1:entry-1', '1:entry-1']):
            with self.subTest(order=order):
                payload, report = prepare([native], chapter_order=order)
                self.assertIsNone(payload)
                self.assertEqual(report['included'], [])
                self.assertIn('Selected entries have no usable body', report['errors'][0])
                self.assertEqual(report['not_built'][0]['key'], '1:entry-1')
                with self.assertRaises(BundleError):
                    prepare([native], chapter_order=order, skip_missing=True)
        payload, report = prepare([native], chapter_order=['1:entry-1'], skip_missing=True)
        self.assert_book(payload, report, ['1:entry-1'])
        for options in ({'tags': ['absent']}, {'ids': ['1:entry-2'], 'skip_missing': True}):
            payload, report = prepare([native], chapter_order=['1:unknown'], **options)
            self.assertIsNone(payload)
            self.assertIn('No usable chapters selected.', report['errors'])

    def test_empty_after_cleaning_cannot_be_scheduled_with_skip_missing(self):
        path = self.write('cleaned-empty.json', [
            {'id': 1, 'url': '', 'content': '<script>removed()</script>'},
            {'id': 2, 'url': '', 'content': '<p>Usable</p>'}])
        payload, report = prepare([path], chapter_order=['1:2', '1:1'])
        self.assertIsNone(payload)
        self.assertEqual(report['skipped'][0]['reason'], 'empty_after_cleaning')
        with self.assertRaisesRegex(BundleError, 'unknown/excluded/skipped'):
            prepare([path], chapter_order=['1:2', '1:1'], skip_missing=True)
        payload, report = prepare([path], chapter_order=['1:2'], skip_missing=True)
        self.assert_book(payload, report, ['1:2'])
        self.assertEqual(report['skipped'][0]['key'], '1:1')

    def test_full_input_resource_validation_precedes_order(self):
        invalid = self.write('bad.json', [{'id': 3, 'url': '', 'content': {'invalid': 'body'}}])
        with self.assertRaisesRegex(BundleError, 'content must be HTML'):
            prepare([WALLABAG, invalid], ids=['1:101'], chapter_order=['1:unknown'])
        manifest = self.write('images.json', {'schema_version': 1, 'images': [
            {'key': '1:102', 'src': '/missing.png', 'path': 'absent.png',
             'media_type': 'image/png', 'sha256': '0' * 64}]})
        with self.assertRaises((BundleError, OSError)) as caught:
            prepare([WALLABAG], ids=['1:101'], image_manifest=manifest, chapter_order=[])
        self.assertNotIn('chapter_order', str(caught.exception))

    def test_bagkeep_order_preserves_archive_indices_hashes_and_dedup(self):
        baseline, before = prepare([], bagkeep_bundle=FIXTURE)
        keys = ['1:2', '1:1']
        payload, report = prepare([], bagkeep_bundle=FIXTURE, chapter_order=keys)
        self.assert_book(payload, report, keys)
        self.assertNotEqual(payload, baseline)
        expected = copy.deepcopy(before['bagkeep_archive'])
        expected['articles'][0]['output_index'] = 1
        expected['articles'][1]['output_index'] = 0
        self.assertEqual(report['bagkeep_archive'], expected)
        self.assertEqual(report['resources'], before['resources'])
        self.assertEqual(report['changes'], before['changes'])
        with zipfile.ZipFile(io.BytesIO(payload)) as book:
            images = [n for n in book.namelist() if n.endswith('.png')]
            self.assertEqual(len(images), 1)
            self.assertEqual(hashlib.sha256(book.read(images[0])).hexdigest(), report['resources'][0]['sha256'])
            for i in (1, 2):
                chapter = ET.fromstring(book.read(f'EPUB/chapter-{i:04d}.xhtml'))
                self.assertEqual('EPUB/' + chapter.find('.//' + q(XHTML, 'img')).get('src'), images[0])
        selected, r = prepare([], bagkeep_bundle=FIXTURE, ids=['1:2'], chapter_order=['1:2'])
        self.assert_book(selected, r, ['1:2'])
        self.assertEqual([e['output_index'] for e in r['bagkeep_archive']['articles']], [None, 0])
        bundle = self.work / 'archive'
        shutil.copytree(FIXTURE, bundle)
        manifest = json.loads((bundle / 'manifest.json').read_text())
        manifest['records'][0]['cached_body'] = False
        (bundle / 'manifest.json').write_text(json.dumps(manifest))
        missing, r = prepare([], bagkeep_bundle=bundle, chapter_order=keys)
        self.assertIsNone(missing)
        self.assertEqual([e['output_index'] for e in r['bagkeep_archive']['articles']], [None, None])
        payload, r = prepare([], bagkeep_bundle=bundle, chapter_order=['1:2'], skip_missing=True)
        self.assert_book(payload, r, ['1:2'])
        self.assertEqual([e['output_index'] for e in r['bagkeep_archive']['articles']], [None, 0])

    def test_local_fragments_relative_links_and_keyed_events_follow_bodies(self):
        path = self.write('links.json', [{'id': n, 'title': str(n), 'url': f'https://example.invalid/{n}/base',
            'content': f'<h2 id="here">{n}</h2><a href="#here">jump</a><a href="#absent">lost</a><a href="next">next</a>'} for n in (1, 2)])
        payload, report = prepare([path], chapter_order=['1:2', '1:1'])
        self.assert_book(payload, report, ['1:2', '1:1'])
        self.assertEqual([e['key'] for e in report['changes'] if e['code'] == 'removed_broken_fragment'], ['1:1', '1:2'])
        with zipfile.ZipFile(io.BytesIO(payload)) as book:
            for index, n in enumerate((2, 1), 1):
                chapter = book.read(f'EPUB/chapter-{index:04d}.xhtml').decode()
                self.assertIn('href="#here"', chapter)
                self.assertIn(f'href="https://example.invalid/{n}/next"', chapter)
                self.assertNotIn('href="#absent"', chapter)

    def test_cli_order_success_dry_run_and_failures_publish_nothing(self):
        self.write('order.json', ['1:102', '1:101'])
        args = ['build', WALLABAG, '--chapter-order', self.order_path, '-o', self.output]
        result = self.cli(*args, '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        dry = json.loads(result.stdout)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.report_path.exists())
        result = self.cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(self.report_path.read_text())
        self.assertEqual(dry, report)
        self.assert_book(self.output.read_bytes(), report, ['1:102', '1:101'])
        self.output.unlink(); self.report_path.unlink()
        for order in ([], ['1:101'], ['1:101', '1:101'], ['1:102', '1:unknown']):
            for dry_run in ([], ['--dry-run']):
                with self.subTest(order=order, dry_run=dry_run):
                    self.write('order.json', order)
                    result = self.cli(*args, *dry_run)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn('chapter_order', result.stderr)
                    self.assertNotIn('Traceback', result.stderr)
                    self.assertFalse(self.output.exists()); self.assertFalse(self.report_path.exists())
                    self.assertFalse(list(self.work.glob('.readbundle-*')))

    def test_cli_missing_failure_report_before_incomplete_order(self):
        self.write('order.json', ['1:unknown'])
        result = self.cli('build', ROOT / 'examples/epub/karakeep-export.json', '--chapter-order', self.order_path, '-o', self.output)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Selected entries have no usable body', result.stderr)
        self.assertFalse(self.output.exists())
        report = json.loads(self.report_path.read_text())
        self.assertEqual(report['chapter_order'], ['1:unknown'])
        self.assertEqual(report['status'], 'failed')

    def test_cli_rejects_file_encoding_json_shapes_and_missing_file(self):
        cases = [b'\xff', b'\xef\xbb\xbf["1:101","1:102"]', b'{', b'null', b'"1:101"', b'{}',
                 b'[NaN]', b'[Infinity]', b'[-Infinity]', b'["1:101",]', b'[] trailing',
                 b'["1:\\ud800"]', b'[true]', b'[1]', b'[{"x":1,"x":2}]', b'[' * 1100 + b']' * 1100]
        for raw in cases:
            with self.subTest(raw=raw[:50]):
                self.order_path.write_bytes(raw)
                result = self.cli('build', WALLABAG, '--chapter-order', self.order_path, '-o', self.output)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                self.assertFalse(self.output.exists()); self.assertFalse(self.report_path.exists())
        self.order_path.unlink()
        result = self.cli('build', WALLABAG, '--chapter-order', self.order_path, '-o', self.output)
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.output.exists()); self.assertFalse(self.report_path.exists())

    def test_cli_order_file_output_aliases_preserve_file(self):
        self.write('order.json', ['1:102', '1:101'])
        raw = self.order_path.read_bytes()
        alias = self.work / 'alias'
        alias.symlink_to(self.order_path)
        for path in (self.order_path, alias, self.work / 'sub/../order.json'):
            (self.work / 'sub').mkdir(exist_ok=True)
            for destination in (['-o', path], ['-o', self.output, '--report', path]):
                for dry in ([], ['--dry-run']):
                    result = self.cli('build', WALLABAG, '--chapter-order', self.order_path, *destination, *dry)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn('output conflicts with input', result.stderr)
                    self.assertEqual(self.order_path.read_bytes(), raw)
                    self.assertFalse(self.output.exists()); self.assertFalse(self.report_path.exists())

    def test_file_read_once_with_exact_byte_bound(self):
        from notewrench.epub.ordering import MAX_ORDER_BYTES, load_chapter_order
        raw = b'["1:102", "1:101"]'
        self.order_path.write_bytes(raw + b' ' * (MAX_ORDER_BYTES - len(raw)))
        self.assertEqual(load_chapter_order(self.order_path), ['1:102', '1:101'])
        self.order_path.write_bytes(self.order_path.read_bytes() + b' ')
        with self.assertRaisesRegex(BundleError, 'exceeds 1048576 bytes'):
            load_chapter_order(self.order_path)
        stream = io.BytesIO(raw)
        with patch('notewrench.epub.ordering.Path.open') as opened:
            opened.return_value.__enter__.return_value = stream
            with patch.object(stream, 'read', wraps=stream.read) as read:
                self.assertEqual(load_chapter_order(self.order_path), ['1:102', '1:101'])
                opened.assert_called_once_with('rb')
                read.assert_called_once_with(MAX_ORDER_BYTES + 1)
        result = self.cli('build', WALLABAG, '--chapter-order', self.order_path, '-o', self.output)
        self.assertEqual(result.returncode, 2)
        self.assertIn('exceeds 1048576 bytes', result.stderr)
        self.assertFalse(self.output.exists()); self.assertFalse(self.report_path.exists())

    def test_entry_count_bound_for_file_and_library(self):
        from notewrench.epub.ordering import MAX_ORDER_ENTRIES, validate_chapter_order
        keys = [f'1:{n}' for n in range(MAX_ORDER_ENTRIES)]
        self.assertEqual(validate_chapter_order(keys), keys)
        keys.append('1:extra')
        with self.assertRaisesRegex(BundleError, 'exceeds 10000 entries'):
            prepare([WALLABAG], chapter_order=keys)
        self.write('order.json', keys)
        result = self.cli('build', WALLABAG, '--chapter-order', self.order_path, '-o', self.output)
        self.assertEqual(result.returncode, 2)
        self.assertIn('exceeds 10000 entries', result.stderr)
        self.assertFalse(self.output.exists()); self.assertFalse(self.report_path.exists())


if __name__ == '__main__':
    unittest.main()
