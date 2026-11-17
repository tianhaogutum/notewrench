import pandas as pd
import pytest
from bib_dedupe.bib_dedupe import prep

from notewrench.zoterodupes.matching import find_candidates
from notewrench.zoterodupes.model import Record


@pytest.mark.parametrize("item_type,expected", [("book", "book"), ("bookSection", "inbook"), ("chapter", "inbook"), ("conferencePaper", "inproceedings"), ("paper-conference", "inproceedings"), ("article-journal", "article"), ("report", "techreport")])
def test_bibdedupe_entry_type_and_container_contract(item_type, expected):
    record = Record("library", "a", "export-id", {"title": "Source title", "type": item_type, "publication": "Container title"}, {})
    row = record.matching_row()
    assert row["ENTRYTYPE"] == expected
    assert row["booktitle"] == row["journal"] == row["container_title"] == "Container title"
    prepared = prep(pd.DataFrame([row]), cpu=1, verbosity_level=0)
    assert prepared.iloc[0]["container_title"]


def test_equivalent_csl_and_zotero_types_do_not_emit_false_version_warning():
    records = [Record(label, "a", "export-id", {"title": "A study of title equivalence", "type": item_type}, {}) for label, item_type in [("native", "journalArticle"), ("csl", "article-journal")]]
    pairs, _ = find_candidates(records)
    assert len(pairs) == 1
    assert not any("Item types differ" in warning for warning in pairs[0]["warnings"])


def test_distinct_article_and_preprint_types_still_warn():
    records = [Record(label, "a", "export-id", {"title": "A study of title equivalence", "type": item_type}, {}) for label, item_type in [("native", "journalArticle"), ("preprint", "preprint")]]
    pairs, _ = find_candidates(records)
    assert any("Item types differ" in warning for warning in pairs[0]["warnings"])


def test_conference_adapter_enables_upstream_container_evidence():
    titles = ["Functional network topology in cognitive neuroscience", "Functional network topologies for cognitive neuroscience"]
    records = [Record(label, label, "export-id", {"title": title, "type": item_type, "publication": "Proceedings of Example Conference", "authors": "Smith, Jane", "date": "2020"}, {}) for label, title, item_type in zip(["native", "csl"], titles, ["conferencePaper", "paper-conference"])]
    pairs, _ = find_candidates(records)
    assert len(pairs) == 1
    assert pairs[0]["bib_dedupe_label"] == "duplicate"

