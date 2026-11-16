# SPDX-License-Identifier: AGPL-3.0-only
"""Read-only input inspection contracts; independently authored synthetic exports."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from notewrench.wallabagnotes import core
from notewrench.wallabagnotes.cli import main

ROOT = Path(__file__).resolve().parents[2]


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode()


def article(title='Original', text='Original', **extra):
    return dict(id=1, title=title, url='https://example.org/1', content='<p>original</p>',
                annotations=[dict(id=9, quote='中文 🌱', text=text, ranges=[])]) | extra


def snapshots():
    # The later two annotation variants differ from each other on quote, which
    # cannot be discovered by comparing their differing_fields to the first only.
    first = article()
    second = article(title='Edited', text='Edited')
    second['annotations'][0]['quote'] = 'second'
    third = copy.deepcopy(second)
    third['annotations'][0]['quote'] = 'third'
    return [('personal', encoded([value])) for value in (first, second, third)]


class ConflictInspectionTests(unittest.TestCase):
    def test_all_pairs_include_annotations_under_conflicting_articles_and_both_hashes(self):
        inputs = snapshots()
        result = core.inspect_sources(inputs)
        self.assertEqual(result['schema_version'], 1)
        self.assertEqual(result['tool'], 'bagnotes-conflicts')
        self.assertEqual(result['status'], 'conflicts')
        self.assertEqual(result['counts'], dict(article_conflicts=2, annotation_conflicts=3, conflicts=5))
        self.assertEqual(result['inputs'], [dict(input_order=i, namespace=ns, sha256=hashlib.sha256(data).hexdigest())
                                            for i, (ns, data) in enumerate(inputs)])
        # Deterministic current-occurrence order, earlier occurrences in input order.
        self.assertEqual([(c['code'], c['first']['input_order'], c['second']['input_order'])
                          for c in result['conflicts']], [
                              ('article_conflict', 0, 1), ('annotation_conflict', 0, 1),
                              ('article_conflict', 0, 2), ('annotation_conflict', 0, 2),
                              ('annotation_conflict', 1, 2)])
        for conflict in result['conflicts']:
            self.assertEqual(conflict['namespace'], 'personal')
            self.assertEqual(conflict['source_id'], '1')
            is_annotation = conflict['code'] == 'annotation_conflict'
            if is_annotation:
                self.assertEqual(conflict['identity'], 'id:9')
            for side in ('first', 'second'):
                location = conflict[side]
                index = location['input_order']
                self.assertEqual(location['sha256'], hashlib.sha256(inputs[index][1]).hexdigest())
                self.assertEqual(location['source_order'], 0)
                self.assertEqual(location['path'], f'$inputs[{index}].articles[0]' +
                                 ('.annotations[0]' if is_annotation else ''))
                if is_annotation:
                    self.assertEqual(location['annotation_order'], 0)
        self.assertEqual(result['conflicts'][-1]['differing_fields'], ['quote'])
        self.assertEqual(result, core.inspect_sources(inputs))
        with self.assertRaises(core.BagnotesError) as caught:
            core.parse_sources(inputs)
        self.assertEqual(caught.exception.diagnostic['code'], 'article_conflict')

    def test_every_occurrence_including_within_one_file_and_repeated_variants(self):
        a, b = article(), article(title='B', text='B')
        inputs = [('personal', encoded([a, b, copy.deepcopy(b)])), ('personal', encoded([a]))]
        result = core.inspect_sources(inputs)
        for code in ('article_conflict', 'annotation_conflict'):
            conflicts = [c for c in result['conflicts'] if c['code'] == code]
            self.assertEqual(len(conflicts), 4)
            self.assertEqual([(c['first']['source_order'], c['second']['source_order'],
                               c['second']['input_order']) for c in conflicts],
                             [(0, 1, 0), (0, 2, 0), (1, 0, 1), (2, 0, 1)])
        repeated = article()
        repeated['annotations'].append(dict(repeated['annotations'][0], text='other'))
        result = core.inspect_sources([('personal', encoded([repeated]))])
        self.assertEqual(result['counts']['annotation_conflicts'], 1)
        c = result['conflicts'][0]
        self.assertEqual((c['first']['annotation_order'], c['second']['annotation_order']), (0, 1))

    def test_multiple_articles_and_annotations_are_all_inspected(self):
        first = [article(), article(id=2)]
        first[0]['annotations'].append(dict(id=10, quote='Another', text='', ranges=[]))
        second = copy.deepcopy(first)
        for item in second:
            item['title'] = 'Edited'
            for annotation in item['annotations']:
                annotation['text'] = 'Edited'
        result = core.inspect_sources([('p', encoded(first)), ('p', encoded(second))])
        self.assertEqual(result['counts'], dict(article_conflicts=2, annotation_conflicts=3, conflicts=5))
        self.assertEqual([(c['source_id'], c.get('identity')) for c in result['conflicts']],
                         [('1', None), ('1', 'id:9'), ('1', 'id:10'), ('2', None), ('2', 'id:9')])

    def test_compatible_scopes_normalized_ids_partial_empty_and_anonymous_edits(self):
        a = article()
        normalized = article(id='001')
        normalized['annotations'][0]['id'] = '009'
        partial = article(annotations=[dict(id=10, quote='new', text='', ranges=[])])
        anonymous = article()
        del anonymous['annotations'][0]['id']
        edited_anonymous = copy.deepcopy(anonymous)
        edited_anonymous['annotations'][0]['text'] = 'edited'
        inputs = [('personal', encoded([a, normalized, partial])), ('work', encoded([article('Other', 'Other')])),
                  ('empty', b'[]'), ('anonymous', encoded([anonymous, edited_anonymous]))]
        result = core.inspect_sources(inputs)
        self.assertEqual(result['status'], 'compatible')
        self.assertEqual(result['counts'], dict(article_conflicts=0, annotation_conflicts=0, conflicts=0))
        self.assertEqual(result['conflicts'], [])
        self.assertEqual(len(result['inputs']), 4)
        self.assertIn('missing_annotation_id', [w['code'] for w in result['warnings']])
        distinct = article(id=2, title='Other', text='Other')
        self.assertEqual(core.inspect_sources([('personal', encoded([a, distinct]))])['conflicts'], [])

    def test_metadata_content_missing_keys_and_opaque_numeric_values_follow_export_comparison(self):
        a = article()
        b = article(content='<p>changed</p>', unknown=None)
        inputs = [('p', encoded([a, b]))]
        self.assertEqual(core.inspect_sources(inputs)['conflicts'][0]['differing_fields'], ['content', 'unknown'])
        data = encoded([article()]).replace(b'"ranges": []', b'"ranges": [0.123456789012345678901]')
        changed = data.replace(b'012345678901]', b'012345678902]')
        result = core.inspect_sources([('p', data), ('p', changed)])
        self.assertEqual(result['conflicts'][0]['differing_fields'], ['ranges'])
        self.assertEqual(core.inspect_sources([('p', data), ('p', data)])['conflicts'], [])

    def test_malformed_later_input_fails_instead_of_returning_partial_conflicts(self):
        bad_annotation = article()
        del bad_annotation['annotations'][0]['quote']
        cases = [(b'{', 'invalid_json'), (b'{}', 'unsupported_format'),
                 (b'[{"id":1,"id":2}]', 'duplicate_json_key'),
                 (encoded([bad_annotation]), 'missing_quote'),
                 (encoded([article(annotations=None)]), 'invalid_annotations')]
        for data, code in cases:
            with self.subTest(code=code), self.assertRaises(core.BagnotesError) as caught:
                core.inspect_sources([*snapshots(), ('later', data)])
            d = caught.exception.diagnostic
            self.assertEqual(d['code'], code)
            self.assertEqual(d['input_order'], 3)
            self.assertEqual(d['namespace'], 'later')
        for inputs, code in (([], 'missing_sources'), ([('../bad', b'[]')], 'invalid_namespace')):
            with self.subTest(code=code), self.assertRaises(core.BagnotesError) as caught:
                core.inspect_sources(inputs)
            self.assertEqual(caught.exception.diagnostic['code'], code)


class ConflictCLITests(unittest.TestCase):
    def test_inspection_never_calls_render_update_or_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'input.json'
            source.write_bytes(encoded([article()]))
            with patch('notewrench.wallabagnotes.cli.render', side_effect=AssertionError('render called')), \
                 patch('notewrench.wallabagnotes.cli.check_output', side_effect=AssertionError('output checked')), \
                 patch('notewrench.wallabagnotes.cli.publish', side_effect=AssertionError('publish called')), \
                 patch('notewrench.wallabagnotes.update.plan_update', side_effect=AssertionError('update called')), \
                 patch('builtins.print'):
                self.assertEqual(main(['--source', 'p', str(source), '--inspect-conflicts']), 0)

    def test_relative_cli_contract_read_only_and_negative_cases(self):
        with tempfile.TemporaryDirectory(prefix='wallabag-notes-inspect-') as temporary:
            work = Path(temporary)
            args = []
            for i, (namespace, data) in enumerate(snapshots()):
                (work / f'{i}.json').write_bytes(data)
                args += ['--source', namespace, f'{i}.json']
            (work / 'bad.json').write_bytes(b'{')
            (work / 'existing-notes').mkdir()
            (work / 'existing-notes/personal.md').write_bytes(b'Handwritten\r\n')

            def state():
                return {str(p.relative_to(work)): (p.read_bytes(), p.stat().st_mtime_ns)
                        for p in work.rglob('*') if p.is_file()}

            def run(options):
                return subprocess.run([*[sys.executable, '-m', 'notewrench', 'wallabag-notes'], *options], cwd=work,
                                      capture_output=True, text=True, timeout=120)

            before = state()
            conflicting = run([*args, '--inspect-conflicts'])
            self.assertEqual(conflicting.returncode, 2, conflicting.stderr)
            self.assertEqual(conflicting.stderr, '')
            result = json.loads(conflicting.stdout)
            self.assertEqual(result['counts']['conflicts'], 5)
            self.assertEqual([item['file'] for item in result['inputs']], ['0.json', '1.json', '2.json'])
            compatible = run([*args[:3], '--inspect-conflicts'])
            self.assertEqual(compatible.returncode, 0, compatible.stderr)
            self.assertEqual(json.loads(compatible.stdout)['status'], 'compatible')
            invalid = run([*args, '--source', 'later', 'bad.json', '--inspect-conflicts'])
            self.assertEqual(invalid.returncode, 2)
            self.assertEqual(invalid.stdout, '')
            self.assertEqual(json.loads(invalid.stderr)['code'], 'invalid_json')
            missing = run(['--source', 'p', 'missing.json', '--inspect-conflicts'])
            self.assertEqual(missing.returncode, 3)
            self.assertEqual(missing.stdout, '')
            for extra in (['-o', 'new-notes'], ['-o', 'existing-notes'], ['--update'], ['--dry-run'],
                          ['--format', 'csv'], ['--format', 'all']):
                with self.subTest(extra=extra):
                    rejected = run([*args, '--inspect-conflicts', *extra])
                    self.assertEqual(rejected.returncode, 2)
                    self.assertEqual(rejected.stdout, '')
            for options in (['0.json', '--inspect-conflicts'], ['--inspect-conflicts'], args[:3]):
                with self.subTest(options=options):
                    self.assertEqual(run(options).returncode, 2)
            self.assertEqual(state(), before)
            self.assertEqual(sorted(p.name for p in work.iterdir()),
                             ['0.json', '1.json', '2.json', 'bad.json', 'existing-notes'])
            # Export still rejects conflicts, including dry-run, and requires -o.
            for extra in ([], ['--dry-run'], ['--update']):
                rejected = run([*args, '-o', 'new-notes', *extra])
                self.assertEqual(rejected.returncode, 2)
                self.assertEqual(json.loads(rejected.stderr)['code'], 'article_conflict')
                self.assertFalse((work / 'new-notes').exists())


if __name__ == '__main__':
    unittest.main()
