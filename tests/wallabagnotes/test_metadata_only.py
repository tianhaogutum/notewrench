"""Metadata-only managed inspection. SPDX-License-Identifier: AGPL-3.0-only."""
from __future__ import annotations

import base64
import contextlib
import io
import itertools
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from notewrench.wallabagnotes import cli, core, update
from .test_managed_conflicts import filesystem_state
from . import test_managed_only as managed
from . import test_proposed_updates as proposals


def metadata_projection(detailed):
    """Independent schema oracle: project managed-only output at known locations."""
    result = json.loads(core.json_encode(detailed))
    result['metadata_only'] = True
    for item in result['conflicts']:
        item.pop('unified_diff')
        for side in ('expected', 'current'):
            if item[side] is not None:
                item[side].pop('base64')
    if 'proposed_update' in result:
        proposed = result['proposed_update']
        for kind in ('added', 'modified', 'deleted', 'articles'):
            for item in proposed['changes'][kind]:
                item.pop('before', None)
                item.pop('after', None)
        for key, sides in [('region_changes', ('before', 'after')),
                           ('local_region_changes', ('local', 'proposed'))]:
            for item in proposed.get(key, []):
                item.pop('unified_diff')
                for side in sides:
                    item[side].pop('base64')
    return result


class MetadataOnlyTests(unittest.TestCase):
    setUp = proposals.ProposedUpdateTests.setUp
    run_cli = proposals.ProposedUpdateTests.run_cli

    def safe(self, *extra):
        return self.run_cli('--inspect-managed', str(self.out), '--metadata-only', *extra)

    def mixed(self):
        old = [dict(id=n, title='PRIVATE saved title', annotations=[dict(
            id=9, quote='PRIVATE saved quote', text='PRIVATE saved note', ranges=[{'secret': 'PRIVATE range'}],
            opaque={'secret': 'PRIVATE opaque'})]) for n in range(1, 5)]
        # A separate initialized bundle provides intact, damaged and unavailable paths together.
        self.out = self.work / 'mixed'
        cli.publish(self.out, update.plan_update(self.out, core.parse(json.dumps(old).encode()), 'all').files)
        new = json.loads(json.dumps(old))[:3]
        new[0]['title'] = 'PRIVATE proposed title'
        new[0]['opaque'] = None
        new[0]['annotations'][0].update(quote='PRIVATE proposed quote', opaque={'changed': 'PRIVATE proposed value'})
        new[1]['annotations'] = [dict(quote='PRIVATE anonymous', text='', ranges=[])]
        new.append(dict(id=5, title='PRIVATE new', annotations=[]))
        self.source.write_text(json.dumps(new))
        intact = self.out / 'markdown/article-1.md'
        intact.write_bytes(b'PERSONAL prefix\xff' + intact.read_bytes().replace(b'PRIVATE saved quote', b'PRIVATE local\xff') + b'PERSONAL suffix')
        damaged = self.out / 'logseq/article-1.md'
        damaged.write_bytes(damaged.read_bytes().replace(update.END, b'') + b'PERSONAL damaged\xff')
        (self.out / 'markdown/article-2.md').unlink()
        target = self.work / 'private-target'
        target.write_bytes(b'PRIVATE link target')
        (self.out / 'logseq/article-2.md').unlink()
        (self.out / 'logseq/article-2.md').symlink_to(target)
        (self.out / 'changes.json').unlink()
        (self.out / 'report.json').write_bytes(b'PRIVATE artifact')
        (self.out / 'markdown/article-5.md').symlink_to('missing')
        return damaged

    def test_prior_exposure_then_metadata_opt_in(self):
        damaged = self.mixed()
        args = ['--propose-update', str(self.source), '--proposed-paths', '--proposed-regions',
                '--region-relationships', '--local-proposed-regions']
        before = filesystem_state(self.work)
        detailed = self.run_cli('--inspect-managed', str(self.out), '--managed-only', *args)
        self.assertEqual((detailed.returncode, detailed.stderr), (2, ''))
        old = json.loads(detailed.stdout)
        edited = next(c for c in old['conflicts'] if c['code'] == 'managed_edit')
        self.assertIn(b'PRIVATE local', base64.b64decode(edited['current']['base64']))
        self.assertIn('PRIVATE saved', edited['unified_diff'])
        changes = old['proposed_update']['changes']
        self.assertEqual(changes['modified'][0]['after']['opaque']['changed'], 'PRIVATE proposed value')
        self.assertIn('PRIVATE proposed', changes['articles'][0]['after']['title'])
        print('Confirmed prior-runtime PRIVATE managed base64/diff and proposed raw values in mixed fixture; managed-only does not suppress these.')
        result = self.safe(*args)
        self.assertEqual((result.returncode, result.stderr), (2, ''))
        report = json.loads(result.stdout)
        self.assertEqual(report, metadata_projection(old))
        for text in ('PRIVATE', 'PERSONAL', 'base64', 'unified_diff', update.sha(damaged.read_bytes())):
            self.assertNotIn(text, result.stdout)
        self.assertEqual(len(report['conflicts']), 4)
        self.assertEqual(len(report['artifact_conflicts']), 2)
        self.assertEqual(len(report['proposed_update']['path_collisions']), 1)
        self.assertEqual(self.safe(*args).stdout, result.stdout)
        self.assertEqual(self.safe('--managed-only', *args).stdout, result.stdout)
        self.assertEqual(self.run_cli('--inspect-managed', str(self.out), '--managed-only', *args).stdout, detailed.stdout)
        self.assertEqual(filesystem_state(self.work), before)

    def test_all_flag_combinations_preserve_schema_order_and_fingerprints_without_payload_work(self):
        self.mixed()
        before = filesystem_state(self.work)
        options = [{}] + [dict(next_export=core.parse(self.source.read_bytes()), include_paths=paths,
                              include_regions=regions, include_relationships=bool(rel), include_local_regions=rel == 2)
                          for paths, regions, rel in itertools.product((False, True), (False, True), (0, 1, 2))]
        for opts in options:
            with self.subTest(opts=opts):
                detailed = update.inspect_managed(self.out, managed_only=True, **opts)
                with patch('notewrench.wallabagnotes.update.byte_description', side_effect=AssertionError('encoded bytes')), \
                     patch('notewrench.wallabagnotes.update.region_diff', side_effect=AssertionError('computed diff')):
                    report = update.inspect_managed(self.out, metadata_only=True, **opts)
                self.assertEqual(report, metadata_projection(detailed))
                self.assertEqual(update.inspect_managed(self.out, managed_only=True, **opts), detailed)
        self.assertEqual(filesystem_state(self.work), before)

    def test_formats_scopes_noop_raw_only_retirement_and_reappearance(self):
        for fmt, scoped in itertools.product(('all', 'markdown', 'logseq', 'csv'), (False, True)):
            with self.subTest(fmt=fmt, scoped=scoped):
                def parsed(data):
                    return core.parse_sources([('p', data), ('q', data), ('empty', b'[]'), ('p', data)]) if scoped else core.parse(data)
                old = parsed(proposals.BEFORE)
                raw_only = json.loads(proposals.BEFORE)
                raw_only[0]['annotations'][0]['unknown'] = {'PRIVATE': [None, '', 1]}
                raw_only = parsed(json.dumps(raw_only).encode())
                empty = parsed(b'[]')
                out = self.work / f'{fmt}-{scoped}'
                cli.publish(out, update.plan_update(out, old, fmt).files)
                for stage in ('active', 'retired'):
                    for label, proposal in [('noop-or-return', old), ('raw-only', raw_only), ('retirement', empty)]:
                        opts = dict(next_export=proposal, format_name=fmt, include_paths=True, include_regions=True,
                                    include_relationships=True, include_local_regions=True)
                        before = filesystem_state(self.work)
                        detailed = update.inspect_managed(out, managed_only=True, **opts)
                        report = update.inspect_managed(out, metadata_only=True, **opts)
                        self.assertEqual(report, metadata_projection(detailed))
                        self.assertEqual(report['status'], 'clean')
                        p = report['proposed_update']
                        if stage == 'active' and label == 'raw-only':
                            self.assertTrue(p['changes']['modified'])
                            self.assertEqual(p['region_changes'], [])
                            self.assertEqual(p['local_region_changes'], [])
                        if stage == 'retired' and label == 'noop-or-return' and fmt != 'csv':
                            self.assertEqual({c['change'] for c in p['region_changes']}, {'reappeared'})
                        self.assertEqual(filesystem_state(self.work), before)
                    if stage == 'active': update.publish_update(out, update.plan_update(out, empty, fmt))

    def test_no_snapshot_dependency_and_usage_dependencies_before_io(self):
        result = self.safe()
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        report = json.loads(result.stdout)
        self.assertEqual(report, metadata_projection(update.inspect_managed(self.out, managed_only=True)))
        self.assertNotIn('proposed_update', report)
        self.assertEqual(report['conflicts'], [])
        for args in [['--metadata-only'], [str(self.source), '-o', 'unused', '--metadata-only'],
                     [str(self.source), '-o', 'unused', '--metadata-only', '--update'],
                     [str(self.source), '-o', 'unused', '--metadata-only', '--dry-run'],
                     ['--source', 'p', str(self.source), '--inspect-conflicts', '--metadata-only'],
                     *[['--inspect-managed', 'missing', '--metadata-only', flag] for flag in
                       ('--local-proposed-regions', '--region-relationships', '--proposed-regions', '--proposed-paths',
                        '--update', '--dry-run', '--inspect-conflicts', '--conflict-values')]]:
            with self.subTest(args=args), patch('notewrench.wallabagnotes.update.path_mode', side_effect=AssertionError('bundle I/O')), \
                 patch.object(Path, 'read_bytes', side_effect=AssertionError('input I/O')), \
                 contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
                with self.assertRaises(SystemExit) as caught: cli.main(args)
                self.assertEqual(caught.exception.code, 2)
                self.assertEqual(stdout.getvalue(), '')
                self.assertIn('usage:', stderr.getvalue())
        with patch('notewrench.wallabagnotes.update.path_mode', side_effect=AssertionError('bundle I/O')):
            for opt in ('include_paths', 'include_regions', 'include_relationships', 'include_local_regions'):
                with self.assertRaises(core.BagnotesError) as caught:
                    update.inspect_managed(self.out, metadata_only=True, **{opt: True})
                self.assertEqual(caught.exception.diagnostic['code'], 'update_scope')

    # Reuse the same fatal/OS boundary scenarios with this class's metadata-only CLI.
    test_invalid_input_scope_and_verified_baseline_no_stdout = managed.ManagedOnlyTests.test_invalid_input_scope_and_verified_baseline_no_stdout
    test_actual_os_errors_exit_three_no_partial_stdout = managed.ManagedOnlyTests.test_actual_os_errors_exit_three_no_partial_stdout

    def test_unavailable_paths_never_follow_links_or_describe_whole_pages(self):
        page = self.out / 'markdown/article-1.md'
        parent = page.parent
        parent.rename(self.work / 'real-pages')
        parent.symlink_to(self.work / 'real-pages', target_is_directory=True)
        read, mode = Path.read_bytes, update.path_mode
        def guarded_read(path):
            if path.parent == parent: raise AssertionError('read through link')
            return read(path)
        def guarded_mode(path):
            if path.parent == parent: raise AssertionError('metadata through link')
            return mode(path)
        before = filesystem_state(self.work)
        with patch.object(Path, 'read_bytes', guarded_read), patch('notewrench.wallabagnotes.update.path_mode', side_effect=guarded_mode):
            report = update.inspect_managed(self.out, metadata_only=True)
        item = report['conflicts'][0]
        self.assertEqual(item['code'], 'unreadable_page')
        self.assertEqual(item['unavailable_reason'], dict(code=item['code'], message=item['message']))
        self.assertIsNone(item['current'])
        self.assertNotIn('unified_diff', item)
        self.assertEqual(filesystem_state(self.work), before)


if __name__ == '__main__':
    unittest.main()
