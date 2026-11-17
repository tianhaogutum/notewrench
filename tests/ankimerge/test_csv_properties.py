"""Randomized real-format parser checks, with Python CSV generating records."""

import csv
import io
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings, strategies as st

from notewrench.ankimerge.text import read_deck


@settings(max_examples=300, deadline=None)
@given(
    separator=st.sampled_from(["\t", ",", ";", "|", ":", " "]),
    values=st.lists(st.text(alphabet=st.characters(blacklist_categories=("Cs",),
                     blacklist_characters="\x00\x1f"), max_size=150), min_size=1, max_size=8),
    layout=st.permutations(["CourseID", "Back", "guid", "tags", "notetype", "deck"]),
    eol=st.sampled_from(["\n", "\r\n", "\r"]),
    bom=st.booleans(),
)
def test_csv_records_survive_arbitrary_special_column_positions(separator, values, layout, eol, bom):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter=separator, lineterminator=eol, quoting=csv.QUOTE_ALL)
    separator_name = {"\t": "Tab", ",": "Comma", ";": "Semicolon", "|": "Pipe", ":": "Colon", " ": "Space"}[separator]
    stream.write(f"#separator:{separator_name}{eol}#html:true{eol}")
    for special in ("guid", "notetype", "deck", "tags"):
        stream.write(f"#{special} column:{layout.index(special) + 1}{eol}")
    stream.write(f"#tags:global{eol}#columns:")
    writer.writerow(layout)
    for index, value in enumerate(values):
        row = {"CourseID": f"#id-{index}", "Back": value, "guid": f"guid-{index}",
               "notetype": "Course", "deck": "Biology", "tags": "z a"}
        writer.writerow([row[name] for name in layout])
        stream.write(f"# external comment{eol}")
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "random.txt"
        path.write_bytes((("\ufeff" if bom else "") + stream.getvalue()).encode("utf-8"))
        deck = read_deck(path, "CourseID")
    assert list(deck.notes) == [f"#id-{i}" for i in range(len(values))]
    for index, value in enumerate(values):
        note = deck.notes[f"#id-{index}"]
        assert note.fields["Back"] == value
        assert note.guid == f"guid-{index}"
        assert note.tags == ["a", "global", "z"]
        assert note.notetype == "Course"
        assert note.deck == "Biology"
