"""Original two-article API archive contracts; fixture made by `notewrench archive api-import`
(manifest tool "bagkeep", version 0.1.0)."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
import zipfile

from notewrench.epub import BundleError
from notewrench.epub.core import prepare
from notewrench.epub.epub import OPF, XHTML, q, validate_epub

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / 'tests/epub/fixtures/archive-bundle'


class BagkeepTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='epub-archive-')
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        self.bundle = self.work / 'arbitrary-directory'
        shutil.copytree(FIXTURE, self.bundle)
        self.original = json.loads((self.bundle / 'manifest.json').read_text())
        self.image = next(n for n in self.original['files'] if n.endswith('.png'))
        self.article = self.original['records'][0]['file']

    def build(self, **options):
        return prepare([], bagkeep_bundle=self.bundle, **options)

    def write_manifest(self, data):
        (self.bundle / 'manifest.json').write_text(json.dumps(data, ensure_ascii=False))

    def test_requested_shared_png_and_source_mapping(self):
        with patch.object(socket, 'socket', side_effect=AssertionError('network forbidden')), \
             patch.object(socket, 'getaddrinfo', side_effect=AssertionError('DNS forbidden')):
            payload, report = self.build()
            self.assertEqual(payload, self.build()[0])
            self.assertEqual(validate_epub(io.BytesIO(payload))['chapters'], 2)
        with zipfile.ZipFile(io.BytesIO(payload)) as book:
            opf = ET.fromstring(book.read('EPUB/package.opf'))
            items = opf.find(q(OPF, 'manifest'))
            images = [i for i in items if i.get('media-type') == 'image/png']
            self.assertEqual(len(images), 1)
            href = images[0].get('href')
            self.assertEqual(book.read('EPUB/' + href), (self.bundle / self.image).read_bytes())
            spine = [i.get('idref') for i in opf.find(q(OPF, 'spine'))]
            by_id = {i.get('id'): i.get('href') for i in items}
            for n, item in enumerate(spine, 1):
                chapter = ET.fromstring(book.read('EPUB/' + by_id[item]))
                self.assertEqual(chapter.findtext('.//' + q(XHTML, 'h1')), f'Saved 图 {n}')
                self.assertIn(f'Original body {n} 中文', ''.join(chapter.itertext()))
                self.assertEqual(chapter.find('.//' + q(XHTML, 'img')).get('src'), href)
        archive = report['bagkeep_archive']
        self.assertEqual(archive['manifest_sha256'], hashlib.sha256((self.bundle/'manifest.json').read_bytes()).hexdigest())
        self.assertEqual(archive['wallabag_sha256'], self.original['files']['wallabag.json']['sha256'])
        self.assertEqual(archive['upstream_issues'], self.original['issues'])
        self.assertEqual(archive['upstream_loss_count'], sum(i['severity']=='loss' for i in self.original['issues']))
        self.assertEqual([a['output_index'] for a in archive['articles']], [0, 1])
        self.assertEqual([a['bagkeep_entry'] for a in archive['articles']], [0, 1])
        self.assertEqual(archive['articles'][0]['source'], self.original['records'][0]['source'])
        self.assertEqual(archive['articles'][0]['article_sha256'], self.original['files'][self.article]['sha256'])
        self.assertEqual(len(report['resources']), 1)
        self.assertEqual(len(report['resources'][0]['declarations']), 2)
        self.assertEqual(archive['image_source_mapping'], self.original['image_assets']['mapping'])
        self.assertEqual(archive['articles'][0]['image_references'], self.original['records'][0]['image_references'])
        self.assertTrue(any(e['code']=='missing_resource' for e in report['changes']))
        self.assertNotIn('missing_image_asset', [e['code'] for e in report['changes']])

    def test_tampered_missing_and_symlink_payloads_fail_before_publication(self):
        for name in ('wallabag.json', self.article, self.image, 'provenance.json', 'index.html'):
            for kind in ('tamper', 'missing', 'symlink'):
                with self.subTest(name=name, kind=kind):
                    path = self.bundle / name
                    original = path.read_bytes()
                    if kind=='tamper': path.write_bytes(bytes([original[0] ^ 1])+original[1:])
                    elif kind=='missing': path.unlink()
                    else:
                        target = self.work/'outside'; target.write_bytes(original)
                        path.unlink(); path.symlink_to(target)
                    self.rejected_cli()
                    path.unlink(missing_ok=True); path.write_bytes(original)

    def rejected_cli(self, *extra):
        out = self.work/'rejected.epub'; report = self.work/'rejected.json'
        p = subprocess.run([sys.executable, '-m', 'notewrench', 'epub', 'build', '--archive-bundle', str(self.bundle),
                            '-o', str(out), '--report', str(report), *extra], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(p.returncode, 2, p.stdout+p.stderr)
        self.assertNotIn('Traceback', p.stderr)
        self.assertFalse(out.exists()); self.assertFalse(report.exists())
        self.assertFalse(list(self.work.glob('.readbundle-*')))
        return p.stderr

    def test_unknown_versions_malformed_schema_and_escaping_paths_reject(self):
        mutations = [lambda m:m.update(schema_version=2), lambda m:m.update(schema_version=True),
                     lambda m:m.update(version='0.2.0'), lambda m:m.update(mode='rescue'),
                     lambda m:m['files'].pop('wallabag.json'), lambda m:m.update(output_count=3),
                     lambda m:m['records'][0].update(entry=1), lambda m:m['records'][0].update(cached_body='yes'),
                     lambda m:m['files']['wallabag.json'].update(bytes=True),
                     lambda m:m['files']['wallabag.json'].update(sha256='0'*64),
                     lambda m:m['records'][0]['image_references'][0].update(sha256='0'*64),
                     lambda m:m.update(image_assets=[]), lambda m:m['image_assets'].update(mapping=[None]),
                     lambda m:m['image_assets']['mapping'][0].update(media_type=[]),
                     lambda m:m['source'].update(format=[]), lambda m:m['issues'][0].update(severity=[]),
                     lambda m:m['records'][0].update(file=[]), lambda m:m['records'][0].update(image_references=[None])]
        for name in ('../outside', '/tmp/outside', 'sub/../outside', './index.html', 'sub\\file', 'x//file', 'manifest.json'):
            mutations.append(lambda m,n=name:m['files'].update({n:dict(m['files']['index.html'])}))
        (self.bundle/'linked').symlink_to(self.bundle, target_is_directory=True)
        mutations.append(lambda m:m['files'].update({'linked/index.html':dict(m['files']['index.html'])}))
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                manifest=copy.deepcopy(self.original);mutate(manifest);self.write_manifest(manifest)
                self.rejected_cli()
        self.write_manifest(self.original)
        path=self.bundle/'manifest.json'; raw=path.read_bytes(); path.unlink()
        target=self.work/'manifest-copy';target.write_bytes(raw);path.symlink_to(target)
        self.rejected_cli()
        path.unlink();path.write_bytes(raw)
        for bad in (b'{"schema_version":1,"schema_version":1}', b'{"name":"\\ud800"}', b'{"count":NaN}'):
            path.write_bytes(bad);self.rejected_cli()
        path.write_bytes(raw)
        link=self.work/'bundle-link';link.symlink_to(self.bundle, target_is_directory=True)
        with self.assertRaises((BundleError,OSError)):prepare([],bagkeep_bundle=link)
        # Unlisted external files are never read; FIFO cannot block a declared read.
        os.mkfifo(self.bundle/'pipe')
        m=copy.deepcopy(self.original);m['files']['pipe']={'bytes':0,'sha256':hashlib.sha256(b'').hexdigest()}
        self.write_manifest(m);self.rejected_cli()

    def test_missing_upstream_body_is_not_a_complete_archive(self):
        m=copy.deepcopy(self.original);m['records'][0]['cached_body']=False;self.write_manifest(m)
        payload,report=self.build()
        self.assertIsNone(payload)
        self.assertEqual(report['skipped'][0]['reason'],'bagkeep_missing_cached_body')
        self.assertIsNone(report['bagkeep_archive']['articles'][0]['output_index'])
        payload,report=self.build(skip_missing=True)
        self.assertEqual(len(report['included']),1)
        self.assertEqual([a['output_index'] for a in report['bagkeep_archive']['articles']],[None,0])
        self.assertEqual(validate_epub(io.BytesIO(payload))['chapters'],1)

    def test_selection_and_explicit_selector_boundaries(self):
        payload,report=self.build(ids=['1:2'])
        self.assertEqual([a['output_index'] for a in report['bagkeep_archive']['articles']],[None,0])
        self.assertEqual(len(report['resources'][0]['declarations']),1)
        with self.assertRaises((BundleError, OSError)):prepare([self.bundle])
        for options in ({'image_manifest':ROOT / 'examples/epub/image-manifest.json'}, {'format':'karakeep-api'}):
            with self.assertRaises(BundleError):self.build(**options)
        with self.assertRaises(BundleError):prepare([ROOT / 'examples/epub/wallabag.json'],bagkeep_bundle=self.bundle)

    def test_existing_jpeg_subset_is_reused_even_for_unused_assets(self):
        data=(ROOT / 'examples/epub/image-resources/shared-restart.jpg').read_bytes()
        m=copy.deepcopy(self.original)
        (self.bundle/self.image).write_bytes(data)
        digest=hashlib.sha256(data).hexdigest()
        m['files'][self.image]={'bytes':len(data),'sha256':digest}
        m['image_assets']['mapping'][0].update(sha256=digest,media_type='image/jpeg',bytes=len(data))
        for r in m['records']:r['image_references'][0]['sha256']=digest
        self.write_manifest(m)
        payload,report=self.build()
        self.assertEqual(report['resources'][0]['media_type'],'image/jpeg')
        self.assertEqual(validate_epub(io.BytesIO(payload))['chapters'],2)
        # Progressive SOF2: matching payload hashes must not bypass the epub image profile.
        bad=data.replace(b'\xff\xc0',b'\xff\xc2',1)
        (self.bundle/self.image).write_bytes(bad);digest=hashlib.sha256(bad).hexdigest()
        m['files'][self.image]['sha256']=digest;m['image_assets']['mapping'][0]['sha256']=digest
        for r in m['records']:r['image_references'][0]['sha256']=digest
        self.write_manifest(m);self.rejected_cli('--tag','excluded')


if __name__=='__main__':unittest.main()
