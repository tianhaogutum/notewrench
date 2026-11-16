# SPDX-License-Identifier: AGPL-3.0-only
"""Reference destination/title separators, with original-source diagnostics."""

import unittest

from .synthetic import MISSING, NOTE_A, NOTE_B, RESOURCE
from notewrench.joplinlogseq.render import render_body


class ReferenceSeparatorTests(unittest.TestCase):
    def render(self, body):
        issues = []
        stats = dict(note_links=0, resource_links=0, checkboxes=0, protected_fences=0)
        result = render_body(body, NOTE_A,
                             {NOTE_B: dict(page='Target page', path='pages/Target page.md')},
                             {RESOURCE: 'assets/' + RESOURCE + '.png'}, issues, stats)
        return result, issues, stats

    def issue(self, code, target, line=1):
        return dict(code=code, note_id=NOTE_A, target=target, line=line)

    def test_attached_title_is_not_a_definition(self):
        body = f'[target]: :/{NOTE_B}"tooltip"'
        result, issues, stats = self.render(body)
        self.assertEqual(result, body)
        self.assertEqual(issues, [self.issue('unsupported_link_context', NOTE_B)])
        self.assertEqual(stats, dict(note_links=0, resource_links=0, checkboxes=0, protected_fences=0))

    def test_attached_titles_preserve_bytes_and_report_all_uris(self):
        for opening, closing in [('"', '"'), ("'", "'"), ('(', ')')]:
            for target in (NOTE_B, RESOURCE, MISSING):
                for fragment in ('', '#part'):
                    for angle in (False, True):
                        for destination_break in (' \t', '\n    '):
                            uri = ':/' + target + fragment
                            destination = '<' + uri + '>' if angle else uri
                            title = opening + f'literal :/{MISSING}' + closing
                            body = '   [target]:' + destination_break + destination + title + ' \t'
                            line = 1 + destination_break.count('\n')
                            expected = [self.issue('unsupported_link_context', target, line)]
                            if target == MISSING:
                                expected.append(self.issue('broken_link', MISSING, line))
                            expected += [self.issue('unsupported_link_context', MISSING, line),
                                         self.issue('broken_link', MISSING, line)]
                            with self.subTest(body=body):
                                result, issues, stats = self.render(body)
                                self.assertEqual(result, body)
                                self.assertEqual(issues, expected)
                                self.assertEqual(stats, dict(note_links=0, resource_links=0,
                                                             checkboxes=0, protected_fences=0))

    def test_valid_separators_preserve_escaped_titles_as_data(self):
        for opening, closing in [('"', '"'), ("'", "'"), ('(', ')')]:
            # Odd backslash runs escape inner delimiters; the final pair leaves
            # the closing delimiter structural. The whole title is literal data.
            title = (opening + 'a \\' + opening + 'quoted\\' + closing +
                     f' `:/{MISSING}` <b> :/{NOTE_B} ' + '\\' * 2 + closing)
            for target, path in [(NOTE_B, '../pages/Target%20page.md'),
                                 (RESOURCE, '../assets/' + RESOURCE + '.png'), (MISSING, None)]:
                for fragment in ('', '#part'):
                    for angle in (False, True):
                        for destination_break in (' \t', '\n    '):
                            for separator in (' ', '\t', '\n', '  \n\t'):
                                uri = ':/' + target + fragment
                                destination = '<' + uri + '>' if angle else uri
                                prefix = f'   [literal :/{MISSING}]:' + destination_break
                                suffix = separator + title + ' \t\n'
                                body = prefix + destination + suffix
                                converted = path + fragment if path else uri
                                expected_destination = '<' + converted + '>' if angle else converted
                                expected_issues = []
                                if target in (NOTE_B, MISSING):
                                    expected_issues = [self.issue('note_link_uses_file_path' if path else
                                        'broken_link', target, 1 + destination_break.count('\n'))]
                                with self.subTest(body=body):
                                    result, issues, stats = self.render(body)
                                    self.assertEqual(result, prefix + expected_destination + suffix)
                                    self.assertEqual(issues, expected_issues)
                                    self.assertEqual(stats, dict(note_links=int(target == NOTE_B),
                                        resource_links=int(target == RESOURCE), checkboxes=0, protected_fences=0))

    def test_no_title_and_blank_line_boundaries(self):
        for destination_break in (' ', '\n\t'):
            for whitespace in ('', ' ', '\t', ' \t'):
                body = f'[target]:{destination_break}:/{NOTE_B}' + whitespace
                with self.subTest(body=body):
                    result, issues, stats = self.render(body)
                    self.assertEqual(result, body.replace(f':/{NOTE_B}', '../pages/Target%20page.md'))
                    self.assertEqual(issues, [self.issue('note_link_uses_file_path', NOTE_B,
                                                        1 + destination_break.count('\n'))])
                    self.assertEqual(stats['note_links'], 1)
        body = f'[target]: :/{NOTE_B}\n \t\n"literal :/{MISSING}"'
        result, issues, stats = self.render(body)
        self.assertEqual(result, body.replace(f':/{NOTE_B}', '../pages/Target%20page.md'))
        self.assertEqual(issues, [self.issue('note_link_uses_file_path', NOTE_B),
                                 self.issue('unsupported_link_context', MISSING, 3),
                                 self.issue('broken_link', MISSING, 3)])
        self.assertEqual(stats['note_links'], 1)

    def test_malformed_definition_does_not_consume_following_valid_link(self):
        for title in ('"tooltip"', "'tooltip'", '(tooltip)', '"unterminated', '"ok" junk'):
            body = f'[bad]:\n\t:/{MISSING}' + title + f'\n[real](:/{NOTE_B})'
            with self.subTest(body=body):
                result, issues, stats = self.render(body)
                self.assertEqual(result, body.replace(f'[real](:/{NOTE_B})', '[real]([[Target page]])'))
                self.assertEqual(issues, [self.issue('unsupported_link_context', MISSING, 2),
                                         self.issue('broken_link', MISSING, 2)])
                self.assertEqual(stats, dict(note_links=1, resource_links=0, checkboxes=0, protected_fences=0))

    def test_attached_titles_respect_enclosing_code_and_html(self):
        definition = f'[target]: :/{NOTE_B}"literal :/{MISSING}"'
        for body, line, fences in [('~~~\n' + definition + '\n~~~', None, 1),
                                   ('`' + definition + '`', None, 0),
                                   ('    ' + definition, None, 0),
                                   ('\t' + definition, None, 0),
                                   ('<!--\n' + definition + '\n-->', 2, 0),
                                   ("<a title='" + definition + "'>HTML</a>", 1, 0)]:
            with self.subTest(body=body):
                result, issues, stats = self.render(body)
                self.assertEqual(result, body)
                expected = [] if line is None else [self.issue(code, target, line) for code, target in (
                    ('unsupported_link_context', NOTE_B), ('unsupported_link_context', MISSING),
                    ('broken_link', MISSING))]
                self.assertEqual(issues, expected)
                self.assertEqual(stats, dict(note_links=0, resource_links=0, checkboxes=0, protected_fences=fences))


if __name__ == '__main__':
    unittest.main()
