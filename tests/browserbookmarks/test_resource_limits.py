# SPDX-License-Identifier: MIT
# Copyright (c) 2026 bookmark-receipts contributors (see licenses/browser-bookmarks-MIT.txt)
import pytest

from notewrench.browserbookmarks import parsing, review
from notewrench.browserbookmarks.parsing import ReviewError, parse_html, url_key
from notewrench.browserbookmarks.review import create_review


def test_ipvfuture_literal_does_not_collide_with_dns_hostname():
    assert url_key("https://[v1.example]/a")[0] != url_key("https://v1.example/a")[0]
    assert url_key("https://[v1.a:b]/a")[0] != url_key("https://[v1.A:B]/a")[0]


def test_inline_description_heading_text_is_retained():
    records, _ = parse_html(b'<DL><DT><A HREF="https://example.org/">Title</A><DD>Intro<h3>Important context</h3>Conclusion</DL>', "s1", "html")
    assert len(records) == 1
    assert "Important context" in records[0]["description"]
    assert "contextConclusion" not in records[0]["description"]


def test_anchor_budget_refuses_oversized_dom_before_publishing(tmp_path, monkeypatch):
    monkeypatch.setattr(parsing, "MAX_ANCHORS", 3)
    source = tmp_path / "many.html"
    source.write_text('<DL>' + '<DT><A HREF="https://example.org/">A</A>' * 4 + '</DL>')
    with pytest.raises(ReviewError, match="HTML-anchor complexity limit"):
        create_review([(source, "html")], tmp_path / "review")
    assert not (tmp_path / "review").exists()


def test_depth_budget_is_clear_instead_of_recursive_failure(monkeypatch):
    monkeypatch.setattr(parsing, "MAX_DL_DEPTH", 4)
    with pytest.raises(ReviewError, match="DL nesting limit"):
        parse_html(b"<DL>" * 5 + b"</DL>" * 5, "s1", "html")


def test_non_bookmark_tags_and_generic_depth_are_also_bounded(monkeypatch):
    monkeypatch.setattr(parsing, "MAX_TAGS", 4)
    with pytest.raises(ReviewError, match="HTML-tag complexity"):
        parse_html(b"<DL><br><br><br><br></DL>", "s1", "html")
    monkeypatch.setattr(parsing, "MAX_TAGS", 100)
    monkeypatch.setattr(parsing, "MAX_ELEMENT_DEPTH", 4)
    with pytest.raises(ReviewError, match="HTML nesting limit"):
        parse_html(b"<DL>" + b"<span>" * 4 + b"text</DL>", "s1", "html")


def test_combined_record_budget_and_metadata_expansion_budget(tmp_path, monkeypatch):
    source = tmp_path / "bookmarks.html"
    source.write_text('<DL>' + '<DT><A HREF="https://example.org/">A</A>' * 3 + '</DL>')
    monkeypatch.setattr(review, "MAX_RECORDS", 4)
    with pytest.raises(ReviewError, match="Combined inputs"):
        create_review([(source, "html"), (source, "html")], tmp_path / "record-budget")
    monkeypatch.setattr(review, "MAX_RECORD_JSON_BYTES", 10)
    with pytest.raises(ReviewError, match="Expanded bookmark metadata"):
        create_review([(source, "html")], tmp_path / "metadata-budget")
    assert not (tmp_path / "record-budget").exists()
    assert not (tmp_path / "metadata-budget").exists()


def test_expanded_html_budget_cleans_unpublished_result(tmp_path, monkeypatch):
    from notewrench.browserbookmarks import merging

    source = tmp_path / "bookmarks.html"
    source.write_text('<DL><DT><A HREF="https://example.org/">Title</A><DD>' + '&amp;' * 200 + '</DL>')
    path = tmp_path / "review/review.json"
    plan = create_review([(source, "html")], path.parent)
    monkeypatch.setattr(merging, "MAX_HTML_BYTES", 100)
    with pytest.raises(ReviewError, match="Expanded bookmark HTML"):
        merging.apply_review(plan, path, tmp_path / "result")
    assert not (tmp_path / "result").exists()
    assert not list(tmp_path.glob(".result-*"))


def test_oversized_static_view_falls_back_without_losing_review_data(tmp_path, monkeypatch):
    from notewrench.browserbookmarks import rendering
    from notewrench.browserbookmarks.review import load_review

    monkeypatch.setattr(rendering, "MAX_VIEW_BYTES", 20)
    source = tmp_path / "bookmarks.html"
    source.write_text('<DL><DT><A HREF="https://example.org/">Title</A><DD>Important context</DL>')
    path = tmp_path / "review/review.json"
    create_review([(source, "html")], path.parent)
    assert "Full HTML view exceeded" in (path.parent / "review.html").read_text()
    assert load_review(path)["snapshot"]["records"][0]["description"] == "Important context"


def test_custom_array_budget_applies_to_the_whole_value(monkeypatch):
    from notewrench.browserbookmarks.review import FIELDS, validate_decision

    monkeypatch.setattr(review, "MAX_INPUT_BYTES", 100)
    group = {"id": "g-test", "members": ["s1:1"], "decision": {"action": "merge", "fields": {field: "@s1:1" for field in FIELDS}}}
    group["decision"]["fields"]["tags"] = {"value": ["a" * 60, "b" * 60]}
    with pytest.raises(ReviewError, match="serialized-value budget"):
        validate_decision(group)
