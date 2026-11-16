"""Shared helpers for the ``notewrench links`` suite (port of linkdedupe's node:test suite).

CLI tests call :func:`notewrench.linkdedupe.cli.main` in-process (stdout/stderr are
captured as raw UTF-8 bytes and decoded), which is much faster than spawning an
interpreter for each of the several thousand invocations the contracts make.
"""

from __future__ import annotations

import io
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / 'src'
if SRC.is_dir() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from notewrench.linkdedupe import cli  # noqa: E402

EXAMPLES = ROOT / 'examples' / 'links'
PREFIX = 'notewrench links: '


def fixture(name: str) -> str:
    return str(EXAMPLES / name)


def read_fixture(name: str) -> str:
    return (EXAMPLES / name).read_text(encoding='utf-8')


def js(value: Any) -> str:
    """``JSON.stringify(value)`` for ordinary test data."""
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


@dataclass
class Result:
    status: int
    stdout: str
    stderr: str


def invoke(args: list[str], cwd: str | Path) -> Result:
    """Run ``notewrench links <args>`` in ``cwd`` (``spawnSync`` equivalent)."""
    out, err = io.BytesIO(), io.BytesIO()
    stdout, stderr = io.TextIOWrapper(out, encoding='utf-8'), io.TextIOWrapper(err, encoding='utf-8')
    saved = sys.stdout, sys.stderr
    previous = os.getcwd()
    os.chdir(cwd)
    sys.stdout, sys.stderr = stdout, stderr
    try:
        status = cli.main([str(arg) for arg in args])
        stdout.flush()
        stderr.flush()
    finally:
        sys.stdout, sys.stderr = saved
        os.chdir(previous)
    return Result(status, out.getvalue().decode('utf-8'), err.getvalue().decode('utf-8'))


def listdir(path: str | Path) -> list[str]:
    return sorted(os.listdir(path))


@pytest.fixture
def cwd(tmp_path: Path) -> Path:
    """A physical (symlink-free) temporary working directory."""
    return Path(os.path.realpath(tmp_path))
