import copy
import json

import pytest

from notewrench.zoterodupes.cli import main
from notewrench.zoterodupes.decisions import carry_decisions, decision_template, export_confirmed, validate_review
from notewrench.zoterodupes.model import InputError, digest


def refresh(review):
    for record in review["records"].values():
        record["fingerprint"] = digest({k: v for k, v in record.items() if k != "fingerprint"})
    review["review_id"] = "review-" + digest({k: v for k, v in review.items() if k != "review_id"})[:32]
    return review


def decisions_with_one_confirmation(review):
    payload = decision_template(review)
    pair = review["pairs"][0]
    payload["decisions"][pair["id"]].update(status="same", preferred=pair["left"], comment="A considered review note")
    return payload


def test_carry_only_unchanged_records_to_new_snapshot(realistic_review):
    old = realistic_review
    payload = decisions_with_one_confirmation(old)
    new = copy.deepcopy(old)
    new["libraries"][0]["sha256"] = "new formatting of the same export"
    refresh(new)
    carried, audit = carry_decisions(old, payload, new)
    assert old["review_id"] != new["review_id"]
    assert carried["review_id"] == new["review_id"]
    assert carried["decisions"] == payload["decisions"]
    assert len(audit["carried"]) == 3
    assert audit["reset_changed"] == []
    old_map = export_confirmed(old, payload)[0]["correspondences"]
    new_map = export_confirmed(new, carried)[0]["correspondences"]
    assert old_map[0]["correspondence_id"] == new_map[0]["correspondence_id"]


@pytest.mark.parametrize("field", ["notes", "tags", "annotations", "attachments", "raw", "metadata"])
def test_any_source_change_resets_decision_including_child_content(realistic_review, field):
    old = realistic_review
    payload = decisions_with_one_confirmation(old)
    new = copy.deepcopy(old)
    pair = old["pairs"][0]
    changed = new["records"][pair["left"]]
    if isinstance(changed[field], list):
        changed[field].append({"changed": "new information"})
    else:
        changed[field]["changed"] = "new information"
    refresh(new)
    carried, audit = carry_decisions(old, payload, new)
    assert carried["decisions"][pair["id"]] == {"status": "unreviewed", "preferred": None, "comment": ""}
    assert any(row["pair_id"] == pair["id"] for row in audit["reset_changed"])
    assert export_confirmed(new, carried)[0]["correspondences"] == []
    assert payload["decisions"][pair["id"]]["status"] == "same"


def test_absent_pairs_are_audited_and_never_imported(realistic_review):
    old = realistic_review
    payload = decisions_with_one_confirmation(old)
    new = copy.deepcopy(old)
    removed = new["pairs"].pop(0)
    # Clusters are navigational; rebuilding not needed for the carryover model.
    refresh(new)
    carried, audit = carry_decisions(old, payload, new)
    assert removed["id"] not in carried["decisions"]
    assert audit["absent_from_new"][0]["previous_decision"]["status"] == "same"


def test_new_candidate_pair_starts_unreviewed(realistic_review):
    new = realistic_review
    old = copy.deepcopy(new)
    added = old["pairs"].pop()
    refresh(old)
    payload = decisions_with_one_confirmation(old)
    carried, audit = carry_decisions(old, payload, new)
    assert carried["decisions"][added["id"]]["status"] == "unreviewed"
    assert audit["new_pairs"] == [added["id"]]


def test_carry_cli_emits_audit_and_loadable_decisions(realistic_review, tmp_path):
    old = realistic_review
    new = copy.deepcopy(old)
    new["libraries"][0]["file"] = "refreshed.json"
    refresh(new)
    files = {"old.json": old, "new.json": new, "decisions.json": decisions_with_one_confirmation(old)}
    for name, value in files.items():
        (tmp_path / name).write_text(json.dumps(value))
    out = tmp_path / "carried"
    assert main(["carry-decisions", "--old-review", str(tmp_path / "old.json"), "--new-review", str(tmp_path / "new.json"), "--decisions", str(tmp_path / "decisions.json"), "--output", str(out)]) == 0
    assert (out / "carryover-audit.json").is_file()
    carried = json.loads((out / "decisions.json").read_text())
    assert len(export_confirmed(new, carried)[0]["correspondences"]) == 1


def test_self_consistent_review_with_bad_record_fingerprint_is_rejected(realistic_review):
    review = copy.deepcopy(realistic_review)
    next(iter(review["records"].values()))["raw"]["changed"] = True
    review["review_id"] = "review-" + digest({k:v for k,v in review.items() if k != "review_id"})[:32]
    with pytest.raises(InputError, match="source record fingerprint"):
        validate_review(review)


def test_self_consistent_review_with_malformed_pair_is_actionable(realistic_review):
    review = copy.deepcopy(realistic_review)
    review["pairs"][0].pop("warnings")
    review["review_id"] = "review-" + digest({k:v for k,v in review.items() if k != "review_id"})[:32]
    with pytest.raises(InputError, match="Invalid review at pairs"):
        validate_review(review)


def test_review_cannot_drop_library_provenance(realistic_review):
    review = copy.deepcopy(realistic_review)
    review["libraries"][0]["label"] = "unrelated-library"
    review["review_id"] = "review-" + digest({k:v for k,v in review.items() if k != "review_id"})[:32]
    with pytest.raises(InputError, match="unlisted library"):
        validate_review(review)

