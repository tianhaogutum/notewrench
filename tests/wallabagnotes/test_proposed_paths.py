"""Opt-in proposed destination checks. SPDX-License-Identifier: AGPL-3.0-only."""
from __future__ import annotations

import json
import os
import stat
from unittest.mock import patch
from pathlib import Path
import unittest

from notewrench.wallabagnotes import cli, core, update
from .test_managed_conflicts import filesystem_state
from . import test_proposed_updates as proposals

AFTER, BEFORE = proposals.AFTER, proposals.BEFORE


class ProposedPathTests(unittest.TestCase):
    setUp = proposals.ProposedUpdateTests.setUp
    run_cli = proposals.ProposedUpdateTests.run_cli
    proposal = proposals.ProposedUpdateTests.proposal

    def test_multiple_collisions_changes_and_local_diagnostics_are_separate_read_only(self):
        data = json.loads(AFTER) + [dict(id=n, title=f'New {n}', annotations=[])
                                    for n in (4, 2, 3)]
        self.source.write_text(json.dumps(data))
        (self.out / 'markdown/article-2.md').write_bytes(b'PRIVATE untracked\xff')
        (self.out / 'logseq/article-3.md').mkdir()
        (self.out / 'logseq/article-3.md/handwritten').write_bytes(b'PRIVATE directory')
        target = self.work / 'private'
        target.write_bytes(b'PRIVATE linked content')
        (self.out / 'markdown/article-4.md').symlink_to(target)
        (self.out / 'logseq/article-2.md').symlink_to('missing')
        (self.out / 'markdown/unrelated.md').write_bytes(b'untouched')
        (self.out / 'report.json').write_bytes(b'edited report')
        for kind in ('markdown', 'logseq'):
            path = self.out / f'{kind}/article-1.md'
            path.write_bytes(path.read_bytes().replace(b'original', b'local edit'))
        before = filesystem_state(self.work)
        default = self.proposal()
        self.assertEqual((default.returncode, default.stderr), (2, ''))
        result = self.proposal('--proposed-paths')
        self.assertEqual((result.returncode, result.stderr), (2, ''))
        report = json.loads(result.stdout)
        proposed = report['proposed_update']
        self.assertEqual(proposed['checked_new_pages'], 6)
        self.assertEqual(proposed['path_collisions'], [
            dict(path=path, code='new_page_collision', namespace=None, source_id=identity,
                 blocking_path=path, kind=kind)
            for path, identity, kind in [('logseq/article-2.md', '2', 'symlink'),
                                         ('logseq/article-3.md', '3', 'directory'),
                                         ('markdown/article-2.md', '2', 'file'),
                                         ('markdown/article-4.md', '4', 'symlink')]])
        self.assertEqual(proposed['changes']['counts'], dict(added=1, modified=1, deleted=1))
        self.assertEqual([c['source_id'] for c in proposed['changes']['articles']], ['4', '2', '3'])
        self.assertEqual([c['path'] for c in report['conflicts']], ['logseq/article-1.md', 'markdown/article-1.md'])
        self.assertEqual([c['path'] for c in report['artifact_conflicts']], ['report.json'])
        stripped = json.loads(result.stdout)
        del stripped['proposed_update']['checked_new_pages'], stripped['proposed_update']['path_collisions']
        self.assertEqual(stripped, json.loads(default.stdout))
        self.assertNotIn('PRIVATE', result.stdout)
        repeat = self.proposal('--proposed-paths')
        self.assertEqual((repeat.returncode, repeat.stdout, repeat.stderr), (2, result.stdout, ''))
        self.assertEqual(filesystem_state(self.work), before)

    def test_collision_only_sets_status_and_default_remains_unchanged(self):
        self.source.write_text(json.dumps(json.loads(AFTER) + [dict(id=2, annotations=[])]))
        collision = self.out / 'markdown/article-2.md'
        collision.write_bytes(b'unreadable content need not be read')
        unrelated = self.out / 'logseq/article-99.md'
        unrelated.symlink_to('missing')
        before = filesystem_state(self.work)
        default = self.proposal()
        self.assertEqual((default.returncode, default.stderr), (0, ''))
        result = self.proposal('--proposed-paths')
        self.assertEqual((result.returncode, result.stderr), (2, ''))
        report = json.loads(result.stdout)
        self.assertEqual((report['status'], report['conflicts'], report['artifact_conflicts']), ('conflicts', [], []))
        self.assertEqual(report['proposed_update']['checked_new_pages'], 2)
        self.assertEqual([c['path'] for c in report['proposed_update']['path_collisions']], ['markdown/article-2.md'])
        # Collision classification uses metadata only, even for unreadable regular files.
        read_bytes = Path.read_bytes
        def guarded_read(path):
            if path in (collision, unrelated):
                raise AssertionError('read an untracked destination')
            return read_bytes(path)
        with patch.object(Path, 'read_bytes', guarded_read), \
             patch('notewrench.wallabagnotes.update.plan_update', side_effect=AssertionError('planned')), \
             patch('notewrench.wallabagnotes.cli.publish', side_effect=AssertionError('published')), \
             patch('notewrench.wallabagnotes.update.publish_update', side_effect=AssertionError('updated')):
            self.assertEqual(update.inspect_managed(self.out, next_export=core.parse(self.source.read_bytes()),
                                                   include_paths=True), report)
        self.assertEqual(filesystem_state(self.work), before)

    def test_unsafe_format_parents_are_reported_without_looking_through_links(self):
        next_export = core.parse(b'[{"id":3,"annotations":[]},{"id":2,"annotations":[]}]')
        for kind in ('file', 'symlink', 'dangling', 'loop', 'other'):
            with self.subTest(kind=kind):
                if kind == 'other' and not hasattr(os, 'mkfifo'):
                    continue
                out = self.work / kind
                cli.publish(out, update.plan_update(out, core.parse(b'[]'), 'all').files)
                parent = out / 'markdown'
                if kind == 'file':
                    parent.write_bytes(b'PRIVATE parent file')
                elif kind == 'other':
                    os.mkfifo(parent)
                else:
                    target = self.work / ('directory-target' if kind == 'symlink' else 'missing')
                    if kind == 'symlink':
                        target.mkdir()
                        (target / 'article-2.md').write_bytes(b'PRIVATE linked destination')
                    parent.symlink_to(parent if kind == 'loop' else target, target_is_directory=True)
                before = filesystem_state(self.work)
                original_lstat, original_read = Path.lstat, Path.read_bytes
                def guarded_lstat(path, *args, **kwargs):
                    if parent in path.parents:
                        raise AssertionError('looked through unsafe parent')
                    return original_lstat(path, *args, **kwargs)
                def guarded_read(path):
                    if path == parent or parent in path.parents:
                        raise AssertionError('read unsafe parent or child')
                    return original_read(path)
                with patch.object(Path, 'lstat', guarded_lstat), patch.object(Path, 'read_bytes', guarded_read):
                    report = update.inspect_managed(out, next_export=next_export, include_paths=True)
                proposed = report['proposed_update']
                self.assertEqual((report['status'], proposed['checked_new_pages']), ('conflicts', 4))
                self.assertEqual(proposed['path_collisions'], [
                    dict(path=f'markdown/article-{identity}.md', code='unsafe_page_parent', namespace=None,
                         source_id=identity, blocking_path='markdown',
                         kind=kind if kind in ('file', 'other') else 'symlink') for identity in ('2', '3')])
                self.assertEqual(report['conflicts'], [])
                self.assertEqual(filesystem_state(self.work), before)

    def test_all_formats_namespaces_empty_scope_and_retired_paths(self):
        empty = self.work / 'empty.json'
        empty.write_bytes(b'[]')
        self.source.write_text(json.dumps(json.loads(BEFORE) + [dict(id='002', annotations=[])]))
        for fmt in ('all', 'markdown', 'logseq', 'csv'):
            with self.subTest(fmt=fmt):
                out = self.work / fmt
                old = core.parse_sources([('p', BEFORE), ('empty', b'[]')])
                cli.publish(out, update.plan_update(out, old, fmt).files)
                retired = core.parse_sources([('p', b'[]'), ('empty', b'[]')])
                update.publish_update(out, update.plan_update(out, retired, fmt))
                new = core.parse_sources([('empty', b'[]'), ('p', self.source.read_bytes())])
                kinds = ('markdown', 'logseq') if fmt == 'all' else (fmt,) if fmt != 'csv' else ()
                for kind in kinds:
                    # A retired tracked page is a local page issue, never an untracked collision.
                    page = out / f'{kind}/{old.articles[0].file_stem}.md'
                    page.write_bytes(b'edited retired page')
                    (out / f'{kind}/{new.articles[1].file_stem}.md').mkdir()
                before = filesystem_state(self.work)
                result = self.run_cli('--inspect-managed', str(out), '--propose-update', '--proposed-paths',
                                      '--format', fmt, '--source', 'empty', str(empty),
                                      '--source', 'p', str(self.source), '--source', 'p', str(self.source))
                self.assertEqual((result.returncode, result.stderr), (2 if kinds else 0, ''))
                report = json.loads(result.stdout)
                proposed = report['proposed_update']
                self.assertEqual(proposed['checked_new_pages'], len(kinds))
                self.assertEqual([(c['namespace'], c['source_id'], c['kind']) for c in proposed['path_collisions']],
                                 [('p', '2', 'directory')] * len(kinds))
                self.assertEqual([c['path'] for c in proposed['path_collisions']],
                                 sorted(f'{kind}/{new.articles[1].file_stem}.md' for kind in kinds))
                self.assertEqual(len(report['conflicts']), len(kinds))
                self.assertEqual([a['source_id'] for a in proposed['changes']['articles']], ['1', '2'])
                baseline_annotation = old.articles[0].annotations[0]
                self.assertEqual(proposed['changes']['added'][0]['anchor'], baseline_annotation.anchor)
                self.assertEqual(proposed['changes']['added'][0]['block_id'], baseline_annotation.block_id)
                self.assertEqual(proposed['namespaces'], ['empty', 'p'])
                self.assertEqual([i['namespace'] for i in proposed['snapshot']['inputs']], ['empty', 'p', 'p'])
                self.assertEqual(filesystem_state(self.work), before)

    def test_missing_parents_clear_destinations_and_special_targets(self):
        out = self.work / 'empty'
        cli.publish(out, update.plan_update(out, core.parse(b'[]'), 'all').files)
        new = core.parse(b'[{"id":2,"annotations":[]}]')
        for step in ('absent', 'directory', 'fifo'):
            if step == 'fifo' and not hasattr(os, 'mkfifo'):
                continue
            if step == 'directory':
                (out / 'markdown').mkdir()
                (out / 'markdown/unrelated').write_bytes(b'ignored')
            elif step == 'fifo':
                os.mkfifo(out / 'markdown/article-2.md')
            before = filesystem_state(self.work)
            report = update.inspect_managed(out, next_export=new, include_paths=True)
            self.assertEqual(report['proposed_update']['checked_new_pages'], 2)
            self.assertEqual(report['status'], 'conflicts' if step == 'fifo' else 'clean')
            collisions = report['proposed_update']['path_collisions']
            self.assertEqual(len(collisions), 1 if step == 'fifo' else 0)
            if collisions:
                self.assertEqual(collisions[0]['kind'], 'other')
            self.assertEqual(filesystem_state(self.work), before)
        # No new article means no path probing, even if unrelated format paths are unsafe.
        original_lstat = Path.lstat
        def no_format_probe(path, *args, **kwargs):
            if path.parent == out and path.name in ('markdown', 'logseq'):
                raise AssertionError('probed a format without proposed pages')
            return original_lstat(path, *args, **kwargs)
        with patch.object(Path, 'lstat', no_format_probe):
            self.assertEqual(update.inspect_managed(out, next_export=core.parse(b'[]'),
                                                   include_paths=True)['proposed_update']['checked_new_pages'], 0)

    def test_invalid_flags_inputs_scope_and_baseline_suppress_stdout(self):
        for args in [ ['--inspect-managed', str(self.out), '--proposed-paths'],
                      ['--proposed-paths', str(self.source), '-o', 'unused'],
                      ['--proposed-paths', '--propose-update', str(self.source), '-o', 'unused'],
                      ['--inspect-managed', str(self.out), '--propose-update', '--proposed-paths'] ]:
            result = self.run_cli(*args)
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertIn('usage:', result.stderr)
        with self.assertRaises(core.BagnotesError) as caught:
            update.inspect_managed(self.out, include_paths=True)
        self.assertEqual(caught.exception.diagnostic['code'], 'update_scope')
        for data, extra, code in [(b'{', [], 'invalid_json'), (b'[{"id":2}]', [], 'incomplete_snapshot'),
                                  (AFTER, ['--format', 'csv'], 'update_scope')]:
            self.source.write_bytes(data)
            result = self.proposal('--proposed-paths', *extra)
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertEqual(json.loads(result.stderr)['code'], code)
        self.source.write_text(json.dumps(json.loads(AFTER) + [dict(id=2, annotations=[])]))
        (self.out / 'markdown/article-2.md').write_bytes(b'collision')
        for name, code in [('provenance.json', 'update_conflict'), (update.STATE, 'update_state')]:
            path = self.out / name
            saved = path.read_bytes()
            path.write_bytes(b'{}')
            before = filesystem_state(self.work)
            result = self.proposal('--proposed-paths')
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertEqual(json.loads(result.stderr)['code'], code)
            self.assertEqual(filesystem_state(self.work), before)
            path.write_bytes(saved)
        self.source.unlink()
        result = self.proposal('--proposed-paths')
        self.assertEqual((result.returncode, result.stdout), (3, ''))
        self.assertEqual(json.loads(result.stderr)['code'], 'io_error')

    @unittest.skipIf(os.name != 'posix' or os.geteuid() == 0, 'requires unprivileged POSIX file permissions')
    def test_real_destination_os_failure_is_exit_three_not_partial_report(self):
        out = self.work / 'empty'
        cli.publish(out, update.plan_update(out, core.parse(b'[]'), 'all').files)
        self.source.write_bytes(b'[{"id":2,"annotations":[]}]')
        # A collision is found first; later metadata failure must suppress that partial report.
        (out / 'logseq').mkdir()
        (out / 'logseq/article-2.md').write_bytes(b'collision')
        parent = out / 'markdown'
        parent.mkdir()
        before = filesystem_state(self.work)
        permissions = stat.S_IMODE(parent.stat().st_mode)
        parent.chmod(0)
        try:
            result = self.run_cli('--inspect-managed', str(out), '--propose-update',
                                  str(self.source), '--proposed-paths')
            self.assertEqual((result.returncode, result.stdout), (3, ''))
            self.assertEqual(json.loads(result.stderr)['code'], 'io_error')
        finally:
            parent.chmod(permissions)
        self.assertEqual(filesystem_state(self.work), before)


if __name__ == '__main__':
    unittest.main()
