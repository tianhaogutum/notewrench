import json
import os

import pytest
from .test_workflow import html, selection, setup_index

from notewrench.relink.archive import ArchiveChangedError
from notewrench.relink.cli import main
from notewrench.relink.workflow import atomic_write, build_index, make_patch, scan_notes


def test_index_refuses_unrelated_existing_file(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    source = html(archive / "original.html", "https://old.example/story")
    before = source.read_bytes()
    with pytest.raises(ValueError, match="refusing to overwrite"):
        build_index(archive, source)
    assert source.read_bytes() == before


def test_scan_output_cannot_be_note_or_index(tmp_path, capsys):
    _, note, db = setup_index(tmp_path)
    for target in [note, db]:
        before = target.read_bytes()
        assert main(["scan", str(note), "--db", str(db), "--out", str(target)]) == 2
        assert "overwrite an input" in capsys.readouterr().err
        assert target.read_bytes() == before


def test_scan_output_cannot_be_archive_candidate(tmp_path):
    archive, note, db = setup_index(tmp_path)
    target = archive / "saved.html"
    before = target.read_bytes()
    assert main(["scan", str(note), "--db", str(db), "--out", str(target)]) == 2
    assert target.read_bytes() == before


def test_patch_output_cannot_be_note(tmp_path):
    _, note, db = setup_index(tmp_path)
    plan = scan_notes(db, [note])
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    chosen = next(iter(selection(plan).items()))
    before = note.read_bytes()
    assert (
        main(
            [
                "patch",
                str(plan_path),
                "--select",
                "=".join(chosen),
                "--base",
                str(tmp_path),
                "--out",
                str(note),
            ]
        )
        == 2
    )
    assert note.read_bytes() == before


def test_atomic_output_refuses_symlink(tmp_path):
    original = tmp_path / "original"
    original.write_text("private")
    link = tmp_path / "output"
    link.symlink_to(original)
    with pytest.raises(ValueError, match="symlink"):
        atomic_write(link, "overwrite")
    assert original.read_text() == "private"


def test_tampered_plan_cannot_patch_arbitrary_prose(tmp_path):
    _, note, db = setup_index(tmp_path)
    plan = scan_notes(db, [note])
    link = plan["links"][0]
    link.update(start=0, end=8, original=note.read_text()[:8])
    with pytest.raises(ValueError, match="not a current Markdown citation"):
        make_patch(plan, selection(plan), tmp_path)


def test_corrupt_warc_does_not_discard_usable_html(tmp_path):
    archive, note, db = setup_index(tmp_path)
    (archive / "bad.warc").write_bytes(b"not a WARC\n")
    result = build_index(archive, db)
    assert result["documents"] == 1
    assert any("cannot index" in w["message"] for w in result["warnings"])
    assert scan_notes(db, [note])["links"][0]["candidates"]


def test_failed_rebuild_preserves_previous_index(tmp_path, monkeypatch):
    archive, note, db = setup_index(tmp_path)
    before = db.read_bytes()

    def fail(*args):
        raise RuntimeError("simulated disk/parser failure")

    monkeypatch.setattr("notewrench.relink.workflow.documents", fail)
    with pytest.raises(RuntimeError, match="simulated"):
        build_index(archive, db)
    assert db.read_bytes() == before
    assert scan_notes(db, [note])["links"][0]["candidates"]


def test_plan_size_limit_leaves_previous_output_intact(tmp_path, monkeypatch):
    _, note, db = setup_index(tmp_path)
    note.write_text("[one](https://old.example/story)\n\n[two](https://old.example/story)\n")
    output = tmp_path / "plan.json"
    output.write_text("previous plan")
    monkeypatch.setattr("notewrench.relink.workflow.MAX_PLAN_LINKS", 1)
    assert main(["scan", str(note), "--db", str(db), "--out", str(output)]) == 2
    assert output.read_text() == "previous plan"


def test_metadata_sidecar_is_protected_as_output(tmp_path):
    archive, note, db = setup_index(tmp_path)
    metadata = archive / "index.json"
    # Register the existing HTML through ArchiveBox's canonical output map.
    metadata.write_text(
        json.dumps(
            {
                "url": "https://old.example/story",
                "canonical": {"singlefile_path": "saved.html"},
            }
        )
    )
    build_index(archive, db)
    before = metadata.read_bytes()
    assert main(["scan", str(note), "--db", str(db), "--out", str(metadata)]) == 2
    assert metadata.read_bytes() == before


def test_quote_lookup_and_patch_do_not_use_network(tmp_path, monkeypatch):
    def forbid_network(*args, **kwargs):
        raise AssertionError("runtime attempted network access")

    monkeypatch.setattr("socket.socket", forbid_network)
    _, note, db = setup_index(tmp_path)
    plan = scan_notes(db, [note])
    assert make_patch(plan, selection(plan), tmp_path)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named-pipe check")
def test_named_pipe_is_skipped_without_opening_it(tmp_path):
    archive, _, db = setup_index(tmp_path)
    os.mkfifo(archive / "pipe.html")
    result = build_index(archive, db)
    assert result["documents"] == 1
    assert any("non-regular" in w["message"] for w in result["warnings"])


def test_unreadable_subdirectory_is_reported(tmp_path, monkeypatch):
    archive, _, db = setup_index(tmp_path)
    blocked = archive / "blocked"
    blocked.mkdir()
    original_scandir = os.scandir

    def scandir(path):
        if str(path) == str(blocked):
            raise PermissionError(13, "simulated permission denial", str(path))
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    result = build_index(archive, db)
    assert result["documents"] == 1
    assert any("cannot read directory" in w["message"] for w in result["warnings"])


def test_content_changed_between_parse_and_hash_aborts_atomic_rebuild(tmp_path, monkeypatch):
    archive, _, db = setup_index(tmp_path)
    before = db.read_bytes()
    from notewrench.relink.archive import documents as real_documents

    def changing_documents(*args):
        for doc in real_documents(*args):
            doc.target.write_bytes(doc.target.read_bytes() + b"<p>concurrent edit</p>")
            yield doc

    monkeypatch.setattr("notewrench.relink.workflow.documents", changing_documents)
    with pytest.raises(ArchiveChangedError, match="changed while indexing"):
        build_index(archive, db)
    assert db.read_bytes() == before


def test_metadata_changed_after_parse_aborts_atomic_rebuild(tmp_path, monkeypatch):
    archive, _, db = setup_index(tmp_path)
    metadata = archive / "index.json"
    metadata.write_text(
        json.dumps(
            {
                "url": "https://old.example/story",
                "canonical": {"dom_path": "saved.html"},
            }
        )
    )
    build_index(archive, db)
    before = db.read_bytes()
    from notewrench.relink.archive import _metadata_records as real_records

    def changing_metadata(path, warn):
        for record in real_records(path, warn):
            yield record
            path.write_text("{}")

    monkeypatch.setattr("notewrench.relink.archive._metadata_records", changing_metadata)
    with pytest.raises(ArchiveChangedError, match="metadata changed"):
        build_index(archive, db)
    assert db.read_bytes() == before


def test_explicit_selection_file_generates_only_selected_patch(tmp_path):
    _, note, db = setup_index(tmp_path)
    plan = scan_notes(db, [note])
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    choices = tmp_path / "choices.json"
    chosen = selection(plan)
    choices.write_text(json.dumps(chosen))
    output = tmp_path / "repair.patch"
    assert (
        main(
            [
                "patch",
                str(plan_path),
                "--selections",
                str(choices),
                "--base",
                str(tmp_path),
                "--out",
                str(output),
            ]
        )
        == 0
    )
    assert "../archive/saved.html" in output.read_text()
    assert "https://old.example/story" in note.read_text()


def test_all_null_selection_file_cannot_approve_any_candidate(tmp_path):
    _, note, db = setup_index(tmp_path)
    plan = scan_notes(db, [note])
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    choices = tmp_path / "choices.json"
    choices.write_text(json.dumps({plan["links"][0]["id"]: None}))
    output = tmp_path / "repair.patch"
    assert (
        main(
            [
                "patch",
                str(plan_path),
                "--selections",
                str(choices),
                "--base",
                str(tmp_path),
                "--out",
                str(output),
            ]
        )
        == 2
    )
    assert not output.exists()


def test_duplicate_json_selection_cannot_silently_override_choice(tmp_path, capsys):
    _, note, db = setup_index(tmp_path)
    plan = scan_notes(db, [note])
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan))
    link, candidate = next(iter(selection(plan).items()))
    choices = tmp_path / "choices.json"
    choices.write_text('{"' + link + '":"' + candidate + '","' + link + '":null}')
    output = tmp_path / "repair.patch"
    assert (
        main(
            [
                "patch",
                str(plan_path),
                "--selections",
                str(choices),
                "--base",
                str(tmp_path),
                "--out",
                str(output),
            ]
        )
        == 2
    )
    assert "duplicate JSON key" in capsys.readouterr().err
    assert not output.exists()


def test_byte_budget_preserves_previous_plan(tmp_path, monkeypatch):
    _, note, db = setup_index(tmp_path)
    output = tmp_path / "plan.json"
    output.write_text("previous plan")
    monkeypatch.setattr("notewrench.relink.workflow.MAX_PLAN_BYTES", 128)
    assert main(["scan", str(note), "--db", str(db), "--out", str(output)]) == 2
    assert output.read_text() == "previous plan"


def test_failed_review_write_preserves_previous_authoritative_plan(tmp_path, monkeypatch):
    _, note, db = setup_index(tmp_path)
    output = tmp_path / "plan.json"
    output.write_text("previous plan")
    review = tmp_path / "review.md"
    review.write_text("previous review")
    from notewrench.relink.workflow import atomic_write as real_write

    def fail_review(path, content):
        if path == review:
            raise OSError("simulated disk-full review write")
        real_write(path, content)

    monkeypatch.setattr("notewrench.relink.cli.atomic_write", fail_review)
    assert (
        main(
            [
                "scan",
                str(note),
                "--db",
                str(db),
                "--out",
                str(output),
                "--review",
                str(review),
            ]
        )
        == 2
    )
    assert output.read_text() == "previous plan"
    assert review.read_text() == "previous review"


def test_note_warning_accumulation_is_bounded(tmp_path):
    _, note, db = setup_index(tmp_path)
    note.write_text("[unsupported](https://user:password@example.org/private)\n\n" * 1005)
    plan = scan_notes(db, [note])
    assert plan["links"] == []
    assert len(plan["warnings"]) == 1001
    assert "5 further note warnings suppressed" in plan["warnings"][-1]["message"]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX named-pipe check")
def test_atomic_output_does_not_replace_special_file(tmp_path):
    import stat

    output = tmp_path / "output.pipe"
    os.mkfifo(output)
    with pytest.raises(ValueError, match="not a regular file"):
        atomic_write(output, "patch text")
    assert stat.S_ISFIFO(output.stat().st_mode)


def test_unrelated_sqlite_schema_one_is_not_mistaken_for_legacy_index(tmp_path):
    import sqlite3

    archive = tmp_path / "archive"
    archive.mkdir()
    html(archive / "saved.html", "https://old.example/story")
    database = tmp_path / "unrelated.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE meta (key TEXT, value TEXT)")
        connection.execute("INSERT INTO meta VALUES ('schema','1')")
    before = database.read_bytes()
    with pytest.raises(ValueError, match="refusing to overwrite"):
        build_index(archive, database)
    assert database.read_bytes() == before
