"""Top-level `notewrench` dispatcher: overview, version, routing and error paths."""
import subprocess
import sys

import pytest

import notewrench
from notewrench import cli

SUBCOMMANDS = {
    "doctor": [],
    "zotero-logseq": [],
    "joplin-logseq": [],
    "wallabag-notes": [],
    "archive": ["rescue", "k2w", "api-import", "verify"],
    "epub": ["build", "plan", "validate"],
    "links": [],
    "joplin-dupes": [],
    "calibre-dupes": [],
    "citekeys": [],
    "zotero-attachments": ["compare"],
}
# Single-module subpackages: `python -m notewrench.<subpackage>` must route through the dispatcher.
SUBPACKAGES = {
    "doctor": "notewrench.doctor",
    "zotero-logseq": "notewrench.zoterologseq", "joplin-logseq": "notewrench.joplinlogseq",
    "wallabag-notes": "notewrench.wallabagnotes", "archive": "notewrench.archive", "epub": "notewrench.epub",
    "links": "notewrench.linkdedupe", "joplin-dupes": "notewrench.joplindupes",
    "calibre-dupes": "notewrench.calibredupes", "citekeys": "notewrench.citekeys",
    "zotero-attachments": "notewrench.zoteroattachments",
}
ALL_HELP = [(name, None) for name in SUBCOMMANDS] + [(name, sub) for name, subs in SUBCOMMANDS.items() for sub in subs]


def run(*args, module=("-m", "notewrench")):
    # Generous timeout: cold interpreter starts on macOS runners can be slow.
    return subprocess.run([sys.executable, *module, *map(str, args)], capture_output=True, text=True, timeout=120)


def require(name):
    """Skip commands whose subpackage is not part of this build (yet)."""
    if not cli._installed(cli.BY_NAME[name]):
        pytest.skip(f"notewrench {name} is not shipped in this build")


def _main(argv, capsys):
    try:
        code = cli.main(argv)
    except SystemExit as exc:
        code = exc.code
    captured = capsys.readouterr()
    return code, captured.out, captured.err


@pytest.mark.parametrize("args", [[], ["--help"], ["-h"], ["help"]])
def test_overview_lists_every_group_and_command(args, capsys):
    assert cli.main(args) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"notewrench {notewrench.__version__} ")
    for group in cli.GROUPS:
        assert f"\n{group}:\n" in out
    for command in cli.COMMANDS:
        assert f"\n  {command.name} " in out


def test_command_table_shape():
    groups = {group: [c.name for c in cli.COMMANDS if c.group == group] for group in cli.GROUPS}
    assert groups == {
        "Check": ["doctor"],
        "Migrate & sync notes": ["zotero-logseq", "joplin-logseq", "wallabag-notes"],
        "Read-later archives": ["archive", "epub"],
        "Duplicates": ["links", "joplin-dupes", "calibre-dupes"],
        "Citations & attachments": ["citekeys", "zotero-attachments"],
    }
    # The health check is the entry point: listed first, in its own group.
    assert cli.GROUPS[0] == "Check" and cli.COMMANDS[0].name == "doctor"
    assert {c.name: c.module for c in cli.COMMANDS} == {name: f"{pkg}.cli" for name, pkg in SUBPACKAGES.items()}
    assert [c.name for c in cli.COMMANDS if c.linux_only] == ["archive", "zotero-attachments"]
    assert all(c.group in cli.GROUPS and c.summary for c in cli.COMMANDS)
    assert len(cli.BY_NAME) == len(cli.COMMANDS)
    # calibre-dupes kept its standalone name and doctor is new; every other command has a former name.
    assert set(cli.LEGACY.values()) == set(cli.BY_NAME) - {"calibre-dupes", "doctor"}
    assert not set(cli.LEGACY) & set(cli.BY_NAME)


def test_subcommand_table_covers_every_command():
    assert set(SUBCOMMANDS) == set(cli.BY_NAME) == set(SUBPACKAGES)


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version(flag, capsys):
    assert cli.main([flag]) == 0
    assert capsys.readouterr().out == f"notewrench {notewrench.__version__}\n"


@pytest.mark.parametrize("tool", list(SUBCOMMANDS))
def test_tool_help_uses_new_program_name(tool):
    require(tool)
    result = run(tool, "--help")
    assert result.returncode == 0, result.stderr
    assert f"usage: notewrench {tool}" in result.stdout


@pytest.mark.parametrize("tool", list(SUBCOMMANDS))
def test_tool_version_uses_new_program_name(tool):
    require(tool)
    result = run(tool, "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith(f"notewrench {tool} "), result.stdout


@pytest.mark.parametrize("tool", list(SUBCOMMANDS))
def test_help_subcommand_routes_to_tool_help(tool, capsys):
    require(tool)
    code, out, err = _main(["help", tool], capsys)
    assert code == 0, err
    assert f"usage: notewrench {tool}" in out


def test_tool_exit_codes_pass_through():
    require("epub")
    bad = run("epub", "no-such-subcommand")
    assert bad.returncode == 2 and "usage: notewrench epub" in bad.stderr


def test_unknown_command_suggests_close_match(capsys):
    assert cli.main(["zotero-logsec"]) == 2
    err = capsys.readouterr().err
    assert "unknown command 'zotero-logsec'" in err and "did you mean 'zotero-logseq'?" in err


def test_close_match_to_legacy_name_suggests_new_command(capsys):
    assert cli.main(["jex2logsec"]) == 2
    assert "did you mean 'joplin-logseq'?" in capsys.readouterr().err


@pytest.mark.parametrize("old,new", sorted(cli.LEGACY.items()))
def test_unknown_legacy_name_points_to_new_command(old, new, capsys):
    assert cli.main([old]) == 2
    assert f"{old} is now 'notewrench {new}'" in capsys.readouterr().err


def test_unknown_command_without_match(capsys):
    assert cli.main(["zzzzzz"]) == 2
    err = capsys.readouterr().err
    assert "unknown command 'zzzzzz'" in err and "did you mean" not in err


def test_unknown_option(capsys):
    assert cli.main(["--frobnicate"]) == 2
    assert "unknown option '--frobnicate'" in capsys.readouterr().err


def test_missing_module_reports_not_available(monkeypatch, capsys):
    ghost = cli.Command("ghost", "notewrench.ghost_tool.cli", "not shipped", "Duplicates")
    monkeypatch.setitem(cli.BY_NAME, "ghost", ghost)
    monkeypatch.setattr(cli, "COMMANDS", (*cli.COMMANDS, ghost))
    assert "ghost" in cli.overview() and "(coming soon)" in cli.overview()
    assert cli.main(["ghost", "--help"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("notewrench: command 'ghost' is not available") and "Traceback" not in err


def test_subpackage_without_cli_module_reports_not_available(monkeypatch, capsys):
    # The package exists but its cli module does not (e.g. a tool still being ported).
    ghost = cli.Command("ghost", "notewrench.no_such_cli", "half shipped", "Duplicates")
    monkeypatch.setitem(cli.BY_NAME, "ghost", ghost)
    monkeypatch.setattr(cli, "_installed", lambda command: True)
    assert cli.main(["ghost"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("notewrench: command 'ghost' is not available") and "Traceback" not in err


@pytest.mark.parametrize("platform,noted", [("linux", False), ("darwin", True)])
def test_linux_only_commands_are_marked_elsewhere(monkeypatch, platform, noted):
    monkeypatch.setattr(cli.sys, "platform", platform)
    lines = cli.overview().splitlines()
    for command in cli.COMMANDS:
        if not cli._installed(command):
            continue
        (line,) = [line for line in lines if line.startswith(f"  {command.name} ")]
        assert line.endswith("(Linux only)") == (noted and command.linux_only), line


@pytest.mark.parametrize("tool", list(SUBPACKAGES))
def test_subpackage_main_routes_through_dispatcher(tool):
    require(tool)
    result = run("--help", module=("-m", SUBPACKAGES[tool]))
    assert result.returncode == 0, result.stderr
    assert f"usage: notewrench {tool}" in result.stdout


@pytest.mark.parametrize("flag", ["--help", "-h"])
@pytest.mark.parametrize("command,sub", ALL_HELP, ids=lambda v: v or "-")
def test_every_command_and_subcommand_help_exits_0(command, sub, flag, capsys):
    require(command)
    argv = [command, *([sub] if sub else []), flag]
    code, out, err = _main(argv, capsys)
    assert code == 0, (argv, err)
    assert "usage" in out.lower(), (argv, out)
    if sub:
        assert f"notewrench {command} {sub}" in out, (argv, out)
