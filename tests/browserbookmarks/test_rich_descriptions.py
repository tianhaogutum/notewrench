# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (see licenses/browser-bookmarks-MIT.txt)
import pytest

from notewrench.browserbookmarks.merging import apply_review
from notewrench.browserbookmarks.parsing import parse_html
from notewrench.browserbookmarks.review import create_review
from notewrench.browserbookmarks.verification import verify_export

RICH = b'''<!DOCTYPE NETSCAPE-Bookmark-file-1><DL>
<DT><A HREF="https://example.org/story">Story</A>
<DD>Definitions:<DL><DT>term <a href="https://example.org/context">reference</a><DD>meaning</DL>
<DT><A HREF="https://example.org/next">Next</A>
</DL>'''


def test_definition_lists_inside_descriptions_are_not_phantom_bookmarks():
    records, _ = parse_html(RICH, "s1", "html")
    assert [record["url"] for record in records] == ["https://example.org/story", "https://example.org/next"]
    assert "term reference" in records[0]["description"]
    assert "meaning" in records[0]["description"]
    assert records[1]["description"] == ""
    assert 'href="https://example.org/context"' in records[0]["dd_html"][0]


def test_rich_description_receipt_and_output_round_trip(tmp_path):
    source = tmp_path / "rich.html"
    source.write_bytes(RICH)
    path = tmp_path / "review/review.json"
    plan = create_review([(source, "html")], path.parent)
    apply_review(plan, path, tmp_path / "result")
    result = verify_export(tmp_path / "result")
    assert result["verified_outputs"] == result["verified_original_records"] == 2
    assert (tmp_path / "result/originals/s1.html").read_bytes() == RICH


@pytest.mark.parametrize(("label", "encoding", "title", "description"), [
    ("windows-1252", "cp1252", "Café “quotes”", "Price €10 — saved"),
    ("iso-8859-1", "cp1252", "Café “quotes”", "Price €10 — saved"),
    ("windows-1251", "cp1251", "Заметки", "Сохранить контекст"),
    ("shift_jis", "shift_jis", "資料", "保存したメモ"),
])
def test_declared_legacy_encodings_preserve_titles_and_description_text(label, encoding, title, description):
    document = f'<!DOCTYPE NETSCAPE-Bookmark-file-1><meta charset="{label}"><DL><DT><A HREF="https://example.org/">{title}</A><DD>{description}</DL>'
    records, _ = parse_html(document.encode(encoding), "s1", "html")
    assert records[0]["title"] == title
    assert records[0]["description"] == description


def test_browser_mode_warns_about_linkding_markers_without_reinterpreting_text():
    data = b'<DL><DT><A HREF="https://example.org/">Title</A><DD>Summary[linkding-notes]Important note[/linkding-notes]</DL>'
    records, _ = parse_html(data, "s1", "html")
    assert records[0]["notes"] == ""
    assert "Important note" in records[0]["description"]
    assert any("--linkding" in warning for warning in records[0]["warnings"])
