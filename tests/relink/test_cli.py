"""``notewrench relink`` program name, version, sub-command help and error prefix."""
import pytest

import notewrench
from notewrench.relink.cli import main


def test_version_uses_notewrench_name(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])
    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"notewrench relink {notewrench.__version__}\n"


@pytest.mark.parametrize("command", [None, "index", "scan", "patch", "doctor"])
def test_help_uses_notewrench_name(command, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main([*([command] if command else []), "--help"])
    assert exit_info.value.code == 0
    assert f"usage: notewrench relink{' ' + command if command else ''}" in capsys.readouterr().out


def test_errors_use_notewrench_prefix(tmp_path, capsys):
    note = tmp_path / "note.md"
    note.write_text("[a](https://old.example/a)\n")
    assert main(["scan", str(note), "--db", str(tmp_path / "missing.sqlite"),
                 "--out", str(tmp_path / "plan.json")]) == 2
    assert capsys.readouterr().err.startswith("notewrench relink: ")


def test_doctor_reports_sqlite_and_packages(capsys):
    import json

    assert main(["doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["fts5_trigram"] is True
    assert set(report["packages"]) == {"beautifulsoup4", "idna", "markdown-it-py", "mdit-py-plugins", "warcio"}
