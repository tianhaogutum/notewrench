# SPDX-License-Identifier: AGPL-3.0-only
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from notewrench.joplinlogseq import ConversionError, Limits, convert
from notewrench.joplinlogseq.items import parse_item
from .synthetic import (CHILD, FOLDER, MISSING, NOTE_A, NOTE_B, NOTE_C,
    RELATION, RESOURCE, TAG, png_bytes, record, sample_files, write_jex)

ROOT = Path(__file__).resolve().parents[2]


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'input.jex'
        self.output = self.root / 'graph'

    def run_conversion(self, files=None, **kwargs):
        write_jex(self.source, files)
        return convert(self.source, self.output, **kwargs)

    def reject(self, files, message):
        with self.assertRaisesRegex(ConversionError, message):
            self.run_conversion(files)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.jex2logseq-*')))


class ConversionTests(FixtureTests):
    def test_full_graph_cycles_tags_todos_and_resource_bytes(self):
        report = self.run_conversion()
        self.assertEqual(report['counts'], {'note': 3, 'folder': 2, 'resource': 1, 'tag': 1, 'note_tag': 1})
        self.assertEqual(json.loads((self.output / 'report.json').read_text()), report)
        pages = report['pages']
        self.assertNotEqual(pages[NOTE_A]['path'], pages[NOTE_B]['path'])
        self.assertIn('--' + NOTE_A, pages[NOTE_A]['path'])
        self.assertEqual(pages[NOTE_C]['path'], 'pages/Notebook___Projects___Release.md')
        first = (self.output / pages[NOTE_A]['path']).read_text()
        second = (self.output / pages[NOTE_B]['path']).read_text()
        self.assertIn(f'[other plan]([[{pages[NOTE_B]["page"]}]])', first)
        self.assertIn(f'[Back]([[{pages[NOTE_A]["page"]}]])', second)
        self.assertIn('tags:: [[work in progress]]', first)
        self.assertIn('- TODO Plan', first)
        self.assertIn('- DONE Plan', second)
        self.assertIn('  - TODO Draft', first)
        self.assertIn('  - DONE Reviewed', first)
        actual = (self.output / report['assets'][RESOURCE]['path']).read_bytes()
        self.assertEqual(actual, png_bytes())
        self.assertEqual(report['assets'][RESOURCE]['sha256'], hashlib.sha256(actual).hexdigest())
        self.assertEqual(report['rewrites']['note_links'], 4)
        self.assertEqual(report['rewrites']['resource_links'], 2)
        self.assertEqual(report['rewrites']['protected_fences'], 1)
        self.assertEqual(report['rewrites']['checkboxes'], 2)
        self.assertEqual(report['issues'], [{'code': 'broken_link', 'note_id': NOTE_A, 'target': MISSING, 'line': 13}])
        self.assertIn(f'[missing](:/{MISSING})', first)
        self.assertEqual(report['source_items'][NOTE_A]['metadata']['future_field'], 'preserve: this unknown value')

    def test_terminal_metadata_does_not_eat_body_colons_or_blank_lines(self):
        body = 'http://example.test/a:b\n\nid: this is body\ntype_: 999\n\nlast: paragraph\n\n'
        data = record(NOTE_A, 1, title='A title', body=body, custom='value: with: colons')
        item = parse_item(NOTE_A + '.md', data)
        self.assertEqual(item.body, body)
        self.assertEqual(item.title, 'A title')
        self.assertEqual(item.metadata['custom'], 'value: with: colons')
        self.assertEqual(parse_item(NOTE_A + '.md', data + b'\n').body, body)

    def test_empty_note_empty_title_and_metadata_only_relation(self):
        item = parse_item(NOTE_A + '.md', record(NOTE_A, 1, title=''))
        self.assertEqual((item.title, item.body), ('', ''))
        item = parse_item(RELATION + '.md', record(RELATION, 6, note_id=NOTE_A, tag_id=TAG))
        self.assertEqual(item.type, 6)
        self.assertEqual(item.title, '')

    def test_fences_and_single_line_inline_code_are_unchanged(self):
        body = (f'````md\n[keep](:/{NOTE_B})\n```\n- [x] keep\n````\n'
                f'~~~\n:/{NOTE_B}\n~~~\n'
                f'Literal ``[keep](:/{NOTE_B}) ` tick``; [change](:/{NOTE_B})')
        files = {NOTE_A + '.md': record(NOTE_A, 1, title='A', body=body),
                 NOTE_B + '.md': record(NOTE_B, 1, title='B')}
        report = self.run_conversion(files)
        result = (self.output / report['pages'][NOTE_A]['path']).read_text()
        # Strip only the two spaces added for the containing Logseq block.
        restored = '\n'.join(line[2:] for line in result.splitlines()[5:])
        self.assertEqual(restored, body.replace(f'[change](:/{NOTE_B})', '[change]([[B]])'))
        self.assertEqual(report['rewrites']['protected_fences'], 2)
        self.assertEqual(report['rewrites']['checkboxes'], 0)

    def test_determinism_across_tar_order_and_case_collisions(self):
        files = {NOTE_A + '.md': record(NOTE_A, 1, title='Plan'),
                 NOTE_B + '.md': record(NOTE_B, 1, title='plan'),
                 NOTE_C + '.md': record(NOTE_C, 1, title='Plan--' + NOTE_A)}
        first = self.run_conversion(files)
        out2 = self.root / 'again'
        with tarfile.open(self.source, 'w') as archive:
            for name, data in reversed(list(files.items())):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        second = convert(self.source, out2)
        self.assertEqual(first, second)
        self.assertEqual(len({v['path'].casefold() for v in first['pages'].values()}), 3)
        for path in self.output.rglob('*'):
            if path.is_file():
                self.assertEqual(path.read_bytes(), (out2 / path.relative_to(self.output)).read_bytes())

    def test_duplicate_folder_names_and_unsafe_titles(self):
        files = {FOLDER + '.md': record(FOLDER, 2, title='work', parent_id=''),
                 CHILD + '.md': record(CHILD, 2, title='WORK', parent_id=''),
                 NOTE_A + '.md': record(NOTE_A, 1, title='../x [[y]]', parent_id=FOLDER),
                 NOTE_B + '.md': record(NOTE_B, 1, title='../x [[y]]', parent_id=CHILD)}
        report = self.run_conversion(files)
        for value in report['pages'].values():
            self.assertEqual(len(Path(value['path']).parts), 2)
            self.assertNotIn('[[', value['page'])
            self.assertIn('--', value['page'])
        self.assertEqual(report['source_items'][NOTE_A]['title'], '../x [[y]]')

    def test_missing_note_folder_is_reported_for_single_note_exports(self):
        report = self.run_conversion({NOTE_A + '.md': record(NOTE_A, 1, title='Solo', parent_id=FOLDER)})
        self.assertEqual(report['pages'][NOTE_A]['page'], 'Unfiled/Solo')
        self.assertEqual(report['issues'][0]['code'], 'missing_note_folder')

    def test_bare_links_fragments_and_unsupported_contexts_reported(self):
        body = f':/{NOTE_B}\n[x](:/{NOTE_B}#heading)\n<a href=":/{NOTE_B}">html</a>\n[ref]: :/{RESOURCE}'
        files = {NOTE_A + '.md': record(NOTE_A, 1, title='A', body=body),
                 NOTE_B + '.md': record(NOTE_B, 1, title='B')}
        report = self.run_conversion(files)
        result = (self.output / report['pages'][NOTE_A]['path']).read_text()
        self.assertIn('  [[B]]', result)
        self.assertIn('[x](../pages/B.md#heading)', result)
        self.assertIn(f'<a href=":/{NOTE_B}">', result)
        self.assertIn('unsupported_link_context', [v['code'] for v in report['issues']])

    def test_html_attributes_and_comments_are_unchanged_and_reported(self):
        body = (f'<a title="[label](:/{NOTE_B})">HTML</a>\n'
                f'<!-- [comment](:/{NOTE_B}) -->\n'
                f'[real](:/{NOTE_B})')
        files = {NOTE_A + '.md': record(NOTE_A, 1, title='A', body=body),
                 NOTE_B + '.md': record(NOTE_B, 1, title='B')}
        report = self.run_conversion(files)
        result = (self.output / report['pages'][NOTE_A]['path']).read_text()
        restored = '\n'.join(line[2:] for line in result.splitlines()[5:])
        self.assertEqual(restored, body.replace(f'[real](:/{NOTE_B})', '[real]([[B]])'))
        self.assertEqual(report['rewrites']['note_links'], 1)
        self.assertEqual(report['issues'], [
            {'code': 'unsupported_link_context', 'note_id': NOTE_A, 'target': NOTE_B, 'line': line}
            for line in (1, 2)])

    def test_multiline_html_quotes_comments_and_missing_targets(self):
        body = (f'<a title="greater > ` [label](:/{NOTE_B})\n'
                f':/{MISSING}">[real](:/{NOTE_B})</a>\n'
                '<!-- > `\n\n'
                f'[comment](:/{NOTE_B})\n-->\n'
                f'[after](:/{NOTE_B})')
        files = {NOTE_A + '.md': record(NOTE_A, 1, title='A', body=body),
                 NOTE_B + '.md': record(NOTE_B, 1, title='B')}
        report = self.run_conversion(files)
        result = (self.output / report['pages'][NOTE_A]['path']).read_text()
        expected = body.replace(f'[real](:/{NOTE_B})', '[real]([[B]])').replace(
            f'[after](:/{NOTE_B})', '[after]([[B]])')
        self.assertEqual('\n'.join(line[2:] for line in result.splitlines()[5:]), expected)
        self.assertEqual(report['rewrites']['note_links'], 2)
        self.assertEqual([(i['code'], i['line']) for i in report['issues']], [
            ('unsupported_link_context', 1), ('unsupported_link_context', 2),
            ('broken_link', 2), ('unsupported_link_context', 5)])

    def test_reference_definitions_map_destinations_and_preserve_uses(self):
        body = (f'[full][page] [page][] [page] ![swatch][asset]\n'
                f'[page]: :/{NOTE_B} "tooltip"\n'
                f"  [asset]: <:/{RESOURCE}#pixel> 'image title'  \n"
                f'[fragment]: :/{NOTE_B}#heading (section)\n'
                f'[missing]: :/{MISSING}\n'
                'Text: café, punctuation, https://example.test/path.\n'
                f'[direct](:/{NOTE_B})')
        files = {NOTE_A + '.md': record(NOTE_A, 1, title='A', body=body),
                 NOTE_B + '.md': record(NOTE_B, 1, title='Target page'),
                 RESOURCE + '.md': record(RESOURCE, 4, title='swatch', file_extension='png'),
                 'resources/' + RESOURCE + '.png': png_bytes()}
        report = self.run_conversion(files)
        result = (self.output / report['pages'][NOTE_A]['path']).read_text()
        expected = body.replace(f'[page]: :/{NOTE_B}', '[page]: ../pages/Target%20page.md')
        expected = expected.replace(f'<:/{RESOURCE}#pixel>', f'<../assets/{RESOURCE}.png#pixel>')
        expected = expected.replace(f'[fragment]: :/{NOTE_B}', '[fragment]: ../pages/Target%20page.md')
        expected = expected.replace(f'[direct](:/{NOTE_B})', '[direct]([[Target page]])')
        self.assertEqual('\n'.join(line[2:] for line in result.splitlines()[5:]), expected)
        self.assertEqual(report['source_items'][NOTE_A]['body'], body)
        self.assertEqual((self.output / report['assets'][RESOURCE]['path']).read_bytes(), png_bytes())
        self.assertEqual((report['rewrites']['note_links'], report['rewrites']['resource_links']), (3, 1))
        self.assertEqual([(i['code'], i['line']) for i in report['issues']], [
            ('note_link_uses_file_path', 2), ('note_link_uses_file_path', 4), ('broken_link', 5)])

    def test_code_and_escaped_link_examples_do_not_convert_or_report(self):
        body = (f'    [indented](:/{NOTE_B})\n    - [x] literal\n'
                f'  \t[ref]: :/{MISSING}\n\n'
                f'Literal ``start\n[inline](:/{NOTE_B})\n- [ ] code\nend``.\n'
                f'\\[example](:/{NOTE_B})\n'
                f'`[missing](:/{MISSING})`\n\n'
                f'~~~\n[ref]: :/{MISSING}\n~~~\n'
                f'[real](:/{NOTE_B})')
        files = {NOTE_A + '.md': record(NOTE_A, 1, title='A', body=body),
                 NOTE_B + '.md': record(NOTE_B, 1, title='B')}
        report = self.run_conversion(files)
        result = (self.output / report['pages'][NOTE_A]['path']).read_text()
        self.assertEqual('\n'.join(line[2:] for line in result.splitlines()[5:]),
                         body.replace(f'[real](:/{NOTE_B})', '[real]([[B]])'))
        self.assertEqual(report['issues'], [])
        self.assertEqual(report['rewrites'], dict(note_links=1, resource_links=0,
                                                  checkboxes=0, protected_fences=1))

    def test_reference_titles_are_data_and_split_destinations_convert(self):
        body = (f'[page]: :/{NOTE_B} "`code` <a title=\'text\'>"\n'
                f'[split]:\n  :/{NOTE_B}\n'
                f'[missing]:\n  :/{MISSING}\n'
                f'[bad]: :/{NOTE_B} extra text')
        files = {NOTE_A + '.md': record(NOTE_A, 1, title='A', body=body),
                 NOTE_B + '.md': record(NOTE_B, 1, title='B')}
        report = self.run_conversion(files)
        result = (self.output / report['pages'][NOTE_A]['path']).read_text()
        expected = body.replace(f'[page]: :/{NOTE_B}', '[page]: ../pages/B.md')
        expected = expected.replace(f'[split]:\n  :/{NOTE_B}', '[split]:\n  ../pages/B.md')
        self.assertEqual('\n'.join(line[2:] for line in result.splitlines()[5:]), expected)
        self.assertEqual(report['rewrites']['note_links'], 2)
        self.assertEqual([(i['code'], i['line']) for i in report['issues']], [
            ('note_link_uses_file_path', 1), ('note_link_uses_file_path', 3),
            ('broken_link', 5), ('unsupported_link_context', 6)])

    def test_inline_delimiter_boundaries_and_escape_parity(self):
        body = (f'`unmatched [one](:/{NOTE_B})\n\n'
                f'[two](:/{NOTE_B}) `\n\n'
                f'\\` [three](:/{NOTE_B}) `\n\n'
                f'\\\\[four](:/{NOTE_B})\n'
                f'``literal ` [keep](:/{MISSING})``\n'
                f'`open\n~~~\n[keep](:/{MISSING})\n~~~\n'
                f'[five](:/{NOTE_B})')
        files = {NOTE_A + '.md': record(NOTE_A, 1, title='A', body=body),
                 NOTE_B + '.md': record(NOTE_B, 1, title='B')}
        report = self.run_conversion(files)
        result = (self.output / report['pages'][NOTE_A]['path']).read_text()
        expected = body
        for label in ('one', 'two', 'three', 'four', 'five'):
            expected = expected.replace(f'[{label}](:/{NOTE_B})', f'[{label}]([[B]])')
        self.assertEqual('\n'.join(line[2:] for line in result.splitlines()[5:]), expected)
        self.assertEqual(report['rewrites']['note_links'], 5)
        self.assertEqual(report['issues'], [])

    def test_html_comment_fence_markers_and_unterminated_tag_are_preserved(self):
        body = (f'<!--\n```\n[keep](:/{NOTE_B})\n-->\n'
                f'[real](:/{NOTE_B})\n'
                f'<a title="unterminated >\n[keep](:/{MISSING})')
        files = {NOTE_A + '.md': record(NOTE_A, 1, title='A', body=body),
                 NOTE_B + '.md': record(NOTE_B, 1, title='B')}
        report = self.run_conversion(files)
        result = (self.output / report['pages'][NOTE_A]['path']).read_text()
        expected = body.replace(f'[real](:/{NOTE_B})', '[real]([[B]])')
        self.assertEqual('\n'.join(line[2:] for line in result.splitlines()[5:]), expected)
        self.assertEqual(report['rewrites']['protected_fences'], 0)
        self.assertEqual(report['rewrites']['note_links'], 1)
        self.assertEqual([(i['code'], i['line']) for i in report['issues']], [
            ('unsupported_link_context', 3), ('unsupported_link_context', 7), ('broken_link', 7)])

    def test_quoted_and_list_contained_fences(self):
        body = f'> ```md\n> [keep](:/{NOTE_B})\n> ```\n- ~~~\n  :/{NOTE_B}\n  ~~~'
        files = {NOTE_A + '.md': record(NOTE_A, 1, title='A', body=body),
                 NOTE_B + '.md': record(NOTE_B, 1, title='B')}
        report = self.run_conversion(files)
        result = (self.output / report['pages'][NOTE_A]['path']).read_text()
        self.assertEqual('\n'.join(line[2:] for line in result.splitlines()[5:]), body)
        self.assertEqual(report['rewrites']['protected_fences'], 2)
        self.assertEqual(report['rewrites']['note_links'], 0)

    def test_filename_encoding_collisions_and_windows_reserved_names(self):
        files = {FOLDER + '.md': record(FOLDER, 2, title='a_'),
                 CHILD + '.md': record(CHILD, 2, title='a'),
                 NOTE_A + '.md': record(NOTE_A, 1, title='b', parent_id=FOLDER),
                 NOTE_B + '.md': record(NOTE_B, 1, title='_b', parent_id=CHILD),
                 NOTE_C + '.md': record(NOTE_C, 1, title='CON'),
                 MISSING + '.md': record(MISSING, 1, title='_CON')}
        report = self.run_conversion(files)
        paths = [p['path'] for p in report['pages'].values()]
        self.assertEqual(len(set(paths)), 4)
        for path in paths:
            self.assertTrue((self.output / path).is_file())
        self.assertIn('--' + NOTE_A, report['pages'][NOTE_A]['path'])

    def test_overlong_filename_and_deep_notebooks_fail(self):
        files = {FOLDER + '.md': record(FOLDER, 2, title='本' * 60),
                 NOTE_A + '.md': record(NOTE_A, 1, title='本' * 60, parent_id=FOLDER)}
        self.reject(files, '240 UTF-8 bytes')
        files = {}
        parent = ''
        for number in range(33):
            item_id = f'{number:032x}'
            files[item_id + '.md'] = record(item_id, 2, title='N', parent_id=parent)
            parent = item_id
        self.reject(files, '32 levels')

    def test_unclosed_fence_report_and_original_metadata_retention(self):
        body = f'```\n:/{MISSING}'
        report = self.run_conversion({NOTE_A + '.md': record(NOTE_A, 1, title='A',
            body=body, is_completed='1', custom=r'line\nnext', created_time='2026-11-11T00:00:00.000Z')})
        self.assertEqual(report['issues'][0]['code'], 'unclosed_code_fence')
        self.assertEqual(report['source_items'][NOTE_A]['body'], body)
        self.assertEqual(report['metadata_not_applied'][NOTE_A]['is_completed'], '1')
        self.assertEqual(report['metadata_not_applied'][NOTE_A]['custom'], r'line\nnext')

    def test_module_cli_conversion_outside_repository(self):
        command = [sys.executable, '-m', 'notewrench', 'joplin-logseq']
        body = (f'<a title="[literal](:/{NOTE_B})">HTML</a>\n'
                f'<!-- [comment](:/{NOTE_B}) -->\n'
                f'[see][target] ![image][asset]\n[target]:\n    :/{NOTE_B}\n'
                f'  "page title"\n[asset]: <:/{RESOURCE}>\n\t\'image title\'\n'
                f'[missing]:\n  :/{MISSING}\n'
                f'    [code](:/{NOTE_B})\n\n'
                f'`start\n[code](:/{NOTE_B})\nend`\n'
                f'\\[literal](:/{NOTE_B})\n[real](:/{NOTE_B})')
        files = sample_files()
        files[NOTE_A + '.md'] = record(NOTE_A, 1, title='Contexts', body=body)
        write_jex(self.source, files)
        input_before = self.source.read_bytes()
        preview = subprocess.run([*command, str(self.source), str(self.output), '--dry-run'],
                                 cwd=self.root, text=True, capture_output=True, timeout=60)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertFalse(self.output.exists())
        run = subprocess.run([*command, str(self.source), str(self.output)],
                             cwd=self.root, text=True, capture_output=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads((self.output / 'report.json').read_text())
        self.assertEqual(report, json.loads(preview.stdout))
        self.assertEqual(report['counts']['note'], 3)
        self.assertEqual(self.source.read_bytes(), input_before)
        expected = body.replace(f'    :/{NOTE_B}', '    ../' + report['pages'][NOTE_B]['path'])
        expected = expected.replace(f'<:/{RESOURCE}>', f'<../assets/{RESOURCE}.png>')
        expected = expected.replace(f'[real](:/{NOTE_B})', '[real]([[' + report['pages'][NOTE_B]['page'] + ']])')
        page = (self.output / report['pages'][NOTE_A]['path']).read_text()
        self.assertEqual('\n'.join(line[2:] for line in page.split('\n  \n', 1)[1].splitlines()), expected)
        self.assertEqual([(i['code'], i['line']) for i in report['issues']], [
            ('unsupported_link_context', 1), ('unsupported_link_context', 2),
            ('note_link_uses_file_path', 5), ('broken_link', 10)])
        self.assertEqual((self.output / report['assets'][RESOURCE]['path']).read_bytes(), png_bytes())
        failure = subprocess.run([*command, str(self.source), str(self.output)],
                                 cwd=self.root, text=True, capture_output=True, timeout=60)
        self.assertEqual(failure.returncode, 2)
        self.assertIn('already exists', failure.stderr)

    def test_bundled_jex_matches_its_original_generator(self):
        write_jex(self.source)
        self.assertEqual(self.source.read_bytes(), (ROOT / 'examples/joplin-logseq/synthetic.jex').read_bytes())

    def test_encryption_locked_unknown_type_and_html_fail(self):
        for kwargs, message in [({'encryption_applied': '1'}, 'Encrypted'),
                                ({'encryption_blob_encrypted': '1'}, 'Encrypted'),
                                ({'encryption_cipher_text': 'cipher'}, 'Encrypted'),
                                ({'is_locked': '1'}, 'locked'),
                                ({'markup_language': '2'}, 'markup_language'),
                                ({'todo_completed': 'NaN'}, 'todo_completed')]:
            with self.subTest(kwargs=kwargs):
                self.reject({NOTE_A + '.md': record(NOTE_A, 1, title='A', **kwargs)}, message)
        self.reject({NOTE_A + '.md': record(NOTE_A, 13)}, 'Unsupported item type_ 13')

    def test_corrupt_metadata(self):
        cases = [b'Title\n\nbroken property', record(NOTE_B, 1, title='B'),
                 f'Title\n\nid: {NOTE_A}\nid: {NOTE_A}\ntype_: 1'.encode(),
                 f'Title\n\nid: {NOTE_A}\ntype_: wat'.encode(), b'\xff',
                 record(NOTE_A, 1, title='A', parent_id='../../x'),
                 record(NOTE_A, 2, title='A', body='not allowed'),
                 record(NOTE_A, 1, title='multi\nline'),
                 record(NOTE_A, 1, title='A', encryption_applied='2')]
        for data in cases:
            with self.subTest(data=data):
                self.reject({NOTE_A + '.md': data}, '.')

    def test_missing_resource_or_orphan_or_extension_mismatch(self):
        files = sample_files()
        del files['resources/' + RESOURCE + '.png']
        self.reject(files, 'Missing or ambiguous resource')
        self.reject({'resources/' + RESOURCE + '.png': png_bytes()}, 'no item records')
        files = sample_files()
        del files[RESOURCE + '.md']
        self.reject(files, 'without metadata')
        files = sample_files()
        files[RESOURCE + '.md'] = record(RESOURCE, 4, title='r', file_extension='pdf')
        self.reject(files, 'extension mismatch')
        files[RESOURCE + '.md'] = record(RESOURCE, 4, title='r', file_extension='../png')
        self.reject(files, 'Unsafe resource')

    def test_extensionless_and_mime_fallback_resources(self):
        for extension in ('', '.png'):
            with self.subTest(extension=extension):
                files = {RESOURCE + '.md': record(RESOURCE, 4, title='r', mime='image/png'),
                         'resources/' + RESOURCE + extension: png_bytes()}
                self.run_conversion(files)
                self.assertEqual((self.output / ('assets/' + RESOURCE + extension)).read_bytes(), png_bytes())
                import shutil
                shutil.rmtree(self.output)

    def test_ambiguous_resource_and_encrypted_blob(self):
        files = sample_files()
        files['resources/' + RESOURCE] = b'other bytes'
        self.reject(files, 'ambiguous resource')
        self.reject({RESOURCE + '.md': record(RESOURCE, 4, title='r'),
                     'resources/' + RESOURCE + '.crypted': b'encrypted'}, 'Encrypted resource')

    def test_folder_cycle_missing_parent_and_dangling_tag(self):
        self.reject({FOLDER + '.md': record(FOLDER, 2, title='A', parent_id=CHILD),
                     CHILD + '.md': record(CHILD, 2, title='B', parent_id=FOLDER)}, 'Folder cycle')
        self.reject({FOLDER + '.md': record(FOLDER, 2, title='A', parent_id=CHILD)}, 'Missing parent folder')
        files = sample_files()
        files[RELATION + '.md'] = record(RELATION, 6, note_id=MISSING, tag_id=TAG)
        self.reject(files, 'Dangling note_tag')

    def test_existing_output_file_directory_and_symlink_are_untouched(self):
        write_jex(self.source)
        for kind in ('file', 'directory', 'symlink'):
            with self.subTest(kind=kind):
                if kind == 'file':
                    self.output.write_text('keep')
                elif kind == 'directory':
                    self.output.mkdir()
                    (self.output / 'keep').write_text('keep')
                else:
                    self.output.symlink_to(self.root / 'nonexistent')
                with self.assertRaisesRegex(ConversionError, 'already exists'):
                    convert(self.source, self.output)
                if kind == 'directory':
                    self.assertEqual((self.output / 'keep').read_text(), 'keep')
                    (self.output / 'keep').unlink()
                    self.output.rmdir()
                else:
                    if kind == 'file':
                        self.assertEqual(self.output.read_text(), 'keep')
                    self.output.unlink()

    def test_dry_run_has_no_filesystem_side_effects(self):
        write_jex(self.source)
        before = set(self.root.iterdir())
        report = convert(self.source, self.output, dry_run=True)
        self.assertEqual(before, set(self.root.iterdir()))
        self.assertEqual(report['counts']['note'], 3)

    def test_staging_failure_cleans_up(self):
        write_jex(self.source)
        with patch.object(Path, 'write_bytes', side_effect=OSError('simulated disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                convert(self.source, self.output)
        self.assertEqual(set(self.root.iterdir()), {self.source})

    def test_publication_race_refuses_new_existing_directory(self):
        write_jex(self.source)
        original = Path.mkdir
        def mkdir(path, *args, **kwargs):
            if path == self.output:
                original(path)
                (path / 'keep').write_text('other writer')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'mkdir', mkdir):
            with self.assertRaisesRegex(ConversionError, 'appeared'):
                convert(self.source, self.output)
        self.assertEqual((self.output / 'keep').read_text(), 'other writer')
        self.assertFalse(list(self.root.glob('.jex2logseq-*')))

    def test_cli_dry_run_and_error_exit_codes(self):
        write_jex(self.source)
        run = subprocess.run([sys.executable, '-m', 'notewrench', 'joplin-logseq', str(self.source), str(self.output), '--dry-run'],
                             cwd=ROOT, text=True, capture_output=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)['counts']['note'], 3)
        self.assertFalse(self.output.exists())
        for args in ([str(self.root / 'missing'), str(self.output)],
                     [str(self.source), str(self.output), '--max-members', '0']):
            run = subprocess.run([sys.executable, '-m', 'notewrench', 'joplin-logseq', *args], cwd=ROOT,
                                 text=True, capture_output=True, timeout=60)
            self.assertEqual(run.returncode, 2)
            self.assertIn('error' if '0' in args else 'Cannot read', run.stderr)


class ArchiveSafetyTests(FixtureTests):
    def malicious(self, members):
        with tarfile.open(self.source, 'w') as archive:
            for member in members:
                archive.addfile(member, io.BytesIO(b'x' * member.size) if member.isfile() else None)
        with self.assertRaises(ConversionError):
            convert(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_traversal_absolute_backslash_and_dot_segments(self):
        for name in ('../escape', '/tmp/escape', 'resources/../../escape', 'resources\\evil', 'a/./b'):
            with self.subTest(name=name):
                member = tarfile.TarInfo(name)
                member.size = 1
                self.malicious([member])
        self.assertFalse((self.root / 'escape').exists())

    def test_symlink_hardlink_fifo_and_sparse_rejected(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.GNUTYPE_SPARSE):
            with self.subTest(kind=kind):
                member = tarfile.TarInfo(NOTE_A + '.md')
                member.type = kind
                member.linkname = '/etc/passwd'
                self.malicious([member])

    def test_duplicates_and_unsupported_members_rejected(self):
        self.malicious([tarfile.TarInfo(NOTE_A + '.md'), tarfile.TarInfo('./' + NOTE_A + '.md')])
        for name in ('README', 'notes/a.md', 'note-lock-key.json'):
            self.malicious([tarfile.TarInfo(name)])

    def test_each_archive_limit(self):
        write_jex(self.source)
        for limits, field in [(Limits(max_archive_bytes=1), 'max_archive_bytes'),
                              (Limits(max_member_bytes=1), 'max_member_bytes'),
                              (Limits(max_total_bytes=1), 'max_total_bytes'),
                              (Limits(max_members=1), 'max_members')]:
            with self.subTest(field=field):
                with self.assertRaisesRegex(ConversionError, field):
                    convert(self.source, self.output, limits=limits)
                self.assertFalse(self.output.exists())
        with self.assertRaises(ValueError):
            Limits(max_members=0)

    def test_empty_truncated_invalid_and_compressed_archives(self):
        for data in (b'', b'not tar', b'\x1f\x8babc'):
            self.source.write_bytes(data)
            with self.assertRaises(ConversionError):
                convert(self.source, self.output)
        write_jex(self.source)
        data = self.source.read_bytes()
        self.source.write_bytes(data[:600])
        with self.assertRaises(ConversionError):
            convert(self.source, self.output)
        write_jex(self.source, {})
        with self.assertRaisesRegex(ConversionError, 'no items'):
            convert(self.source, self.output)

    def test_real_gzip_and_concatenated_archives_fail(self):
        import gzip
        write_jex(self.source)
        data = self.source.read_bytes()
        for invalid in (gzip.compress(data), data + data, data + b'x', data[:9728]):
            with self.subTest(length=len(invalid)):
                self.source.write_bytes(invalid)
                with self.assertRaises(ConversionError):
                    convert(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_nonregular_input_rejected_without_opening_fifo(self):
        import os
        os.mkfifo(self.source)
        with self.assertRaisesRegex(ConversionError, 'regular archive'):
            convert(self.source, self.output)

    def test_leading_dot_paths_resources_directory_and_negative_size(self):
        files = sample_files()
        with tarfile.open(self.source, 'w') as archive:
            directory = tarfile.TarInfo('./resources/')
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
            for name, data in sorted(files.items()):
                member = tarfile.TarInfo('./' + name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        report = convert(self.source, self.output)
        self.assertEqual(report['counts']['note'], 3)
        import shutil
        shutil.rmtree(self.output)
        negative = tarfile.TarInfo(NOTE_A + '.md')
        negative.size = -1
        self.source.write_bytes(negative.tobuf(format=tarfile.GNU_FORMAT) + bytes(1024))
        with self.assertRaises(ConversionError):
            convert(self.source, self.output)


if __name__ == '__main__':
    unittest.main()
