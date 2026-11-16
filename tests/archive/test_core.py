# SPDX-License-Identifier: AGPL-3.0-only
import copy
import hashlib
from html.parser import HTMLParser
import json
import re
from pathlib import Path
import unittest

from notewrench.archive.core import InputError, build, decode, json_text
from notewrench.archive.html import TAGS, sanitize, web_url

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    return json.loads((ROOT / 'examples' / 'archive' / name).read_text(encoding='utf-8'))


def convert(mode, value):
    return build(mode, json_text(value).encode('utf-8'))


class AuditHTML(HTMLParser):
    def handle_starttag(self, tag, attrs):
        if tag not in TAGS | {'html', 'head', 'meta', 'title', 'body'}:
            raise AssertionError(f'unsafe tag {tag}')
        for key, value in attrs:
            if key.lower().startswith('on') or key in {'style', 'src', 'srcset', 'formaction'}:
                raise AssertionError(f'unsafe attribute {key}')
            if key == 'href' and not (web_url(value) or value.startswith('#') or re.fullmatch(r'article-[0-9a-f]{64}(?:-[0-9]+)?\.html', value)):
                raise AssertionError(f'unsafe URL {value}')


class RescueTests(unittest.TestCase):
    def setUp(self):
        self.data = load('wallabag-v2.json')

    def test_body_metadata_annotations_and_unknown_fields(self):
        before = copy.deepcopy(self.data)
        files, manifest = convert('rescue', self.data)
        article = files[manifest['records'][0]['file']]
        for value in ['缓存正文', '研究', '<blockquote>保存知识，也保存上下文。</blockquote>', '这是合成注释', '/p[1]', '2026-10-01T09:30:00+0200', 'https://example.org/reference']:
            self.assertIn(value, article)
        self.assertEqual(self.data, before)
        self.assertEqual(json.loads(files['provenance.json'])['source'], self.data)
        self.assertEqual(manifest['input_count'], 1)
        self.assertEqual(manifest['output_count'], 1)
        AuditHTML().feed(article)
        AuditHTML().feed(files['index.html'])
        for name, metadata in manifest['files'].items():
            self.assertEqual(hashlib.sha256(files[name].encode()).hexdigest(), metadata['sha256'])
            self.assertEqual(len(files[name].encode()), metadata['bytes'])

    def test_determinism_duplicates_and_title_traversal(self):
        self.data[0]['title'] = '../../escape/<script>do not execute</script>'
        self.data *= 2
        one, manifest = convert('rescue', self.data)
        two, _ = convert('rescue', self.data)
        self.assertEqual(one, two)
        self.assertEqual(len([n for n in one if n.startswith('article-')]), 2)
        self.assertTrue(any(i['code'] == 'duplicate_url' for i in manifest['issues']))
        self.assertTrue(manifest['records'][1]['stable_id'].endswith('-2'))
        for name in one:
            self.assertEqual(Path(name).name, name)
        self.assertIn('&lt;script&gt;', one['index.html'])

    def test_stable_names_do_not_depend_on_distinct_entry_order(self):
        other = copy.deepcopy(self.data[0])
        other['url'] = 'https://example.org/b'
        first = convert('rescue', self.data + [other])[1]
        second = convert('rescue', [other] + self.data)[1]
        self.assertEqual({r['url']: r['file'] for r in first['records']}, {r['url']: r['file'] for r in second['records']})

    def test_missing_body_has_explicit_placeholder_and_loss(self):
        for content in (None, '', '  ', '<script>alert(1)</script>', '<img src="https://example.org/image">'):
            with self.subTest(content=content):
                self.data[0]['content'] = content
                files, report = convert('rescue', self.data)
                self.assertFalse(report['records'][0]['cached_body'])
                self.assertIn('missing_cached_body', [i['code'] for i in report['issues']])
                self.assertIn('No readable cached body', files[report['records'][0]['file']])

    def test_integer_flags_and_optional_fields(self):
        entry = {'url': 'https://example.org/a', 'title': 'a', 'content': '<p>ok</p>', 'is_archived': 0, 'is_starred': 1}
        _, report = convert('rescue', [entry])
        self.assertEqual(report['output_count'], 1)

    def test_unsafe_source_and_metadata_escaped(self):
        self.data[0]['url'] = 'javascript:alert(1)'
        self.data[0]['title'] = '<img src=x onerror=alert(1)>'
        self.data[0]['tags'] = ['<script>bad</script>']
        self.data[0]['annotations'][0]['quote'] = '<svg onload=x>'
        self.data[0]['annotations'][0]['text'] = '</pre><script>x</script>'
        files, report = convert('rescue', self.data)
        article = files[report['records'][0]['file']]
        AuditHTML().feed(article)
        self.assertIn('&lt;svg onload=x&gt;', article)
        self.assertIn('unsafe_source_url', [i['code'] for i in report['issues']])


class SanitizerTests(unittest.TestCase):
    def test_attack_corpus(self):
        attacks = [
            '<script>alert(1)</script><p onclick="x">保留</p>',
            '<svg><a href="javascript:x">bad</a></svg><p>保留</p>',
            '<math><mtext><table><mglyph><style><!--</style><img title="--><img src=x onerror=x>">',
            '<iframe srcdoc="<script>x</script>">bad</iframe><p>保留</p>',
            '<object data="x">bad</object><embed src=x><p>保留</p>',
            '<a href="java&#x09;script:alert(1)" onfocus=x>保留</a>',
            '<a href="data:text/html,evil">保留</a><a href="file:///etc/passwd">x</a>',
            '<a href="vbscript:evil">保留</a><img src="https://remote/image" onerror=x alt="替代文本">',
            '<form action=x><input formaction=x><button>保留</button></form>',
            '<style>body{background:url(https://remote)}</style><link rel=stylesheet href=x><meta http-equiv=refresh content="0;url=https://evil"><base href=x>',
            '<a href="https://example.org/" style="color:red" ping="https://remote">保留</a>',
            '<p title="&quot; onload=&quot;x">保留</p><img srcset=x>',
            '<a href="//example.org/path">保留</a><a href="\\\\evil.test">x</a>',
            '<!-- bad --><!doctype html><p>保留</p>',
            '<script/><p>保留</p><b><i>正常</b></i>',
        ]
        for content in attacks:
            with self.subTest(content=content):
                html, changes, text = sanitize(content, 'https://example.org/a')
                AuditHTML().feed(html)
                self.assertTrue(changes)
                self.assertNotIn('<script', html)
                self.assertNotIn('<img', html)
                self.assertNotIn('<svg', html)

    def test_relative_resolution_and_allowed_formatting(self):
        html, changes, text = sanitize('<p><a href="/b?q=1&amp;x=2">中文</a><strong>strong</strong><br><code>&lt;x&gt;</code></p>', 'https://example.org/a')
        self.assertIn('href="https://example.org/b?q=1&amp;x=2"', html)
        self.assertIn('<strong>strong</strong>', html)
        self.assertEqual(changes, {'resolved_relative_links': 1})
        self.assertIn('中文', text)

    def test_url_validation(self):
        for url in ['javascript:x', 'https://user:pass@example.org', 'https:///nohost', 'https://x:bad', 'https://x:0', 'https://x:99999', '//x', 'https://x/\n', 'https://x/ a', 'file:///tmp/x', 'https://x\\y']:
            self.assertFalse(web_url(url), url)
        for url in ['https://example.org/中文', 'http://localhost/a', 'https://[::1]/', 'HTTPS://EXAMPLE.ORG/a#b']:
            self.assertTrue(web_url(url), url)


class KarakeepTests(unittest.TestCase):
    def setUp(self):
        self.data = load('karakeep-links.json')

    def test_seconds_dates_tags_archive_and_note(self):
        files, report = convert('k2w', self.data)
        output = json.loads(files['wallabag.json'])
        self.assertEqual(output[0]['created_at'], '2026-10-01T08:00:00Z')
        self.assertEqual(output[0]['tags'], ['研究'])
        self.assertTrue(output[0]['is_archived'])
        self.assertFalse(output[0]['is_starred'])
        self.assertIn('合成笔记', output[0]['content'])
        self.assertEqual(report['skipped_count'], 0)
        self.assertFalse(any(i['severity'] == 'loss' for i in report['issues']))
        # Actual output schema feeds the reverse archive operation.
        archive, rescued = build('rescue', files['wallabag.json'].encode())
        self.assertIn('合成笔记', archive[rescued['records'][0]['file']])

    def test_null_title_defaults_fractional_seconds_and_note_escape(self):
        entry = self.data['bookmarks'][0]
        entry.update(title=None, note='<script>x</script>', createdAt=0.5)
        del entry['archived']
        files, _ = convert('k2w', self.data)
        output = json.loads(files['wallabag.json'])[0]
        self.assertEqual(output['title'], entry['content']['url'])
        self.assertEqual(output['created_at'], '1970-01-01T00:00:00.500000Z')
        self.assertFalse(output['is_archived'])
        self.assertIn('&lt;script&gt;', output['content'])
        AuditHTML().feed(output['content'])

    def test_native_null_text_lists_and_unknown_preserved(self):
        data = load('karakeep-native.json')
        files, report = convert('k2w', data)
        self.assertEqual(report['input_count'], 3)
        self.assertEqual(report['output_count'], 1)
        self.assertEqual(report['skipped_count'], 2)
        codes = {i['code'] for i in report['issues']}
        self.assertTrue({'text_without_url', 'null_content', 'list_membership_not_mapped', 'list_definitions_not_mapped', 'provenance_only_fields'} <= codes)
        self.assertEqual(json.loads(files['provenance.json'])['source'], data)
        self.assertNotIn('text', json.loads(files['wallabag.json'])[0]['url'])

    def test_submicrosecond_date_loss_reported(self):
        self.data['bookmarks'][0]['createdAt'] = 0.0000001
        _, report = convert('k2w', self.data)
        self.assertIn('timestamp_precision_loss', [i['code'] for i in report['issues']])

    def test_assets_reported_and_no_fake_url(self):
        self.data['bookmarks'][0]['content'] = {'type': 'asset', 'assetId': 'synthetic'}
        files, report = convert('k2w', self.data)
        self.assertEqual(json.loads(files['wallabag.json']), [])
        self.assertEqual(report['records'][0]['status'], 'skipped')
        self.assertIn('unsupported_asset', [i['code'] for i in report['issues']])

    def test_duplicates_retained_and_reported(self):
        self.data['bookmarks'] *= 2
        files, report = convert('k2w', self.data)
        self.assertEqual(len(json.loads(files['wallabag.json'])), 2)
        duplicate = next(i for i in report['issues'] if i['code'] == 'duplicate_url')
        self.assertEqual(duplicate['entries'], [0, 1])
        self.assertEqual(duplicate['severity'], 'loss')

    def test_api_html_not_interpreted_as_native_cached_content(self):
        self.data['bookmarks'][0]['content']['htmlContent'] = '<p>invented cached HTML</p>'
        files, report = convert('k2w', self.data)
        self.assertNotIn('invented', files['wallabag.json'])
        self.assertIn('invented', files['provenance.json'])
        self.assertTrue(any(i.get('content_fields') == ['htmlContent'] for i in report['issues']))

    def test_unsafe_urls_skipped_not_silently_changed(self):
        self.data['bookmarks'][0]['content']['url'] = 'javascript:alert(1)'
        files, report = convert('k2w', self.data)
        self.assertEqual(json.loads(files['wallabag.json']), [])
        self.assertIn('unsafe_link_url', [i['code'] for i in report['issues']])


class ValidationTests(unittest.TestCase):
    def test_invalid_json_and_encoding(self):
        for raw in [b'{', b'[] garbage', b'[{"x":1,"x":2}]', b'{"n":NaN}', b'{"n":Infinity}', b'{"n":1e999}', b'{"x":"\\ud800"}', b'\xff']:
            with self.subTest(raw=raw), self.assertRaises(InputError):
                decode(raw)

    def test_wrong_wallabag_structures(self):
        for data in [{}, {'bookmarks': []}, [None], [1], [{'url': 2, 'title': 'x'}], [{'url':'https://example.org','title':'x','content':[]}]]:
            with self.subTest(data=data), self.assertRaises(InputError):
                convert('rescue', data)

    def test_wrong_wallabag_fields(self):
        entry = load('wallabag-v2.json')[0]
        changes = [{'created_at': 'not-a-date'}, {'created_at': '2026-01-01T00:00:00+01:60'}, {'created_at': '2026-02-30T00:00:00Z'}, {'created_at': '2026-01-01'}, {'created_at': '2026-01-01T00:00:00'}, {'tags': [{'label':'x'}]}, {'is_archived': 2}, {'annotations': None}, {'annotations':[{'quote':2}]}, {'annotations':[{'created_at':'yesterday'}]}, {'id': False}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(InputError):
                convert('rescue', [dict(entry, **change)])

    def test_wrong_native_structures_and_dates(self):
        data = load('karakeep-links.json')
        for value in [[], {}, {'bookmarks':{}}, {'bookmarks':[None]}]:
            with self.subTest(value=value), self.assertRaises(InputError):
                convert('k2w', value)
        for change in [{'createdAt':'1790841600'}, {'createdAt':True}, {'createdAt':1790841600000}, {'createdAt':10**1000}, {'tags':None}, {'note':5}, {'title':5}, {'archived':1}, {'content':{'type':'other'}}, {'content':{'type':'link','url':None}}, {'lists':[3]}]:
            with self.subTest(change=change), self.assertRaises(InputError):
                convert('k2w', {'bookmarks':[dict(data['bookmarks'][0], **change)]})
        for key in ['content', 'createdAt', 'note', 'title', 'tags']:
            altered = copy.deepcopy(data)
            del altered['bookmarks'][0][key]
            with self.subTest(missing=key), self.assertRaises(InputError):
                convert('k2w', altered)

    def test_native_list_schema_checked(self):
        data = load('karakeep-native.json')
        for lists in [None, ['list'], [{}], [dict(data['lists'][0], type='unknown')]]:
            with self.subTest(lists=lists), self.assertRaises(InputError):
                convert('k2w', dict(data, lists=lists))

    def test_unknown_mode_rejected(self):
        with self.assertRaises(InputError):
            build('unknown', b'[]')

    def test_deep_json_explicit_error(self):
        with self.assertRaises(InputError):
            decode(b'[' * 2000 + b']' * 2000)

    def test_empty_and_bom(self):
        files, manifest = build('rescue', b'\xef\xbb\xbf[]')
        self.assertEqual(manifest['input_count'], 0)
        self.assertIn('index.html', files)
        _, manifest = convert('k2w', {'bookmarks': []})
        self.assertIn('no_convertible_links', [i['code'] for i in manifest['issues']])
