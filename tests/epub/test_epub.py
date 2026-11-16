import hashlib
import io
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
import zipfile

from notewrench.epub import BundleError
from notewrench.epub.core import check_destinations, prepare, publish_new
from notewrench.epub.epub import DC, EPUB, OPF, XHTML, q, validate_epub
from notewrench.epub.htmlclean import clean_body
from notewrench.epub.inputs import load_input

ROOT = Path(__file__).resolve().parents[2]
WALLABAG = ROOT / 'examples/epub/wallabag.json'
EXPORT = ROOT / 'examples/epub/karakeep-export.json'
API = ROOT / 'examples/epub/karakeep-api.json'


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='epub-test-')
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)

    def input(self, data, name='input.json'):
        path = self.work / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return path

    def article(self, body, **extra):
        return {'id': 1, 'url': 'https://example.invalid/article', 'content': body,
                'title': '中文 title', 'tags': ['a'], **extra}

    def build(self, data, **kwargs):
        return prepare([self.input(data)], **kwargs)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'notewrench', 'epub', *map(str, args)],
                              cwd=ROOT, capture_output=True, text=True, timeout=60)

    def rewrite(self, payload, mutate):
        with zipfile.ZipFile(io.BytesIO(payload)) as book:
            entries = [(i.filename, book.read(i)) for i in book.infolist()]
        entries = mutate(entries)
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as book:
            for name, blob in entries:
                book.writestr(name, blob, compress_type=0 if name == 'mimetype' else 8)
        return output.getvalue()

    def test_two_chapters_real_zip_xml_metadata(self):
        payload, report = prepare([WALLABAG], title='Weekend reading', language='zh-Hans')
        self.assertEqual(validate_epub(io.BytesIO(payload))['chapters'], 2)
        self.assertEqual([x['id'] for x in report['included']], ['101', '102'])
        with zipfile.ZipFile(io.BytesIO(payload)) as book:
            self.assertEqual(book.infolist()[0].filename, 'mimetype')
            self.assertEqual(book.infolist()[0].compress_type, 0)
            self.assertEqual(book.read('mimetype'), b'application/epub+zip')
            opf = ET.fromstring(book.read('EPUB/package.opf'))
            self.assertEqual(opf.findtext(f'{q(OPF,"metadata")}/{q(DC,"title")}'), 'Weekend reading')
            manifest = opf.find(q(OPF, 'manifest'))
            spine = opf.find(q(OPF, 'spine'))
            self.assertEqual(len(manifest), 3)
            self.assertEqual(len(spine), 2)
            nav = ET.fromstring(book.read('EPUB/nav.xhtml'))
            self.assertEqual([x.get('href') for x in nav.iter(q(XHTML, 'a'))],
                             ['chapter-0001.xhtml', 'chapter-0002.xhtml'])
            chapter = book.read('EPUB/chapter-0001.xhtml').decode()
            for value in ('中文', '周末笔记', '<ul>', '<pre>', '<code>', 'https://example.invalid/reading/one'):
                self.assertIn(value, chapter)
            self.assertNotIn('<img', chapter)
        self.assertTrue(any(x['code'] == 'missing_resource' for x in report['changes']))

    def test_deterministic_bytes_and_hash(self):
        options = dict(title='Fixed', author='Synthetic Author', language='en', modified='2026-01-01T00:00:00Z')
        first, report1 = prepare([WALLABAG], **options)
        second, report2 = prepare([WALLABAG], **options)
        self.assertEqual(first, second)
        self.assertEqual(hashlib.sha256(first).hexdigest(), report1['epub_sha256'])
        self.assertEqual(report1, report2)
        changed, _ = prepare([WALLABAG], **{**options, 'title': 'Different'})
        self.assertNotEqual(first, changed)

    def test_no_network_in_build_or_validate(self):
        with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), \
             patch.object(socket, 'create_connection', side_effect=AssertionError('network forbidden')), \
             patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS forbidden')):
            payload, report = prepare([WALLABAG, API, EXPORT], skip_missing=True)
            self.assertEqual(validate_epub(io.BytesIO(payload))['chapters'], 4)
            self.assertEqual(len(report['skipped']), 1)

    def test_native_link_default_rejected_with_report(self):
        output, report_path = self.work / 'reading.epub', self.work / 'report.json'
        result = self.run_cli('build', EXPORT, '-o', output, '--report', report_path)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(output.exists())
        report = json.loads(report_path.read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(report['included'], [])
        self.assertEqual(report['skipped'][0]['reason'], 'native_link_has_no_body')
        self.assertEqual(report['not_built'][0]['id'], 'entry-1')

    def test_explicit_skip_and_literal_text(self):
        payload, report = prepare([EXPORT], skip_missing=True)
        self.assertEqual(len(report['included']), 1)
        self.assertEqual(len(report['skipped']), 1)
        with zipfile.ZipFile(io.BytesIO(payload)) as book:
            chapter = book.read('EPUB/chapter-0001.xhtml').decode()
            self.assertIn('**stays literal**', chapter)
            self.assertIn('&lt;script&gt;', chapter)
            self.assertNotIn('<script>', chapter)

    def test_api_single_array_and_page(self):
        snapshot = json.loads(API.read_text())
        for data, fmt in ((snapshot, 'auto'), ([snapshot], 'karakeep-api'),
                          ({'bookmarks': [snapshot], 'nextCursor': None}, 'auto')):
            with self.subTest(data=type(data).__name__, format=fmt):
                payload, report = self.build(data, format=fmt)
                self.assertIsNotNone(payload)
                self.assertEqual(report['included'][0]['format'], 'karakeep-api')
                self.assertEqual(report['included'][0]['id'], 'api-synthetic-1')

    def test_api_page_cursor_is_reported_without_fetching(self):
        snapshot = json.loads(API.read_text())
        payload, report = self.build({'bookmarks': [snapshot], 'nextCursor': {'ver': 2, 'id': 'next'}})
        self.assertIsNotNone(payload)
        self.assertEqual(report['warnings'][0]['code'], 'api_more_pages')
        self.assertEqual(len(report['included']), 1)

    def test_image_srcset_and_local_references_are_reported(self):
        root, events = clean_body('<p>Text<img srcset="/small.png 1x, /big.png 2x" alt="diagram"><img src="file:///private/example.png"></p>', True)
        resources = [e for e in events if e['code'] == 'missing_resource']
        self.assertEqual(resources[0]['srcset'], '/small.png 1x, /big.png 2x')
        self.assertEqual(resources[1]['url'], 'file:///private/example.png')
        self.assertIn('[Image omitted: diagram]', ''.join(root.itertext()))
        self.assertEqual(list(root.iter('img')), [])

    def test_missing_api_body_not_fabricated_from_description(self):
        data = json.loads(API.read_text())
        data['content'].pop('htmlContent')
        data['content']['description'] = 'Not a cached article'
        payload, report = self.build(data, skip_missing=True)
        self.assertIsNone(payload)
        self.assertEqual(report['skipped'][0]['reason'], 'missing_api_htmlContent')

    def test_api_asset_explicit_skip_report(self):
        data = json.loads(API.read_text())
        data['content'] = {'type': 'asset', 'assetType': 'pdf', 'assetId': 'pdf-1'}
        payload, report = self.build(data, skip_missing=True)
        self.assertIsNone(payload)
        self.assertEqual(report['skipped'][0]['reason'], 'unsupported_content_type')

    def test_tag_and_exact_key_selection(self):
        payload, report = prepare([WALLABAG, API], tags=['中文', 'other'], ids=['1:101', '2:api-synthetic-1'])
        self.assertEqual(len(report['included']), 1)
        self.assertEqual(len(report['excluded']), 2)
        self.assertEqual(report['included'][0]['key'], '1:101')
        self.assertEqual(validate_epub(io.BytesIO(payload))['chapters'], 1)
        with self.assertRaisesRegex(BundleError, 'unknown selection'):
            prepare([WALLABAG], ids=['1:does-not-exist'])

    def test_no_selected_chapters(self):
        payload, report = prepare([WALLABAG], tags=['does-not-exist'])
        self.assertIsNone(payload)
        self.assertEqual(len(report['excluded']), 2)
        self.assertIn('No usable', report['errors'][0])

    def test_empty_and_script_only_bodies(self):
        for body in (None, '', '  ', '<script>danger()</script>', '<style>p{}</style>'):
            with self.subTest(body=body):
                payload, report = self.build([self.article(body)])
                self.assertIsNone(payload)
                self.assertEqual(len(report['skipped']), 1)

    def test_active_content_events_and_dangerous_links(self):
        data = '<p onclick="bad()" style="color:red">中文<script>danger()</script><a href="jav&#x61;script:bad()">click</a><iframe src="https://example.invalid">evil</iframe><svg><script>x</script></svg></p>'
        payload, report = self.build([self.article(data)])
        with zipfile.ZipFile(io.BytesIO(payload)) as book:
            text = book.read('EPUB/chapter-0001.xhtml').decode()
            for forbidden in ('onclick', 'style=', 'danger()', 'javascript:', '<iframe', '<svg', 'evil'):
                self.assertNotIn(forbidden, text)
            self.assertIn('中文', text)
            self.assertIn('click', text)
        codes = [x['code'] for x in report['changes']]
        self.assertIn('removed_active_element', codes)
        self.assertIn('removed_attribute', codes)
        self.assertIn('removed_unsafe_uri', codes)

    def test_unsafe_uri_variants(self):
        for uri in ('data:text/html,evil', 'file:///etc/passwd', 'javascript:evil()', 'java\nscript:evil()', 'https://bad.invalid:bad/a', '\\evil.invalid', 'vbscript:evil'):
            with self.subTest(uri=uri):
                root, events = clean_body(f'<a href="{uri}">keep</a>', True)
                self.assertNotIn('href', root.find('a').attrib)
                self.assertTrue(any(x['code'] == 'removed_unsafe_uri' for x in events))

    def test_relative_links_resolve_and_fragments_preserved(self):
        payload, report = self.build([self.article('<h2 id="here">标题</h2><a href="#here">jump</a><a href="#absent">missing</a><a href="/next">relative</a>')])
        self.assertIsNotNone(payload)
        self.assertTrue(any(x['code'] == 'removed_broken_fragment' for x in report['changes']))
        with zipfile.ZipFile(io.BytesIO(payload)) as book:
            text = book.read('EPUB/chapter-0001.xhtml').decode()
            self.assertIn('href="#here"', text)
            self.assertIn('href="https://example.invalid/next"', text)

    def test_html_repairs_entities_and_unknown_wrappers(self):
        root, events = clean_body('<custom><p>A & B<p>中文 &copy;<ul><li>one<li>two</ul></custom>', True)
        ET.fromstring(ET.tostring(root))
        self.assertIn('中文 ©', ''.join(root.itertext()))
        self.assertEqual(len(list(root.iter('li'))), 2)
        self.assertIn('unwrapped_element', [x['code'] for x in events])

    def test_unsupported_xml_controls_declarations_and_depth(self):
        for html in ('<p>bad\x00</p>', '<!DOCTYPE html SYSTEM "file:///x"><p>x</p>', '<?bad processing?><p>x</p>', '<div>' * 130 + 'x'):
            with self.subTest(html=html[:80]):
                with self.assertRaises(BundleError):
                    self.build([self.article(html)])
        with self.assertRaises(BundleError):
            self.build([self.article('<p>x</p>', title='bad\x01')])

    def test_malformed_json_and_duplicate_properties(self):
        path = self.work / 'bad.json'
        for data in (b'{', b'\xff', b'{"bookmarks":[],"bookmarks":[]}', b'[NaN]', b'[' + b'9' * 5000 + b']'):
            with self.subTest(data=data):
                path.write_bytes(data)
                result = self.run_cli('build', path, '-o', self.work / 'out.epub')
                self.assertEqual(result.returncode, 2)
                self.assertNotIn('Traceback', result.stderr)

    def test_unknown_shapes_and_wrong_field_types(self):
        cases = [42, {}, {'entries': []}, [], [self.article({'bad': 'body'})],
                 [self.article('<p>x</p>', tags=[1])],
                 [self.article('<p>x</p>'), self.article('<p>duplicate</p>')],
                 {'bookmarks': [{'id': 'api', 'content': {'type': 'text', 'text': 'x'}, 'tags': []}, {'content': {'type': 'text', 'text': 'x'}}]},
                 {'bookmarks': [{'content': {'type': 'mystery'}, 'tags': []}]},
                 {'bookmarks': [{'content': {'type': []}, 'tags': []}]},
                 {'bookmarks': [{'content': {'type': {}}, 'tags': []}]},
                 {'bookmarks': [{'content': {'type': 'link', 'url': 'https://example.invalid', 'htmlContent': '<p>invented native body</p>'}}]}]
        for data in cases:
            with self.subTest(data=data):
                with self.assertRaises(BundleError):
                    self.build(data)

    def test_empty_explicit_format_reports_no_chapters(self):
        payload, report = self.build([], format='wallabag')
        self.assertIsNone(payload)
        self.assertTrue(report['errors'])

    def test_bad_metadata(self):
        for options in ({'title': ' '}, {'author': ''}, {'language': 'bad language'},
                        {'modified': 'today'}, {'modified': '2026-02-31T00:00:00Z'}):
            with self.subTest(options=options):
                with self.assertRaises(BundleError):
                    prepare([WALLABAG], **options)

    def test_cli_success_validate_and_output_conflict(self):
        output, report = self.work / 'out.epub', self.work / 'report.json'
        result = self.run_cli('build', WALLABAG, '-o', output, '--report', report)
        self.assertEqual(result.returncode, 0, result.stderr)
        original = output.read_bytes()
        self.assertEqual(self.run_cli('validate', output).returncode, 0)
        result = self.run_cli('build', WALLABAG, '-o', output, '--report', report)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(output.read_bytes(), original)
        self.assertEqual(len(json.loads(report.read_text())['included']), 2)

    def test_dry_run_writes_nothing(self):
        result = self.run_cli('build', WALLABAG, '-o', self.work / 'dry.epub', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'completed')
        self.assertEqual(list(self.work.iterdir()), [])

    def test_output_input_samepath_and_broken_symlink(self):
        source = self.input([self.article('<p>x</p>')])
        with self.assertRaises(BundleError):
            check_destinations([source], [source])
        with self.assertRaises(BundleError):
            check_destinations([self.work / 'x', self.work / 'x'], [source])
        link = self.work / 'broken'
        link.symlink_to(self.work / 'absent')
        with self.assertRaises(BundleError):
            check_destinations([link], [source])
        with self.assertRaises(BundleError):
            check_destinations([self.work / 'absent-parent/out'], [source])

    def test_publish_rolls_back_if_second_destination_conflicts(self):
        first, second = self.work / 'first', self.work / 'second'
        second.write_bytes(b'original')
        with self.assertRaises(FileExistsError):
            publish_new([(first, b'new'), (second, b'new')])
        self.assertFalse(first.exists())
        self.assertEqual(second.read_bytes(), b'original')
        self.assertEqual(sorted(p.name for p in self.work.iterdir()), ['second'])

    def test_input_size_bound(self):
        path = self.input([])
        with patch('notewrench.epub.inputs.MAX_INPUT_BYTES', 1):
            with self.assertRaisesRegex(BundleError, 'exceeds'):
                load_input(path, 1)

    def test_validate_rejects_corrupt_and_missing_file(self):
        for raw in (b'not a ZIP', b'PK\x03\x04'):
            with self.assertRaises(BundleError):
                validate_epub(io.BytesIO(raw))
        self.assertEqual(self.run_cli('validate', self.work / 'absent.epub').returncode, 2)

    def test_validator_rejects_zip_contract_violations(self):
        payload, _ = prepare([WALLABAG])
        changes = {
            'mimetype not first': lambda e: [e[1], e[0], *e[2:]],
            'wrong mimetype': lambda e: [('mimetype', b'text/html'), *e[1:]],
            'missing chapter': lambda e: [x for x in e if x[0] != 'EPUB/chapter-0001.xhtml'],
            'zip traversal': lambda e: [*e, ('../escape', b'x')],
            'unmanifested': lambda e: [*e, ('EPUB/extra.txt', b'x')],
        }
        for name, mutate in changes.items():
            with self.subTest(name=name):
                bad = self.rewrite(payload, mutate)
                with self.assertRaises(BundleError):
                    validate_epub(io.BytesIO(bad))
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, 'w', compression=8) as book:
            book.writestr('mimetype', b'application/epub+zip')
        with self.assertRaisesRegex(BundleError, 'uncompressed'):
            validate_epub(io.BytesIO(bad.getvalue()))

    def test_validator_rejects_manifest_spine_nav_and_xml_tampering(self):
        payload, _ = prepare([WALLABAG])
        cases = [
            ('EPUB/package.opf', b'idref="chapter-0001"', b'idref="absent"'),
            ('EPUB/package.opf', b'href="chapter-0001.xhtml"', b'href="absent.xhtml"'),
            ('EPUB/package.opf', b'properties="nav"', b'properties=""'),
            ('EPUB/package.opf', b'<dc:title>Reading bundle</dc:title>', b''),
            ('EPUB/nav.xhtml', b'href="chapter-0001.xhtml"', b'href="chapter-0002.xhtml"'),
            ('EPUB/nav.xhtml', b'epub:type="toc"', b'epub:type="landmarks"'),
            ('EPUB/chapter-0001.xhtml', b'<h1>', b'<h1 onclick="bad()">'),
            ('EPUB/chapter-0001.xhtml', b'<h1>', b'<script>bad()</script><h1>'),
            ('EPUB/chapter-0001.xhtml', b'<h1>', b'<h1><broken>'),
            ('EPUB/chapter-0001.xhtml', b'<h1>', b'<!DOCTYPE html><h1>'),
        ]
        for name, old, new in cases:
            with self.subTest(name=name, old=old):
                def mutate(entries):
                    result = []
                    for path, blob in entries:
                        if path == name:
                            self.assertIn(old, blob)
                            blob = blob.replace(old, new, 1)
                        result.append((path, blob))
                    return result
                with self.assertRaises(BundleError):
                    validate_epub(io.BytesIO(self.rewrite(payload, mutate)))

    def test_validator_size_bounds(self):
        payload, _ = prepare([WALLABAG])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            archive.infolist()[0].file_size = 17 * 1024 * 1024
            from notewrench.epub.epub import _validate
            with self.assertRaisesRegex(BundleError, '16 MiB'):
                _validate(archive)


if __name__ == '__main__':
    unittest.main()
