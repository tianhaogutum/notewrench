# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic format contracts and real CLI/file-system integration tests."""
from __future__ import annotations

import csv
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

from notewrench.wallabagnotes.core import BagnotesError, parse
from notewrench.wallabagnotes.render import render
from notewrench.wallabagnotes.cli import publish

ROOT = Path(__file__).resolve().parents[2]


def annotation(**changes):
    return dict(id=9, quote='中文 "引文"\n第二行 🌱', text='注释，"带引号"\r\n下一行',
                ranges=[dict(start='/div[1]/p[1]', end='/div[1]/p[1]', startOffset=2, endOffset=4)]) | changes


def article(annotations=None, **changes):
    return dict(id=1, title='阅读', url='https://example.org/a?x=1&y=2',
                annotations=[annotation()] if annotations is None else annotations) | changes


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode('utf-8')


class ParserTests(unittest.TestCase):
    def error(self, data, code):
        with self.assertRaises(BagnotesError) as caught:
            parse(encoded(data))
        self.assertEqual(caught.exception.diagnostic['code'], code)
        return caught.exception.diagnostic

    def test_unicode_csv_roundtrip_and_raw_provenance(self):
        original = annotation(extra={'未知字段': ['保留', None]})
        result = parse(encoded([article([original])]))
        files, report = render(result)
        rows = list(csv.DictReader(io.StringIO(files['annotations.csv'].decode(), newline='')))
        self.assertEqual(rows[0]['quote'], original['quote'])
        self.assertEqual(rows[0]['comment'], original['text'])
        self.assertEqual(json.loads(rows[0]['ranges_json']), original['ranges'])
        raw = json.loads(files['provenance.json'])['articles'][0]['annotations'][0]['raw']
        self.assertEqual(raw, original)
        self.assertEqual(report['counts']['annotations'], 1)
        self.assertIn('> 中文', files['markdown/article-1.md'].decode())
        self.assertIn('  id:: ', files['logseq/article-1.md'].decode())

    def test_source_order_is_not_id_or_range_order(self):
        result = parse(encoded([article([annotation(id=20), annotation(id=3)], id=12), article(id=2)]))
        self.assertEqual([a.source_id for a in result.articles], ['12', '2'])
        self.assertEqual([a.annotation_id for a in result.articles[0].annotations], ['20', '3'])
        rows = list(csv.DictReader(io.StringIO(render(result)[0]['annotations.csv'].decode())))
        self.assertEqual([(r['source_order'], r['annotation_order']) for r in rows], [('0', '0'), ('0', '1'), ('1', '0')])

    def test_duplicate_ids_normalize_decimal_string_and_keep_occurrences(self):
        result = parse(encoded([article([annotation(), annotation(id='009')]), article(id='01')]))
        self.assertEqual(result.counts['duplicate_articles'], 1)
        self.assertEqual(result.counts['duplicate_annotations'], 2)
        self.assertEqual(len(result.articles[0].annotations), 1)
        self.assertEqual([a['raw']['id'] for a in result.articles[0].annotations[0].occurrences], [9, '009', 9])

    def test_duplicate_id_conflict_reports_both_paths_and_changed_fields(self):
        report = self.error([article([annotation(), annotation(text='异文')])], 'annotation_conflict')
        self.assertEqual(report['first_path'], '$[0].annotations[0]')
        self.assertEqual(report['path'], '$[0].annotations[1]')
        self.assertEqual(report['differing_fields'], ['text'])
        for changes in ({'ranges': []}, {'quote': '不同'}, {'extra': True}):
            with self.subTest(changes=changes):
                self.error([article([annotation(), annotation(**changes)])], 'annotation_conflict')

    def test_article_conflict(self):
        for key in ('title', 'url', 'content', 'custom'):
            with self.subTest(key=key):
                self.error([article(), article(**{key: 'other'})], 'article_conflict')

    def test_annotations_scoped_to_source(self):
        result = parse(encoded([article(), article([annotation(text='不同文章')], id=2)]))
        self.assertEqual(result.counts['annotations'], 2)
        self.assertNotEqual(result.articles[0].annotations[0].anchor, result.articles[1].annotations[0].anchor)

    def test_explicit_id_anchors_survive_edits_reordering_and_other_formats(self):
        first = parse(encoded([article()])).articles[0].annotations[0]
        changed = parse(encoded([article(id=2), article([annotation(text='edited', quote='new', ranges=[])])]))
        other = changed.articles[1].annotations[0]
        self.assertEqual(first.anchor, other.anchor)
        self.assertEqual(first.block_id, other.block_id)
        self.assertEqual(uuid.UUID(first.block_id).version, 5)
        for fmt in ('all', 'markdown', 'logseq', 'csv'):
            files, _ = render(changed, fmt)
            self.assertIn(first.anchor.encode(), files['provenance.json'])

    def test_real_export_without_annotation_ids_has_explicit_content_identity(self):
        raw = annotation()
        del raw['id']
        reversed_keys = dict(reversed(list(raw.items())))
        result = parse(encoded([article([raw, reversed_keys])]))
        one = result.articles[0].annotations[0]
        self.assertIsNone(one.annotation_id)
        self.assertTrue(one.identity.startswith('content-sha256:'))
        self.assertEqual(result.counts['duplicate_annotations'], 1)
        self.assertEqual(result.counts['content_identities'], 1)
        self.assertIn('missing_annotation_id', [w['code'] for w in result.warnings])
        changed = parse(encoded([article([dict(raw, text='edit')])])).articles[0].annotations[0]
        self.assertNotEqual(one.anchor, changed.anchor)

    def test_empty_export_and_empty_annotations_are_valid(self):
        files, report = render(parse(b'[]'))
        self.assertEqual(list(csv.DictReader(io.StringIO(files['annotations.csv'].decode()))), [])
        self.assertEqual(report['counts']['articles'], 0)
        files, report = render(parse(encoded([article([])])))
        self.assertIn(b'No annotations.', files['markdown/article-1.md'])
        self.assertEqual(report['counts']['annotations'], 0)

    def test_quote_is_never_inferred_from_html(self):
        raw = annotation()
        del raw['quote']
        self.error([article([raw], content='<p>Possible quote</p>')], 'missing_quote')
        result = parse(encoded([article([annotation(quote='')])]))
        self.assertEqual(result.articles[0].annotations[0].raw['quote'], '')
        self.assertIn('empty_quote', [w['code'] for w in result.warnings])

    def test_malformed_or_future_ranges_are_retained_opaque(self):
        ranges = [{'start': 'no-xpath', 'startOffset': 'bad', 'extra': ['保留']}, None, '/unknown', 3, {}]
        result = parse(encoded([article([annotation(ranges=ranges)])]))
        self.assertEqual(len([w for w in result.warnings if w['code'] == 'opaque_range']), len(ranges))
        files, _ = render(result)
        self.assertEqual(json.loads(files['provenance.json'])['articles'][0]['annotations'][0]['raw']['ranges'], ranges)

    def test_invalid_top_levels_and_records(self):
        for value in ({}, {'items': []}, 'text', None, 1):
            with self.subTest(value=value):
                self.error(value, 'unsupported_format')
        self.error([None], 'invalid_article')
        self.error([article([None])], 'invalid_annotation')

    def test_invalid_field_types(self):
        for key, values in {'quote': [None, False, 4, []], 'text': [None, 3, {}],
                            'ranges': [None, {}, 'range', 4]}.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    self.error([article([annotation(**{key: value})])],
                               'invalid_ranges' if key == 'ranges' else 'invalid_field')
        for value in (None, {}, '', False):
            self.error([article(annotations=value) | {'annotations': value}], 'invalid_annotations')
        for key in ('title', 'url'):
            self.error([article(**{key: 1})], 'invalid_field')
        raw = article()
        del raw['annotations']
        result = parse(encoded([raw]))
        self.assertEqual(result.counts['annotations'], 0)
        self.assertIn('absent_annotations', [w['code'] for w in result.warnings])
        for key in ('text', 'ranges'):
            raw = annotation()
            del raw[key]
            self.error([article([raw])], 'missing_text' if key == 'text' else 'invalid_ranges')

    def test_invalid_ids_and_path_traversal(self):
        for value in (None, True, False, 0, -1, 1.2, '', '000', '../escape', '1/2', '９', '9\n', [], '1'*129):
            with self.subTest(value=value):
                self.error([article(id=value)], 'invalid_id')
                self.error([article([annotation(id=value)])], 'invalid_id')
        files, _ = render(parse(encoded([article(title='../../outside')])))
        self.assertIn('markdown/article-1.md', files)
        self.assertFalse(any('outside' in name for name in files))

    def test_json_rejects_duplicate_keys_nonfinite_truncation_and_bad_unicode(self):
        for raw in (b'[{"id":1,"id":2}]', b'[NaN]', b'[Infinity]', b'[1e9999999999999999999999999999999]', b'[', b'\xff', b'["\\ud800"]'):
            with self.subTest(raw=raw), self.assertRaises(BagnotesError):
                parse(raw)
        self.assertEqual(parse(b'\xef\xbb\xbf[]').counts['articles'], 0)

    def test_opaque_numbers_retain_precision_and_numeric_type(self):
        raw = b'[{"id":1,"annotations":[{"quote":"x","text":"","ranges":[{"future":0.12345678901234567890123456789,"large":1e999}]}]}]'
        result = parse(raw)
        files, _ = render(result)
        document = json.loads(files['provenance.json'], parse_float=Decimal)
        ranges = document['articles'][0]['annotations'][0]['raw']['ranges']
        self.assertEqual(ranges[0]['future'], Decimal('0.12345678901234567890123456789'))
        self.assertEqual(ranges[0]['large'], Decimal('1e999'))
        rows = list(csv.DictReader(io.StringIO(files['annotations.csv'].decode())))
        self.assertEqual(json.loads(rows[0]['ranges_json'], parse_float=Decimal), ranges)

    def test_null_metadata_and_unsafe_urls_stay_in_provenance(self):
        for url in (None, '', 'javascript:alert(1)', 'https://a/\nscript', 'https://[invalid'):
            with self.subTest(url=url):
                result = parse(encoded([article(title=None, url=url)]))
                files, report = render(result)
                self.assertIn('unlinked_url', [w['code'] for w in report['warnings']])
                self.assertNotIn('[Source](<', files['markdown/article-1.md'].decode())
                self.assertEqual(json.loads(files['provenance.json'])['articles'][0]['metadata']['url'], url)

    def test_html_and_logseq_markup_inert_and_fences_cannot_be_closed_by_ranges(self):
        raw = annotation(quote='<script>alert(1)</script>\n[[secret]]\nid:: evil',
                         text='{{renderer x}}\n- # injected', ranges=[{'opaque': '```\n````\n<script>'}])
        files, _ = render(parse(encoded([article([raw], title='Title\n- second', url='https://example.org/a>(x)')])) )
        md = files['markdown/article-1.md'].decode()
        ls = files['logseq/article-1.md'].decode()
        self.assertNotIn('<script>alert', md)
        self.assertNotIn('[[secret]]', ls)
        self.assertNotIn('> id:: evil', ls)
        self.assertIn('`````json', ls)
        self.assertIn('https://example.org/a%3E%28x%29', md)
        self.assertEqual(json.loads(files['provenance.json'])['articles'][0]['annotations'][0]['raw'], raw)

    def test_content_omission_and_unknown_article_metadata_are_explicit(self):
        files, report = render(parse(encoded([article(content='not an archive', custom={'a': 1})])))
        p = json.loads(files['provenance.json'])['articles'][0]
        self.assertEqual(p['metadata']['custom'], {'a': 1})
        self.assertEqual(p['omitted_fields'], ['content'])
        self.assertNotIn('content', p['metadata'])
        self.assertIn('omitted_article_content', [w['code'] for w in report['warnings']])

    def test_parser_does_not_mutate_and_outputs_are_repeatable(self):
        raw = encoded([article()])
        first = render(parse(raw))[0]
        self.assertEqual(first, render(parse(raw))[0])
        self.assertEqual(json.loads(raw), [article()])
        self.assertEqual(json.loads(first['report.json'])['input_sha256'], hashlib.sha256(raw).hexdigest())


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wallabag-notes-tests-')
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.source = self.work / 'input.json'
        self.source.write_bytes(encoded([article()]))
        self.prefix = [sys.executable, '-m', 'notewrench', 'wallabag-notes']

    def run_cli(self, *args, cwd=ROOT, prefix=None):
        return subprocess.run([*(prefix or self.prefix), *map(str, args)], cwd=cwd,
                              capture_output=True, text=True, encoding='utf-8', timeout=120)

    def test_all_formats_real_files_e2e(self):
        self.source.write_bytes((ROOT / 'examples/wallabag-notes/with-ids.json').read_bytes())
        before = self.source.read_bytes()
        out = self.work / 'notes'
        result = self.run_cli(self.source, '-o', out)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((out / 'report.json').read_bytes())
        self.assertEqual(report['counts']['annotations'], 2)
        self.assertEqual(report['counts']['duplicate_annotations'], 1)
        self.assertEqual(sorted(str(p.relative_to(out)) for p in out.rglob('*') if p.is_file()), report['files'])
        with (out / 'annotations.csv').open(encoding='utf-8', newline='') as handle:
            rows = list(csv.DictReader(handle))
        original = json.loads(before)[0]['annotations'][0]
        self.assertEqual(rows[0]['quote'], original['quote'])
        self.assertEqual(rows[0]['comment'], original['text'])
        self.assertEqual(json.loads(rows[0]['ranges_json']), original['ranges'])
        self.assertEqual(self.source.read_bytes(), before)
        self.assertIn('证据', (out / 'markdown/article-1.md').read_text())
        self.assertIn('id:: ' + rows[0]['block_id'], (out / 'logseq/article-1.md').read_text())

    def test_dry_run_no_writes_with_script_like_input(self):
        marker = self.work / 'executed'
        self.source.write_bytes(encoded([article([annotation(text=f'$(touch {marker})')], content='<script>bad()</script>')]))
        before = sorted(self.work.rglob('*'))
        result = self.run_cli(self.source, '-o', self.work / 'plan', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'dry-run')
        self.assertEqual(sorted(self.work.rglob('*')), before)
        self.assertFalse(marker.exists())

    def test_path_conflicts_file_directory_symlink_and_input_never_overwritten(self):
        folder = self.work / 'existing'
        folder.mkdir()
        link = self.work / 'link'
        link.symlink_to(self.work / 'absent', target_is_directory=True)
        for target in (folder, self.source, link):
            for extra in ((), ('--dry-run',)):
                with self.subTest(target=target, extra=extra):
                    result = self.run_cli(self.source, '-o', target, *extra)
                    self.assertEqual(result.returncode, 2, result.stderr)
                    self.assertEqual(json.loads(result.stderr)['code'], 'output_conflict')
        self.assertEqual(list(folder.iterdir()), [])
        self.assertEqual(json.loads(self.source.read_bytes()), [article()])

    def test_output_parent_missing(self):
        result = self.run_cli(self.source, '-o', self.work / 'no' / 'out')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)['code'], 'output_parent')
        self.assertFalse((self.work / 'no').exists())

    def test_input_errors_leave_no_output(self):
        for raw, code in ((b'{', 'invalid_json'), (b'{"items":[]}', 'unsupported_format'),
                          (encoded([article([annotation(), annotation(text='conflict')])]), 'annotation_conflict')):
            with self.subTest(code=code):
                self.source.write_bytes(raw)
                output = self.work / 'out'
                result = self.run_cli(self.source, '-o', output)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stderr)['code'], code)
                self.assertFalse(output.exists())
                self.assertEqual(result.stdout, '')

    def test_missing_or_directory_input_is_clear_io_error(self):
        for source in (self.work / 'absent.json', self.work):
            result = self.run_cli(source, '-o', self.work / 'out')
            self.assertEqual(result.returncode, 3)
            self.assertEqual(json.loads(result.stderr)['code'], 'io_error')

    def test_each_format_can_be_selected_independently(self):
        for fmt in ('markdown', 'logseq', 'csv'):
            output = self.work / fmt
            result = self.run_cli(self.source, '-o', output, '--format', fmt)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((output / 'annotations.csv').exists(), fmt == 'csv')
            self.assertEqual((output / 'markdown').exists(), fmt == 'markdown')
            self.assertEqual((output / 'logseq').exists(), fmt == 'logseq')
            self.assertTrue((output / 'provenance.json').exists())

    def test_repeat_exports_identical_bytes(self):
        a, b = self.work / 'a', self.work / 'b'
        for out in (a, b):
            self.assertEqual(self.run_cli(self.source, '-o', out).returncode, 0)
        for path in a.rglob('*'):
            if path.is_file():
                self.assertEqual(path.read_bytes(), (b / path.relative_to(a)).read_bytes())

    def test_publication_collision_does_not_delete_preexisting_directory(self):
        output = self.work / 'race'
        output.mkdir()
        keep = output / 'keep'
        keep.write_text('do not remove')
        with self.assertRaises(FileExistsError):
            publish(output, {'file': b'x'})
        self.assertEqual(keep.read_text(), 'do not remove')

    def test_publication_io_failure_rolls_back_its_files(self):
        output = self.work / 'rollback'
        original = Path.open

        def failing(path, mode='r', *args, **kwargs):
            if path.name == 'second':
                raise OSError('synthetic disk failure')
            return original(path, mode, *args, **kwargs)

        with patch.object(Path, 'open', failing), self.assertRaises(OSError):
            publish(output, {'markdown/first': b'written', 'second': b'failure'})
        self.assertFalse(output.exists())

    def test_runs_outside_the_repository_with_relative_paths_and_propagates_errors(self):
        result = self.run_cli('input.json', '-o', 'portable', cwd=self.work)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.work / 'portable/annotations.csv').exists())
        result = self.run_cli('input.json', '-o', 'portable', cwd=self.work)
        self.assertEqual(result.returncode, 2, result.stderr)

    def test_help_version_bad_arguments(self):
        self.assertEqual(self.run_cli('--help').returncode, 0)
        self.assertIn('notewrench wallabag-notes ', self.run_cli('--version').stdout)
        self.assertEqual(self.run_cli(self.source, '-o', self.work / 'out', '--format', 'unknown').returncode, 2)


if __name__ == '__main__':
    unittest.main()
