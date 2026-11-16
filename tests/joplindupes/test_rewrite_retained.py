# SPDX-License-Identifier: AGPL-3.0-only
"""Preservation is permitted for reachable notes without exact-match evidence."""
import copy
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest

from notewrench.joplindupes import ExportError, Limits, analyze, load_export
from notewrench.joplindupes.rewrite import decision_template, rewrite_archive
from .synthetic import example_files, linked_files, record, uid, write_jex, write_raw


def preservation_files():
    files = example_files()
    for number, title, body in (
        (10, 'Cycle A', f'[B](:/{uid(11)})'),
        (11, 'Cycle B', f'[A](:/{uid(10)})'),
        (12, 'Self', f'[self](:/{uid(12).upper()}#section)'),
        (13, 'Empty', ''),
        (14, 'Whitespace', ' \t '),
        (15, 'Incoming', f'[cycle](:/{uid(10)}) [empty](:/{uid(13)})'),
    ):
        files[uid(number) + '.md'] = record(uid(number), 1, title, body,
                                           custom='Café e\u0301 retained', is_todo=0)
    return files


def scoped_files():
    files = [linked_files(n) for n in (0, 1000, 2000)]
    files[2][uid(2004) + '.md'] = files[2][uid(2003) + '.md'].replace(
        uid(2003).encode(), uid(2004).encode())
    return files


class RetainedNoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def approved(self, exports, pair=None):
        assessment = analyze(exports, target_pair=pair)
        decisions = decision_template(exports, assessment)
        for group in decisions['groups']:
            group['approved'], group['keeper'] = True, group['members'][0]
        return assessment, decisions

    def assert_retained_records(self, exports, result, report, assessment):
        mapping = {m['old_key']: m for m in report['id_map']}
        token = re.compile(r':/([a-fA-F0-9]{32})')
        structural = {'parent_id', 'conflict_original_id', 'note_id', 'tag_id'}
        for n, export in enumerate(exports, 1):
            for old_id, item in export.items.items():
                entry = mapping[f's{n}:{old_id}']
                if entry['action'] != 'retained':
                    continue
                def remap(value):
                    return token.sub(lambda m: ':/' + mapping[f's{n}:{m[1].lower()}']['new_id'], value)
                actual = result.items[entry['new_id']]
                self.assertEqual(actual.title, remap(item.title))
                self.assertEqual(actual.body, remap(item.body))
                expected_meta = {k: entry['new_id'] if k == 'id' else
                                 mapping[f's{n}:{v}']['new_id'] if k in structural and v else remap(v)
                                 for k, v in item.metadata.items()}
                self.assertEqual(actual.metadata, expected_meta)
            for old_id, blob in export.blobs.items():
                actual = result.blobs[mapping[f's{n}:{old_id}']['new_id']]
                self.assertEqual((actual.sha256, actual.size), (blob.sha256, blob.size))
        blocked = [n for n in assessment['notes'] if n['blocked_reasons']]
        self.assertEqual(report['retained_blocked_notes'], [
            {'old_key': n['key'], 'new_id': mapping[n['key']]['new_id'],
             'blocked_reasons': n['blocked_reasons']} for n in blocked])
        self.assertTrue(all(mapping[n['key']]['action'] == 'retained' for n in blocked))
        self.assertTrue(report['validation']['all_internal_references_reachable'])

    def assert_output_assessment(self, output, report, assessment, expected_blocks):
        archive = output / 'deduplicated.jex'
        review = analyze([load_export(archive)])
        post = report['post_rewrite_assessment']
        source_reasons = {n['key']: n['blocked_reasons'] for n in assessment['notes']}
        self.assertEqual(post, {
            'archive': 'deduplicated.jex',
            'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
            'target_pair': None,
            'summary': review['summary'],
            'blocked_notes': [{'id': n['id'], 'blocked_reasons': n['blocked_reasons'],
                              'origins': [{'old_key': m['old_key'],
                                           'blocked_reasons': source_reasons[m['old_key']]}
                                          for m in report['id_map'] if m['new_id'] == n['id']],
                              'note_links': n['note_links']}
                              for n in review['notes'] if n['blocked_reasons']],
            'remaining_exact_groups': [
                {'signature': group['signature'], 'action': 'review_only',
                 'conflict_fields': sorted(group['conflicts']),
                 'members': [{'id': key.split(':', 1)[1],
                              'origins': [{'old_key': m['old_key'],
                                           'blocked_reasons': source_reasons[m['old_key']]}
                                          for m in report['id_map']
                                          if m['new_id'] == key.split(':', 1)[1]]}
                             for key in group['members']]}
                for group in review['exact_duplicates']],
            'remaining_near_candidates': [
                {'signature': group['signature'], 'reasons': group['reasons'],
                 'action': 'manual_comparison_required',
                 'conflict_fields': sorted(group['conflicts']),
                 'members': [{'id': key.split(':', 1)[1],
                              'origins': [{'old_key': m['old_key'],
                                           'blocked_reasons': source_reasons[m['old_key']]}
                                          for m in report['id_map']
                                          if m['type'] == 1 and m['new_id'] == key.split(':', 1)[1]]}
                             for key in group['members']]}
                for group in review['near_candidates']],
            'warnings': review['warnings'],
            'review_exit_code': 3 if expected_blocks else 0,
        })
        self.assertEqual(len(post['remaining_near_candidates']), post['summary']['near_groups'])
        for group in post['remaining_near_candidates']:
            self.assertEqual(set(group), {'signature', 'reasons', 'action', 'conflict_fields', 'members'})
            self.assertEqual(group['action'], 'manual_comparison_required')
            self.assertTrue(all(isinstance(field, str) for field in group['conflict_fields']))
            for member in group['members']:
                self.assertEqual(set(member), {'id', 'origins'})
                for origin in member['origins']:
                    self.assertEqual(set(origin), {'old_key', 'blocked_reasons'})
        self.assertEqual(len(post['remaining_exact_groups']), post['summary']['exact_groups'])
        self.assertFalse({n['id'] for n in post['blocked_notes']} &
                         {m['id'] for g in post['remaining_exact_groups'] for m in g['members']})
        self.assertEqual(post['summary']['blocked_notes'], expected_blocks)
        self.assertEqual(len(post['blocked_notes']), expected_blocks)
        self.assertEqual(post['archive_sha256'], report['validation']['archive_sha256'])
        self.assertEqual(json.loads((output / 'rewrite-report.json').read_text()), report)
        self.assertNotIn(str(output.parent), json.dumps(post))

    def test_cycles_self_links_empty_and_incoming_notes_survive_without_merging(self):
        files = preservation_files()
        path = write_raw(self.root / 'source', files)
        exports = [load_export(path)]
        assessment, decisions = self.approved(exports)
        self.assertEqual(assessment['summary']['blocked_notes'], 6)
        self.assertEqual(len(decisions['groups']), 1)
        self.assertTrue(all(n['exact_signature'] is None for n in assessment['notes'] if n['blocked_reasons']))
        output = self.root / 'result'
        report = rewrite_archive(exports, assessment, decisions, output, Limits())
        result = load_export(output / 'deduplicated.jex')
        self.assertEqual(report['validation']['notes'], 8)  # Nine originals minus one approved duplicate.
        self.assertEqual(len(result.blobs), 2)
        self.assert_retained_records(exports, result, report, assessment)
        self.assertEqual(analyze([result])['summary']['blocked_notes'], 6)
        self.assert_output_assessment(output, report, assessment, 6)
        self.assertEqual(report['post_rewrite_assessment']['remaining_exact_groups'], [])
        self.assertEqual(report['post_rewrite_assessment']['remaining_near_candidates'], [])
        mapping = {m['old_key']: m['new_id'] for m in report['id_map']}
        blocks = {n['id']: n for n in report['post_rewrite_assessment']['blocked_notes']}
        self.assertEqual(blocks[mapping[f's1:{uid(13)}']]['note_links'], [])
        self.assertEqual(blocks[mapping[f's1:{uid(12)}']]['note_links'][0]['status'], 'cyclic_dependency')
        self.assertEqual(len(blocks[mapping[f's1:{uid(15)}']]['note_links']), 2)
        self.assertTrue(all((path / name).read_bytes() == data for name, data in files.items()))

    def test_excluded_ambiguous_chain_keeps_original_targets_and_unapproved_leaves(self):
        files = scoped_files()
        paths = [write_raw(self.root / 'left', files[0]),
                 write_jex(self.root / 'right.jex', files[1]),
                 write_jex(self.root / 'third.jex', files[2])]
        before = [p.read_bytes() for p in paths[1:]]
        exports = [load_export(p) for p in paths]
        assessment, decisions = self.approved(exports, ['s1', 's2'])
        self.assertEqual(assessment['summary']['blocked_notes'], 2)
        decisions['groups'] = [g for g in decisions['groups'] if f's1:{uid(3)}' not in g['members']]
        self.assertEqual(len(decisions['groups']), 2)
        for group in decisions['groups']:
            if f's1:{uid(2)}' in group['members']:
                group['keeper'] = f's2:{uid(1002)}'
        for number in (2001, 2002):
            forged = copy.deepcopy(decisions)
            forged['groups'][0]['members'].append(f's3:{uid(number)}')
            refused = self.root / f'forged-{number}'
            with self.assertRaisesRegex(ExportError, 'complete current exact group'):
                rewrite_archive(exports, assessment, forged, refused, Limits())
            self.assertFalse(refused.exists())
        output = self.root / 'result'
        report = rewrite_archive(exports, assessment, decisions, output, Limits())
        self.assertEqual(report['validation']['notes'], 8)
        self.assertEqual(report['validation']['resources'], 3)
        result = load_export(output / 'deduplicated.jex')
        self.assert_retained_records(exports, result, report, assessment)
        # Combining exports changes target uniqueness: the two source blocks
        # become four output blocks. The original pair is not an output scope.
        self.assertEqual(report['target_pair'], ['s1', 's2'])
        self.assertEqual(len(report['retained_blocked_notes']), 2)
        self.assert_output_assessment(output, report, assessment, 4)
        mapping = {m['old_key']: m['new_id'] for m in report['id_map']}
        blocks = {n['id']: n for n in report['post_rewrite_assessment']['blocked_notes']}
        for left, right in ((1, 1001), (2, 1002)):
            self.assertEqual(blocks[mapping[f's1:{uid(left)}']]['origins'], [
                {'old_key': f's1:{uid(left)}', 'blocked_reasons': []},
                {'old_key': f's2:{uid(right)}', 'blocked_reasons': []}])
        for number in (2001, 2002):
            origin, = blocks[mapping[f's3:{uid(number)}']]['origins']
            self.assertEqual(origin['old_key'], f's3:{uid(number)}')
            self.assertTrue(origin['blocked_reasons'])
        near = report['post_rewrite_assessment']['remaining_near_candidates']
        self.assertEqual(len(near), 2)
        self.assertEqual({frozenset(o['old_key'] for m in g['members'] for o in m['origins'])
                          for g in near},
                         {frozenset(f's{s}:{uid(n)}' for s, n in family)
                          for family in (((1, 1), (2, 1001), (3, 2001)),
                                         ((1, 2), (2, 1002), (3, 2002)))})
        for group in near:
            self.assertEqual(group['conflict_fields'],
                             ['blocked_reasons', 'content_signature',
                              'normalized_body_sha256', 'note_links'])
            self.assertEqual(group['reasons'], ['note_link_target_differs_or_unproven',
                                               'incomplete_or_unsupported_evidence'])
            self.assertEqual(len(group['members']), 2)
            self.assertTrue(all(m['id'] in blocks for m in group['members']))
            for member in group['members']:
                self.assertEqual(member['origins'], blocks[member['id']]['origins'])
        detail_keys = sorted('s1:' + mapping[f's{n}:{uid(number)}']
                             for n, number in ((1, 3), (2, 1003), (3, 2003), (3, 2004)))
        remaining, = report['post_rewrite_assessment']['remaining_exact_groups']
        self.assertEqual(remaining['action'], 'review_only')
        self.assertEqual(remaining['conflict_fields'], ['normalized_body_sha256', 'resources'])
        self.assertEqual(['s1:' + m['id'] for m in remaining['members']], detail_keys)
        self.assertEqual(sorted(o['old_key'] for m in remaining['members'] for o in m['origins']),
                         [f's{n}:{uid(number)}' for n, number in
                          ((1, 3), (2, 1003), (3, 2003), (3, 2004))])
        self.assertTrue(all(not o['blocked_reasons']
                            for m in remaining['members'] for o in m['origins']))
        self.assertEqual(report['approved_groups'], decisions['groups'])
        for source, middle, detail in ((2, 1002, 1003), (3, 2002, 2003)):
            link, = blocks[mapping[f's{source}:{uid(middle)}']]['note_links']
            self.assertEqual(link['status'], 'ambiguous_target')
            self.assertEqual(link['target_key'], 's1:' + mapping[f's{source}:{uid(detail)}'])
            self.assertEqual(sorted(link['candidate_targets']), detail_keys)
            self.assertIsNone(link['target_exact_signature'])
            self.assertEqual(link['correspondence_sources'], ['s1'])
            self.assertEqual(link['candidate_counts_by_source'], {'s1': 4})
        for source, index in ((1, 1), (3, 2001)):
            links = blocks[mapping[f's{source}:{uid(index)}']]['note_links']
            self.assertEqual(len(links), 2)  # Repeated literal links remain ordered occurrences.
            for link in links:
                self.assertEqual(link['status'], 'target_blocked')
                self.assertEqual(link['candidate_targets'], [])
        # Report evidence, including blocked inventories, is destination independent.
        other = self.root / 'another-destination'
        self.assertEqual(rewrite_archive(exports, assessment, decisions, other, Limits()), report)
        self.assertEqual((other / 'deduplicated.jex').read_bytes(), (output / 'deduplicated.jex').read_bytes())
        self.assertTrue(all((paths[0] / name).read_bytes() == data for name, data in files[0].items()))
        self.assertEqual([p.read_bytes() for p in paths[1:]], before)

    def test_fully_approved_mixed_source_keepers_have_clean_output_assessment(self):
        exports = [load_export(write_raw(self.root / 'left', linked_files())),
                   load_export(write_jex(self.root / 'right.jex', linked_files(1000)))]
        assessment, decisions = self.approved(exports, ['s1', 's2'])
        for group in decisions['groups']:
            if f's1:{uid(2)}' in group['members']:
                group['keeper'] = f's2:{uid(1002)}'
        reports = []
        for name in ('result', 'another-destination'):
            output = self.root / name
            report = rewrite_archive(exports, assessment, decisions, output, Limits())
            self.assertEqual(report['validation']['notes'], 3)
            self.assertEqual(report['validation']['resources'], 2)
            self.assertEqual(report['retained_blocked_notes'], [])
            self.assert_output_assessment(output, report, assessment, 0)
            self.assertEqual(report['post_rewrite_assessment']['remaining_exact_groups'], [])
            self.assertEqual(report['post_rewrite_assessment']['remaining_near_candidates'], [])
            reports.append(report)
        self.assertEqual(reports[0], reports[1])

    def test_remaining_conflict_names_cover_context_and_absence_without_values(self):
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
        exports = [load_export(write_jex(self.root / 'source.jex', files))]
        assessment, decisions = self.approved(exports)
        decisions['groups'] = [g for g in decisions['groups'] if f's1:{uid(1)}' in g['members']]
        output = self.root / 'result'
        report = rewrite_archive(exports, assessment, decisions, output, Limits())
        self.assert_output_assessment(output, report, assessment, 0)
        groups = report['post_rewrite_assessment']['remaining_exact_groups']
        self.assertEqual(len(groups), 2)
        by_origin = {o['old_key']: g for g in groups for m in g['members'] for o in m['origins']}
        self.assertEqual(by_origin[f's1:{uid(20)}']['conflict_fields'],
                         ['metadata.custom', 'metadata.parent_id', 'notebook', 'title'])
        self.assertEqual(by_origin[f's1:{uid(22)}']['conflict_fields'], [])
        for group in groups:
            self.assertEqual(set(group), {'signature', 'action', 'conflict_fields', 'members'})
            self.assertEqual(group['action'], 'review_only')
        self.assertEqual(report['approved_groups'], decisions['groups'])

    def test_remaining_state_near_family_keeps_merged_origins_without_new_approval(self):
        files = example_files()
        files[uid(20) + '.md'] = record(
            uid(20), 1, 'Task variant',
            f'Café observations\n\n![sample](:/{uid(301)})\n\ncode: this is body text',
            is_todo=1, parent_id=uid(101), custom='')
        path = write_jex(self.root / 'source.jex', files)
        before = path.read_bytes()
        exports = [load_export(path)]
        assessment, decisions = self.approved(exports)
        near, = assessment['near_candidates']
        forged = copy.deepcopy(decisions)
        forged['groups'] = [{'signature': near['signature'], 'members': near['members'],
                             'approved': True, 'keeper': near['members'][0]}]
        refused = self.root / 'near-refused'
        with self.assertRaisesRegex(ExportError, 'complete current exact group'):
            rewrite_archive(exports, assessment, forged, refused, Limits())
        self.assertFalse(refused.exists())
        output = self.root / 'result'
        report = rewrite_archive(exports, assessment, decisions, output, Limits())
        self.assert_output_assessment(output, report, assessment, 0)
        self.assertEqual(report['post_rewrite_assessment']['remaining_exact_groups'], [])
        group, = report['post_rewrite_assessment']['remaining_near_candidates']
        # The reason describes state; conflict names also cover context and
        # missing versus explicitly empty metadata, without copying values.
        self.assertEqual(group['conflict_fields'], [
            'metadata.created_time', 'metadata.custom', 'metadata.encryption_applied',
            'metadata.is_conflict', 'metadata.is_todo', 'metadata.markup_language',
            'metadata.parent_id', 'metadata.synthetic_extra', 'metadata.todo_completed',
            'metadata.todo_due', 'metadata.updated_time', 'notebook', 'state', 'tags', 'title'])
        self.assertEqual(group['reasons'], ['todo_conflict_or_trash_state_differs'])
        self.assertEqual(len(group['members']), 2)
        self.assertEqual(sorted(sorted(o['old_key'] for o in m['origins']) for m in group['members']),
                         [[f's1:{uid(1)}', f's1:{uid(2)}'], [f's1:{uid(20)}']])
        self.assertTrue(all(not o['blocked_reasons'] for m in group['members'] for o in m['origins']))
        self.assertEqual(report['approved_groups'], decisions['groups'])
        self.assertEqual(path.read_bytes(), before)

    def test_preservation_does_not_allow_blocked_members_in_approved_groups(self):
        exports = [load_export(write_jex(self.root / 'source.jex', preservation_files()))]
        assessment, base = self.approved(exports)
        for number in (10, 12, 13, 14, 15):
            for append in (False, True):
                with self.subTest(number=number, append=append):
                    decisions = copy.deepcopy(base)
                    if append:
                        decisions['groups'].append({'signature': '0' * 64, 'approved': True,
                            'members': [f's1:{uid(number)}', f's1:{uid(3)}'], 'keeper': f's1:{uid(number)}'})
                    else:
                        decisions['groups'][0]['members'].append(f's1:{uid(number)}')
                    output = self.root / f'rejected-{number}-{append}'
                    with self.assertRaisesRegex(ExportError, 'complete current exact group'):
                        rewrite_archive(exports, assessment, decisions, output, Limits())
                    self.assertFalse(output.exists())

    def test_unrelated_unsupported_or_unreachable_evidence_still_fails(self):
        for scenario in ('html', 'state', 'encrypted', 'locked', 'missing_url', 'title_url',
                         'metadata_url', 'missing_blob', 'encrypted_blob', 'size', 'extension',
                         'unknown', 'wrong_type', 'folder_cycle', 'orphan_blob'):
            with self.subTest(scenario=scenario):
                files = preservation_files()
                fields = {'markup_language': 2} if scenario == 'html' else (
                    {'is_todo': 'invalid'} if scenario == 'state' else (
                    {'encryption_applied': 1} if scenario == 'encrypted' else (
                    {'is_locked': 1} if scenario == 'locked' else {})))
                title, body = 'Terminal', 'Unrelated terminal'
                if scenario == 'missing_url':
                    body += f' :/{uid(999)}'
                elif scenario == 'title_url':
                    title += f' :/{uid(999)}'
                elif scenario == 'metadata_url':
                    fields['custom'] = f':/{uid(999)}'
                elif scenario == 'wrong_type':
                    body += f' :/{uid(100)}'  # A folder cannot be a note/resource URL.
                elif scenario == 'folder_cycle':
                    files[uid(100) + '.md'] = record(uid(100), 2, 'Folder', parent_id=uid(100))
                elif scenario == 'unknown':
                    files[uid(999) + '.md'] = record(uid(999), 99, 'Unsupported')
                elif scenario == 'orphan_blob':
                    files[f'resources/{uid(999)}.txt'] = b'orphan'
                if scenario in ('missing_blob', 'encrypted_blob', 'size', 'extension'):
                    resource = uid(950)
                    body += f' :/{resource}'
                    props = {'file_extension': 'txt', 'size': 4}
                    if scenario == 'encrypted_blob':
                        props['encryption_blob_encrypted'] = 1
                    elif scenario == 'size':
                        props['size'] = 5
                    elif scenario == 'extension':
                        props['file_extension'] = 'pdf'
                    files[resource + '.md'] = record(resource, 4, 'Terminal resource', **props)
                    if scenario != 'missing_blob':
                        files[f'resources/{resource}.txt'] = b'data'
                files[uid(16) + '.md'] = record(uid(16), 1, title, body, **fields)
                # Incoming target_blocked is not permission to preserve an unsupported terminal.
                files[uid(17) + '.md'] = record(uid(17), 1, 'Incoming terminal', f':/{uid(16)}')
                exports = [load_export(write_jex(self.root / (scenario + '.jex'), files))]
                assessment, decisions = self.approved(exports)
                self.assertEqual(len(decisions['groups']), 1)
                output = self.root / scenario
                with self.assertRaises(ExportError):
                    rewrite_archive(exports, assessment, decisions, output, Limits())
                self.assertFalse(output.exists())
