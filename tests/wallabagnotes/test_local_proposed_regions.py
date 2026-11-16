"""Exact local-to-proposed managed bytes. SPDX-License-Identifier: AGPL-3.0-only."""
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
from notewrench.wallabagnotes.render import render
from .test_managed_conflicts import filesystem_state
from . import test_proposed_updates as proposals


def article(n, title=None):
    return dict(id=n, title=title or f'Title {n}', annotations=[])


class LocalProposedRegionTests(unittest.TestCase):
    setUp = proposals.ProposedUpdateTests.setUp
    run_cli = proposals.ProposedUpdateTests.run_cli
    proposal = proposals.ProposedUpdateTests.proposal

    def preview(self, *extra):
        return self.proposal('--region-relationships', '--local-proposed-regions', *extra)

    def assert_bytes(self, description, data):
        self.assertEqual(description, dict(sha256=update.sha(data), bytes=len(data),
                                          base64=base64.b64encode(data).decode('ascii')))

    def assert_comparison(self, item, local, proposed):
        name = item['path']
        self.assertEqual(set(item), {'path', 'local', 'proposed', 'unified_diff'})
        self.assert_bytes(item['local'], local)
        self.assert_bytes(item['proposed'], proposed)
        self.assertTrue(item['unified_diff'].startswith(f'--- {name}:local\n+++ {name}:proposed\n'))
        self.assertEqual(item['unified_diff'], update.region_diff(name, local, proposed, labels=('local', 'proposed')))

    def test_mixed_identical_divergent_unavailable_and_independent_reports(self):
        out = self.work / 'mixed'
        old = core.parse(json.dumps([article(n) for n in range(1, 10)]).encode())
        cli.publish(out, update.plan_update(out, old, 'all').files)
        current = core.parse(json.dumps([article(n) for n in range(1, 8)]).encode())
        update.publish_update(out, update.plan_update(out, current, 'all'))
        saved = {p.relative_to(out).as_posix(): update.region(p.read_bytes(), str(p))[1]
                 for p in out.rglob('*.md')}
        data = json.dumps([article(n, f'Next {n}' if n in (3, 4, 5) else None)
                           for n in (*range(1, 9), 10)]).encode()
        self.source.write_bytes(data)
        proposed = {n: update.managed(b) for n, b in render(core.parse(data), 'all')[0].items() if n.endswith('.md')}
        local = dict(saved)
        for kind in ('markdown', 'logseq'):
            for n in (2, 4, 5):
                name = f'{kind}/article-{n}.md'
                local[name] = proposed[name] if n == 4 else saved[name].replace(f'Title {n}'.encode(), b'LOCAL\xff\r\n')
                (out / name).write_bytes(b'PRIVATE prefix\xff\r\n' + local[name] + b'PRIVATE suffix')
            (out / f'{kind}/article-6.md').unlink()
            (out / f'{kind}/article-7.md').write_bytes(b'PRIVATE broken markers')
        (out / 'report.json').write_bytes(b'PRIVATE artifact')
        (out / 'markdown/article-10.md').symlink_to('missing')
        before = filesystem_state(self.work)
        args = ['--inspect-managed', str(out), '--propose-update', str(self.source), '--region-relationships',
                '--proposed-paths', '--proposed-regions']
        default = self.run_cli(*args)
        report_before = json.loads(default.stdout)
        categories = {r['relationship'] for r in report_before['proposed_update']['region_relationships']}
        self.assertEqual(categories, {'unchanged', 'local-only', 'proposed-only', 'identical-local-and-proposed',
                                     'divergent', 'unavailable'})
        self.assertNotIn('local_region_changes', report_before['proposed_update'])
        result = self.run_cli(*args, '--local-proposed-regions')
        self.assertEqual((result.returncode, result.stderr), (2, ''))
        report = json.loads(result.stdout)
        items = report['proposed_update'].pop('local_region_changes')
        self.assertEqual(report, report_before)
        self.assertEqual([i['path'] for i in items], sorted(f'{k}/article-{n}.md' for k in ('markdown', 'logseq')
                                                          for n in (2, 3, 5, 8)))
        for item in items:
            self.assert_comparison(item, local[item['path']], proposed[item['path']])
        self.assertIn('\\xff', next(i for i in items if i['path'] == 'markdown/article-5.md')['unified_diff'])
        self.assertNotIn('PRIVATE', json.dumps(items))
        repeat = self.run_cli(*args, '--local-proposed-regions')
        self.assertEqual((repeat.returncode, repeat.stdout, repeat.stderr), (2, result.stdout, ''))
        self.assertEqual(filesystem_state(self.work), before)

    def test_unavailable_paths_markers_and_linked_parent_never_become_comparisons(self):
        name = 'markdown/article-1.md'
        page = self.out / name
        original = page.read_bytes()
        target = self.work / 'private'
        target.write_bytes(original)
        cases = [None, 'directory', 'link', 'dangling', 'loop', 'fifo', b'PRIVATE',
                 original + update.BEGIN, original + update.END, update.END + b'PRIVATE' + update.BEGIN,
                 original.replace(update.BEGIN, update.BEGIN.replace(b'\n', b'\r\n'))]
        for case in cases:
            with self.subTest(case=case):
                page.unlink()
                if isinstance(case, bytes):
                    page.write_bytes(case)
                elif case == 'directory':
                    page.mkdir()
                elif case in ('link', 'dangling', 'loop'):
                    page.symlink_to(target if case == 'link' else 'missing' if case == 'dangling' else page.name)
                elif case == 'fifo':
                    if not hasattr(os, 'mkfifo'):
                        page.write_bytes(original)
                        continue
                    os.mkfifo(page)
                before = filesystem_state(self.work)
                result = self.preview()
                self.assertEqual((result.returncode, result.stderr), (2, ''))
                proposed = json.loads(result.stdout)['proposed_update']
                item = next(r for r in proposed['region_relationships'] if r['path'] == name)
                self.assertEqual((item['relationship'], item['local']), ('unavailable', None))
                self.assertEqual(item['unavailable_reason']['code'], 'invalid_markers' if isinstance(case, bytes) else 'unreadable_page')
                self.assertNotIn(name, [i['path'] for i in proposed['local_region_changes']])
                self.assertNotIn('PRIVATE', json.dumps(proposed['local_region_changes']))
                self.assertEqual(filesystem_state(self.work), before)
                if case == 'directory':
                    page.rmdir()
                    page.write_bytes(original)
                elif case is None:
                    page.write_bytes(original)
        page.unlink()
        page.write_bytes(original)
        parent = self.out / 'markdown'
        parent.rename(self.work / 'moved')
        parent.symlink_to(self.work / 'moved', target_is_directory=True)
        before = filesystem_state(self.work)
        real_mode, real_read = update.path_mode, Path.read_bytes
        def mode(path):
            if path.parent == parent:
                raise AssertionError('queried linked parent child')
            return real_mode(path)
        def read(path):
            if path.parent == parent:
                raise AssertionError('read through linked parent')
            return real_read(path)
        with patch('notewrench.wallabagnotes.update.path_mode', side_effect=mode), patch.object(Path, 'read_bytes', read):
            report = update.inspect_managed(self.out, next_export=core.parse(proposals.AFTER),
                                            include_relationships=True, include_local_regions=True)
        self.assertTrue(all(i['path'].startswith('logseq/') for i in report['proposed_update']['local_region_changes']))
        self.assertEqual(filesystem_state(self.work), before)

    def test_all_formats_and_scopes_exact_bytes_lifecycle_stable_identities(self):
        data = proposals.AFTER.replace(b'"ranges": []', b'"ranges": [{"n": 0.123456789012345678901, "i": 9007199254740993}]')
        self.source.write_bytes(data)
        empty = self.work / 'empty.json'
        empty.write_bytes(b'[]')
        for fmt in ('all', 'markdown', 'logseq', 'csv'):
            for scoped in (False, True):
                with self.subTest(fmt=fmt, scoped=scoped):
                    out = self.work / f'{fmt}-{scoped}'
                    def parsed(raw):
                        return (core.parse_sources([('p', raw), ('q', raw), ('empty', b'[]')]) if scoped else core.parse(raw))
                    old, new, retired = parsed(proposals.BEFORE), parsed(data), parsed(b'[]')
                    if scoped:
                        new = core.parse_sources([('empty', b'[]'), ('q', data), ('p', data), ('p', data)])
                    cli.publish(out, update.plan_update(out, old, fmt).files)
                    plan = update.plan_update(out, new, fmt)
                    args = ['--inspect-managed', str(out), '--propose-update', '--format', fmt,
                            '--region-relationships', '--local-proposed-regions']
                    args += (['--source', 'empty', str(empty), '--source', 'q', str(self.source),
                              '--source', 'p', str(self.source), '--source', 'p', str(self.source)]
                             if scoped else [str(self.source)])
                    before = filesystem_state(self.work)
                    result = self.run_cli(*args)
                    self.assertEqual((result.returncode, result.stderr), (0, ''))
                    report = core.decode(result.stdout.encode())
                    self.assertEqual(report, update.inspect_managed(out, next_export=new, format_name=fmt,
                                     include_relationships=True, include_local_regions=True))
                    items = report['proposed_update']['local_region_changes']
                    self.assertEqual(len(items), (4 if scoped else 2) if fmt == 'all' else 0 if fmt == 'csv' else (2 if scoped else 1))
                    for item in items:
                        self.assert_comparison(item, update.region((out / item['path']).read_bytes(), item['path'])[1],
                                               update.region(plan.files[item['path']], item['path'])[1])
                    annotation = core.decode((out / 'provenance.json').read_bytes())['articles'][0]['annotations'][0]
                    change = report['proposed_update']['changes']['modified'][0]
                    for field in ('identity', 'anchor', 'block_id'):
                        self.assertEqual(change[field], annotation[field])
                    self.assertEqual(filesystem_state(self.work), before)
                    for next_export in (retired,):
                        report = update.inspect_managed(out, next_export=next_export, format_name=fmt,
                                                        include_relationships=True, include_local_regions=True)
                        for item in report['proposed_update']['local_region_changes']:
                            self.assert_bytes(item['proposed'], update.retired(item['path']))
                    self.assertEqual(filesystem_state(self.work), before)
                    update.publish_update(out, update.plan_update(out, retired, fmt))
                    before = filesystem_state(self.work)
                    for next_export in (retired, old):
                        report = update.inspect_managed(out, next_export=next_export, format_name=fmt,
                                                        include_relationships=True, include_local_regions=True, include_paths=True)
                        self.assertEqual(report['proposed_update']['checked_new_pages'], 0)
                        items = report['proposed_update']['local_region_changes']
                        self.assertEqual(len(items), 0 if next_export is retired else len(report['proposed_update']['region_relationships']))
                        for item in items:
                            self.assert_bytes(item['local'], update.retired(item['path']))
                    self.assertEqual(filesystem_state(self.work), before)

    def test_noop_raw_only_order_only_and_no_new_destination_io_or_publication(self):
        raw, reordered = json.loads(proposals.BEFORE), json.loads(proposals.BEFORE)
        raw[0]['extra'] = 'raw-only'
        reordered[0]['annotations'].reverse()
        for page in self.out.rglob('*.md'):
            page.write_bytes(b'PRIVATE\xff\r\n' + page.read_bytes() + b'PRIVATE suffix')
        for data, count in [(proposals.BEFORE, 0), (json.dumps(raw).encode(), 0), (json.dumps(reordered).encode(), 2)]:
            self.source.write_bytes(data)
            before = filesystem_state(self.work)
            result = self.preview()
            self.assertEqual((result.returncode, result.stderr), (0, ''))
            report = json.loads(result.stdout)
            items = report['proposed_update'].pop('local_region_changes')
            self.assertEqual(len(items), count)
            self.assertEqual(report, json.loads(self.proposal('--region-relationships').stdout))
            self.assertNotIn('PRIVATE', json.dumps(items))
            self.assertEqual(filesystem_state(self.work), before)
        self.source.write_bytes(b'[{"id":2,"annotations":[]}]')
        (self.out / 'markdown/article-2.md').symlink_to('missing')
        before = filesystem_state(self.work)
        real_mode = update.path_mode
        def mode(path):
            if path.name == 'article-2.md':
                raise AssertionError('untracked destination inspected')
            return real_mode(path)
        with patch('notewrench.wallabagnotes.update.path_mode', side_effect=mode), \
             patch('notewrench.wallabagnotes.update.plan_update', side_effect=AssertionError('planned')), \
             patch('notewrench.wallabagnotes.update.publish_update', side_effect=AssertionError('published')), \
             patch('notewrench.wallabagnotes.cli.publish', side_effect=AssertionError('published')), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(['--inspect-managed', str(self.out), '--propose-update', str(self.source),
                                       '--region-relationships', '--local-proposed-regions']), 0)
        self.assertTrue(all(i['path'].endswith('article-1.md') for i in json.loads(output.getvalue())['proposed_update']['local_region_changes']))
        self.assertEqual(self.preview('--proposed-paths').returncode, 2)
        self.assertEqual(filesystem_state(self.work), before)

    def test_dependency_flags_and_api_fail_before_io(self):
        for args in [['--local-proposed-regions'],
                     ['--inspect-managed', 'missing', '--propose-update', 'missing', '--local-proposed-regions'],
                     ['--inspect-managed', 'missing', '--region-relationships', '--local-proposed-regions'],
                     ['--inspect-managed', 'missing', '--propose-update', '--region-relationships', '--local-proposed-regions'],
                     *[['--inspect-managed', 'missing', '--propose-update', 'missing', '--region-relationships',
                        '--local-proposed-regions', *extra] for extra in
                       (['--update'], ['--dry-run'], ['-o', 'unused'], ['--inspect-conflicts'], ['--conflict-values'])]]:
            result = self.run_cli(*args)
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertIn('usage:', result.stderr)
        with patch('notewrench.wallabagnotes.update.path_mode', side_effect=AssertionError('bundle I/O')):
            for extra in ({}, dict(include_relationships=True), dict(next_export=core.parse(proposals.AFTER))):
                with self.assertRaises(core.BagnotesError) as caught:
                    update.inspect_managed(self.out, include_local_regions=True, **extra)
                self.assertEqual(caught.exception.diagnostic['code'], 'update_scope')

    def test_invalid_inputs_scope_baselines_no_stdout(self):
        (self.out / 'report.json').write_bytes(b'local conflict')
        for data, code in [(b'{', 'invalid_json'), (b'[{}]', 'invalid_id'), (b'[{"id":1}]', 'incomplete_snapshot')]:
            self.source.write_bytes(data)
            result = self.preview('--proposed-regions', '--proposed-paths')
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertEqual(json.loads(result.stderr)['code'], code)
        self.source.write_bytes(proposals.AFTER)
        result = self.preview('--format', 'csv')
        self.assertEqual((result.returncode, result.stdout), (2, ''))
        self.assertEqual(json.loads(result.stderr)['code'], 'update_scope')
        result = self.run_cli('--inspect-managed', str(self.out), '--propose-update', '--source', 'p', str(self.source),
                              '--region-relationships', '--local-proposed-regions')
        self.assertEqual((result.returncode, result.stdout), (2, ''))
        self.assertEqual(json.loads(result.stderr)['code'], 'update_scope')
        for name, code in [('provenance.json', 'update_conflict'), (update.STATE, 'update_state')]:
            path = self.out / name
            original = path.read_bytes()
            path.write_bytes(b'{}')
            result = self.preview()
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertEqual(json.loads(result.stderr)['code'], code)
            path.write_bytes(original)
        path = self.out / update.STATE
        original = path.read_bytes()
        manifest = json.loads(original)
        manifest['pages']['markdown/article-1.md'] = '0' * 64
        path.write_text(json.dumps(manifest))
        result = self.preview()
        self.assertEqual((result.returncode, result.stdout), (2, ''))
        self.assertEqual(json.loads(result.stderr)['code'], 'update_state')
        path.write_bytes(original)
        self.source.unlink()
        result = self.preview()
        self.assertEqual((result.returncode, result.stdout), (3, ''))
        self.assertEqual(json.loads(result.stderr)['code'], 'io_error')

    @unittest.skipIf(os.name != 'posix' or os.geteuid() == 0, 'requires unprivileged POSIX permissions')
    def test_actual_os_errors_abort_not_unavailable(self):
        (self.out / 'changes.json').unlink()
        for path in [self.source, self.out / 'provenance.json', self.out / update.STATE,
                     self.out / 'report.json', self.out / 'markdown/article-1.md', self.out / 'markdown']:
            with self.subTest(path=path):
                before = filesystem_state(self.work)
                permissions = stat.S_IMODE(path.stat().st_mode)
                path.chmod(0)
                try:
                    result = self.preview('--proposed-regions', '--proposed-paths')
                    self.assertEqual((result.returncode, result.stdout), (3, ''))
                    self.assertEqual(json.loads(result.stderr)['code'], 'io_error')
                finally:
                    path.chmod(permissions)
                self.assertEqual(filesystem_state(self.work), before)


if __name__ == '__main__':
    unittest.main()
