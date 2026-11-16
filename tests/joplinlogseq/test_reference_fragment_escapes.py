# SPDX-License-Identifier: AGPL-3.0-only
"""Reference fragment boundaries must preserve labels/titles as literal data."""

import unittest

from .synthetic import MISSING, NOTE_A, NOTE_B, RESOURCE
from notewrench.joplinlogseq.render import render_body


class ReferenceFragmentEscapeTests(unittest.TestCase):
    def render(self, body):
        issues = []
        stats = dict(note_links=0, resource_links=0, checkboxes=0, protected_fences=0)
        result = render_body(body, NOTE_A,
                             {NOTE_B: dict(page='Target', path='pages/Target.md')},
                             {RESOURCE: 'assets/' + RESOURCE + '.png'}, issues, stats)
        return result, issues, stats

    def issue(self, code, target, line=1):
        return dict(code=code, note_id=NOTE_A, target=target, line=line)

    def test_escaped_fragment_closer_keeps_title_uris_as_data(self):
        body = f'[target]: \t:/{NOTE_B}#part\\)tail "tooltip :/{NOTE_B} :/{MISSING} " \t'
        result, issues, stats = self.render(body)
        self.assertEqual(result, body.replace(f':/{NOTE_B}', '../pages/Target.md', 1))
        self.assertEqual(issues, [self.issue('note_link_uses_file_path', NOTE_B)])
        self.assertEqual(stats, dict(note_links=1, resource_links=0, checkboxes=0, protected_fences=0))

    def test_odd_runs_preserve_definition_data_and_source_lines(self):
        uses = '[full][target] [target][] [target] ![image][target]\n'
        for target, path in ((NOTE_B, '../pages/Target.md'),
                             (RESOURCE, '../assets/' + RESOURCE + '.png'), (MISSING, None)):
            for count in (1, 3, 5):
                for tail in ('tail', '', 'tail\\)end'):
                    fragment = '#part' + '\\' * count + ')' + tail
                    uri = ':/' + target + fragment
                    for angle in (False, True):
                        destination = '<' + uri + '>' if angle else uri
                        converted = (path + fragment) if path else uri
                        replacement = ('<' + converted + '>') if angle else converted
                        for before in ('', ' \t', '\n', ' \t\n    ', '\n\t'):
                            for separator in (' \t', '\n', ' \t\n\t'):
                                for opening, closing in (('"', '"'), ("'", "'"), ('(', ')')):
                                    title = (opening + 'a \\' + opening + 'quote\\' + closing +
                                             f' `:/{MISSING}` <b> :/{NOTE_B} ' + '\\' * 2 + closing)
                                    prefix = uses + f'   [literal :/{MISSING} \\] ` <b>]:'+ before
                                    suffix = separator + title + ' \t\n' + uses
                                    with self.subTest(target=target, count=count, tail=tail,
                                                      angle=angle, before=before, title=title,
                                                      separator=separator):
                                        result, issues, stats = self.render(prefix + destination + suffix)
                                        self.assertEqual(result, prefix + replacement + suffix)
                                        expected = [] if target == RESOURCE else [self.issue(
                                            'broken_link' if path is None else 'note_link_uses_file_path',
                                            target, 2 + before.count('\n'))]
                                        self.assertEqual(issues, expected)
                                        self.assertEqual(stats, dict(note_links=int(target == NOTE_B),
                                            resource_links=int(target == RESOURCE), checkboxes=0,
                                            protected_fences=0))

    def test_no_title_and_even_terminal_backslashes(self):
        for target, path in ((NOTE_B, '../pages/Target.md'),
                             (RESOURCE, '../assets/' + RESOURCE + '.png'), (MISSING, None)):
            for count in (0, 2, 4, 6):
                for angle in (False, True):
                    fragment = '#part\\)tail' + '\\' * count
                    uri = ':/' + target + fragment
                    destination = '<' + uri + '>' if angle else uri
                    body = '[target]:\n\t' + destination + ' \t\n'
                    with self.subTest(body=body):
                        result, issues, stats = self.render(body)
                        self.assertEqual(result, body.replace(uri, path + fragment) if path else body)
                        self.assertEqual(issues, [] if target == RESOURCE else [self.issue(
                            'note_link_uses_file_path' if path else 'broken_link', target, 2)])
                        self.assertEqual(stats, dict(note_links=int(target == NOTE_B),
                            resource_links=int(target == RESOURCE), checkboxes=0, protected_fences=0))

    def test_even_runs_do_not_escape_fragment_closer(self):
        for target in (NOTE_B, RESOURCE, MISSING):
            for count in (0, 2, 4, 6):
                for angle in (False, True):
                    for tail in ('tail', ''):
                        uri = ':/' + target + '#part' + '\\' * count + ')' + tail
                        destination = '<' + uri + '>' if angle else uri
                        body = '[bad]:\n    ' + destination + f' "literal :/{MISSING}"'
                        with self.subTest(body=body):
                            result, issues, stats = self.render(body)
                            self.assertEqual(result, body)
                            expected = [self.issue('unsupported_link_context', target, 2)]
                            if target == MISSING:
                                expected.append(self.issue('broken_link', target, 2))
                            expected += [self.issue('unsupported_link_context', MISSING, 2),
                                         self.issue('broken_link', MISSING, 2)]
                            self.assertEqual(issues, expected)
                            self.assertEqual(stats, dict(note_links=0, resource_links=0,
                                                        checkboxes=0, protected_fences=0))

    def test_escaped_closer_does_not_rescue_malformed_definition(self):
        for target in (NOTE_B, RESOURCE, MISSING):
            uri = f':/{target}#part\\)tail'
            for destination in ('<' + uri, uri + '>', '<<' + uri + '>>',
                                uri + '"attached"', uri + "'attached'", uri + '(attached)',
                                uri + ' "ok" junk', uri + ' "unterminated',
                                uri + ' "escaped\\"', uri + ' "early\\\\" junk"',
                                uri + ' (unescaped (nested))', uri + ' "multi\nline"'):
                body = '[bad]:\n  ' + destination + f'\n[real](:/{NOTE_B})'
                with self.subTest(body=body):
                    result, issues, stats = self.render(body)
                    self.assertEqual(result, body.replace(f'[real](:/{NOTE_B})', '[real]([[Target]])'))
                    expected = [self.issue('unsupported_link_context', target, 2)]
                    if target == MISSING:
                        expected.append(self.issue('broken_link', target, 2))
                    self.assertEqual(issues, expected)
                    self.assertEqual(stats, dict(note_links=1, resource_links=0,
                                                checkboxes=0, protected_fences=0))

    def test_code_html_and_blank_line_boundaries(self):
        definition = f'[target]: :/{NOTE_B}#part\\)tail "literal :/{MISSING}"'
        for body, line, fences in (
                ('~~~\n' + definition + '\n~~~', None, 1),
                ('`' + definition + '`', None, 0), ('    ' + definition, None, 0),
                ('\t' + definition, None, 0),
                ("<a title='" + definition + "'>HTML</a>", 1, 0),
                ('<!--\n' + definition + '\n-->', 2, 0)):
            with self.subTest(body=body):
                result, issues, stats = self.render(body)
                self.assertEqual(result, body)
                self.assertEqual(issues, [] if line is None else [
                    self.issue('unsupported_link_context', NOTE_B, line),
                    self.issue('unsupported_link_context', MISSING, line),
                    self.issue('broken_link', MISSING, line)])
                self.assertEqual(stats, dict(note_links=0, resource_links=0,
                                            checkboxes=0, protected_fences=fences))
        body = f'[target]:\n \t\n    :/{MISSING}#part\\)tail "title"'
        self.assertEqual(self.render(body), (body, [], dict(note_links=0, resource_links=0,
                                                           checkboxes=0, protected_fences=0)))
        body = f'[target]: :/{NOTE_B}#part\\)tail\n \t\n"literal :/{MISSING}"'
        result, issues, stats = self.render(body)
        self.assertEqual(result, body.replace(f':/{NOTE_B}', '../pages/Target.md'))
        self.assertEqual(issues, [self.issue('note_link_uses_file_path', NOTE_B),
                                 self.issue('unsupported_link_context', MISSING, 3),
                                 self.issue('broken_link', MISSING, 3)])
        self.assertEqual(stats['note_links'], 1)

    def test_inline_and_bare_fragment_rules_stay_separate(self):
        body = (f'[inline]( :/{NOTE_B}#part\\)tail "literal :/{MISSING}" )\n'
                f':/{NOTE_B}#part\\)tail\n:/{MISSING}#part\\)tail')
        result, issues, stats = self.render(body)
        self.assertEqual(result, body.replace(f':/{NOTE_B}', '../pages/Target.md', 1))
        self.assertEqual(issues, [self.issue('note_link_uses_file_path', NOTE_B),
                                 self.issue('unsupported_link_context', NOTE_B, 2),
                                 self.issue('unsupported_link_context', MISSING, 3),
                                 self.issue('broken_link', MISSING, 3)])
        self.assertEqual(stats, dict(note_links=1, resource_links=0, checkboxes=0, protected_fences=0))


if __name__ == '__main__':
    unittest.main()
