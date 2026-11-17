# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (see licenses/browser-bookmarks-MIT.txt)
"""Reproducible generated cases complement the fixed real-format regressions."""

import copy
import os
from datetime import UTC, datetime
from types import SimpleNamespace

from hypothesis import given, settings, strategies as st

from notewrench.browserbookmarks import _linkding_exporter as producer
from notewrench.browserbookmarks.merging import export_html, make_outputs
from notewrench.browserbookmarks.parsing import NOTES_CLOSE, NOTES_OPEN, parse_html, url_key
from notewrench.browserbookmarks.review import groups_for, proposed_decision

EXAMPLES = int(os.environ.get("BOOKMARK_RECEIPTS_FUZZ_EXAMPLES", "300"))
text = st.text(st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"), max_size=100)
note = text.filter(lambda value: NOTES_OPEN not in value and NOTES_CLOSE not in value)


def normalize_html_lines(value):
    return value.replace("\r\n", "\n").replace("\r", "\n")


@settings(max_examples=EXAMPLES, deadline=None, derandomize=True)
@given(title=text, first=note, second=note)
def test_generated_unicode_notes_survive_real_producer_and_merge_round_trip(title, first, second):
    epoch = datetime(2024, 1, 1, tzinfo=UTC)
    bookmarks = [SimpleNamespace(
        url="https://example.org/story", resolved_title=title, resolved_description="Description",
        notes=value, tag_names=["Research", "Read"], is_archived=False, unread=True, shared=False,
        date_added=epoch, date_modified=epoch,
    ) for value in (first, second)]
    data = producer.export_netscape_html(bookmarks).encode()
    records, _ = parse_html(data, "s1", "linkding")
    assert [record["notes"] for record in records] == [normalize_html_lines(first), normalize_html_lines(second)]
    assert all(record["title"] == normalize_html_lines(title) for record in records)
    original = copy.deepcopy(records)
    groups = groups_for(records)
    for group in groups:
        group["decision"] = {**proposed_decision(group), "action": "merge"}
    outputs = make_outputs({"groups": groups, "snapshot": {"records": records, "sources": []}})
    parsed, _ = parse_html(export_html(outputs).encode(), "output", "linkding")
    assert len(parsed) == 1
    assert parsed[0]["notes"] == outputs[0]["fields"]["notes"]
    assert parsed[0]["description"] == outputs[0]["fields"]["description"]
    assert parsed[0]["tags"] == ["Research", "Read"]
    assert records == original


path = st.text(alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/-._~%", max_size=80)
query = st.one_of(st.none(), st.text(alphabet="abcXYZ012=&%+;", max_size=60))
fragment = st.one_of(st.none(), st.text(alphabet="abcXYZ012=&%+;/-", max_size=60))


@settings(max_examples=EXAMPLES, deadline=None, derandomize=True)
@given(scheme=st.sampled_from(["http", "https"]), suffix=path, parameters=query, anchor=fragment)
def test_generated_http_keys_are_idempotent_and_only_fold_approved_parts(scheme, suffix, parameters, anchor):
    tail = "/" + suffix
    if parameters is not None:
        tail += "?" + parameters
    if anchor is not None:
        tail += "#" + anchor
    port = "80" if scheme == "http" else "443"
    original = f"{scheme.upper()}://EXAMPLE.ORG:{port}{tail}"
    canonical = f"{scheme}://example.org{tail}"
    key = url_key(original)[0]
    assert key == url_key(canonical)[0]
    assert key.startswith("web:")
    assert url_key(key.removeprefix("web:"))[0] == key
