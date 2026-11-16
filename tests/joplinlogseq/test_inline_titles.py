# SPDX-License-Identifier: AGPL-3.0-only
"""Source-preserving contracts for one-line inline link/image titles."""

import unittest

from .synthetic import MISSING, NOTE_A, NOTE_B, RESOURCE
from notewrench.joplinlogseq.render import render_body


class InlineTitleTests(unittest.TestCase):
    def render(self, body):
        issues = []
        stats = dict(note_links=0, resource_links=0, checkboxes=0, protected_fences=0)
        result = render_body(body, NOTE_A,
                             {NOTE_B: dict(page='Target page', path='pages/Target page.md')},
                             {RESOURCE: 'assets/' + RESOURCE + '.png'}, issues, stats)
        return result, issues, stats

    def issue(self, code, target, line=1):
        return dict(code=code, note_id=NOTE_A, target=target, line=line)

    def test_escaped_quote_title_is_data(self):
        body = f'[target](:/{NOTE_B} "a \\"quoted\\" :/{MISSING}")'
        result, issues, stats = self.render(body)
        self.assertEqual(result, body.replace(f':/{NOTE_B}', '../pages/Target%20page.md'))
        self.assertEqual(issues, [self.issue('note_link_uses_file_path', NOTE_B)])
        self.assertEqual(stats, dict(note_links=1, resource_links=0, checkboxes=0, protected_fences=0))

    def test_escape_parity_preserves_layout_for_each_destination_kind(self):
        for image in ('', '!'):
            for target in (NOTE_B, RESOURCE, MISSING):
                for angle in (False, True):
                    for separator in (' ', '\t  '):
                        for escaped_count in (1, 3, 5):
                            for final_count in (0, 2, 4):
                                with self.subTest(image=image, target=target, angle=angle,
                                                  separator=separator, escaped=escaped_count,
                                                  final=final_count):
                                    slash = '\\' * escaped_count
                                    title = ('"a ' + slash + '"quoted' + slash +
                                             f'" :/{MISSING} ' + '\\' * final_count + '"')
                                    uri = f':/{target}#part'
                                    destination = '<' + uri + '>' if angle else uri
                                    body = ('before\n  ' + image + r'[a \] label]' + '(' +
                                            destination + separator + title + ')  \nafter')
                                    if target == NOTE_B:
                                        path = '../pages/Target%20page.md#part'
                                        diagnostics = [self.issue('note_link_uses_file_path', target, 2)]
                                    elif target == RESOURCE:
                                        path = '../assets/' + RESOURCE + '.png#part'
                                        diagnostics = []
                                    else:
                                        path = destination
                                        diagnostics = [self.issue('broken_link', target, 2)]
                                    # Existing inline angle wrappers are omitted in generated links.
                                    output = body.replace('(' + destination, '(' + path, 1)
                                    result, issues, stats = self.render(body)
                                    self.assertEqual(result, output)
                                    self.assertEqual(issues, diagnostics)
                                    self.assertEqual(stats, dict(note_links=int(target == NOTE_B),
                                        resource_links=int(target == RESOURCE), checkboxes=0, protected_fences=0))

    def test_title_code_html_and_link_syntax_are_literal_data(self):
        for title in (f'"`code :/{MISSING}` \\"quote\\" [inner](:/{NOTE_B})"',
                      f'"<a title=\'literal :/{MISSING}\'> \\"quote\\" <!-- :/{NOTE_B} -->"'):
            body = f'[target](:/{NOTE_B} {title}) [real](:/{NOTE_B})'
            expected = body.replace(f'[target](:/{NOTE_B}', '[target](../pages/Target%20page.md').replace(
                f'[real](:/{NOTE_B})', '[real]([[Target page]])')
            with self.subTest(title=title):
                result, issues, stats = self.render(body)
                self.assertEqual(result, expected)
                self.assertEqual(issues, [self.issue('note_link_uses_file_path', NOTE_B)])
                self.assertEqual((stats['note_links'], stats['resource_links']), (2, 0))

    def test_invalid_titles_keep_ordinary_uri_diagnostics(self):
        titles = ['"unterminated :/' + MISSING + '\\' * count + '"' for count in (1, 3)]
        titles += ['"early' + '\\' * count + '" junk :/' + MISSING + '"' for count in (0, 2, 4)]
        titles += [f'"no close :/{MISSING}', f'"ok" junk :/{MISSING}',
                   f"'single :/{MISSING}' junk", f'(unescaped (nested) :/{MISSING})']
        for title in titles:
            body = f'[bad](:/{NOTE_B} ' + title + ')'
            with self.subTest(title=title):
                result, issues, stats = self.render(body)
                self.assertEqual(result, body)
                self.assertEqual(issues, [self.issue(code, target) for code, target in (
                    ('unsupported_link_context', NOTE_B),
                    ('unsupported_link_context', MISSING), ('broken_link', MISSING))])
                self.assertEqual((stats['note_links'], stats['resource_links']), (0, 0))
        body = f'[bad](:/{NOTE_B} "multiline \\"quote\\"\n:/{MISSING}")'
        result, issues, stats = self.render(body)
        self.assertEqual(result, body)
        self.assertEqual(issues, [self.issue('unsupported_link_context', NOTE_B),
                                 self.issue('unsupported_link_context', MISSING, 2),
                                 self.issue('broken_link', MISSING, 2)])
        self.assertEqual(stats['note_links'], 0)

    def test_code_html_and_escaped_opening_protection(self):
        link = f'[target](:/{NOTE_B} "a \\"quoted\\" :/{MISSING}")'
        for body, diagnostics, fences in [
                ('~~~\n' + link + '\n~~~', [], 1),
                ('`' + link + '`', [], 0),
                ('    ' + link, [], 0),
                ('\\' + link, [], 0),
                (f"<a title='{link}'>HTML</a>", [self.issue(code, target) for code, target in (
                    ('unsupported_link_context', NOTE_B), ('unsupported_link_context', MISSING),
                    ('broken_link', MISSING))], 0),
                ('<!--\n' + link + '\n-->', [self.issue(code, target, 2) for code, target in (
                    ('unsupported_link_context', NOTE_B), ('unsupported_link_context', MISSING),
                    ('broken_link', MISSING))], 0)]:
            with self.subTest(body=body):
                result, issues, stats = self.render(body)
                self.assertEqual(result, body)
                self.assertEqual(issues, diagnostics)
                self.assertEqual(stats, dict(note_links=0, resource_links=0, checkboxes=0,
                                            protected_fences=fences))


if __name__ == '__main__':
    unittest.main()
