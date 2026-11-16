#!/usr/bin/env python3
"""Reproducible seeded size-prefilter vs full-hash experiment for `notewrench zotero-attachments`.

Run from a clone after `pip install -e .`: python benchmarks/zotero-attachments/benchmark.py
No speed claim; Linux only (audits open the snapshot through /proc/self/fd).
"""
# SPDX-License-Identifier: AGPL-3.0-only
import argparse
import importlib.util
import json
from pathlib import Path
import random
import platform
import tempfile
import time

from notewrench.zoteroattachments import audit

# The synthetic fixture generator lives next to the examples; load it by path.
ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    '_zotero_attachments_make_fixture', ROOT / 'examples' / 'zotero-attachments' / 'make_fixture.py')
_fixture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fixture)
create_fixture = _fixture.create_fixture


def run(seed=100, unique_files=256):
    with tempfile.TemporaryDirectory(prefix='zotero-audit-benchmark-') as temporary:
        root = Path(temporary)
        snapshot, storage = create_fixture(root)
        rng = random.Random(seed)
        for i in range(unique_files):
            (storage / 'ABCDEFGH' / f'unique-{i:04d}.bin').write_bytes(rng.randbytes(4096 + i))
        for i in range(12):
            payload = rng.randbytes(2048 + i)
            for key, name in [('ABCDEFGH', 'left'), ('JKLMNPQR', 'right')]:
                (storage / key / f'{name}-{i}.bin').write_bytes(payload)
        # Same size, different bytes: these must be hashed but must not coalesce.
        for i in range(8):
            (storage / 'ABCDEFGH' / f'control-{i}.bin').write_bytes(rng.randbytes(3000))
        reports, measurements = {}, {}
        for name, hash_all in [('hash-all', True), ('size-prefilter', False)]:
            started = time.perf_counter()
            report = audit(snapshot, storage, hash_all=hash_all)
            elapsed = time.perf_counter() - started
            assert report['auditComplete'], report['auditErrors']
            reports[name] = report
            measurements[name] = {**{k: report['statistics'][k] for k in (
                'filesInventoried', 'bytesInventoried', 'filesHashAttempted', 'filesHashed',
                'bytesHashed', 'filesSkippedUniqueSize', 'duplicateGroups')}, 'wallSeconds': elapsed}
        assert reports['hash-all']['duplicateGroups'] == reports['size-prefilter']['duplicateGroups']
        assert measurements['size-prefilter']['bytesHashed'] < measurements['hash-all']['bytesHashed']
        return {'seed': seed, 'uniqueFiles': unique_files, 'duplicateResultsEqual': True,
                'environment': {'python': platform.python_version(), 'platform': platform.platform()},
                'baseline': 'hash every inventoried regular file using the same SHA-256 reader',
                'measurements': measurements,
                'note': 'Sequential warm-cache local experiment. Wall time is descriptive, not a speed guarantee.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=100)
    parser.add_argument('--unique-files', type=int, default=256)
    args = parser.parse_args()
    if args.unique_files < 1 or args.unique_files > 10000:
        parser.error('--unique-files must be in 1..10000')
    print(json.dumps(run(args.seed, args.unique_files), indent=2))
