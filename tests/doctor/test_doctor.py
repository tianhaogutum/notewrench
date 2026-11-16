# SPDX-License-Identifier: AGPL-3.0-only
"""`notewrench doctor`: detection, verdicts, top fixes, exit codes and read-only behavior."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

import pytest

from notewrench.doctor import SCHEMA, UsageError, detect
from notewrench.doctor import cli

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "examples" / "doctor"
LINUX = sys.platform.startswith("linux")
linux_only = pytest.mark.skipif(not LINUX, reason="Zotero attachment audits need Linux (/proc/self/fd)")


def run(argv, capsys):
    code = cli.main([str(a) for a in argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def doctor_json(argv, capsys):
    code, out, err = run([*argv, "--format", "json"], capsys)
    return code, json.loads(out)


def by_check(doc):
    return {record["check"]: record for record in doc["sources"]}


def tree_digest(root: Path) -> dict:
    return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def demo(tmp_path):
    """A private copy of examples/doctor (some commands under test create output files)."""
    copy = tmp_path / "examples" / "doctor"
    shutil.copytree(DEMO, copy)
    return copy


# --- detection ---------------------------------------------------------------------

def test_detects_every_bundled_source_kind():
    scan = detect([str(DEMO)])
    kinds = sorted((s.kind, Path(s.path).relative_to(DEMO).as_posix()) for s in scan.sources)
    assert kinds == [
        ("bibliography", "thesis/references-old.json"),
        ("bibliography", "thesis/references.json"),
        ("calibre", "calibre"),
        ("joplin", "joplin-export.jex"),
        ("markdown", "thesis/chapter-1.md"),
        ("markdown", "thesis/reading-notes.md"),
        ("readlater", "read-later/karakeep.json"),
        ("readlater", "read-later/wallabag.json"),
        ("zotero", "zotero"),
    ]
    assert scan.ignored == 1  # make_calibre.py
    zotero = next(s for s in scan.sources if s.kind == "zotero")
    assert zotero.snapshot.endswith("zotero.sqlite") and zotero.storage.endswith("storage")


@pytest.mark.parametrize("relative,kind", [
    ("zotero", "zotero"), ("zotero/zotero.sqlite", "zotero"), ("calibre", "calibre"),
    ("calibre/metadata.db", "calibre"), ("joplin-export.jex", "joplin"),
    ("read-later/karakeep.json", "readlater"), ("read-later/wallabag.json", "readlater"),
    ("thesis/references.json", "bibliography"), ("thesis/chapter-1.md", "markdown"),
])
def test_explicit_paths(relative, kind):
    (source,) = detect([str(DEMO / relative)]).sources
    assert source.kind == kind


def test_joplin_raw_folder_and_formats():
    (raw,) = detect([str(ROOT / "examples" / "joplin-dupes" / "raw")]).sources
    assert raw.kind == "joplin"
    formats = {s.format for s in detect([str(DEMO / "read-later")]).sources}
    assert formats == {"karakeep", "wallabag-v2"}


def test_jex_under_another_name_is_detected_when_explicit(tmp_path):
    renamed = tmp_path / "backup.tar"
    shutil.copyfile(DEMO / "joplin-export.jex", renamed)
    (source,) = detect([str(renamed)]).sources
    assert source.kind == "joplin"


@pytest.mark.parametrize("make", [
    lambda d: d / "missing.json",
    lambda d: (d / "notes.txt").write_text("hello") and d / "notes.txt",
    lambda d: (d / "package.json").write_text('{"name": "x"}') and d / "package.json",
    lambda d: (d / "empty").mkdir() or d / "empty",
])
def test_unrecognized_or_missing_paths_exit_2(tmp_path, make, capsys):
    path = make(tmp_path)
    with pytest.raises(UsageError):
        detect([str(path)])
    code, out, err = run([path], capsys)
    assert code == 2 and out == ""
    assert f"notewrench doctor: {path}" in err and "--help" in err


def test_same_path_twice_is_a_usage_error(capsys):
    code, _, err = run([DEMO / "calibre", DEMO / "calibre"], capsys)
    assert code == 2 and "given more than once" in err


def test_scan_skips_hidden_entries_and_symlinks(tmp_path):
    (tmp_path / ".cache").mkdir()
    shutil.copyfile(DEMO / "read-later" / "wallabag.json", tmp_path / ".cache" / "wallabag.json")
    shutil.copyfile(DEMO / "read-later" / "karakeep.json", tmp_path / "karakeep.json")
    try:
        os.symlink(DEMO / "read-later" / "wallabag.json", tmp_path / "linked.json")
    except OSError:
        pytest.skip("symlinks unavailable")
    scan = detect([str(tmp_path)])
    assert [Path(s.path).name for s in scan.sources] == ["karakeep.json"]
    assert scan.ignored == 1


def test_usage_errors_exit_2(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2
    with pytest.raises(SystemExit) as exc:
        cli.main([str(DEMO), "--fail-on", "sometimes"])
    assert exc.value.code == 2


# --- the bundled demo --------------------------------------------------------------

def test_demo_counts_per_source(capsys):
    code, doc = doctor_json([DEMO], capsys)
    assert doc["schema"] == SCHEMA and doc["read_only"] is True
    checks = by_check(doc)
    assert list(checks) == ["zotero", "joplin", "calibre", "links", "citations"]
    joplin = checks["joplin"]["counts"]
    assert (joplin["notes"], joplin["exact_groups"], joplin["duplicate_notes"], joplin["blocked_notes"]) == (3, 1, 1, 0)
    calibre = checks["calibre"]["counts"]
    assert (calibre["books"], calibre["strong_clusters"], calibre["candidate_clusters"],
            calibre["invalid_isbn"], calibre["missing_author"]) == (7, 1, 1, 1, 1)
    links = checks["links"]["counts"]
    assert (links["exports"], links["bookmarks"], links["clusters"], links["clusters_across_exports"]) == (2, 4, 1, 1)
    citations = checks["citations"]["counts"]
    assert (citations["documents"], citations["bibliographies"], citations["unknown_citations"],
            citations["stale_citations"], citations["unsupported_citations"]) == (2, 2, 1, 7, 2)
    assert code == 1  # the unknown citekey is an error on every platform


@linux_only
def test_demo_zotero_audit(capsys):
    _, doc = doctor_json([DEMO / "zotero"], capsys)
    zotero = by_check(doc)["zotero"]
    assert zotero["status"] == "findings"
    counts = zotero["counts"]
    assert (counts["attachments"], counts["missing"], counts["duplicate_sets"], counts["orphan_folders"]) == (7, 1, 1, 1)
    assert counts["duplicate_extra_bytes"] == 11 and counts["orphan_bytes"] == 22
    assert "1 missing file" in zotero["verdict"]
    (fix,) = zotero["fixes"]
    assert fix["severity"] == "error" and "STUVWXYZ/missing.pdf" in fix["detail"]


def test_zotero_is_skipped_where_audits_are_unsupported(monkeypatch, capsys):
    real = os.path.isdir
    monkeypatch.setattr(os.path, "isdir", lambda p: False if p == "/proc/self/fd" else real(p))
    code, doc = doctor_json([DEMO / "zotero"], capsys)
    zotero = by_check(doc)["zotero"]
    assert zotero["status"] == "skipped" and "Linux" in zotero["reason"]
    assert zotero["verdict"].startswith("SKIPPED: ") and zotero["fixes"] == []
    assert code == 0 and doc["summary"]["skipped"] == 1


def test_text_report_lists_verdicts_and_top_fixes(capsys):
    code, out, _ = run([DEMO, "--no-color"], capsys)
    assert out.startswith("notewrench doctor  5 checks · read-only, nothing is written\n")
    assert "\x1b[" not in out
    for label in ("Joplin", "Calibre", "Read-later", "Citations"):
        assert f"\n  ! {label} " in out or f"\n  ✗ {label} " in out
    assert "\nTop fixes\n  1. " in out
    assert "notewrench citekeys --old " in out and "--dry-run" in out
    assert "--fail-on error: FAIL (exit 1)" in out and code == 1


def test_fix_ordering_errors_first(capsys):
    _, doc = doctor_json([DEMO], capsys)
    ranks = [{"error": 0, "warning": 1, "note": 2}[fix["severity"]] for fix in doc["top_fixes"]]
    assert ranks == sorted(ranks) and ranks[0] == 0


@pytest.mark.parametrize("fail_on,code", [("none", 0), ("error", 0), ("warning", 1), ("note", 1)])
def test_fail_on_threshold(fail_on, code, capsys):
    # The Joplin export has warnings only.
    got, doc = doctor_json([DEMO / "joplin-export.jex", "--fail-on", fail_on], capsys)
    assert got == code and doc["summary"]["fail_on"] == fail_on


def test_clean_input_exits_0(tmp_path, capsys):
    shutil.copyfile(DEMO / "read-later" / "karakeep.json", tmp_path / "karakeep.json")
    code, out, _ = run([tmp_path / "karakeep.json", "--no-color", "--fail-on", "note"], capsys)
    assert code == 0 and "no problems found" in out and "Nothing to fix." in out


def test_markdown_without_bibliography_is_skipped(capsys):
    code, doc = doctor_json([DEMO / "thesis" / "chapter-1.md"], capsys)
    citations = by_check(doc)["citations"]
    assert citations["status"] == "skipped" and "CSL-JSON" in citations["reason"] and code == 0


def test_identical_bibliography_copies_do_not_hide_renamed_keys(demo, capsys):
    shutil.copyfile(demo / "thesis" / "references-old.json", demo / "thesis" / "references-old-copy.json")
    _, doc = doctor_json([demo / "thesis"], capsys)
    assert by_check(doc)["citations"]["counts"]["stale_citations"] == 7


def test_unreadable_source_exits_2(demo, capsys):
    (demo / "calibre" / "metadata.db-wal").write_bytes(b"")
    code, doc = doctor_json([demo / "calibre"], capsys)
    calibre = by_check(doc)["calibre"]
    assert calibre["status"] == "error" and calibre["verdict"].startswith("could not check: ")
    assert code == 2 and doc["summary"]["exit_status"] == 2


# --- read-only, and the suggested commands work ------------------------------------

def test_doctor_writes_nothing(demo, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    before = tree_digest(tmp_path)
    run([demo], capsys)
    run([demo, "--format", "json"], capsys)
    assert tree_digest(tmp_path) == before


def test_suggested_commands_run(demo, monkeypatch, capsys):
    workdir = demo.parent.parent
    monkeypatch.chdir(workdir)
    _, doc = doctor_json(["examples/doctor"], capsys)
    commands = [command for fix in doc["top_fixes"] for command in fix["commands"]]
    assert any(c.startswith("notewrench calibre-dupes ") for c in commands)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(ROOT / "src"), os.environ.get("PYTHONPATH")]))}
    for command in commands:
        argv, _, redirect = command.partition(" > ")
        args = shlex.split(argv)
        assert args[0] == "notewrench"
        result = subprocess.run([sys.executable, "-m", "notewrench", *args[1:]], cwd=workdir, env=env,
                                capture_output=True, text=True, timeout=120)
        # citekeys exits 1 while an unknown key remains unresolved; everything else succeeds.
        expected = 1 if args[1] == "citekeys" else 0
        assert result.returncode == expected, (command, result.stderr)
        if redirect:
            (workdir / redirect).write_text(result.stdout, encoding="utf-8")
    assert (workdir / "calibre-review.json").is_file() and (workdir / "links-review").is_dir()
