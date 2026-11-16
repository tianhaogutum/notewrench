# SPDX-License-Identifier: AGPL-3.0-only
"""Pair scope contracts: exclude third-export ambiguity, never its evidence."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from notewrench.joplindupes import ExportError, Limits, analyze, load_export
from notewrench.joplindupes.report import render_json, render_markdown
from notewrench.joplindupes.rewrite import decision_template, rewrite_archive
from .synthetic import COMMAND, linked_files, uid, write_jex, write_raw


class TargetPairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = [linked_files(n) for n in (0, 1000, 2000)]
        self.paths = []

    def inputs(self, duplicate_source=2, duplicate_note=3):
        if duplicate_source is not None:
            offset = duplicate_source * 1000
            files = self.files[duplicate_source]
            files[uid(offset + 4) + '.md'] = files[uid(offset + duplicate_note) + '.md'].replace(
                uid(offset + duplicate_note).encode(), uid(offset + 4).encode())
        self.paths = [write_raw(self.root / 'left', self.files[0]),
                      write_jex(self.root / 'right.jex', self.files[1]),
                      write_jex(self.root / 'third.jex', self.files[2])]
        return [load_export(p) for p in self.paths]

    def test_unrelated_third_ambiguity_does_not_block_pair_or_disappear(self):
        exports = self.inputs()
        baseline = analyze(exports)
        self.assertEqual(baseline['summary']['exact_groups'], 1)
        self.assertEqual(baseline['summary']['blocked_notes'], 6)
        report = analyze(exports, target_pair=('s1', 's2'))
        self.assertEqual(report['target_pair'], ['s1', 's2'])
        self.assertEqual(report['summary']['exact_groups'], 3)
        self.assertEqual(report['summary']['blocked_notes'], 2)
        notes = {n['key']: n for n in report['notes']}
        for number in (1, 2):
            self.assertTrue(any(g['members'] == [f's1:{uid(number)}', f's2:{uid(1000+number)}']
                                for g in report['exact_duplicates']))
            self.assertIsNone(notes[f's3:{uid(2000+number)}']['exact_signature'])
        middle = notes[f's1:{uid(2)}']['note_links'][0]
        self.assertEqual(middle['status'], 'normalized')
        self.assertEqual(middle['candidate_targets'],
                         [f's1:{uid(3)}', f's2:{uid(1003)}', f's3:{uid(2003)}', f's3:{uid(2004)}'])
        self.assertEqual(middle['correspondence_sources'], ['s1', 's2'])
        self.assertEqual(middle['candidate_counts_by_source'], {'s1': 1, 's2': 1, 's3': 2})
        self.assertEqual(middle['ambiguous_sources'], ['s3'])
        self.assertEqual(middle['scope_ambiguous_sources'], [])
        third = notes[f's3:{uid(2002)}']['note_links'][0]
        self.assertEqual(third['status'], 'ambiguous_target')
        self.assertEqual(third['candidate_targets'], middle['candidate_targets'])
        self.assertEqual(third['correspondence_sources'], ['s1', 's2', 's3'])
        self.assertEqual(third['scope_ambiguous_sources'], ['s3'])
        self.assertEqual(notes[f's3:{uid(2001)}']['note_links'][0]['status'], 'target_blocked')
        self.assertIn('candidate_counts_by_source', render_markdown(report))
        self.assertEqual(render_json(report), render_json(analyze(exports, target_pair=['s2', 's1'])))
        # One scoped run must not contaminate a subsequent default assessment.
        self.assertEqual(render_json(baseline), render_json(analyze(exports)))

    def test_nested_duplicate_targets_in_excluded_export_still_block_its_index(self):
        report = analyze(self.inputs(duplicate_note=2), target_pair=['s1', 's2'])
        self.assertEqual(report['summary']['exact_groups'], 3)
        self.assertEqual(report['summary']['blocked_notes'], 1)
        index = next(n for n in report['notes'] if n['key'] == f's3:{uid(2001)}')
        self.assertEqual(index['note_links'][0]['status'], 'ambiguous_target')
        self.assertEqual(index['note_links'][0]['scope_ambiguous_sources'], ['s3'])

    def test_ambiguity_inside_selected_pair_is_not_overridden(self):
        exports = self.inputs(duplicate_source=1)
        for pair in (['s1', 's2'], ['s2', 's3']):
            with self.subTest(pair=pair):
                report = analyze(exports, target_pair=pair)
                self.assertEqual(report['summary']['exact_groups'], 1)
                self.assertEqual(report['summary']['blocked_notes'], 6)
                link = report['notes'][1]['note_links'][0]
                self.assertEqual(link['status'], 'ambiguous_target')
                self.assertEqual(link['scope_ambiguous_sources'], ['s2'])
        # Choosing the other two actual source keys works symmetrically.
        report = analyze(exports, target_pair=['s1', 's3'])
        self.assertEqual(report['summary']['exact_groups'], 3)
        self.assertEqual(report['summary']['blocked_notes'], 2)

    def test_pair_does_not_hide_changed_missing_or_cyclic_target(self):
        for change in ('changed', 'missing', 'cycle'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                right = linked_files(1000)
                if change == 'changed':
                    right[uid(1003)+'.md'] = right[uid(1003)+'.md'].replace(b'detail\n', b'other\n')
                elif change == 'missing':
                    del right[uid(1003)+'.md']
                else:
                    right[uid(1003)+'.md'] = right[uid(1003)+'.md'].replace(b'Caf\xc3\xa9 detail',
                                                                                        f':/{uid(1001)}'.encode())
                root = Path(temp)
                exports = [load_export(write_jex(root / 'a.jex', linked_files())),
                           load_export(write_jex(root / 'b.jex', right))]
                report = analyze(exports, target_pair=['s1', 's2'])
                self.assertEqual(report['exact_duplicates'], [])
                self.assertEqual(report['summary']['blocked_notes'], {'changed': 0, 'missing': 2, 'cycle': 3}[change])

    def test_invalid_pair_is_rejected(self):
        exports = self.inputs()
        for pair in ([], ['s1'], ['s1', 's1'], ['s1', 's4'], ['s1', 's2', 's3'],
                     's1,s2', [1, 2], [None, 's2']):
            with self.subTest(pair=pair), self.assertRaises(ValueError):
                analyze(exports, target_pair=pair)

    def test_scoped_decisions_require_same_scope_even_for_unchanged_groups(self):
        exports = self.inputs(duplicate_source=None)
        report = analyze(exports, target_pair=['s1', 's2'])
        template = decision_template(exports, report)
        self.assertEqual(template['target_pair'], ['s1', 's2'])
        for group in template['groups']:
            group['approved'], group['keeper'] = True, group['members'][0]
        # These groups happen to be identical under every scope. Bind the policy too.
        for n, pair in enumerate((None, ['s1', 's3'])):
            with self.subTest(pair=pair), self.assertRaisesRegex(ExportError, 'scope'):
                rewrite_archive(exports, analyze(exports, target_pair=pair), template,
                                self.root / f'rejected-{n}', Limits())
            self.assertFalse((self.root / f'rejected-{n}').exists())
        output = self.root / 'approved'
        rewrite_archive(exports, report, template, output, Limits())
        self.assertEqual(len([i for i in load_export(output / 'deduplicated.jex').items.values() if i.type == 1]), 3)
        rewrite = json.loads((output / 'rewrite-report.json').read_text())
        self.assertEqual(rewrite['target_pair'], ['s1', 's2'])
        # Old unscoped decisions remain valid only for an unscoped assessment.
        legacy = decision_template(exports, analyze(exports))
        self.assertNotIn('target_pair', legacy)
        for group in legacy['groups']:
            group['approved'], group['keeper'] = True, group['members'][0]
        with self.assertRaisesRegex(ExportError, 'scope'):
            rewrite_archive(exports, report, legacy, self.root / 'legacy-rejected', Limits())

    def test_packaged_cli_scope_evidence_and_invalid_arguments(self):
        self.inputs()
        original_jex = [p.read_bytes() for p in self.paths[1:]]
        def run(*args):
            return subprocess.run([*COMMAND, *map(str, self.paths), *args],
                                  cwd=self.root, text=True, capture_output=True, timeout=60)

        baseline = run()
        self.assertEqual(baseline.returncode, 3, baseline.stderr)
        self.assertEqual(json.loads(baseline.stdout)['summary']['blocked_notes'], 6)
        template = self.root / 'decisions.json'
        scoped = run('--target-pair', 's1', 's2', '--decision-template', str(template))
        self.assertEqual(scoped.returncode, 3, scoped.stderr)  # Excluded evidence remains incomplete.
        report = json.loads(scoped.stdout)
        self.assertEqual(report['summary']['exact_groups'], 3)
        self.assertEqual(report['summary']['blocked_notes'], 2)
        decision = json.loads(template.read_text())
        self.assertEqual(decision['target_pair'], ['s1', 's2'])
        for group in decision['groups']:
            group['approved'], group['keeper'] = True, group['members'][0]
        template.write_text(json.dumps(decision))
        output = self.root / 'retained-output'
        created = run('--target-pair', 's1', 's2', '--apply-decisions', str(template), '--output-dir', str(output))
        self.assertEqual(created.returncode, 0, created.stderr)
        rewrite = json.loads((output / 'rewrite-report.json').read_text())
        self.assertEqual(rewrite['validation']['notes'], 5)
        self.assertEqual([n['old_key'] for n in rewrite['retained_blocked_notes']],
                         [f's3:{uid(2001)}', f's3:{uid(2002)}'])
        markdown = run('--target-pair', 's2', 's1', '--format', 'markdown')
        self.assertEqual(markdown.returncode, 3)
        self.assertIn('Target correspondence pair: `s1`, `s2`', markdown.stdout)
        for args in (('--target-pair', 's1', 's1'), ('--target-pair', 's1', 's4'),
                     ('--target-pair', 's1'), ('--target-pair', 's1', 's2', '--target-pair', 's2', 's3')):
            with self.subTest(args=args):
                out = self.root / 'invalid.json'
                failed = run(*args, '-o', str(out))
                self.assertEqual(failed.returncode, 2)
                self.assertEqual(failed.stdout, '')
                self.assertFalse(out.exists())
        self.assertTrue(all((self.paths[0] / name).read_bytes() == data for name, data in self.files[0].items()))
        self.assertEqual([p.read_bytes() for p in self.paths[1:]], original_jex)

    def test_packaged_scoped_approval_requires_same_pair(self):
        self.inputs(duplicate_source=None)
        command = [*COMMAND, *map(str, self.paths)]
        path = self.root / 'decisions.json'
        template = subprocess.run(command + ['--target-pair', 's1', 's2', '--decision-template', str(path)],
                                  cwd=self.root, text=True, capture_output=True, timeout=60)
        self.assertEqual(template.returncode, 0, template.stderr)
        decision = json.loads(path.read_text())
        self.assertEqual(decision['target_pair'], ['s1', 's2'])
        for group in decision['groups']:
            group['approved'], group['keeper'] = True, group['members'][0]
        path.write_text(json.dumps(decision))
        for name, scope, expected in (('default', [], 2), ('different', ['s1', 's3'], 2),
                                      ('same', ['s2', 's1'], 0)):
            with self.subTest(scope=scope):
                output = self.root / name
                flags = ['--target-pair', *scope] if scope else []
                result = subprocess.run(command + flags + ['--apply-decisions', str(path), '--output-dir', str(output)],
                                        cwd=self.root, text=True, capture_output=True, timeout=60)
                self.assertEqual(result.returncode, expected, result.stderr)
                if expected:
                    self.assertIn('scope', result.stderr)
                    self.assertFalse(output.exists())
                else:
                    rewrite = json.loads((output / 'rewrite-report.json').read_text())
                    self.assertEqual(rewrite['target_pair'], ['s1', 's2'])
                    export = load_export(output / 'deduplicated.jex')
                    self.assertEqual(len([i for i in export.items.values() if i.type == 1]), 3)
