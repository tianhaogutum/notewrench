"""Read-only inspection of tracked managed pages. SPDX-License-Identifier: AGPL-3.0-only."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import json
import os
import stat
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from notewrench.wallabagnotes import cli, core, update

ROOT = Path(__file__).resolve().parents[2]
BEFORE = (ROOT / 'examples/wallabag-notes/updates/before.json').read_bytes()
AFTER = (ROOT / 'examples/wallabag-notes/updates/after.json').read_bytes()


def state(directory):
    return {str(p.relative_to(directory)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in directory.rglob('*') if p.is_file() and not p.is_symlink()}


def filesystem_state(directory):
    """Include directory/link mtimes and targets without traversing any link."""
    result = {}
    for folder, dirs, files in os.walk(directory, followlinks=False):
        for path in [Path(folder), *(Path(folder) / n for n in dirs + files)]:
            info = path.lstat()
            data = (path.readlink().as_posix() if stat.S_ISLNK(info.st_mode) else
                    path.read_bytes() if stat.S_ISREG(info.st_mode) else None)
            result[str(path.relative_to(directory))] = (info.st_mode, data, info.st_mtime_ns)
    return result


class ManagedConflictTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='wallabag-notes-managed-inspection-')
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.out = self.work / 'notes'
        cli.publish(self.out, update.plan_update(self.out, core.parse(BEFORE), 'all').files)

    def run_cli(self, args):
        return subprocess.run([sys.executable, '-m', 'notewrench', 'wallabag-notes', *args], cwd=ROOT,
                              capture_output=True, text=True, timeout=120)

    def test_cli_lists_every_edited_region_with_previous_baseline_diff_and_no_writes(self):
        originals, currents = {}, {}
        for kind in ('markdown', 'logseq'):
            name = f'{kind}/article-1.md'
            page = self.out / name
            originals[name] = update.region(page.read_bytes(), name)[1]
            # Non-UTF8 personal bytes must not appear in a managed-region diff.
            current = originals[name].replace(b'original', 'hand edit 中文 🌱'.encode())
            currents[name] = current
            page.write_bytes(b'PRIVATE PREFIX\xff\r\n' + current + b'PRIVATE SUFFIX\r\n  ')
        before = state(self.work)
        inspected = self.run_cli(['--inspect-managed', str(self.out)])
        self.assertEqual(inspected.returncode, 2, inspected.stderr)
        self.assertEqual(inspected.stderr, '')
        report = json.loads(inspected.stdout)
        self.assertEqual(report['schema_version'], 1)
        self.assertEqual(report['tool'], 'bagnotes-managed-conflicts')
        self.assertEqual(report['status'], 'conflicts')
        self.assertEqual(report['output'], str(self.out))
        self.assertEqual(report['checked_pages'], 2)
        self.assertEqual([c['path'] for c in report['conflicts']], sorted(originals))
        for item in report['conflicts']:
            name = item['path']
            self.assertEqual(item['code'], 'managed_edit')
            self.assertEqual(item['comparison'], 'managed-region')
            for side, data in [('expected', originals[name]), ('current', currents[name])]:
                self.assertEqual(base64.b64decode(item[side]['base64']), data)
                self.assertEqual(item[side]['sha256'], hashlib.sha256(data).hexdigest())
                self.assertEqual(item[side]['bytes'], len(data))
            self.assertIn(f'--- {name}:expected\n', item['unified_diff'])
            self.assertIn(f'+++ {name}:current\n', item['unified_diff'])
            self.assertIn('-' + ('> original' if name.startswith('markdown') else '    > original'), item['unified_diff'])
            self.assertIn('hand edit 中文 🌱', item['unified_diff'])
            self.assertNotIn('PRIVATE', item['unified_diff'])
            self.assertNotIn('updated', item['unified_diff'])
        again = self.run_cli(['--inspect-managed', str(self.out)])
        self.assertEqual(again.stdout, inspected.stdout)
        self.assertEqual(state(self.work), before)

    def test_collects_all_artifact_issues_alongside_two_page_diffs_read_only(self):
        manifest_path = self.out / update.STATE
        manifest = json.loads(manifest_path.read_bytes())
        # Report order is independent of manifest insertion order.
        manifest['artifacts'] = dict(reversed(list(manifest['artifacts'].items())))
        manifest['pages'] = dict(reversed(list(manifest['pages'].items())))
        manifest_path.write_text(json.dumps(manifest))
        (self.out / 'report.json').write_bytes(b'edited report\xff')
        (self.out / update.CHANGES).unlink()
        outside = self.work / 'private.csv'
        outside.write_bytes(b'PRIVATE LINK TARGET')
        csv = self.out / 'annotations.csv'
        csv.unlink()
        csv.symlink_to(outside)
        for page in self.out.rglob('*.md'):
            page.write_bytes(b'PRIVATE PREFIX\xff\r\n' +
                             page.read_bytes().replace(b'original', b'edited region') + b'PRIVATE SUFFIX')
        before = filesystem_state(self.work)
        result = self.run_cli(['--inspect-managed', str(self.out)])
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stderr, '')
        report = json.loads(result.stdout)
        self.assertEqual(report['schema_version'], 1)
        self.assertEqual(report['status'], 'conflicts')
        self.assertEqual(report['checked_artifacts'], 4)
        self.assertEqual(report['checked_pages'], 2)
        issues = report['artifact_conflicts']
        self.assertEqual([c['path'] for c in issues], ['annotations.csv', 'changes.json', 'report.json'])
        for item in issues:
            name = item['path']
            self.assertEqual(item['expected_sha256'], manifest['artifacts'][name])
            self.assertEqual(item['code'], 'artifact_edit' if name == 'report.json' else 'unreadable_artifact')
            self.assertEqual(item['current_sha256'], update.sha(b'edited report\xff') if name == 'report.json' else None)
        self.assertEqual([c['path'] for c in report['conflicts']], ['logseq/article-1.md', 'markdown/article-1.md'])
        for item in report['conflicts']:
            self.assertEqual(item['expected']['sha256'], manifest['pages'][item['path']])
            self.assertEqual(item['comparison'], 'managed-region')
            self.assertIn('edited region', item['unified_diff'])
        self.assertNotIn('PRIVATE', result.stdout)
        original_read = Path.read_bytes
        def guarded_read(path):
            self.assertNotIn(path, (csv, outside), 'must not read symlink or target')
            return original_read(path)
        with patch.object(Path, 'read_bytes', guarded_read):
            self.assertEqual(update.inspect_managed(self.out), report)
        again = self.run_cli(['--inspect-managed', str(self.out)])
        self.assertEqual((again.returncode, again.stdout, again.stderr), (2, result.stdout, ''))
        self.assertEqual(filesystem_state(self.work), before)

    def test_artifact_only_edited_missing_linked_and_nonregular_files(self):
        manifest = json.loads((self.out / update.STATE).read_bytes())
        for name in ('annotations.csv', 'changes.json', 'report.json'):
            path = self.out / name
            original = path.read_bytes()
            target = self.work / ('saved-' + name)
            target.write_bytes(original)  # Even an intact link target is unavailable.
            for kind in ('edited', 'missing', 'symlink', 'dangling', 'directory'):
                with self.subTest(name=name, kind=kind):
                    path.unlink()
                    if kind == 'edited':
                        path.write_bytes(original + b'\xff')
                    elif kind in ('symlink', 'dangling'):
                        path.symlink_to(target if kind == 'symlink' else self.work / 'absent')
                    elif kind == 'directory':
                        path.mkdir()
                    before = filesystem_state(self.work)
                    result = self.run_cli(['--inspect-managed', str(self.out)])
                    self.assertEqual((result.returncode, result.stderr), (2, ''))
                    report = json.loads(result.stdout)
                    self.assertEqual(report['status'], 'conflicts')
                    self.assertEqual(report['conflicts'], [])
                    self.assertEqual(len(report['artifact_conflicts']), 1)
                    item = report['artifact_conflicts'][0]
                    self.assertEqual(item['path'], name)
                    self.assertEqual(item['expected_sha256'], manifest['artifacts'][name])
                    self.assertEqual(item['current_sha256'], update.sha(original + b'\xff') if kind == 'edited' else None)
                    self.assertEqual(item['code'], 'artifact_edit' if kind == 'edited' else 'unreadable_artifact')
                    self.assertEqual(filesystem_state(self.work), before)
                    if path.is_dir() and not path.is_symlink():
                        path.rmdir()
                    elif path.exists() or path.is_symlink():
                        path.unlink()
                    path.write_bytes(original)

    def test_bundle_ancestor_symlinks_fail_without_partial_conflict_report(self):
        (self.out / 'changes.json').unlink()
        (self.out / 'report.json').write_bytes(b'edited report')
        for page in self.out.rglob('*.md'):
            page.write_bytes(page.read_bytes().replace(b'original', b'edited region'))
        alias = self.work / 'alias'
        alias.symlink_to(self.work, target_is_directory=True)
        leaf = self.work / 'linked-notes'
        leaf.symlink_to(self.out, target_is_directory=True)
        dangling = self.work / 'dangling'
        dangling.symlink_to(self.work / 'absent', target_is_directory=True)
        loop = self.work / 'loop'
        loop.symlink_to(loop, target_is_directory=True)
        before = filesystem_state(self.work)
        for path, link in [(alias / 'notes', alias),
                           (Path(os.path.relpath(alias / 'notes', ROOT)), alias),
                           (alias / '..' / self.work.name / 'notes', alias),
                           (leaf, leaf), (dangling / 'notes', dangling), (loop / 'notes', loop)]:
            with self.subTest(path=path):
                result = self.run_cli(['--inspect-managed', str(path)])
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertFalse(result.stdout, 'linked path must fail before reporting conflicts')
                diagnostic = json.loads(result.stderr)
                self.assertEqual(diagnostic['code'], 'update_state')
                self.assertEqual(os.path.abspath(diagnostic['path']), str(link))
        # Lexical '..' is allowed when no traversed component is a symlink.
        for path in (self.out, self.out / '..' / 'notes'):
            result = self.run_cli(['--inspect-managed', str(path)])
            self.assertEqual((result.returncode, result.stderr), (2, ''))
            report = json.loads(result.stdout)
            self.assertEqual([c['path'] for c in report['artifact_conflicts']], ['changes.json', 'report.json'])
            self.assertEqual([c['path'] for c in report['conflicts']],
                             ['logseq/article-1.md', 'markdown/article-1.md'])
        self.assertEqual(filesystem_state(self.work), before)

    def test_bundle_ancestor_guard_never_reads_or_stats_through_link(self):
        alias = self.work / 'alias'
        alias.symlink_to(self.work, target_is_directory=True)
        original_lstat = Path.lstat
        def guarded_lstat(path, *args, **kwargs):
            self.assertNotIn(alias, path.parents, 'must not look up metadata through a symlink')
            return original_lstat(path, *args, **kwargs)
        with patch.object(Path, 'lstat', guarded_lstat), \
             patch.object(Path, 'read_bytes', side_effect=AssertionError('must not read baseline')), \
             contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(cli.main(['--inspect-managed', str(alias / 'notes')]), 2)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(json.loads(stderr.getvalue())['code'], 'update_state')

    def test_bundle_ancestor_metadata_error_remains_io_failure(self):
        original_lstat = Path.lstat
        def denied_lstat(path, *args, **kwargs):
            if path == self.work:
                raise PermissionError('synthetic ancestor metadata denied')
            return original_lstat(path, *args, **kwargs)
        with patch.object(Path, 'lstat', denied_lstat), \
             patch.object(Path, 'read_bytes', side_effect=AssertionError('must not read baseline')), \
             contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(cli.main(['--inspect-managed', str(self.out)]), 3)
        self.assertEqual(stdout.getvalue(), '')
        self.assertEqual(json.loads(stderr.getvalue())['code'], 'io_error')

    def test_invalid_baselines_hide_no_partial_artifact_or_page_report(self):
        original_state = (self.out / update.STATE).read_bytes()
        provenance = self.out / 'provenance.json'
        original_provenance = provenance.read_bytes()
        (self.out / 'annotations.csv').unlink()
        (self.out / 'report.json').write_bytes(b'edited')
        for page in self.out.rglob('*.md'):
            page.write_bytes(page.read_bytes().replace(b'original', b'hand edit'))
        for kind in ('missing', 'edited', 'symlink', 'directory', 'unreconstructible', 'state-json', 'state-hash'):
            with self.subTest(kind=kind):
                provenance.unlink()
                if kind == 'symlink':
                    target = self.work / 'baseline.json'
                    target.write_bytes(original_provenance)
                    provenance.symlink_to(target)
                elif kind == 'directory':
                    provenance.mkdir()
                elif kind != 'missing':
                    provenance.write_bytes(b'{}' if kind in ('edited', 'unreconstructible') else original_provenance)
                manifest = json.loads(original_state)
                if kind == 'unreconstructible':
                    manifest['artifacts']['provenance.json'] = update.sha(b'{}')
                elif kind == 'state-hash':
                    manifest['pages']['markdown/article-1.md'] = '0' * 64
                (self.out / update.STATE).write_bytes(b'{' if kind == 'state-json' else json.dumps(manifest).encode())
                before = filesystem_state(self.work)
                result = self.run_cli(['--inspect-managed', str(self.out)])
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertIn(json.loads(result.stderr)['code'], ('update_state', 'update_conflict'))
                self.assertEqual(filesystem_state(self.work), before)
                if kind == 'directory':
                    provenance.rmdir()
                elif provenance.exists() or provenance.is_symlink():
                    provenance.unlink()
                provenance.write_bytes(original_provenance)
                (self.out / update.STATE).write_bytes(original_state)

    @unittest.skipIf(os.name != 'posix' or os.geteuid() == 0, 'requires unprivileged POSIX file permissions')
    def test_real_os_read_failures_exit_three_without_partial_stdout(self):
        (self.out / 'annotations.csv').unlink()  # Earlier collected artifact issue.
        (self.out / 'logseq/article-1.md').write_bytes(b'malformed page')
        # Late artifact/page errors as well as baseline errors must all be fatal.
        for name in ('report.json', 'markdown/article-1.md', 'provenance.json', update.STATE):
            with self.subTest(name=name):
                path = self.out / name
                permissions = stat.S_IMODE(path.stat().st_mode)
                before = filesystem_state(self.work)
                path.chmod(0)
                try:
                    result = self.run_cli(['--inspect-managed', str(self.out)])
                    self.assertEqual(result.returncode, 3, result.stderr)
                    self.assertEqual(result.stdout, '')
                    self.assertEqual(json.loads(result.stderr)['code'], 'io_error')
                finally:
                    path.chmod(permissions)
                self.assertEqual(filesystem_state(self.work), before)

    @unittest.skipIf(os.name != 'posix' or os.geteuid() == 0, 'requires unprivileged POSIX file permissions')
    def test_real_metadata_permission_failures_are_not_unavailable_files(self):
        (self.out / 'annotations.csv').unlink()
        for directory in (self.out / 'markdown', self.out):
            with self.subTest(directory=directory):
                before = filesystem_state(self.work)
                permissions = stat.S_IMODE(directory.stat().st_mode)
                directory.chmod(0)
                try:
                    result = self.run_cli(['--inspect-managed', str(self.out)])
                    self.assertEqual(result.returncode, 3, result.stderr)
                    self.assertEqual(result.stdout, '')
                    self.assertEqual(json.loads(result.stderr)['code'], 'io_error')
                finally:
                    directory.chmod(permissions)
                self.assertEqual(filesystem_state(self.work), before)

    def test_clean_personal_edits_all_formats_namespaces_anonymous_and_retired_pages(self):
        # Baselines predate this feature: no added state fields are needed.
        for fmt in ('all', 'markdown', 'logseq', 'csv'):
            for namespaced in (False, True):
                with self.subTest(fmt=fmt, namespaced=namespaced):
                    out = self.work / f'{fmt}-{namespaced}'
                    raw = b'[{"id":"001","title":"Unicode \\u4e2d","annotations":[{"quote":"q","text":"t","ranges":[0.1234567890123456789012345]}]}]'
                    export = core.parse_sources([('p', raw), ('empty', b'[]')]) if namespaced else core.parse(raw)
                    cli.publish(out, update.plan_update(out, export, fmt).files)
                    for page in out.rglob('*.md'):
                        page.write_bytes(b'personal\xff\r\n' + page.read_bytes() + b'no final newline')
                    before = state(out)
                    result = update.inspect_managed(out)
                    self.assertEqual(result['status'], 'clean')
                    self.assertEqual(result['conflicts'], [])
                    self.assertEqual(result['artifact_conflicts'], [])
                    self.assertEqual(result['checked_artifacts'], 4 if fmt in ('all', 'csv') else 3)
                    self.assertEqual(state(out), before)
                    empty = core.parse_sources([('p', b'[]'), ('empty', b'[]')]) if namespaced else core.parse(b'[]')
                    update.publish_update(out, update.plan_update(out, empty, fmt))
                    self.assertEqual(update.inspect_managed(out)['status'], 'clean')
                    for page in out.rglob('*.md'):
                        page.write_bytes(page.read_bytes().replace(b'highlights removed', b'hand edit'))
                    self.assertEqual(len(update.inspect_managed(out)['conflicts']), len(list(out.rglob('*.md'))))

    def test_damaged_markers_binary_bytes_and_missing_page_do_not_hide_other_edits(self):
        first = self.out / 'logseq/article-1.md'
        second = self.out / 'markdown/article-1.md'
        original = first.read_bytes()
        for value in (original.replace(update.END, b''), original + update.BEGIN + b'tail',
                      update.END + original.replace(update.END, b''), original.replace(b'original', b'\xff')):
            with self.subTest(value=value):
                first.write_bytes(value.rstrip(b'\n'))
                second.write_bytes(second.read_bytes().replace(b'original', b'hand edit'))
                before = state(self.out)
                report = update.inspect_managed(self.out)
                self.assertEqual(len(report['conflicts']), 2)
                item = report['conflicts'][0]
                # Truncating the final newline outside the block is still allowed.
                binary = b'\xff' in value
                self.assertEqual(item['comparison'], 'managed-region' if binary else 'whole-page')
                self.assertEqual(item['code'], 'managed_edit' if binary else 'invalid_markers')
                self.assertEqual(base64.b64decode(item['current']['base64']),
                                 update.region(first.read_bytes(), str(first))[1] if binary else first.read_bytes())
                if not binary:
                    self.assertIn('\\ No newline at end of file', item['unified_diff'])
                else:
                    self.assertIn('\\xff', item['unified_diff'])
                self.assertEqual(state(self.out), before)
        first.unlink()
        report = update.inspect_managed(self.out)
        self.assertEqual(len(report['conflicts']), 2)
        self.assertEqual(report['conflicts'][0]['code'], 'unreadable_page')
        self.assertIsNone(report['conflicts'][0]['current'])
        self.assertIsNone(report['conflicts'][0]['unified_diff'])
        outside = self.work / 'outside.md'
        outside.write_bytes(original)
        first.symlink_to(outside)
        with patch.object(Path, 'read_bytes', autospec=True, side_effect=AssertionError('must not read linked page')):
            with self.assertRaises(core.BagnotesError):
                update.read_owned(self.out, 'logseq/article-1.md')
        self.assertEqual(len(update.inspect_managed(self.out)['conflicts']), 2)
        first.unlink()
        (self.out / 'logseq').rmdir()
        (self.out / 'logseq').symlink_to(self.work, target_is_directory=True)
        self.assertEqual(update.inspect_managed(self.out)['conflicts'][0]['code'], 'unreadable_page')

    def test_invalid_bundle_baseline_sidecar_and_io_fail_without_partial_report(self):
        baseline = state(self.out)
        bad_state = json.loads((self.out / update.STATE).read_bytes())
        bad_state['pages']['markdown/article-1.md'] = '0' * 64
        for name, value in [(update.STATE, b'{'), (update.STATE, json.dumps(bad_state).encode()),
                            ('provenance.json', b'{}')]:
            with self.subTest(name=name):
                path = self.out / name
                path.write_bytes(value)
                result = self.run_cli(['--inspect-managed', str(self.out)])
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertIn(json.loads(result.stderr)['code'], ('update_state', 'update_conflict'))
                path.write_bytes(baseline[name][0])
        for directory in (self.work / 'missing', self.work):
            result = self.run_cli(['--inspect-managed', str(directory)])
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, '')
        link = self.work / 'linked-notes'
        link.symlink_to(self.out, target_is_directory=True)
        self.assertEqual(self.run_cli(['--inspect-managed', str(link)]).returncode, 2)
        with patch.object(Path, 'read_bytes', side_effect=PermissionError('synthetic denied')), \
             contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(cli.main(['--inspect-managed', str(self.out)]), 3)
            self.assertEqual(stdout.getvalue(), '')
            self.assertEqual(json.loads(stderr.getvalue())['code'], 'io_error')

    def test_argument_combinations_rejected_and_inspection_never_publishes(self):
        args = ['--inspect-managed', str(self.out)]
        for extra in (['input.json'], ['--source', 'p', 'input.json'], ['-o', 'new'],
                      ['--format', 'all'], ['--update'], ['--dry-run'], ['--inspect-conflicts']):
            with self.subTest(extra=extra):
                result = self.run_cli([*args, *extra])
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, '')
        with patch('notewrench.wallabagnotes.cli.publish', side_effect=AssertionError('publish called')), \
             patch('notewrench.wallabagnotes.update.plan_update', side_effect=AssertionError('plan called')), \
             patch('notewrench.wallabagnotes.update.publish_update', side_effect=AssertionError('update called')), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(args), 0)

    def test_relative_inspection_then_manual_migration_and_retry(self):
        source = self.work / 'next.json'
        source.write_bytes(AFTER)
        pages = list(self.out.rglob('*.md'))
        originals = {p: p.read_bytes() for p in pages}
        note = 'handwritten 中文 🌱'.encode()
        for page in pages:
            page.write_bytes(page.read_bytes().replace(b'original', note))
        before = state(self.work)
        result = subprocess.run([*[sys.executable, '-m', 'notewrench', 'wallabag-notes'], '--inspect-managed', 'notes'],
                                cwd=self.work, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 2, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(len(report['conflicts']), 2)
        self.assertEqual(state(self.work), before)
        # User moves hand edits outside markers and restores exact expected bytes.
        for item in report['conflicts']:
            page = self.out / item['path']
            _, _, suffix = update.region(originals[page], str(page))
            page.write_bytes(base64.b64decode(item['expected']['base64']) + suffix + note)
        result = subprocess.run([*[sys.executable, '-m', 'notewrench', 'wallabag-notes'], 'next.json', '-o', 'notes', '--update'],
                                cwd=self.work, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        for page in pages:
            self.assertTrue(page.read_bytes().endswith(note))
            self.assertIn('updated 中文'.encode(), page.read_bytes())


if __name__ == '__main__':
    unittest.main()
