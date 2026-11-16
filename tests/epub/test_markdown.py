"""Synthetic local Karakeep fixtures for the opt-in static Markdown profile."""
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
from notewrench.epub.core import prepare
from notewrench.epub.epub import XHTML, q, validate_epub
from notewrench.epub.markdown import render_markdown

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / 'examples/epub/karakeep-markdown.json'


class MarkdownTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='epub-markdown-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)

    def source(self, text, api=False):
        row = {'title': 'Synthetic note', 'tags': [],
               'content': {'type': 'text', 'text': text}}
        if api:
            row['id'] = 'note-1'
            row['content']['sourceUrl'] = 'https://example.invalid/notes/current'
        path = self.work / 'input.json'
        path.write_text(json.dumps(row if api else {'bookmarks': [row], 'lists': []}), encoding='utf-8')
        return path

    def content(self, payload):
        with zipfile.ZipFile(io.BytesIO(payload)) as book:
            root = ET.fromstring(book.read('EPUB/chapter-0001.xhtml'))
        return root.find(f'{q(XHTML, "body")}/{q(XHTML, "div")}')

    def test_emphasis_delimiter_contract_fixtures(self):
        cases = json.loads((ROOT / 'tests/fixtures/markdown-emphasis.json').read_text(encoding='utf-8'))
        for case in cases['rendered']:
            with self.subTest(source=case['source']):
                body, events = render_markdown(case['source'])
                expected = ET.fromstring('<div>' + case['html'] + '</div>')
                self.assertEqual(ET.tostring(body), ET.tostring(expected))
                self.assertEqual(events, [])
        for source in cases['literal']:
            with self.subTest(literal=source):
                body, events = render_markdown(source)
                self.assertEqual([child.tag for child in body], ['p'])
                self.assertEqual(len(body[0]), 0, 'fallback must not partially render markup')
                self.assertEqual(body[0].text, source)
                self.assertEqual(events, [{
                    'code': 'markdown_literal_inline', 'line_start': 1,
                    'line_end': source.count('\n') + 1,
                    'reason': 'inline_syntax_outside_static_subset_or_raw_html',
                }])

    def test_emphasis_epub_and_fallback_provenance(self):
        source = '## **Saved**\r\n\r\nRead *中文* and __notes__.\r\n\r\n*outer\r\n_inner_ outer*'
        for api in (False, True):
            with self.subTest(api=api):
                path = self.source(source, api=api)
                with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')):
                    payload, report = prepare([path], text_mode='markdown')
                    repeat, _ = prepare([path], text_mode='markdown')
                self.assertEqual(payload, repeat)
                body = self.content(payload)
                self.assertEqual(body.findtext(f'{q(XHTML, "h2")}/{q(XHTML, "strong")}'), 'Saved')
                self.assertEqual(body.findtext(f'{q(XHTML, "p")}/{q(XHTML, "em")}'), '中文')
                self.assertEqual(body.findtext(f'{q(XHTML, "p")}/{q(XHTML, "strong")}'), 'notes')
                self.assertEqual(body[-1].text, '*outer\n_inner_ outer*')
                self.assertEqual(len(body[-1]), 0)
                literal_events = [e for e in report['changes'] if e['code'] == 'markdown_literal_inline']
                self.assertEqual([(e['line_start'], e['line_end']) for e in literal_events], [(5, 6)])
                self.assertEqual(report['included'][0]['original_text'], source)
                self.assertEqual(report['included'][0]['original_text_sha256'], hashlib.sha256(source.encode()).hexdigest())
                literal, _ = prepare([path])
                explicit, _ = prepare([path], text_mode='literal')
                self.assertEqual(literal, explicit)
                # XML parsers normalize literal CRLF too; the report above is exact.
                self.assertEqual(self.content(literal)[0].text, source.replace('\r\n', '\n'))
                self.assertEqual(validate_epub(io.BytesIO(payload))['chapters'], 1)

    def test_escape_rendered_contract_fixtures(self):
        cases = json.loads((ROOT / 'tests/fixtures/markdown-escapes.json').read_text(encoding='utf-8'))
        for case in cases['rendered']:
            with self.subTest(source=case['source']):
                body, events = render_markdown(case['source'])
                expected = ET.fromstring('<div>' + case['html'] + '</div>')
                self.assertEqual(ET.tostring(body), ET.tostring(expected))
                self.assertEqual(events, case.get('events', []))

    def test_escape_atomic_literal_contract_fixtures(self):
        cases = json.loads((ROOT / 'tests/fixtures/markdown-escapes.json').read_text(encoding='utf-8'))
        for source in cases['literal']:
            with self.subTest(source=source):
                body, events = render_markdown(source)
                self.assertEqual([child.tag for child in body], ['p'])
                self.assertEqual(len(body[0]), 0)
                self.assertEqual(body[0].text, source)
                self.assertEqual(events, [{
                    'code': 'markdown_literal_inline', 'line_start': 1,
                    'line_end': source.count('\n') + 1,
                    'reason': 'inline_syntax_outside_static_subset_or_raw_html',
                }])

    def test_escape_native_api_epub_and_exact_provenance(self):
        source = ('Read \\*literal\\* and **bold \\_note\\_**.\r\n\r\n'
                  '*Safe \\<script\\>inactive\\</script\\>*\r\n\r\n'
                  'Read \\*literal\\* and &amp; **outer\r\n*inner* outer**')
        for api in (False, True):
            with self.subTest(api=api):
                path = self.source(source, api=api)
                with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), \
                     patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS forbidden')):
                    payload, report = prepare([path], text_mode='markdown')
                    repeat, _ = prepare([path], text_mode='markdown')
                    validation = validate_epub(io.BytesIO(payload))
                self.assertEqual(payload, repeat)
                self.assertEqual(validation['chapters'], 1)
                body = self.content(payload)
                self.assertEqual(body[0].text, 'Read *literal* and ')
                self.assertEqual(body[0][0].tag, q(XHTML, 'strong'))
                self.assertEqual(body[0][0].text, 'bold _note_')
                self.assertEqual(body[0][0].tail, '.')
                self.assertEqual(body[1][0].tag, q(XHTML, 'em'))
                self.assertEqual(body[1][0].text, 'Safe <script>inactive</script>')
                self.assertEqual(list(body.iter(q(XHTML, 'script'))), [])
                self.assertEqual(body[-1].text, source.split('\r\n\r\n')[-1].replace('\r\n', '\n'))
                self.assertEqual(len(body[-1]), 0)
                entry = report['included'][0]
                self.assertEqual(entry['original_text'], source)
                self.assertEqual(entry['original_text_sha256'], hashlib.sha256(source.encode('utf-8')).hexdigest())
                self.assertEqual(report['epub_sha256'], hashlib.sha256(payload).hexdigest())
                events = [e for e in report['changes'] if e['code'] == 'markdown_literal_inline']
                self.assertEqual([(e['line_start'], e['line_end']) for e in events], [(5, 6)])
                literal, _ = prepare([path])
                explicit, _ = prepare([path], text_mode='literal')
                self.assertEqual(literal, explicit)
                self.assertEqual(self.content(literal)[0].text, source.replace('\r\n', '\n'))

    def test_link_label_requested_example(self):
        source = r'[Read \*literal\*](https://example.invalid/notes) and **saved**.'
        body, events = render_markdown(source)
        expected = ET.fromstring('<div><p><a href="https://example.invalid/notes">'
                                 'Read *literal*</a> and <strong>saved</strong>.</p></div>')
        self.assertEqual(ET.tostring(body), ET.tostring(expected))
        self.assertEqual(events, [])

    def test_link_label_rendered_contract_fixtures(self):
        cases = json.loads((ROOT / 'tests/fixtures/markdown-link-label-escapes.json').read_text(encoding='utf-8'))
        for case in cases['rendered']:
            with self.subTest(source=case['source']):
                body, events = render_markdown(case['source'])
                expected = ET.fromstring('<div>' + case['html'] + '</div>')
                self.assertEqual(ET.tostring(body), ET.tostring(expected))
                self.assertEqual(events, case.get('events', []))

    def test_link_label_atomic_literal_contract_fixtures(self):
        cases = json.loads((ROOT / 'tests/fixtures/markdown-link-label-escapes.json').read_text(encoding='utf-8'))
        for source in cases['literal']:
            with self.subTest(source=source):
                body, events = render_markdown(source)
                self.assertEqual([child.tag for child in body], ['p'])
                self.assertEqual(len(body[0]), 0, 'fallback must not retain any rendered tokens')
                self.assertEqual(body[0].text, source)
                self.assertEqual(events, [{
                    'code': 'markdown_literal_inline', 'line_start': 1,
                    'line_end': source.count('\n') + 1,
                    'reason': 'inline_syntax_outside_static_subset_or_raw_html',
                }])

    def test_link_label_invalid_entities_preserve_recognition_boundary(self):
        for entity in ('&#0;', '&#xD800;', '&#x1;'):
            with self.subTest(entity=entity):
                with self.assertRaises(BundleError):
                    render_markdown(f'[{entity}](https://example.invalid)')
                for source in (f'[{entity} *unsupported*](https://example.invalid)',
                               f'[{entity}](missing'):
                    body, events = render_markdown(source)
                    self.assertEqual(len(body[0]), 0)
                    self.assertEqual(body[0].text, source)
                    self.assertEqual(events, [{
                        'code': 'markdown_literal_inline', 'line_start': 1,
                        'line_end': source.count('\n') + 1,
                        'reason': 'inline_syntax_outside_static_subset_or_raw_html',
                    }])
                body, events = render_markdown(f'[\\{entity}](https://example.invalid)')
                self.assertEqual(body[0][0].text, entity)
                self.assertEqual(events, [])

    def test_link_label_native_api_epub_and_exact_provenance(self):
        source = (r'[Read \*literal\*](https://example.invalid/notes) and **saved**.' + '\r\n\r\n'
                  r'[\<script\>inactive\</script\> \&amp; &amp; \] \notes](/next)' + '\r\n\r\n'
                  r'[unsafe \*label\*](jav&#x61;script:evil)' + '\r\n\r\n'
                  r'[okay \*](https://example.invalid) and &amp; **outer' + '\r\n*inner* outer**')
        for api in (False, True):
            with self.subTest(api=api):
                path = self.source(source, api=api)
                with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), \
                     patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS forbidden')):
                    payload, report = prepare([path], text_mode='markdown')
                    repeat, repeat_report = prepare([path], text_mode='markdown')
                    validation = validate_epub(io.BytesIO(payload))
                    literal, _ = prepare([path])
                    explicit, _ = prepare([path], text_mode='literal')
                self.assertEqual(payload, repeat)
                self.assertEqual(report, repeat_report)
                self.assertEqual(validation['chapters'], 1)
                body = self.content(payload)
                self.assertEqual(body[0][0].tag, q(XHTML, 'a'))
                self.assertEqual(body[0][0].get('href'), 'https://example.invalid/notes')
                self.assertEqual(body[0][0].text, 'Read *literal*')
                self.assertEqual(body[0][1].tag, q(XHTML, 'strong'))
                self.assertEqual(body[0][1].text, 'saved')
                safe = body[1][0]
                self.assertEqual(safe.tag, q(XHTML, 'a'))
                self.assertEqual(safe.text, r'<script>inactive</script> &amp; & ] \notes')
                self.assertEqual(safe.get('href'), 'https://example.invalid/next' if api else None)
                self.assertEqual(len(safe), 0)
                self.assertEqual(list(body.iter(q(XHTML, 'script'))), [])
                self.assertIsNone(body[2][0].get('href'))
                self.assertEqual(body[2][0].text, 'unsafe *label*')
                self.assertEqual(body[-1].text, source.split('\r\n\r\n')[-1].replace('\r\n', '\n'))
                self.assertEqual(len(body[-1]), 0)
                entry = report['included'][0]
                self.assertEqual(entry['original_text'], source)
                self.assertEqual(entry['original_text_sha256'], hashlib.sha256(source.encode('utf-8')).hexdigest())
                self.assertEqual(report['epub_sha256'], hashlib.sha256(payload).hexdigest())
                events = [e for e in report['changes'] if e['code'] == 'markdown_literal_inline']
                self.assertEqual([(e['line_start'], e['line_end']) for e in events], [(7, 8)])
                unsafe = [e for e in report['changes'] if e['code'] == 'removed_unsafe_uri']
                self.assertEqual([e['value'] for e in unsafe], ['javascript:evil'] if api else ['/next', 'javascript:evil'])
                self.assertEqual(literal, explicit)
                self.assertEqual(self.content(literal)[0].text, source.replace('\r\n', '\n'))

    def test_supported_blocks_and_exact_source_provenance(self):
        payload, report = prepare([EXAMPLE], text_mode='markdown')
        body = self.content(payload)
        self.assertEqual(body.findtext(q(XHTML, 'h2')), 'Weekend 中文')
        self.assertEqual([x.text for x in body.findall(f'{q(XHTML, "ul")}/{q(XHTML, "li")}')], ['First item', 'Second item'])
        ordered = body.find(q(XHTML, 'ol'))
        self.assertEqual(ordered.get('start'), '3')
        self.assertEqual(len(ordered), 2)
        self.assertEqual(body.findtext(f'{q(XHTML, "blockquote")}/{q(XHTML, "p")}'), 'A saved quotation.\nStill the same paragraph.')
        self.assertEqual(body.findtext(f'{q(XHTML, "pre")}/{q(XHTML, "code")}'), 'print("<safe> & 中文")\n\n# code, not a heading\n')
        self.assertEqual(body.findtext(f'{q(XHTML, "p")}/{q(XHTML, "code")}'), 'code span')
        self.assertIn('python', [e.get('value') for e in report['changes'] if e['code'] == 'markdown_code_info'])
        original = json.loads(EXAMPLE.read_text())['bookmarks'][0]['content']['text']
        entry = report['included'][0]
        self.assertEqual(entry['text_mode'], 'markdown')
        self.assertEqual(entry['original_text'], original)
        self.assertEqual(entry['original_text_sha256'], hashlib.sha256(original.encode()).hexdigest())
        self.assertEqual(report['text_mode'], 'markdown')
        self.assertEqual(validate_epub(io.BytesIO(payload))['chapters'], 1)

    def test_literal_default_is_unchanged_and_mode_is_explicit(self):
        path = self.source('## Heading\n\n- list\n\n<script>text()</script>')
        implicit, report = prepare([path])
        explicit, _ = prepare([path], text_mode='literal')
        self.assertEqual(implicit, explicit)
        body = self.content(implicit)
        self.assertEqual([x.tag for x in body], [q(XHTML, 'pre')])
        self.assertEqual(body[0].text, json.loads(path.read_text())['bookmarks'][0]['content']['text'])
        self.assertNotIn('original_text', report['included'][0])
        with self.assertRaisesRegex(BundleError, 'text.mode'):
            prepare([path], text_mode='guess')

    def test_html_links_and_entities_cannot_bypass_cleaner(self):
        source = ('[safe](/next) and [mail](mailto:reader@example.invalid)\n\n'
                  '[bad](<javascript:alert(1)>)\n\n'
                  '[encoded](jav&#x61;script:evil)\n\n'
                  '[local](file:///etc/passwd)\n\n'
                  '[fragment](#absent)\n\n'
                  '<script>alert(1)</script><a href="javascript:bad" onclick="evil()">raw</a>\n\n'
                  '`<img src=x onerror=evil()>`')
        payload, report = prepare([self.source(source, api=True)], text_mode='markdown')
        body = self.content(payload)
        self.assertEqual([a.get('href') for a in body.iter(q(XHTML, 'a'))],
                         ['https://example.invalid/next', 'mailto:reader@example.invalid', None, None, None, None])
        for tag in ('script', 'img', 'iframe'):
            self.assertEqual(list(body.iter(q(XHTML, tag))), [])
        self.assertIn('<script>alert(1)</script>', ''.join(body.itertext()))
        codes = [e['code'] for e in report['changes']]
        self.assertEqual(codes.count('removed_unsafe_uri'), 3)
        self.assertIn('removed_broken_fragment', codes)
        self.assertIn('markdown_literal_inline', codes)

    def test_unsupported_syntax_is_visible_and_reported(self):
        cases = ['- outer\n  - nested', 'Title\n=====', '| A | B |\n| - | - |',
                 '[ref]: https://example.invalid\n\n[ref]', '![image](file:///x)',
                 '**emphasis** and ~~extension~~', '    indented code', '> > nested quote',
                 '- # nested heading', 'paragraph  \nhard break', '[nested](url(with-parens))']
        for source in cases:
            with self.subTest(source=source):
                payload, report = prepare([self.source(source)], text_mode='markdown')
                visible = ''.join(self.content(payload).itertext())
                for line in source.splitlines():
                    self.assertIn(line, visible)
                events = [e for e in report['changes'] if e['code'].startswith('markdown_literal_')]
                self.assertTrue(events)
                self.assertTrue(all(e['line_start'] >= 1 and e['line_end'] >= e['line_start'] for e in events))
                self.assertEqual(report['included'][0]['original_text'], source)

    def test_reproducible_offline_and_only_text_is_affected(self):
        with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), \
             patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS forbidden')):
            first, report = prepare([EXAMPLE], text_mode='markdown')
            second, repeat = prepare([EXAMPLE], text_mode='markdown')
        self.assertEqual(first, second)
        self.assertEqual(report, repeat)
        literal, _ = prepare([EXAMPLE])
        self.assertNotEqual(first, literal)
        html = ROOT / 'examples/epub/wallabag.json'
        self.assertEqual(prepare([html])[0], prepare([html], text_mode='markdown')[0])

    def test_fences_headings_entities_and_original_line_endings(self):
        source = '# One #\r\n\r\n###### Six\r\n\r\n## ###\r\n\r\n~~~~ lang\r\n```\r\n~~~\r\n~~~~~\r\n\r\n` a &amp; ` and &amp;\r\n\r\n```\r\nEOF\u2028still code'
        path = self.source(source)
        payload, report = prepare([path], text_mode='markdown')
        body = self.content(payload)
        self.assertEqual(body.findtext(q(XHTML, 'h1')), 'One')
        self.assertEqual(body.findtext(q(XHTML, 'h6')), 'Six')
        self.assertEqual(body.findtext(q(XHTML, 'h2')), '')
        self.assertEqual([e.text for e in body.findall(f'{q(XHTML, "pre")}/{q(XHTML, "code")}')],
                         ['```\n~~~\n', 'EOF\u2028still code\n'])
        self.assertEqual(body.findtext(f'{q(XHTML, "p")}/{q(XHTML, "code")}'), 'a &amp;')
        self.assertIn(' and &', ''.join(body.itertext()))
        self.assertEqual(report['included'][0]['original_text'], source)
        self.assertEqual(report['included'][0]['original_text_sha256'], hashlib.sha256(source.encode()).hexdigest())
        for entity in ('&#0;', '&#x1;', '&#xD800;'):
            with self.subTest(entity=entity), self.assertRaises(BundleError):
                render_markdown(entity)

    def test_mixed_blocks_and_unavailable_bodies_keep_failure_policy(self):
        for source in ('## Heading\nparagraph', 'paragraph\n- list'):
            payload, report = prepare([self.source(source)], text_mode='markdown')
            self.assertTrue(any(e['code'] == 'markdown_literal_block' for e in report['changes']))
            for line in source.splitlines():
                if line:
                    self.assertIn(line, ''.join(self.content(payload).itertext()))
        payload, report = prepare([ROOT / 'examples/epub/karakeep-export.json'], text_mode='markdown')
        self.assertIsNone(payload)
        self.assertEqual(report['skipped'][0]['reason'], 'native_link_has_no_body')
        self.assertEqual(report['not_built'][0]['text_mode'], 'markdown')

    def test_invalid_xml_and_bad_cli_mode_fail_without_publication(self):
        for text, mode in (('## Invalid\x00', 'markdown'), ('## Invalid\ud800', 'markdown'),
                           ('## Invalid\x00', 'guessed')):
            source = self.source(text)
            result = subprocess.run([sys.executable, '-m', 'notewrench', 'epub', 'build', str(source),
                                     '--text-mode', mode, '-o', str(self.work / 'out.epub')],
                                    cwd=ROOT, text=True, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 2)
            self.assertFalse((self.work / 'out.epub').exists())
            self.assertFalse((self.work / 'out.report.json').exists())
            self.assertNotIn('Traceback', result.stderr)
            self.assertIn('XML 1.0' if mode == 'markdown' else 'invalid choice', result.stderr)
