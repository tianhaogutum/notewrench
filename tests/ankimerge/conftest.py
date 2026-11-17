from pathlib import Path

import pytest

from notewrench.ankimerge.text import Note, TextDeck


@pytest.fixture(scope="module")
def deck():
    """Synthetic focused merge cases; real Anki exports have separate tests."""
    def make(rows, names=("CourseID", "Front", "Back"), html=True, guids=None):
        notes = {}
        for row in rows:
            values = dict(zip(names, row))
            key = values["CourseID"]
            notes[key] = Note(values, (guids or {}).get(key, ""), "Course", "Default", ["course"])
        return TextDeck(Path("synthetic.tsv"), tuple(names), notes, html,
                        repr(rows), {})
    return make


@pytest.fixture
def project():
    """Repository root; the sample exports live in examples/anki-merge/."""
    return Path(__file__).resolve().parents[2]
