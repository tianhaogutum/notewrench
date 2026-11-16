# SPDX-License-Identifier: AGPL-3.0-only
"""An escaped exclamation mark is literal prefix data before an inline link."""

import unittest

from .synthetic import MISSING, NOTE_A, NOTE_B, RESOURCE
from notewrench.joplinlogseq.render import render_body


class InlineImageMarkerTests(unittest.TestCase):
    def render(self, body):
        issues = []
        stats = dict(note_links=0, resource_links=0, checkboxes=0, protected_fences=0)
        result = render_body(body, NOTE_A,
                             {NOTE_B: dict(page='Target', path='pages/Target.md')},
                             {RESOURCE: 'assets/' + RESOURCE + '.png'}, issues, stats)
        return result, issues, stats

    def issue(self, code, target, line=1):
        return dict(code=code, note_id=NOTE_A, target=target, line=line)

    def test_escaped_marker_is_literal_before_note_link(self):
        body = f'\\![target]( \t:/{NOTE_B} \t)'
        result, issues, stats = self.render(body)
        self.assertEqual(result, '\\![target]( \t[[Target]] \t)')
        self.assertEqual(issues, [])
        self.assertEqual(stats, dict(note_links=1, resource_links=0,
                                    checkboxes=0, protected_fences=0))

    def test_marker_parity_preserves_prefix_and_destination_mapping(self):
        for count in range(7):
            for target in (NOTE_B, RESOURCE, MISSING):
                for angle in (False, True):
                    for fragment in ('', '#part'):
                        for title in ('', ' ""', f'\t "a \\"quote\\" `:/{MISSING}` <b>"'):
                            for leading, trailing in (('', ''), (' ', '\t'), ('\t ', ' \t')):
                                with self.subTest(count=count, target=target, angle=angle,
                                                  fragment=fragment, title=title,
                                                  leading=leading, trailing=trailing):
                                    uri = f':/{target}' + fragment
                                    destination = '<' + uri + '>' if angle else uri
                                    prefix = 'before\n  ' + '\\' * count + f'![literal :/{MISSING} \\] label](' + leading
                                    suffix = title + trailing + ')  \nafter'
                                    expected_issues = []
                                    if target == NOTE_B:
                                        fallback = count % 2 == 0 or bool(fragment or title)
                                        replacement = '../pages/Target.md' + fragment if fallback else '[[Target]]'
                                        if fallback:
                                            expected_issues = [self.issue('note_link_uses_file_path', target, 2)]
                                    elif target == RESOURCE:
                                        replacement = '../assets/' + RESOURCE + '.png' + fragment
                                    else:
                                        replacement = destination
                                        expected_issues = [self.issue('broken_link', target, 2)]
                                    result, issues, stats = self.render(prefix + destination + suffix)
                                    self.assertEqual(result, prefix + replacement + suffix)
                                    self.assertEqual(issues, expected_issues)
                                    self.assertEqual(stats, dict(note_links=int(target == NOTE_B),
                                        resource_links=int(target == RESOURCE), checkboxes=0, protected_fences=0))

    def test_escaped_opening_brackets_remain_literal(self):
        for marker_count in range(5):
            for bracket_count in (1, 3, 5):
                body = ('\\' * marker_count + '!' + '\\' * bracket_count +
                        f'[literal :/{MISSING}]( \t<:/{NOTE_B}> "data :/{MISSING}" \t)')
                with self.subTest(body=body):
                    result, issues, stats = self.render(body)
                    self.assertEqual(result, body)
                    self.assertEqual(issues, [])
                    self.assertEqual(stats, dict(note_links=0, resource_links=0,
                                                checkboxes=0, protected_fences=0))

    def test_code_html_and_original_line_numbers(self):
        link = f'\\![target]( \t:/{NOTE_B} "data :/{MISSING}" \t)'
        for body, line, fences in (
                ('~~~\n' + link + '\n~~~', None, 1),
                ('`' + link + '`', None, 0),
                ('    ' + link, None, 0),
                (f"<a title='{link}'>HTML</a>", 1, 0),
                ('<!--\n' + link + '\n-->', 2, 0)):
            with self.subTest(body=body):
                result, issues, stats = self.render(body)
                self.assertEqual(result, body)
                self.assertEqual(issues, [] if line is None else [
                    self.issue('unsupported_link_context', NOTE_B, line),
                    self.issue('unsupported_link_context', MISSING, line),
                    self.issue('broken_link', MISSING, line)])
                self.assertEqual(stats, dict(note_links=0, resource_links=0,
                                            checkboxes=0, protected_fences=fences))
        body = f'prefix\n\\![missing]( \t:/{MISSING} \t) \\![target]( \t:/{NOTE_B} \t)\n\\\\![image](:/{NOTE_B})'
        result, issues, stats = self.render(body)
        self.assertEqual(result, body.replace(f':/{NOTE_B} \t)', '[[Target]] \t)').replace(
            f'[image](:/{NOTE_B})', '[image](../pages/Target.md)'))
        self.assertEqual(issues, [self.issue('broken_link', MISSING, 2),
                                 self.issue('note_link_uses_file_path', NOTE_B, 3)])
        self.assertEqual(stats, dict(note_links=2, resource_links=0,
                                    checkboxes=0, protected_fences=0))

    def test_marker_escape_does_not_rescue_malformed_links(self):
        for count in (1, 2, 3):
            for target in (NOTE_B, RESOURCE, MISSING):
                for middle in (f'<:/{target}', f':/{target}>', f'<<:/{target}>>',
                               f':/{target} "unterminated', f':/{target} "escaped\\"',
                               f':/{target} "ok" junk', f':/{target} junk',
                               f':/{target}"attached"', f":/{target} 'single' junk",
                               f':/{target} (unescaped (nested))'):
                    body = 'before\n  ' + '\\' * count + '![bad]( \t' + middle + ' \t)'
                    with self.subTest(body=body):
                        result, issues, stats = self.render(body)
                        self.assertEqual(result, body)
                        expected = [self.issue('unsupported_link_context', target, 2)]
                        if target == MISSING:
                            expected.append(self.issue('broken_link', target, 2))
                        self.assertEqual(issues, expected)
                        self.assertEqual(stats, dict(note_links=0, resource_links=0,
                                                    checkboxes=0, protected_fences=0))

    def test_marker_escape_does_not_expand_multiline_grammar(self):
        for body in (f'\\![bad]( \t:/{NOTE_B} "multi\n:/{MISSING}" \t)',
                     f'\\![bad]( \t:/{NOTE_B}\n \t)'):
            result, issues, stats = self.render(body)
            self.assertEqual(result, body)
            expected = [self.issue('unsupported_link_context', NOTE_B)]
            if MISSING in body:
                expected += [self.issue('unsupported_link_context', MISSING, 2),
                             self.issue('broken_link', MISSING, 2)]
            self.assertEqual(issues, expected)
            self.assertEqual(stats, dict(note_links=0, resource_links=0,
                                        checkboxes=0, protected_fences=0))


if __name__ == '__main__':
    unittest.main()
