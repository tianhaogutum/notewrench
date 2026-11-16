# SPDX-License-Identifier: AGPL-3.0-only
"""Complete-snapshot update contracts with synthetic Wallabag v2 exports."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BEGIN = b'<!-- bagnotes:managed:start -->\n'
END = b'<!-- bagnotes:managed:end -->\n'


def snapshot(*annotations, article_id=1):
    return json.dumps([dict(id=article_id, title='Reading', url='https://example.org',
                            annotations=[dict(id=i, quote=q, text=t, ranges=[]) for i, q, t in annotations])],
                      ensure_ascii=False).encode()


BEFORE = snapshot((9, 'original', 'old comment'), (10, 'remove me', 'deleted comment'))
AFTER = snapshot((9, 'updated 中文', 'new comment'), (11, 'added', 'new'))


def tree(directory):
    return {str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob('*') if p.is_file()}


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wallabag-notes-updates-')
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.source = self.work / 'input.json'
        self.source.write_bytes(BEFORE)
        self.out = self.work / 'notes'
        self.prefix = [sys.executable, '-m', 'notewrench', 'wallabag-notes']
        self.cwd = ROOT

    def run_cli(self, *args, data=None, expected=0):
        if data is not None:
            self.source.write_bytes(data)
        result = subprocess.run([*self.prefix, str(self.source), '-o', str(self.out), '--update', *args],
                                cwd=self.cwd, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, expected, result.stderr)
        return json.loads(result.stdout if expected == 0 else result.stderr)

    def test_relative_update_diff_notes_and_repeat_no_writes(self):
        self.cwd = self.work
        self.run_cli()
        prefixes, suffixes = {}, {}
        for kind in ('markdown', 'logseq'):
            page = self.out / kind / 'article-1.md'
            original = page.read_bytes()
            self.assertIn(BEGIN, original)
            self.assertIn(END, original)
            prefixes[kind] = b'My preface\r\n\xe6\x89\x8b\xe5\x86\x99\n'
            suffixes[kind] = b'\r\n- My notes: [[link]]\r\n  exact spaces  \n\xf0\x9f\x8c\xb1'
            page.write_bytes(prefixes[kind] + original + suffixes[kind])
        before = tree(self.out)
        planned = self.run_cli('--dry-run', data=AFTER)
        self.assertEqual(tree(self.out), before)
        self.assertEqual(planned['changes']['counts'], dict(added=1, modified=1, deleted=1))
        actual = self.run_cli()
        self.assertEqual(actual['changes'], planned['changes'])
        changes = json.loads((self.out / 'changes.json').read_bytes())
        self.assertEqual(changes['counts'], dict(added=1, modified=1, deleted=1))
        self.assertEqual(changes['modified'][0]['before']['quote'], 'original')
        self.assertEqual(changes['modified'][0]['after']['quote'], 'updated 中文')
        self.assertEqual(changes['modified'][0]['differing_fields'], ['quote', 'text'])
        self.assertEqual(changes['deleted'][0]['before']['text'], 'deleted comment')
        for kind in ('markdown', 'logseq'):
            value = (self.out / kind / 'article-1.md').read_bytes()
            self.assertTrue(value.startswith(prefixes[kind]))
            self.assertTrue(value.endswith(suffixes[kind]))
            self.assertNotIn(b'remove me', value)
            self.assertEqual(value.count(b'Annotation 9'), 1)
            self.assertEqual(value.count(b'Annotation 11'), 1)
        before = tree(self.out)
        mtimes = {p: p.stat().st_mtime_ns for p in self.out.rglob('*') if p.is_file()}
        repeated = self.run_cli()
        self.assertEqual(repeated['status'], 'unchanged')
        self.assertEqual(repeated['changes']['counts'], dict(added=0, modified=0, deleted=0))
        self.assertEqual(tree(self.out), before)
        self.assertEqual({p: p.stat().st_mtime_ns for p in mtimes}, mtimes)

    def test_managed_edits_marker_damage_and_sidecar_edits_reject_whole_update(self):
        for target, mutate in [
            ('markdown/article-1.md', lambda b: b.replace(b'original', b'hand edit')),
            ('logseq/article-1.md', lambda b: b.replace(END, b'')),
            ('markdown/article-1.md', lambda b: b + BEGIN),
            ('logseq/article-1.md', lambda b: END + b.replace(END, b'')),
            ('provenance.json', lambda b: b + b' '),
            ('annotations.csv', lambda b: b.replace(b'original', b'hand edit')),
            ('changes.json', lambda b: b + b' '),
        ]:
            with self.subTest(target=target), tempfile.TemporaryDirectory(dir=self.work) as folder:
                self.out = Path(folder) / 'notes'
                self.run_cli(data=BEFORE)
                path = self.out / target
                path.write_bytes(mutate(path.read_bytes()))
                before = tree(self.out)
                for dry in ([], ['--dry-run']):
                    failure = self.run_cli(*dry, data=AFTER, expected=2)
                    self.assertEqual(failure['code'], 'update_conflict')
                    self.assertEqual(tree(self.out), before)

    def test_removed_article_keeps_page_and_notes_and_reappears_once(self):
        self.run_cli()
        page = self.out / 'markdown/article-1.md'
        page.write_bytes(page.read_bytes() + b'PERSONAL\r\n')
        result = self.run_cli(data=b'[]')
        self.assertEqual(result['changes']['counts']['deleted'], 2)
        self.assertIn(b'absent from current snapshot', page.read_bytes())
        self.assertTrue(page.read_bytes().endswith(b'PERSONAL\r\n'))
        self.run_cli(data=AFTER)
        self.assertEqual(len(list((self.out / 'markdown').glob('*.md'))), 1)
        self.assertEqual(page.read_bytes().count(b'Annotation 9'), 1)
        self.assertTrue(page.read_bytes().endswith(b'PERSONAL\r\n'))

    def test_legacy_output_and_scope_or_format_switch_are_not_adopted(self):
        self.out.mkdir()
        (self.out / 'keep').write_bytes(b'unchanged')
        failure = self.run_cli(expected=2)
        self.assertEqual(failure['code'], 'update_state')
        self.assertEqual(tree(self.out), {'keep': b'unchanged'})
        self.out = self.work / 'managed'
        self.run_cli()
        before = tree(self.out)
        self.assertEqual(self.run_cli('--format', 'markdown', expected=2)['code'], 'update_scope')
        result = subprocess.run([*self.prefix, '--source', 'personal', str(self.source), '-o', str(self.out), '--update'],
                                cwd=self.cwd, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr)['code'], 'update_scope')
        self.assertEqual(tree(self.out), before)

    def test_missing_annotations_does_not_infer_deletion(self):
        self.run_cli()
        before = tree(self.out)
        self.assertEqual(self.run_cli(data=b'[{"id":1}]', expected=2)['code'], 'incomplete_snapshot')
        self.assertEqual(tree(self.out), before)

    def test_new_page_collision_and_symlink_rejected_before_writes(self):
        self.run_cli()
        foreign = self.out / 'markdown/article-2.md'
        foreign.write_bytes(b'Unmanaged handwritten page')
        before = tree(self.out)
        self.assertEqual(self.run_cli(data=snapshot((20, 'new', ''), article_id=2), expected=2)['code'], 'update_conflict')
        self.assertEqual(tree(self.out), before)
        foreign.unlink()
        page = self.out / 'markdown/article-1.md'
        external = self.work / 'external.md'
        page.rename(external)
        page.symlink_to(external)
        before = tree(self.out)
        self.assertEqual(self.run_cli(data=AFTER, expected=2)['code'], 'update_conflict')
        self.assertEqual(tree(self.out), before)

    def test_all_formats_support_empty_initial_snapshot(self):
        for fmt in ('all', 'markdown', 'logseq', 'csv'):
            with self.subTest(fmt=fmt):
                self.out = self.work / fmt
                self.run_cli('--format', fmt, data=b'[]')
                result = self.run_cli('--format', fmt, data=AFTER)
                self.assertEqual(result['changes']['counts']['added'], 2)
                self.assertEqual(self.run_cli('--format', fmt)['status'], 'unchanged')

    def test_namespaces_retained_and_normalized_ids_not_modified(self):
        def run(data, labels=('personal', 'work')):
            self.source.write_bytes(data)
            args = [item for label in labels for item in ('--source', label, str(self.source))]
            return subprocess.run([*self.prefix, *args, '-o', str(self.out), '--update'],
                                  cwd=self.cwd, capture_output=True, text=True, timeout=120)
        self.assertEqual(run(BEFORE).returncode, 0)
        updated = run(AFTER)
        self.assertEqual(updated.returncode, 0, updated.stderr)
        self.assertEqual(json.loads(updated.stdout)['changes']['counts'], dict(added=2, modified=2, deleted=2))
        before = tree(self.out)
        self.assertEqual(run(AFTER, ('personal',)).returncode, 2)
        self.assertEqual(tree(self.out), before)
        normalized = json.loads(AFTER)
        normalized[0]['id'] = '001'
        normalized[0]['annotations'][0]['id'] = '009'
        result = run(json.dumps(normalized).encode())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['changes']['counts'], dict(added=0, modified=0, deleted=0))

    def test_anonymous_edit_is_reported_as_delete_and_add_and_numbers_survive(self):
        original = b'[{"id":1,"annotations":[{"quote":"old","text":"","ranges":[{"n":0.1234567890123456789012345}]}]}]'
        self.run_cli(data=original)
        result = self.run_cli(data=original.replace(b'old', b'new'))
        self.assertEqual(result['changes']['counts'], dict(added=1, modified=0, deleted=1))
        self.assertIn(b'0.1234567890123456789012345', (self.out / 'changes.json').read_bytes())

    def test_damaged_state_or_missing_page_rejected_and_new_dry_run_writes_nothing(self):
        before = tree(self.work)
        self.run_cli('--dry-run')
        self.assertEqual(tree(self.work), before)
        self.run_cli()
        page = self.out / 'markdown/article-1.md'
        content = page.read_bytes()
        page.unlink()
        self.assertEqual(self.run_cli(data=AFTER, expected=2)['code'], 'update_conflict')
        page.write_bytes(content)
        state = self.out / 'update-state.json'
        original = state.read_bytes()
        for malformed in (b'{}', b'{', original.replace(b'markdown/article-1.md', b'../../escape.md')):
            state.write_bytes(malformed)
            before = tree(self.out)
            self.assertEqual(self.run_cli(expected=2)['code'], 'update_state')
            self.assertEqual(tree(self.out), before)

    def test_edits_after_planning_are_rejected_without_any_publication(self):
        from notewrench.wallabagnotes.core import parse
        from notewrench.wallabagnotes.update import plan_update, publish_update
        from notewrench.wallabagnotes.core import BagnotesError
        self.run_cli()
        plan = plan_update(self.out, parse(AFTER), 'all')
        page = self.out / 'markdown/article-1.md'
        page.write_bytes(page.read_bytes() + b'new personal note')
        before = tree(self.out)
        with self.assertRaises(BagnotesError) as caught:
            publish_update(self.out, plan)
        self.assertEqual(caught.exception.diagnostic['code'], 'update_conflict')
        self.assertEqual(tree(self.out), before)
        self.assertEqual(list(self.work.glob('.bagnotes-update-*')), [])

    def test_ordinary_replacement_failure_rolls_back_existing_bundle(self):
        self.run_cli()
        from notewrench.wallabagnotes import cli, update
        before = tree(self.out)
        original = update.os.replace
        calls = 0

        def fail_once(source, destination):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise OSError('synthetic replace failure')
            return original(source, destination)

        self.source.write_bytes(AFTER)
        with contextlib.redirect_stderr(io.StringIO()), patch.object(update.os, 'replace', fail_once):
            self.assertEqual(cli.main([str(self.source), '-o', str(self.out), '--update']), 3)
        self.assertGreaterEqual(calls, 3)
        self.assertEqual(tree(self.out), before)


if __name__ == '__main__':
    unittest.main()
