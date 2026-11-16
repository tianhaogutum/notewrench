# SPDX-License-Identifier: AGPL-3.0-only
"""Opt-in value inspection: raw values, presence and unchanged default reports."""
from __future__ import annotations

import copy
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from notewrench.wallabagnotes import core
from notewrench.wallabagnotes.cli import main

ROOT = Path(__file__).resolve().parents[2]


def inputs():
    before, after = [(ROOT / f'examples/wallabag-notes/conflicts/values-{name}.json').read_bytes()
                     for name in ('before', 'after')]
    # Include reverse differences and repeated versions at distinct locations.
    return [('p', before), ('p', after), ('p', before)]


def decoded(data):
    return json.loads(data, parse_float=Decimal)


def verify_values(case, report, sources):
    roots = [decoded(data) for _, data in sources]
    for conflict in report['conflicts']:
        case.assertEqual(list(conflict['field_values']), conflict['differing_fields'])
        case.assertNotIn('id', conflict['differing_fields'])
        for side in ('first', 'second'):
            location = conflict[side]
            index = location['input_order']
            case.assertEqual(location['sha256'], hashlib.sha256(sources[index][1]).hexdigest())
            record = roots[index][location['source_order']]
            expected_path = f"$inputs[{index}].articles[{location['source_order']}]"
            if 'annotation_order' in location:
                record = record['annotations'][location['annotation_order']]
                expected_path += f".annotations[{location['annotation_order']}]"
            case.assertEqual(location['path'], expected_path)
            for field, values in conflict['field_values'].items():
                expected = {'present': field in record}
                if field in record:
                    expected['value'] = record[field]
                case.assertEqual(values[side], expected)


class ConflictValueTests(unittest.TestCase):
    def test_cli_value_details_match_both_original_locations(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = []
            for i, (namespace, data) in enumerate(inputs()):
                path = Path(temporary) / f'{i}.json'
                path.write_bytes(data)
                args += ['--source', namespace, str(path)]
            completed = subprocess.run([sys.executable, '-m', 'notewrench', 'wallabag-notes', *args,
                                        '--inspect-conflicts', '--conflict-values'], cwd=ROOT,
                                       capture_output=True, text=True, timeout=120)
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(completed.stderr, '')
            report = decoded(completed.stdout)
            self.assertEqual(report['counts'], dict(article_conflicts=2, annotation_conflicts=2, conflicts=4))
            verify_values(self, report, inputs())
            article, annotation = report['conflicts'][:2]
            self.assertEqual(article['field_values']['title'],
                             dict(first=dict(present=True, value=None), second=dict(present=False)))
            self.assertEqual(annotation['field_values']['text']['first'], dict(present=True, value=''))
            self.assertEqual(annotation['field_values']['ranges']['first']['value'][1],
                             Decimal('0.123456789012345678901'))
            self.assertEqual(annotation['field_values']['ranges']['second']['value'][2], Decimal('2e-999'))

    def test_api_opt_in_only_adds_values_and_preserves_within_file_pairs(self):
        sources = inputs()
        # Additional within-file occurrences, nonzero article/annotation orders.
        first, second = [json.loads(data) for _, data in sources[:2]]
        for records in (first, second):
            records.insert(0, dict(id=2, title='Other', annotations=[]))
            records[1]['annotations'].insert(0, dict(id=10, quote='Other', text='', ranges=[]))
        sources.append(('p', json.dumps(first + second).encode()))
        default = core.inspect_sources(sources)
        detailed = core.inspect_sources(sources, include_values=True)
        verify_values(self, detailed, sources)
        stripped = copy.deepcopy(detailed)
        for conflict in stripped['conflicts']:
            del conflict['field_values']
        self.assertEqual(stripped, default)
        self.assertEqual(core.json_encode(default), core.json_encode(core.inspect_sources(sources, include_values=False)))
        self.assertTrue(any(c['first']['input_order'] == c['second']['input_order']
                            for c in detailed['conflicts']))
        self.assertEqual(decoded(core.json_encode(detailed)), detailed)

    def test_normalized_ids_equal_fields_and_scopes_do_not_create_value_entries(self):
        sources = inputs()
        # Keep exact numbers by replacing just ID tokens in the original bytes.
        normalized = sources[0][1].replace(b'"id": 1,', b'"id": "001",').replace(b'"id": 9,', b'"id": "009",')
        compatible = [sources[0], ('p', normalized), ('work', sources[1][1]), ('empty', b'[]')]
        self.assertEqual(core.inspect_sources(compatible, include_values=True), core.inspect_sources(compatible))
        self.assertEqual(core.inspect_sources(compatible)['conflicts'], [])

    def test_later_invalid_input_has_no_partial_stdout_and_does_not_render(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = []
            for i, (namespace, data) in enumerate(inputs()):
                path = root / f'{i}.json'
                path.write_bytes(data)
                args += ['--source', namespace, str(path)]
            invalid_annotation = json.loads(inputs()[0][1])
            del invalid_annotation[0]['annotations'][0]['quote']
            cases = [(b'{', 'invalid_json'), (b'{}', 'unsupported_format'),
                     (b'[{"id":1,"id":2}]', 'duplicate_json_key'),
                     (json.dumps(invalid_annotation).encode(), 'missing_quote')]
            for data, code in cases:
                bad = root / 'bad.json'
                bad.write_bytes(data)
                stdout, stderr = io.StringIO(), io.StringIO()
                with self.subTest(code=code), redirect_stdout(stdout), redirect_stderr(stderr):
                    result = main([*args, '--source', 'later', str(bad), '--inspect-conflicts', '--conflict-values'])
                self.assertEqual(result, 2)
                self.assertEqual(stdout.getvalue(), '')
                self.assertEqual(json.loads(stderr.getvalue())['code'], code)
            with patch('notewrench.wallabagnotes.cli.render', side_effect=AssertionError('render')), \
                 patch('notewrench.wallabagnotes.cli.publish', side_effect=AssertionError('publish')), \
                 patch('notewrench.wallabagnotes.cli.check_output', side_effect=AssertionError('output')), \
                 patch('notewrench.wallabagnotes.update.plan_update', side_effect=AssertionError('update')), \
                 redirect_stdout(io.StringIO()):
                self.assertEqual(main([*args, '--inspect-conflicts', '--conflict-values']), 2)

    def test_value_flag_rejected_outside_inspection_and_export_options_still_rejected(self):
        source = str(ROOT / 'examples/wallabag-notes/conflicts/values-before.json')
        prefix = ['--source', 'p', source]
        cases = [['--conflict-values'], [source, '-o', 'unused', '--conflict-values'],
                 [*prefix, '-o', 'unused', '--conflict-values'],
                 [*prefix, '-o', 'unused', '--update', '--conflict-values'],
                 [*prefix, '-o', 'unused', '--dry-run', '--conflict-values'],
                 ['--inspect-managed', 'missing', '--conflict-values'],
                 [source, '--inspect-conflicts', '--conflict-values']]
        cases += [[*prefix, '--inspect-conflicts', '--conflict-values', *extra]
                  for extra in (['-o', 'unused'], ['--format', 'all'], ['--update'], ['--dry-run'],
                                ['--inspect-managed', 'missing'])]
        for args in cases:
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.subTest(args=args), redirect_stdout(stdout), redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as caught:
                    main(args)
            self.assertEqual(caught.exception.code, 2)
            self.assertEqual(stdout.getvalue(), '')
            self.assertIn('error:', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()
