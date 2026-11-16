# SPDX-License-Identifier: AGPL-3.0-only
"""Closing horizontal padding belongs to the link, not its destination/title."""

import unittest

from .synthetic import MISSING, NOTE_A, NOTE_B, RESOURCE
from notewrench.joplinlogseq.render import render_body


class InlinePaddingTests(unittest.TestCase):
    def render(self, body):
        issues = []
        stats = dict(note_links=0, resource_links=0, checkboxes=0, protected_fences=0)
        result = render_body(body, NOTE_A,
                             {NOTE_B: dict(page='Target page', path='pages/Target page.md')},
                             {RESOURCE: 'assets/' + RESOURCE + '.png'}, issues, stats)
        return result, issues, stats

    def issue(self, code, target, line=1):
        return dict(code=code, note_id=NOTE_A, target=target, line=line)

    def test_padding_after_title_is_preserved_and_title_uri_is_data(self):
        body = f'[target](:/{NOTE_B} "tooltip :/{MISSING}" \t)'
        result, issues, stats = self.render(body)
        self.assertEqual(result, body.replace(f':/{NOTE_B}', '../pages/Target%20page.md'))
        self.assertEqual(issues, [self.issue('note_link_uses_file_path', NOTE_B)])
        self.assertEqual(stats, dict(note_links=1, resource_links=0, checkboxes=0, protected_fences=0))

    def test_padding_matrix_preserves_data_and_destination_mapping(self):
        for image in ('', '!'):
            for target in (NOTE_B, RESOURCE, MISSING):
                for angle in (False, True):
                    for fragment in ('', '#part'):
                        for title in ('', ' ""', f'\t "a \\"quoted\\" `:/{MISSING}` <b> :/{NOTE_B}"'):
                            for padding in (' ', '\t', ' \t  \t'):
                                with self.subTest(image=image, target=target, angle=angle,
                                                  fragment=fragment, title=title, padding=padding):
                                    uri = f':/{target}' + fragment
                                    destination = '<' + uri + '>' if angle else uri
                                    prefix = 'before\n  ' + image + f'[literal :/{MISSING} \\] label]('
                                    suffix = title + padding + ')  \nafter'
                                    body = prefix + destination + suffix
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
                                    result, issues, stats = self.render(body)
                                    self.assertEqual(result, prefix + path + suffix)
                                    self.assertEqual(issues, diagnostics)
                                    self.assertEqual(stats, dict(note_links=int(target == NOTE_B),
                                        resource_links=int(target == RESOURCE), checkboxes=0, protected_fences=0))

    def test_padded_title_retains_odd_and_even_backslash_parity(self):
        for escaped_count in (1, 3, 5):
            for final_count in (0, 2, 4):
                with self.subTest(escaped=escaped_count, final=final_count):
                    slash = '\\' * escaped_count
                    title = '"a ' + slash + '"quoted' + slash + f'" :/{MISSING} ' + '\\' * final_count + '"'
                    body = f'[target](:/{NOTE_B} ' + title + '\t )'
                    result, issues, stats = self.render(body)
                    self.assertEqual(result, body.replace(f':/{NOTE_B}', '../pages/Target%20page.md'))
                    self.assertEqual(issues, [self.issue('note_link_uses_file_path', NOTE_B)])
                    self.assertEqual(stats['note_links'], 1)

    def test_padding_does_not_rescue_malformed_wrappers_or_titles(self):
        for target in (NOTE_B, RESOURCE, MISSING):
            for image in ('', '!'):
                for middle in (f'<:/{target}', f':/{target}>', f'<<:/{target}>>',
                               f'<:/{target}>> "ok"', f':/{target} "unterminated',
                               f':/{target} "escaped\\"', f':/{target} "ok" junk',
                               f':/{target} junk', f':/{target}\t"early\\\\" junk"',
                               f':/{target}"attached"', f":/{target} 'single' junk",
                               f':/{target} (unescaped (nested))'):
                    with self.subTest(target=target, image=image, middle=middle):
                        body = 'before\n  ' + image + '[bad](' + middle + ' \t)'
                        result, issues, stats = self.render(body)
                        self.assertEqual(result, body)
                        expected = [self.issue('unsupported_link_context', target, 2)]
                        if target == MISSING:
                            expected.append(self.issue('broken_link', target, 2))
                        self.assertEqual(issues, expected)
                        self.assertEqual(stats, dict(note_links=0, resource_links=0,
                                                     checkboxes=0, protected_fences=0))

    def test_padding_does_not_expand_multiline_grammar(self):
        for body in (f'[bad](:/{NOTE_B} "multi\n:/{MISSING}" \t)',
                     f'[bad](:/{NOTE_B} "title" \t\n)', f'[bad](:/{NOTE_B}\n \t)'):
            with self.subTest(body=body):
                result, issues, stats = self.render(body)
                self.assertEqual(result, body)
                expected = [self.issue('unsupported_link_context', NOTE_B)]
                if MISSING in body:
                    expected += [self.issue('unsupported_link_context', MISSING, 2),
                                 self.issue('broken_link', MISSING, 2)]
                self.assertEqual(issues, expected)
                self.assertEqual(stats['note_links'], 0)

    def test_padded_links_keep_protection_and_recover_following_prose(self):
        link = f'[target](:/{NOTE_B} "literal :/{MISSING}" \t)'
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
        body = f'[bad](<:/{MISSING} \t) ' + link + f'\n[real](:/{NOTE_B}\t)'
        result, issues, stats = self.render(body)
        self.assertEqual(result, body.replace(link, link.replace(f':/{NOTE_B}', '../pages/Target%20page.md')).replace(
            f'[real](:/{NOTE_B}\t)', '[real]([[Target page]]\t)'))
        self.assertEqual(issues, [self.issue('unsupported_link_context', MISSING),
                                 self.issue('broken_link', MISSING),
                                 self.issue('note_link_uses_file_path', NOTE_B)])
        self.assertEqual(stats, dict(note_links=2, resource_links=0, checkboxes=0, protected_fences=0))


if __name__ == '__main__':
    unittest.main()
