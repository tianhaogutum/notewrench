# SPDX-License-Identifier: AGPL-3.0-only
"""Exact-group inspection projects complete existing evidence after analysis."""
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
from notewrench.joplindupes.report import render_json
from .synthetic import COMMAND, linked_files, record, uid, write_jex, write_raw
from .test_inspect_note import inspection_files

ROOT = Path(__file__).resolve().parents[2]


class InspectExactGroupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        left, right = inspection_files()
        # Include an unrelated exact group so the focused inventory is tested.
        left.update({uid(n)+'.md': record(uid(n), 1, 'Other', 'other body') for n in (40, 41)})
        self.paths = [write_raw(self.root/'left', left), write_jex(self.root/'right.jex', right)]
        self.report = analyze([load_export(p) for p in self.paths])
        self.group = next(g for g in self.report['exact_duplicates'] if f's1:{uid(1)}' in g['members'])
        self.signature = self.group['signature']

    def cli(self, *args, paths=None, expected=3):
        run = subprocess.run([*COMMAND,
                              *map(str, self.paths if paths is None else paths), *map(str, args)],
                             cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, expected, run.stderr)
        return run

    def assert_view(self, view, report, group):
        self.assertEqual(set(view), {'schema_version', 'view', 'read_only', 'selected_signature',
                                    'scope', 'notice', 'group', 'notes', 'near_candidates',
                                    'identity_conflicts', 'tool', 'policy', 'target_pair',
                                    'sources', 'summary', 'metrics', 'warnings'})
        self.assertEqual(view['schema_version'], 1)
        self.assertEqual(view['view'], 'exact_group_inspection')
        self.assertIs(view['read_only'], True)
        self.assertEqual(view['selected_signature'], group['signature'])
        self.assertEqual(view['scope'], {'analysis': 'all_inputs', 'selection': 'one_existing_exact_group',
                                        'relationships': 'complete_groups_intersecting_selected_group'})
        self.assertEqual(view['group'], group)
        notes = {n['key']: n for n in report['notes']}
        self.assertEqual(view['notes'], [notes[key] for key in group['members']])
        for field in ('near_candidates', 'identity_conflicts'):
            self.assertEqual(view[field], [g for g in report[field] if set(group['members']).intersection(g['members'])])
        for field in ('tool', 'policy', 'target_pair', 'sources', 'summary', 'metrics', 'warnings'):
            self.assertEqual(view[field], report[field])
        for phrase in ('not a complete global inventory', 'all inputs', 'human keeper', 'explicit approval'):
            self.assertIn(phrase, view['notice'])

    def test_multisource_exact_subgroup_keeps_full_assessments_conflicts_and_larger_families(self):
        view = json.loads(self.cli('--inspect-exact-group', self.signature).stdout)
        self.assert_view(view, self.report, self.group)
        self.assertEqual(view['group']['members'], [f's1:{uid(1)}', f's1:{uid(2)}', f's2:{uid(1)}'])
        self.assertEqual(view['near_candidates'][0]['members'],
                         [f's1:{uid(1)}', f's1:{uid(2)}', f's2:{uid(1)}', f's2:{uid(3)}'])
        self.assertEqual(view['near_candidates'][0]['reasons'], ['todo_conflict_or_trash_state_differs'])
        self.assertEqual(view['identity_conflicts'][0]['members'], [f's1:{uid(1)}', f's2:{uid(1)}'])
        self.assertIn('metadata.custom', view['group']['conflicts'])
        self.assertNotIn('custom', view['notes'][0]['metadata'])
        self.assertEqual(view['notes'][2]['metadata']['custom'], '')
        self.assertEqual(view['summary']['blocked_notes'], 2)
        self.assertEqual(view['summary']['exact_groups'], 2)

    def test_loads_all_inputs_analyzes_once_before_lookup_and_does_not_mutate(self):
        before = copy.deepcopy(self.report)
        for selector, code in ((self.signature, 3), ('0'*64, 2), ('bad', 2)):
            with self.subTest(selector=selector), \
                    patch('notewrench.joplindupes.cli.load_export', wraps=load_export) as load, \
                    patch('notewrench.joplindupes.cli.analyze', return_value=self.report) as analysis, \
                    patch('sys.stdout', new_callable=io.StringIO) as stdout, \
                    patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(main([*map(str, self.paths), '--inspect-exact-group', selector]), code)
                self.assertEqual(load.call_count, 2)
                self.assertEqual(analysis.call_count, 1)
                self.assertEqual(len(analysis.call_args.args[0]), 2)
                if code == 3:
                    self.assert_view(json.loads(stdout.getvalue()), before, self.group)
                else:
                    self.assertEqual(stdout.getvalue(), '')
                self.assertEqual(self.report, before)

    def test_target_pair_keeps_excluded_source_members_and_link_evidence(self):
        files = [linked_files(n) for n in (0, 1000, 2000)]
        files[2][uid(2004)+'.md'] = files[2][uid(2003)+'.md'].replace(uid(2003).encode(), uid(2004).encode())
        paths = [write_jex(self.root/f'linked-{i}.jex', f) for i, f in enumerate(files)]
        full = analyze([load_export(p) for p in paths], target_pair=['s1', 's2'])
        for group in full['exact_duplicates']:
            with self.subTest(signature=group['signature']):
                view = json.loads(self.cli('--target-pair', 's2', 's1', '--inspect-exact-group',
                                           group['signature'], paths=paths).stdout)
                self.assert_view(view, full, group)
                self.assertEqual(view['target_pair'], ['s1', 's2'])
                self.assertEqual(view['summary']['blocked_notes'], 2)
                if f's1:{uid(2)}' in group['members']:
                    link = view['notes'][0]['note_links'][0]
                    self.assertEqual(len(link['candidate_targets']), 4)
                    self.assertEqual(link['ambiguous_sources'], ['s3'])
                    self.assertEqual(link['scope_ambiguous_sources'], [])
                if f's1:{uid(3)}' in group['members']:
                    self.assertIn(f's3:{uid(2004)}', group['members'])

    def test_clean_conflict_free_group_and_unrelated_warning_unknown_exit(self):
        files = {uid(n)+'.md': record(uid(n), 1, 'Same', 'same') for n in (1, 2)}
        for dirty, code in ((False, 0), (True, 3)):
            if dirty:
                files[uid(30)+'.md'] = record(uid(30), 1, 'Other', 'other', parent_id=uid(999))
                files[uid(31)+'.md'] = record(uid(31), 99, 'Unknown')
            path = write_jex(self.root/f'warnings-{dirty}.jex', files)
            full = analyze([load_export(path)])
            group = full['exact_duplicates'][0]
            self.assertEqual(group['conflicts'], {})
            view = json.loads(self.cli('--inspect-exact-group', group['signature'], paths=[path], expected=code).stdout)
            self.assert_view(view, full, group)
            self.assertEqual(full['summary']['blocked_notes'], 0)
            self.assertEqual(full['summary']['unknown_items'], int(dirty))
            self.assertEqual(bool(full['warnings']), dirty)

    def test_unknown_malformed_repeated_near_and_singleton_selectors_fail_atomically(self):
        for selector in ('0'*64, self.signature[:12], self.signature.upper(), ' '+self.signature,
                         self.signature+'\n', 'g'*64, '', f's1:{uid(1)}',
                         self.report['near_candidates'][0]['signature']):
            with self.subTest(selector=selector):
                out = self.root/'refused.json'
                run = self.cli('--inspect-exact-group', selector, '-o', out, expected=2)
                self.assertEqual(run.stdout, '')
                self.assertIn('--inspect-exact-group', run.stderr)
                self.assertFalse(out.exists())
        for second in (self.signature, '0'*64):
            run = self.cli('--inspect-exact-group', self.signature, '--inspect-exact-group', second, expected=2)
            self.assertEqual(run.stdout, '')
            self.assertIn('only once', run.stderr)
        for label, files in (
                ('single', {uid(1)+'.md': record(uid(1), 1, 'Only', 'single')}),
                ('near-only', {uid(n)+'.md': record(uid(n), 1, 'Near', 'same', is_todo=n-1) for n in (1, 2)}),
                ('no-notes', {uid(20)+'.md': record(uid(20), 2, 'Folder')})):
            path = write_jex(self.root/f'{label}.jex', files)
            full = analyze([load_export(path)])
            self.assertEqual(full['exact_duplicates'], [])
            selectors = [self.signature] + [n['exact_signature'] for n in full['notes'] if n['exact_signature']]
            for selector in selectors:
                with self.subTest(label=label, selector=selector):
                    out = self.root/'absent.json'
                    run = self.cli('--inspect-exact-group', selector, '-o', out, paths=[path], expected=2)
                    self.assertEqual(run.stdout, '')
                    self.assertIn('Unknown exact group', run.stderr)
                    self.assertFalse(out.exists())

    def test_invalid_later_input_fails_before_analysis_or_publication(self):
        bad = self.root/'bad.jex'
        bad.write_bytes(b'not a tar')
        out = self.root/'view.json'
        for selector in (self.signature, 'bad'):
            with patch('notewrench.joplindupes.cli.analyze') as analysis, \
                    patch('sys.stdout', new_callable=io.StringIO) as stdout, \
                    patch('sys.stderr', new_callable=io.StringIO):
                self.assertEqual(main([*map(str, self.paths), str(bad), '--inspect-exact-group', selector,
                                       '-o', str(out)]), 2)
            analysis.assert_not_called()
            self.assertEqual(stdout.getvalue(), '')
            self.assertFalse(out.exists())

    def test_incompatible_modes_leave_no_reports_decisions_or_archive(self):
        decision, directory, output = self.root/'decisions.json', self.root/'copy', self.root/'view.json'
        for flags in (['--format', 'markdown'], ['--inspect-note', f's1:{uid(1)}'],
                      ['--decision-template', decision], ['--apply-decisions', decision],
                      ['--apply-decisions', decision, '--output-dir', directory], ['--output-dir', directory]):
            with self.subTest(flags=flags):
                run = self.cli('--inspect-exact-group', self.signature, *flags, '-o', output, expected=2)
                self.assertEqual(run.stdout, '')
                self.assertIn('--inspect-exact-group', run.stderr)
                for path in (decision, directory, output):
                    self.assertFalse(path.exists())

    def test_output_paths_private_mode_limits_and_no_overwrite(self):
        out = self.root/'view.json'
        self.assertEqual(self.cli('--inspect-exact-group', self.signature, '-o', out).stdout, '')
        self.assert_view(json.loads(out.read_text()), self.report, self.group)
        self.assertEqual(out.stat().st_mode & 0o777, 0o600)
        before = out.read_bytes()
        symbolic, hard, ancestor = self.root/'symbolic', self.root/'hard', self.root/'alias'
        symbolic.symlink_to(out)
        hard.hardlink_to(out)
        ancestor.symlink_to(self.root, target_is_directory=True)
        for target in (out, symbolic, hard, self.paths[0], self.paths[1], self.paths[0]/'new.json',
                       ancestor/'new.json', self.root/'missing'/'new.json'):
            with self.subTest(target=target):
                self.assertEqual(self.cli('--inspect-exact-group', self.signature, '-o', target, expected=2).stdout, '')
        self.assertEqual(out.read_bytes(), before)
        self.assertFalse((self.root/'new.json').exists())
        for flag in ('--max-file-bytes', '--max-total-bytes', '--max-entries', '--max-metadata-bytes'):
            target = self.root/'limited.json'
            with self.subTest(flag=flag):
                run = self.cli('--inspect-exact-group', self.signature, flag, '1', '-o', target, expected=2)
                self.assertEqual(run.stdout, '')
                self.assertIn('limit', run.stderr.lower())
                self.assertFalse(target.exists())
        # Bound the encoded focused view, including full member/conflict evidence,
        # before stdout or exclusive file creation, using the existing renderer.
        with patch('notewrench.joplindupes.cli.render_json', side_effect=lambda view: render_json(view, max_bytes=20)), \
                patch('sys.stdout', new_callable=io.StringIO) as stdout, \
                patch('sys.stderr', new_callable=io.StringIO) as stderr:
            self.assertEqual(main([*map(str, self.paths), '--inspect-exact-group', self.signature,
                                   '-o', str(self.root/'budget.json')]), 2)
        self.assertEqual(stdout.getvalue(), '')
        self.assertIn('Report size limit', stderr.getvalue())
        self.assertFalse((self.root/'budget.json').exists())


if __name__ == '__main__':
    unittest.main()
