import json
from pathlib import Path

import pytest

from notewrench.bibtexkeys.bibliography import BBT_JSON_TRANSLATOR, DEFAULT_ID_FIELDS, InputError, Record, load_snapshot
from notewrench.bibtexkeys.planner import make_plan

ROOT = Path(__file__).resolve().parents[2]


def snapshot(items):
    return {"config": {"id": BBT_JSON_TRANSLATOR, "label": "BetterBibTeX JSON"}, "items": items}


def item(key="Old", uri="https://zotero.org/users/12345/items/ABCD1234"):
    return {"itemType": "journalArticle", "citationKey": key, "uri": uri, "itemID": 42}


def test_real_bbt_debug_fixture_never_falls_back_to_numeric_item_id():
    _, records, issues = load_snapshot(ROOT / "tests/bibtexkeys/fixtures/upstream/bbt-sample.json", DEFAULT_ID_FIELDS)
    assert records == [Record("radler_integration_2022", None)]
    assert issues[0]["kind"] == "missing_identity"


def test_json_snapshots_match_the_bibtex_identity_recipe(tmp_path):
    old = tmp_path / "old.json"
    old.write_text(json.dumps(snapshot([item("radler_integration_2022"), {"itemType": "note", "note": "Not a citable entry"}])))
    report, patch = make_plan(old, ROOT / "examples/bibtex-keys/new.bib", ROOT / "examples/bibtex-keys/notes")
    assert report["status"] == "ready"
    assert report["summary"]["replacements"] == 4
    assert report["inputs"]["old_format"] == "bbt-json"
    assert report["inputs"]["new_format"] == "bibtex"
    assert "Radler2022SysML" in patch


def test_json_key_swaps_preserve_item_identity(tmp_path):
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    old.write_text(json.dumps(snapshot([item("A"), item("B", "https://zotero.org/groups/123/items/BBBB1234") ])))
    new.write_text(json.dumps(snapshot([item("B"), item("A", "https://zotero.org/groups/123/items/BBBB1234") ])))
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "reading.md").write_text("@A @B")
    report, patch = make_plan(old, new, notes)
    assert report["status"] == "ready"
    assert report["summary"]["replacements"] == 2
    assert "+@B @A" in patch


@pytest.mark.parametrize("data", [
    [], [{"id": "CSL-citation-key"}], {"items": [item()]},
    {"config": {"id": "wrong"}, "items": [item()]},
    snapshot([]), snapshot([{"itemType": "note"}]), snapshot([{"itemType": "journalArticle", "itemID": 1}]),
    snapshot([item("A"), item("a")]), snapshot([{"itemType": "book", "citationKey": 42}]),
    snapshot([{"itemType": "book", "citationKey": "bad\ud800"}]), snapshot([None]),
])
def test_wrong_json_shapes_or_missing_keys_are_not_silently_reinterpreted(tmp_path, data):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data))
    with pytest.raises(InputError):
        load_snapshot(path, DEFAULT_ID_FIELDS)


def test_duplicate_json_members_are_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"config":{"id":"' + BBT_JSON_TRANSLATOR + '"},"items":[],"items":[]}')
    with pytest.raises(InputError, match="duplicate JSON member"):
        load_snapshot(path, DEFAULT_ID_FIELDS)


def test_missing_key_in_one_item_is_a_visible_partial_snapshot_issue(tmp_path):
    path = tmp_path / "partial.json"
    path.write_text(json.dumps(snapshot([item(), {"itemType": "book", "uri": "https://zotero.org/users/1/items/ITEM0002"}])))
    _, records, issues = load_snapshot(path, DEFAULT_ID_FIELDS)
    assert len(records) == 1
    assert issues[0]["kind"] == "missing_citation_key"
