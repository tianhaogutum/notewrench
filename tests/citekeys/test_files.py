# SPDX-License-Identifier: AGPL-3.0-only
"""Port of test/files.test.js."""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from notewrench.citekeys.files import write_outputs
from notewrench.citekeys.identity import RekeyError


@pytest.fixture
def root(tmp_path: Path) -> Path:
    directory = Path(os.path.realpath(tmp_path)) / 'sandbox'
    directory.mkdir()
    return directory


def test_failed_batch_output_removes_files_already_written_in_its_new_tree(root):
    output = root / 'output'
    with pytest.raises(Exception):
        write_outputs(str(output), [{'path': 'first.md', 'text': 'one'}, {'path': 'first.md/impossible.md', 'text': 'two'}])
    assert not output.exists()


def test_output_writer_refuses_root_relative_escape_and_rolls_back_new_tree(root):
    output = root / 'output'
    with pytest.raises(RekeyError) as caught:
        write_outputs(str(output), [{'path': '../escaped.md', 'text': 'escape'}])
    assert caught.value.code == 'PATH_ESCAPE'
    assert os.listdir(root) == []


def test_output_files_and_directories_use_private_modes_and_exclusive_creation(root):
    output = root / 'output'
    write_outputs(str(output), [{'path': 'nested/paper.md', 'text': 'exact\r\n'}])
    assert stat.S_IMODE(os.stat(output).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(output / 'nested').st_mode) == 0o700
    assert stat.S_IMODE(os.stat(output / 'nested/paper.md').st_mode) == 0o600
    with pytest.raises(RekeyError) as caught:
        write_outputs(str(output), [{'path': 'nested/paper.md', 'text': 'replace'}])
    assert caught.value.code == 'OUTPUT_EXISTS'
    assert (output / 'nested/paper.md').read_bytes() == b'exact\r\n'
