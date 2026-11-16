# SPDX-License-Identifier: AGPL-3.0-only
"""CLI policy uses completed reports, preserving preview evidence and no-write rules."""

from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from .synthetic import (FOLDER, MISSING, NOTE_A, NOTE_B, RESOURCE,
                                    TAG, png_bytes, record, write_jex)
from notewrench.joplinlogseq.cli import main

ROOT = Path(__file__).resolve().parents[2]


def snapshot(root):
    return {str(p.relative_to(root)): ('symlink', p.readlink().as_posix()) if p.is_symlink()
            else ('directory',) if p.is_dir() else ('file', p.read_bytes())
            for p in root.rglob('*')}


class BrokenLinkGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'input.jex'
        self.output = self.root / 'graph'

    def archive(self, body, **metadata):
        write_jex(self.source, {
            NOTE_A + '.md': record(NOTE_A, 1, title='Source', body=body, **metadata),
            NOTE_B + '.md': record(NOTE_B, 1, title='Target'),
            FOLDER + '.md': record(FOLDER, 2, title='Notebook'),
            TAG + '.md': record(TAG, 5, title='Tag'),
        })

    def invoke(self, options, *, source=None, output=None):
        before = snapshot(self.root)
        stdout, stderr = io.StringIO(), io.StringIO()
        # A final snapshot alone cannot detect transient staging and cleanup.
        with patch('notewrench.joplinlogseq.converter.tempfile.mkdtemp', side_effect=AssertionError('staged preview')):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                try:
                    code = main([str(source or self.source), str(output or self.output), *options])
                except SystemExit as exc:
                    code = exc.code
        self.assertEqual(snapshot(self.root), before)
        return code, stdout.getvalue(), stderr.getvalue()

    def preview(self, expected_exit, *options):
        ordinary = self.invoke(['--dry-run', *options])
        gated = self.invoke(['--dry-run', '--fail-on-broken-links', *options])
        self.assertEqual(ordinary[0], 0, ordinary[2])
        self.assertEqual(gated[0], expected_exit, gated[2])
        self.assertEqual(gated[1], ordinary[1])
        self.assertEqual(ordinary[2], '')
        self.assertEqual(gated[2], '')
        return json.loads(gated[1])

    def issue(self, code, target, line):
        return dict(code=code, note_id=NOTE_A, target=target, line=line)

    def test_one_real_missing_target_with_protected_title_and_code(self):
        body = (f'[target]( \t:/{NOTE_B}#part\\)tail "literal :/{MISSING}" \t)\n'
                f'`[code](:/{MISSING})`\n[missing](:/{MISSING})')
        self.archive(body)
        report = self.preview(1)
        self.assertEqual(report['issues'], [self.issue('note_link_uses_file_path', NOTE_B, 1),
                                           self.issue('broken_link', MISSING, 3)])
        self.assertEqual(report['rewrites'], dict(note_links=1, resource_links=0,
                                                 checkboxes=0, protected_fences=0))
        self.assertEqual(report['source_items'][NOTE_A]['body'], body)
        self.assertNotIn('fail_on_broken_links', report['options'])

    def test_reference_fragment_titles_do_not_fail_gate(self):
        for destination_break in (' \t', '\n    '):
            for target in (NOTE_B, MISSING):
                with self.subTest(destination_break=destination_break, target=target):
                    body = (f'[target]:{destination_break}:/{target}#part\\)tail '
                            f'"tooltip :/{NOTE_B} :/{MISSING} " \t')
                    self.archive(body)
                    report = self.preview(int(target == MISSING))
                    self.assertEqual(report['issues'], [self.issue(
                        'broken_link' if target == MISSING else 'note_link_uses_file_path',
                        target, 1 + destination_break.count('\n'))])
                    self.assertEqual(report['rewrites'], dict(note_links=int(target == NOTE_B),
                        resource_links=0, checkboxes=0, protected_fences=0))
                    self.assertEqual(report['source_items'][NOTE_A]['body'], body)

    def test_single_quoted_inline_titles_only_gate_the_destination(self):
        for marker in ('', '!'):
            for angle in (False, True):
                for target in (NOTE_B, RESOURCE, MISSING):
                    with self.subTest(marker=marker, angle=angle, target=target):
                        uri = f':/{target}#part\\)tail'
                        destination = '<' + uri + '>' if angle else uri
                        body = (f'before\n{marker}[target]( \t{destination} '
                                f"'tooltip \\'quote\\' :/{NOTE_B} :/{MISSING} ' \t)")
                        write_jex(self.source, {
                            NOTE_A + '.md': record(NOTE_A, 1, title='Source', body=body),
                            NOTE_B + '.md': record(NOTE_B, 1, title='Target'),
                            RESOURCE + '.md': record(RESOURCE, 4, file_extension='png'),
                            'resources/' + RESOURCE + '.png': png_bytes(),
                        })
                        report = self.preview(int(target == MISSING))
                        expected = [] if target == RESOURCE else [self.issue(
                            'broken_link' if target == MISSING else 'note_link_uses_file_path', target, 2)]
                        self.assertEqual(report['issues'], expected)
                        self.assertEqual(report['rewrites'], dict(note_links=int(target == NOTE_B),
                            resource_links=int(target == RESOURCE), checkboxes=0, protected_fences=0))
                        self.assertEqual(report['source_items'][NOTE_A]['body'], body)

    def test_parenthesized_inline_titles_only_gate_the_destination(self):
        for marker in ('', '!'):
            for angle in (False, True):
                for target in (NOTE_B, RESOURCE, MISSING):
                    with self.subTest(marker=marker, angle=angle, target=target):
                        uri = f':/{target}#part\\)tail'
                        destination = '<' + uri + '>' if angle else uri
                        body = (f'before\n{marker}[target]( \t{destination} '
                                f'(tooltip \\(nested\\) :/{NOTE_B} :/{MISSING} ) \t)')
                        write_jex(self.source, {
                            NOTE_A + '.md': record(NOTE_A, 1, title='Source', body=body),
                            NOTE_B + '.md': record(NOTE_B, 1, title='Target'),
                            RESOURCE + '.md': record(RESOURCE, 4, file_extension='png'),
                            'resources/' + RESOURCE + '.png': png_bytes(),
                        })
                        report = self.preview(int(target == MISSING))
                        expected = [] if target == RESOURCE else [self.issue(
                            'broken_link' if target == MISSING else 'note_link_uses_file_path', target, 2)]
                        self.assertEqual(report['issues'], expected)
                        self.assertEqual(report['rewrites'], dict(note_links=int(target == NOTE_B),
                            resource_links=int(target == RESOURCE), checkboxes=0, protected_fences=0))
                        self.assertEqual(report['source_items'][NOTE_A]['body'], body)

    def test_no_one_many_missing_note_and_resource_targets(self):
        for body, expected in (
            (f'[valid](:/{NOTE_B})', []),
            (f'[note](:/{MISSING})', [(MISSING, 1)]),
            (f'![asset](:/{RESOURCE})', [(RESOURCE, 1)]),
            (f'[one](:/{MISSING})\n![two](:/{RESOURCE})\n[three]:\n  :/{MISSING}',
             [(MISSING, 1), (RESOURCE, 2), (MISSING, 4)]),
        ):
            with self.subTest(body=body):
                self.archive(body)
                report = self.preview(int(bool(expected)))
                self.assertEqual(report['issues'], [self.issue('broken_link', *i) for i in expected])

    def test_organization_types_resolve_only_when_selected(self):
        for targets in ((FOLDER,), (TAG,), (FOLDER, TAG)):
            self.archive('\n'.join(f'[org](:/{target})' for target in targets))
            for options, selected in (
                ((), set()), (('--include-notebook-pages',), {FOLDER}),
                (('--include-tag-pages',), {TAG}),
                (('--include-notebook-pages', '--include-tag-pages'), {FOLDER, TAG}),
                (('--include-organization-pages',), {FOLDER, TAG}),
                (('--include-organization-pages', '--include-tag-pages'), {FOLDER, TAG}),
            ):
                with self.subTest(targets=targets, options=options):
                    missing = [self.issue('broken_link', target, line)
                               for line, target in enumerate(targets, 1) if target not in selected]
                    report = self.preview(int(bool(missing)), *options)
                    self.assertEqual(report['issues'], missing)
                    self.assertEqual(report['rewrites']['note_links'], len(targets)-len(missing))

    def test_literal_and_html_contexts_keep_existing_classifications(self):
        bodies = (
            (f'~~~\n:/{MISSING}\n~~~', []),
            (f'`code :/{MISSING}`', []), (f'    :/{MISSING}', []),
            (f'\\[literal](:/{MISSING})', []),
            (f'[label :/{MISSING}](:/{NOTE_B})', []),
            (f'[valid](:/{NOTE_B} "title :/{MISSING}")',
             [self.issue('note_link_uses_file_path', NOTE_B, 1)]),
            (f'[ref]: :/{NOTE_B}\n  "title :/{MISSING}"',
             [self.issue('note_link_uses_file_path', NOTE_B, 1)]),
            (f'<a title="[literal](:/{NOTE_B})">HTML</a>',
             [self.issue('unsupported_link_context', NOTE_B, 1)]),
            (f'<!--\n[missing](:/{MISSING})\n-->',
             [self.issue('unsupported_link_context', MISSING, 2), self.issue('broken_link', MISSING, 2)]),
            (f'<a href=":/{MISSING}">HTML</a>',
             [self.issue('unsupported_link_context', MISSING, 1), self.issue('broken_link', MISSING, 1)]),
        )
        for body, issues in bodies:
            with self.subTest(body=body):
                self.archive(body)
                report = self.preview(int(any(i['code'] == 'broken_link' for i in issues)))
                self.assertEqual(report['issues'], issues)
                self.assertEqual(report['source_items'][NOTE_A]['body'], body)

    def test_unrelated_issues_and_unapplied_metadata_do_not_fail(self):
        self.archive(f'[file](:/{NOTE_B}#part)\n```\n:/{MISSING}',
                     parent_id=MISSING, future_field='preserve this')
        report = self.preview(0)
        self.assertEqual([i['code'] for i in report['issues']],
                         ['missing_note_folder', 'note_link_uses_file_path', 'unclosed_code_fence'])
        self.assertEqual(report['metadata_not_applied'][NOTE_A]['future_field'], 'preserve this')

    def test_gate_uses_completed_report_once_without_rescanning(self):
        for issues, expected in (([], 0), ([{'code': 'unsupported_link_context'}], 0),
                                 ([{'code': 'broken_link'}], 1), ([{'code': 'broken_link'}]*3, 1)):
            report = dict(issues=issues, source_items={'literal': ':/' + MISSING})
            with self.subTest(issues=issues), patch('notewrench.joplinlogseq.cli.convert', return_value=report) as convert:
                code, stdout, stderr = self.invoke(['--dry-run', '--fail-on-broken-links'])
                self.assertEqual((code, stderr), (expected, ''))
                self.assertEqual(stdout, json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)+'\n')
                convert.assert_called_once()
                self.assertTrue(convert.call_args.kwargs['dry_run'])
                self.assertNotIn('fail_on_broken_links', convert.call_args.kwargs)

    def test_gate_requires_dry_run_before_conversion_or_input_read(self):
        with patch('notewrench.joplinlogseq.cli.convert') as convert, patch('notewrench.joplinlogseq.converter.read_archive') as read:
            code, stdout, stderr = self.invoke(['--fail-on-broken-links'])
            self.assertEqual((code, stdout), (2, ''))
            self.assertIn('--fail-on-broken-links requires --dry-run', stderr)
            convert.assert_not_called()
            read.assert_not_called()

    def test_invalid_archive_payload_and_destination_remain_exit_two(self):
        self.source.write_bytes(b'not tar')
        for options in (['--dry-run'], ['--dry-run', '--fail-on-broken-links']):
            code, stdout, stderr = self.invoke(options)
            self.assertEqual((code, stdout), (2, ''))
            self.assertIn('Cannot read JEX', stderr)
        write_jex(self.source, {RESOURCE+'.md': record(RESOURCE, 4, file_extension='png')})
        code, stdout, stderr = self.invoke(['--dry-run', '--fail-on-broken-links'])
        self.assertEqual((code, stdout), (2, ''))
        self.assertIn('Missing or ambiguous resource bytes', stderr)
        self.archive(f'[missing](:/{MISSING})')
        for kind in ('directory', 'file', 'dangling-symlink'):
            if kind == 'directory':
                self.output.mkdir()
            elif kind == 'file':
                self.output.write_bytes(b'keep')
            else:
                self.output.symlink_to('absent')
            with self.subTest(kind=kind):
                code, stdout, stderr = self.invoke(['--dry-run', '--fail-on-broken-links'])
                self.assertEqual((code, stdout), (2, ''))
                self.assertIn('already exists', stderr)
            if kind == 'directory':
                self.output.rmdir()
            else:
                self.output.unlink()
        for source, output, extra, diagnostic in (
            (self.root/'absent', self.output, [], 'Cannot read JEX'),
            (self.source, self.root/'absent'/'graph', [], 'parent directory'),
            (self.source, self.output, ['--max-members', '0'], 'positive integer'),
        ):
            code, stdout, stderr = self.invoke(['--dry-run', '--fail-on-broken-links', *extra],
                                               source=source, output=output)
            self.assertEqual((code, stdout), (2, ''))
            self.assertIn(diagnostic, stderr)

    def test_real_module_cli_and_default_conversion(self):
        self.archive(f'[missing](:/{MISSING})\n`literal :/{RESOURCE}`')
        before = snapshot(self.root)
        argv = [sys.executable, '-m', 'notewrench', 'joplin-logseq', str(self.source), str(self.output)]
        ordinary = subprocess.run([*argv, '--dry-run'], cwd=ROOT, capture_output=True, timeout=60)
        gated = subprocess.run([*argv, '--dry-run', '--fail-on-broken-links'],
                               cwd=ROOT, capture_output=True, timeout=60)
        self.assertEqual((ordinary.returncode, gated.returncode), (0, 1), gated.stderr)
        self.assertEqual(ordinary.stdout, gated.stdout)
        self.assertEqual(ordinary.stderr + gated.stderr, b'')
        self.assertEqual(snapshot(self.root), before)
        converted = subprocess.run(argv, cwd=ROOT, capture_output=True, timeout=60)
        self.assertEqual(converted.returncode, 0, converted.stderr)
        self.assertEqual((self.output/'report.json').read_bytes(), ordinary.stdout)
        report = json.loads(ordinary.stdout)
        self.assertEqual(json.loads(converted.stdout), dict(output=str(self.output),
                         counts=report['counts'], issues=1))
        self.assertEqual(self.source.read_bytes(), before['input.jex'][1])


if __name__ == '__main__':
    unittest.main()
