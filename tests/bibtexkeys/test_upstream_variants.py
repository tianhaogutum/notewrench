from copy import deepcopy
from pathlib import Path

from pybtex.database import BibliographyData, parse_string
import pytest

from notewrench.bibtexkeys.bibliography import DEFAULT_ID_FIELDS, load_snapshot
from notewrench.bibtexkeys.notes import NoteScanner, apply_edits
from notewrench.bibtexkeys.planner import make_plan

VARIANTS = Path(__file__).parent / "fixtures/upstream/variants"


@pytest.mark.parametrize("path", sorted(VARIANTS.glob("*.bib")), ids=lambda path: path.name)
def test_unmodified_bbt_version_and_format_variant(path):
    _, records, issues = load_snapshot(path, DEFAULT_ID_FIELDS)
    assert records
    assert len(issues) == len(records)
    assert {issue["kind"] for issue in issues} == {"missing_identity"}


@pytest.mark.parametrize("path", sorted(VARIANTS.glob("*.bib")), ids=lambda path: path.name)
def test_migrate_export_based_records_without_metadata_matching(path, tmp_path):
    # Identity addition and rename are explicitly synthetic. Pybtex writes the
    # controlled pair; raw upstream exports are independently tested above.
    database = parse_string(path.read_text(), "bibtex")
    new_entries = []
    keys = list(database.entries)
    for index, (key, entry) in enumerate(database.entries.items()):
        entry.fields["zotero-uri"] = f"https://zotero.org/groups/12345/items/ITEM{index:04d}"
        new_entries.append((f"Renamed{index}", deepcopy(entry)))
    old = tmp_path / "old.bib"
    new = tmp_path / "new.bib"
    old.write_text(database.to_string("bibtex"))
    new.write_text(BibliographyData(entries=new_entries).to_string("bibtex"))
    notes = tmp_path / "notes"
    notes.mkdir()
    text = "Citations: " + "; ".join("@" + key for key in keys) + ".\n"
    (notes / "reading.md").write_text(text)
    report, patch = make_plan(old, new, notes)
    assert report["status"] == "ready"
    assert report["summary"]["replacements"] == len(keys)
    assert len(report["mappings"]) == len(keys)
    assert "Renamed0" in patch
    assert (notes / "reading.md").read_text() == text


def test_same_doi_multiple_library_items_still_map_by_distinct_ids(tmp_path):
    path = VARIANTS / "v8-stable.bib"
    database = parse_string(path.read_text(), "bibtex")
    assert len({entry.fields["doi"] for entry in database.entries.values()}) == 1
    assert len(database.entries) == 6
    old = []
    from notewrench.bibtexkeys.bibliography import Record, match_snapshots
    for index, key in enumerate(database.entries):
        old.append(Record(key, f"stable-id-{index}"))
    new = [Record(f"new-{index}", record.identity) for index, record in enumerate(reversed(old))]
    mappings, states, conflicts = match_snapshots(old, new)
    assert len(mappings) == 6
    assert not conflicts
    assert states[old[0].key]["new_key"] == "new-5"
