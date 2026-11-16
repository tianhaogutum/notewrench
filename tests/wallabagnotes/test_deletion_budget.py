"""Opt-in complete-snapshot deletion budgets. SPDX-License-Identifier: AGPL-3.0-only."""
from __future__ import annotations

import contextlib
import io
import itertools
import json
import os
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


class DeletionBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wallabag-notes-budget-')
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.out = self.work / 'notes'
        self.source = self.work / 'input.json'
        self.source.write_bytes(BEFORE)

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'notewrench', 'wallabag-notes', *args], cwd=ROOT,
                              capture_output=True, text=True, timeout=120)

    def call(self, *extra):
        return self.run_cli(str(self.source), '-o', str(self.out), '--update', *extra)

    def initialize(self, export=None, fmt='all', **kwargs):
        plan = update.plan_update(self.out, export or core.parse(BEFORE), fmt, **kwargs)
        cli.publish(self.out, plan.files)
        return plan

    def budget_error(self, result, limit, deleted):
        self.assertEqual((result.returncode, result.stdout), (2, ''), result.stderr)
        self.assertEqual(json.loads(result.stderr), dict(
            status='error', code='deletion_budget_exceeded',
            message='Planned annotation deletions exceed the selected limit', path=str(self.out),
            max_deleted_annotations=limit, deleted_annotations=deleted))

    def test_prior_destructive_snapshot_accepted_then_opt_in_refuses(self):
        self.initialize()
        self.source.write_bytes(b'[]')
        accepted = self.call()
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(json.loads(accepted.stdout)['changes']['counts']['deleted'], 2)
        print('Confirmed unguarded complete snapshot [] accepted: 2 annotation deletions, exit 0.', flush=True)
        self.out = self.work / 'guarded'
        self.initialize()
        before = filesystem_state(self.work)
        for extra in ([], ['--dry-run']):
            self.budget_error(self.call('--max-deleted-annotations', '0', *extra), 0, 2)
            self.assertEqual(filesystem_state(self.work), before)

    def test_all_formats_scopes_exact_count_equality_retirement_reappearance_and_noop(self):
        # Two unique identities per account: duplicates/normalized IDs collapse;
        # the same ID in another article/account remains independent.
        data = json.dumps([
            dict(id=1, annotations=[dict(id=9, quote='q', text='', ranges=[]),
                                    dict(id='009', quote='q', text='', ranges=[])]),
            dict(id=2, annotations=[dict(id=9, quote='q', text='', ranges=[])])]).encode()
        for fmt, scoped in itertools.product(('all', 'markdown', 'logseq', 'csv'), (False, True)):
            with self.subTest(fmt=fmt, scoped=scoped):
                self.out = self.work / f'{fmt}-{scoped}'
                def parsed(raw):
                    return (core.parse_sources([('p', raw), ('empty', b'[]'), ('q', raw), ('p', raw)])
                            if scoped else core.parse(raw))
                export = parsed(data)
                self.initialize(export, fmt, max_deleted_annotations=0)
                for path in self.out.rglob('*.md'):
                    path.write_bytes(b'PERSONAL\xff\r\n' + path.read_bytes() + b'PERSONAL suffix\r\n')
                target = self.work / f'target-{fmt}-{scoped}'
                target.write_bytes(b'unrelated target')
                (self.out / 'untracked-link').symlink_to(target)
                before = filesystem_state(self.work)
                plain = update.plan_update(self.out, export, fmt)
                guarded = update.plan_update(self.out, export, fmt, max_deleted_annotations=0)
                self.assertEqual(guarded, plain)
                self.assertEqual(guarded.action, 'unchanged')
                update.publish_update(self.out, guarded)
                self.assertEqual(filesystem_state(self.work), before)
                removed = 4 if scoped else 2
                with self.assertRaises(core.BagnotesError) as caught:
                    update.plan_update(self.out, parsed(b'[]'), fmt, max_deleted_annotations=removed-1)
                self.assertEqual(caught.exception.diagnostic['deleted_annotations'], removed)
                self.assertEqual(filesystem_state(self.work), before)
                plan = update.plan_update(self.out, parsed(b'[]'), fmt, max_deleted_annotations=removed)
                self.assertEqual(plan, update.plan_update(self.out, parsed(b'[]'), fmt))
                self.assertEqual(plan.changes['counts']['deleted'], removed)
                update.publish_update(self.out, plan)
                retired_state = filesystem_state(self.work)
                self.assertEqual(update.plan_update(self.out, parsed(b'[]'), fmt,
                                                   max_deleted_annotations=0).action, 'unchanged')
                self.assertEqual(filesystem_state(self.work), retired_state)
                restored = update.plan_update(self.out, export, fmt, max_deleted_annotations=0)
                self.assertEqual(restored.changes['counts'], dict(added=removed, modified=0, deleted=0))
                update.publish_update(self.out, restored)
                for path in self.out.rglob('*.md'):
                    self.assertTrue(path.read_bytes().startswith(b'PERSONAL\xff\r\n'))
                    self.assertTrue(path.read_bytes().endswith(b'PERSONAL suffix\r\n'))
                self.assertEqual(update.plan_update(self.out, export, fmt,
                                                   max_deleted_annotations=0).action, 'unchanged')
        # Explicit entirely empty scope also initializes and replays under zero.
        self.out = self.work / 'empty-scope'
        export = core.parse_sources([('empty', b'[]'), ('empty', b'[]')])
        self.initialize(export, max_deleted_annotations=0)
        self.assertEqual(update.plan_update(self.out, export, 'all', max_deleted_annotations=0).action, 'unchanged')

    def test_anonymous_edit_counts_one_but_identified_raw_or_metadata_edits_count_zero(self):
        original = json.loads(BEFORE)
        original[0]['annotations'][1].pop('id')
        export = core.parse(json.dumps(original).encode())
        self.initialize(export)
        original[0]['title'] = 'new title'
        original[0]['annotations'][0].update(id='009', quote='new', opaque={'raw': None})
        modified = core.parse(json.dumps(original).encode())
        plan = update.plan_update(self.out, modified, 'all', max_deleted_annotations=0)
        self.assertEqual(plan.changes['counts'], dict(added=0, modified=1, deleted=0))
        original[0]['annotations'][1]['quote'] = 'anonymous edit'
        edited = core.parse(json.dumps(original).encode())
        with self.assertRaises(core.BagnotesError) as caught:
            update.plan_update(self.out, edited, 'all', max_deleted_annotations=0)
        self.assertEqual(caught.exception.diagnostic['deleted_annotations'], 1)
        plan = update.plan_update(self.out, edited, 'all', max_deleted_annotations=1)
        self.assertEqual(plan.changes['counts'], dict(added=1, modified=1, deleted=1))

    def test_cli_leading_zeros_equality_dry_run_and_nonpersistent_guard(self):
        self.assertEqual(self.call('--max-deleted-annotations', '00', '--dry-run').returncode, 0)
        self.assertFalse(self.out.exists())
        self.assertEqual(self.call('--max-deleted-annotations', '0').returncode, 0)
        self.source.write_bytes(AFTER)
        before = filesystem_state(self.work)
        self.budget_error(self.call('--max-deleted-annotations', '0'), 0, 1)
        self.assertEqual(filesystem_state(self.work), before)
        dry = self.call('--max-deleted-annotations', '01', '--dry-run')
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertEqual(filesystem_state(self.work), before)
        applied = self.call('--max-deleted-annotations', '1')
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(json.loads(dry.stdout)['changes'], json.loads(applied.stdout)['changes'])
        self.assertNotIn('max_deleted_annotations', applied.stdout)
        before = filesystem_state(self.work)
        self.assertEqual(json.loads(self.call('--max-deleted-annotations', '0').stdout)['status'], 'unchanged')
        self.assertEqual(filesystem_state(self.work), before)
        self.source.write_bytes(b'[]')
        self.assertEqual(self.call().returncode, 0)  # prior limit is not saved

    def test_invalid_cli_and_api_arguments_before_io(self):
        options = [[], ['--dry-run'], ['--inspect-conflicts', '--source', 'p', 'absent'],
                   ['--inspect-managed', 'absent'],
                   ['--inspect-managed', 'absent', '--propose-update', 'absent']]
        for extra in options:
            with self.subTest(extra=extra):
                with patch.object(Path, 'read_bytes', side_effect=AssertionError('input I/O')), \
                     patch.object(Path, 'exists', side_effect=AssertionError('output I/O')), \
                     contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()) as out:
                    with self.assertRaises(SystemExit) as caught:
                        cli.main(['--max-deleted-annotations', '0', *extra])
                    self.assertEqual(caught.exception.code, 2)
                    self.assertEqual(out.getvalue(), '')
        for bad in ('-1', '+1', '1.0', '1e2', '', ' 1', '1 ', '١', '１', 'NaN'):
            with self.subTest(bad=bad), patch.object(Path, 'read_bytes', side_effect=AssertionError('I/O')), \
                 contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()) as out:
                with self.assertRaises(SystemExit) as caught:
                    cli.main(['absent', '-o', 'absent', '--update', '--max-deleted-annotations='+bad])
                self.assertEqual(caught.exception.code, 2)
                self.assertEqual(out.getvalue(), '')
        for bad in (-1, True, False, 1.0, '0', [], {}):
            with self.subTest(bad=bad), patch('notewrench.wallabagnotes.update.render', side_effect=AssertionError('render')), \
                 patch.object(Path, 'exists', side_effect=AssertionError('I/O')):
                with self.assertRaises(core.BagnotesError) as caught:
                    update.plan_update(self.out, None, 'all', max_deleted_annotations=bad)
                self.assertEqual(caught.exception.diagnostic['code'], 'invalid_deletion_budget')

    def test_existing_validation_precedes_budget_and_refusal_preserves_everything(self):
        self.initialize()
        cases = [('input.json', b'{', 'invalid_json'),
                 ('input.json', b'[{"id":1}]', 'incomplete_snapshot'),
                 ('notes/update-state.json', b'{}', 'update_state'),
                 ('notes/provenance.json', b'{}', 'update_conflict'),
                 ('notes/report.json', b'edited', 'update_conflict'),
                 ('notes/markdown/article-1.md', b'edited', 'update_conflict')]
        self.source.write_bytes(b'[]')
        for name, data, code in cases:
            path = self.work / name
            old = path.read_bytes()
            path.write_bytes(data)
            before = filesystem_state(self.work)
            for extra in ([], ['--dry-run']):
                result = self.call('--max-deleted-annotations', '0', *extra)
                self.assertEqual((result.returncode, result.stdout), (2, ''), result.stderr)
                self.assertEqual(json.loads(result.stderr)['code'], code)
                self.assertEqual(filesystem_state(self.work), before)
            path.write_bytes(old)
        before = filesystem_state(self.work)
        result = self.call('--format', 'csv', '--max-deleted-annotations', '0')
        self.assertEqual(json.loads(result.stderr)['code'], 'update_scope')
        self.assertEqual(filesystem_state(self.work), before)
        # Linked tracked paths, missing paths and new-path collisions still win.
        page = self.out / 'markdown/article-1.md'
        original = page.read_bytes()
        page.unlink()
        target = self.work / 'target'
        target.write_bytes(original)
        for linked in (False, True):
            if linked:
                page.symlink_to(target)
            before = filesystem_state(self.work)
            result = self.call('--max-deleted-annotations', '0')
            self.assertEqual(json.loads(result.stderr)['code'], 'update_conflict')
            self.assertEqual(filesystem_state(self.work), before)
        page.unlink()
        page.write_bytes(original)
        next_data = json.loads(BEFORE)
        next_data[0]['id'] = 2
        self.source.write_text(json.dumps(next_data))
        (self.out / 'markdown/article-2.md').symlink_to(target)
        before = filesystem_state(self.work)
        result = self.call('--max-deleted-annotations', '0')
        self.assertEqual(json.loads(result.stderr)['code'], 'update_conflict')
        self.assertEqual(filesystem_state(self.work), before)

    @unittest.skipIf(os.name != 'posix' or os.geteuid() == 0, 'requires unprivileged POSIX permissions')
    def test_actual_os_errors_still_exit_three_without_stdout_or_writes(self):
        self.initialize()
        self.source.write_bytes(b'[]')
        for path in [self.source, self.out / update.STATE, self.out / 'provenance.json',
                     self.out / 'report.json', self.out / 'markdown/article-1.md']:
            with self.subTest(path=path):
                path.chmod(0)
                # Exclude unreadable file contents from our own snapshot helper.
                listing = [(p, p.lstat().st_mtime_ns) for p in self.work.rglob('*')]
                try:
                    for extra in ([], ['--dry-run']):
                        result = self.call('--max-deleted-annotations', '0', *extra)
                        self.assertEqual((result.returncode, result.stdout), (3, ''), result.stderr)
                        self.assertEqual(json.loads(result.stderr)['code'], 'io_error')
                    self.assertEqual([(p, p.lstat().st_mtime_ns) for p in self.work.rglob('*')], listing)
                finally:
                    path.chmod(0o644)
        self.source.write_bytes(AFTER)
        before = filesystem_state(self.out)
        self.work.chmod(0o555)  # actual staging creation failure with equal permitted budget
        try:
            result = self.call('--max-deleted-annotations', '1')
            self.assertEqual((result.returncode, result.stdout), (3, ''), result.stderr)
            self.assertEqual(json.loads(result.stderr)['code'], 'io_error')
            self.assertEqual(filesystem_state(self.out), before)
        finally:
            self.work.chmod(0o755)


if __name__ == '__main__':
    unittest.main()
