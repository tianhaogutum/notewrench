# SPDX-License-Identifier: AGPL-3.0-only
"""Parenthesized inline titles are literal one-line data with escape parity."""

import unittest

from .synthetic import MISSING, NOTE_A, NOTE_B, RESOURCE
from notewrench.joplinlogseq.render import render_body


class InlineParenthesizedTitleTests(unittest.TestCase):
    def render(self, body):
        issues = []
        stats = dict(note_links=0, resource_links=0, checkboxes=0, protected_fences=0)
        result = render_body(body, NOTE_A,
                             {NOTE_B: dict(page='Target', path='pages/Target.md')},
                             {RESOURCE: 'assets/' + RESOURCE + '.png'}, issues, stats)
        return result, issues, stats

    def issue(self, code, target, line=1):
        return dict(code=code, note_id=NOTE_A, target=target, line=line)

    def test_selected_title_only_missing_uri(self):
        body = f'[target]( \t:/{NOTE_B}#part\\)tail (tooltip :/{NOTE_B} :/{MISSING} ) \t)'
        result, issues, stats = self.render(body)
        self.assertEqual(result, body.replace(':/'+NOTE_B, '../pages/Target.md', 1))
        self.assertEqual(issues, [self.issue('note_link_uses_file_path', NOTE_B)])
        self.assertEqual(stats, dict(note_links=1, resource_links=0, checkboxes=0, protected_fences=0))

    def test_destination_kinds_wrappers_padding_and_fragment_escapes(self):
        for marker in ('', '!'):
            for target in (NOTE_B, RESOURCE, MISSING):
                for angle in (False, True):
                    for fragment in ('', '#part', '#part\\)tail', '#part\\\\\\)tail'):
                        for leading, separator, padding in (('', ' ', ''), (' \t', '\t  ', ' \t')):
                            for title in ('()', f'(tooltip :/{NOTE_B} :/{MISSING} )'):
                                with self.subTest(marker=marker, target=target, angle=angle,
                                                  fragment=fragment, leading=leading, title=title):
                                    uri = ':/' + target + fragment
                                    destination = '<' + uri + '>' if angle else uri
                                    prefix = 'before\n  ' + marker + '[target](' + leading
                                    suffix = separator + title + padding + ')  \nafter'
                                    body = prefix + destination + suffix
                                    path = ('../pages/Target.md' if target == NOTE_B else
                                            '../assets/' + RESOURCE + '.png') + fragment
                                    result, issues, stats = self.render(body)
                                    self.assertEqual(result, prefix + (destination if target == MISSING else path) + suffix)
                                    expected = [] if target == RESOURCE else [self.issue(
                                        'broken_link' if target == MISSING else 'note_link_uses_file_path', target, 2)]
                                    self.assertEqual(issues, expected)
                                    self.assertEqual(stats, dict(note_links=int(target == NOTE_B),
                                        resource_links=int(target == RESOURCE), checkboxes=0, protected_fences=0))

    def test_parenthesis_escape_parity_preserves_every_byte(self):
        for escaped_count in (1, 3, 5):
            for closing_count in (0, 2, 4, 6):
                with self.subTest(escaped=escaped_count, closing=closing_count):
                    title = ('(a ' + '\\' * escaped_count + '(nested' + '\\' * escaped_count +
                             f') :/{MISSING} ' + '\\' * closing_count + ')')
                    body = f'[target]( \t<:/{NOTE_B}#part\\)tail>\t' + title + ' \t)'
                    result, issues, stats = self.render(body)
                    self.assertEqual(result, body.replace('<:/'+NOTE_B, '../pages/Target.md', 1).replace('tail>', 'tail', 1))
                    self.assertEqual(issues, [self.issue('note_link_uses_file_path', NOTE_B)])
                    self.assertEqual(stats['note_links'], 1)

    def test_complete_links_precede_label_title_code_html_and_uri_scanning(self):
        label = f'[label ` :/{MISSING} <b> :/{NOTE_B}]'
        for title in (f'(`code :/{MISSING}` [inner]\\(:/{NOTE_B}\\))',
                      f'(<a title="literal :/{MISSING}"> <!-- :/{NOTE_B} -->)',
                      f'(unpaired ` <!-- :/{MISSING} \\(nested\\) "double" \'single\' :/{NOTE_B})'):
            body = label + f'( \t:/{NOTE_B} ' + title + f' \t) [real](:/{NOTE_B})'
            expected = body.replace('( \t:/'+NOTE_B, '( \t../pages/Target.md', 1).replace(
                f'[real](:/{NOTE_B})', '[real]([[Target]])')
            result, issues, stats = self.render(body)
            self.assertEqual(result, expected)
            self.assertEqual(issues, [self.issue('note_link_uses_file_path', NOTE_B)])
            self.assertEqual(stats, dict(note_links=2, resource_links=0, checkboxes=0, protected_fences=0))

    def test_invalid_titles_do_not_consume_following_valid_links(self):
        titles = ['(unterminated', '(ok) junk', '(attached)', '(multi\nline)', '(ok)\n', '(unescaped (nested))']
        titles += ['(escaped' + '\\' * n + ')' for n in (1, 3, 5)]
        titles += ['(early' + '\\' * n + ') junk)' for n in (0, 2, 4, 6)]
        titles += ['(nested' + '\\' * n + '(open)' for n in (0, 2, 4, 6)]
        for target in (NOTE_B, RESOURCE, MISSING):
            for angle in (False, True):
                for title in titles:
                    # Without a fragment, an attached ( cannot be URI data.
                    fragment = '' if title == '(attached)' else '#part\\)tail'
                    uri = ':/' + target + fragment
                    destination = '<' + uri + '>' if angle else uri
                    separator = '' if title == '(attached)' else ' \t'
                    body = f'before\n  [bad]( \t{destination}' + separator + title + f') [real](:/{NOTE_B})'
                    with self.subTest(target=target, angle=angle, title=title):
                        result, issues, stats = self.render(body)
                        self.assertEqual(result, body.replace(f'[real](:/{NOTE_B})', '[real]([[Target]])'))
                        expected = [self.issue('unsupported_link_context', target, 2)]
                        if target == MISSING:
                            expected.append(self.issue('broken_link', target, 2))
                        self.assertEqual(issues, expected)
                        self.assertEqual(stats, dict(note_links=1, resource_links=0, checkboxes=0, protected_fences=0))

    def test_enclosing_code_html_and_escaped_openings_keep_boundaries(self):
        link = f'[target]( \t:/{NOTE_B} (literal :/{MISSING}) \t)'
        for body, line, fences in (
                ('~~~\n' + link + '\n~~~', None, 1), ('`' + link + '`', None, 0),
                ('    ' + link, None, 0), ('\\' + link, None, 0), ('!\\' + link, None, 0),
                ('<a title="' + link + '">HTML</a>', 1, 0), ('<!--\n' + link + '\n-->', 2, 0)):
            result, issues, stats = self.render(body)
            self.assertEqual(result, body)
            self.assertEqual(issues, [] if line is None else [
                self.issue('unsupported_link_context', NOTE_B, line),
                self.issue('unsupported_link_context', MISSING, line), self.issue('broken_link', MISSING, line)])
            self.assertEqual(stats, dict(note_links=0, resource_links=0, checkboxes=0, protected_fences=fences))
        for count in (1, 2, 3, 4):
            body = '\\' * count + '!' + link
            result, issues, stats = self.render(body)
            self.assertEqual(result, body.replace(':/'+NOTE_B, '../pages/Target.md', 1))
            self.assertEqual(issues, [self.issue('note_link_uses_file_path', NOTE_B)])
            self.assertEqual(stats['note_links'], 1)

    def test_bare_uris_and_all_reference_title_forms_are_unchanged(self):
        for title in (f'"literal :/{MISSING}"', f"'literal :/{MISSING}'", f'(literal \\(nested\\) :/{MISSING})'):
            body = f':/{NOTE_B}#part\\)tail\n[ref]: <:/{NOTE_B}#part\\)tail> ' + title + '\n[ref]'
            result, issues, stats = self.render(body)
            self.assertEqual(result, body.replace('<:/'+NOTE_B, '<../pages/Target.md', 1))
            self.assertEqual(issues, [self.issue('unsupported_link_context', NOTE_B),
                                      self.issue('note_link_uses_file_path', NOTE_B, 2)])
            self.assertEqual(stats['note_links'], 1)


if __name__ == '__main__':
    unittest.main()
