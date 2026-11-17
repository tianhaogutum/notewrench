import json
import shutil
import subprocess
from io import BytesIO
from pathlib import Path

import pytest
from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter

from notewrench.relink.workflow import build_index, make_patch, scan_notes

# Repository root (examples/relink/) and this suite's upstream fixtures.
ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures/upstream"


def html(path, url=None, body="A saved article body with enough specific words.", title="Title"):
    comment = (
        f"<!--\n Page saved with SingleFile\n url: {url}\n saved date: today\n-->" if url else ""
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"<!doctype html>{comment}<title>{title}</title><h1>{title}</h1><p>{body}</p>")
    return path


def setup_index(tmp_path, documents=None, note=None):
    archive = tmp_path / "archive"
    archive.mkdir()
    for filename, url, body in documents or [
        ("saved.html", "https://old.example/story", "Article body.")
    ]:
        html(archive / filename, url, body)
    note_path = tmp_path / "notes" / "note.md"
    note_path.parent.mkdir()
    note_path.write_text(note or "[source](https://old.example/story)\n")
    db = tmp_path / "index.sqlite"
    result = build_index(archive, db)
    assert result["documents"]
    return archive, note_path, db


def selection(plan):
    link = plan["links"][0]
    return {link["id"]: link["candidates"][0]["id"]}


def apply_patch(tmp_path, patch):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, timeout=5)
    patch_path = tmp_path / "repair.patch"
    patch_path.write_bytes(patch.encode())
    subprocess.run(
        ["git", "apply", "--check", str(patch_path)], cwd=tmp_path, check=True, timeout=5
    )
    subprocess.run(["git", "apply", str(patch_path)], cwd=tmp_path, check=True, timeout=5)


def test_selected_patch_is_small_and_real_git_applies(tmp_path):
    _, note, db = setup_index(tmp_path)
    plan = scan_notes(db, [note])
    assert plan["links"][0]["candidates"][0]["evidence"][0]["kind"] == "source_url"
    patch = make_patch(plan, selection(plan), tmp_path)
    assert "../archive/saved.html" in patch
    assert "https://old.example/story" in note.read_text()
    apply_patch(tmp_path, patch)
    assert note.read_text() == "[source](../archive/saved.html)\n"


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r", ""])
def test_line_endings_and_missing_final_newline(tmp_path, newline):
    _, note, db = setup_index(tmp_path)
    raw = f'[source](https://old.example/story "Keep this title"){newline}'.encode()
    note.write_bytes(raw)
    plan = scan_notes(db, [note])
    patch = make_patch(plan, selection(plan), tmp_path)
    apply_patch(tmp_path, patch)
    assert (
        note.read_bytes() == f'[source](../archive/saved.html "Keep this title"){newline}'.encode()
    )


def test_title_only_decoy_does_not_match(tmp_path):
    archive = tmp_path / "archive"
    shutil.copytree(ROOT / "examples/relink/archive", archive)
    note = tmp_path / "note.md"
    shutil.copyfile(ROOT / "examples/relink/note.md", note)
    db = tmp_path / "index.sqlite"
    build_index(archive, db)
    plan = scan_notes(db, [note])
    assert [len(link["candidates"]) for link in plan["links"]] == [1, 1, 0]
    candidate = plan["links"][1]["candidates"][0]
    assert Path(candidate["target"]).name == "unknown-origin.html"
    assert candidate["evidence"][0]["identity_verified"] is False


def test_same_url_multiple_captures_stays_ambiguous(tmp_path):
    _, note, db = setup_index(
        tmp_path,
        [
            ("one.html", "https://old.example/story", "First version."),
            ("two.html", "https://old.example/story", "Second version."),
        ],
    )
    plan = scan_notes(db, [note])
    assert plan["links"][0]["status"] == "ambiguous"
    assert len(plan["links"][0]["candidates"]) == 2
    with pytest.raises(ValueError, match="select at least"):
        make_patch(plan, {}, tmp_path)


@pytest.mark.parametrize("change", ["note", "target"])
def test_stale_inputs_refused(tmp_path, change):
    archive, note, db = setup_index(tmp_path)
    plan = scan_notes(db, [note])
    path = note if change == "note" else archive / "saved.html"
    path.write_text(path.read_text() + "changed")
    with pytest.raises(ValueError, match="changed"):
        make_patch(plan, selection(plan), tmp_path)


def test_upstream_warc_nb_and_archivebox_resource_manifest(tmp_path):
    archive = tmp_path / "archive"
    shutil.copytree(FIXTURES, archive)
    db = tmp_path / "index.sqlite"
    result = build_index(archive, db)
    assert result["warnings"] == []
    note = tmp_path / "note.md"
    note.write_text(
        "[warc](http://example.com/)\n\n"
        "[nb](https://old.example.org/story)\n\n"
        "[manifest](https://crontab.guru/#@daily)\n"
    )
    plan = scan_notes(db, [note])
    assert [len(link["candidates"]) for link in plan["links"]] == [1, 1, 1]
    assert [link["candidates"][0]["kind"] for link in plan["links"]] == [
        "warc_response",
        "nb_bookmark",
        "html",
    ]
    assert Path(plan["links"][0]["candidates"][0]["target"]).is_file()


def test_legacy_archivebox_metadata_and_missing_output(tmp_path):
    archive = tmp_path / "archive"
    folder = archive / "archive/1234567890"
    html(folder / "singlefile.html", body="This capture contains the article body.")
    (folder / "index.json").write_text(
        json.dumps(
            {
                "url": "https://old.example/story",
                "timestamp": "1234567890",
                "title": "Article",
                "history": {"wget": [{"status": "succeeded", "output": "missing.html"}]},
            }
        )
    )
    db = tmp_path / "index.sqlite"
    build_index(archive, db)
    note = tmp_path / "note.md"
    note.write_text("[source](https://old.example/story)")
    plan = scan_notes(db, [note])
    assert len(plan["links"][0]["candidates"]) == 1
    (folder / "index.json").write_text("{}")
    with pytest.raises(ValueError, match="evidence changed"):
        make_patch(plan, selection(plan), tmp_path)


def write_warc(path, responses):
    with path.open("wb") as stream:
        writer = WARCWriter(stream, gzip=True)
        for url, status, headers, body in responses:
            http = StatusAndHeaders(status, headers, protocol="HTTP/1.1")
            record = writer.create_warc_record(
                url, "response", payload=BytesIO(body), http_headers=http
            )
            writer.write_record(record)


def test_recorded_redirect_chain(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    write_warc(
        archive / "capture.warc.gz",
        [
            ("http://old.example/story", "301 Moved Permanently", [("Location", "/new")], b""),
            (
                "http://old.example/new",
                "302 Found",
                [("Location", "https://new.example/story")],
                b"",
            ),
            (
                "https://new.example/story",
                "200 OK",
                [("Content-Type", "text/html")],
                b"<p>The saved body.</p>",
            ),
        ],
    )
    db = tmp_path / "index.sqlite"
    result = build_index(archive, db)
    assert result["redirects"] == 2
    note = tmp_path / "note.md"
    note.write_text("[source](http://old.example/story)")
    plan = scan_notes(db, [note])
    item = plan["links"][0]["candidates"][0]["evidence"][0]
    assert item["kind"] == "recorded_redirect"
    assert len(item["chain"]) == 2
    assert "http://old.example/story" in item["chain"][0]["from"]


def test_redirect_cycles_are_bounded_and_useful_branch_is_found(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    write_warc(
        archive / "cycle.warc.gz",
        [
            ("https://old.example/a", "301 Moved", [("Location", "/b")], b""),
            ("https://old.example/b", "302 Found", [("Location", "/a")], b""),
            ("https://old.example/b", "301 Moved", [("Location", "/final")], b""),
            (
                "https://old.example/final",
                "200 OK",
                [("Content-Type", "text/html")],
                b"<p>Final content.</p>",
            ),
        ],
    )
    db = tmp_path / "index.sqlite"
    build_index(archive, db)
    note = tmp_path / "note.md"
    note.write_text("[source](https://old.example/a)")
    plan = scan_notes(db, [note])
    assert len(plan["links"][0]["candidates"]) == 1
    assert len(plan["links"][0]["candidates"][0]["evidence"][0]["chain"]) == 2


def test_redirect_hop_limit_is_reported_as_truncated_search(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    records = [
        (f"https://old.example/{i}", "301 Moved", [("Location", f"/{i + 1}")], b"")
        for i in range(9)
    ]
    records.append(
        ("https://old.example/9", "200 OK", [("Content-Type", "text/html")], b"<p>Final.</p>")
    )
    write_warc(archive / "long.warc.gz", records)
    db = tmp_path / "index.sqlite"
    build_index(archive, db)
    note = tmp_path / "note.md"
    note.write_text("[source](https://old.example/0)")
    plan = scan_notes(db, [note])
    assert plan["links"][0]["candidates"] == []
    assert plan["links"][0]["candidates_truncated"] is True


@pytest.mark.parametrize(
    ("location", "original_fragment", "expected"),
    [
        ("/new#section", "old", "section"),
        ("/new", "old", "old"),
        ("/new#", "old", ""),
        ("/new#section", "", "section"),
    ],
)
def test_recorded_redirect_fragment_semantics(tmp_path, location, original_fragment, expected):
    from urllib.parse import urlsplit

    from markdown_it import MarkdownIt

    archive = tmp_path / "archive"
    archive.mkdir()
    write_warc(
        archive / "fragments.warc.gz",
        [
            ("https://old.example/story", "301 Moved", [("Location", location)], b""),
            (
                "https://old.example/new",
                "200 OK",
                [("Content-Type", "text/html")],
                b'<p id="section">Saved article content.</p>',
            ),
        ],
    )
    db = tmp_path / "index.sqlite"
    build_index(archive, db)
    note = tmp_path / "note.md"
    suffix = "#" + original_fragment if original_fragment else ""
    note.write_text(f"[source](https://old.example/story{suffix})\n")
    plan = scan_notes(db, [note])
    apply_patch(tmp_path, make_patch(plan, selection(plan), tmp_path))
    links = [
        token
        for block in MarkdownIt("commonmark").parse(note.read_text())
        for token in block.children or []
        if token.type == "link_open"
    ]
    assert urlsplit(links[0].attrGet("href")).fragment == expected


def test_invalid_inputs_and_symlinks_are_isolated(tmp_path):
    archive, note, db = setup_index(tmp_path)
    (archive / "broken/index.json").parent.mkdir()
    (archive / "broken/index.json").write_text("{not json}")
    (archive / "link.html").symlink_to(archive / "saved.html")
    result = build_index(archive, db)
    assert result["documents"] == 1
    assert len(result["warnings"]) == 1
    assert len(scan_notes(db, [note])["links"][0]["candidates"]) == 1


def test_url_filter_explicit_quote_and_limit(tmp_path):
    _, note, db = setup_index(
        tmp_path,
        [
            (
                f"{i}.html",
                None,
                "A very distinctive passage that appears in multiple private captures.",
            )
            for i in range(3)
        ],
    )
    quotes = {
        "https://old.example/story": (
            "A very distinctive passage that appears in multiple private captures."
        )
    }
    assert scan_notes(db, [note], quotes, set())["links"] == []
    plan = scan_notes(db, [note], quotes, limit=1)
    assert plan["links"][0]["candidates_truncated"] is True
    assert plan["links"][0]["status"] == "ambiguous"


def test_autolinks_and_percent_encoded_local_path(tmp_path):
    _, note, db = setup_index(
        tmp_path,
        [
            ("中文 (saved) #copy.html", "https://old.example/story", "Body."),
        ],
        "<https://old.example/story>",
    )
    plan = scan_notes(db, [note])
    patch = make_patch(plan, selection(plan), tmp_path)
    apply_patch(tmp_path, patch)
    assert note.read_text().startswith("[https://old.example/story](../archive/%E4%B8%AD")
    assert "%28saved%29%20%23copy.html" in note.read_text()


def test_unicode_line_separators_do_not_corrupt_unified_patch(tmp_path):
    _, note, db = setup_index(
        tmp_path,
        note="Prose with a Unicode line separator\u2028and a citation "
        "[source](https://old.example/story)\u2029more prose.\n",
    )
    plan = scan_notes(db, [note])
    patch = make_patch(plan, selection(plan), tmp_path)
    apply_patch(tmp_path, patch)
    assert "\u2028" in note.read_text() and "\u2029" in note.read_text()
    assert "../archive/saved.html" in note.read_text()


def test_utf8_bom_and_crlf_survive_real_patch(tmp_path):
    _, note, db = setup_index(tmp_path)
    note.write_bytes(b"\xef\xbb\xbf[source](https://old.example/story)\r\n")
    plan = scan_notes(db, [note])
    apply_patch(tmp_path, make_patch(plan, selection(plan), tmp_path))
    assert note.read_bytes() == b"\xef\xbb\xbf[source](../archive/saved.html)\r\n"
