"""Original intraword-text source contracts and native/API provenance checks."""
import hashlib
from html import escape
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

FIXTURE = Path(__file__).parent / 'fixtures/markdown-intraword-text.json'


def normalized(source):
    return source.replace('\r\n', '\n').replace('\r', '\n')


class IntrawordTextTests(unittest.TestCase):
    def test_requested_example(self):
        body, events = render_markdown('read_notes and **saved**.')
        expected = ET.fromstring('<div><p>read_notes and <strong>saved</strong>.</p></div>')
        self.assertEqual(ET.tostring(body), ET.tostring(expected))
        self.assertEqual(events, [])

    def test_rendered_source_contracts(self):
        for case in json.loads(FIXTURE.read_text(encoding='utf-8'))['rendered']:
            with self.subTest(source=case['source']):
                body, events = render_markdown(case['source'])
                expected = ET.fromstring('<div>' + case['html'] + '</div>')
                self.assertEqual(ET.tostring(body), ET.tostring(expected))
                self.assertEqual(events, case.get('events', []))

    def test_atomic_literal_source_contracts(self):
        for source in json.loads(FIXTURE.read_text(encoding='utf-8'))['literal']:
            with self.subTest(source=source):
                body, events = render_markdown(source)
                self.assertEqual([child.tag for child in body], ['p'])
                self.assertEqual(len(body[0]), 0, 'rollback must discard every rendered token')
                self.assertEqual(body[0].text, normalized(source))
                self.assertEqual(events, [dict(
                    code='markdown_literal_inline', line_start=1,
                    line_end=source.count('\n') + 1,
                    reason='inline_syntax_outside_static_subset_or_raw_html')])

    def test_entities_and_token_precedence(self):
        for entity in ('&#0;', '&#xD800;', '&#x1;'):
            with self.subTest(entity=entity):
                for source in (f'a_b {entity}', f'a_b\nc_d {entity}'):
                    with self.assertRaises(BundleError):
                        render_markdown(source)
                for source in (f'a_b \\{entity}', f'a_b `{entity}`'):
                    body, events = render_markdown(source)
                    self.assertIn(entity, ''.join(body.itertext()))
                    self.assertEqual(events, [])

    def test_native_api_epub_report_crlf_and_literal_default(self):
        fixtures = json.loads(FIXTURE.read_text(encoding='utf-8'))
        cases = fixtures['rendered'] + [dict(source=s) for s in fixtures['literal']]
        source = '\r\n\r\n'.join(normalized(c['source']).replace('\n', '\r\n') for c in cases)
        expected = ET.fromstring('<div>' + ''.join(
            c.get('html', '<p>' + escape(normalized(c['source'])) + '</p>') for c in cases) + '</div>')
        literal_events, cleaning_events, line = [], [], 1
        for case in cases:
            end = line + normalized(case['source']).count('\n')
            if 'html' not in case:
                literal_events.append(dict(code='markdown_literal_inline', line_start=line,
                                           line_end=end, reason='inline_syntax_outside_static_subset_or_raw_html'))
            cleaning_events.extend(case.get('events', []))
            line = end + 2
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for api in (False, True):
                row = dict(title='Intraword text', tags=[], content=dict(type='text', text=source))
                if api:
                    row['id'] = 'intraword-texts'
                    row['content']['sourceUrl'] = 'https://example.invalid/notes/current'
                path = Path(directory) / ('api.json' if api else 'native.json')
                path.write_text(json.dumps(row if api else dict(bookmarks=[row], lists=[])), encoding='utf-8')
                paths.append(path)
            with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), \
                 patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS forbidden')):
                payload, report = prepare(paths, text_mode='markdown')
                repeat, repeat_report = prepare(paths, text_mode='markdown')
                validation = validate_epub(io.BytesIO(payload))
                literal, literal_report = prepare(paths)
                explicit, explicit_report = prepare(paths, text_mode='literal')
            self.assertEqual(payload, repeat)
            self.assertEqual(report, repeat_report)
            self.assertEqual(validation['chapters'], 2)
            self.assertEqual(validation['members'], 6)
            self.assertEqual(report['epub_sha256'], hashlib.sha256(payload).hexdigest())
            with zipfile.ZipFile(io.BytesIO(payload)) as book:
                for i, entry in enumerate(report['included'], 1):
                    self.assertEqual(entry['original_text'], source)
                    self.assertEqual(entry['original_text_sha256'], hashlib.sha256(source.encode()).hexdigest())
                    changes = [{k: v for k, v in e.items() if k != 'key'}
                               for e in report['changes'] if e['key'] == entry['key']]
                    self.assertEqual(changes, literal_events + cleaning_events)
                    chapter = ET.fromstring(book.read(f'EPUB/chapter-{i:04d}.xhtml'))
                    body = chapter.find(f'{q(XHTML, "body")}/{q(XHTML, "div")}')
                    for element in body.iter():
                        element.tag = element.tag.split('}')[-1]
                    self.assertEqual(ET.tostring(body), ET.tostring(expected))
                    self.assertEqual(list(body.iter('script')), [])
            self.assertEqual(literal, explicit)
            self.assertEqual(literal_report, explicit_report)
            with zipfile.ZipFile(io.BytesIO(literal)) as book:
                for i in (1, 2):
                    chapter = ET.fromstring(book.read(f'EPUB/chapter-{i:04d}.xhtml'))
                    body = chapter.find(f'{q(XHTML, "body")}/{q(XHTML, "div")}')
                    self.assertEqual(len(body), 1)
                    self.assertEqual(body[0].tag, q(XHTML, 'pre'))
                    self.assertEqual(len(body[0]), 0)
                    self.assertEqual(body[0].text, normalized(source))
