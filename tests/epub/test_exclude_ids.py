"""Exact ID exclusions share selection, retain identities and precede body policy."""
from contextlib import ExitStack
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from notewrench.epub import BundleError
from notewrench.epub.core import plan, prepare

ROOT = Path(__file__).resolve().parents[2]
WALLABAG = ROOT / 'examples/epub/wallabag.json'
BAGKEEP = ROOT / 'tests/epub/fixtures/archive-bundle'
REASON = 'explicit_id_exclusion'


class ExcludeIdTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='epub-exclude-')
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)

    def write(self, name, value):
        path = self.work / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return path

    def cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'notewrench', 'epub', *map(str, args)],
                              cwd=self.work, env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')},
                              capture_output=True, text=True, timeout=60)

    def snapshot(self):
        return {str(p.relative_to(self.work)): p.read_bytes()
                for p in self.work.rglob('*') if p.is_file()}

    def test_requested_cli_exclusion_without_output_path(self):
        before = self.snapshot()
        result = self.cli('plan', WALLABAG, '--exclude-id', '1:101')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        r = json.loads(result.stdout)
        self.assertEqual(r['chapter_order'], ['1:102'])
        self.assertEqual(r['excluded'], [{**plan([WALLABAG])['included'][0], 'reason': REASON}])
        self.assertEqual(self.snapshot(), before)

    def test_filter_boolean_rules_explicit_reason_and_repeat_idempotence(self):
        original = plan([WALLABAG])
        for options, expected in ((dict(tags=['weekend', 'absent'], ids=['1:101', '1:102']), ['1:102']),
                                  (dict(tags=['中文'], ids=['1:101', '1:102']), []),
                                  (dict(ids=['1:102']), ['1:102'])):
            with self.subTest(options=options):
                r = plan([WALLABAG], exclude_ids=['1:101', '1:101'], **options)
                self.assertEqual(r['chapter_order'], expected)
                self.assertEqual(r['excluded'][0], {**original['included'][0], 'reason': REASON})
                self.assertEqual(len(r['excluded']), 2 - len(expected))
                if not expected:
                    self.assertEqual(r['excluded'][1]['reason'], 'selection_filter')
                self.assertEqual(r, plan([WALLABAG], exclude_ids=('1:101',), **options))
        result = self.cli('plan', WALLABAG, '--tag', 'weekend', '--tag', 'absent',
                          '--id', '1:101', '--id', '1:102', '--exclude-id', '1:101', '--exclude-id', '1:101')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['chapter_order'], ['1:102'])

    def test_mixed_native_unicode_exact_keys_and_original_markdown_evidence(self):
        text = 'read_notes and **saved**.\r\n\r\n[local](#absent)'
        native = self.write('native.json', {'bookmarks': [
            {'content': {'type': 'link', 'url': 'https://example.invalid/missing'}},
            {'title': 'Native 二', 'content': {'type': 'text', 'text': text}}]})
        keys = ['101', 'é', 'e\u0301', '中文:三', ' spaces ']
        api = self.write('api.json', {'bookmarks': [
            {'id': k, 'title': k, 'content': {'type': 'text', 'text': 'Safe'}} for k in keys],
            'nextCursor': 'retained'})
        inputs = [WALLABAG, native, api]
        exclusions = ['1:101', '2:entry-1', '3:é', '3:中文:三', '3: spaces ']
        order = ['3:e\u0301', '2:entry-2', '3:101', '1:102']
        options = dict(exclude_ids=exclusions, text_mode='markdown', chapter_order=order)
        r = plan(inputs, **options)
        self.assertEqual(r['status'], 'planned')
        self.assertEqual(r['chapter_order'], order)
        self.assertEqual([e['key'] for e in r['excluded']], exclusions)
        self.assertTrue(all(e['reason'] == REASON for e in r['excluded']))
        self.assertEqual(r['skipped'], [])
        self.assertTrue(r['warnings'])
        entry = r['included'][1]
        self.assertEqual((entry['id'], entry['input'], entry['format']), ('entry-2', str(native), 'karakeep-export'))
        self.assertEqual(entry['original_text'], text)
        self.assertEqual(entry['original_text_sha256'], hashlib.sha256(text.encode()).hexdigest())
        self.assertTrue(r['changes'])
        payload, built = prepare(inputs, **options)
        self.assertIsNotNone(payload)
        for field in ('included', 'excluded', 'skipped', 'changes', 'warnings'):
            self.assertEqual(r[field], built[field])
        # Omitting articles is identical to selecting the remaining keys for EPUB bytes.
        expected, _ = prepare(inputs, ids=order, chapter_order=order, text_mode='markdown')
        self.assertEqual(payload, expected)
        self.assertEqual(exclusions, options['exclude_ids'])

    def test_excluded_bodies_do_not_trigger_missing_policy_or_cleaning(self):
        path = self.write('bodies.json', [
            {'id': 'missing', 'url': '', 'content': None},
            {'id': 'blank', 'url': '', 'content': '  '},
            {'id': 'cleaned', 'url': '', 'content': '<script>gone()</script>'},
            {'id': 'ok', 'url': '', 'content': '<p>Usable</p>'}])
        exclusions = ['1:missing', '1:blank', '1:cleaned']
        r = plan([path], exclude_ids=exclusions)
        self.assertEqual((r['status'], r['chapter_order'], r['skipped'], r['changes']),
                         ('planned', ['1:ok'], [], []))
        payload, built = prepare([path], exclude_ids=exclusions, chapter_order=['1:ok'])
        self.assertIsNotNone(payload)
        self.assertEqual(built['excluded'], r['excluded'])
        for function in (plan, prepare):
            result = function([path], exclude_ids=exclusions + ['1:ok'])
            report = result if function is plan else result[1]
            self.assertEqual(report['errors'], ['No usable chapters selected.'])
            self.assertEqual(len(report['excluded']), 4)
            self.assertEqual(report['skipped'], [])

    def test_unknown_and_malformed_exclusions_are_fatal_even_outside_inclusion(self):
        for function in (plan, prepare):
            for value in (['1:no-such-id'], ['2:101'], ['1:101 '], ['1:101', '1:no-such-id']):
                with self.subTest(function=function.__name__, value=value), self.assertRaisesRegex(BundleError, 'unknown exclusion keys'):
                    function([WALLABAG], ids=['1:102'], exclude_ids=value)
            for value in ('1:101', b'1:101', bytearray(b'1:101'), Path('x'), {'1:101'},
                          {'1:101': True}, iter(['1:101']), None, [True], [1], [[]],
                          ['01:101'], ['0:101'], ['1:'], ['101'], ['1:\x00'], ['1:\ud800']):
                with self.subTest(function=function.__name__, value=repr(value)), self.assertRaises(BundleError):
                    function([WALLABAG], exclude_ids=value)
        before = self.snapshot()
        for command, flags in (('plan', []), ('build', ['-o', self.work/'bad.epub'])):
            r = self.cli(command, WALLABAG, '--id', '1:102', '--exclude-id', '1:typo', *flags)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(r.stdout, '')
            self.assertIn('unknown exclusion keys', r.stderr)
            self.assertNotIn('Traceback', r.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_remaining_order_and_existing_missing_policy_precedence(self):
        native = ROOT / 'examples/epub/karakeep-export.json'
        for function in (plan, prepare):
            for keys in (['1:101', '1:102'], ['1:101'], [], ['1:102', '1:typo']):
                with self.subTest(function=function.__name__, keys=keys), self.assertRaisesRegex(BundleError, 'chapter_order'):
                    function([WALLABAG], exclude_ids=['1:101'], chapter_order=keys)
            result = function([WALLABAG, native], exclude_ids=['1:101'], chapter_order=['1:101'])
            r = result if function is plan else result[1]
            self.assertTrue(r['errors'])
            self.assertEqual(r['skipped'][0]['key'], '2:entry-2')
            if function is plan:
                self.assertEqual(r['chapter_order'], ['1:102', '2:entry-1'])
            else:
                self.assertIsNone(result[0])
            with self.assertRaisesRegex(BundleError, 'chapter_order'):
                function([WALLABAG, native], exclude_ids=['1:101'], chapter_order=['1:101'], skip_missing=True)
            with self.assertRaisesRegex(BundleError, 'unknown exclusion keys'):
                function([native], exclude_ids=['1:typo'])
        order = self.write('order.json', ['1:101', '1:102'])
        before = self.snapshot()
        for command, flags in (('plan', []), ('build', ['-o', self.work/'bad.epub'])):
            r = self.cli(command, WALLABAG, '--exclude-id', '1:101', '--chapter-order', order, *flags)
            self.assertEqual(r.returncode, 2)
            self.assertEqual(r.stdout, '')
            self.assertIn('unknown/excluded/skipped', r.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_all_inputs_and_unused_resources_validate_before_exclusion(self):
        bad = self.write('bad.json', [{'id': 101, 'url': '', 'content': 123}])
        manifest = self.write('images.json', {'schema_version': 1, 'images': [
            {'key': '1:101', 'src': '/absent.png', 'path': 'absent.png',
             'media_type': 'image/png', 'sha256': '0'*64}]})
        for function in (plan, prepare):
            with self.assertRaisesRegex(BundleError, 'content must be HTML string'):
                function([WALLABAG, bad], exclude_ids=['2:101'])
            # The missing asset belongs to an excluded article; resource failure
            # also precedes an unknown exclusion typo.
            for exclusions in (['1:101'], ['1:typo']):
                with self.assertRaises(OSError):
                    function([WALLABAG], image_manifest=manifest, exclude_ids=exclusions)
        before = self.snapshot()
        r = self.cli('plan', WALLABAG, '--image-manifest', manifest, '--exclude-id', '1:101')
        self.assertEqual(r.returncode, 2)
        self.assertEqual(r.stdout, '')
        self.assertEqual(self.snapshot(), before)

    def test_bagkeep_provenance_resources_indices_missing_and_corruption(self):
        bundle = self.work / 'bundle'
        shutil.copytree(BAGKEEP, bundle)
        manifest_path = bundle / 'manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['records'][0]['cached_body'] = False
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        options = dict(bagkeep_bundle=bundle, exclude_ids=['1:1'], chapter_order=['1:2'])
        r = plan([], **options)
        self.assertEqual(r['status'], 'planned')
        self.assertEqual(r['skipped'], [])
        self.assertEqual(r['excluded'][0]['key'], '1:1')
        self.assertEqual(r['excluded'][0]['reason'], REASON)
        self.assertEqual([e['plan_index'] for e in r['bagkeep_archive']['articles']], [None, 0])
        self.assertEqual([e['output_index'] for e in r['bagkeep_archive']['articles']], [None, None])
        payload, built = prepare([], **options)
        self.assertIsNotNone(payload)
        archive = copy.deepcopy(built['bagkeep_archive'])
        for e in archive['articles']:
            e['plan_index'] = e['output_index']
            e['output_index'] = None
        self.assertEqual(r['bagkeep_archive'], archive)
        self.assertEqual(r['bagkeep_archive']['upstream_issues'], manifest['issues'])
        self.assertEqual(r['bagkeep_archive']['articles'][0]['source'], manifest['records'][0]['source'])
        self.assertEqual(r['bagkeep_archive']['manifest_sha256'], hashlib.sha256(manifest_path.read_bytes()).hexdigest())
        self.assertTrue(r['unused_images'])
        self.assertTrue(all(e['key'] == '1:2' for resource in r['resources'] for e in resource['declarations']))
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            self.assertEqual(len([n for n in z.namelist() if n.endswith('.png')]), 1)
        before = self.snapshot()
        cli = self.cli('plan', '--bagkeep-bundle', bundle, '--exclude-id', '1:1')  # former spelling stays accepted
        self.assertEqual(cli.returncode, 0, cli.stderr)
        self.assertEqual(json.loads(cli.stdout), r)
        self.assertEqual(self.snapshot(), before)
        # A declared but unneeded archive payload must still hash correctly.
        damaged = bundle / manifest['records'][0]['file']
        damaged.write_bytes(damaged.read_bytes() + b'corrupt')
        for function in (plan, prepare):
            with self.assertRaisesRegex(BundleError, 'payload'):
                function([], **options)

    def test_plan_exclusion_never_calls_epub_publication_or_network(self):
        before = self.snapshot()
        with ExitStack() as stack:
            for name in ('notewrench.epub.core.make_epub', 'notewrench.epub.core.validate_epub',
                         'notewrench.epub.core.check_destinations', 'notewrench.epub.core.publish_new',
                         'zipfile.ZipFile', 'tempfile.NamedTemporaryFile', 'os.link',
                         'socket.socket', 'socket.getaddrinfo'):
                stack.enter_context(patch(name, side_effect=AssertionError(name)))
            r = plan([WALLABAG], exclude_ids=['1:101'], chapter_order=['1:102'])
        self.assertEqual(r['status'], 'planned')
        self.assertEqual(self.snapshot(), before)
