#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Self-checking demo of ``notewrench citekeys`` on the bundled synthetic examples.

Copies old.json/new.json/paper.md into a temporary directory, runs the CLI in
dry-run and --out-dir modes, verifies the exact output bytes and that the source
is unchanged, and always removes the temporary directory.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1] / 'src'


def main() -> int:
    directory = Path(os.path.realpath(tempfile.mkdtemp(prefix='citekeys-demo-')))
    try:
        for name in ['old.json', 'new.json', 'paper.md']:
            shutil.copyfile(HERE / name, directory / name)
        before = (directory / 'paper.md').read_bytes()
        env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [str(SRC), os.environ.get('PYTHONPATH')])))

        def invoke(*extra: str) -> dict:
            child = subprocess.run([sys.executable, '-m', 'notewrench.citekeys', '--old', 'old.json', '--new', 'new.json',
                                    'paper.md', *extra], cwd=directory, env=env, capture_output=True, timeout=120)
            assert child.returncode == 0, child.stderr.decode() + child.stdout.decode()
            return json.loads(child.stdout)

        preview = invoke()
        assert preview['mode'] == 'dry-run'
        assert len(preview['documents'][0]['edits']) == 5
        assert sorted(os.listdir(directory)) == ['new.json', 'old.json', 'paper.md']
        written = invoke('--out-dir', 'output')
        assert len(written['written']) == 1
        after = (directory / 'output/paper.md').read_bytes()
        expected = before.replace(b'See [@doe2020; -@doe2020-extra]. Exact keys: [@doe2020-extra].',
                                  b'See [@Doe20; -@Field-Notes]. Exact keys: [@Field-Notes].', 1) \
            .replace(b'Simultaneous migration: [@chainA; @chainB].', b'Simultaneous migration: [@chainB; @chainC].', 1)
        assert after == expected
        assert (directory / 'paper.md').read_bytes() == before
        assert preview['documents'][0]['diff'] == written['documents'][0]['diff']
        print(json.dumps({'result': 'PASS', 'verifiedEdits': 5,
                          'verified': ['dry-run zero output writes', 'exact output bytes', 'code/email preservation',
                                       'simultaneous key chain', 'original unchanged'],
                          'temporaryDirectoryRemovedOnExit': True}, indent=2))
        return 0
    except Exception:  # noqa: BLE001
        print(f'Demo failed: {traceback.format_exc()}', file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(directory, ignore_errors=True)


if __name__ == '__main__':
    raise SystemExit(main())
