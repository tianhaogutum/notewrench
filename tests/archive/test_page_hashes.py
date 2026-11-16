# SPDX-License-Identifier: AGPL-3.0-only
"""Optional saved-response pins cover bytes, independently of pagination checks."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import socket
import tempfile
import unittest
from unittest.mock import patch

from notewrench.archive.api import build_api
from notewrench.archive.cli import main
from notewrench.archive.core import InputError

ROOT = Path(__file__).resolve().parents[2]
MODES = [(), ('--strict',), ('--dry-run',), ('--strict', '--dry-run')]


class PageHashTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='archive-page-hash-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.pages = self.root / 'pages'
        shutil.copytree(ROOT / 'examples/archive/api', self.pages)
        self.output = self.root / 'bundle'

    def manifest(self, kind):
        return json.loads((self.pages / f'{kind}-manifest.json').read_text())

    def save(self, kind, manifest):
        (self.pages / f'{kind}-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')

    def pin(self, kind):
        manifest = self.manifest(kind)
        for page in manifest['pages']:
            page['sha256'] = hashlib.sha256((self.pages / page['file']).read_bytes()).hexdigest()
        self.save(kind, manifest)
        return manifest

    def run_cli(self, kind, *extra):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(['api-import', str(self.pages / f'{kind}-manifest.json'), '-o', str(self.output), *extra])
        return code, out.getvalue(), err.getvalue()

    def rejected(self, kind, diagnostic, *extra):
        code, out, err = self.run_cli(kind, *extra)
        self.assertEqual(code, 2, (out, err))
        self.assertEqual(out, '')
        self.assertIn(diagnostic, err)
        self.assertNotIn('Traceback', err)
        self.assertFalse(self.output.exists())
        self.assertFalse(list(self.root.glob('.bagkeep-*')))
        return err

    def test_matching_mixed_and_unpinned_hashes_provenance_and_determinism(self):
        for kind in ['wallabag', 'karakeep']:
            for pinned in [2, 1, 0]:
                with self.subTest(kind=kind, pinned=pinned):
                    manifest = self.pin(kind)
                    for page in manifest['pages'][pinned:]:
                        page.pop('sha256')
                    manifest['pages'].reverse()  # Metadata follows descriptor locators, not assembly order.
                    self.save(kind, manifest)
                    originals = {p.name: p.read_bytes() for p in self.pages.iterdir()}
                    code, out, err = self.run_cli(kind)
                    self.assertEqual(code, 0, err)
                    report = json.loads(out)['manifest']
                    provenance = json.loads((self.output / 'provenance.json').read_text())
                    self.assertEqual(provenance['source'], manifest)
                    summary = report['pagination']['pages']
                    self.assertEqual(len(summary), 2)
                    for i, (descriptor, page, metadata) in enumerate(zip(manifest['pages'], provenance['pages'], summary)):
                        digest = hashlib.sha256(originals[descriptor['file']]).hexdigest()
                        expected = descriptor.get('sha256')
                        for record in [page, metadata]:
                            self.assertEqual(record['page_index'], i)
                            self.assertEqual(record['file'], descriptor['file'])
                            self.assertEqual(record['expected_sha256'], expected)
                            self.assertEqual(record['actual_sha256'], digest)
                            self.assertEqual(record['sha256'], digest)  # Existing field stays actual.
                            self.assertEqual(record['status'], 'verified' if expected else 'unpinned')
                            key = 'page' if kind == 'wallabag' else 'cursor'
                            self.assertEqual(record[key], descriptor[key])
                        self.assertEqual(page['response'], json.loads(originals[descriptor['file']]))
                        self.assertEqual(metadata, {k: v for k, v in page.items() if k != 'response'})
                    first = {p.name: p.read_bytes() for p in self.output.iterdir()}
                    for name, info in report['files'].items():
                        self.assertEqual(hashlib.sha256(first[name]).hexdigest(), info['sha256'])
                    shutil.rmtree(self.output)
                    code, out, err = self.run_cli(kind)
                    self.assertEqual(code, 0, err)
                    self.assertEqual(first, {p.name: p.read_bytes() for p in self.output.iterdir()})
                    shutil.rmtree(self.output)
                    code, out, err = self.run_cli(kind, '--dry-run')
                    self.assertEqual(code, 0, err)
                    self.assertEqual(json.loads(out)['manifest'], report)
                    self.assertFalse(self.output.exists())
                    self.assertEqual(originals, {p.name: p.read_bytes() for p in self.pages.iterdir()})

    def test_pins_cover_bom_crlf_and_utf8_bytes_before_decoding(self):
        for kind in ['wallabag', 'karakeep']:
            page = self.pages / f'{kind}-1.json'
            raw = b'\xef\xbb\xbf' + page.read_bytes().replace(b'\n', b'\r\n')
            page.write_bytes(raw)
            self.pin(kind)
            code, out, err = self.run_cli(kind)
            self.assertEqual(code, 0, err)
            record = json.loads(out)['manifest']['pagination']['pages'][0]
            self.assertEqual(record['actual_sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(record['status'], 'verified')
            shutil.rmtree(self.output)

    def test_changed_bytes_reject_in_all_modes_even_when_json_is_unchanged_or_invalid(self):
        for kind in ['wallabag', 'karakeep']:
            manifest = self.pin(kind)
            page = self.pages / f'{kind}-2.json'
            original = page.read_bytes()
            for changed in [original + b' ', original.replace(b'\n', b'\r\n'), b'\xef\xbb\xbf' + original, b'not JSON', original.replace(b'cached body', b'edited body')]:
                page.write_bytes(changed)
                for mode in MODES:
                    with self.subTest(kind=kind, changed=changed[:20], mode=mode):
                        err = self.rejected(kind, 'page_sha256', *mode)
                        self.assertIn('$.pages[1]', err)
                        self.assertIn(page.name, err)
                        self.assertIn(manifest['pages'][1]['sha256'], err)
                        self.assertIn(hashlib.sha256(changed).hexdigest(), err)
            page.write_bytes(original)

    def test_malformed_supplied_pins_are_not_unpinned_in_any_mode(self):
        for kind in ['wallabag', 'karakeep']:
            manifest = self.pin(kind)
            for invalid in [None, False, 7, [], {}, '', 'a' * 63, 'a' * 65, 'A' * 64, 'g' * 64, 'a' * 64 + '\n', ' ' + 'a' * 64, 'ａ' * 64]:
                manifest['pages'][1]['sha256'] = invalid
                self.save(kind, manifest)
                for mode in MODES:
                    with self.subTest(kind=kind, invalid=invalid, mode=mode):
                        err = self.rejected(kind, 'page_sha256', *mode)
                        self.assertIn('$.pages[1].sha256', err)
                        self.assertIn('lowercase SHA-256', err)
            # Invalid last descriptor is rejected before any page-file callback.
            with self.assertRaisesRegex(InputError, 'page_sha256'), patch('socket.socket', side_effect=AssertionError('network forbidden')):
                build_api((self.pages / f'{kind}-manifest.json').read_bytes(),
                          lambda *args: self.fail('malformed pins must reject before page reads'))

    def test_valid_pins_preserve_namespace_duplicates_and_pagination_checks(self):
        for kind in ['wallabag', 'karakeep']:
            originals = {p.name: p.read_bytes() for p in self.pages.iterdir()}
            for case, diagnostic in [('namespace', 'source_namespace'), ('item', 'duplicate_item'),
                                     ('duplicate', 'duplicate_page'), ('missing', 'incomplete_pagination'),
                                     ('pagination', 'incomplete_pagination')]:
                for name, raw in originals.items():
                    (self.pages / name).write_bytes(raw)
                manifest = self.manifest(kind)
                page_path = self.pages / f'{kind}-2.json'
                response = json.loads(page_path.read_text())
                if case == 'namespace':
                    manifest['pages'][1]['namespace'] = 'other'
                elif case == 'duplicate':
                    manifest['pages'].append(dict(manifest['pages'][0]))
                elif case == 'missing':
                    manifest['pages'].pop()
                elif case == 'item':
                    if kind == 'wallabag':
                        response['_embedded']['items'][0]['id'] = 1
                    else:
                        response['bookmarks'][0]['id'] = 'bookmark-1'
                elif kind == 'wallabag':
                    response['total'] = 3
                else:
                    response['nextCursor'] = 'unsaved-next'
                page_path.write_text(json.dumps(response), encoding='utf-8')
                self.save(kind, manifest)
                self.pin(kind)
                with self.subTest(kind=kind, case=case):
                    self.rejected(kind, diagnostic)

    def test_pinned_inputs_keep_aggregate_limit_and_local_path_checks(self):
        for kind in ['wallabag', 'karakeep']:
            manifest = self.pin(kind)
            raw = (self.pages / f'{kind}-manifest.json').read_bytes()
            size = len(raw) + sum((self.pages / p['file']).stat().st_size for p in manifest['pages'])
            with patch('notewrench.archive.api.MAX_INPUT_BYTES', size - 1):
                self.rejected(kind, 'aggregate 64 MiB')
            manifest['pages'][0]['file'] = '../outside.json'
            (self.root / 'outside.json').write_bytes((self.pages / f'{kind}-1.json').read_bytes())
            self.save(kind, manifest)
            self.rejected(kind, 'local_page_path')

    def test_require_hashes_rejects_each_omission_in_descriptor_order_all_modes(self):
        for kind in ['wallabag', 'karakeep']:
            for reverse in [False, True]:
                for omitted in [0, 1, None]:
                    manifest = self.pin(kind)
                    if reverse:
                        manifest['pages'].reverse()
                    for i, page in enumerate(manifest['pages']):
                        if omitted is None or i == omitted:
                            del page['sha256']
                    self.save(kind, manifest)
                    for mode in MODES:
                        with self.subTest(kind=kind, reverse=reverse, omitted=omitted, mode=mode):
                            # Before implementation argparse exits on the unknown flag.
                            # Catch that so the regression fails on the missing diagnostic.
                            try:
                                err = self.rejected(kind, 'missing_page_pin', '--require-page-hashes', *mode)
                            except SystemExit as exc:
                                self.fail(f'policy is not implemented: CLI usage exit {exc.code}')
                            index = 0 if omitted is None else omitted
                            self.assertIn(f'$.pages[{index}].sha256', err)
                            self.assertIn('--require-page-hashes', err)

    def test_require_hashes_preflights_later_omission_before_any_page_read(self):
        for kind in ['wallabag', 'karakeep']:
            manifest = self.pin(kind)
            manifest['pages'].reverse()
            del manifest['pages'][1]['sha256']
            self.save(kind, manifest)
            with self.subTest(kind=kind), self.assertRaisesRegex(InputError, r'missing_page_pin: \$\.pages\[1\]\.sha256'):
                build_api((self.pages / f'{kind}-manifest.json').read_bytes(),
                          lambda *args: self.fail('missing pins must reject before page reads'),
                          require_page_hashes=True)

    def test_require_hashes_preserves_pinned_bundles_and_default_unpinned_strict(self):
        for kind in ['wallabag', 'karakeep']:
            # Remove unrelated API-only metadata losses to exercise strict pin policy.
            for name in [f'{kind}-1.json', f'{kind}-2.json']:
                path = self.pages / name
                response = json.loads(path.read_text())
                records = response['_embedded']['items'] if kind == 'wallabag' else response['bookmarks']
                for record in records:
                    tag_key = 'label' if kind == 'wallabag' else 'name'
                    record['tags'] = [{tag_key: tag[tag_key]} for tag in record['tags']]
                    if kind == 'karakeep':
                        for key in set(record) - {'id', 'userId', 'createdAt', 'title', 'archived', 'favourited', 'tags', 'content'}:
                            del record[key]
                path.write_text(json.dumps(response), encoding='utf-8')
            manifest = self.pin(kind)
            manifest['pages'].reverse()
            self.save(kind, manifest)
            code, out, err = self.run_cli(kind, '--strict')
            self.assertEqual(code, 0, err)
            baseline = {p.name: p.read_bytes() for p in self.output.iterdir()}
            expected_manifest = json.loads(out)['manifest']
            shutil.rmtree(self.output)
            for mode in MODES:
                with self.subTest(kind=kind, mode=mode):
                    code, out, err = self.run_cli(kind, '--require-page-hashes', *mode)
                    self.assertEqual(code, 0, err)
                    self.assertEqual(json.loads(out)['manifest'], expected_manifest)
                    self.assertEqual(json.loads(out)['loss_count'], 0)
                    if '--dry-run' in mode:
                        self.assertFalse(self.output.exists())
                    else:
                        self.assertEqual(baseline, {p.name: p.read_bytes() for p in self.output.iterdir()})
                        shutil.rmtree(self.output)
            for pinned in [1, 0]:
                manifest = self.pin(kind)
                for page in manifest['pages'][pinned:]:
                    del page['sha256']
                self.save(kind, manifest)
                for mode in MODES:
                    with self.subTest(kind=kind, pinned=pinned, mode=mode):
                        code, out, err = self.run_cli(kind, *mode)
                        self.assertEqual(code, 0, err)
                        self.assertEqual(json.loads(out)['loss_count'], 0)
                        pages = json.loads(out)['manifest']['pagination']['pages']
                        self.assertEqual([p['status'] for p in pages], ['verified'] * pinned + ['unpinned'] * (2 - pinned))
                        self.assertIsNone(pages[-1]['expected_sha256'])
                        if self.output.exists():
                            shutil.rmtree(self.output)

    def test_require_hashes_keeps_specific_malformed_and_mismatch_errors(self):
        for kind in ['wallabag', 'karakeep']:
            for invalid in [None, False, 7, [], {}, '', 'A' * 64, 'a' * 63, 'g' * 64]:
                manifest = self.pin(kind)
                manifest['pages'][1]['sha256'] = invalid
                self.save(kind, manifest)
                for mode in MODES:
                    with self.subTest(kind=kind, invalid=invalid, mode=mode):
                        err = self.rejected(kind, 'page_sha256', '--require-page-hashes', *mode)
                        self.assertIn('$.pages[1].sha256', err)
                        self.assertNotIn('missing_page_pin', err)
            manifest = self.pin(kind)
            page = self.pages / manifest['pages'][1]['file']
            original = page.read_bytes()
            page.write_bytes(original + b' ')
            for mode in MODES:
                with self.subTest(kind=kind, mode=mode):
                    err = self.rejected(kind, 'page_sha256', '--require-page-hashes', *mode)
                    self.assertIn('$.pages[1]', err)
                    self.assertIn(page.name, err)
                    self.assertIn(manifest['pages'][1]['sha256'], err)
                    self.assertIn(hashlib.sha256(page.read_bytes()).hexdigest(), err)
                    self.assertNotIn('missing_page_pin', err)
            page.write_bytes(original)

    def test_require_hashes_keeps_namespace_pagination_size_and_path_guards(self):
        for kind in ['wallabag', 'karakeep']:
            originals = {p.name: p.read_bytes() for p in self.pages.iterdir()}
            for case, diagnostic in [('namespace', 'source_namespace'), ('missing', 'incomplete_pagination'),
                                     ('duplicate', 'duplicate_page'), ('size', 'aggregate 64 MiB'),
                                     ('count', 'saved pages'), ('path', 'local_page_path')]:
                for name, raw in originals.items():
                    (self.pages / name).write_bytes(raw)
                manifest = self.pin(kind)
                if case == 'namespace':
                    manifest['pages'][1]['namespace'] = 'another-account'
                elif case == 'missing':
                    manifest['pages'].pop()
                elif case == 'duplicate':
                    manifest['pages'].append(dict(manifest['pages'][0]))
                elif case == 'path':
                    manifest['pages'][1]['file'] = '../outside.json'
                    (self.root / 'outside.json').write_bytes((self.pages / f'{kind}-2.json').read_bytes())
                self.save(kind, manifest)
                raw = (self.pages / f'{kind}-manifest.json').read_bytes()
                size = len(raw) + sum((self.pages / p['file']).stat().st_size for p in manifest['pages'])
                with self.subTest(kind=kind, case=case), contextlib.ExitStack() as stack:
                    if case == 'size':
                        stack.enter_context(patch('notewrench.archive.api.MAX_INPUT_BYTES', size - 1))
                    elif case == 'count':
                        stack.enter_context(patch('notewrench.archive.api.MAX_PAGES', 1))
                    self.rejected(kind, diagnostic, '--require-page-hashes')
