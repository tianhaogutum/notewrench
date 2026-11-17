import json
import shutil
from pathlib import Path

import pytest
from .test_workflow import FIXTURES, html

from notewrench.relink.archive import html_document
from notewrench.relink.workflow import build_index, scan_notes


def test_real_nb_source_only_capture_supports_body_phrase(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    shutil.copyfile(
        FIXTURES / "nb-example-com.bookmark.md",
        archive / "renamed.bookmark.md",
    )
    db = tmp_path / "index.sqlite"
    assert build_index(archive, db)["documents"] == 1
    note = tmp_path / "note.md"
    note.write_text(
        "[source](https://lost.example/unknown) says "
        "“This domain is for use in documentation examples without needing permission.”"
    )
    plan = scan_notes(db, [note])
    assert len(plan["links"][0]["candidates"]) == 1
    assert plan["links"][0]["candidates"][0]["kind"] == "nb_bookmark"


def test_real_wget_warc_capture(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    shutil.copyfile(FIXTURES / "wget-example.warc.gz", archive / "wget.warc.gz")
    db = tmp_path / "index.sqlite"
    result = build_index(archive, db)
    assert result["documents"] == 1
    assert result["warnings"] == []
    note = tmp_path / "note.md"
    note.write_text("[original](https://example.com)")
    plan = scan_notes(db, [note])
    assert plan["links"][0]["candidates"][0]["kind"] == "warc_response"


def test_current_archivebox_typed_results_resolve_plugin_outputs(tmp_path):
    folder = tmp_path / "archive/snapshot"
    html(folder / "singlefile/index.html", body="Main captured article.")
    html(folder / "wget/example.org/story.html", body="Another capture of the same article.")
    html(folder / "wget/example.org/iframe.html", body="An unrelated embedded document.")
    records = [
        {"type": "Snapshot", "id": "snapshot-1", "url": "https://old.example/story"},
        {
            "type": "ArchiveResult",
            "snapshot_id": "snapshot-1",
            "plugin": "singlefile",
            "status": "succeeded",
            "output_str": "index.html",
            "output_files": {"index.html": {"size": 100}},
        },
        {
            "type": "ArchiveResult",
            "snapshot_id": "snapshot-1",
            "plugin": "wget",
            "status": "succeeded",
            "output_str": "example.org/story.html",
            "output_files": {
                "example.org/story.html": {"size": 100},
                "example.org/iframe.html": {"size": 100},
            },
        },
        {
            "type": "ArchiveResult",
            "snapshot_id": "unrelated",
            "plugin": "wget",
            "status": "succeeded",
            "output_str": "example.org/iframe.html",
        },
    ]
    (folder / "index.jsonl").write_text("\n".join(json.dumps(r) for r in records))
    db = tmp_path / "index.sqlite"
    build_index(tmp_path / "archive", db)
    note = tmp_path / "note.md"
    note.write_text("[source](https://old.example/story)")
    plan = scan_notes(db, [note])
    targets = {Path(c["target"]).name for c in plan["links"][0]["candidates"]}
    assert targets == {"index.html", "story.html"}


def test_jsonl_invalid_line_does_not_hide_later_valid_resource(tmp_path):
    folder = tmp_path / "archive"
    html(folder / "page.html", body="A real page from a resource manifest.")
    record = {"url": "https://old.example/story", "path": "page.html", "status": 200}
    (folder / "index.jsonl").write_text("{invalid json}\n" + json.dumps(record) + "\n")
    db = tmp_path / "index.sqlite"
    result = build_index(folder, db)
    assert len(result["warnings"]) == 1
    note = tmp_path / "note.md"
    note.write_text("[source](https://old.example/story)")
    assert scan_notes(db, [note])["links"][0]["candidates"]


def test_bad_jsonl_record_shape_does_not_hide_later_valid_resource(tmp_path):
    folder = tmp_path / "archive"
    html(folder / "page.html", body="A usable archived article.")
    bad = {"type": [], "url": "https://old.example/story"}
    good = {"url": "https://old.example/story", "path": "page.html", "status": 200}
    (folder / "index.jsonl").write_text(json.dumps(bad) + "\n" + json.dumps(good) + "\n")
    db = tmp_path / "index.sqlite"
    result = build_index(folder, db)
    assert len(result["warnings"]) == 1
    note = tmp_path / "note.md"
    note.write_text("[source](https://old.example/story)")
    assert scan_notes(db, [note])["links"][0]["candidates"]


def test_html_metadata_and_encodings(tmp_path):
    data = (
        '<meta charset="shift_jis"><title>日本語のタイトル</title>'
        '<link rel="canonical" href="https://example.org/canonical">'
        '<meta property="og:url" content="https://example.org/social">'
        "<p>日本語で保存した本文の引用部分を正しく検索します。</p>"
        "<script>Title must not become searchable body content.</script>"
    ).encode("shift_jis")
    doc = html_document(data, tmp_path / "page.html")
    assert doc.title == "日本語のタイトル"
    assert "日本語で保存した本文" in doc.body
    assert "Title" not in doc.body
    assert {item["kind"] for item in doc.urls} == {"declared_alias"}


def test_unicode_explicit_quote_keys_match_normalized_markdown_urls(tmp_path):
    archive = tmp_path / "archive"
    html(archive / "unknown.html", body="A distinctive passage from the lost original article.")
    db = tmp_path / "index.sqlite"
    build_index(archive, db)
    note = tmp_path / "note.md"
    note.write_text("[source](https://例え.jp/本)")
    plan = scan_notes(
        db,
        [note],
        {
            "https://例え.jp/本": "A distinctive passage from the lost original article.",
        },
    )
    assert len(plan["links"][0]["candidates"]) == 1


def test_url_match_reports_quote_mismatch_without_claiming_body_identity(tmp_path):
    archive = tmp_path / "archive"
    html(archive / "login.html", "https://old.example/story", "Please log in to continue.")
    db = tmp_path / "index.sqlite"
    build_index(archive, db)
    note = tmp_path / "note.md"
    note.write_text(
        "[source](https://old.example/story) said "
        "“A distinctive passage from the lost original article.”"
    )
    plan = scan_notes(db, [note])
    candidate = plan["links"][0]["candidates"][0]
    assert candidate["evidence"][0]["kind"] == "source_url"
    assert candidate["quote_checks"][0]["found"] is False
    assert candidate["preview"] == "please log in to continue."


def test_title_outside_head_never_becomes_body_evidence(tmp_path):
    quote = "A distinctive title that does not appear in the article body."
    doc = html_document(
        f"<title>{quote}</title><h1>{quote}</h1><p>An unrelated short paragraph.</p>".encode(),
        tmp_path / "malformed.html",
    )
    assert doc.title == quote
    assert quote.casefold() not in doc.body


@pytest.mark.parametrize("headers", [True, False])
def test_archivebox_collection_export_recovers_missing_snapshot_index(tmp_path, headers):
    archive = tmp_path / "archivebox"
    snapshot = archive / "archive/1234567890.123"
    html(snapshot / "singlefile.html", body="An existing archived article.")
    html(snapshot / "index.html", body="ArchiveBox navigation UI should not be a candidate.")
    entry = {
        "url": "https://old.example/story",
        "timestamp": "1234567890.123",
        "canonical": {"singlefile_path": "singlefile.html", "index_html_path": "index.html"},
    }
    data = {"schema": "archivebox.index.json", "links": [entry]} if headers else [entry]
    (archive / "index.json").write_text(json.dumps(data))
    db = tmp_path / "index.sqlite"
    result = build_index(archive, db)
    assert result["documents"] == 1
    note = tmp_path / "note.md"
    note.write_text("[source](https://old.example/story)")
    plan = scan_notes(db, [note])
    assert [Path(c["target"]).name for c in plan["links"][0]["candidates"]] == ["singlefile.html"]


def test_archivebox_collection_timestamp_cannot_traverse_paths(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "index.json").write_text(
        json.dumps(
            {
                "links": [
                    {"url": "https://old.example/story", "timestamp": "../../outside"},
                ]
            }
        )
    )
    result = build_index(archive, tmp_path / "index.sqlite")
    assert result["documents"] == 0
    assert result["warnings"]


def test_resource_manifest_index_html_is_article_content(tmp_path):
    archive = tmp_path / "archive"
    html(archive / "index.html", body="This index.html is a captured page, not a navigation UI.")
    (archive / "index.jsonl").write_text(
        json.dumps(
            {
                "url": "https://old.example/story",
                "path": "index.html",
                "status": 200,
                "resourceType": "document",
                "mimeType": "text/html",
            }
        )
        + "\n"
    )
    db = tmp_path / "index.sqlite"
    result = build_index(archive, db)
    assert result["documents"] == 1
    note = tmp_path / "note.md"
    note.write_text("[source](https://old.example/story)")
    assert scan_notes(db, [note])["links"][0]["candidates"]


@pytest.mark.parametrize("metadata", ["source_url", "declared_alias"])
def test_incorrect_url_copy_does_not_hide_real_body_quote_candidate(tmp_path, metadata):
    archive = tmp_path / "archive"
    quote = "A distinctive passage that only appears in the original saved article."
    if metadata == "source_url":
        html(archive / "login.html", "https://old.example/story", "Please log in to continue.")
    else:
        html(archive / "login.html", body="Please log in to continue.")
        with (archive / "login.html").open("a") as stream:
            stream.write('<link rel="canonical" href="https://old.example/story">')
    html(archive / "true-copy.html", body=quote)
    db = tmp_path / "index.sqlite"
    build_index(archive, db)
    note = tmp_path / "note.md"
    note.write_text(f"[source](https://old.example/story) said “{quote}”")
    plan = scan_notes(db, [note])
    targets = {Path(candidate["target"]).name for candidate in plan["links"][0]["candidates"]}
    assert targets == {"login.html", "true-copy.html"}
    assert plan["links"][0]["status"] == "ambiguous"


def test_nb_article_headings_and_code_remain_searchable_body(tmp_path):
    from notewrench.relink.archive import nb_document

    data = (
        b"# Bookmark title\n\n<https://old.example/article>\n\n## Content\n\n"
        b"# Article title\n\n## Findings\n\n"
        b"A distinctive sentence after an article subheading.\n\n"
        b"The `specific_function()` call preserves its textual evidence.\n\n"
        b"```python\nprint('A distinctive sentence in archived source code.')\n```\n\n"
        b"## Comment\n\nPrivate user commentary must stay outside archived body evidence.\n"
    )
    doc = nb_document(data, tmp_path / "article.bookmark.md")
    assert doc.title == "Bookmark title"
    assert "a distinctive sentence after an article subheading." in doc.body
    assert "specific_function()" in doc.body
    assert "a distinctive sentence in archived source code." in doc.body
    assert "private user commentary" not in doc.body


def test_actual_buku_markdown_export_links_to_nb_and_warc_copies(tmp_path):
    archive = tmp_path / "archive"
    shutil.copytree(FIXTURES, archive)
    note = tmp_path / "buku-export.md"
    shutil.copyfile(FIXTURES / "buku-export.md", note)
    db = tmp_path / "index.sqlite"
    build_index(archive, db)
    plan = scan_notes(db, [note])
    assert len(plan["links"]) == 2
    assert [len(link["candidates"]) for link in plan["links"]] == [1, 1]
    assert [link["candidates"][0]["kind"] for link in plan["links"]] == [
        "nb_bookmark",
        "warc_response",
    ]


def test_nb_extracted_body_obeys_shared_text_budget(tmp_path, monkeypatch):
    from notewrench.relink.archive import nb_document

    monkeypatch.setattr("notewrench.relink.archive.MAX_TEXT_CHARS", 32)
    data = b"# Saved page\n\n<https://old.example/article>\n\n## Content\n\n" + b"body words " * 20
    doc = nb_document(data, tmp_path / "bounded.bookmark.md")
    assert len(doc.body) == 32
