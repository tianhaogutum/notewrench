"""Exact managed-region relationships. SPDX-License-Identifier: AGPL-3.0-only."""
from __future__ import annotations

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


def digest(data):
    return dict(sha256=update.sha(data), bytes=len(data))


def article(n, title=None):
    return dict(id=n, title=title or f'Title {n}', annotations=[])


class RegionRelationshipTests(unittest.TestCase):
    setUp = proposals.ProposedUpdateTests.setUp
    run_cli = proposals.ProposedUpdateTests.run_cli
    proposal = proposals.ProposedUpdateTests.proposal

    def test_all_relationships_with_identical_divergent_and_unavailable_keep_reports_independent(self):
        out = self.work / 'relationships'
        old = core.parse(json.dumps([article(n) for n in range(1, 10)]).encode())
        cli.publish(out, update.plan_update(out, old, 'all').files)
        current = core.parse(json.dumps([article(n) for n in range(1, 8)]).encode())
        update.publish_update(out, update.plan_update(out, current, 'all'))
        saved = {p.relative_to(out).as_posix(): update.region(p.read_bytes(), str(p))[1]
                 for p in out.rglob('*.md')}
        next_bytes = json.dumps([article(n, f'Next {n}' if n in (3, 4, 5) else None)
                                for n in (*range(1, 9), 10)]).encode()
        self.source.write_bytes(next_bytes)
        proposed = {name: update.managed(data) for name, data in render(core.parse(next_bytes), 'all')[0].items()
                    if name.endswith('.md')}
        local = dict(saved)
        for kind in ('markdown', 'logseq'):
            for n in (1, 2, 4, 5):
                name = f'{kind}/article-{n}.md'
                block = (proposed[name] if n == 4 else saved[name].replace(f'Title {n}'.encode(), b'LOCAL\xff\r\n')
                         if n in (2, 5) else saved[name])
                local[name] = block
                (out / name).write_bytes(b'PRIVATE prefix\xff\r\n' + block + b'PRIVATE suffix no newline')
            (out / f'{kind}/article-6.md').unlink()
            (out / f'{kind}/article-7.md').write_bytes(b'PRIVATE damaged markers')
        (out / 'report.json').write_bytes(b'PRIVATE report edit')
        target = self.work / 'private-target'
        target.write_bytes(b'PRIVATE collision target')
        (out / 'markdown/article-10.md').symlink_to(target)
        before = filesystem_state(self.work)
        args = ['--inspect-managed', str(out), '--propose-update', str(self.source),
                '--proposed-paths', '--proposed-regions']
        default = self.run_cli(*args)
        result = self.run_cli(*args, '--region-relationships')
        self.assertEqual((result.returncode, result.stderr), (2, ''))
        report = json.loads(result.stdout)
        relationships = report['proposed_update'].pop('region_relationships')
        self.assertEqual(report, json.loads(default.stdout))
        self.assertEqual([r['path'] for r in relationships], sorted(saved))
        categories = {1: 'unchanged', 2: 'local-only', 3: 'proposed-only',
                      4: 'identical-local-and-proposed', 5: 'divergent',
                      6: 'unavailable', 7: 'unavailable', 8: 'proposed-only', 9: 'unchanged'}
        for item in relationships:
            name = item['path']
            n = int(Path(name).stem.split('-')[1])
            self.assertEqual(item['relationship'], categories[n])
            self.assertEqual(item['saved'], digest(saved[name]))
            self.assertEqual(item['proposed'], digest(proposed.get(name, update.retired(name))))
            self.assertEqual(item['saved_state'], 'retired' if n in (8, 9) else 'active')
            self.assertEqual(item['proposed_state'], 'retired' if n == 9 else 'active')
            if n in (6, 7):
                self.assertIsNone(item['local'])
                reason = item['unavailable_reason']
                self.assertEqual(reason['code'], 'unreadable_page' if n == 6 else 'invalid_markers')
                self.assertTrue(reason['message'])
            else:
                self.assertEqual(item['local'], digest(local[name]))
                self.assertIsNone(item['unavailable_reason'])
        self.assertEqual(len(report['conflicts']), 10)
        self.assertEqual(len(report['artifact_conflicts']), 1)
        self.assertEqual(len(report['proposed_update']['path_collisions']), 1)
        self.assertNotIn('PRIVATE', json.dumps(relationships))
        self.assertNotIn('base64', json.dumps(relationships))
        repeated = self.run_cli(*args, '--region-relationships')
        self.assertEqual((repeated.returncode, repeated.stdout, repeated.stderr), (2, result.stdout, ''))
        self.assertEqual(filesystem_state(self.work), before)

    def test_all_unavailable_boundaries_never_extract_whole_page_or_follow_links(self):
        name = 'markdown/article-1.md'
        page = self.out / name
        original = page.read_bytes()
        target = self.work / 'private'
        target.write_bytes(original)
        # A whole page equal to a proposed region but without an intact pair is unavailable.
        cases = [None, 'directory', 'link', 'dangling', 'loop', 'fifo',
                 b'PRIVATE', original + update.BEGIN, original + update.END,
                 update.END + b'PRIVATE' + update.BEGIN,
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
                result = self.proposal('--region-relationships')
                self.assertEqual((result.returncode, result.stderr), (2, ''))
                item = next(r for r in json.loads(result.stdout)['proposed_update']['region_relationships'] if r['path'] == name)
                self.assertEqual(item['relationship'], 'unavailable')
                self.assertIsNone(item['local'])
                self.assertEqual(item['unavailable_reason']['code'], 'invalid_markers' if isinstance(case, bytes) else 'unreadable_page')
                self.assertEqual(filesystem_state(self.work), before)
                if case == 'directory':
                    page.rmdir()
                    page.write_bytes(original)
                elif case is None:
                    page.write_bytes(original)
        page.unlink()
        page.write_bytes(original)
        # Linked parents must be rejected before querying or reading their children.
        parent = self.out / 'markdown'
        parent.rename(self.work / 'moved')
        parent.symlink_to(self.work / 'moved', target_is_directory=True)
        before = filesystem_state(self.work)
        real_mode, real_read = update.path_mode, Path.read_bytes
        def mode(path):
            if path.parent == parent:
                raise AssertionError('queried a linked parent child')
            return real_mode(path)
        def read(path):
            if path.parent == parent:
                raise AssertionError('read through linked parent')
            return real_read(path)
        with patch('notewrench.wallabagnotes.update.path_mode', side_effect=mode), patch.object(Path, 'read_bytes', read):
            report = update.inspect_managed(self.out, next_export=core.parse(proposals.AFTER), include_relationships=True)
        item = next(r for r in report['proposed_update']['region_relationships'] if r['path'] == name)
        self.assertEqual(item['relationship'], 'unavailable')
        self.assertIn('symbolic links', item['unavailable_reason']['message'])
        self.assertEqual(filesystem_state(self.work), before)

    def test_all_formats_legacy_and_namespaced_scope_exact_rendering_stable_identities(self):
        data = proposals.AFTER.replace(b'"ranges": []', b'"ranges": [{"n": 0.123456789012345678901, "i": 9007199254740993}]')
        self.source.write_bytes(data)
        empty_file = self.work / 'empty.json'
        empty_file.write_bytes(b'[]')
        for fmt in ('all', 'markdown', 'logseq', 'csv'):
            for scoped in (False, True):
                with self.subTest(fmt=fmt, scoped=scoped):
                    out = self.work / f'{fmt}-{scoped}'
                    old = (core.parse_sources([('p', proposals.BEFORE), ('q', proposals.BEFORE), ('empty', b'[]')])
                           if scoped else core.parse(proposals.BEFORE))
                    new = (core.parse_sources([('empty', b'[]'), ('q', data), ('p', data), ('p', data)])
                           if scoped else core.parse(data))
                    cli.publish(out, update.plan_update(out, old, fmt).files)
                    plan = update.plan_update(out, new, fmt)
                    args = ['--inspect-managed', str(out), '--propose-update', '--format', fmt, '--region-relationships']
                    args += (['--source', 'empty', str(empty_file), '--source', 'q', str(self.source),
                              '--source', 'p', str(self.source), '--source', 'p', str(self.source)]
                             if scoped else [str(self.source)])
                    before = filesystem_state(self.work)
                    result = self.run_cli(*args)
                    self.assertEqual((result.returncode, result.stderr), (0, ''))
                    report = core.decode(result.stdout.encode())
                    self.assertEqual(report, update.inspect_managed(out, next_export=new, format_name=fmt, include_relationships=True))
                    items = report['proposed_update']['region_relationships']
                    self.assertEqual(len(items), (4 if scoped else 2) if fmt == 'all' else 0 if fmt == 'csv' else (2 if scoped else 1))
                    for item in items:
                        self.assertEqual(item['relationship'], 'proposed-only')
                        self.assertEqual(item['saved'], item['local'])
                        self.assertEqual(item['proposed'], digest(update.region(plan.files[item['path']], item['path'])[1]))
                    baseline = core.decode((out / 'provenance.json').read_bytes())['articles'][0]['annotations'][0]
                    change = report['proposed_update']['changes']['modified'][0]
                    for field in ('identity', 'anchor', 'block_id'):
                        self.assertEqual(change[field], baseline[field])
                    self.assertEqual(filesystem_state(self.work), before)
                    retired = (core.parse_sources([('p', b'[]'), ('q', b'[]'), ('empty', b'[]')]) if scoped else core.parse(b'[]'))
                    retirement = update.inspect_managed(out, next_export=retired, format_name=fmt, include_relationships=True)
                    for item in retirement['proposed_update']['region_relationships']:
                        self.assertEqual(item['proposed_state'], 'retired')
                        self.assertEqual(item['proposed'], digest(update.retired(item['path'])))
                    update.publish_update(out, update.plan_update(out, retired, fmt))
                    before = filesystem_state(self.work)
                    for next_export, state in [(retired, 'unchanged'), (old, 'proposed-only')]:
                        report = update.inspect_managed(out, next_export=next_export, format_name=fmt,
                                                        include_relationships=True, include_paths=True)
                        self.assertEqual(report['proposed_update']['checked_new_pages'], 0)
                        for item in report['proposed_update']['region_relationships']:
                            self.assertEqual(item['relationship'], state)
                            self.assertEqual(item['saved_state'], 'retired')
                    self.assertEqual(filesystem_state(self.work), before)

    def test_noop_raw_only_order_only_personal_bytes_and_independent_flags(self):
        raw = json.loads(proposals.BEFORE)
        raw[0]['extra'] = 'raw-only'
        reordered = json.loads(proposals.BEFORE)
        reordered[0]['annotations'].reverse()
        for page in self.out.rglob('*.md'):
            page.write_bytes(b'PRIVATE\xff\r\n' + page.read_bytes() + b'PRIVATE suffix')
        for data, category in [(proposals.BEFORE, 'unchanged'), (json.dumps(raw).encode(), 'unchanged'),
                               (json.dumps(reordered).encode(), 'proposed-only')]:
            self.source.write_bytes(data)
            before = filesystem_state(self.work)
            result = self.proposal('--region-relationships')
            self.assertEqual((result.returncode, result.stderr), (0, ''))
            report = json.loads(result.stdout)
            items = report['proposed_update'].pop('region_relationships')
            self.assertEqual({i['relationship'] for i in items}, {category})
            self.assertEqual(report, json.loads(self.proposal().stdout))
            self.assertEqual(filesystem_state(self.work), before)
        # New destinations are excluded and never inspected without path opt-in.
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
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['--inspect-managed', str(self.out), '--propose-update',
                                       str(self.source), '--region-relationships']), 0)
        self.assertEqual(self.proposal('--region-relationships', '--proposed-paths').returncode, 2)
        self.assertEqual(filesystem_state(self.work), before)

    def test_invalid_flags_and_api_require_snapshot_before_io(self):
        for args in [['--inspect-managed', 'missing', '--region-relationships'],
                     ['--region-relationships', 'missing', '-o', 'unused'],
                     ['--region-relationships', '--propose-update', 'missing', '-o', 'unused'],
                     ['--inspect-managed', 'missing', '--propose-update', '--region-relationships'],
                     *[['--inspect-managed', 'missing', '--propose-update', str(self.source),
                        '--region-relationships', *extra] for extra in
                       (['--update'], ['--dry-run'], ['-o', 'unused'], ['--inspect-conflicts'], ['--conflict-values'])]]:
            result = self.run_cli(*args)
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertIn('usage:', result.stderr)
        with patch('notewrench.wallabagnotes.update.path_mode', side_effect=AssertionError('bundle I/O')):
            with self.assertRaises(core.BagnotesError) as caught:
                update.inspect_managed(self.out, include_relationships=True)
        self.assertEqual(caught.exception.diagnostic['code'], 'update_scope')

    def test_invalid_input_scope_baseline_and_later_inputs_suppress_stdout(self):
        (self.out / 'report.json').write_bytes(b'local conflict')
        for data, code in [(b'{', 'invalid_json'), (b'[{}]', 'invalid_id'),
                           (b'[{"id":1}]', 'incomplete_snapshot')]:
            self.source.write_bytes(data)
            result = self.proposal('--region-relationships', '--proposed-regions', '--proposed-paths')
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertEqual(json.loads(result.stderr)['code'], code)
        self.source.write_bytes(proposals.AFTER)
        for args in [('--format', 'csv'), ('--source', 'p', str(self.source))]:
            result = (self.proposal('--region-relationships', *args) if args[0] == '--format' else
                      self.run_cli('--inspect-managed', str(self.out), '--propose-update', '--region-relationships', *args))
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertEqual(json.loads(result.stderr)['code'], 'update_scope')
        for name, code in [('provenance.json', 'update_conflict'), (update.STATE, 'update_state')]:
            path = self.out / name
            original = path.read_bytes()
            path.write_bytes(b'{}')
            result = self.proposal('--region-relationships')
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertEqual(json.loads(result.stderr)['code'], code)
            path.write_bytes(original)
        path = self.out / update.STATE
        original = path.read_bytes()
        manifest = json.loads(original)
        manifest['pages']['markdown/article-1.md'] = '0' * 64
        path.write_text(json.dumps(manifest))
        result = self.proposal('--region-relationships')
        self.assertEqual((result.returncode, result.stdout), (2, ''))
        self.assertEqual(json.loads(result.stderr)['code'], 'update_state')
        path.write_bytes(original)
        scoped = self.work / 'scoped'
        cli.publish(scoped, update.plan_update(scoped, core.parse_sources([('p', proposals.BEFORE)]), 'all').files)
        later = self.work / 'later.json'
        for data, code in [(b'{', 'invalid_json'), (proposals.BEFORE, 'annotation_conflict'),
                           (b'[{"id":2}]', 'incomplete_snapshot')]:
            later.write_bytes(data)
            before = filesystem_state(self.work)
            result = self.run_cli('--inspect-managed', str(scoped), '--propose-update', '--region-relationships',
                                  '--source', 'p', str(self.source), '--source', 'p', str(later))
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertEqual(json.loads(result.stderr)['code'], code)
            self.assertEqual(filesystem_state(self.work), before)
        self.source.unlink()
        result = self.proposal('--region-relationships')
        self.assertEqual((result.returncode, result.stdout), (3, ''))
        self.assertEqual(json.loads(result.stderr)['code'], 'io_error')

    @unittest.skipIf(os.name != 'posix' or os.geteuid() == 0, 'requires unprivileged POSIX file permissions')
    def test_actual_os_failures_are_not_unavailable_and_suppress_stdout(self):
        (self.out / 'changes.json').unlink()
        for path in [self.source, self.out / 'provenance.json', self.out / update.STATE,
                     self.out / 'report.json', self.out / 'markdown/article-1.md', self.out / 'markdown']:
            with self.subTest(path=path):
                before = filesystem_state(self.work)
                permissions = stat.S_IMODE(path.stat().st_mode)
                path.chmod(0)
                try:
                    result = self.proposal('--region-relationships', '--proposed-regions', '--proposed-paths')
                    self.assertEqual((result.returncode, result.stdout), (3, ''))
                    self.assertEqual(json.loads(result.stderr)['code'], 'io_error')
                finally:
                    path.chmod(permissions)
                self.assertEqual(filesystem_state(self.work), before)


if __name__ == '__main__':
    unittest.main()
