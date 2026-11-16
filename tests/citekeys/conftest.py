# SPDX-License-Identifier: AGPL-3.0-only
"""Shared helpers for the ``notewrench citekeys`` suite (port of citekey-rekey's node:test suite).

Report offsets count UTF-16 code units like the original JavaScript tool, so the
helpers below mirror ``String.prototype.indexOf``/``slice``/``length``.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1] / 'src'
if SRC.is_dir() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from notewrench.citekeys import cli  # noqa: E402
from notewrench.citekeys._js import from_units, to_units, u16len  # noqa: E402

FIXTURES = HERE / 'fixtures'
EXAMPLES = HERE.parents[1] / 'examples' / 'citekeys'
SUBPROCESS_TIMEOUT = 120


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / f'{name}.json').read_text(encoding='utf-8'))


# --- JavaScript string helpers (UTF-16 code units) -------------------------------

def index_of(text: str, sub: str, start: int = 0) -> int:
    return to_units(text).find(to_units(sub), start)


def last_index_of(text: str, sub: str, start: int | None = None) -> int:
    units = to_units(text)
    return units.rfind(to_units(sub)) if start is None else units.rfind(to_units(sub), 0, start + len(sub))


def js_slice(text: str, start: int, end: int | None = None) -> str:
    return from_units(to_units(text)[start:end])


def byte_length(text: str) -> int:
    return len(text.encode('utf-8'))


def length(text: str) -> int:
    return u16len(text)


def assert_edit_location(text: str, edit: dict[str, Any]) -> None:
    """The edit's UTF-16 and UTF-8 ranges, line and column refer to the original text."""
    assert js_slice(text, edit['offset'], edit['offset'] + edit['length']) == edit['key']
    before = js_slice(text, 0, edit['offset'])
    assert edit['byteOffset'] == byte_length(before)
    assert edit['byteLength'] == byte_length(edit['key'])
    data = text.encode('utf-8')
    assert data[edit['byteOffset']:edit['byteOffset'] + edit['byteLength']].decode('utf-8') == edit['key']
    assert edit['line'] == len(before.split('\n'))
    assert edit['column'] == edit['offset'] - last_index_of(before, '\n')


def unknown_location(text: str) -> dict[str, Any]:
    """Expected ``unresolved`` entry for the ``-@unknown`` marker of a fixture."""
    offset = index_of(text, '-@unknown') + 2
    before = js_slice(text, 0, offset)
    return {'key': 'unknown', 'offset': offset, 'line': len(before.split('\n')),
            'column': offset - last_index_of(text, '\n', offset), 'reason': 'no_mapping'}


# --- CLI helpers ------------------------------------------------------------------

@dataclass
class Result:
    status: int
    stdout: str
    stderr: str


def snapshot(root: Path) -> list[Any]:
    """Names, modes, mtimes and content hashes of a tree: proves zero filesystem writes."""
    rows: list[Any] = []
    for entry in sorted(os.scandir(root), key=lambda item: item.name):
        info = os.lstat(entry.path)
        if entry.is_dir(follow_symlinks=False):
            content: Any = snapshot(Path(entry.path))
        elif entry.is_symlink():
            content = os.readlink(entry.path)
        else:
            content = hashlib.sha256(Path(entry.path).read_bytes()).hexdigest()
        rows.append([entry.name, info.st_mode, info.st_mtime_ns, content])
    return rows


def cli_env() -> dict[str, str]:
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join(filter(None, [str(SRC), env.get('PYTHONPATH')]))
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


def run_subprocess(*args: str, cwd: Path) -> Result:
    """``python -m notewrench.citekeys`` as a real process (``spawnSync``)."""
    completed = subprocess.run([sys.executable, '-m', 'notewrench.citekeys', *args], cwd=cwd, env=cli_env(),
                               capture_output=True, timeout=SUBPROCESS_TIMEOUT)
    return Result(completed.returncode, completed.stdout.decode('utf-8'), completed.stderr.decode('utf-8'))


def git_apply(cwd: Path, diff: str, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(['git', 'apply', *args], cwd=cwd, input=diff.encode('utf-8'), capture_output=True,
                          timeout=SUBPROCESS_TIMEOUT)


requires_git = pytest.mark.skipif(shutil.which('git') is None, reason='git apply verifies the review diffs')


class Fixture:
    """A temporary project: ``old.json``, ``new.json`` and ``input/paper.md``."""

    base = ('--old', 'old.json', '--new', 'new.json', '--root', 'input')

    def __init__(self, directory: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        self.dir = directory
        self._monkeypatch = monkeypatch
        self._capsys = capsys
        (directory / 'input').mkdir()
        self.put('old.json', json.dumps([{'id': 'old', 'DOI': '10.123/demo'}]))
        self.put('new.json', json.dumps([{'id': 'New', 'DOI': 'https://doi.org/10.123/DEMO'}]))
        self.put('input/paper.md', 'See [@old].\n`[@old]` reader@old\n')

    def put(self, name: str, content: str | bytes) -> None:
        path = self.dir / name
        if isinstance(content, str):
            content = content.encode('utf-8')
        path.write_bytes(content)

    def read(self, name: str) -> bytes:
        return (self.dir / name).read_bytes()

    def run_raw(self, *args: str) -> Result:
        """In-process ``cli.main`` with this directory as the working directory."""
        self._capsys.readouterr()
        with self._monkeypatch.context() as patch:
            patch.chdir(self.dir)
            status = cli.main(list(args))
        captured = self._capsys.readouterr()
        return Result(status, captured.out, captured.err)

    def run(self, *args: str) -> Result:
        return self.run_raw(*self.base, *args)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Fixture:
    # A physical path: the tool refuses symlinked ancestors (macOS /var -> /private/var).
    directory = Path(os.path.realpath(tmp_path)) / 'project'
    directory.mkdir()
    return Fixture(directory, monkeypatch, capsys)


def error(result: Result, code: str) -> None:
    assert result.status == 2, result.stdout + result.stderr
    assert json.loads(result.stderr)['error']['code'] == code
