# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (see licenses/browser-bookmarks-MIT.txt)
"""`notewrench browser-bookmarks`: program name, help without the optional extra, error prefix."""
import subprocess
import sys
from pathlib import Path

import pytest

import notewrench
from notewrench.browserbookmarks import REQUIRES, cli

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples" / "browser-bookmarks"


def test_version_and_help_use_the_notewrench_program_name(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out == f"notewrench browser-bookmarks {notewrench.__version__}\n"
    with pytest.raises(SystemExit) as exc:
        cli.main(["scan", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: notewrench browser-bookmarks scan ") and "--linkding" in out


@pytest.mark.parametrize("sub", ["scan", "status", "show", "review", "compat", "render", "decide", "apply", "verify"])
def test_every_subcommand_has_help(sub, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([sub, "--help"])
    assert exc.value.code == 0
    assert f"notewrench browser-bookmarks {sub}" in capsys.readouterr().out


def test_help_does_not_import_the_optional_dependencies():
    code = ("import sys; from notewrench.browserbookmarks import cli\n"
            "try:\n    cli.main(['--help'])\nexcept SystemExit:\n    pass\n"
            f"loaded = sorted(set({REQUIRES!r}) & {{name.partition('.')[0] for name in sys.modules}})\n"
            "print('LOADED=' + ','.join(loaded))")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-1] == "LOADED="


def test_errors_use_the_new_program_name(tmp_path, capsys):
    assert cli.main(["status", str(tmp_path / "missing" / "review.json")]) == 2
    err = capsys.readouterr().err
    assert err.startswith("notewrench browser-bookmarks: ") and "Traceback" not in err


def test_examples_scan_into_a_review_bundle(tmp_path, capsys):
    out = tmp_path / "review"
    assert cli.main(["scan", "--html", str(EXAMPLES / "firefox.html"), str(EXAMPLES / "chrome.html"),
                     "--linkding", str(EXAMPLES / "linkding.html"), "--out", str(out)]) == 0
    assert (out / "review.json").is_file() and (out / "review.html").is_file()
    assert '"duplicate_groups"' in capsys.readouterr().out
