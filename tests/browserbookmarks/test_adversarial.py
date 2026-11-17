# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (see licenses/browser-bookmarks-MIT.txt)
import json

import pytest

from notewrench.browserbookmarks.cli import main
from notewrench.browserbookmarks.parsing import ReviewError, parse_html
from notewrench.browserbookmarks.review import canonical_json, create_review, load_review, sha256, status

DOCUMENT = '<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><DT><A HREF="https://example.org/">笔记</A></DL>'


def test_links_inside_descriptions_are_text_not_phantom_bookmarks():
    data = b'''<DL><DT><A HREF="https://example.org/a">A</A><DD>Read <a href="https://notes.example.org/">this context</a> carefully
<DT><A HREF="https://example.org/b">B</A></DL>'''
    records, _ = parse_html(data, "s1", "html")
    assert len(records) == 2
    assert records[0]["description"] == "Read this context carefully"
    assert records[1]["url"] == "https://example.org/b"


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-be", "utf-8-sig"])
def test_bom_encodings_preserve_non_ascii(encoding):
    data = DOCUMENT.encode(encoding)
    if encoding == "utf-16-be":
        data = b"\xfe\xff" + data
    records, _ = parse_html(data, "s1", "html")
    assert records[0]["title"] == "笔记"


def test_invalid_declared_utf8_cannot_silently_replace_note_characters():
    with pytest.raises(ReviewError, match="invalid utf-8"):
        parse_html(b'<meta charset=utf-8><DL><DT><A HREF="https://example.org/">bad \xff title</A></DL>', "s1", "html")


def test_valid_literal_replacement_character_is_not_rejected():
    records, _ = parse_html(('<meta charset=utf-8>' + DOCUMENT.replace("笔记", "valid � text")).encode(), "s1", "html")
    assert records[0]["title"] == "valid � text"


@pytest.mark.parametrize("value", [[], {}, None, 1, ["merge"], True])
def test_wrong_decision_action_types_are_clean_errors(tmp_path, value):
    source = tmp_path / "input.html"
    source.write_text(DOCUMENT)
    plan = create_review([(source, "html")], tmp_path / "review")
    plan["groups"][0]["decision"]["action"] = value
    path = tmp_path / "review/review.json"
    path.write_text(json.dumps(plan))
    assert main(["status", str(path)]) == 2


@pytest.mark.parametrize("custom", ["\ud800", "contains\x00nul"])
def test_invalid_custom_text_does_not_leave_partial_json(tmp_path, custom):
    source = tmp_path / "input.html"
    source.write_text(DOCUMENT)
    create_review([(source, "html")], tmp_path / "review")
    path = tmp_path / "review/review.json"
    before = path.read_bytes()
    group_id = load_review(path)["groups"][0]["id"]
    assert main(["decide", str(path), "--group", group_id, "--action", "merge", "--field", "notes=" + json.dumps({"value": custom})]) == 2
    assert path.read_bytes() == before


def test_snapshot_invalid_record_source_type_is_clean_error(tmp_path):
    source = tmp_path / "input.html"
    source.write_text(DOCUMENT)
    plan = create_review([(source, "html")], tmp_path / "review")
    plan["snapshot"]["records"][0]["source_id"] = []
    plan["snapshot_sha256"] = sha256(canonical_json(plan["snapshot"]))
    path = tmp_path / "review/review.json"
    path.write_text(json.dumps(plan))
    with pytest.raises(ReviewError, match="Invalid record source"):
        load_review(path)


def test_pending_singleton_is_counted_and_blocks_export(tmp_path):
    source = tmp_path / "input.html"
    source.write_text(DOCUMENT)
    plan = create_review([(source, "html")], tmp_path / "review")
    plan["groups"][0]["decision"]["action"] = "pending"
    assert status(plan)["pending"] == 1
