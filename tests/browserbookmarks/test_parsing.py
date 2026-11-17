# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (see licenses/browser-bookmarks-MIT.txt)

import pytest

from notewrench.browserbookmarks.parsing import ReviewError, parse_html, split_linkding_notes, url_key
from notewrench.browserbookmarks.review import create_review


@pytest.mark.parametrize(("left", "right"), [
    ("HTTPS://Example.COM:443", "https://example.com/"),
    ("http://EXAMPLE.com:80/a", "http://example.com/a"),
    ("https://[2001:DB8::1]:443/a", "https://[2001:db8::1]/a"),
])
def test_conservative_equivalences(left, right):
    assert url_key(left)[0] == url_key(right)[0]


@pytest.mark.parametrize(("left", "right"), [
    ("http://example.com/a", "https://example.com/a"),
    ("https://example.com/a", "https://example.com/a/"),
    ("https://example.com/a#one", "https://example.com/a#two"),
    ("https://example.com/?a=1&b=2", "https://example.com/?b=2&a=1"),
    ("https://example.com/?utm_source=one", "https://example.com/"),
    ("https://example.com/%2f", "https://example.com/%2F"),
    ("https://example.com/", "https://example.com/?"),
    ("https://example.com/", "https://example.com/#"),
    ("https://example.com/A", "https://example.com/a"),
    ("https://example.com:/", "https://example.com/"),
    ("https://User@example.com/", "https://user@example.com/"),
    ("javascript:Alert(1)", "javascript:alert(1)"),
    ("https://example.com/\na", "https://example.com/a"),
])
def test_significant_differences_are_not_merged(left, right):
    assert url_key(left)[0] != url_key(right)[0]


def test_description_metadata_does_not_leak_between_bookmarks():
    data = b'''<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><p>
<DT><A HREF="https://example.org/a" TAGS="X,y" PRIVATE="1">First</A><DD>First note
<DT><A HREF="https://example.org/b">Second</A>
<DT><H3>folder/with/slashes</H3><DD>Folder description
<DL><p><DT><A HREF="https://example.org/c">Third</A><DD>line one<br>line two</DL><p>
</DL>'''
    records, _ = parse_html(data, "s1", "html")
    assert records[0]["description"] == "First note"
    assert records[1]["description"] == ""
    assert records[1]["tags"] == [] and records[1]["private"] is None
    assert records[2]["folder"] == ["folder/with/slashes"]
    assert records[2]["description"] == "line one\nline two"


def test_marker_parsing_is_explicit_and_ambiguous_text_is_retained():
    text = "description[linkding-notes]note[/linkding-notes]"
    assert split_linkding_notes(text, "html") == (text, "", [])
    assert split_linkding_notes(text, "linkding") == ("description", "note", [])
    for ambiguous in ("[linkding-notes]unfinished", text + text):
        description, notes, warnings = split_linkding_notes(ambiguous, "linkding")
        assert description == ambiguous and notes == "" and warnings


@pytest.mark.parametrize("data", [b"not a bookmark file", b"<DL><A>missing URL</A></DL>", b"<DL>\x00</DL>"])
def test_invalid_input_fails_without_silently_skipping(data):
    with pytest.raises(ReviewError):
        parse_html(data, "s1", "html")


def test_failed_scan_removes_staging_and_never_touches_inputs(tmp_path):
    source = tmp_path / "bad.html"
    source.write_bytes(b"not HTML")
    with pytest.raises(ReviewError):
        create_review([(source, "html")], tmp_path / "review")
    assert source.read_bytes() == b"not HTML"
    assert not (tmp_path / "review").exists()
    assert not list(tmp_path.glob(".review-*"))
