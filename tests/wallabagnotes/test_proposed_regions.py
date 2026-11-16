"""Saved-to-proposed managed regions. SPDX-License-Identifier: AGPL-3.0-only."""
from __future__ import annotations

import base64
import contextlib
import io
import os
import stat
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from notewrench.wallabagnotes import cli, core, update
from notewrench.wallabagnotes.render import render
from .test_managed_conflicts import filesystem_state
from . import test_proposed_updates as proposals

BEFORE, AFTER = proposals.BEFORE, proposals.AFTER


class ProposedRegionTests(unittest.TestCase):
    setUp = proposals.ProposedUpdateTests.setUp
    run_cli = proposals.ProposedUpdateTests.run_cli
    proposal = proposals.ProposedUpdateTests.proposal

    def assert_bytes(self, description, data):
        self.assertEqual(description, dict(sha256=update.sha(data), bytes=len(data),
                                          base64=base64.b64encode(data).decode('ascii')))

    def test_lifecycle_previews_ignore_local_edits_and_keep_all_diagnostics_separate(self):
        def article(n):
            return dict(id=n, title=f'Article {n}', annotations=[])
        out = self.work / 'lifecycle'
        old = core.parse(json.dumps(json.loads(BEFORE) + [article(n) for n in (2, 3, 5)]).encode())
        cli.publish(out, update.plan_update(out, old, 'all').files)
        current = core.parse(json.dumps(json.loads(BEFORE) + [article(n) for n in (2, 5)]).encode())
        update.publish_update(out, update.plan_update(out, current, 'all'))
        saved = {p.relative_to(out).as_posix(): update.region(p.read_bytes(), str(p))[1]
                 for p in out.rglob('*.md')}
        next_bytes = json.dumps(json.loads(AFTER) + [article(n) for n in (4, 5, 3)]).encode()
        self.source.write_bytes(next_bytes)
        new = core.parse(next_bytes)
        generated = render(new, 'all')[0]
        for kind in ('markdown', 'logseq'):
            page = out / f'{kind}/article-1.md'
            page.write_bytes(b'PRIVATE prefix\xff\r\n' + page.read_bytes().replace(b'original', b'LOCAL EDIT') +
                             b'PRIVATE suffix no newline')
        (out / 'markdown/article-2.md').unlink()
        (out / 'logseq/article-3.md').write_bytes(b'PRIVATE damaged markers')
        (out / 'report.json').write_bytes(b'PRIVATE report edit')
        target = self.work / 'private-target'
        target.write_bytes(b'PRIVATE collision bytes')
        (out / 'markdown/article-4.md').symlink_to(target)
        before = filesystem_state(self.work)
        args = ['--inspect-managed', str(out), '--propose-update', str(self.source), '--proposed-paths']
        default = self.run_cli(*args)
        result = self.run_cli(*args, '--proposed-regions')
        self.assertEqual((result.returncode, result.stderr), (2, ''))
        report = json.loads(result.stdout)
        proposed = report['proposed_update']
        regions = proposed.pop('region_changes')
        self.assertEqual(report, json.loads(default.stdout))
        self.assertEqual([r['path'] for r in regions],
                         [f'{kind}/article-{n}.md' for kind in ('logseq', 'markdown') for n in (1, 2, 3, 4)])
        transitions = {1: ('modified', 'active', 'active'), 2: ('retired', 'active', 'retired'),
                       3: ('reappeared', 'retired', 'active'), 4: ('added', 'absent', 'active')}
        for item in regions:
            name = item['path']
            n = int(Path(name).stem.split('-')[1])
            self.assertEqual((item['change'], item['before_state'], item['after_state']), transitions[n])
            self.assert_bytes(item['before'], saved.get(name, b''))
            self.assert_bytes(item['after'], update.managed(generated[name]) if name in generated else update.retired(name))
            self.assertTrue(item['unified_diff'].startswith(f'--- {name}:saved\n+++ {name}:proposed\n'))
        self.assertNotIn('PRIVATE', json.dumps(regions))
        self.assertNotIn('LOCAL EDIT', json.dumps(regions))
        self.assertNotIn('Personal notes', json.dumps(regions))
        self.assertEqual(len(report['conflicts']), 4)
        self.assertEqual([c['path'] for c in report['artifact_conflicts']], ['report.json'])
        self.assertEqual([c['path'] for c in proposed['path_collisions']], ['markdown/article-4.md'])
        self.assertEqual(proposed['changes']['counts'], dict(added=1, modified=1, deleted=1))
        annotation = proposed['changes']['modified'][0]
        self.assertEqual(annotation['identity'], 'id:9')
        baseline = json.loads((out / 'provenance.json').read_bytes())['articles'][0]['annotations'][0]
        for field in ('anchor', 'block_id'):
            self.assertEqual(annotation[field], baseline[field])
        repeated = self.run_cli(*args, '--proposed-regions')
        self.assertEqual((repeated.returncode, repeated.stdout, repeated.stderr), (2, result.stdout, ''))
        self.assertEqual(filesystem_state(self.work), before)

    def test_clean_exit_noop_raw_only_order_only_changes_and_update_byte_parity(self):
        raw_only = json.loads(BEFORE)
        raw_only[0]['extra'] = {'nested': 'metadata only'}
        raw_only[0]['annotations'][0]['extra'] = 'not rendered'
        reordered = json.loads(BEFORE)
        reordered[0]['annotations'].reverse()
        cases = [(BEFORE, 0), (AFTER, 2), (b'[]', 2),
                 (json.dumps(raw_only).encode(), 0), (json.dumps(reordered).encode(), 2)]
        for data, count in cases:
            with self.subTest(data=data):
                self.source.write_bytes(data)
                new = core.parse(data)
                plan = update.plan_update(self.out, new, 'all')
                before = filesystem_state(self.work)
                result = self.proposal('--proposed-regions')
                self.assertEqual((result.returncode, result.stderr), (0, ''))
                report = json.loads(result.stdout)
                regions = report['proposed_update']['region_changes']
                self.assertEqual(len(regions), count)
                for item in regions:
                    self.assert_bytes(item['after'], update.region(plan.files[item['path']], item['path'])[1])
                if data == json.dumps(raw_only).encode():
                    self.assertEqual(report['proposed_update']['changes']['counts']['modified'], 1)
                    self.assertEqual(len(report['proposed_update']['changes']['articles']), 1)
                if data == json.dumps(reordered).encode():
                    self.assertEqual(report['proposed_update']['changes']['counts'], dict(added=0, modified=0, deleted=0))
                default = json.loads(self.proposal().stdout)
                del report['proposed_update']['region_changes']
                self.assertEqual(report, default)
                self.assertEqual(filesystem_state(self.work), before)
        # Rendering previews never invokes a write plan or publication.
        with patch('notewrench.wallabagnotes.update.plan_update', side_effect=AssertionError('planned')), \
             patch('notewrench.wallabagnotes.update.publish_update', side_effect=AssertionError('updated')), \
             patch('notewrench.wallabagnotes.cli.publish', side_effect=AssertionError('published')), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['--inspect-managed', str(self.out), '--propose-update',
                                       str(self.source), '--proposed-regions']), 0)

    def test_namespaced_all_formats_repeated_inputs_retirement_return_and_exact_rendering(self):
        # Preserve opaque exact numbers and render Unicode, escaped markup and CRLF.
        data = AFTER.replace(b'"ranges": []', b'"ranges": [{"n": 0.123456789012345678901, "i": 9007199254740993}]')
        data = data.replace(b'updated ', b'line\\r\\nupdated ')
        self.source.write_bytes(data)
        empty = self.work / 'empty.json'
        empty.write_bytes(b'[]')
        for fmt in ('all', 'markdown', 'logseq', 'csv'):
            with self.subTest(fmt=fmt):
                out = self.work / fmt
                old = core.parse_sources([('p', BEFORE), ('q', BEFORE), ('empty', b'[]')])
                cli.publish(out, update.plan_update(out, old, fmt).files)
                new = core.parse_sources([('empty', b'[]'), ('q', data), ('p', data), ('p', data)])
                before = filesystem_state(self.work)
                result = self.run_cli('--inspect-managed', str(out), '--propose-update', '--proposed-regions',
                                      '--format', fmt, '--source', 'empty', str(empty),
                                      '--source', 'q', str(self.source), '--source', 'p', str(self.source),
                                      '--source', 'p', str(self.source))
                self.assertEqual((result.returncode, result.stderr), (0, ''))
                report = core.decode(result.stdout.encode())
                self.assertEqual(report, update.inspect_managed(out, next_export=new, format_name=fmt, include_regions=True))
                regions = report['proposed_update']['region_changes']
                self.assertEqual(len(regions), 4 if fmt == 'all' else 0 if fmt == 'csv' else 2)
                self.assertEqual([r['path'] for r in regions], sorted(r['path'] for r in regions))
                rendered = render(new, fmt)[0]
                for item in regions:
                    self.assert_bytes(item['after'], update.managed(rendered[item['path']]))
                    after = base64.b64decode(item['after']['base64'])
                    article = next(a for a in new.articles if a.file_stem in item['path'])
                    self.assertIn(article.annotations[0].anchor.encode(), after)
                    if item['path'].startswith('logseq/'):
                        self.assertIn(article.annotations[0].block_id.encode(), after)
                    self.assertIn(b'0.123456789012345678901', after)
                self.assertEqual(filesystem_state(self.work), before)
                # Both namespaces are explicitly empty; retired paths persist.
                retired = core.parse_sources([('empty', b'[]'), ('p', b'[]'), ('q', b'[]')])
                retirement = update.inspect_managed(out, next_export=retired, format_name=fmt, include_regions=True)
                for item in retirement['proposed_update']['region_changes']:
                    self.assertEqual((item['change'], item['after_state']), ('retired', 'retired'))
                    self.assert_bytes(item['after'], update.retired(item['path']))
                update.publish_update(out, update.plan_update(out, retired, fmt))
                before = filesystem_state(self.work)
                self.assertEqual(update.inspect_managed(out, next_export=retired, format_name=fmt,
                                                        include_regions=True)['proposed_update']['region_changes'], [])
                returned = update.inspect_managed(out, next_export=old, format_name=fmt,
                                                  include_regions=True, include_paths=True)['proposed_update']
                self.assertEqual(returned['checked_new_pages'], 0)
                self.assertEqual(returned['path_collisions'], [])
                self.assertEqual([r['path'] for r in returned['region_changes']], [r['path'] for r in regions])
                for item in returned['region_changes']:
                    self.assertEqual((item['change'], item['before_state']), ('reappeared', 'retired'))
                    self.assert_bytes(item['before'], update.retired(item['path']))
                self.assertEqual(filesystem_state(self.work), before)

    def test_collision_only_exits_and_no_collision_reads_without_path_option(self):
        self.source.write_bytes(b'[{"id":2,"annotations":[]}]')
        target = self.work / 'private'
        target.mkdir()
        (target / 'note').write_bytes(b'PRIVATE note')
        (self.out / 'markdown/article-2.md').symlink_to(target, target_is_directory=True)
        (self.out / 'logseq/article-2.md').mkdir()
        before = filesystem_state(self.work)
        unflagged = self.proposal('--proposed-regions')
        self.assertEqual((unflagged.returncode, unflagged.stderr), (0, ''))
        flagged = self.proposal('--proposed-regions', '--proposed-paths')
        self.assertEqual((flagged.returncode, flagged.stderr), (2, ''))
        regions = json.loads(unflagged.stdout)['proposed_update']['region_changes']
        self.assertEqual(json.loads(flagged.stdout)['proposed_update']['region_changes'], regions)
        self.assertEqual(len(regions), 4)
        self.assertNotIn('PRIVATE', flagged.stdout)
        # Neither contents nor metadata of untracked destinations are needed for previews.
        real_mode = update.path_mode
        def mode(path):
            if path.name == 'article-2.md':
                raise AssertionError('untracked destination inspected without path opt-in')
            return real_mode(path)
        with patch('notewrench.wallabagnotes.update.path_mode', side_effect=mode):
            self.assertEqual(update.inspect_managed(self.out, next_export=core.parse(self.source.read_bytes()),
                                                   include_regions=True)['proposed_update']['region_changes'], regions)
        self.assertEqual(filesystem_state(self.work), before)

    def test_invalid_flags_fail_before_io_and_api_requires_next_snapshot(self):
        for args in [ ['--inspect-managed', 'missing', '--proposed-regions'],
                      ['--proposed-regions', 'missing', '-o', 'unused'],
                      ['--proposed-regions', '--propose-update', 'missing', '-o', 'unused'],
                      ['--inspect-managed', 'missing', '--propose-update', '--proposed-regions'],
                      ['--inspect-managed', 'missing', '--propose-update', '--proposed-regions',
                       str(self.source), '--update'],
                      ['--inspect-managed', 'missing', '--propose-update', '--proposed-regions',
                       str(self.source), '--dry-run'],
                      ['--inspect-managed', 'missing', '--propose-update', '--proposed-regions',
                       str(self.source), '--conflict-values'] ]:
            result = self.run_cli(*args)
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertIn('usage:', result.stderr)
        with patch('notewrench.wallabagnotes.update.path_mode', side_effect=AssertionError('bundle I/O')):
            with self.assertRaises(core.BagnotesError) as caught:
                update.inspect_managed(self.out, include_regions=True)
        self.assertEqual(caught.exception.diagnostic['code'], 'update_scope')

    def test_invalid_next_scope_and_baselines_suppress_all_stdout(self):
        (self.out / 'report.json').write_bytes(b'local conflict')
        for data, code in [(b'{', 'invalid_json'), (b'[{}]', 'invalid_id'),
                           (b'[{"id":1}]', 'incomplete_snapshot')]:
            self.source.write_bytes(data)
            before = filesystem_state(self.work)
            result = self.proposal('--proposed-regions', '--proposed-paths')
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertEqual(json.loads(result.stderr)['code'], code)
            self.assertEqual(filesystem_state(self.work), before)
        self.source.write_bytes(AFTER)
        for extra in [('--format', 'csv'), ('--source', 'p', str(self.source))]:
            args = ['--inspect-managed', str(self.out), '--propose-update', '--proposed-regions']
            if extra[0] == '--format':
                args.append(str(self.source))
            result = self.run_cli(*args, *extra)
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertEqual(json.loads(result.stderr)['code'], 'update_scope')
        for name, code in [('provenance.json', 'update_conflict'), (update.STATE, 'update_state')]:
            path = self.out / name
            saved = path.read_bytes()
            path.write_bytes(b'{}')
            result = self.proposal('--proposed-regions')
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertEqual(json.loads(result.stderr)['code'], code)
            path.write_bytes(saved)
        path = self.out / update.STATE
        saved = path.read_bytes()
        manifest = json.loads(saved)
        manifest['pages']['markdown/article-1.md'] = '0' * 64
        path.write_text(json.dumps(manifest))
        result = self.proposal('--proposed-regions')
        self.assertEqual((result.returncode, result.stdout), (2, ''))
        self.assertEqual(json.loads(result.stderr)['code'], 'update_state')
        path.write_bytes(saved)
        scoped = self.work / 'scoped'
        cli.publish(scoped, update.plan_update(scoped, core.parse_sources([('p', BEFORE)]), 'all').files)
        later = self.work / 'later.json'
        for data, code in [(b'{', 'invalid_json'), (BEFORE, 'annotation_conflict'),
                           (b'[{"id":2}]', 'incomplete_snapshot')]:
            later.write_bytes(data)
            result = self.run_cli('--inspect-managed', str(scoped), '--propose-update', '--proposed-regions',
                                  '--source', 'p', str(self.source), '--source', 'p', str(later))
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertEqual(json.loads(result.stderr)['code'], code)
        self.source.unlink()
        result = self.proposal('--proposed-regions')
        self.assertEqual((result.returncode, result.stdout), (3, ''))
        self.assertEqual(json.loads(result.stderr)['code'], 'io_error')

    @unittest.skipIf(os.name != 'posix' or os.geteuid() == 0, 'requires unprivileged POSIX file permissions')
    def test_actual_os_failures_after_preview_generation_still_suppress_stdout(self):
        (self.out / 'changes.json').unlink()
        for path in [self.source, self.out / 'provenance.json', self.out / 'report.json',
                     self.out / 'markdown/article-1.md', self.out / 'markdown']:
            with self.subTest(path=path):
                before = filesystem_state(self.work)
                permissions = stat.S_IMODE(path.stat().st_mode)
                path.chmod(0)
                try:
                    result = self.proposal('--proposed-regions', '--proposed-paths')
                    self.assertEqual((result.returncode, result.stdout), (3, ''))
                    self.assertEqual(json.loads(result.stderr)['code'], 'io_error')
                finally:
                    path.chmod(permissions)
                self.assertEqual(filesystem_state(self.work), before)


if __name__ == '__main__':
    unittest.main()
