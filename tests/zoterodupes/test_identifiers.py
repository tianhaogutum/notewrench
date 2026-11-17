from pathlib import Path

import pytest

from notewrench.zoterodupes.matching import find_candidates
from notewrench.zoterodupes.model import Record, identifier_values, isbn_values, normalize_pmid
from notewrench.zoterodupes.readers import load_library
from .paths import UPSTREAM


def test_real_upstream_multi_isbn_field_matches_any_valid_value():
    records, _ = load_library("personal", UPSTREAM / "bbt-collected-notes.json")
    item = next(r for r in records if r.metadata.get("ISBN") == "978-0-7456-4658-9 978-0-7456-4659-6")
    assert isbn_values(item.metadata["ISBN"]) == ("9780745646589", "9780745646596")
    other = Record("group", "other", "export-id", {"title": "A translated title without shared wording", "type": "book", "ISBN": "9780745646596"}, {})
    pairs, _ = find_candidates([item, other])
    assert len(pairs) == 1
    assert "Exact ISBN" in pairs[0]["reasons"]
    assert any("edition" in warning for warning in pairs[0]["warnings"])


def test_real_csl_multiple_isbn10_values_convert_independently():
    records, _ = load_library("csl", UPSTREAM / "bbt-complex-dates.csl.json")
    item = next(r for r in records if r.metadata.get("ISBN") == "0-943396-04-2 0-85131-041-9")
    assert isbn_values(item.metadata["ISBN"]) == ("9780851310411", "9780943396040")


def test_mixed_identifier_field_ignores_invalid_trailing_number():
    assert isbn_values("0774803274; 08470502") == ("9780774803274",)
    assert isbn_values("not an ISBN") == ()


def test_real_bbt_tex_doi_extra_is_explicit_evidence():
    records, _ = load_library("personal", UPSTREAM / "bbt-extra-doi.json")
    item = records[0]
    assert identifier_values(item.metadata)["DOI"] == ("10.1007/3-540-48515-5_8",)
    other = Record("group", "other", "export-id", {"title": "A completely unrelated transcription", "DOI": "10.1007/3-540-48515-5_8"}, {})
    pairs, _ = find_candidates([item, other])
    assert len(pairs) == 1
    assert "DOI extracted from Extra" in pairs[0]["reasons"]
    assert "DOI" not in item.metadata  # original native metadata is not rewritten


def test_native_identifier_takes_precedence_over_extra_even_when_invalid():
    assert identifier_values({"DOI": "10.1234/native", "extra": "DOI: 10.1234/extra"})["DOI"] == ("10.1234/native",)
    assert identifier_values({"DOI": "bad native DOI", "extra": "DOI: 10.1234/extra"})["DOI"] == ()


def test_documented_zotero_extra_and_pmid_candidates():
    item = Record("personal", "a", "export-id", {"title": "An old title", "extra": "Some context\nPMID: 00123456\nDOI: 10.1234/example"}, {})
    other = Record("group", "b", "export-id", {"title": "A completely different transcription", "PMID": "https://pubmed.ncbi.nlm.nih.gov/123456/"}, {})
    pairs, _ = find_candidates([item, other])
    assert len(pairs) == 1
    assert "Exact PMID" in pairs[0]["reasons"]
    assert "PMID extracted from Extra" in pairs[0]["reasons"]


@pytest.mark.parametrize("value,result", [("PMID: 123456", "123456"), ("000123", "123"), ("https://www.ncbi.nlm.nih.gov/pubmed/123/", "123"), ("https://evil.example/123", ""), ("0", ""), ("12.5", "")])
def test_pmid_normalization(value, result):
    assert normalize_pmid(value) == result


def test_invalid_identifiers_retained_with_export_diagnostics():
    records, info = load_library("fixture", UPSTREAM / "bbt-auto-tags.json")
    assert records[0].metadata["DOI"] == "make it break"
    assert any("nonempty DOI" in warning for warning in info["warnings"])


def test_malformed_pubmed_url_is_diagnosed_not_an_uncaught_parse_error(tmp_path):
    import json
    path = tmp_path / "bad-url.json"
    path.write_text(json.dumps([{"key": "AAAA1234", "title": "Study", "PMID": "http://["}]))
    records, info = load_library("a", path)
    assert records[0].metadata["PMID"] == "http://["
    assert any("nonempty PMID" in warning for warning in info["warnings"])

