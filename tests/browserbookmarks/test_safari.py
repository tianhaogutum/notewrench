# SPDX-License-Identifier: MIT
# Copyright (c) 2026 bookmark-receipts contributors (see licenses/browser-bookmarks-MIT.txt)
from pathlib import Path

import pytest

from notewrench.browserbookmarks.merging import apply_review
from notewrench.browserbookmarks.parsing import ReviewError, parse_html
from notewrench.browserbookmarks.review import create_review
from notewrench.browserbookmarks.verification import verify_export

FIXTURES = Path(__file__).parent / "fixtures"


def test_upstream_safari_without_root_dl_keeps_every_top_level_bookmark():
    records, _ = parse_html((FIXTURES / "safari-no-root.html").read_bytes(), "s1", "html")
    assert [(record["title"], record["folder"]) for record in records] == [("Google", []), ("Chromium", [])]
    assert records[0]["added"] == "904914000"
    assert records[1]["added"] is None


def test_upstream_safari_folders_and_reading_list_paths():
    folders, _ = parse_html((FIXTURES / "safari-folders.html").read_bytes(), "s1", "html")
    assert [(record["title"], record["folder"]) for record in folders] == [
        ("Google", []), ("Example", ["Folder 1"]), ("Kitsune", ["Folder 1", "Folder 1.1"]),
    ]
    reading, _ = parse_html((FIXTURES / "safari-reading-list.html").read_bytes(), "s2", "html")
    assert reading[1]["folder"] == ["Reading List"]
    assert reading[2]["title"] == "Brian Wilson" and reading[2]["added"] == "-868878000"


def test_rootless_top_level_folder_then_root_bookmark_does_not_leak_path():
    data = b'''<!DOCTYPE NETSCAPE-Bookmark-file-1><HTML><H1>Bookmarks</H1>
<DT><H3 id="BookmarksBar">Favorites</H3><DL><p><DT><A HREF="https://example.org/a">A</A></DL><p>
<DT><A HREF="https://example.org/b">B</A></HTML>'''
    records, _ = parse_html(data, "s1", "html")
    assert records[0]["folder"] == ["Favorites"]
    assert records[1]["folder"] == []


def test_generic_html_outside_dl_still_fails_without_netscape_declaration():
    with pytest.raises(ReviewError, match="outside DL"):
        parse_html(b'<A HREF="https://example.org/nav">Navigation</A><DL></DL>', "s1", "html")
    with pytest.raises(ReviewError, match="expected Netscape"):
        parse_html(b'<!DOCTYPE NETSCAPE-Bookmark-file-10><A HREF="https://example.org/">Wrong format name</A>', "s1", "html")


def test_safari_bundle_round_trip_deep_verification(tmp_path):
    path = tmp_path / "review/review.json"
    plan = create_review([(FIXTURES / "safari-no-root.html", "html"), (FIXTURES / "safari-folders.html", "html"), (FIXTURES / "safari-reading-list.html", "html")], path.parent)
    for group in plan["groups"]:
        if len(group["members"]) > 1:
            group["decision"]["action"] = "merge"
    apply_review(plan, path, tmp_path / "result")
    verified = verify_export(tmp_path / "result")
    assert verified["verified_original_records"] == 8
    assert verified["verified_outputs"] == 6
