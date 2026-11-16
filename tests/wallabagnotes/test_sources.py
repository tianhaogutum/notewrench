# SPDX-License-Identifier: AGPL-3.0-only
"""Namespaced offline merge regression contracts; all inputs are synthetic."""
from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from notewrench.wallabagnotes import core
from notewrench.wallabagnotes.render import render

ROOT = Path(__file__).resolve().parents[2]
PERSONAL = (ROOT / 'examples/wallabag-notes/accounts/personal.json').read_bytes()
WORK = (ROOT / 'examples/wallabag-notes/accounts/work.json').read_bytes()


def changed(data, *, metadata=None, annotation=None):
    value = json.loads(data)
    value[0].update(metadata or {})
    value[0]['annotations'][0].update(annotation or {})
    return json.dumps(value).encode()


class SourceParserTests(unittest.TestCase):
    def test_colliding_account_ids_have_distinct_stable_anchors_and_files(self):
        merged = core.parse_sources([('personal', PERSONAL), ('work', WORK)])
        files, report = render(merged)
        self.assertEqual(report['counts']['articles'], 2)
        self.assertEqual(report['counts']['annotations'], 2)
        articles = json.loads(files['provenance.json'])['articles']
        self.assertEqual([a['namespace'] for a in articles], ['personal', 'work'])
        self.assertEqual([a['source_id'] for a in articles], ['1', '1'])
        anchors = {a['namespace']: a['annotations'][0]['anchor'] for a in articles}
        blocks = {a['annotations'][0]['block_id'] for a in articles}
        self.assertEqual(len(set(anchors.values())), 2)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(len([name for name in files if name.startswith('markdown/')]), 2)
        reordered = core.parse_sources([('work', WORK), ('personal', changed(PERSONAL, annotation={'text': 'edited'}))])
        self.assertEqual({a.namespace: a.annotations[0].anchor for a in reordered.articles}, anchors)
        solo = core.parse_sources([('personal', PERSONAL)]).articles[0].annotations[0]
        self.assertEqual(solo.anchor, anchors['personal'])
        self.assertNotEqual(solo.anchor, core.parse(PERSONAL).articles[0].annotations[0].anchor)
        self.assertEqual(files, render(core.parse_sources([('personal', PERSONAL), ('work', WORK)]))[0])

    def test_same_namespace_dedup_retains_every_input_and_occurrence(self):
        repeated = changed(PERSONAL, metadata={'id': '001'}, annotation={'id': '009'})
        export = core.parse_sources([('personal', PERSONAL), ('personal', repeated)])
        files, report = render(export)
        self.assertEqual(report['counts']['articles'], 1)
        self.assertEqual(report['counts']['annotations'], 1)
        self.assertEqual(report['counts']['duplicate_articles'], 1)
        self.assertEqual(report['counts']['duplicate_annotations'], 1)
        p = json.loads(files['provenance.json'])
        self.assertEqual(p['schema_version'], 2)
        self.assertNotIn('input_sha256', p)  # no invented hash for concatenated files
        self.assertEqual(p['inputs'], [dict(input_order=i, namespace='personal', sha256=hashlib.sha256(data).hexdigest())
                                      for i, data in enumerate((PERSONAL, repeated))])
        a = p['articles'][0]
        self.assertEqual(a['source_occurrences'], [dict(input_order=i, source_order=0) for i in range(2)])
        self.assertEqual([o['input_order'] for o in a['annotations'][0]['occurrences']], [0, 1])
        self.assertEqual([o['raw']['id'] for o in a['annotations'][0]['occurrences']], [9, '009'])
        self.assertEqual(report['inputs'], p['inputs'])
        rows = list(csv.DictReader(io.StringIO(files['annotations.csv'].decode())))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['namespace'], rows[0]['input_order']), ('personal', '0'))
        self.assertIn(b'Source namespace: personal', next(v for k, v in files.items() if k.startswith('markdown/')))
        self.assertIn(b'bagnotes-namespace:: personal', next(v for k, v in files.items() if k.startswith('logseq/')))

    def test_same_namespace_conflicts_report_both_input_locations(self):
        for kwargs, code, field in [({'annotation': {'text': 'conflict'}}, 'annotation_conflict', 'text'),
                                    ({'metadata': {'title': 'conflict'}}, 'article_conflict', 'title'),
                                    ({'metadata': {'content': 'new HTML'}}, 'article_conflict', 'content')]:
            with self.subTest(code=code, field=field), self.assertRaises(core.BagnotesError) as caught:
                core.parse_sources([('personal', PERSONAL), ('personal', changed(PERSONAL, **kwargs))])
            d = caught.exception.diagnostic
            self.assertEqual(d['code'], code)
            self.assertEqual(d['namespace'], 'personal')
            self.assertIn('$inputs[0].articles[0]', d['first_path'])
            self.assertIn('$inputs[1].articles[0]', d['path'])
            self.assertEqual(d['differing_fields'], [field])

    def test_partial_exports_union_articles_and_annotations_in_first_seen_order(self):
        later = json.loads(PERSONAL)
        later[0]['annotations'].insert(0, dict(id=3, quote='additional', text='', ranges=[]))
        later.insert(0, dict(id=2, title='Empty', annotations=[]))
        export = core.parse_sources([('personal', PERSONAL), ('personal', json.dumps(later).encode())])
        self.assertEqual([a.source_id for a in export.articles], ['1', '2'])
        self.assertEqual([a.annotation_id for a in export.articles[0].annotations], ['9', '3'])
        self.assertEqual(export.counts['duplicate_annotations'], 1)
        rows = list(csv.DictReader(io.StringIO(render(export)[0]['annotations.csv'].decode())))
        self.assertEqual([(r['input_order'], r['source_order'], r['annotation_order']) for r in rows],
                         [('0', '0', '0'), ('1', '1', '0')])

    def test_anonymous_dedup_is_scoped_and_order_is_first_seen(self):
        raw = json.loads(PERSONAL)
        del raw[0]['annotations'][0]['id']
        data = json.dumps(raw).encode()
        export = core.parse_sources([('work', data), ('personal', data), ('work', data)])
        self.assertEqual([a.namespace for a in export.articles], ['work', 'personal'])
        self.assertEqual(export.counts['annotations'], 2)
        self.assertEqual(export.counts['duplicate_annotations'], 1)
        self.assertNotEqual(export.articles[0].annotations[0].anchor, export.articles[1].annotations[0].anchor)
        warnings = [w for w in export.warnings if w['code'] == 'missing_annotation_id']
        self.assertEqual([w['namespace'] for w in warnings], ['work', 'personal'])
        self.assertTrue(warnings[1]['path'].startswith('$inputs[1].articles'))

    def test_namespace_validation_and_bad_later_input(self):
        for namespace in ('', '../work', 'work/account', ' work', 'work\n', 'x' * 65, None):
            with self.subTest(namespace=namespace), self.assertRaises(core.BagnotesError) as caught:
                core.parse_sources([(namespace, PERSONAL)])
            self.assertEqual(caught.exception.diagnostic['code'], 'invalid_namespace')
        with self.assertRaises(core.BagnotesError) as caught:
            core.parse_sources([])
        self.assertEqual(caught.exception.diagnostic['code'], 'missing_sources')
        for bad, code in ((b'{', 'invalid_json'), (b'{}', 'unsupported_format'),
                          (b'[{"id":1,"annotations":[{"text":"","ranges":[]}]}]', 'missing_quote')):
            with self.subTest(code=code), self.assertRaises(core.BagnotesError) as caught:
                core.parse_sources([('personal', PERSONAL), ('work', bad)])
            self.assertEqual(caught.exception.diagnostic['code'], code)
            self.assertEqual(caught.exception.diagnostic['namespace'], 'work')
            self.assertTrue(caught.exception.diagnostic['path'].startswith('$inputs[1]'))

    def test_empty_exports_still_retain_their_namespaces_and_hashes(self):
        files, report = render(core.parse_sources([('work', b'[]'), ('personal', PERSONAL)]))
        self.assertEqual(len(report['inputs']), 2)
        self.assertEqual(report['counts']['articles'], 1)
        self.assertEqual(json.loads(files['provenance.json'])['inputs'][0]['namespace'], 'work')

    def test_legacy_schema_filename_and_identity_remain_compatible(self):
        export = core.parse(PERSONAL)
        files, report = render(export)
        expected = 'bagnotes-' + core.digest(['1', 'id:9'])
        self.assertEqual(export.articles[0].annotations[0].anchor, expected)
        self.assertIn('markdown/article-1.md', files)
        self.assertEqual(report['schema_version'], 1)
        p = json.loads(files['provenance.json'])
        self.assertEqual(p['input_sha256'], hashlib.sha256(PERSONAL).hexdigest())
        self.assertEqual(p['articles'][0]['source_occurrences'], [0])
        self.assertNotIn('namespace', p['articles'][0])


class SourceCLITests(unittest.TestCase):
    def test_relative_cli_merge_dry_run_and_conflict_no_publication(self):
        with tempfile.TemporaryDirectory(prefix='wallabag-notes-sources-') as temporary:
            work = Path(temporary)
            (work / 'personal.json').write_bytes(PERSONAL)
            (work / 'work.json').write_bytes(WORK)
            prefix = [sys.executable, '-m', 'notewrench', 'wallabag-notes']
            args = ['--source', 'personal', 'personal.json', '--source', 'work', 'work.json',
                    '--source', 'personal', 'personal.json', '-o', 'notes']

            def run(*extra):
                return subprocess.run([*prefix, *args, *extra], cwd=work, capture_output=True, text=True, timeout=120)

            before = sorted(work.iterdir())
            dry = run('--dry-run')
            self.assertEqual(dry.returncode, 0, dry.stderr)
            self.assertEqual(sorted(work.iterdir()), before)
            result = run()
            self.assertEqual(result.returncode, 0, result.stderr)
            p = json.loads((work / 'notes/provenance.json').read_bytes())
            self.assertEqual(len(p['inputs']), 3)
            self.assertEqual(len(p['articles']), 2)
            self.assertEqual(len(p['articles'][0]['annotations'][0]['occurrences']), 2)
            self.assertEqual(len(list((work / 'notes/markdown').glob('*.md'))), 2)
            self.assertEqual(len(list((work / 'notes/logseq').glob('*.md'))), 2)
            with (work / 'notes/annotations.csv').open(newline='', encoding='utf-8') as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([r['namespace'] for r in rows], ['personal', 'work'])
            for a, row in zip(p['articles'], rows):
                self.assertEqual(a['annotations'][0]['raw']['quote'], row['quote'])
                self.assertEqual(a['annotations'][0]['anchor'], row['anchor'])
            (work / 'conflict.json').write_bytes(changed(PERSONAL, annotation={'text': 'conflicting edit'}))
            bad = json.loads(PERSONAL)
            del bad[0]['annotations'][0]['quote']
            (work / 'bad.json').write_bytes(json.dumps(bad).encode())
            for filename, code in [('work.json', 'article_conflict'), ('conflict.json', 'annotation_conflict'),
                                   ('bad.json', 'missing_quote')]:
                for dry_flag in ([], ['--dry-run']):
                    with self.subTest(filename=filename, dry_flag=dry_flag):
                        conflict = subprocess.run([*prefix, '--source', 'personal', 'personal.json',
                                                   '--source', 'personal', filename, '-o', 'rejected', *dry_flag],
                                                  cwd=work, capture_output=True, text=True, timeout=120)
                        self.assertEqual(conflict.returncode, 2, conflict.stderr)
                        self.assertEqual(json.loads(conflict.stderr)['code'], code)
                        self.assertFalse((work / 'rejected').exists())
            self.assertEqual((work / 'personal.json').read_bytes(), PERSONAL)
            self.assertEqual((work / 'work.json').read_bytes(), WORK)

    def test_cli_rejects_ambiguous_missing_or_bad_source_arguments(self):
        with tempfile.TemporaryDirectory(prefix='wallabag-notes-arguments-') as temporary:
            work = Path(temporary)
            source = work / 'input.json'
            source.write_bytes(PERSONAL)
            for args in ([], [source, '--source', 'work', source], ['--source', '../work', source],
                         ['--source', 'work'], ['--source', 'work', work / 'missing.json']):
                with self.subTest(args=args):
                    result = subprocess.run([sys.executable, '-m', 'notewrench', 'wallabag-notes', *map(str, args), '-o', str(work / 'out')],
                                            cwd=ROOT, capture_output=True, text=True, timeout=120)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((work / 'out').exists())


if __name__ == '__main__':
    unittest.main()
