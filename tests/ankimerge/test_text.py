import io

import pytest

from notewrench.ankimerge.text import InputError, Note, read_deck, write_deck


def test_real_anki_exports(project):
    student = read_deck(project / "examples/anki-merge/student.txt", "CourseID", ["Front", "Back", "CourseID", "Personal"])
    assert len(student.notes) == 5
    assert student.notes["C01"].guid
    assert "第二行" in student.notes["C01"].fields["Personal"]
    assert student.notes["C01"].notetype == "Course Facts"
    assert student.notes["C01"].tags == ["course::biology", "personal"]


@pytest.mark.parametrize("delimiter,header", [("\t", "Tab"), (",", "Comma"), (";", "Semicolon"), ("|", "Pipe"), (":", "Colon")])
def test_supported_delimiters_and_multiline_comments(tmp_path, delimiter, header):
    path = tmp_path / "format.txt"
    path.write_text(f'#separator:{header}\n#columns:CourseID{delimiter}Back\n1{delimiter}"line one\n# this is content\nline three"\n# outside comment\n2{delimiter}"quoted ""answer"""\n')
    deck = read_deck(path, "CourseID")
    assert deck.notes["1"].fields["Back"] == "line one\n# this is content\nline three"
    assert deck.notes["2"].fields["Back"] == 'quoted "answer"'


def test_roundtrip_quoting_bom_and_hash_first_field(tmp_path):
    stream = io.StringIO(newline="")
    notes = [Note({"CourseID": "#id", "Back": 'é\t中文\n#not a header\n"x"'}, guid="abc")]
    write_deck(stream, ("CourseID", "Back"), notes, True)
    path = tmp_path / "output.txt"
    path.write_text("\ufeff" + stream.getvalue(), encoding="utf-8")
    actual = read_deck(path, "CourseID")
    assert actual.notes["#id"].snapshot() == notes[0].snapshot()


@pytest.mark.parametrize("content,match", [
    ("#columns:CourseID\tBack\n1\ta\n1\tb\n", "duplicate course"),
    ("#columns:CourseID\tBack\n1\ta\n2\n", "expected 2"),
    ("#columns:CourseID\tBack\n1\t\"unclosed\n", "CSV"),
    ("#columns:CourseID\tBack\n\ta\n", "course IDs"),
    ("#columns:CourseID\tBack\n 1\ta\n", "course IDs"),
    ("#columns:CourseID\tBack\n1\ta\x00\n", "NUL"),
    ("#guid column:1\n#tags column:1\n#columns:g\tCourseID\nguid\t1\n", "overlap"),
    ("#guid column:7\n#columns:CourseID\tBack\n1\ta\n", "exceeds"),
    ("#guid column:0\n#columns:CourseID\tBack\n1\ta\n", "positive integer"),
    ("#html:maybe\n#columns:CourseID\tBack\n1\ta\n", "#html"),
    ("#separator:tab\n#separator:comma\n1\ta\n", "duplicate"),
    ("#future:dangerous\n#columns:CourseID\tBack\n1\ta\n", "unsupported header"),
    ('#columns:"CourseID\tBack\n1\ta\n', "invalid CSV in #columns"),
    ("#notetype:Course\n#notetype column:1\n#columns:Type\tCourseID\nCourse\t1\n", "conflicting global"),
    ("#if matches:duplicate\n#columns:CourseID\tBack\n1\ta\n", "#if matches"),
    ("#match scope:everything\n#columns:CourseID\tBack\n1\ta\n", "#match scope"),
    ("#notetype:Cour\x00se\n#columns:CourseID\tBack\n1\ta\n", "in headers"),
])
def test_malformed_inputs_fail_closed(tmp_path, content, match):
    path = tmp_path / "bad.txt"
    path.write_text(content)
    with pytest.raises(InputError, match=match):
        read_deck(path, "CourseID")


def test_ordinary_csv_header_is_explicit(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text('CourseID,Back\n001,"a,b"\n')
    deck = read_deck(path, "CourseID", delimiter="comma", header_row=True)
    assert deck.notes["001"].fields["Back"] == "a,b"


def test_empty_deck_keeps_explicit_schema(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("#separator:Tab\n#guid column:1\n")
    deck = read_deck(path, "CourseID", ["CourseID", "Back"])
    assert not deck.notes
    assert deck.fields == ("CourseID", "Back")


def test_invalid_utf8_is_actionable(tmp_path):
    path = tmp_path / "invalid.txt"
    path.write_bytes(b"#columns:CourseID\tBack\n1\t\xff\n")
    with pytest.raises(InputError, match="UTF-8"):
        read_deck(path, "CourseID")


def test_empty_deck_with_field_name_iterator(tmp_path):
    path = tmp_path / "empty.txt"
    path.write_text("#separator:tab\n")
    assert read_deck(path, "CourseID", iter(["CourseID", "Back"])).fields == ("CourseID", "Back")


def test_digest_matches_exact_consumed_bytes(tmp_path):
    import hashlib

    path = tmp_path / "bom-crlf.txt"
    payload = '\ufeff#columns:CourseID\tBack\r\n1\t"a\r\nb"\r\n'.encode("utf-8")
    path.write_bytes(payload)
    assert read_deck(path, "CourseID").sha256 == hashlib.sha256(payload).hexdigest()


def test_input_mutation_is_rejected(tmp_path, monkeypatch):
    from notewrench.ankimerge import text

    path = tmp_path / "changing.txt"
    path.write_text("#columns:CourseID\tBack\n1\ta\n")
    original = text._records
    def mutate(*args):
        yield from original(*args)
        with path.open("a") as output:
            output.write("2\tb\n")
    monkeypatch.setattr(text, "_records", mutate)
    with pytest.raises(InputError, match="changed while reading"):
        read_deck(path, "CourseID")


def test_nfd_course_ids_fail_instead_of_silently_changing(tmp_path):
    path = tmp_path / "unicode.txt"
    path.write_text("#columns:CourseID\tBack\ne\u0301\tanswer\n")
    with pytest.raises(InputError, match="Unicode NFC"):
        read_deck(path, "CourseID")


def test_missing_metadata_defaults_are_explicit(tmp_path):
    path = tmp_path / "plain.txt"
    path.write_text("#columns:CourseID\tBack\n1\tanswer\n")
    note = read_deck(path, "CourseID", defaults={"notetype": "Known Course", "deck": "Biology"}).notes["1"]
    assert note.notetype == "Known Course"
    assert note.deck == "Biology"


def test_partial_output_metadata_is_rejected():
    notes = [Note({"CourseID": "1"}, notetype="Course"), Note({"CourseID": "2"})]
    with pytest.raises(InputError, match="incomplete notetype"):
        write_deck(io.StringIO(), ["CourseID"], notes, True)


def test_unbounded_physical_lines_fail_before_csv_allocates_them(tmp_path, monkeypatch):
    from notewrench.ankimerge import text

    monkeypatch.setattr(text, "MAX_LINE_CHARS", 32)
    path = tmp_path / "huge-line.txt"
    path.write_text("x" * 1000)
    with pytest.raises(InputError, match="physical line exceeds"):
        read_deck(path, "CourseID", ["CourseID"])


def test_header_row_cannot_silently_discard_a_note_with_explicit_fields(tmp_path):
    path = tmp_path / "not-a-header.txt"
    path.write_text("001\tmy note\n002\tanother note\n")
    with pytest.raises(InputError, match="refusing to discard"):
        read_deck(path, "CourseID", ["CourseID", "Back"], header_row=True)


def test_empty_special_metadata_can_be_filled_explicitly(tmp_path):
    path = tmp_path / "missing-type.txt"
    path.write_text("#notetype column:1\n#columns:Type\tCourseID\tBack\n\t1\ta\n")
    note = read_deck(path, "CourseID", defaults={"notetype": "Course"}).notes["1"]
    assert note.notetype == "Course"


def test_historical_tags_first_line_is_not_imported_as_a_note(tmp_path):
    path = tmp_path / "legacy.txt"
    path.write_text("\ufefftags:old course\n#separator:tab\n#columns:CourseID\tBack\n1\ta\n")
    deck = read_deck(path, "CourseID")
    assert set(deck.notes) == {"1"}
    assert deck.notes["1"].tags == ["course", "old"]


def test_named_pipe_input_is_rejected_without_opening_it(tmp_path):
    import os

    if not hasattr(os, "mkfifo"):
        pytest.skip("This platform has no mkfifo API")
    path = tmp_path / "pipe.txt"
    os.mkfifo(path)
    with pytest.raises(InputError, match="regular text file"):
        read_deck(path, "CourseID", ["CourseID", "Back"])


def test_collection_package_has_an_actionable_text_export_error(tmp_path):
    path = tmp_path / "course.apkg"
    path.write_bytes(b"PK\x03\x04")
    with pytest.raises(InputError, match="Notes in Plain Text"):
        read_deck(path, "CourseID", ["CourseID", "Back"])
