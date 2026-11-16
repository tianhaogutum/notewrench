"""Read-only next-snapshot inspection. SPDX-License-Identifier: AGPL-3.0-only."""
from __future__ import annotations

import base64
import contextlib
import io
import os
import stat
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from notewrench.wallabagnotes import cli, core, update
from .test_managed_conflicts import filesystem_state

ROOT = Path(__file__).resolve().parents[2]
BEFORE = (ROOT / 'examples/wallabag-notes/updates/before.json').read_bytes()
AFTER = (ROOT / 'examples/wallabag-notes/updates/after.json').read_bytes()


class ProposedUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wallabag-notes-proposed-')
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.out = self.work / 'notes'
        self.source = self.work / 'next.json'
        self.source.write_bytes(AFTER)
        cli.publish(self.out, update.plan_update(self.out, core.parse(BEFORE), 'all').files)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'notewrench', 'wallabag-notes', *args], cwd=ROOT,
                              capture_output=True, text=True, timeout=120)

    def proposal(self, *extra):
        return self.run_cli('--inspect-managed', str(self.out), '--propose-update',
                            str(self.source), *extra)

    def test_proposed_changes_and_all_local_conflicts_use_saved_baseline_read_only(self):
        next_data = json.loads(AFTER)
        next_data[0]['title'] = 'Next title 中文'
        self.source.write_text(json.dumps(next_data))
        originals = {}
        for kind in ('markdown', 'logseq'):
            page = self.out / f'{kind}/article-1.md'
            originals[str(page.relative_to(self.out))] = update.region(page.read_bytes(), str(page))[1]
            page.write_bytes(b'PRIVATE prefix\xff\r\n' + page.read_bytes().replace(b'original', b'local edit') +
                             b'PRIVATE suffix')
        (self.out / 'report.json').write_bytes(b'local report edit')
        (self.out / 'changes.json').unlink()
        target = self.work / 'private.csv'
        target.write_bytes(b'PRIVATE link target')
        (self.out / 'annotations.csv').unlink()
        (self.out / 'annotations.csv').symlink_to(target)
        before = filesystem_state(self.work)
        result = self.proposal()
        self.assertEqual((result.returncode, result.stderr), (2, ''))
        report = json.loads(result.stdout)
        self.assertEqual((report['schema_version'], report['tool'], report['status']),
                         (1, 'bagnotes-managed-conflicts', 'conflicts'))
        self.assertEqual([c['path'] for c in report['artifact_conflicts']],
                         ['annotations.csv', 'changes.json', 'report.json'])
        self.assertEqual([c['path'] for c in report['conflicts']], sorted(originals))
        for item in report['conflicts']:
            self.assertEqual(base64.b64decode(item['expected']['base64']), originals[item['path']])
            self.assertIn('local edit', item['unified_diff'])
            self.assertNotIn('Next title', item['unified_diff'])
        proposed = report['proposed_update']
        self.assertEqual(proposed['format'], 'all')
        self.assertIsNone(proposed['namespaces'])
        self.assertEqual(proposed['snapshot'], dict(schema_version=1, input_sha256=update.sha(self.source.read_bytes())))
        changes = proposed['changes']
        self.assertEqual(changes['counts'], dict(added=1, modified=1, deleted=1))
        self.assertEqual(changes['articles'][0]['differing_fields'], ['title'])
        self.assertEqual(changes['articles'][0]['after']['title'], 'Next title 中文')
        self.assertEqual(changes['modified'][0]['identity'], 'id:9')
        self.assertEqual(changes['modified'][0]['before']['quote'], 'original')
        self.assertEqual(changes['modified'][0]['after']['quote'], 'updated 中文')
        baseline = json.loads((self.out / 'provenance.json').read_bytes())['articles'][0]['annotations'][0]
        for name in ('anchor', 'block_id'):
            self.assertEqual(changes['modified'][0][name], baseline[name])
        local = self.run_cli('--inspect-managed', str(self.out))
        self.assertEqual({k: v for k, v in report.items() if k != 'proposed_update'}, json.loads(local.stdout))
        self.assertNotIn('PRIVATE', result.stdout)
        repeated = self.proposal()
        self.assertEqual((repeated.returncode, repeated.stdout, repeated.stderr), (2, result.stdout, ''))
        self.assertEqual(filesystem_state(self.work), before)

    def test_clean_changes_noop_and_empty_snapshot_have_local_exit_zero(self):
        for data, counts in [(AFTER, dict(added=1, modified=1, deleted=1)),
                             (BEFORE, dict(added=0, modified=0, deleted=0)),
                             (b'[]', dict(added=0, modified=0, deleted=2))]:
            with self.subTest(data=data):
                self.source.write_bytes(data)
                before = filesystem_state(self.work)
                result = self.proposal()
                self.assertEqual((result.returncode, result.stderr), (0, ''))
                report = json.loads(result.stdout)
                self.assertEqual(report['status'], 'clean')
                self.assertEqual(report['proposed_update']['changes']['counts'], counts)
                self.assertEqual(report, update.inspect_managed(self.out, next_export=core.parse(data)))
                self.assertEqual(filesystem_state(self.work), before)
        # The inspection must never call the publication or update-plan path.
        with patch('notewrench.wallabagnotes.cli.publish', side_effect=AssertionError('published')), \
             patch('notewrench.wallabagnotes.update.publish_update', side_effect=AssertionError('updated')), \
             patch('notewrench.wallabagnotes.update.plan_update', side_effect=AssertionError('planned')), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['--inspect-managed', str(self.out), '--propose-update', str(self.source)]), 0)

    def test_changes_keep_saved_order_then_new_identities_and_normalize_id_spelling(self):
        def annotation(identity, text):
            return dict(id=identity, quote=text, text='', ranges=[])
        def article(identity, title, annotations):
            return dict(id=identity, title=title, annotations=annotations)
        old = [article(3, 'removed', [annotation(5, 'deleted article')]),
               article(1, 'old', [annotation(9, 'deleted annotation'), annotation(2, 'old quote')])]
        new = [article(4, 'new', [annotation(7, 'new article')]),
               article('001', 'next', [annotation(8, 'new annotation'), annotation('002', 'modified')])]
        out = self.work / 'ordered'
        cli.publish(out, update.plan_update(out, core.parse(json.dumps(old).encode()), 'all').files)
        before = filesystem_state(self.work)
        report = update.inspect_managed(out, next_export=core.parse(json.dumps(new).encode()))
        changes = report['proposed_update']['changes']
        self.assertEqual([(a['source_id'], a['change']) for a in changes['articles']],
                         [('3', 'deleted'), ('1', 'modified'), ('4', 'added')])
        self.assertEqual(changes['articles'][1]['differing_fields'], ['title'])
        self.assertEqual([(a['source_id'], a['identity']) for a in changes['added']], [('1', 'id:8'), ('4', 'id:7')])
        self.assertEqual([(a['source_id'], a['identity']) for a in changes['deleted']], [('3', 'id:5'), ('1', 'id:9')])
        self.assertEqual(changes['modified'][0]['differing_fields'], ['quote'])
        self.assertEqual(filesystem_state(self.work), before)

    def test_namespaced_multiple_inputs_all_formats_empty_scope_and_retired_page_reappearance(self):
        for fmt in ('all', 'markdown', 'logseq', 'csv'):
            with self.subTest(fmt=fmt):
                out = self.work / fmt
                old = core.parse_sources([('p', BEFORE), ('empty', b'[]')])
                cli.publish(out, update.plan_update(out, old, fmt).files)
                empty = self.work / 'empty.json'
                empty.write_bytes(b'[]')
                args = ['--inspect-managed', str(out), '--propose-update', '--format', fmt,
                        '--source', 'empty', str(empty), '--source', 'p', str(self.source),
                        '--source', 'p', str(self.source)]
                before = filesystem_state(self.work)
                result = self.run_cli(*args)
                self.assertEqual((result.returncode, result.stderr), (0, ''))
                report = json.loads(result.stdout)['proposed_update']
                self.assertEqual(report['namespaces'], ['empty', 'p'])
                self.assertEqual(report['format'], fmt)
                self.assertEqual(report['changes']['counts'], dict(added=1, modified=1, deleted=1))
                descriptors = report['snapshot']['inputs']
                self.assertEqual([(i['namespace'], i['input_order'], i['sha256']) for i in descriptors],
                                 [('empty', 0, update.sha(b'[]')), ('p', 1, update.sha(AFTER)), ('p', 2, update.sha(AFTER))])
                self.assertEqual(report['snapshot']['schema_version'], 2)
                baseline = json.loads((out / 'provenance.json').read_bytes())['articles'][0]['annotations'][0]
                for field in ('anchor', 'block_id'):
                    self.assertEqual(report['changes']['modified'][0][field], baseline[field])
                self.assertEqual(filesystem_state(self.work), before)
                # Explicit empties preserve scope; retired pages are compared to their
                # saved retirement baseline when proposing an article's return.
                update.publish_update(out, update.plan_update(out, core.parse_sources([('p', b'[]'), ('empty', b'[]')]), fmt))
                before = filesystem_state(self.work)
                proposed = update.inspect_managed(out, next_export=old, format_name=fmt)
                self.assertEqual(proposed['status'], 'clean')
                self.assertEqual(proposed['proposed_update']['changes']['counts'], dict(added=2, modified=0, deleted=0))
                self.assertEqual(proposed['proposed_update']['changes']['articles'],
                                 [dict(namespace='p', source_id='1', change='added')])
                self.assertEqual(filesystem_state(self.work), before)

    def test_anonymous_edit_exact_numbers_unknown_metadata_and_omitted_content(self):
        raw = b'[{"id":1,"content":"old html","extra":null,"annotations":[{"quote":"old","text":"","ranges":[{"n":0.1234567890123456789012345}]}]}]'
        out = self.work / 'anonymous'
        cli.publish(out, update.plan_update(out, core.parse(raw), 'csv').files)
        self.source.write_bytes(raw.replace(b'old', b'next').replace(b'null', b'[]'))
        before = filesystem_state(self.work)
        result = self.run_cli('--inspect-managed', str(out), '--propose-update', str(self.source), '--format', 'csv')
        self.assertEqual((result.returncode, result.stderr), (0, ''))
        proposed = json.loads(result.stdout)['proposed_update']
        self.assertEqual(proposed['changes']['counts'], dict(added=1, modified=0, deleted=1))
        self.assertEqual(proposed['changes']['articles'][0]['differing_fields'], ['extra'])
        self.assertTrue(proposed['changes']['added'][0]['identity'].startswith('content-sha256:'))
        self.assertIn('missing_annotation_id', [w['code'] for w in proposed['warnings']])
        self.assertIn('0.1234567890123456789012345', result.stdout)
        self.assertNotIn('html', result.stdout)
        self.assertEqual(filesystem_state(self.work), before)

    def test_bad_next_inputs_scope_and_baselines_fail_without_partial_stdout(self):
        (self.out / 'report.json').write_bytes(b'edited')
        for page in self.out.rglob('*.md'):
            page.write_bytes(page.read_bytes().replace(b'original', b'edited'))
        for data, code in [(b'{', 'invalid_json'), (b'[{}]', 'invalid_id'),
                           (b'[{"id":1}]', 'incomplete_snapshot'),
                           (b'[{"id":1,"annotations":[{"text":"","ranges":[]}]}]', 'missing_quote')]:
            self.source.write_bytes(data)
            before = filesystem_state(self.work)
            result = self.proposal()
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertEqual(json.loads(result.stderr)['code'], code)
            self.assertEqual(filesystem_state(self.work), before)
        self.source.write_bytes(AFTER)
        for args in [('--format', 'csv'), ()]:
            if args:
                result = self.proposal(*args)
            else:
                result = self.run_cli('--inspect-managed', str(self.out), '--propose-update',
                                      '--source', 'p', str(self.source))
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertEqual(json.loads(result.stderr)['code'], 'update_scope')
        alias = self.work / 'alias'
        alias.symlink_to(self.out, target_is_directory=True)
        result = self.run_cli('--inspect-managed', str(alias), '--propose-update', str(self.source))
        self.assertEqual((result.returncode, result.stdout), (2, ''))
        self.assertEqual(json.loads(result.stderr)['code'], 'update_state')
        for name, data in [('provenance.json', b'{}'), (update.STATE, b'{')]:
            path = self.out / name
            original = path.read_bytes()
            path.write_bytes(data)
            before = filesystem_state(self.work)
            result = self.proposal()
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertIn(json.loads(result.stderr)['code'], ('update_conflict', 'update_state'))
            self.assertEqual(filesystem_state(self.work), before)
            path.write_bytes(original)
        manifest = self.out / update.STATE
        saved_state = manifest.read_bytes()
        data = json.loads(manifest.read_bytes())
        data['pages']['markdown/article-1.md'] = '0' * 64
        manifest.write_text(json.dumps(data))
        result = self.proposal()
        self.assertEqual((result.returncode, result.stdout), (2, ''))
        self.assertEqual(json.loads(result.stderr)['code'], 'update_state')
        # Even if page reconstruction succeeds, unusable saved comparison records
        # must fail as a baseline error, without a traceback or partial report.
        manifest.write_bytes(saved_state)
        provenance = self.out / 'provenance.json'
        broken = json.loads(provenance.read_bytes())
        del broken['articles'][0]['annotations'][0]['identity']
        provenance.write_text(json.dumps(broken))
        data = json.loads(saved_state)
        data['artifacts']['provenance.json'] = update.sha(provenance.read_bytes())
        manifest.write_text(json.dumps(data))
        before = filesystem_state(self.work)
        result = self.proposal()
        self.assertEqual((result.returncode, result.stdout), (2, ''))
        self.assertEqual(json.loads(result.stderr)['code'], 'update_state')
        self.assertEqual(filesystem_state(self.work), before)

    def test_later_namespaced_input_conflict_absence_bad_json_or_io_never_reports_partially(self):
        out = self.work / 'scoped'
        cli.publish(out, update.plan_update(out, core.parse_sources([('p', BEFORE)]), 'all').files)
        (out / 'report.json').unlink()
        later = self.work / 'later.json'
        args = ['--inspect-managed', str(out), '--propose-update', '--source', 'p', str(self.source),
                '--source', 'p', str(later)]
        for data, code in [(b'{', 'invalid_json'), (BEFORE, 'annotation_conflict'),
                           (b'[{"id":2}]', 'incomplete_snapshot')]:
            later.write_bytes(data)
            before = filesystem_state(self.work)
            result = self.run_cli(*args)
            self.assertEqual((result.returncode, result.stdout), (2, ''))
            self.assertEqual(json.loads(result.stderr)['code'], code)
            self.assertEqual(filesystem_state(self.work), before)
        later.unlink()
        for path in (later, self.work):
            result = self.run_cli(*args[:-1], str(path))
            self.assertEqual((result.returncode, result.stdout), (3, ''))
            self.assertEqual(json.loads(result.stderr)['code'], 'io_error')
        empty = self.work / 'empty.json'
        empty.write_bytes(b'[]')
        result = self.run_cli('--inspect-managed', str(out), '--propose-update', '--source', 'p', str(self.source),
                              '--source', 'extra', str(empty))
        self.assertEqual((result.returncode, result.stdout), (2, ''))
        self.assertEqual(json.loads(result.stderr)['code'], 'update_scope')

    @unittest.skipIf(os.name != 'posix' or os.geteuid() == 0, 'requires unprivileged POSIX file permissions')
    def test_real_os_failures_in_next_input_baseline_artifacts_pages_and_metadata_exit_three(self):
        (self.out / 'changes.json').unlink()
        (self.out / 'logseq/article-1.md').write_bytes(b'broken markers')
        paths = [self.source, self.out / 'provenance.json', self.out / update.STATE,
                 self.out / 'report.json', self.out / 'markdown/article-1.md', self.out / 'markdown']
        for path in paths:
            with self.subTest(path=path):
                before = filesystem_state(self.work)
                permissions = stat.S_IMODE(path.stat().st_mode)
                path.chmod(0)
                try:
                    result = self.proposal()
                    self.assertEqual((result.returncode, result.stdout), (3, ''))
                    self.assertEqual(json.loads(result.stderr)['code'], 'io_error')
                finally:
                    path.chmod(permissions)
                self.assertEqual(filesystem_state(self.work), before)

    def test_proposal_usage_contract_rejected_before_io(self):
        base = ['--inspect-managed', str(self.out), '--propose-update']
        for args in [base, ['--propose-update', str(self.source), '-o', 'no'],
                     [*base, str(self.source), '--source', 'p', str(self.source)],
                     *[[*base, str(self.source), *extra] for extra in
                       (['--dry-run'], ['--update'], ['-o', 'no'], ['--inspect-conflicts'], ['--conflict-values'])]]:
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual((result.returncode, result.stdout), (2, ''))
                self.assertIn('usage:', result.stderr)


if __name__ == '__main__':
    unittest.main()
