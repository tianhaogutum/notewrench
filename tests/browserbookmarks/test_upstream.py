# SPDX-License-Identifier: MIT
# Copyright (c) 2026 bookmark-receipts contributors (see licenses/browser-bookmarks-MIT.txt)
"""Offline integration with actual versioned linkding format code."""

import ast
import sys
import types
from pathlib import Path

import pytest

from notewrench.browserbookmarks._linkding_rules import normalize_url, parse_tag_string
from notewrench.browserbookmarks.compatibility import assess_linkding
from notewrench.browserbookmarks.merging import apply_review, make_outputs
from notewrench.browserbookmarks.parsing import parse_html
from notewrench.browserbookmarks.review import create_review

UPSTREAM = Path(__file__).parent / "upstream"
FIXTURES = Path(__file__).parent / "fixtures"


def load_upstream(version, kind):
    path = UPSTREAM / f"linkding_{version}_{kind}.py"
    tree = ast.parse(path.read_text())
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not (node.module or "").startswith("bookmarks")]
    module = types.ModuleType(f"test_linkding_{version}_{kind}")
    sys.modules[module.__name__] = module
    module.__dict__.update(Bookmark=object, parse_tag_string=parse_tag_string, normalize_url=normalize_url)
    exec(compile(tree, str(path), "exec"), module.__dict__)
    return module


@pytest.mark.parametrize(("version", "notes", "archive_tag", "modified"), [
    ("1.15", "", None, None),
    ("1.31", "Markdown **note**\nsecond line", "linkding:archived", None),
    ("1.47", "Markdown **note**\nsecond line", "linkding:bookmarks.archived", "1704067200"),
])
def test_real_exporter_version_fixtures(version, notes, archive_tag, modified):
    records, _ = parse_html((FIXTURES / f"linkding-v{version}.html").read_bytes(), "s1", "linkding")
    assert len(records) == 1
    record = records[0]
    assert record["notes"] == notes
    assert record["modified"] == modified
    assert record["toread"] == "1"
    if archive_tag:
        assert archive_tag in record["tags"]


@pytest.mark.parametrize("version", ["v1_31", "v1_47"])
def test_merged_notes_round_trip_through_actual_upstream_parser(tmp_path, version):
    path = tmp_path / "review/review.json"
    plan = create_review([(FIXTURES / "linkding-conflicts.html", "linkding")], path.parent)
    plan["groups"][0]["decision"]["action"] = "merge"
    apply_review(plan, path, tmp_path / "result")
    exported = (tmp_path / "result/bookmarks.html").read_text()
    records = load_upstream(version, "parser").parse(exported)
    assert len(records) == 1
    expected = make_outputs(plan)[0]["fields"]
    assert records[0].notes == expected["notes"]
    assert records[0].description == expected["description"]
    assert records[0].title == expected["title"]
    assert "Keep this first note" in records[0].notes and "Keep this second note" in records[0].notes
    assert records[0].tag_names == parse_tag_string(",".join(expected["tags"]))


def test_legacy_parser_still_reads_exported_url_and_description(tmp_path):
    path = tmp_path / "review/review.json"
    plan = create_review([(FIXTURES / "linkding-v1.15.html", "linkding")], path.parent)
    apply_review(plan, path, tmp_path / "result")
    records = load_upstream("v1_15", "parser").parse((tmp_path / "result/bookmarks.html").read_text())
    assert records[0].description == "Description legacy"
    assert records[0].href == "https://example.org/legacy"


def test_destination_collision_is_reported_without_merging_conservative_groups(tmp_path):
    source = tmp_path / "input.html"
    source.write_text('<DL><DT><A HREF="https://example.org/a?b=2&amp;a=1">A</A><DT><A HREF="https://example.org/a/?a=1&amp;b=2">B</A></DL>')
    plan = create_review([(source, "html")], tmp_path / "review")
    assert len(plan["groups"]) == 2
    outputs = make_outputs(plan)
    report = assess_linkding(outputs)
    collisions = [issue for issue in report["issues"] if issue["kind"] == "normalized_url_collision"]
    assert collisions[0]["output_ids"] == ["b000001", "b000002"]
    assert len(outputs) == 2


def test_tag_changes_and_legacy_archive_state_are_explicit():
    fields = {"url": "https://example.org/", "title": "Example", "tags": ["Research", "research", "two words", "linkding:archived", "x" * 65]}
    report = assess_linkding([{"id": "b1", "fields": fields}])
    tag_issue = next(issue for issue in report["issues"] if issue["kind"] == "tag_normalization")
    assert "two-words" in tag_issue["imported_tags"]
    assert "x" * 65 not in tag_issue["imported_tags"]
    assert any(issue["kind"] == "legacy_archive_tag" for issue in report["issues"])


def test_timestamp_selection_uses_upstream_fallback_not_digit_guess(tmp_path):
    source = tmp_path / "input.html"
    source.write_text('<DL><DT><A HREF="https://example.org/" ADD_DATE="100000000000">Far future</A><DT><A HREF="https://example.org/" ADD_DATE="1704067200000">2024 in milliseconds</A></DL>')
    plan = create_review([(source, "html")], tmp_path / "review")
    plan["groups"][0]["decision"]["action"] = "merge"
    assert make_outputs(plan)[0]["fields"]["added"] == "1704067200000"
