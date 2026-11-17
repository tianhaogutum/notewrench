import json

import pytest

from notewrench.zoterodupes.decisions import decision_template, export_confirmed
from notewrench.zoterodupes.matching import build_review
from notewrench.zoterodupes.readers import load_library
from .paths import EXAMPLES, FIXTURES, UPSTREAM


@pytest.mark.parametrize("file,count", [
    ("bbt-all-fields.json", 26), ("bbt-all-fields.csl.json", 26),
    ("bbt-collected-notes.json", 36), ("bbt-complex-dates.csl.json", 15),
    ("bbt-auto-tags.json", 1), ("bbt-extra-doi.json", 1),
    ("bbt-nonfile-attachment.json", 2), ("bbt-csl-creators.csl.json", 1),
    ("bbt-chinese.json", 1),
])
def test_upstream_regression_export_variants(file, count):
    records, _ = load_library("fixture", UPSTREAM / file)
    assert len(records) == count


def test_real_automatic_tag_type_and_html_note_retained():
    records, _ = load_library("fixture", UPSTREAM / "bbt-auto-tags.json")
    assert {tag["tag"] for tag in records[0].tags if tag.get("type") == 1} == {"⚠️ Invalid DOI"}
    assert records[0].notes == ["<p>MR 46:7018</p>"]


def test_official_translator_nested_section_and_uri_journal():
    records, _ = load_library("rdf", FIXTURES / "derived-official-translator.rdf")
    assert len(records) == 1
    record = records[0]
    assert record.metadata["publication"] == "Bulletin of Volcanology"
    assert record.metadata["section"] == "Research articles"
    assert record.metadata["publicationAbbreviation"] == "Bull. Volcanol."
    assert record.metadata["publisher"] == "Example publisher"
    assert record.metadata["place"] == "Berlin"
    assert record.metadata["authors"].startswith("Adams, Nancy K and DeSilva, Shanaka L")
    assert "Editor, Example" not in record.metadata["authors"]
    assert any(c["creatorType"] == "editor" for c in record.metadata["creators"])
    assert len(record.notes) == 2
    assert len(record.attachments) == 1
    # URI-subject journals must preserve unknown/context fields too.
    assert "0258-8900" in json.dumps(record.raw)
    assert "Bulletin of Volcanology" in json.dumps(record.raw)


def test_json_rdf_cross_format_review_preserves_child_content():
    a, ia = load_library("personal", EXAMPLES / "personal.json")
    b, ib = load_library("group", FIXTURES / "derived-official-translator.rdf")
    review = build_review(a + b, [ia, ib])
    assert len(review["pairs"]) == 1
    decisions = decision_template(review)
    decisions["decisions"][review["pairs"][0]["id"]]["status"] = "same"
    _, suggestions = export_confirmed(review, decisions)
    assert len(suggestions["suggestions"]) == 1
    assert any("Attachment note" in json.dumps(value) for value in suggestions["suggestions"][0]["preserve_notes"])

