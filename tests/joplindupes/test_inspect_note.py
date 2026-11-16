# SPDX-License-Identifier: AGPL-3.0-only
"""Focused views retain complete full-analysis relationships and safety policy."""
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from notewrench.joplindupes import analyze, load_export
from notewrench.joplindupes.cli import main
from .synthetic import COMMAND, linked_files, record, uid, write_jex, write_raw

ROOT = Path(__file__).resolve().parents[2]
GROUPS = ('exact_duplicates', 'near_candidates', 'identity_conflicts')


def inspection_files():
    left = {
        uid(1)+'.md': record(uid(1), 1, 'Original', 'shared body'),
        uid(2)+'.md': record(uid(2), 1, 'Copy', 'shared body'),
        uid(10)+'.md': record(uid(10), 1, 'Singleton', 'unique body'),
        uid(11)+'.md': record(uid(11), 1, 'Blocked', f'follow :/{uid(12)}'),
        uid(12)+'.md': record(uid(12), 1, 'Empty'),
        uid(20)+'.md': record(uid(20), 2, 'Folder'),
    }
    right = {
        uid(1)+'.md': record(uid(1), 1, 'Renamed', 'shared body', custom=''),
        uid(3)+'.md': record(uid(3), 1, 'Task', 'shared body', is_todo=1),
    }
    return left, right


def incoming_files():
    # Deliberately insert notes out of ID order. Resource occurrences are not
    # note_links; unresolved tokens are and must count towards link_index.
    left = {
        uid(10)+'.md': record(uid(10), 1, 'Target', 'leaf body'),
        uid(2)+'.md': record(uid(2), 1, 'Second referrer', f':/{uid(10)}'),
        uid(1)+'.md': record(uid(1), 1, 'Repeated referrer',
                            f':/{uid(999)} :/{uid(101)} '
                            f':/{uid(10).upper()}#part :/{uid(10)}'),
        uid(101)+'.md': record(uid(101), 4, 'data.txt', mime='text/plain',
                              file_extension='txt', size=4),
        f'resources/{uid(101)}.txt': b'data',
    }
    right = {
        uid(10)+'.md': record(uid(10), 1, 'Other target', 'leaf body'),
        uid(1)+'.md': record(uid(1), 1, 'Other referrer', f':/{uid(10)}'),
    }
    return left, right


class InspectNoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        left, right = inspection_files()
        self.paths = [write_raw(self.root/'left', left), write_jex(self.root/'right.jex', right)]
        self.report = analyze([load_export(p) for p in self.paths])

    def cli(self, *args, expected=3, paths=None):
        run = subprocess.run([*COMMAND,
                              *map(str, self.paths if paths is None else paths), *map(str, args)],
                             cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, expected, run.stderr)
        return run

    def assert_view(self, view, report, key):
        self.assertEqual(view['view'], 'note_inspection')
        self.assertEqual(view['schema_version'], 1)
        self.assertTrue(view['read_only'])
        self.assertEqual(view['selected_key'], key)
        self.assertEqual(view['scope'], {'analysis': 'all_inputs',
                                       'selection': 'one_source_qualified_note',
                                       'relationships': 'complete_groups_containing_selected_note'})
        note = next(n for n in report['notes'] if n['key'] == key)
        self.assertEqual(view['note'], note)
        self.assertEqual(view['assessment_status'], 'blocked' if note['blocked_reasons'] else 'assessed')
        for field in ('tool', 'policy', 'target_pair', 'sources', 'summary', 'metrics', 'warnings'):
            self.assertEqual(view[field], report[field])
        for field in GROUPS:
            self.assertEqual(view[field], [g for g in report[field] if key in g['members']])
        self.assertEqual(view['has_review_relationships'], any(view[f] for f in GROUPS))
        self.assertNotIn('notes', view)
        self.assertIn('not a complete global inventory', view['notice'])
        self.assertIn('no keeper or merge approval', view['notice'])

    def test_literal_incoming_repeats_indices_and_source_identity(self):
        left, right = incoming_files()
        paths = [write_raw(self.root/'incoming', left), write_jex(self.root/'incoming.jex', right)]
        full = analyze([load_export(p) for p in paths])
        notes = {n['key']: n for n in full['notes']}
        for source, positions in (
                ('s1', [(f's1:{uid(1)}', 1), (f's1:{uid(1)}', 2), (f's1:{uid(2)}', 0)]),
                ('s2', [(f's2:{uid(1)}', 0)])):
            with self.subTest(source=source):
                key = f'{source}:{uid(10)}'
                view = json.loads(self.cli('--inspect-note', key, paths=paths).stdout)
                self.assert_view(view, full, key)
                self.assertEqual(view['incoming_note_links'], [
                    {'referring_key': ref, 'link_index': index, 'link': notes[ref]['note_links'][index]}
                    for ref, index in positions])
                self.assertTrue(all(e['link']['status'] == 'normalized' for e in view['incoming_note_links']))
        # The first referrer is blocked for an unrelated token; its literal
        # normalized occurrences still count and preserve original URL spelling.
        self.assertTrue(notes[f's1:{uid(1)}']['blocked_reasons'])
        self.assertEqual(notes[f's1:{uid(1)}']['note_links'][1]['original_url'],
                         f':/{uid(10).upper()}')

    def test_literal_incoming_self_cycles_blocked_targets_and_empty_inventory(self):
        bodies = {1: f'self :/{uid(1)}', 2: f'cycle :/{uid(3)}',
                  3: f'cycle :/{uid(2)}', 4: f'empty :/{uid(5)}', 5: '',
                  6: f'incoming cycle :/{uid(2)}', 10: 'isolated body'}
        path = write_jex(self.root/'cycles.jex', {
            uid(n)+'.md': record(uid(n), 1, str(n), body) for n, body in bodies.items()})
        full = analyze([load_export(path)])
        notes = {n['key']: n for n in full['notes']}
        for number, positions, status in ((1, [(1, 0)], 'cyclic_dependency'),
                                          (2, [(3, 0), (6, 0)], 'cyclic_dependency'),
                                          (5, [(4, 0)], 'target_blocked'), (10, [], None)):
            with self.subTest(number=number):
                key = f's1:{uid(number)}'
                view = json.loads(self.cli('--inspect-note', key, paths=[path]).stdout)
                self.assert_view(view, full, key)
                self.assertEqual(view['incoming_note_links'], [
                    {'referring_key': f's1:{uid(ref)}', 'link_index': index,
                     'link': notes[f's1:{uid(ref)}']['note_links'][index]} for ref, index in positions])
                for entry in view['incoming_note_links']:
                    self.assertEqual(entry['link']['status'], status)
                if number == 10:
                    self.assertEqual(view['assessment_status'], 'assessed')
                    self.assertFalse(view['has_review_relationships'])
                    self.assertEqual(view['summary']['blocked_notes'], 6)

    def test_exact_subgroup_near_family_and_identity_conflict_remain_complete(self):
        key = f's1:{uid(1)}'
        view = json.loads(self.cli('--inspect-note', key).stdout)
        self.assert_view(view, self.report, key)
        self.assertEqual(view['exact_duplicates'][0]['members'],
                         [key, f's1:{uid(2)}', f's2:{uid(1)}'])
        self.assertEqual(view['near_candidates'][0]['members'],
                         [key, f's1:{uid(2)}', f's2:{uid(1)}', f's2:{uid(3)}'])
        self.assertEqual(view['near_candidates'][0]['reasons'], ['todo_conflict_or_trash_state_differs'])
        self.assertEqual(view['identity_conflicts'][0]['members'], [key, f's2:{uid(1)}'])
        self.assertIn('metadata.custom', view['identity_conflicts'][0]['conflicts'])
        # Same note ID selects a different occurrence, never all ID matches.
        self.assert_view(json.loads(self.cli('--inspect-note', f's2:{uid(1)}').stdout),
                         self.report, f's2:{uid(1)}')

    def test_singleton_and_blocked_without_groups_are_distinct_and_keep_global_exit(self):
        for number, status in ((10, 'assessed'), (11, 'blocked'), (12, 'blocked')):
            with self.subTest(number=number):
                view = json.loads(self.cli('--inspect-note', f's1:{uid(number)}').stdout)
                self.assert_view(view, self.report, f's1:{uid(number)}')
                self.assertEqual(view['assessment_status'], status)
                self.assertFalse(view['has_review_relationships'])
                self.assertEqual(view['summary']['blocked_notes'], 2)
                if number == 11:
                    self.assertEqual(view['note']['note_links'][0]['status'], 'target_blocked')
                    self.assertEqual(view['note']['note_links'][0]['target_key'], f's1:{uid(12)}')

    def test_projection_loads_each_input_and_analyzes_once_without_mutation(self):
        before = copy.deepcopy(self.report)
        with patch('notewrench.joplindupes.cli.load_export', wraps=load_export) as load, \
                patch('notewrench.joplindupes.cli.analyze', return_value=self.report) as analysis, \
                patch('sys.stdout', new_callable=io.StringIO) as stdout:
            code = main([*map(str, self.paths), '--inspect-note', f's1:{uid(12)}'])
        self.assertEqual(code, 3)
        self.assertEqual(load.call_count, 2)
        self.assertEqual(analysis.call_count, 1)
        self.assertEqual(len(analysis.call_args.args[0]), 2)
        self.assertEqual(self.report, before)
        view = json.loads(stdout.getvalue())
        self.assert_view(view, before, f's1:{uid(12)}')
        self.assertEqual(view['incoming_note_links'], [
            {'referring_key': f's1:{uid(11)}', 'link_index': 0,
             'link': next(n for n in before['notes'] if n['key'] == f's1:{uid(11)}')['note_links'][0]}])

    def test_target_pair_preserves_excluded_source_ambiguity_and_full_groups(self):
        files = [linked_files(n) for n in (0, 1000, 2000)]
        files[2][uid(2004)+'.md'] = files[2][uid(2003)+'.md'].replace(uid(2003).encode(), uid(2004).encode())
        paths = [write_jex(self.root/f'linked-{i}.jex', f) for i, f in enumerate(files)]
        full = analyze([load_export(p) for p in paths], target_pair=['s1', 's2'])
        for key in (f's1:{uid(2)}', f's3:{uid(2002)}', f's3:{uid(2001)}'):
            with self.subTest(key=key):
                view = json.loads(self.cli('--target-pair', 's2', 's1', '--inspect-note', key, paths=paths).stdout)
                self.assert_view(view, full, key)
                self.assertEqual(view['target_pair'], ['s1', 's2'])
                self.assertEqual(view['summary']['blocked_notes'], 2)
        first = json.loads(self.cli('--target-pair', 's1', 's2', '--inspect-note', f's1:{uid(2)}', paths=paths).stdout)
        link = first['note']['note_links'][0]
        self.assertEqual(link['status'], 'normalized')
        self.assertEqual(len(link['candidate_targets']), 4)
        self.assertEqual(link['ambiguous_sources'], ['s3'])
        self.assertEqual(link['scope_ambiguous_sources'], [])

        # Global equivalent candidates, including an excluded-source duplicate,
        # never add incoming edges to the selected literal target occurrence.
        for source, number, referring in (('s1', 3, [(f's1:{uid(2)}', 0)]),
                                          ('s3', 2003, [(f's3:{uid(2002)}', 0)]),
                                          ('s3', 2004, [])):
            with self.subTest(incoming_target=(source, number)):
                key = f'{source}:{uid(number)}'
                view = json.loads(self.cli('--target-pair', 's1', 's2', '--inspect-note', key, paths=paths).stdout)
                self.assert_view(view, full, key)
                self.assertIn(key, link['candidate_targets'])
                self.assertEqual(view['incoming_note_links'], [
                    {'referring_key': ref, 'link_index': index,
                     'link': next(n for n in full['notes'] if n['key'] == ref)['note_links'][index]}
                    for ref, index in referring])

    def test_unknown_malformed_non_note_and_duplicate_selectors_fail_atomically(self):
        cases = [(['--inspect-note', f's3:{uid(1)}'], 'Unknown source'),
                 (['--inspect-note', f's1:{uid(999)}'], 'Unknown note'),
                 (['--inspect-note', f's1:{uid(20)}'], 'Unknown note'),
                 (['--inspect-note', uid(1)], 'sN:'),
                 (['--inspect-note', f's01:{uid(1)}'], 'sN:'),
                 (['--inspect-note', f's1:{uid(15).upper()}'], 'sN:'),
                 (['--inspect-note', f's1:{uid(1)}', '--inspect-note', f's1:{uid(1)}'], 'only once'),
                 (['--inspect-note', f's1:{uid(1)}', '--inspect-note', f's2:{uid(1)}'], 'only once')]
        out = self.root/'invalid.json'
        for args, message in cases:
            with self.subTest(args=args):
                run = self.cli(*args, '-o', out, expected=2)
                self.assertEqual(run.stdout, '')
                self.assertIn(message, run.stderr)
                self.assertFalse(out.exists())

    def test_malformed_later_export_precedes_selection_and_never_publishes(self):
        bad = self.root/'bad.jex'
        bad.write_bytes(b'not a tar export')
        for selector in (f's1:{uid(1)}', f's9:{uid(999)}'):
            out = self.root/'unpublished.json'
            run = self.cli('--inspect-note', selector, '-o', out, paths=[self.paths[0], bad], expected=2)
            self.assertEqual(run.stdout, '')
            self.assertNotIn('Unknown source', run.stderr)
            self.assertFalse(out.exists())

    def test_markdown_and_decision_modes_are_incompatible_without_artifacts(self):
        decision, output = self.root/'decision.json', self.root/'copy'
        for extra in (['--format', 'markdown'], ['--decision-template', decision],
                      ['--apply-decisions', decision, '--output-dir', output], ['--output-dir', output]):
            with self.subTest(extra=extra):
                run = self.cli('--inspect-note', f's1:{uid(1)}', *extra, expected=2)
                self.assertEqual(run.stdout, '')
                self.assertIn('--inspect-note', run.stderr)
                self.assertFalse(decision.exists())
                self.assertFalse(output.exists())

    def test_global_warnings_and_unknown_items_survive_singleton_selection(self):
        extra = {uid(30)+'.md': record(uid(30), 1, 'Only', 'only body', parent_id=uid(999)),
                 uid(31)+'.md': record(uid(31), 99, 'Unknown')}
        path = write_jex(self.root/'warnings.jex', extra)
        full = analyze([load_export(path)])
        self.assertEqual(full['summary']['blocked_notes'], 0)
        self.assertEqual(full['summary']['unknown_items'], 1)
        self.assertTrue(full['warnings'])
        view = json.loads(self.cli('--inspect-note', f's1:{uid(30)}', paths=[path]).stdout)
        self.assert_view(view, full, f's1:{uid(30)}')

    def test_output_paths_limits_and_no_overwrite_remain_protected(self):
        key = f's1:{uid(10)}'
        out = self.root/'view.json'
        run = self.cli('--inspect-note', key, '-o', out)
        self.assertEqual(run.stdout, '')
        self.assertEqual(out.stat().st_mode & 0o777, 0o600)
        self.assert_view(json.loads(out.read_text()), self.report, key)
        before = out.read_bytes()
        symlink, hardlink = self.root/'symbolic', self.root/'hard'
        symlink.symlink_to(out)
        hardlink.hardlink_to(out)
        ancestor = self.root/'alias'
        ancestor.symlink_to(self.root, target_is_directory=True)
        for target in (out, symlink, hardlink, self.paths[0]/'report.json', ancestor/'new.json'):
            with self.subTest(target=target):
                self.assertEqual(self.cli('--inspect-note', key, '-o', target, expected=2).stdout, '')
        self.assertEqual(out.read_bytes(), before)
        self.assertFalse((self.root/'new.json').exists())
        for limit in ('--max-file-bytes', '--max-total-bytes', '--max-entries', '--max-metadata-bytes'):
            with self.subTest(limit=limit):
                target = self.root/'limited.json'
                run = self.cli('--inspect-note', key, limit, '1', '-o', target, expected=2)
                self.assertEqual(run.stdout, '')
                self.assertIn('limit', run.stderr.lower())
                self.assertFalse(target.exists())

    def test_clean_singleton_exits_zero(self):
        path = write_jex(self.root/'single.jex', {uid(10)+'.md': record(uid(10), 1, 'Only', 'unique')})
        view = json.loads(self.cli('--inspect-note', f's1:{uid(10)}', paths=[path], expected=0).stdout)
        self.assertEqual(view['assessment_status'], 'assessed')
        self.assertFalse(view['has_review_relationships'])


if __name__ == '__main__':
    unittest.main()
