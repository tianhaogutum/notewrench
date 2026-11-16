# SPDX-License-Identifier: AGPL-3.0-only
"""Source-preserving contracts for adjacent-line reference definitions."""

import unittest

from .synthetic import MISSING, NOTE_A, NOTE_B, RESOURCE
from notewrench.joplinlogseq.render import render_body


class MultilineReferenceTests(unittest.TestCase):
    def render(self, body):
        issues = []
        stats = dict(note_links=0, resource_links=0, checkboxes=0, protected_fences=0)
        result = render_body(body, NOTE_A,
                             {NOTE_B: {'page': 'Target page', 'path': 'pages/Target page.md'}},
                             {RESOURCE: 'assets/' + RESOURCE + '.png'}, issues, stats)
        return result, issues, stats

    def test_next_line_destinations_preserve_all_surrounding_whitespace(self):
        body = (f'[full][page] [page][] [page] ![image][asset]\n'
                f'   [page]: \t\n    <:/{NOTE_B}#part>  \n'
                f'\t"page title"  \n'
                f'[asset]:\n\t:/{RESOURCE}#pixel \'image title\'\t\n'
                f'[unindented]:\n:/{NOTE_B}\n'
                f'[three spaces]:\n   :/{NOTE_B} (section)\n')
        expected = body.replace(f':/{NOTE_B}', '../pages/Target%20page.md').replace(
            f':/{RESOURCE}', '../assets/' + RESOURCE + '.png')
        result, issues, stats = self.render(body)
        self.assertEqual(result, expected)
        self.assertEqual(issues, [
            dict(code='note_link_uses_file_path', note_id=NOTE_A, target=NOTE_B, line=line)
            for line in (3, 8, 10)])
        self.assertEqual((stats['note_links'], stats['resource_links']), (3, 1))

    def test_next_line_titles_are_data_including_internal_uris(self):
        body = (f'[page]: :/{NOTE_B}\n'
                f'  "`literal` <a title=\'text\'> :/{MISSING}"\t\n'
                f'[asset]:\n :/{RESOURCE}\n'
                f"'image :/{MISSING}'  \n"
                f'[escaped\\]label]:\n  <:/{NOTE_B}>\n'
                f'    (section :/{MISSING})')
        expected = body.replace(f':/{NOTE_B}', '../pages/Target%20page.md').replace(
            f':/{RESOURCE}', '../assets/' + RESOURCE + '.png')
        result, issues, stats = self.render(body)
        self.assertEqual(result, expected)
        self.assertEqual(issues, [
            dict(code='note_link_uses_file_path', note_id=NOTE_A, target=NOTE_B, line=line)
            for line in (1, 7)])
        self.assertEqual((stats['note_links'], stats['resource_links']), (2, 1))

    def test_missing_and_malformed_destinations_report_the_uri_line(self):
        body = (f'[missing]:\n  <:/{MISSING}#part>\n  "title"\n'
                f'[bad-angle]:\n  <:/{NOTE_B}\n'
                f'[bad-title]:\n  :/{MISSING} "unterminated\n'
                f'[junk]:\n\t:/{NOTE_B} extra text\n'
                f'[real](:/{NOTE_B})')
        result, issues, stats = self.render(body)
        self.assertEqual(result, body.replace(f'[real](:/{NOTE_B})', '[real]([[Target page]])'))
        self.assertEqual(issues, [
            dict(code=code, note_id=NOTE_A, target=target, line=line)
            for code, target, line in (
                ('broken_link', MISSING, 2),
                ('unsupported_link_context', NOTE_B, 5),
                ('unsupported_link_context', MISSING, 7), ('broken_link', MISSING, 7),
                ('unsupported_link_context', NOTE_B, 9))])
        self.assertEqual((stats['note_links'], stats['resource_links']), (1, 0))

    def test_blank_lines_code_and_html_do_not_become_definitions(self):
        body = (f'[blank]:\n \t\n    :/{MISSING}\n'
                f'    [code]:\n    :/{MISSING}\n'
                f'~~~\n[fenced]:\n:/{MISSING}\n~~~\n'
                f'<!--\n[html]:\n  :/{MISSING}\n-->\n'
                f'[after](:/{NOTE_B})')
        result, issues, stats = self.render(body)
        self.assertEqual(result, body.replace(f'[after](:/{NOTE_B})', '[after]([[Target page]])'))
        self.assertEqual(issues, [
            dict(code=code, note_id=NOTE_A, target=MISSING, line=12)
            for code in ('unsupported_link_context', 'broken_link')])
        self.assertEqual(stats, dict(note_links=1, resource_links=0, checkboxes=0, protected_fences=1))

    def test_escaped_quote_adjacent_title_is_data(self):
        body = f'[target]:\n  :/{NOTE_B}\n  "a \\"quoted\\" :/{MISSING}"'
        result, issues, stats = self.render(body)
        self.assertEqual(result, body.replace(f':/{NOTE_B}', '../pages/Target%20page.md'))
        self.assertEqual(issues, [dict(code='note_link_uses_file_path', note_id=NOTE_A,
                                      target=NOTE_B, line=2)])
        self.assertEqual(stats, dict(note_links=1, resource_links=0, checkboxes=0, protected_fences=0))

    def test_escaped_title_delimiters_and_backslash_parity(self):
        for opening, closing in [('"', '"'), ("'", "'"), ('(', ')')]:
            for destination_break in (' \t', '\n    '):
                for title_break in (' \t', '  \n\t'):
                    for escaped_count in (1, 3, 5):
                        for final_count in (0, 2, 4, 6):
                            with self.subTest(delimiters=opening + closing,
                                              destination=destination_break, title=title_break,
                                              escaped=escaped_count, final=final_count):
                                slash = '\\' * escaped_count
                                title = (opening + 'literal ' + slash + opening + 'quoted' +
                                         slash + closing + f' `:/{NOTE_B}` :/{MISSING} ' +
                                         '\\' * final_count + closing)
                                body = (f'   [target]:{destination_break}<:/{NOTE_B}#part>' +
                                        title_break + title + ' \t\n')
                                result, issues, stats = self.render(body)
                                self.assertEqual(result, body.replace(
                                    f'<:/{NOTE_B}#part>', '<../pages/Target%20page.md#part>'))
                                self.assertEqual(issues, [dict(code='note_link_uses_file_path',
                                    note_id=NOTE_A, target=NOTE_B, line=1 + destination_break.count('\n'))])
                                self.assertEqual((stats['note_links'], stats['resource_links']), (1, 0))

    def test_escaped_titles_on_resource_and_missing_destinations(self):
        body = (f"[asset]:\n\t<:/{RESOURCE}#pixel> 'a \\'quote\\' :/{MISSING}'\t\n"
                f'[missing]:\n  :/{MISSING}#part\n'
                f'    (a \\(section\\) :/{NOTE_B} :/{MISSING})')
        result, issues, stats = self.render(body)
        self.assertEqual(result, body.replace(f':/{RESOURCE}', '../assets/' + RESOURCE + '.png'))
        self.assertEqual(issues, [dict(code='broken_link', note_id=NOTE_A, target=MISSING, line=4)])
        self.assertEqual((stats['note_links'], stats['resource_links']), (0, 1))

    def test_invalid_escaped_titles_do_not_hide_uri_issues(self):
        for opening, closing in [('"', '"'), ("'", "'"), ('(', ')')]:
            titles = [opening + f'bad :/{MISSING}' + '\\' * count + closing for count in (1, 3)]
            titles += [opening + 'early' + '\\' * count + closing + f' junk :/{MISSING} ' + closing
                       for count in (0, 2, 4)]
            titles += [opening + f'bad :/{MISSING}', opening + 'ok' + closing + f' junk :/{MISSING}']
            if opening == '(':
                titles.append(f'(unescaped (nested) :/{MISSING})')
            for title in titles:
                with self.subTest(title=title):
                    body = f'[bad]: :/{NOTE_B} ' + title
                    result, issues, stats = self.render(body)
                    self.assertEqual(result, body)
                    self.assertEqual(issues, [dict(code=code, note_id=NOTE_A, target=target, line=1)
                        for code, target in [('unsupported_link_context', NOTE_B),
                                             ('unsupported_link_context', MISSING), ('broken_link', MISSING)]])
                    self.assertEqual((stats['note_links'], stats['resource_links']), (0, 0))

    def test_invalid_adjacent_titles_and_blank_lines_use_ordinary_contexts(self):
        titles = [f'"unterminated \\" :/{MISSING}" junk',
                  f'"a \\"quoted\\" :/{MISSING}"',
                  f'"multiline \\"\n:/{MISSING}"']
        for title in titles:
            blank = '\n' if title == titles[1] else ''
            body = f'[target]:\n  :/{NOTE_B}\n' + blank + '  ' + title
            with self.subTest(body=body):
                result, issues, stats = self.render(body)
                self.assertEqual(result, body.replace(f':/{NOTE_B}', '../pages/Target%20page.md'))
                self.assertEqual(issues, [dict(code=code, note_id=NOTE_A, target=target, line=line)
                    for code, target, line in [('note_link_uses_file_path', NOTE_B, 2),
                        ('unsupported_link_context', MISSING, 3 if title == titles[0] else 4),
                        ('broken_link', MISSING, 3 if title == titles[0] else 4)]])
                self.assertEqual((stats['note_links'], stats['resource_links']), (1, 0))

    def test_escaped_titles_respect_code_and_html_protection(self):
        definition = f'[target]: :/{NOTE_B} "a \\"quoted\\" :/{MISSING}"'
        for body, expected_issues, fences in [
                ('~~~\n' + definition + '\n~~~', [], 1),
                ('`' + definition + '`', [], 0),
                ('    ' + definition, [], 0),
                ('<!--\n' + definition + '\n-->', [
                    dict(code=code, note_id=NOTE_A, target=target, line=2)
                    for code, target in [('unsupported_link_context', NOTE_B),
                        ('unsupported_link_context', MISSING), ('broken_link', MISSING)]], 0)]:
            with self.subTest(body=body):
                result, issues, stats = self.render(body)
                self.assertEqual(result, body)
                self.assertEqual(issues, expected_issues)
                self.assertEqual(stats, dict(note_links=0, resource_links=0, checkboxes=0,
                                            protected_fences=fences))


if __name__ == '__main__':
    unittest.main()
