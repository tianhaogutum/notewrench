# SPDX-License-Identifier: MIT
"""``notewrench zotero-dupes`` naming: program name, version, errors and the review page."""
import os
import subprocess
import sys

import pytest

import notewrench
from notewrench.zoterodupes import cli

from .paths import EXAMPLES, ROOT

THIRD_PARTY = ("bib_dedupe", "rdflib", "defusedxml", "rapidfuzz", "pandas", "isbnlib", "jsonschema")


@pytest.mark.parametrize("argv", [["--help"], ["compare", "--help"], ["finalize", "--help"], ["inspect", "--help"],
                                  ["carry-decisions", "--help"], ["schema", "--help"]])
def test_help_uses_notewrench_program_name(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("usage: notewrench zotero-dupes")


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert capsys.readouterr().out == f"notewrench zotero-dupes {notewrench.__version__}\n"


def test_help_and_version_need_no_optional_dependencies():
    # Block the extra's packages: the parser, --help and --version must not import them.
    code = "\n".join([
        "import sys",
        f"for name in {THIRD_PARTY!r}: sys.modules[name] = None",
        "from notewrench.zoterodupes.cli import main",
        "for argv in (['compare', '--help'], ['--version']):",
        "    try:",
        "        main(argv)",
        "    except SystemExit as exc:",
        "        assert exc.code == 0, exc.code",
    ])
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, env=env)
    assert result.returncode == 0, result.stderr
    assert "usage: notewrench zotero-dupes compare" in result.stdout
    assert f"notewrench zotero-dupes {notewrench.__version__}" in result.stdout


def test_errors_use_notewrench_prefix(tmp_path, capsys):
    out = tmp_path / "exists"
    out.mkdir()
    assert cli.main(["compare", "--library", f"a={EXAMPLES / 'personal.json'}", "--library",
                     f"b={EXAMPLES / 'group.json'}", "--output", str(out)]) == 2
    assert capsys.readouterr().err.startswith("notewrench zotero-dupes: Output already exists")


def test_review_page_names_the_finalize_command(realistic_review):
    from notewrench.zoterodupes.decisions import decision_template
    from notewrench.zoterodupes.render import render_review

    page = render_review(realistic_review, decision_template(realistic_review))
    assert "notewrench zotero-dupes finalize --review" in page
    assert "library-bridge finalize" not in page
