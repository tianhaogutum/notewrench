import json
from pathlib import Path
import shutil
import subprocess

import pytest

from notewrench.bibtexkeys.cli import main
from notewrench.bibtexkeys.planner import Options, digest, make_plan, unified_patch, write_bundle

ROOT = Path(__file__).resolve().parents[2]


def test_real_export_based_example_is_read_only_and_patch_applies(tmp_path):
    notes = tmp_path / "notes"
    shutil.copytree(ROOT / "examples/bibtex-keys/notes", notes)
    before = (notes / "systems.md").read_bytes()
    report, patch = make_plan(ROOT / "examples/bibtex-keys/old.bib", ROOT / "examples/bibtex-keys/new.bib", notes)
    assert report["status"] == "ready"
    assert report["summary"]["replacements"] == 4
    assert (notes / "systems.md").read_bytes() == before
    out = tmp_path / "review"
    write_bundle(out, report, patch)
    subprocess.run(["git", "init", "-q", str(notes)], check=True, timeout=10)
    subprocess.run(["git", "apply", "--check", str(out / "changes.patch")], cwd=notes, check=True, timeout=10)
    subprocess.run(["git", "apply", str(out / "changes.patch")], cwd=notes, check=True, timeout=10)
    after = (notes / "systems.md").read_bytes()
    assert digest(after) == report["files"][0]["sha256_after"]
    assert b"`@radler_integration_2022`" in after
    assert json.loads((out / "review.json").read_text()) == report


@pytest.mark.parametrize("filename", ["space name.md", 'quote"name.md', "中文笔记.md", "back\\slash.md"])
@pytest.mark.parametrize("old", ["@Old", "@Old\n", "\ufeff@Old\r\nother\r\n", "Text\u2028@Old\n"])
def test_patch_round_trip_file_names_line_endings_and_missing_newline(tmp_path, filename, old):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=10)
    path = tmp_path / filename
    path.write_bytes(old.encode())
    new = old.replace("Old", "New")
    patch_path = tmp_path / "change.patch"
    patch_path.write_bytes(unified_patch(old, new, filename).encode())
    subprocess.run(["git", "apply", "--check", str(patch_path)], cwd=tmp_path, check=True, timeout=10)
    subprocess.run(["git", "apply", str(patch_path)], cwd=tmp_path, check=True, timeout=10)
    assert path.read_bytes() == new.encode()


def test_invalid_notes_symlinks_and_limits_are_explicit(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "bad.md").write_bytes(b"\xff")
    (notes / "too-big.md").write_text("@Old" * 10)
    (notes / "link.md").symlink_to(notes / "too-big.md")
    report, patch = make_plan(ROOT / "examples/bibtex-keys/old.bib", ROOT / "examples/bibtex-keys/new.bib", notes, Options(max_note_bytes=10))
    assert report["status"] == "review_required"
    assert {issue["kind"] for issue in report["scan_issues"]} == {"unreadable_note", "symlink_file_skipped"}
    assert patch == ""


def test_cli_success_conflicts_and_existing_output(tmp_path, capsys):
    out = tmp_path / "review"
    args = ["plan", str(ROOT / "examples/bibtex-keys/old.bib"), str(ROOT / "examples/bibtex-keys/new.bib"), str(ROOT / "examples/bibtex-keys/notes"), "--out", str(out)]
    assert main(args) == 0
    before = (out / "review.json").read_bytes()
    assert main(args) == 1
    assert (out / "review.json").read_bytes() == before
    args[1] = str(ROOT / "tests/bibtexkeys/fixtures/upstream/bbt-sample.bib")
    args[-1] = str(tmp_path / "missing-identity")
    assert main(args) == 2
    assert "review_required" in capsys.readouterr().out


def test_no_partial_bundle_on_invalid_bibtex(tmp_path):
    invalid = tmp_path / "bad.bib"
    invalid.write_text("@article{bad")
    out = tmp_path / "review"
    assert main(["plan", str(invalid), str(ROOT / "examples/bibtex-keys/new.bib"), str(ROOT / "examples/bibtex-keys/notes"), "--out", str(out)]) == 1
    assert not out.exists()


def test_output_cannot_change_the_notes_input_tree(tmp_path):
    notes = tmp_path / 'notes'
    shutil.copytree(ROOT / 'examples/bibtex-keys/notes', notes)
    out = notes / 'review'
    args = ['plan', str(ROOT / 'examples/bibtex-keys/old.bib'), str(ROOT / 'examples/bibtex-keys/new.bib'), str(notes), '--out', str(out)]
    assert main(args) == 1
    assert not out.exists()


def test_empty_or_wrong_extension_directory_does_not_claim_complete_coverage(tmp_path):
    notes = tmp_path / 'notes'
    notes.mkdir()
    (notes / 'reading.txt').write_text('@Old')
    report, patch = make_plan(ROOT / 'examples/bibtex-keys/old.bib', ROOT / 'examples/bibtex-keys/new.bib', notes)
    assert report['status'] == 'review_required'
    assert report['scan_issues'][0]['kind'] == 'no_notes_matched'
    assert not patch


def test_special_files_do_not_hang_or_break_bundle(tmp_path):
    import os
    notes = tmp_path / 'notes'
    notes.mkdir()
    os.mkfifo(notes / 'pipe.md')
    report, patch = make_plan(ROOT / 'examples/bibtex-keys/old.bib', ROOT / 'examples/bibtex-keys/new.bib', notes)
    assert 'nonregular_file_skipped' in {issue['kind'] for issue in report['scan_issues']}
    write_bundle(tmp_path / 'review', report, patch)
    assert (tmp_path / 'review/review.json').is_file()


def test_surrogate_filename_is_reported_as_printable_text(tmp_path, monkeypatch):
    # APFS refuses such filenames; simulate os.walk's Linux surrogateescape
    # result to verify the report can still be written without encoding errors.
    notes = tmp_path / 'notes'
    notes.mkdir()
    monkeypatch.setattr('notewrench.bibtexkeys.planner.os.walk', lambda *a, **kw: iter([(str(notes), [], ['invalid-\udcff.md'])]))
    report, patch = make_plan(ROOT / 'examples/bibtex-keys/old.bib', ROOT / 'examples/bibtex-keys/new.bib', notes)
    assert report['scan_issues'][0] == {'kind': 'unsafe_path_skipped', 'path': 'invalid-\\udcff.md'}
    write_bundle(tmp_path / 'review', report, patch)
    assert (tmp_path / 'review/review.json').is_file()


def test_report_limit_failure_cleans_staging_without_touching_inputs(tmp_path, monkeypatch):
    from notewrench.bibtexkeys.bibliography import InputError
    notes = tmp_path / 'notes'
    shutil.copytree(ROOT / 'examples/bibtex-keys/notes', notes)
    before = (notes / 'systems.md').read_bytes()
    report, patch = make_plan(ROOT / 'examples/bibtex-keys/old.bib', ROOT / 'examples/bibtex-keys/new.bib', notes)
    monkeypatch.setattr('notewrench.bibtexkeys.planner.MAX_REVIEW_BYTES', 100)
    with pytest.raises(InputError, match='lower --max-occurrences'):
        write_bundle(tmp_path / 'review', report, patch)
    assert not (tmp_path / 'review').exists()
    assert not list(tmp_path.glob('.citekey-audit-*'))
    assert (notes / 'systems.md').read_bytes() == before


def test_human_review_has_locations_and_escapes_markdown_file_names(tmp_path):
    from notewrench.bibtexkeys.planner import review_markdown
    from markdown_it import MarkdownIt
    notes = tmp_path / 'notes'
    notes.mkdir()
    (notes / '<script>.md').write_text('@radler_integration_2022')
    report, _ = make_plan(ROOT / 'examples/bibtex-keys/old.bib', ROOT / 'examples/bibtex-keys/new.bib', notes)
    markdown = review_markdown(report)
    assert 'Affected locations' in markdown
    assert '<script>.md:1:2' in markdown
    html = MarkdownIt('commonmark').render(markdown)
    assert '<script>' not in html
    assert '&lt;script&gt;' in html


def test_unsafe_directory_name_is_pruned_with_a_printable_issue(tmp_path, monkeypatch):
    notes = tmp_path / 'notes'
    notes.mkdir()
    directories = ['invalid-\udcff']
    monkeypatch.setattr('notewrench.bibtexkeys.planner.os.walk', lambda *a, **kw: iter([(str(notes), directories, [])]))
    report, patch = make_plan(ROOT / 'examples/bibtex-keys/old.bib', ROOT / 'examples/bibtex-keys/new.bib', notes)
    assert directories == []
    assert report['scan_issues'][0] == {'kind': 'unsafe_path_skipped', 'path': 'invalid-\\udcff'}
    write_bundle(tmp_path / 'review', report, patch)


def test_control_characters_in_input_root_are_rejected_before_planning(tmp_path):
    from notewrench.bibtexkeys.bibliography import InputError
    notes = tmp_path / 'notes\nroot'
    notes.mkdir()
    with pytest.raises(InputError, match='input paths'):
        make_plan(ROOT / 'examples/bibtex-keys/old.bib', ROOT / 'examples/bibtex-keys/new.bib', notes)
