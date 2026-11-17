import json
from pathlib import Path
import shutil
import subprocess

import pytest

from notewrench.bibtexkeys.bibliography import InputError
from notewrench.bibtexkeys.cli import main
from notewrench.bibtexkeys.planner import make_plan, write_bundle
from notewrench.bibtexkeys.verify import verify_bundle

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def saved(tmp_path):
    notes = tmp_path / "notes"
    shutil.copytree(ROOT / "examples/bibtex-keys/notes", notes)
    (notes / "unaffected.md").write_text("No citations here.\n")
    report, patch = make_plan(ROOT / "examples/bibtex-keys/old.bib", ROOT / "examples/bibtex-keys/new.bib", notes)
    bundle = tmp_path / "review"
    write_bundle(bundle, report, patch)
    return notes, bundle


def test_verify_before_after_and_relocated_copy(saved, tmp_path):
    notes, bundle = saved
    assert verify_bundle(bundle)["status"] == "snapshot_matches"
    assert verify_bundle(bundle, after=True)["status"] == "stale_or_incomplete"
    copied = tmp_path / "copied"
    shutil.copytree(notes, copied)
    assert verify_bundle(bundle, copied)["status"] == "snapshot_matches"
    subprocess.run(["git", "init", "-q", str(copied)], check=True, timeout=10)
    subprocess.run(["git", "apply", str(bundle / "changes.patch")], cwd=copied, check=True, timeout=10)
    assert verify_bundle(bundle, copied, after=True)["status"] == "snapshot_matches"
    assert verify_bundle(bundle)["status"] == "snapshot_matches"


@pytest.mark.parametrize("change,expected", [("edit", "note_changed"), ("add", "new_note"), ("delete", "note_missing"), ("symlink", "symlink_file_skipped")])
def test_verify_checks_unaffected_notes_and_directory_membership(saved, change, expected):
    notes, bundle = saved
    path = notes / "unaffected.md"
    if change == "edit":
        path.write_text("Newly added @radler_integration_2022 citation.")
    elif change == "add":
        (notes / "added.md").write_text("@radler_integration_2022")
    elif change == "delete":
        path.unlink()
    else:
        path.unlink()
        path.symlink_to(notes / "systems.md")
    result = verify_bundle(bundle)
    assert result["status"] == "stale_or_incomplete"
    assert expected in {issue["kind"] for issue in result["issues"]}


def test_verify_rejects_edited_patch(saved):
    _, bundle = saved
    with (bundle / "changes.patch").open("ab") as stream:
        stream.write(b"\n")
    assert verify_bundle(bundle)["issues"][0]["kind"] == "patch_changed"


@pytest.mark.parametrize("path", ["../escape.md", "/absolute.md", "a/../escape.md", "./file.md", "", "a\nb.md", "bad-\ud800.md"])
def test_untrusted_manifest_cannot_walk_outside_notes(saved, path):
    _, bundle = saved
    report_path = bundle / "review.json"
    report = json.loads(report_path.read_text())
    report["note_manifest"][0]["path"] = path
    report_path.write_text(json.dumps(report))
    with pytest.raises(InputError):
        verify_bundle(bundle)


def test_verify_cli_reports_stale_snapshot_without_changing_it(saved, capsys):
    notes, bundle = saved
    assert main(["verify", str(bundle)]) == 0
    (notes / "unaffected.md").write_text("changed")
    assert main(["verify", str(bundle)]) == 2
    assert "stale_or_incomplete" in capsys.readouterr().out
    assert (notes / "unaffected.md").read_text() == "changed"


@pytest.mark.parametrize("field", ["status", "scan_issues", "note_manifest", "patch_sha256", "inputs", "options"])
def test_incomplete_bundle_returns_a_concise_input_error(saved, field):
    _, bundle = saved
    path = bundle / "review.json"
    report = json.loads(path.read_text())
    del report[field]
    path.write_text(json.dumps(report))
    with pytest.raises(InputError):
        verify_bundle(bundle)
    assert main(["verify", str(bundle)]) == 1


def test_bibliography_drift_is_stale_even_when_notes_are_unchanged(tmp_path):
    notes = tmp_path / 'notes'
    shutil.copytree(ROOT / 'examples/bibtex-keys/notes', notes)
    old, new = tmp_path / 'old.bib', tmp_path / 'new.bib'
    shutil.copyfile(ROOT / 'examples/bibtex-keys/old.bib', old)
    shutil.copyfile(ROOT / 'examples/bibtex-keys/new.bib', new)
    report, patch = make_plan(old, new, notes)
    bundle = tmp_path / 'review'
    write_bundle(bundle, report, patch)
    new.write_text(new.read_text().replace('Radler2022SysML', 'ChangedAgain2026'))
    result = verify_bundle(bundle)
    assert result['status'] == 'stale_or_incomplete'
    assert result['issues'][0]['kind'] == 'bibliography_changed'
    assert result['issues'][0]['snapshot'] == 'new'
    assert verify_bundle(bundle, notes_only=True)['status'] == 'snapshot_matches'
    assert verify_bundle(bundle, notes_only=True)['bibliographies_checked'] is False
    new.unlink()
    assert verify_bundle(bundle)['issues'][0]['kind'] == 'bibliography_unreadable'


@pytest.mark.parametrize('field', ['notes_root', 'old_bib', 'new_bib'])
@pytest.mark.parametrize('value', ['bad\ud800path', 'nul\x00path'])
def test_malformed_absolute_paths_are_input_errors_not_tracebacks(saved, field, value):
    _, bundle = saved
    path = bundle / 'review.json'
    report = json.loads(path.read_text())
    report['inputs'][field] = value
    path.write_text(json.dumps(report))
    assert main(['verify', str(bundle)]) == 1
