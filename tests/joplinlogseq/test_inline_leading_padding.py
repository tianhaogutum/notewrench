# SPDX-License-Identifier: AGPL-3.0-only
"""Opening spaces/tabs are link syntax; only the real destination is rewritten."""

import unittest

from .synthetic import MISSING, NOTE_A, NOTE_B, RESOURCE
from notewrench.joplinlogseq.render import render_body


class InlineLeadingPaddingTests(unittest.TestCase):
    def render(self, body):
        issues = []
        stats = dict(note_links=0, resource_links=0, checkboxes=0, protected_fences=0)
        result = render_body(body, NOTE_A,
                             {NOTE_B: dict(page='Target page', path='pages/Target page.md')},
                             {RESOURCE: 'assets/' + RESOURCE + '.png'}, issues, stats)
        return result, issues, stats

    def issue(self, code, target, line=1):
        return dict(code=code, note_id=NOTE_A, target=target, line=line)

    def test_leading_space_tab_recognizes_title_as_data(self):
        body = f'[target]( \t:/{NOTE_B} "tooltip :/{MISSING}" \t)'
        result, issues, stats = self.render(body)
        self.assertEqual(result, body.replace(f':/{NOTE_B}', '../pages/Target%20page.md'))
        self.assertEqual(issues, [self.issue('note_link_uses_file_path', NOTE_B)])
        self.assertEqual(stats, dict(note_links=1, resource_links=0, checkboxes=0, protected_fences=0))

    def test_leading_padding_matrix_preserves_bytes_and_mapping(self):
        for image in ('', '!'):
            for target in (NOTE_B, RESOURCE, MISSING):
                for angle in (False, True):
                    for fragment in ('', '#part'):
                        for title in ('', ' ""', f'\t "a \\"quoted\\" `:/{MISSING}` <b> :/{NOTE_B}"'):
                            for leading in (' ', '\t', ' \t  \t'):
                                for trailing in ('', ' \t'):
                                    with self.subTest(image=image, target=target, angle=angle,
                                                      fragment=fragment, title=title,
                                                      leading=leading, trailing=trailing):
                                        uri = f':/{target}' + fragment
                                        destination = '<' + uri + '>' if angle else uri
                                        prefix = 'before\n  ' + image + f'[literal :/{MISSING} \\] label](' + leading
                                        suffix = title + trailing + ')  \nafter'
                                        diagnostics = []
                                        if target == NOTE_B:
                                            fallback = bool(image or fragment or title)
                                            path = '../pages/Target%20page.md' + fragment if fallback else '[[Target page]]'
                                            if fallback:
                                                diagnostics = [self.issue('note_link_uses_file_path', target, 2)]
                                        elif target == RESOURCE:
                                            path = '../assets/' + RESOURCE + '.png' + fragment
                                        else:
                                            path = destination
                                            diagnostics = [self.issue('broken_link', target, 2)]
                                        result, issues, stats = self.render(prefix + destination + suffix)
                                        self.assertEqual(result, prefix + path + suffix)
                                        self.assertEqual(issues, diagnostics)
                                        self.assertEqual(stats, dict(note_links=int(target == NOTE_B),
                                            resource_links=int(target == RESOURCE), checkboxes=0, protected_fences=0))

    def test_leading_padding_preserves_title_escape_parity(self):
        for escaped_count in (1, 3, 5):
            for final_count in (0, 2, 4):
                with self.subTest(escaped=escaped_count, final=final_count):
                    slash = '\\' * escaped_count
                    title = '"a ' + slash + '"quoted' + slash + f'" :/{MISSING} ' + '\\' * final_count + '"'
                    body = f'[target](\t :/{NOTE_B} ' + title + '\t )'
                    result, issues, stats = self.render(body)
                    self.assertEqual(result, body.replace(f':/{NOTE_B}', '../pages/Target%20page.md'))
                    self.assertEqual(issues, [self.issue('note_link_uses_file_path', NOTE_B)])
                    self.assertEqual(stats['note_links'], 1)

    def test_leading_padding_does_not_rescue_malformed_links(self):
        for target in (NOTE_B, RESOURCE, MISSING):
            for image in ('', '!'):
                for middle in (f'<:/{target}', f':/{target}>', f'<<:/{target}>>',
                               f'<:/{target}>> "ok"', f':/{target} "unterminated',
                               f':/{target} "escaped\\"', f':/{target} "ok" junk',
                               f':/{target} junk', f':/{target}\t"early\\\\" junk"',
                               f':/{target}"attached"', f":/{target} 'single' junk",
                               f':/{target} (unescaped (nested))'):
                    with self.subTest(target=target, image=image, middle=middle):
                        body = 'before\n  ' + image + '[bad]( \t' + middle + ' \t)'
                        result, issues, stats = self.render(body)
                        self.assertEqual(result, body)
                        expected = [self.issue('unsupported_link_context', target, 2)]
                        if target == MISSING:
                            expected.append(self.issue('broken_link', target, 2))
                        self.assertEqual(issues, expected)
                        self.assertEqual(stats, dict(note_links=0, resource_links=0,
                                                     checkboxes=0, protected_fences=0))

    def test_leading_padding_does_not_expand_multiline_grammar(self):
        for body, expected in (
                (f'[bad]( \t:/{NOTE_B} "multi\n:/{MISSING}" \t)',
                 [self.issue('unsupported_link_context', NOTE_B),
                  self.issue('unsupported_link_context', MISSING, 2), self.issue('broken_link', MISSING, 2)]),
                (f'[bad]( \t:/{NOTE_B} "title" \t\n)', [self.issue('unsupported_link_context', NOTE_B)]),
                (f'[bad]( \t:/{NOTE_B}\n \t)', [self.issue('unsupported_link_context', NOTE_B)]),
                # A newline immediately after '(' is still ordinary source context.
                (f'[bad]( \t\n:/{NOTE_B})', [self.issue('unsupported_link_context', NOTE_B, 2)])):
            with self.subTest(body=body):
                result, issues, stats = self.render(body)
                self.assertEqual(result, body)
                self.assertEqual(issues, expected)
                self.assertEqual(stats['note_links'], 0)

    def test_leading_padding_keeps_protection_and_recovers_prose(self):
        link = f'[target]( \t:/{NOTE_B} "literal :/{MISSING}" \t)'
        for body, line, fences in (
                ('~~~\n' + link + '\n~~~', None, 1),
                ('`' + link + '`', None, 0), ('    ' + link, None, 0),
                ('\\' + link, None, 0),
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
        body = f'[bad]( \t:/{MISSING} junk) ' + link + f'\n[real](\t:/{NOTE_B}\t)\n:/'+RESOURCE
        result, issues, stats = self.render(body)
        self.assertEqual(result, body.replace(link, link.replace(f':/{NOTE_B}', '../pages/Target%20page.md')).replace(
            f'[real](\t:/{NOTE_B}\t)', '[real](\t[[Target page]]\t)').replace(
            '\n:/'+RESOURCE, '\n[asset](../assets/'+RESOURCE+'.png)'))
        self.assertEqual(issues, [self.issue('unsupported_link_context', MISSING),
                                 self.issue('broken_link', MISSING),
                                 self.issue('note_link_uses_file_path', NOTE_B)])
        self.assertEqual(stats, dict(note_links=2, resource_links=1, checkboxes=0, protected_fences=0))


if __name__ == '__main__':
    unittest.main()
