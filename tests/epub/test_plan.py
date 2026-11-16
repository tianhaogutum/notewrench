"""No-publication planning shares build selection and preserves source identity."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from notewrench.epub import BundleError
from notewrench.epub.cli import main
from notewrench.epub.core import plan, prepare
from notewrench.epub.epub import json_bytes
from notewrench.epub.ordering import MAX_ORDER_BYTES, MAX_ORDER_ENTRIES

ROOT = Path(__file__).resolve().parents[2]


class PlanTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='epub-plan-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)

    def write(self, name, data):
        path = self.work / name
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        return path

    def cli(self, *args):
        # Running outside the repository also makes accidental files easy to detect.
        return subprocess.run([sys.executable, '-m', 'notewrench', 'epub', *map(str, args)],
                              cwd=self.work, env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')},
                              capture_output=True, text=True, timeout=60)

    def same_evidence(self, report, built):
        for field in ('inputs', 'included', 'skipped', 'excluded', 'changes', 'warnings',
                      'errors', 'text_mode', 'resources', 'unused_images', 'image_manifest'):
            self.assertEqual(report.get(field), built.get(field), field)
        self.assertEqual(report['chapter_order'], [e['key'] for e in report['included']])
        for field in ('identifier', 'metadata', 'epub_sha256', 'validation', 'not_built'):
            self.assertNotIn(field, report)

    def test_requested_cli_without_output_path(self):
        result = subprocess.run([sys.executable, '-m', 'notewrench', 'epub', 'plan',
                                 'examples/epub/wallabag.json'], cwd=ROOT,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'planned')
        self.assertEqual(report['chapter_order'], ['1:101', '1:102'])
        self.assertEqual([e['key'] for e in report['included']], report['chapter_order'])

    def test_mixed_unicode_exact_keys_and_markdown_provenance(self):
        text = 'read_notes and **saved**.\r\n\r\n[local](#absent)'
        native = self.write('native.json', {'bookmarks': [
            {'content': {'type': 'link', 'url': 'https://example.invalid/missing'}},
            {'title': 'Native 二', 'content': {'type': 'text', 'text': text}}]})
        ids = ['中文:三', 'é', 'e\u0301', ' spaces ']
        api = self.write('api.json', {'bookmarks': [
            {'id': key, 'title': key, 'tags': [], 'content': {'type': 'text', 'text': 'safe'}}
            for key in ids], 'nextCursor': 'local-page-only'})
        inputs = [ROOT / 'examples/epub/wallabag.json', native, api]
        options = dict(skip_missing=True, text_mode='markdown')
        report = plan(inputs, **options)
        self.assertEqual(report['chapter_order'], ['1:101', '1:102', '2:entry-2'] + ['3:' + key for key in ids])
        self.assertEqual(report['skipped'][0]['key'], '2:entry-1')
        self.assertTrue(report['warnings'])
        entry = report['included'][2]
        self.assertEqual(entry['original_text'], text)
        self.assertEqual(entry['original_text_sha256'], hashlib.sha256(text.encode()).hexdigest())
        payload, built = prepare(inputs, chapter_order=report['chapter_order'], **options)
        self.assertIsNotNone(payload)
        self.same_evidence(report, built)
        self.assertTrue(any(e['key'] == '2:entry-2' for e in report['changes']))
        order = self.write('order.json', report['chapter_order'])
        result = self.cli('build', *inputs, '--text-mode', 'markdown', '--skip-missing',
                          '--chapter-order', order, '-o', self.work / 'book.epub')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.work / 'book.report.json').read_text())['included'], report['included'])

    def test_tag_or_id_or_and_selection_preserves_input_positions(self):
        inputs = [ROOT / 'examples/epub/wallabag.json', ROOT / 'examples/epub/karakeep-export.json']
        for options, expected in (({'tags': ['中文', 'weekend'], 'ids': ['1:101', '1:102']}, ['1:101', '1:102']),
                                  ({'tags': ['中文'], 'ids': ['1:101', '1:102']}, ['1:101']),
                                  ({'ids': ['2:entry-1']}, ['2:entry-1'])):
            with self.subTest(options=options):
                r = plan(inputs, **options)
                self.assertEqual(r['status'], 'planned')
                self.assertEqual(r['chapter_order'], expected)
                _, built = prepare(inputs, **options)
                self.same_evidence(r, built)
                self.assertTrue(r['excluded'])
        result = self.cli('plan', inputs[0], '--tag', '中文', '--tag', 'absent', '--id', '1:101', '--id', '1:102')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['chapter_order'], ['1:101'])

    def test_policy_failures_keep_usable_candidates_and_write_nothing(self):
        path = self.write('bodies.json', [
            {'id': 'missing', 'url': '', 'content': None},
            {'id': 'blank', 'url': '', 'content': '  '},
            {'id': 'cleaned', 'url': '', 'content': '<script>gone()</script>'},
            {'id': 'ok', 'url': '', 'content': '<p>usable</p>'}])
        before = {p.name: p.read_bytes() for p in self.work.iterdir()}
        for flags, status, code in (([], 'failed', 2), (['--skip-missing'], 'planned', 0)):
            result = self.cli('plan', path, *flags)
            self.assertEqual(result.returncode, code, result.stderr)
            r = json.loads(result.stdout)
            self.assertEqual(r['status'], status)
            self.assertEqual(r['chapter_order'], ['1:ok'])
            self.assertEqual(len(r['included']), 1)
            self.assertEqual([e['reason'] for e in r['skipped']], ['missing_body', 'missing_body', 'empty_after_cleaning'])
            self.assertEqual(bool(result.stderr), bool(code))
            _, built = prepare([path], skip_missing=bool(flags))
            self.assertEqual(r['errors'], built['errors'])
            self.assertEqual(r['included'], built['included'] if flags else built['not_built'])
        for flags in (['--tag', 'absent'], ['--id', '1:missing', '--skip-missing']):
            result = self.cli('plan', path, *flags)
            self.assertEqual(result.returncode, 2)
            r = json.loads(result.stdout)
            self.assertEqual(r['included'], [])
            self.assertEqual(r['chapter_order'], [])
            self.assertIn('No usable chapters selected.', r['errors'])
        self.assertEqual({p.name: p.read_bytes() for p in self.work.iterdir()}, before)
        api = self.write('unsupported.json', {'id': 'asset', 'content': {'type': 'asset'}})
        self.assertEqual(plan([api], skip_missing=True)['skipped'][0]['reason'], 'unsupported_content_type')

    def test_no_epub_serialization_destinations_staging_publication_or_network(self):
        # Exercise the actual CLI entry point in-process so forbidden calls fail.
        with ExitStack() as stack:
            for target in ('notewrench.epub.core.make_epub', 'notewrench.epub.epub.make_epub',
                           'notewrench.epub.core.validate_epub', 'notewrench.epub.cli.validate_epub',
                           'notewrench.epub.cli.prepare', 'notewrench.epub.core.publish_new',
                           'notewrench.epub.cli.publish_new', 'notewrench.epub.cli.check_destinations',
                           'notewrench.epub.core.check_destinations', 'tempfile.NamedTemporaryFile',
                           'tempfile.TemporaryDirectory', 'os.link', 'zipfile.ZipFile',
                           'xml.etree.ElementTree.tostring', 'socket.socket', 'socket.getaddrinfo'):
                stack.enter_context(patch(target, side_effect=AssertionError('forbidden: ' + target)))
            for args in ([str(ROOT / 'examples/epub/karakeep-markdown.json'), '--text-mode', 'markdown'],
                         ['--archive-bundle', str(ROOT / 'tests/epub/fixtures/archive-bundle')],
                         [str(ROOT / 'examples/epub/karakeep-export.json')]):
                stdout, stderr = io.StringIO(), io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = main(['plan', *args])
                r = json.loads(stdout.getvalue())
                self.assertEqual(code, 2 if r['errors'] else 0)
                self.assertEqual(r['chapter_order'], [e['key'] for e in r['included']])

    def test_resources_image_only_body_and_unused_declarations(self):
        inputs = [ROOT / 'examples/epub/wallabag-images.json']
        # Reuse public resource fixture; all declarations validated even when filtered.
        manifest = ROOT / 'examples/epub/image-manifest.json'
        r = plan(inputs, image_manifest=manifest)
        _, built = prepare(inputs, image_manifest=manifest)
        self.same_evidence(r, built)
        self.assertTrue(r['resources'])
        r = plan(inputs, image_manifest=manifest, ids=['1:2'])
        self.assertTrue(r['unused_images'])
        _, built = prepare(inputs, image_manifest=manifest, ids=['1:2'])
        self.same_evidence(r, built)
        image_only = self.write('image-only.json', [{'id': 1, 'url': '', 'content': '<img src="/shared.png">'}])
        declaration = json.loads(manifest.read_text())['images'][0]
        declaration['path'] = 'pixel.png'
        shutil.copyfile(manifest.parent / json.loads(manifest.read_text())['images'][0]['path'], self.work / 'pixel.png')
        local = self.write('images.json', {'schema_version': 1, 'images': [declaration]})
        r = plan([image_only], image_manifest=local)
        self.assertEqual(r['chapter_order'], ['1:1'])
        self.assertTrue(r['resources'])

    def test_fatal_cli_validation_including_excluded_inputs_and_assets(self):
        good = ROOT / 'examples/epub/wallabag.json'
        malformed = self.work / 'bad.json'
        invalids = [b'{', b'\xff', b'[{"id":1,"id":2}]', b'[NaN]',
                    b'[{"id":1,"content":{}}]',
                    b'[{"id":1,"content":"x"},{"id":1,"content":"y"}]']
        for raw in invalids:
            malformed.write_bytes(raw)
            r = self.cli('plan', good, malformed, '--id', '1:101')
            self.assertEqual(r.returncode, 2, repr(raw))
            self.assertEqual(r.stdout, '')
            self.assertNotIn('Traceback', r.stderr)
        declaration = {'key': '1:102', 'src': '/missing.png', 'path': 'absent.png',
                       'media_type': 'image/png', 'sha256': '0' * 64}
        manifest = self.write('images.json', {'schema_version': 1, 'images': [declaration]})
        invalid_xml = self.write('xml.json', [{'id': 1, 'url': '', 'content': '\x00'}])
        cases = [('plan', invalid_xml), ('plan',), ('plan', good, '--id', '1:unknown'), ('plan', self.work / 'absent.json'),
                 ('plan', good, '--id', '1:101', '--image-manifest', manifest),
                 ('plan', good, '-o', self.work / 'out.epub'), ('plan', good, '--report', 'out.json'),
                 ('plan', good, '--chapter-order', 'order.json'), ('plan', good, '--dry-run'),
                 ('plan', good, '--text-mode', 'bad'),
                 ('plan', good, '--archive-bundle', ROOT / 'tests/epub/fixtures/archive-bundle')]
        before = {p.name: p.read_bytes() for p in self.work.iterdir()}
        for args in cases:
            with self.subTest(args=args):
                r = self.cli(*args)
                self.assertEqual(r.returncode, 2)
                self.assertEqual(r.stdout, '')
                self.assertTrue(r.stderr)
                self.assertNotIn('Traceback', r.stderr)
        self.assertEqual({p.name: p.read_bytes() for p in self.work.iterdir()}, before)

    def test_bagkeep_source_evidence_plan_indices_missing_and_full_validation(self):
        fixture = ROOT / 'tests/epub/fixtures/archive-bundle'
        _, built = prepare([], bagkeep_bundle=fixture)
        for options, indices in (({}, [0, 1]), ({'ids': ['1:2']}, [None, 0])):
            r = plan([], bagkeep_bundle=fixture, **options)
            expected = copy.deepcopy(built['bagkeep_archive'])
            for entry, index in zip(expected['articles'], indices):
                entry.update(output_index=None, plan_index=index)
            self.assertEqual(r['bagkeep_archive'], expected)
            _, selected = prepare([], bagkeep_bundle=fixture, **options)
            self.same_evidence(r, selected)
        archive = self.work / 'archive'
        shutil.copytree(fixture, archive)
        manifest = json.loads((archive / 'manifest.json').read_text())
        manifest['records'][0]['cached_body'] = False
        (archive / 'manifest.json').write_text(json.dumps(manifest))
        for skip, status in ((False, 'failed'), (True, 'planned')):
            r = plan([], bagkeep_bundle=archive, skip_missing=skip)
            self.assertEqual(r['status'], status)
            self.assertEqual(r['chapter_order'], ['1:2'])
            self.assertEqual(r['skipped'][0]['reason'], 'bagkeep_missing_cached_body')
            self.assertEqual([e['plan_index'] for e in r['bagkeep_archive']['articles']], [None, 0])
            self.assertEqual([e['output_index'] for e in r['bagkeep_archive']['articles']], [None, None])
        payload = archive / manifest['records'][0]['file']
        raw = payload.read_bytes(); payload.write_bytes(b'X' + raw[1:])
        result = self.cli('plan', '--archive-bundle', archive, '--id', '1:2')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')
        self.assertIn('SHA-256', result.stderr)

    def test_template_entry_and_serialized_byte_bounds(self):
        # Actual adapters/cleaner, not mocked keys: each input is below its own cap.
        first = self.write('many.json', [{'id': n, 'title': 'x', 'url': '', 'content': 'x'}
                                         for n in range(MAX_ORDER_ENTRIES)])
        self.assertEqual(len(plan([first])['chapter_order']), MAX_ORDER_ENTRIES)
        second = self.write('one.json', [{'id': 0, 'title': 'x', 'url': '', 'content': 'x'}])
        with self.assertRaisesRegex(BundleError, 'exceeds 10000 entries'):
            plan([first, second])
        overhead = len(json_bytes(['1:x'])) - 1
        path = self.write('long-id.json', [{'id': 'x' * (MAX_ORDER_BYTES - overhead),
                                          'title': 'x', 'url': '', 'content': 'x'}])
        r = plan([path])
        self.assertEqual(len(json_bytes(r['chapter_order'])), MAX_ORDER_BYTES)
        self.write('long-id.json', [{'id': 'x' * (MAX_ORDER_BYTES - overhead + 1),
                                    'title': 'x', 'url': '', 'content': 'x'}])
        result = self.cli('plan', path)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, '')
        self.assertIn('chapter_order exceeds 1048576 bytes', result.stderr)
        with self.assertRaisesRegex(BundleError, 'text_mode'):
            plan([second], text_mode='invalid')


if __name__ == '__main__':
    unittest.main()
