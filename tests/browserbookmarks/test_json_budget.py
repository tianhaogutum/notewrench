# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (see licenses/browser-bookmarks-MIT.txt)

import pytest

from notewrench.browserbookmarks import review
from notewrench.browserbookmarks.parsing import ReviewError
from notewrench.browserbookmarks.review import json_text, load_json


@pytest.mark.parametrize("data", [
    b'{"a":' + b"9" * 5000 + b"}",
    b'{"a":NaN}',
    b'{"a":1,"a":2}',
    b'{"a":1e99999}',
    b'{"a":[1,2,]}',
    b'{"a":"\xff"}',
])
def test_invalid_json_and_extreme_integer_are_clean_errors(tmp_path, data):
    path = tmp_path / "review.json"
    path.write_bytes(data)
    with pytest.raises(ReviewError):
        load_json(path)


@pytest.mark.parametrize(("attribute", "value", "data"), [
    ("MAX_JSON_CONTAINERS", 3, b"[{}, {}, {}]"),
    ("MAX_JSON_DEPTH", 3, b"[[[[]]]]"),
    ("MAX_JSON_EVENTS", 3, b"[1,2,3,4]"),
    ("MAX_JSON_STRING_BYTES", 3, b'{"text":"long"}'),
])
def test_json_budgets_reject_before_full_object_allocation(tmp_path, monkeypatch, attribute, value, data):
    path = tmp_path / "review.json"
    path.write_bytes(data)
    monkeypatch.setattr(review, attribute, value)
    with pytest.raises(ReviewError, match="budget"):
        load_json(path)


def test_json_writer_stops_before_expanding_repeated_custom_values(monkeypatch):
    monkeypatch.setattr(review, "MAX_REVIEW_BYTES", 100)
    with pytest.raises(ReviewError, match="Expanded JSON"):
        json_text(["context" * 10] * 100)


def test_json_round_trip_keeps_unicode_booleans_null_and_numeric_metadata(tmp_path):
    expected = {"note": "笔记 🌱", "flags": [True, False, None], "index": 25, "size": 1234567}
    path = tmp_path / "review.json"
    path.write_text(json_text(expected), encoding="utf-8")
    assert load_json(path) == expected


def test_utf8_bom_from_a_manual_editor_is_supported(tmp_path):
    path = tmp_path / "review.json"
    path.write_bytes(b"\xef\xbb\xbf{\"note\":\"ok\"}")
    assert load_json(path) == {"note": "ok"}
