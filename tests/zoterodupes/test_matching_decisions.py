import copy

import pandas as pd
import pytest

from notewrench.zoterodupes.decisions import decision_template, export_confirmed, validate_decisions
from notewrench.zoterodupes.matching import block_upper_bound, build_review, cross_library_block_records, find_candidates
from notewrench.zoterodupes.model import InputError, Record


def record(library, key, title, **metadata):
    return Record(library, key, "zotero-key", {"title": title, **metadata}, {"title": title})


def test_realistic_review_is_pairwise_with_conflict_warnings(realistic_review):
    review = realistic_review
    assert len(review["pairs"]) == 3
    assert sorted(len(c["members"]) for c in review["clusters"]) == [2, 3]
    for pair in review["pairs"]:
        assert review["records"][pair["left"]]["library"] != review["records"][pair["right"]]["library"]
    assert any(any("Conflicting DOI" in w for w in p["warnings"]) for p in review["pairs"])
    assert any("Exact DOI" in p["reasons"] for p in review["pairs"])


def test_sparse_title_candidates_without_authors_or_years():
    records = [record("personal", "a", "An <i>unusually interesting</i> bibliography study"), record("group", "b", "An unusually interesting bibliography study")]
    pairs, _ = find_candidates(records)
    assert len(pairs) == 1
    assert "Exact normalized title" in pairs[0]["reasons"]


def test_identical_identifier_even_with_missing_titles():
    pairs, _ = find_candidates([record("a", "1", "", DOI="10.1234/test"), record("b", "2", "", DOI="https://doi.org/10.1234/TEST")])
    assert len(pairs) == 1 and "Exact DOI" in pairs[0]["reasons"]


def test_no_within_library_candidates():
    pairs, _ = find_candidates([record("a", "1", "An identical title"), record("a", "2", "An identical title")])
    assert pairs == []


def test_caps_fail_before_expensive_matching():
    with pytest.raises(InputError, match="Title review would compare"):
        find_candidates([record("a", "1", "A"), record("b", "2", "B"), record("b", "3", "C")], max_title_comparisons=1)
    from bib_dedupe.bib_dedupe import prep
    data = [record("a", str(i), "An identical title", DOI="10.1234/same").matching_row() for i in range(20)]
    with pytest.raises(InputError, match="blocking would allocate"):
        block_upper_bound(prep(pd.DataFrame(data), cpu=1, verbosity_level=0), 10)


def test_unreviewed_and_rejected_pairs_never_export(realistic_review):
    decisions = decision_template(realistic_review)
    assert export_confirmed(realistic_review, decisions)[0]["correspondences"] == []
    first = next(pair for pair in realistic_review["pairs"] if all(realistic_review["records"][pair[side]]["annotations"] for side in ["left", "right"]) and not pair["warnings"])
    decisions["decisions"][first["id"]]["status"] = "different"
    assert export_confirmed(realistic_review, decisions)[0]["correspondences"] == []
    decisions["decisions"][first["id"]]["status"] = "same"
    mapping, suggestions = export_confirmed(realistic_review, decisions)
    assert len(mapping["correspondences"]) == len(suggestions["suggestions"]) == 1
    assert len(suggestions["suggestions"][0]["preserve_notes"]) == 2
    assert len(suggestions["suggestions"][0]["preserve_annotations"]) == 2
    assert suggestions["suggestions"][0]["source_records"]


def test_decisions_do_not_transitively_confirm(realistic_review):
    decisions = decision_template(realistic_review)
    decisions["decisions"][realistic_review["pairs"][0]["id"]]["status"] = "same"
    assert len(export_confirmed(realistic_review, decisions)[0]["correspondences"]) == 1


@pytest.mark.parametrize("change", ["snapshot", "pair", "status", "preferred", "comment"])
def test_bad_decisions_rejected(realistic_review, change):
    decisions = decision_template(realistic_review)
    pair = realistic_review["pairs"][0]
    if change == "snapshot": decisions["review_id"] = "old"
    if change == "pair": decisions["decisions"]["unknown"] = {"status": "same"}
    if change == "status": decisions["decisions"][pair["id"]]["status"] = "merge"
    if change == "preferred": decisions["decisions"][pair["id"]]["preferred"] = "unrelated-id"
    if change == "comment": decisions["decisions"][pair["id"]]["comment"] = ["bad"]
    with pytest.raises(InputError): validate_decisions(realistic_review, decisions)


def test_tampered_review_fingerprint_rejected(realistic_review):
    review = copy.deepcopy(realistic_review)
    review["pairs"].pop()
    with pytest.raises(InputError, match="fingerprint"):
        export_confirmed(review, decision_template(review))


def test_contradictory_pair_decisions_rejected():
    records = [record(label, label, "A very specific identical study") for label in ["a", "b", "c"]]
    review = build_review(records, [{"label": label} for label in ["a", "b", "c"]])
    decisions = decision_template(review)
    for value in decisions["decisions"].values(): value["status"] = "same"
    decisions["decisions"][review["pairs"][0]["id"]]["status"] = "different"
    with pytest.raises(InputError, match="contradictory"):
        validate_decisions(review, decisions)


@pytest.mark.parametrize("field,value", [("status", []), ("status", {}), ("preferred", []), ("comment", None)])
def test_invalid_decision_types_are_actionable(realistic_review, field, value):
    decisions = decision_template(realistic_review)
    decisions["decisions"][realistic_review["pairs"][0]["id"]][field] = value
    with pytest.raises(InputError):
        validate_decisions(realistic_review, decisions)


def test_preselection_preserves_upstream_block_and_match_results():
    from bib_dedupe.bib_dedupe import prep, block, match
    from notewrench.zoterodupes.readers import load_library
    from .paths import UPSTREAM
    a, _ = load_library("a", UPSTREAM / "bbt-all-fields.json")
    b, _ = load_library("b", UPSTREAM / "bbt-all-fields.csl.json")
    frame = prep(pd.DataFrame([r.matching_row() for r in a + b]), cpu=1, verbosity_level=0)
    all_blocked = block(frame, cpu=1, verbosity_level=0)
    selected = cross_library_block_records(frame)
    selected_blocked = block(selected, cpu=1, verbosity_level=0)
    def normalize(data):
        return {tuple(sorted((str(row["ID_1"]), str(row["ID_2"])))) for row in data.to_dict(orient="records")}
    assert normalize(all_blocked) == normalize(selected_blocked)
    original = match(all_blocked, cpu=1, verbosity_level=0).sort_values(["ID_1", "ID_2"]).reset_index(drop=True)
    improved = match(selected_blocked, cpu=1, verbosity_level=0).sort_values(["ID_1", "ID_2"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(original, improved)


def test_preselection_skips_same_library_only_blocks_and_singletons():
    from bib_dedupe.bib_dedupe import prep
    records = [record("a", "1", "A specific isolated study", DOI="10.1234/same"), record("a", "2", "Same-library duplicate", DOI="10.1234/same"), record("b", "3", "Another independent investigation")]
    frame = prep(pd.DataFrame([r.matching_row() for r in records]), cpu=1, verbosity_level=0)
    assert cross_library_block_records(frame).empty

