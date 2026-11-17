"""Real importer/exporter and scheduler integration in disposable collections."""

import json
from importlib.metadata import version
from pathlib import Path

import pytest

from notewrench.ankimerge.cli import main
from notewrench.ankimerge.text import read_deck


def add_model(c, names=("Front", "Back", "CourseID", "Personal")):
    model = c.models.new("Course Facts")
    for name in names:
        c.models.add_field(model, c.models.new_field(name))
    card = c.models.new_template("Card 1")
    card["qfmt"], card["afmt"] = "{{Front}}", "{{FrontSide}}<hr>{{Back}}"
    c.models.add_template(model, card)
    c.models.add(model)
    return model


def args(project, out, variant="."):
    paths = [str(project / "examples" / "anki-merge" / ("fields-only" if role != "student" else ".") / f"{role}.txt")
             for role in ("old", "new", "student")] if variant == "mixed" else [
                 str(project / "examples" / "anki-merge" / variant / f"{role}.txt") for role in ("old", "new", "student")]
    result = paths + [
        "--id", "CourseID", "--fields", "Front", "Back", "CourseID",
        "--student-fields", "Front", "Back", "CourseID", "Personal", "--out", str(out)]
    return result + (["--notetype", "Course Facts", "--deck", "Default"] if variant == "mixed" else [])


def test_cli_bundle_and_review_roundtrip(project, tmp_path):
    out = tmp_path / "preview"
    assert main(args(project, out)) == 1
    plan = json.loads((out / "plan.json").read_text())
    assert plan["import_count"] == 3
    assert plan["unresolved_count"] == 2
    choices = json.loads((out / "resolutions.json").read_text())
    choices["notes"]["C03"]["fields"]["Back"] = {"value": "A double helix; remember a twisted ladder"}
    choices["notes"]["C05"] = {"action": "skip"}
    resolution_file = tmp_path / "choices.json"
    resolution_file.write_text(json.dumps(choices))
    final = tmp_path / "final"
    assert main(args(project, final) + ["--resolutions", str(resolution_file)]) == 0
    merged = read_deck(final / "import.tsv", "CourseID")
    assert set(merged.notes) == {"C01", "C02", "C03", "C06"}
    assert main(args(project, final)) == 2  # existing bundle preserved


@pytest.mark.parametrize("variant", [".", "mixed"])
def test_anki_import_preserves_identity_scheduling_and_history(project, tmp_path, variant):
    anki = pytest.importorskip("anki.collection")
    from anki import import_export_pb2 as pb

    out = tmp_path / "preview"
    assert main(args(project, out, variant)) == 1
    source = read_deck(project / "examples/anki-merge/student.txt", "CourseID", ["Front", "Back", "CourseID", "Personal"])
    c = anki.Collection(str(tmp_path / "student.anki2"))
    try:
        model = add_model(c)
        for item in source.notes.values():
            note = c.new_note(model)
            note.guid, note.fields, note.tags = item.guid, list(item.fields.values()), item.tags
            c.add_note(note, 1)
        # Real scheduler answers create review rows and meaningful card state.
        for cid in c.find_cards(""):
            card = c.get_card(cid)
            card.start_timer()
            c.sched.answerCard(card, 4)
        before_cards = c.db.all("select id,nid,type,queue,due,ivl,factor,reps,lapses,left,odue,odid,flags,data from cards order by id")
        before_history = c.db.all("select * from revlog order by id")
        assert len(before_history) == 5
        before_ids = {n.fields[2]: n.id for n in (c.get_note(nid) for nid in c.find_notes(""))}
        metadata = c.get_csv_metadata(str(out / "import.tsv"), None)
        if hasattr(pb.CsvMetadata, "UPDATE"):
            metadata.dupe_resolution = pb.CsvMetadata.UPDATE
        result = c.import_csv(pb.ImportCsvRequest(path=str(out / "import.tsv"), metadata=metadata))
        after_ids = {n.fields[2]: n.id for n in (c.get_note(nid) for nid in c.find_notes(""))}
        assert len(after_ids) == 6, result
        assert all(after_ids[key] == value for key, value in before_ids.items())
        assert c.get_note(after_ids["C01"])["Front"] == "Which energy source powers photosynthesis?"
        assert c.get_note(after_ids["C02"])["Back"] == "Canberra"
        assert c.get_note(after_ids["C03"])["Back"].startswith("Twisted ladder")
        assert c.get_note(after_ids["C04"])["Personal"] == source.notes["C04"].fields["Personal"]
        after_cards = c.db.all("select id,nid,type,queue,due,ivl,factor,reps,lapses,left,odue,odid,flags,data from cards where nid in (" + ",".join("?" for _ in before_ids) + ") order by id", *before_ids.values())
        assert after_cards == before_cards
        assert c.db.all("select * from revlog order by id") == before_history
        # Re-import must be idempotent: no duplicate card or note.
        c.import_csv(pb.ImportCsvRequest(path=str(out / "import.tsv"), metadata=metadata))
        assert c.note_count() == 6
        if version("anki") == "2.1.54":
            assert c.db.list("select nid from cards where nid not in (select id from notes)") == [0]
            pytest.xfail("Reproduced upstream Anki 2.1.54 orphan-card bug; see UPSTREAM.md")
        assert c.card_count() == 6
    finally:
        c.close()


def test_anki_first_field_fallback_updates_in_place(tmp_path):
    anki = pytest.importorskip("anki.collection")
    from anki import import_export_pb2 as pb

    paths = []
    for role, back in (("old", "old answer"), ("new", "new answer"), ("student", "old answer")):
        path = tmp_path / f"{role}.txt"
        path.write_text(f"#separator:tab\n#html:true\n#notetype:Basic\n#columns:Front\tBack\nlesson-001\t{back}\n")
        paths.append(str(path))
    out = tmp_path / "fallback"
    assert main(paths + ["--id", "Front", "--out", str(out)]) == 0
    c = anki.Collection(str(tmp_path / "fallback.anki2"))
    try:
        n = c.new_note(c.models.by_name("Basic"))
        n["Front"], n["Back"] = "lesson-001", "old answer"
        c.add_note(n, 1)
        card = c.get_card(c.find_cards("")[0])
        card.start_timer()
        c.sched.answerCard(card, 4)
        before = c.db.all("select * from cards")
        history = c.db.all("select * from revlog")
        metadata = c.get_csv_metadata(str(out / "import.tsv"), None)
        assert list(metadata.column_labels)[-2:] == ["Front", "Back"]
        assert metadata.dupe_resolution == pb.CsvMetadata.UPDATE
        assert metadata.match_scope == pb.CsvMetadata.NOTETYPE
        c.import_csv(pb.ImportCsvRequest(path=str(out / "import.tsv"), metadata=metadata))
        assert c.note_count() == 1
        assert c.get_note(n.id)["Back"] == "new answer"
        # mod/usn may change; actual scheduler and card identities must not.
        assert c.db.all("select * from cards") == before
        assert c.db.all("select * from revlog") == history
    finally:
        c.close()


def test_output_failure_cleans_reservation(project, tmp_path, monkeypatch):
    from notewrench.ankimerge import cli

    out = tmp_path / "cannot-publish"
    def fail(*args):
        raise OSError("simulated rename failure")
    monkeypatch.setattr(cli.os, "rename", fail)
    assert main(args(project, out)) == 2
    assert not out.exists()
    assert not list(tmp_path.glob(".cannot-publish-*"))


def test_dangling_output_symlink_is_never_replaced(project, tmp_path):
    out = tmp_path / "review"
    target = tmp_path / "missing"
    out.symlink_to(target)
    assert main(args(project, out)) == 2
    assert out.is_symlink()
    assert not target.exists()


def test_publication_with_windows_no_replace_rename_semantics(project, tmp_path, monkeypatch):
    from notewrench.ankimerge import cli

    original = cli.os.rename
    def windows_rename(source, destination):
        if Path(destination).exists():
            raise FileExistsError("Windows refuses an existing destination directory")
        original(source, destination)
    monkeypatch.setattr(cli, "WINDOWS", True)
    monkeypatch.setattr(cli.os, "rename", windows_rename)
    out = tmp_path / "windows-semantics"
    assert main(args(project, out)) == 1
    assert (out / "plan.json").is_file()
    assert (out / "import.tsv").is_file()


@pytest.mark.parametrize("variant,imports,unresolved", [
    ("anki-2.1.54", 3, 2), ("plain-text", 3, 2), ("no-guid", 2, 3), ("fields-only", 2, 3),
])
def test_real_upstream_export_variants(project, tmp_path, variant, imports, unresolved):
    out = tmp_path / variant
    assert main(args(project, out, variant)) == 1
    plan = json.loads((out / "plan.json").read_text())
    assert plan["import_count"] == imports
    assert plan["unresolved_count"] == unresolved
    output = read_deck(out / "import.tsv", "CourseID")
    source = read_deck(project / "examples" / "anki-merge" / variant / "student.txt", "CourseID",
                       ["Front", "Back", "CourseID", "Personal"])
    assert output.notes["C02"].fields["Personal"] == source.notes["C02"].fields["Personal"]
    assert output.notes["C02"].guid == source.notes["C02"].guid


def test_mixed_metadata_fails_with_actionable_message(project, tmp_path, capsys):
    out = tmp_path / "mixed"
    flags = args(project, out, "mixed")[:-4]  # omit explicit missing-metadata defaults
    assert main(flags) == 2
    assert "incomplete notetype" in capsys.readouterr().err
    assert not out.exists()


def test_excessive_resolution_json_nesting_is_actionable(project, tmp_path, capsys):
    path = tmp_path / "deep.json"
    path.write_text("[" * 1100 + "0" + "]" * 1100)
    out = tmp_path / "deep-output"
    assert main(args(project, out) + ["--resolutions", str(path)]) == 2
    error = capsys.readouterr().err
    assert error.startswith("notewrench anki-merge:")
    assert "Traceback" not in error
    assert not out.exists()


def test_disk_full_during_report_leaves_no_bundle(project, tmp_path, monkeypatch):
    from notewrench.ankimerge import cli
    import errno

    out = tmp_path / "disk-full"
    def fail(*args, **kwargs):
        raise OSError(errno.ENOSPC, "simulated disk full")
    monkeypatch.setattr(cli.json, "dump", fail)
    assert main(args(project, out)) == 2
    assert not out.exists()
    assert not list(tmp_path.glob(".disk-full-*"))


@pytest.mark.parametrize("new_text,card_count", [
    ("Updated {{c1::ATP}}.", 1),
    ("{{c1::ATP}} and {{c2::ADP}}", 2),
    ("Only {{c2::ATP}}", 2),
    ("No cloze remains", 1),
])
def test_cloze_updates_retain_reviewed_cards_and_personal_fields(tmp_path, new_text, card_count):
    """Genuine Cloze exports; added ordinals create new, unreviewed cards."""
    anki = pytest.importorskip("anki.collection")
    from anki import import_export_pb2 as pb

    def model(c):
        m = c.models.by_name("Cloze")
        for name in ("CourseID", "Personal"):
            c.models.add_field(m, c.models.new_field(name))
        c.models.save(m)
        return m

    def export(c, role):
        path = tmp_path / f"{role}.txt"
        c.export_note_csv(out_path=str(path), limit=None, with_html=True, with_tags=True,
                          with_deck=True, with_notetype=True, with_guid=True)
        return str(path)

    teacher = anki.Collection(str(tmp_path / "teacher.anki2"))
    try:
        teacher_model = model(teacher)
        extra_field = teacher_model["flds"][1]["name"]  # use the actual exported field name
        n = teacher.new_note(teacher_model)
        n["Text"], n["CourseID"] = "Makes {{c1::ATP}}", "BIO-001"
        teacher.add_note(n, 1)
        old = export(teacher, "old")
        n["Text"] = new_text
        teacher.update_note(n)
        new = export(teacher, "new")
    finally:
        teacher.close()
    c = anki.Collection(str(tmp_path / "student.anki2"))
    try:
        note = c.new_note(model(c))
        note["Text"], note["CourseID"] = "Makes {{c1::ATP}}", "BIO-001"
        note[extra_field], note["Personal"] = "My mnemonic", "My lecture notes"
        c.add_note(note, 1)
        card = c.get_card(c.find_cards("")[0])
        card.start_timer()
        c.sched.answerCard(card, 4)
        before = c.db.all("select * from cards")
        history = c.db.all("select * from revlog")
        student = export(c, "student")
        out = tmp_path / "merged"
        assert main([old, new, student, "--id", "CourseID", "--fields", "Text", extra_field, "CourseID", "Personal", "--out", str(out)]) == 0
        metadata = c.get_csv_metadata(str(out / "import.tsv"), None)
        c.import_csv(pb.ImportCsvRequest(path=str(out / "import.tsv"), metadata=metadata))
        assert c.note_count() == 1
        assert c.card_count() == card_count
        assert c.db.all("select * from cards where id=?", card.id) == before
        assert c.db.all("select * from revlog") == history
        updated = c.get_note(note.id)
        assert updated["Text"] == new_text
        assert updated[extra_field] == "My mnemonic"
        assert updated["Personal"] == "My lecture notes"
        assert c.db.scalar("select count(*) from cards where reps=0") == card_count - 1
    finally:
        c.close()


def test_oversized_review_choices_fail_with_a_size_error(project, tmp_path, monkeypatch, capsys):
    from notewrench.ankimerge import cli

    monkeypatch.setattr(cli, "MAX_RESOLUTION_BYTES", 64)
    path = tmp_path / "large-review.json"
    path.write_text(" " * 1000)
    out = tmp_path / "large-output"
    assert main(args(project, out) + ["--resolutions", str(path)]) == 2
    assert "too large" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize("payload,message", [
    ('{"fingerprint":"a","fingerprint":"b","notes":{}}', "Duplicate JSON key"),
    ('{"fingerprint":NaN,"notes":{}}', "Nonstandard JSON constant"),
])
def test_ambiguous_json_review_choices_are_rejected(project, tmp_path, capsys, payload, message):
    path = tmp_path / "ambiguous.json"
    path.write_text(payload)
    out = tmp_path / "ambiguous-output"
    assert main(args(project, out) + ["--resolutions", str(path)]) == 2
    assert message in capsys.readouterr().err
    assert not out.exists()


def test_bom_in_review_choices_is_supported(project, tmp_path):
    out = tmp_path / "preview"
    assert main(args(project, out)) == 1
    choice = json.loads((out / "resolutions.json").read_text())
    choice["notes"] = {"C03": {"action": "skip"}, "C05": {"action": "skip"}}
    path = tmp_path / "choices.json"
    path.write_text("\ufeff" + json.dumps(choice))
    assert main(args(project, tmp_path / "resolved") + ["--resolutions", str(path)]) == 0


def test_author_csv_and_anki_export_can_use_different_headers_and_separators(project, tmp_path):
    import csv

    paths = []
    for role, delimiter in (("old", ","), ("new", ";")):
        path = tmp_path / f"{role}.csv"
        with path.open("w", newline="") as out:
            out.write("#html:true\n")
            writer = csv.writer(out, delimiter=delimiter)
            writer.writerow(["Front", "Back", "CourseID"])
            writer.writerow(["Capital of Australia?", "Sydney" if role == "old" else "Canberra", "C02"])
        paths.append(str(path))
    paths.append(str(project / "examples/anki-merge/student.txt"))
    out = tmp_path / "csv-mixed"
    assert main(paths + ["--id", "CourseID", "--fields", "Front", "Back", "CourseID",
                        "--student-fields", "Front", "Back", "CourseID", "Personal",
                        "--old-header-row", "--new-header-row", "--old-delimiter", "comma",
                        "--new-delimiter", "semicolon", "--notetype", "Course Facts", "--deck", "Default",
                        "--out", str(out)]) == 0
    output = read_deck(out / "import.tsv", "CourseID")
    assert set(output.notes) == {"C02"}
    assert output.notes["C02"].fields["Back"] == "Canberra"
    plan = json.loads((out / "plan.json").read_text())
    assert plan["sources"]["student"]["notes"] == 5


@pytest.mark.parametrize("student_guid", [True, False])
def test_actual_student_field_order_can_differ_from_teacher(tmp_path, student_guid):
    anki = pytest.importorskip("anki.collection")
    from anki import import_export_pb2 as pb

    def export(c, role, guid=True):
        path = tmp_path / f"{role}.txt"
        c.export_note_csv(out_path=str(path), limit=None, with_html=True, with_tags=True,
                          with_deck=True, with_notetype=True, with_guid=guid)
        return str(path)

    teacher = anki.Collection(str(tmp_path / "teacher.anki2"))
    try:
        n = teacher.new_note(add_model(teacher, ("Front", "Back", "CourseID")))
        n["Front"], n["Back"], n["CourseID"] = "Original question", "Original answer", "ORD-001"
        teacher.add_note(n, 1)
        old = export(teacher, "old")
        n["Front"] = "Revised <b>question</b>"
        teacher.update_note(n)
        new = export(teacher, "new")
    finally:
        teacher.close()
    c = anki.Collection(str(tmp_path / "student.anki2"))
    try:
        m = add_model(c, ("CourseID", "Back", "Personal", "Front"))
        n = c.new_note(m)
        n["Front"], n["Back"], n["CourseID"], n["Personal"] = "Original question", "<i>My answer</i>", "ORD-001", "My notes"
        c.add_note(n, 1)
        card = c.get_card(c.find_cards("")[0])
        card.start_timer()
        c.sched.answerCard(card, 4)
        state = c.db.all("select * from cards")
        history = c.db.all("select * from revlog")
        student = export(c, "student", student_guid)
        out = tmp_path / "reordered"
        assert main([old, new, student, "--id", "CourseID", "--fields", "Front", "Back", "CourseID",
                     "--student-fields", "CourseID", "Back", "Personal", "Front", "--out", str(out)]) == 0
        metadata = c.get_csv_metadata(str(out / "import.tsv"), None)
        c.import_csv(pb.ImportCsvRequest(path=str(out / "import.tsv"), metadata=metadata))
        assert c.note_count() == 1
        note = c.get_note(n.id)
        assert note["Front"] == "Revised <b>question</b>"
        assert note["Back"] == "<i>My answer</i>"
        assert note["Personal"] == "My notes"
        assert c.db.all("select * from cards") == state
        assert c.db.all("select * from revlog") == history
    finally:
        c.close()


def test_proposed_html_identity_collision_is_withheld_before_native_import(tmp_path):
    anki = pytest.importorskip("anki.collection")
    from anki import import_export_pb2 as pb
    import csv

    paths = []
    for role, rows in (("old", [["different", "old", "A"]]),
                       ("new", [["<b>same</b>", "old", "A"], ["same", "new", "B"]])):
        path = tmp_path / f"{role}.txt"
        with path.open("w", newline="") as out:
            out.write("#separator:tab\n#html:true\n#notetype:Course Facts\n#deck:Default\n#columns:Front\tBack\tCourseID\n")
            csv.writer(out, delimiter="\t", quoting=csv.QUOTE_ALL).writerows(rows)
        paths.append(str(path))
    c = anki.Collection(str(tmp_path / "student.anki2"))
    try:
        note = c.new_note(add_model(c))
        note["Front"], note["Back"], note["CourseID"], note["Personal"] = "different", "old", "A", "My notes"
        c.add_note(note, 1)
        card = c.get_card(c.find_cards("")[0])
        card.start_timer()
        c.sched.answerCard(card, 4)
        cards = c.db.all("select * from cards")
        history = c.db.all("select * from revlog")
        student = tmp_path / "student.txt"
        c.export_note_csv(out_path=str(student), limit=None, with_html=True, with_tags=True,
                          with_deck=True, with_notetype=True, with_guid=True)
        out = tmp_path / "safe"
        assert main(paths + [str(student), "--id", "CourseID", "--fields", "Front", "Back", "CourseID",
                             "--student-fields", "Front", "Back", "CourseID", "Personal", "--out", str(out)]) == 1
        assert set(read_deck(out / "import.tsv", "CourseID").notes) == {"A"}
        metadata = c.get_csv_metadata(str(out / "import.tsv"), None)
        c.import_csv(pb.ImportCsvRequest(path=str(out / "import.tsv"), metadata=metadata))
        assert c.note_count() == 1
        updated = c.get_note(note.id)
        assert updated["CourseID"] == "A"
        assert updated["Front"] == "<b>same</b>"
        assert updated["Personal"] == "My notes"
        assert c.db.all("select * from cards") == cards
        assert c.db.all("select * from revlog") == history
    finally:
        c.close()
