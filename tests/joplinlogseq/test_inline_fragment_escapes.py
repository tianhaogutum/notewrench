# SPDX-License-Identifier: AGPL-3.0-only
"""Escaped fragment closers must not expose inline label/title data to scanning."""

import unittest

from .synthetic import MISSING, NOTE_A, NOTE_B, RESOURCE
from notewrench.joplinlogseq.render import render_body


class InlineFragmentEscapeTests(unittest.TestCase):
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
        body = f'[target]( \t:/{NOTE_B}#part\\)tail "tooltip :/{NOTE_B} :/{MISSING} " \t)'
        result, issues, stats = self.render(body)
        self.assertEqual(result, body.replace(f':/{NOTE_B}', '../pages/Target.md', 1))
        self.assertEqual(issues, [self.issue('note_link_uses_file_path', NOTE_B)])
        self.assertEqual(stats, dict(note_links=1, resource_links=0, checkboxes=0, protected_fences=0))

    def test_odd_fragment_escape_runs_preserve_mapping_and_data(self):
        for marker in ('', '!', '\\!', '\\\\!'):
            for target in (NOTE_B, RESOURCE, MISSING):
                for angle in (False, True):
                    for count in (1, 3, 5):
                        for tail in ('tail', '', 'tail\\)end'):
                            for title in ('', ' ""', f'\t"a \\"quote\\" `:/{MISSING}` <b> :/{NOTE_B}"'):
                                with self.subTest(marker=marker, target=target, angle=angle,
                                                  count=count, tail=tail, title=title):
                                    fragment = '#part' + '\\' * count + ')' + tail
                                    uri = f':/{target}' + fragment
                                    destination = '<' + uri + '>' if angle else uri
                                    prefix = 'before\n  ' + marker + f'[literal :/{MISSING} \\] label]( \t'
                                    suffix = title + ' \t)  \nafter'
                                    if target == NOTE_B:
                                        replacement = '../pages/Target.md' + fragment
                                        diagnostics = [self.issue('note_link_uses_file_path', target, 2)]
                                    elif target == RESOURCE:
                                        replacement = '../assets/' + RESOURCE + '.png' + fragment
                                        diagnostics = []
                                    else:
                                        replacement = destination
                                        diagnostics = [self.issue('broken_link', target, 2)]
                                    result, issues, stats = self.render(prefix + destination + suffix)
                                    self.assertEqual(result, prefix + replacement + suffix)
                                    self.assertEqual(issues, diagnostics)
                                    self.assertEqual(stats, dict(note_links=int(target == NOTE_B),
                                        resource_links=int(target == RESOURCE), checkboxes=0, protected_fences=0))

    def test_even_fragment_escape_runs_keep_structural_closure(self):
        for count in (0, 2, 4, 6):
            for marker in ('', '!'):
                for target in (NOTE_B, RESOURCE, MISSING):
                    with self.subTest(count=count, marker=marker, target=target):
                        fragment = '#part' + '\\' * count
                        prefix = marker + '[target]( \t'
                        # The first ) closes the link; what follows is ordinary prose.
                        suffix = ')tail :/' + NOTE_B + ' :/' + MISSING + ' )'
                        destination = f':/{target}' + fragment
                        if target == NOTE_B:
                            replacement = '../pages/Target.md' + fragment
                            diagnostics = [self.issue('note_link_uses_file_path', target)]
                        elif target == RESOURCE:
                            replacement = '../assets/' + RESOURCE + '.png' + fragment
                            diagnostics = []
                        else:
                            replacement = destination
                            diagnostics = [self.issue('broken_link', target)]
                        result, issues, stats = self.render(prefix + destination + suffix)
                        self.assertEqual(result, prefix + replacement + suffix.replace(':/'+NOTE_B, '[[Target]]'))
                        self.assertEqual(issues, diagnostics + [self.issue('broken_link', MISSING)])
                        self.assertEqual(stats, dict(note_links=1 + int(target == NOTE_B),
                            resource_links=int(target == RESOURCE), checkboxes=0, protected_fences=0))
        # Backtracking must not reinterpret an even run as escaping ) to find a later >.
        for count in (0, 2, 4):
            body = f'[bad](<:/{NOTE_B}#part' + '\\' * count + ')tail> "title")'
            result, issues, stats = self.render(body)
            self.assertEqual(result, body)
            self.assertEqual(issues, [self.issue('unsupported_link_context', NOTE_B)])
            self.assertEqual(stats['note_links'], 0)

    def test_fragment_escapes_keep_title_quote_parity(self):
        for count in (1, 3, 5):
            for final in (0, 2, 4):
                with self.subTest(count=count, final=final):
                    slash = '\\' * count
                    title = '"a ' + slash + '"quoted' + slash + f'" :/{MISSING} ' + '\\' * final + '"'
                    body = f'[target]( \t:/{NOTE_B}#part\\)tail ' + title + '\t )'
                    result, issues, stats = self.render(body)
                    self.assertEqual(result, body.replace(f':/{NOTE_B}', '../pages/Target.md', 1))
                    self.assertEqual(issues, [self.issue('note_link_uses_file_path', NOTE_B)])
                    self.assertEqual(stats['note_links'], 1)

    def test_fragment_escape_does_not_rescue_malformed_inline_links(self):
        for target in (NOTE_B, RESOURCE, MISSING):
            uri = f':/{target}#part\\)tail'
            for middle, closing in ((uri, ''), ('<' + uri, ')'), (uri + '>', ')'),
                                    ('<<' + uri + '>>', ')'), (uri + ' "ok" junk', ')'),
                                    (uri + ' "unterminated', ')'), (uri + ' "escaped\\"', ')'),
                                    (uri + ' "early\\\\" junk"', ')'), (uri + '"attached"', ')'),
                                    (uri + " 'single' junk", ')'), (uri + ' (unescaped (nested))', ')'),
                                    (uri + ' "multi\nline"', ')'), (uri + '\n', ')'),
                                    (uri + ' "ok" \n', ')')):
                with self.subTest(target=target, middle=middle, closing=closing):
                    body = 'before\n  [bad]( \t' + middle + closing
                    result, issues, stats = self.render(body)
                    self.assertEqual(result, body)
                    diagnostics = [self.issue('unsupported_link_context', target, 2)]
                    if target == MISSING:
                        diagnostics.append(self.issue('broken_link', target, 2))
                    self.assertEqual(issues, diagnostics)
                    self.assertEqual(stats, dict(note_links=0, resource_links=0, checkboxes=0, protected_fences=0))

    def test_fragment_escape_keeps_context_protection_and_original_lines(self):
        link = f'[target]( \t:/{NOTE_B}#part\\)tail "literal :/{MISSING}" \t)'
        for body, line, fences in (
                ('~~~\n' + link + '\n~~~', None, 1),
                ('`' + link + '`', None, 0), ('    ' + link, None, 0),
                ('\\' + link, None, 0), ('!\\' + link, None, 0),
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
        body = link + f'\n[missing]( \t:/{MISSING}#part\\)tail "data :/{NOTE_B}" \t)\n[real](:/{NOTE_B})'
        result, issues, stats = self.render(body)
        self.assertEqual(result, body.replace(link, link.replace(f':/{NOTE_B}', '../pages/Target.md', 1)).replace(
            f'[real](:/{NOTE_B})', '[real]([[Target]])'))
        self.assertEqual(issues, [self.issue('note_link_uses_file_path', NOTE_B),
                                 self.issue('broken_link', MISSING, 2)])
        self.assertEqual(stats, dict(note_links=2, resource_links=0, checkboxes=0, protected_fences=0))


if __name__ == '__main__':
    unittest.main()
