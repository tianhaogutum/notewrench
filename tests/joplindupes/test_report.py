# SPDX-License-Identifier: AGPL-3.0-only
"""Compact Markdown evidence stays separate from complete conflict values."""
import json
from pathlib import Path
import re
import tempfile
import unittest

from notewrench.joplindupes import Limits, analyze, load_export
from notewrench.joplindupes.report import render_json, render_markdown
from notewrench.joplindupes.rewrite import decision_template, rewrite_archive
from .synthetic import example_files, linked_files, record, uid, write_jex
from .test_rewrite_retained import preservation_files, scoped_files


class MarkdownReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def output_review(self, files, partial=False):
        exports = [load_export(write_jex(self.root / f's{n}.jex', records))
                   for n, records in enumerate(files)]
        report = analyze(exports, target_pair=['s1', 's2'] if len(files) > 1 else None)
        decisions = decision_template(exports, report)
        if partial:
            decisions['groups'] = [g for g in decisions['groups']
                                   if f's1:{uid(3)}' not in g['members']]
        for group in decisions['groups']:
            group['approved'], group['keeper'] = True, group['members'][0]
        output = self.root / 'copy'
        rewrite_archive(exports, report, decisions, output, Limits())
        return analyze([load_export(output / 'deduplicated.jex')])

    def assert_overview(self, report, fields, exact_fields=None):
        before = render_json(report)
        markdown = render_markdown(report)
        overview = markdown.split('## Near candidates\n\n', 1)[1].split(
            '\n\n## Complete evidence', 1)[0]
        expected = []
        for group, names in zip(report['near_candidates'], fields, strict=True):
            expected.append('- ' + ', '.join(f'`{key}`' for key in group['members'])
                            + '; ' + ', '.join(group['reasons'])
                            + '; conflicting fields (manual comparison): '
                            + (', '.join(f'`{name}`' for name in names) or 'none') + '.')
        self.assertEqual(overview, '\n'.join(expected) or 'No near candidate groups found.')
        # Neither the renderer nor the compact names may replace/mutate evidence.
        self.assertEqual(render_json(report), before)
        block = markdown.split('## Complete evidence\n\n', 1)[1].split('\n\n', 1)[1]
        fence, evidence = block.split('\n', 1)
        self.assertRegex(fence, r'^`{3,}json$')
        closing = fence[:-4]
        self.assertTrue(evidence.endswith(closing + '\n'))
        self.assertEqual(evidence[:-len(closing + '\n')], before)
        self.assertEqual(json.loads(evidence[:-len(closing + '\n')]), report)
        exact = markdown.split('## Exact duplicates\n\n', 1)[1].split('\n\n## Near candidates', 1)[0]
        if exact_fields is None:
            exact_fields = [sorted(g['conflicts']) for g in report['exact_duplicates']]
        self.assertEqual(exact, '\n'.join(
            '- ' + ', '.join(f'`{k}`' for k in g['members'])
            + f"; conflicting fields: {len(g['conflicts'])}. Review all conflicts below."
            + ' Conflicting fields (manual review): '
            + (', '.join(f'`{name}`' for name in names) or 'none') + '.'
            for g, names in zip(report['exact_duplicates'], exact_fields, strict=True))
            or 'No exact duplicate groups found.')
        return overview

    def assert_identity_overview(self, report, fields):
        # Also enforce unchanged exact/near overviews and complete JSON evidence.
        self.assert_overview(report, [sorted(g['conflicts']) for g in report['near_candidates']])
        markdown = render_markdown(report)
        self.assertIn('## Identity conflicts\n\n', markdown)
        overview = markdown.split('## Identity conflicts\n\n', 1)[1].split(
            '\n\n## Exact duplicates', 1)[0]
        expected = [
            'Same note ID across snapshots does not imply equal content. '
            'Manual review required; no keeper or merge approval is selected.', '']
        for group, names in zip(report['identity_conflicts'], fields, strict=True):
            expected.append(f"- Original note ID `{group['id']}`; occurrences: "
                            + ', '.join(f'`{key}`' for key in group['members'])
                            + '; conflicting fields (manual review): '
                            + (', '.join(f'`{name}`' for name in names) or 'none') + '.')
        if not report['identity_conflicts']:
            expected.append('No identity conflicts found.')
        self.assertEqual(overview, '\n'.join(expected))
        return overview

    def assert_blocked_overview(self, report, expected_notes):
        # Existing sections and complete embedded JSON must remain intact.
        self.assert_identity_overview(report, [sorted(g['conflicts'])
                                               for g in report['identity_conflicts']])
        markdown = render_markdown(report)
        self.assertIn('## Blocked notes\n\n', markdown)
        overview = markdown.split('## Blocked notes\n\n', 1)[1].split(
            '\n\n## Identity conflicts', 1)[0]
        expected = ['Manual review required; blocked notes do not establish duplicate status '
                    'or target correspondence. No keeper or merge approval is selected.', '']
        expected += ['- `' + key + '`; blocked reasons: '
                     + ', '.join(f'`{reason}`' for reason in reasons) + '.'
                     for key, reasons in expected_notes]
        if not expected_notes:
            expected.append('No blocked notes.')
        self.assertEqual(overview, '\n'.join(expected))
        return overview

    def test_blocked_cycles_self_empty_and_incoming_preserve_note_and_reason_order(self):
        report = analyze([load_export(write_jex(self.root / 'blocked.jex', preservation_files()))])
        reasons = {
            10: [f'note_link_cyclic_dependency:{uid(11)}'],
            11: [f'note_link_cyclic_dependency:{uid(10)}'],
            12: [f'note_link_cyclic_dependency:{uid(12)}'],
            13: ['empty_body_insufficient_evidence'],
            14: ['empty_body_insufficient_evidence'],
            15: [f'note_link_cyclic_dependency:{uid(10)}', f'note_link_target_blocked:{uid(13)}'],
        }
        self.assertEqual([(n['key'], n['blocked_reasons']) for n in report['notes'] if n['blocked_reasons']],
                         [(f's1:{uid(n)}', r) for n, r in reasons.items()])
        self.assertEqual(report['summary']['blocked_notes'], 6)
        self.assertTrue(all(n['exact_signature'] is None for n in report['notes'] if n['blocked_reasons']))
        # The projection copies existing order; it does not sort or reassess it.
        report['notes'].reverse()
        report['notes'][0]['blocked_reasons'].reverse()
        reasons[15] = reasons[15][::-1]
        self.assert_blocked_overview(report, [(f's1:{uid(n)}', reasons[n]) for n in reversed(reasons)])

    def test_blocked_scoped_source_keys_and_reparsed_output_keys_are_distinct(self):
        files = scoped_files()
        report = analyze([load_export(write_jex(self.root / f'scope-{i}.jex', f))
                          for i, f in enumerate(files)], target_pair=['s1', 's2'])
        expected = [(f's3:{uid(2001)}', [f'note_link_target_blocked:{uid(2002)}']),
                    (f's3:{uid(2002)}', [f'note_link_ambiguous_target:{uid(2003)}'])]
        self.assertEqual([(n['key'], n['blocked_reasons']) for n in report['notes'] if n['blocked_reasons']],
                         expected)
        self.assert_blocked_overview(report, expected)
        output = self.output_review(files, partial=True)
        blocked = [(n['key'], n['blocked_reasons']) for n in output['notes'] if n['blocked_reasons']]
        self.assertEqual(len(blocked), 4)
        self.assertTrue(all(key.startswith('s1:') for key, _ in blocked))
        self.assertTrue(set(key for key, _ in blocked).isdisjoint(key for key, _ in expected))
        self.assert_blocked_overview(output, blocked)

    def test_blocked_empty_inventory_including_no_notes(self):
        for name, files in [('clean', example_files()),
                            ('no-notes', {uid(100) + '.md': record(uid(100), 2, 'Folder')})]:
            with self.subTest(name=name):
                report = analyze([load_export(write_jex(self.root / f'{name}.jex', files))])
                self.assertEqual(report['summary']['blocked_notes'], 0)
                self.assert_blocked_overview(report, [])

    def test_blocked_safe_identifiers_do_not_render_adversarial_values(self):
        value = '````` [KEEPER_SELECTED](javascript:alert(1)) <img src=x onerror=alert(1)>'
        files = {uid(0xabcdef) + '.md': record(uid(0xabcdef), 1, value,
                 f'[missing](:/{uid(0xfedcba).upper()})', is_todo=value, __private__=value)}
        # s10 is a permitted source key; equal IDs in other sources stay separate.
        exports = [load_export(write_jex(self.root / f'safe-{i}.jex', files)) for i in range(10)]
        report = analyze(exports)
        reasons = ['invalid_state:is_todo', f'note_link_unresolved:{uid(0xfedcba)}',
                   f'unresolved_internal_link:{uid(0xfedcba)}']
        self.assertEqual([n['blocked_reasons'] for n in report['notes']], [reasons] * 10)
        overview = self.assert_blocked_overview(report, [(f's{i}:{uid(0xabcdef)}', reasons)
                                                        for i in range(1, 11)])
        self.assertNotIn('KEEPER_SELECTED', overview)
        self.assertNotIn('<img', overview)
        self.assertIn('``````json', render_markdown(report))

    def test_identity_changed_target_keeps_groups_and_occurrences_in_review_order(self):
        left, right = linked_files(), linked_files()
        right[uid(3) + '.md'] = right[uid(3) + '.md'].replace(b'detail\n', b'changed detail\n')
        report = analyze([load_export(write_jex(self.root / f'changed-{i}.jex', files))
                          for i, files in enumerate((left, right))])
        self.assertEqual(report['exact_duplicates'], [])
        self.assertEqual(report['summary']['identity_conflicts'], 3)
        expected = [['content_signature', 'note_links'], ['content_signature', 'note_links'],
                    ['content_signature', 'normalized_body_sha256']]
        for n, (group, fields) in enumerate(zip(report['identity_conflicts'], expected, strict=True), 1):
            self.assertEqual(group['id'], uid(n))
            self.assertEqual(group['members'], [f's1:{uid(n)}', f's2:{uid(n)}'])
            self.assertEqual(sorted(group['conflicts']), fields)
            group['members'].reverse()
            group['conflicts'] = dict(reversed(list(group['conflicts'].items())))
        report['identity_conflicts'].reverse()
        self.assert_identity_overview(report, expected[::-1])

    def test_identity_context_state_and_missing_metadata_remain_distinct(self):
        left, right = example_files(), example_files()
        right[uid(1) + '.md'] = right[uid(1) + '.md'].replace(
            b'Observation', b'Changed title').replace(uid(100).encode(), uid(101).encode()).replace(
                b'type_: 1', b'custom: \ntype_: 1')
        right[uid(2) + '.md'] = right[uid(2) + '.md'].replace(b'is_todo: 0', b'is_todo: 1')
        report = analyze([load_export(write_jex(self.root / f'context-{i}.jex', files))
                          for i, files in enumerate((left, right))])
        first, second = report['identity_conflicts']
        self.assertEqual([first['id'], second['id']], [uid(1), uid(2)])
        self.assertEqual([(v['present'], v['value']) for v in first['conflicts']['metadata.custom']],
                         [(False, None), (True, '')])
        fields = [['metadata.custom', 'metadata.parent_id', 'notebook', 'title'],
                  ['metadata.is_todo', 'state']]
        self.assertEqual([sorted(g['conflicts']) for g in (first, second)], fields)
        self.assert_identity_overview(report, fields)

    def test_identity_names_are_literal_and_adversarial_values_stay_in_evidence(self):
        names = ['_leading__', 'A0', 'a__b___c', '__', 'z' * 128]
        value = '````` [KEEPER_SELECTED](javascript:alert(1)) <img src=x onerror=alert(1)>'
        exports = [load_export(write_jex(self.root / f'names-{n}.jex', {
            uid(1) + '.md': record(uid(1), 1, 'Same', 'body',
                                   **{name: value if n == 1 else '' for name in names})}))
            for n in (1, 2, 3)]
        report = analyze(exports)
        group, = report['identity_conflicts']
        self.assertEqual(group['members'], [f's{n}:{uid(1)}' for n in (1, 2, 3)])
        fields = sorted('metadata.' + name for name in names)
        self.assertEqual(sorted(group['conflicts']), fields)
        overview = self.assert_identity_overview(report, [fields])
        self.assertEqual(re.findall(r'`([^`]+)`', overview.split('manual review): ', 1)[1]), fields)
        self.assertNotIn('KEEPER_SELECTED', overview)
        self.assertNotIn('<img', overview)
        self.assertIn('``````json', render_markdown(report))

    def test_identity_empty_inventory_including_equal_same_id_occurrences(self):
        same = {uid(1) + '.md': record(uid(1), 1, 'Same', 'body')}
        for name, files in [('remapped', [linked_files(), linked_files(1000)]),
                            ('same-id-equal', [same, same]),
                            ('no-notes', [{uid(100) + '.md': record(uid(100), 2, 'Folder')}])]:
            with self.subTest(name=name):
                report = analyze([load_export(write_jex(self.root / f'{name}-{n}.jex', f))
                                  for n, f in enumerate(files)])
                self.assertEqual(report['identity_conflicts'], [])
                self.assertEqual(report['summary']['identity_conflicts'], 0)
                self.assert_identity_overview(report, [])

    def test_exact_output_resource_names_keep_review_order(self):
        report = self.output_review(scoped_files(), partial=True)
        self.assertEqual(report['summary']['exact_groups'], 1)
        group, = report['exact_duplicates']
        group['conflicts'] = dict(reversed(list(group['conflicts'].items())))
        group['members'].reverse()
        self.assert_overview(report, [sorted(g['conflicts']) for g in report['near_candidates']],
                             [['normalized_body_sha256', 'resources']])

    def test_exact_context_absence_and_no_conflicts_are_distinct(self):
        files = example_files()
        for number, title, folder, extra in (
            (20, 'First title', 100, {'custom': ''}),
            (21, 'Second title', 101, {}),
            (22, 'Same title', 100, {}),
            (23, 'Same title', 100, {}),
        ):
            files[uid(number) + '.md'] = record(
                uid(number), 1, title, 'Context body' if number < 22 else 'Identical body',
                parent_id=uid(folder), **extra)
        report = analyze([load_export(write_jex(self.root / 'context.jex', files))])
        report['exact_duplicates'].reverse()
        expected = []
        for group in report['exact_duplicates']:
            group['members'].reverse()
            group['conflicts'] = dict(reversed(list(group['conflicts'].items())))
            if f's1:{uid(20)}' in group['members']:
                names = ['metadata.custom', 'metadata.parent_id', 'notebook', 'title']
                self.assertEqual(sorted((v['present'], v['value'])
                                        for v in group['conflicts']['metadata.custom']),
                                 [(False, None), (True, '')])
            elif f's1:{uid(22)}' in group['members']:
                names = []
                self.assertEqual(group['conflicts'], {})
            else:
                names = sorted(group['conflicts'])
            expected.append(names)
        self.assertEqual(len(expected), 3)
        self.assert_overview(report, [], expected)

    def test_exact_names_are_literal_and_adversarial_values_stay_in_evidence(self):
        names = ['_leading__', 'A0', 'a__b___c', '__', 'z' * 128]
        value = '````` [KEEPER_SELECTED](javascript:alert(1)) <img src=x onerror=alert(1)>'
        files = {uid(n) + '.md': record(uid(n), 1, 'Same', 'same body',
                                      **{name: value if n == 1 else '' for name in names})
                 for n in (1, 2)}
        report = analyze([load_export(write_jex(self.root / 'exact-names.jex', files))])
        expected = sorted('metadata.' + n for n in names)
        self.assertEqual(sorted(report['exact_duplicates'][0]['conflicts']), expected)
        self.assert_overview(report, [], [expected])
        markdown = render_markdown(report)
        overview = markdown.split('## Exact duplicates\n\n', 1)[1].split('\n\n## Near candidates', 1)[0]
        self.assertEqual(re.findall(r'`([^`]+)`', overview.split('manual review): ', 1)[1]), expected)
        self.assertNotIn('KEEPER_SELECTED', overview)
        self.assertNotIn('<img', overview)
        self.assertIn('``````json', markdown)

    def test_scoped_blocked_output_near_names_keep_review_order(self):
        report = self.output_review(scoped_files(), partial=True)
        self.assertEqual(report['summary']['blocked_notes'], 4)
        self.assertEqual(report['summary']['near_groups'], 2)
        fields = [['blocked_reasons', 'content_signature', 'normalized_body_sha256', 'note_links']] * 2
        # Sorting must not depend on dictionary insertion order, nor reorder groups/members/reasons.
        for group in report['near_candidates']:
            group['conflicts'] = dict(reversed(list(group['conflicts'].items())))
        self.assert_overview(report, fields)

    def test_state_only_output_near_names_include_context_and_missing_metadata(self):
        files = example_files()
        files[uid(20) + '.md'] = record(
            uid(20), 1, 'Task variant',
            f'Café observations\n\n![sample](:/{uid(301)})\n\ncode: this is body text',
            is_todo=1, parent_id=uid(101), custom='')
        report = self.output_review([files])
        group, = report['near_candidates']
        self.assertEqual(group['reasons'], ['todo_conflict_or_trash_state_differs'])
        self.assertEqual(report['summary']['blocked_notes'], 0)
        self.assertEqual(sorted((v['present'], v['value']) for v in group['conflicts']['metadata.custom']),
                         [(False, None), (True, '')])
        self.assert_overview(report, [[
            'metadata.created_time', 'metadata.custom', 'metadata.encryption_applied',
            'metadata.is_conflict', 'metadata.is_todo', 'metadata.markup_language',
            'metadata.parent_id', 'metadata.synthetic_extra', 'metadata.todo_completed',
            'metadata.todo_due', 'metadata.updated_time', 'notebook', 'state', 'tags', 'title']])

    def test_permitted_metadata_names_are_literal_and_values_stay_in_evidence(self):
        names = ['_leading__', 'A0', 'a__b___c', '__', 'z' * 128]
        value = '````` [KEEPER_SELECTED](javascript:alert(1)) <img src=x onerror=alert(1)>'
        files = {uid(n) + '.md': record(uid(n), 1, 'Same', 'same body', is_todo=n-1,
                                      **{name: value if n == 1 else '' for name in names})
                 for n in (1, 2)}
        report = analyze([load_export(write_jex(self.root / 'names.jex', files))])
        group, = report['near_candidates']
        expected = sorted(['metadata.' + n for n in names] + ['metadata.is_todo', 'state'])
        self.assertEqual(sorted(group['conflicts']), expected)
        overview = self.assert_overview(report, [expected])
        displayed = re.findall(r'`([^`]+)`', overview.split('manual comparison): ', 1)[1])
        self.assertEqual(displayed, expected)
        self.assertNotIn('KEEPER_SELECTED', overview)
        self.assertNotIn('<img', overview)
        self.assertIn('``````json', render_markdown(report))

    def test_empty_near_inventories_and_no_conflicting_fields(self):
        for name, files in [('clean', [linked_files(), linked_files(1000)]),
                            ('cycles-empty', [preservation_files()])]:
            with self.subTest(name=name):
                case = self.root / name
                case.mkdir()
                previous, self.root = self.root, case
                report = self.output_review(files)
                self.root = previous
                self.assertEqual(report['near_candidates'], [])
                self.assert_overview(report, [])
        # Equal empty notes are still blocked near candidates; an empty conflict
        # set does not establish duplicates or remove the manual-comparison label.
        files = {uid(n)+'.md': record(uid(n), 1, 'Empty') for n in (1, 2)}
        report = analyze([load_export(write_jex(self.root / 'empty.jex', files))])
        self.assertEqual(report['near_candidates'][0]['conflicts'], {})
        self.assert_overview(report, [[]])
