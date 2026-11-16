"""Original source contracts for soft breaks in flat Markdown link labels."""
import hashlib
import io
import json
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
import zipfile

from notewrench.epub import BundleError
from notewrench.epub.core import prepare
from notewrench.epub.epub import XHTML, q, validate_epub
from notewrench.epub.markdown import render_markdown

FIXTURE = Path(__file__).parent / 'fixtures/markdown-soft-labels.json'


class SoftLabelTests(unittest.TestCase):
    def test_requested_example(self):
        body, events = render_markdown('[Read\nsaved](https://example.invalid/notes) and **notes**.')
        expected = ET.fromstring('<div><p><a href="https://example.invalid/notes">'
                                 'Read\nsaved</a> and <strong>notes</strong>.</p></div>')
        self.assertEqual(ET.tostring(body), ET.tostring(expected))
        self.assertEqual(events, [])

    def test_rendered_source_contracts(self):
        cases = json.loads(FIXTURE.read_text(encoding='utf-8'))
        for case in cases['rendered']:
            with self.subTest(source=case['source']):
                body, events = render_markdown(case['source'])
                expected = ET.fromstring('<div>' + case['html'] + '</div>')
                self.assertEqual(ET.tostring(body), ET.tostring(expected))
                self.assertEqual(events, case.get('events', []))

    def test_atomic_literal_source_contracts(self):
        cases = json.loads(FIXTURE.read_text(encoding='utf-8'))
        for source in cases['literal']:
            with self.subTest(source=source):
                body, events = render_markdown(source)
                self.assertEqual([child.tag for child in body], ['p'])
                self.assertEqual(len(body[0]), 0, 'fallback must discard all rendered tokens')
                self.assertEqual(body[0].text, source)
                self.assertEqual(events, [{
                    'code': 'markdown_literal_inline', 'line_start': 1,
                    'line_end': source.count('\n') + 1,
                    'reason': 'inline_syntax_outside_static_subset_or_raw_html',
                }])

    def test_invalid_entities_wait_for_complete_label_recognition(self):
        for entity in ('&#0;', '&#xD800;', '&#x1;'):
            with self.subTest(entity=entity):
                with self.assertRaises(BundleError):
                    render_markdown(f'[{entity}\nlabel](https://example.invalid)')
                for source in (f'[{entity}\n*raw*](https://example.invalid)',
                               f'[{entity}\nlabel](missing',
                               f'[{entity}\\\nlabel](https://example.invalid)'):
                    body, events = render_markdown(source)
                    self.assertEqual(len(body[0]), 0)
                    self.assertEqual(body[0].text, source)
                    self.assertEqual(events, [{
                        'code': 'markdown_literal_inline', 'line_start': 1, 'line_end': 2,
                        'reason': 'inline_syntax_outside_static_subset_or_raw_html',
                    }])
                body, events = render_markdown(f'[\\{entity}\nlabel](https://example.invalid)')
                self.assertEqual(body[0][0].text, entity + '\nlabel')
                self.assertEqual(events, [])

    def test_native_api_epub_report_crlf_and_literal_default(self):
        source = ('[Read\r\nsaved](https://example.invalid/notes) and **notes**.\r\n\r\n'
                  '[\\<script\\>\r\ninactive\\</script\\> \\&amp; &amp;](/next)\r\n\r\n'
                  '[safe\r\nlabel](jav&#x61;script:evil)\r\n\r\n'
                  '**before** &amp; [hard\\\r\nbreak](https://example.invalid)\r\n\r\n'
                  '**before** &amp; [hard  \r\nbreak](https://example.invalid)')
        for api in (False, True):
            with self.subTest(api=api), tempfile.TemporaryDirectory() as directory:
                row = {'title': 'Soft labels', 'tags': [], 'content': {'type': 'text', 'text': source}}
                if api:
                    row.update(id='soft-labels')
                    row['content']['sourceUrl'] = 'https://example.invalid/notes/current'
                path = Path(directory) / 'input.json'
                path.write_text(json.dumps(row if api else {'bookmarks': [row], 'lists': []}), encoding='utf-8')
                with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), \
                     patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS forbidden')):
                    payload, report = prepare([path], text_mode='markdown')
                    repeat, repeat_report = prepare([path], text_mode='markdown')
                    validation = validate_epub(io.BytesIO(payload))
                    literal, literal_report = prepare([path])
                    explicit, explicit_report = prepare([path], text_mode='literal')
                self.assertEqual(payload, repeat)
                self.assertEqual(report, repeat_report)
                self.assertEqual(validation['chapters'], 1)
                with zipfile.ZipFile(io.BytesIO(payload)) as book:
                    root = ET.fromstring(book.read('EPUB/chapter-0001.xhtml'))
                body = root.find(f'{q(XHTML, "body")}/{q(XHTML, "div")}')
                self.assertEqual(body[0][0].tag, q(XHTML, 'a'))
                self.assertEqual(body[0][0].text, 'Read\nsaved')
                self.assertEqual(body[0][0].get('href'), 'https://example.invalid/notes')
                self.assertEqual(body[0][1].tag, q(XHTML, 'strong'))
                self.assertEqual(body[0][1].text, 'notes')
                self.assertEqual(body[1][0].text, '<script>\ninactive</script> &amp; &')
                self.assertEqual(len(body[1][0]), 0)
                self.assertEqual(body[1][0].get('href'), 'https://example.invalid/next' if api else None)
                self.assertEqual(list(body.iter(q(XHTML, 'script'))), [])
                self.assertIsNone(body[2][0].get('href'))
                self.assertEqual(body[2][0].text, 'safe\nlabel')
                for i in (3, 4):
                    self.assertEqual(len(body[i]), 0)
                    self.assertEqual(body[i].text, source.split('\r\n\r\n')[i].replace('\r\n', '\n'))
                events = [e for e in report['changes'] if e['code'] == 'markdown_literal_inline']
                self.assertEqual([(e['line_start'], e['line_end']) for e in events], [(10, 11), (13, 14)])
                unsafe = [e['value'] for e in report['changes'] if e['code'] == 'removed_unsafe_uri']
                self.assertEqual(unsafe, ['javascript:evil'] if api else ['/next', 'javascript:evil'])
                entry = report['included'][0]
                self.assertEqual(entry['original_text'], source)
                self.assertEqual(entry['original_text_sha256'], hashlib.sha256(source.encode()).hexdigest())
                self.assertEqual(report['epub_sha256'], hashlib.sha256(payload).hexdigest())
                self.assertEqual(literal, explicit)
                self.assertEqual(literal_report, explicit_report)
                with zipfile.ZipFile(io.BytesIO(literal)) as book:
                    body = ET.fromstring(book.read('EPUB/chapter-0001.xhtml')).find(f'{q(XHTML, "body")}/{q(XHTML, "div")}')
                self.assertEqual(body[0].tag, q(XHTML, 'pre'))
                self.assertEqual(body[0].text, source.replace('\r\n', '\n'))
