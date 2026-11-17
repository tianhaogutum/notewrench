"""`notewrench bibtex-keys` program name, version, error prefix and lazy third-party imports."""
from pathlib import Path
import subprocess
import sys

import pytest

import notewrench
from notewrench.bibtexkeys.cli import main

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples/bibtex-keys"


@pytest.mark.parametrize("argv", [["--help"], ["plan", "--help"], ["verify", "--help"]])
def test_help_uses_notewrench_program_name(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: notewrench bibtex-keys")
    assert "citekey-audit" not in out


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out == f"notewrench bibtex-keys {notewrench.__version__}\n"


def test_errors_use_notewrench_prefix(tmp_path, capsys):
    out = tmp_path / "review"
    out.mkdir()
    args = ["plan", str(EXAMPLES / "old.bib"), str(EXAMPLES / "new.bib"), str(EXAMPLES / "notes"), "--out", str(out)]
    assert main(args) == 1
    assert capsys.readouterr().err.startswith("notewrench bibtex-keys: ")


def test_bundle_keeps_original_format_identity(tmp_path):
    import json
    out = tmp_path / "review"
    assert main(["plan", str(EXAMPLES / "old.bib"), str(EXAMPLES / "new.bib"), str(EXAMPLES / "notes"), "--out", str(out)]) == 0
    report = json.loads((out / "review.json").read_text(encoding="utf-8"))
    assert report["tool"] == "citekey-audit" and report["schema_version"] == 1


def test_parser_and_help_import_no_third_party_package():
    code = ("import sys; from notewrench.bibtexkeys import cli; cli.parser().format_help(); "
            "print(sorted(m for m in ('pybtex', 'markdown_it', 'mdit_py_plugins', 'pylatexenc', 'yaml') if m in sys.modules))")
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_module_entry_point_routes_through_dispatcher():
    result = subprocess.run([sys.executable, "-m", "notewrench", "bibtex-keys", "plan", "--help"],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert "usage: notewrench bibtex-keys plan" in result.stdout
