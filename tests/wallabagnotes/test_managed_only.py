"""Opt-in local diagnostics without whole-page fallback. SPDX-License-Identifier: AGPL-3.0-only."""
from __future__ import annotations

import base64
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import unittest
from unittest.mock import patch

from notewrench.wallabagnotes import cli, core, update
from notewrench.wallabagnotes.render import provenance
from .test_managed_conflicts import filesystem_state
from . import test_proposed_updates as proposals


class ManagedOnlyTests(unittest.TestCase):
    setUp = proposals.ProposedUpdateTests.setUp
    run_cli = proposals.ProposedUpdateTests.run_cli
    proposal = proposals.ProposedUpdateTests.proposal

    def safe(self, *extra):
        return self.run_cli('--inspect-managed', str(self.out), '--managed-only', *extra)

    def assert_mode(self, safe, default):
        """Assert the exact opt-in delta, including every unavailable reason."""
        self.assertTrue(safe['managed_only'])
        self.assertNotIn('managed_only', default)
        self.assertEqual(len(safe['conflicts']), len(default['conflicts']))
        for new, old in zip(safe['conflicts'], default['conflicts']):
            expected = dict(old)
            reason = None
            if old['code'] in ('invalid_markers', 'unreadable_page'):
                reason = dict(code=old['code'], message=old['message'])
                expected.update(comparison='unavailable', current=None, unified_diff=None)
            expected['unavailable_reason'] = reason
            self.assertEqual(new, expected)
        self.assertEqual({k: v for k, v in safe.items() if k not in ('managed_only', 'conflicts')},
                         {k: v for k, v in default.items() if k != 'conflicts'})

    def test_prior_exposure_and_intact_edits_then_safe_independent_reports(self):
        broken = self.out / 'markdown/article-1.md'
        intact = self.out / 'logseq/article-1.md'
        private = b'PRIVATE PERSONAL\xff\r\nno trailing newline'
        broken.write_bytes(broken.read_bytes().replace(update.END, b'') + private)
        intact.write_bytes(private + intact.read_bytes().replace(b'original', b'LOCAL\xff\r\n') + private)
        (self.out / 'report.json').write_bytes(b'PRIVATE artifact')
        (self.out / 'changes.json').unlink()
        # A proposed new-page collision stays independent of local diagnostics.
        data = json.loads(proposals.AFTER)
        data.append(dict(id=2, annotations=[]))
        self.source.write_text(json.dumps(data))
        (self.out / 'markdown/article-2.md').symlink_to('missing')
        args = ['--propose-update', str(self.source), '--proposed-paths', '--proposed-regions',
                '--region-relationships', '--local-proposed-regions']
        before = filesystem_state(self.work)
        default = self.run_cli('--inspect-managed', str(self.out), *args)
        self.assertEqual((default.returncode, default.stderr), (2, ''))
        old = json.loads(default.stdout)
        damaged = next(c for c in old['conflicts'] if c['code'] == 'invalid_markers')
        edited = next(c for c in old['conflicts'] if c['code'] == 'managed_edit')
        self.assertEqual(damaged['comparison'], 'whole-page')
        self.assertEqual(base64.b64decode(damaged['current']['base64']), broken.read_bytes())
        self.assertEqual(damaged['current']['sha256'], update.sha(broken.read_bytes()))
        self.assertIn('PRIVATE PERSONAL', damaged['unified_diff'])
        self.assertIn('LOCAL', edited['unified_diff'])
        print('Confirmed prior default whole-page private-byte/base64/hash/diff exposure AND intact local edit in same fixture.')
        result = self.safe(*args)
        self.assertEqual((result.returncode, result.stderr), (2, ''))
        report = json.loads(result.stdout)
        self.assert_mode(report, old)
        self.assertNotIn('PRIVATE', result.stdout)
        self.assertNotIn(update.sha(broken.read_bytes()), result.stdout)
        self.assertNotIn(base64.b64encode(broken.read_bytes()).decode(), result.stdout)
        self.assertEqual(len(report['conflicts']), 2)
        self.assertEqual(len(report['artifact_conflicts']), 2)
        self.assertEqual(len(report['proposed_update']['path_collisions']), 1)
        self.assertEqual([c['path'] for c in report['proposed_update']['local_region_changes']], ['logseq/article-1.md'])
        self.assertEqual(self.safe(*args).stdout, result.stdout)
        self.assertEqual(self.run_cli('--inspect-managed', str(self.out), *args).stdout, default.stdout)
        self.assertEqual(filesystem_state(self.work), before)

    def test_marker_boundaries_and_exact_available_bytes(self):
        page = self.out / 'markdown/article-1.md'
        original = page.read_bytes()
        saved = update.region(original, str(page))[1]
        private = b'PRIVATE\xff\r\n'
        cases = [b'', private, original.replace(update.BEGIN, b''), original.replace(update.END, b''),
                 original + update.BEGIN, original + update.END,
                 update.END + original.replace(update.END, b''),
                 original.replace(update.BEGIN, update.BEGIN.replace(b'\n', b'\r\n')),
                 original.replace(update.END, update.END.replace(b'\n', b'\r\n')),
                 saved[:-1], update.BEGIN + update.END,
                 private + saved.replace(b'original', b'local\xff\r\n') + private,
                 private + saved + private,
                 private + saved + update.BEGIN[:-1] + update.END[:-1]]
        for data in cases:
            with self.subTest(data=data):
                page.write_bytes(data)
                before = filesystem_state(self.work)
                default = update.inspect_managed(self.out)
                with patch('notewrench.wallabagnotes.update.byte_description', wraps=update.byte_description) as describe, \
                     patch('notewrench.wallabagnotes.update.region_diff', wraps=update.region_diff) as diff:
                    safe = update.inspect_managed(self.out, managed_only=True)
                self.assert_mode(safe, default)
                if default['conflicts']:
                    item = safe['conflicts'][0]
                    if item['code'] == 'invalid_markers':
                        self.assertIsNone(item['current'])
                        self.assertIsNone(item['unified_diff'])
                        self.assertTrue(all(c.args[0] != data for c in describe.call_args_list))
                        self.assertFalse(diff.called)
                    else:
                        self.assertEqual(base64.b64decode(item['current']['base64']), update.region(data, str(page))[1])
                        self.assertNotIn('PRIVATE', item['unified_diff'])
                else:
                    self.assertEqual(safe['status'], 'clean')
                self.assertEqual(filesystem_state(self.work), before)

    def test_unavailable_paths_and_linked_parent_not_followed(self):
        page = self.out / 'markdown/article-1.md'
        original = page.read_bytes()
        target = self.work / 'private-target'
        target.write_bytes(b'PRIVATE')
        for case in ['missing', 'directory', 'link', 'dangling', 'loop', 'fifo', 'parent-file']:
            if case == 'fifo' and not hasattr(os, 'mkfifo'):
                continue
            with self.subTest(case=case):
                page.unlink()
                if case == 'directory': page.mkdir()
                elif case in ('link', 'dangling', 'loop'):
                    page.symlink_to(target if case == 'link' else 'absent' if case == 'dangling' else page.name)
                elif case == 'fifo': os.mkfifo(page)
                elif case == 'parent-file':
                    page.parent.rmdir()
                    page.parent.write_bytes(b'PRIVATE parent')
                before = filesystem_state(self.work)
                default = update.inspect_managed(self.out)
                report = update.inspect_managed(self.out, managed_only=True)
                self.assert_mode(report, default)
                item = report['conflicts'][0]
                self.assertEqual(item['code'], 'unreadable_page')
                self.assertIsNone(item['current'])
                self.assertEqual(filesystem_state(self.work), before)
                if case == 'directory': page.rmdir()
                elif case == 'parent-file':
                    page.parent.unlink()
                    page.parent.mkdir()
                elif case != 'missing': page.unlink()
                page.write_bytes(original)
        parent = page.parent
        parent.rename(self.work / 'real-pages')
        parent.symlink_to(self.work / 'real-pages', target_is_directory=True)
        before = filesystem_state(self.work)
        mode, read = update.path_mode, Path.read_bytes
        def guarded_mode(path):
            if path.parent == parent: raise AssertionError('metadata through linked parent')
            return mode(path)
        def guarded_read(path):
            if path.parent == parent: raise AssertionError('read through linked parent')
            return read(path)
        with patch('notewrench.wallabagnotes.update.path_mode', side_effect=guarded_mode), patch.object(Path, 'read_bytes', guarded_read):
            report = update.inspect_managed(self.out, managed_only=True)
        self.assertEqual(report['conflicts'][0]['code'], 'unreadable_page')
        self.assertEqual(filesystem_state(self.work), before)

    def test_formats_scopes_retirement_reappearance_and_stable_identities(self):
        for fmt in ('all', 'markdown', 'logseq', 'csv'):
            for scoped in (False, True):
                with self.subTest(fmt=fmt, scoped=scoped):
                    out = self.work / f'{fmt}-{scoped}'
                    def parsed(data):
                        return core.parse_sources([('p', data), ('q', data), ('empty', b'[]'), ('p', data)]) if scoped else core.parse(data)
                    old, new, empty = map(parsed, (proposals.BEFORE, proposals.AFTER, b'[]'))
                    cli.publish(out, update.plan_update(out, old, fmt).files)
                    baseline = core.decode((out / 'provenance.json').read_bytes())
                    changes = update.changes_between(baseline, provenance(new))
                    for c in changes['modified']:
                        article = next(a for a in baseline['articles'] if a.get('namespace') == c['namespace'])
                        ann = next(a for a in article['annotations'] if a['identity'] == c['identity'])
                        for k in ('identity', 'anchor', 'block_id'): self.assertEqual(c[k], ann[k])
                    for stage in ('active', 'retired'):
                        for proposal in (new, empty, old):
                            pages = list(out.rglob('*.md'))
                            originals = {p: p.read_bytes() for p in pages}
                            for i, p in enumerate(pages):
                                data = originals[p]
                                p.write_bytes(b'PRIVATE\xff' + (data.replace(update.END, b'') if i % 2 == 0 else
                                              data.replace(update.END, b'local\xff\r\n' + update.END)) + b'PRIVATE')
                            before = filesystem_state(self.work)
                            opts = dict(next_export=proposal, format_name=fmt, include_paths=True, include_regions=True,
                                        include_relationships=True, include_local_regions=True)
                            default = update.inspect_managed(out, **opts)
                            safe = update.inspect_managed(out, managed_only=True, **opts)
                            self.assert_mode(safe, default)
                            self.assertNotIn('PRIVATE', core.json_encode(safe))
                            self.assertEqual(safe['checked_pages'], len(pages))
                            self.assertEqual(safe['status'], 'conflicts' if pages else 'clean')
                            self.assertEqual(filesystem_state(self.work), before)
                            for p, data in originals.items(): p.write_bytes(data)
                        if stage == 'active': update.publish_update(out, update.plan_update(out, empty, fmt))

    def test_no_snapshot_dependency_clean_and_existing_flag_dependencies_no_io(self):
        before = filesystem_state(self.work)
        default = self.run_cli('--inspect-managed', str(self.out))
        result = self.safe()
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        self.assert_mode(json.loads(result.stdout), json.loads(default.stdout))
        with patch('notewrench.wallabagnotes.cli.publish', side_effect=AssertionError('published')), \
             patch('notewrench.wallabagnotes.update.publish_update', side_effect=AssertionError('updated')), \
             patch('notewrench.wallabagnotes.update.plan_update', side_effect=AssertionError('planned')), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['--inspect-managed', str(self.out), '--managed-only']), 0)
        for args in [['--managed-only'], [str(self.source), '-o', 'unused', '--managed-only'],
                     [str(self.source), '-o', 'unused', '--managed-only', '--update'],
                     [str(self.source), '-o', 'unused', '--managed-only', '--dry-run'],
                     ['--source', 'p', str(self.source), '--inspect-conflicts', '--managed-only'],
                     *[['--inspect-managed', 'missing', '--managed-only', flag] for flag in
                       ('--local-proposed-regions', '--region-relationships', '--proposed-regions', '--proposed-paths',
                        '--update', '--dry-run', '--inspect-conflicts', '--conflict-values')]]:
            result = self.run_cli(*args)
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertIn('usage:', result.stderr)
        with patch('notewrench.wallabagnotes.update.path_mode', side_effect=AssertionError('bundle I/O')):
            for opt in ('include_paths', 'include_regions', 'include_relationships', 'include_local_regions'):
                with self.assertRaises(core.BagnotesError) as caught:
                    update.inspect_managed(self.out, managed_only=True, **{opt: True})
                self.assertEqual(caught.exception.diagnostic['code'], 'update_scope')
        self.assertEqual(filesystem_state(self.work), before)

    def test_invalid_input_scope_and_verified_baseline_no_stdout(self):
        (self.out / 'report.json').write_bytes(b'local conflict')
        for data, code in [(b'{', 'invalid_json'), (b'[{}]', 'invalid_id'), (b'[{"id":1}]', 'incomplete_snapshot')]:
            self.source.write_bytes(data)
            result = self.safe('--propose-update', str(self.source))
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertEqual(json.loads(result.stderr)['code'], code)
        self.source.write_bytes(proposals.AFTER)
        for extra in [('--format', 'csv'), ('--source', 'other', str(self.source))]:
            args = ['--propose-update', *extra] if extra[0] == '--source' else ['--propose-update', str(self.source), *extra]
            result = self.safe(*args)
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertEqual(json.loads(result.stderr)['code'], 'update_scope')
        saved_state = (self.out / update.STATE).read_bytes()
        bad_state = json.loads(saved_state)
        bad_state['pages']['markdown/article-1.md'] = '0' * 64
        for name, data in [(update.STATE, b'{'), (update.STATE, json.dumps(bad_state).encode()), ('provenance.json', b'{}')]:
            path = self.out / name
            original = path.read_bytes()
            path.write_bytes(data)
            before = filesystem_state(self.work)
            for args in [(), ('--propose-update', str(self.source), '--region-relationships', '--local-proposed-regions')]:
                result = self.safe(*args)
                self.assertEqual((result.returncode, result.stdout), (2, ''))
                self.assertIn(json.loads(result.stderr)['code'], ('update_state', 'update_conflict'))
            self.assertEqual(filesystem_state(self.work), before)
            path.write_bytes(original)
        link = self.work / 'bundle-link'
        link.symlink_to(self.out, target_is_directory=True)
        result = self.run_cli('--inspect-managed', str(link), '--managed-only')
        self.assertEqual((result.returncode, result.stdout), (2, ''))
        self.assertEqual(json.loads(result.stderr)['code'], 'update_state')

    @unittest.skipIf(os.name != 'posix' or os.geteuid() == 0, 'requires unprivileged POSIX permissions')
    def test_actual_os_errors_exit_three_no_partial_stdout(self):
        (self.out / 'changes.json').unlink()
        (self.out / 'logseq/article-1.md').write_bytes(b'PRIVATE damaged markers')
        for path in (self.source, self.out / update.STATE, self.out / 'provenance.json', self.out / 'report.json',
                     self.out / 'markdown/article-1.md', self.out / 'markdown', self.out):
            with self.subTest(path=path):
                permissions = stat.S_IMODE(path.stat().st_mode)
                before = filesystem_state(self.work)
                path.chmod(0)
                try:
                    result = self.safe('--propose-update', str(self.source), '--proposed-regions',
                                       '--proposed-paths', '--region-relationships', '--local-proposed-regions')
                    self.assertEqual((result.returncode, result.stdout), (3, ''))
                    self.assertEqual(json.loads(result.stderr)['code'], 'io_error')
                finally: path.chmod(permissions)
                self.assertEqual(filesystem_state(self.work), before)


if __name__ == '__main__':
    unittest.main()
