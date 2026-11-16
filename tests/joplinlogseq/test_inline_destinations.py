# SPDX-License-Identifier: AGPL-3.0-only
"""Balanced inline destination contracts, including conservative rejection."""

import unittest

from .synthetic import MISSING, NOTE_A, NOTE_B, RESOURCE
from notewrench.joplinlogseq.render import render_body


class InlineDestinationTests(unittest.TestCase):
    def render(self, body):
        issues = []
        stats = dict(note_links=0, resource_links=0, checkboxes=0, protected_fences=0)
        result = render_body(body, NOTE_A,
                             {NOTE_B: dict(page='Target page', path='pages/Target page.md')},
                             {RESOURCE: 'assets/' + RESOURCE + '.png'}, issues, stats)
        return result, issues, stats

    def issue(self, code, target, line=1):
        return dict(code=code, note_id=NOTE_A, target=target, line=line)

    def test_unmatched_opening_is_retained(self):
        body = f'[target](<:/{NOTE_B})'
        result, issues, stats = self.render(body)
        self.assertEqual(result, body)
        self.assertEqual(issues, [self.issue('unsupported_link_context', NOTE_B)])
        self.assertEqual(stats, dict(note_links=0, resource_links=0, checkboxes=0, protected_fences=0))

    def test_malformed_wrappers_are_never_rescued_by_bare_rewriting(self):
        for image in ('', '!'):
            for target in (NOTE_B, RESOURCE, MISSING):
                for fragment in ('', '#part'):
                    for title in ('', ' \t"a \\"quoted\\" title"'):
                        for opening, closing in (('<', ''), ('', '>'), ('<<', '>>'), ('<', '>>')):
                            with self.subTest(image=image, target=target, fragment=fragment,
                                              title=title, wrappers=(opening, closing)):
                                body = ('before\n  ' + image + '[target](' + opening +
                                        f':/{target}' + fragment + closing + title + ')  \nafter')
                                result, issues, stats = self.render(body)
                                self.assertEqual(result, body)
                                expected = [self.issue('unsupported_link_context', target, 2)]
                                if target == MISSING:
                                    expected.append(self.issue('broken_link', target, 2))
                                self.assertEqual(issues, expected)
                                self.assertEqual(stats, dict(note_links=0, resource_links=0,
                                                             checkboxes=0, protected_fences=0))

    def test_plain_and_paired_destinations_preserve_existing_behavior(self):
        for image in ('', '!'):
            for target in (NOTE_B, RESOURCE, MISSING):
                for angle in (False, True):
                    for fragment in ('', '#part'):
                        for title in ('', f' \t"a \\"quoted\\" `:/{MISSING}` <b> :/{NOTE_B}"'):
                            with self.subTest(image=image, target=target, angle=angle,
                                              fragment=fragment, title=title):
                                uri = f':/{target}' + fragment
                                destination = '<' + uri + '>' if angle else uri
                                body = 'before\n  ' + image + r'[a \] label]' + '(' + destination + title + ')  '
                                if target == NOTE_B:
                                    fallback = bool(image or fragment or title)
                                    path = '../pages/Target%20page.md' + fragment if fallback else '[[Target page]]'
                                    expected_issues = [self.issue('note_link_uses_file_path', target, 2)] if fallback else []
                                elif target == RESOURCE:
                                    path = '../assets/' + RESOURCE + '.png' + fragment
                                    expected_issues = []
                                else:
                                    path = destination
                                    expected_issues = [self.issue('broken_link', target, 2)]
                                expected = body.replace('(' + destination, '(' + path, 1)
                                result, issues, stats = self.render(body)
                                self.assertEqual(result, expected)
                                self.assertEqual(issues, expected_issues)
                                self.assertEqual(stats, dict(note_links=int(target == NOTE_B),
                                    resource_links=int(target == RESOURCE), checkboxes=0, protected_fences=0))

    def test_malformed_wrappers_keep_code_html_and_source_line_boundaries(self):
        bad = f'[bad](<:/{MISSING})'
        for body, line, fences in [
                ('~~~\n' + bad + '\n~~~', None, 1),
                ('`' + bad + '`', None, 0),
                ('    ' + bad, None, 0),
                (f'<a title="{bad}">HTML</a>', 1, 0),
                ('<!--\n' + bad + '\n-->', 2, 0)]:
            with self.subTest(body=body):
                result, issues, stats = self.render(body)
                self.assertEqual(result, body)
                self.assertEqual(issues, [] if line is None else [
                    self.issue('unsupported_link_context', MISSING, line),
                    self.issue('broken_link', MISSING, line)])
                self.assertEqual(stats, dict(note_links=0, resource_links=0,
                                            checkboxes=0, protected_fences=fences))
        body = f'[bad](<:/{NOTE_B}) [real](:/{NOTE_B})\n[bad](:/{MISSING}>)'
        result, issues, stats = self.render(body)
        self.assertEqual(result, body.replace(f'[real](:/{NOTE_B})', '[real]([[Target page]])'))
        self.assertEqual(issues, [self.issue('unsupported_link_context', NOTE_B),
                                 self.issue('unsupported_link_context', MISSING, 2),
                                 self.issue('broken_link', MISSING, 2)])
        self.assertEqual(stats, dict(note_links=1, resource_links=0, checkboxes=0, protected_fences=0))


if __name__ == '__main__':
    unittest.main()
