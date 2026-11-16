# SPDX-License-Identifier: AGPL-3.0-only
"""API manifest bytes and response reads belong to one held input parent."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import notewrench.archive.cli as cli
from . import test_input_paths as inputs

MODES = inputs.MODES


class InputSelectionTests(unittest.TestCase):
    fixture = inputs.SelectedInputTests.fixture
    raw = inputs.SelectedInputTests.raw
    invoke = inputs.SelectedInputTests.invoke
    reject = inputs.SelectedInputTests.reject

    def retarget(self, kind, mode, form='leaf', pins=0):
        with self.fixture('api-import', kind) as (root, source):
            manifest = json.loads(source.read_bytes())
            for page in manifest['pages'][pins:]:
                page.pop('sha256')
            source.write_bytes(self.raw(manifest))
            raw = source.read_bytes()
            original = {p['file']: (source.parent / p['file']).read_bytes() for p in manifest['pages']}
            replacement = root / 'replacement'
            shutil.copytree(source.parent, replacement)
            for name, data in original.items():
                response = json.loads(data)
                items = response['_embedded']['items'] if kind == 'wallabag' else response['bookmarks']
                for item in items:
                    item['title'] = 'WRONG replacement root'
                (replacement / name).write_bytes(self.raw(response))
            selected = root / 'selected'
            if form == 'leaf':
                selected.symlink_to(source)
            elif form == 'directory':
                selected.symlink_to(source.parent, target_is_directory=True)
                selected = selected / source.name
            else:
                selected = source
            base_code, base_out, base_err = self.invoke('api-import', selected, root / 'baseline')
            self.assertEqual(base_code, 0, base_err)
            baseline = {p.name: p.read_bytes() for p in (root / 'baseline').iterdir()}
            read_input = cli.read_input
            swaps = []

            def after_read(path, *args, **kwargs):
                data = read_input(path, *args, **kwargs)
                self.assertEqual(data, raw)
                if form == 'leaf':
                    selected.unlink()
                    selected.symlink_to(replacement / source.name)
                elif form == 'directory':
                    selected.parent.unlink()
                    selected.parent.symlink_to(replacement, target_is_directory=True)
                else:
                    source.parent.rename(root / 'held-original')
                    replacement.rename(source.parent)
                swaps.append(True)
                return data

            output = root / 'bundle'
            before = set(os.listdir('/proc/self/fd'))
            with patch.object(cli, 'read_input', after_read):
                code, out, err = self.invoke('api-import', selected, output, mode)
            self.assertEqual(set(os.listdir('/proc/self/fd')), before)
            self.assertEqual(swaps, [True])
            self.assertEqual(code, 0, err)
            report = json.loads(out)['manifest']
            self.assertEqual(report['input_sha256'], hashlib.sha256(raw).hexdigest())
            for page in report['pagination']['pages']:
                self.assertEqual(page['actual_sha256'], hashlib.sha256(original[page['file']]).hexdigest())
            self.assertEqual(report, json.loads(baseline['manifest.json']))
            if '--dry-run' in mode:
                self.assertFalse(output.exists())
            else:
                self.assertEqual({p.name: p.read_bytes() for p in output.iterdir()}, baseline)
            self.assertFalse(list(root.glob('.bagkeep-*')))

    def test_post_read_selected_leaf_retarget_eight_cases(self):
        # Regression probe: old manifest bytes, then another response root.
        for kind in ['wallabag', 'karakeep']:
            for mode in MODES:
                with self.subTest(kind=kind, mode=mode):
                    self.retarget(kind, mode)

    def test_post_read_directory_retarget_parent_rename_and_pins(self):
        for kind in ['wallabag', 'karakeep']:
            for form in ['leaf', 'directory', 'parent-rename']:
                for pins in [0, 1, 2]:
                    for mode in MODES:
                        if form == 'leaf' and pins == 0:
                            continue  # covered by the eight-case regression above
                        with self.subTest(kind=kind, form=form, pins=pins, mode=mode):
                            flags = mode + (('--require-page-hashes',) if pins == 2 else ())
                            self.retarget(kind, flags, form, pins)

    def test_parent_is_held_before_manifest_open(self):
        for kind in ['wallabag', 'karakeep']:
            for mode in MODES:
                with self.subTest(kind=kind, mode=mode), self.fixture('api-import', kind) as (root, source):
                    original = source.read_bytes()
                    replacement = root / 'replacement'
                    shutil.copytree(source.parent, replacement)
                    # Invalid replacement manifest distinguishes reading the held leaf
                    # from reading through the replaced absolute parent path.
                    (replacement / source.name).write_bytes(b'{')
                    real_open = os.open
                    swaps = []

                    def at_open(path, flags, *args, **kwargs):
                        if kwargs.get('dir_fd') is not None and Path(path) == Path(source.name) and not swaps:
                            source.parent.rename(root / 'held-original')
                            replacement.rename(source.parent)
                            swaps.append(True)
                        return real_open(path, flags, *args, **kwargs)

                    before = set(os.listdir('/proc/self/fd'))
                    with patch.object(os, 'open', at_open):
                        code, out, err = self.invoke('api-import', source, root / 'bundle', mode)
                    self.assertEqual(swaps, [True])
                    self.assertEqual(code, 0, err)
                    self.assertEqual(json.loads(out)['manifest']['input_sha256'], hashlib.sha256(original).hexdigest())
                    self.assertEqual(set(os.listdir('/proc/self/fd')), before)

    def test_resolved_leaf_symlink_substitution_rejects_and_closes_root(self):
        for kind in ['wallabag', 'karakeep']:
            for mode in MODES:
                with self.subTest(kind=kind, mode=mode), self.fixture('api-import', kind) as (root, source):
                    selected = root / 'selected.json'
                    selected.symlink_to(source)  # stable explicit symlinks remain supported
                    outside = root / 'outside.json'
                    outside.write_bytes(source.read_bytes())  # identical bytes cannot authorize a new symlink
                    real_open = os.open
                    swaps = []

                    def at_open(path, flags, *args, **kwargs):
                        if kwargs.get('dir_fd') is not None and Path(path) == Path(source.name) and not swaps:
                            source.unlink()
                            source.symlink_to(outside)
                            swaps.append(True)
                        return real_open(path, flags, *args, **kwargs)

                    before = set(os.listdir('/proc/self/fd'))
                    with patch.object(os, 'open', at_open):
                        result = self.invoke('api-import', selected, root / 'bundle', mode)
                    self.assertEqual(swaps, [True])
                    self.reject(result, selected, root / 'bundle')
                    self.assertEqual(set(os.listdir('/proc/self/fd')), before)
