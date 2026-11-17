import shutil
from email import policy
from email.message import EmailMessage
from pathlib import Path

import pytest
from .test_workflow import FIXTURES

from notewrench.relink.archive import mhtml_document
from notewrench.relink.workflow import build_index, scan_notes


def related_capture(charset="utf-8"):
    main = EmailMessage()
    main.set_content(
        "<title>Article title</title><p>A distinctive quotation in the main saved article.</p>",
        subtype="html",
        charset=charset,
        cte="quoted-printable",
    )
    main["Content-ID"] = "<root@archive>"
    main["Content-Location"] = "https://original.example/article"
    child = EmailMessage()
    child.set_content("<p>An unrelated iframe contains different quoted words.</p>", subtype="html")
    child["Content-ID"] = "<frame@archive>"
    child["Content-Location"] = "https://iframe.example/frame"
    message = EmailMessage()
    message["Snapshot-Content-Location"] = "https://old.example/article"
    message.make_related()
    message.set_param("start", "<root@archive>")
    message.attach(child)  # Root is deliberately not the first HTML part.
    message.attach(main)
    return message.as_bytes(policy=policy.SMTP)


def test_rfc_related_start_selects_main_document(tmp_path):
    path = tmp_path / "capture.mhtml"
    doc = mhtml_document(related_capture(), path)
    assert doc.kind == "mhtml"
    assert "distinctive quotation" in doc.body
    assert "unrelated iframe" not in doc.body
    assert {e["url"] for e in doc.urls} == {
        "https://original.example/article",
        "https://old.example/article",
    }
    assert doc.title == "Article title"


def test_upstream_chromium_mhtml_fixtures_have_identity_and_direct_target(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    for source in FIXTURES.glob("chromium-*.mh*"):
        shutil.copyfile(source, archive / source.name)
    db = tmp_path / "index.sqlite"
    result = build_index(archive, db)
    assert result["documents"] == 2
    assert result["warnings"] == []
    note = tmp_path / "note.md"
    note.write_text("[source](http://www.example.com/)")
    plan = scan_notes(db, [note])
    assert plan["links"][0]["status"] == "ambiguous"
    assert all(c["kind"] == "mhtml" for c in plan["links"][0]["candidates"])
    assert all(Path(c["target"]).parent == archive for c in plan["links"][0]["candidates"])


def test_mhtml_quote_search_does_not_search_embedded_iframe(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "capture.mhtml").write_bytes(related_capture())
    db = tmp_path / "index.sqlite"
    build_index(archive, db)
    note = tmp_path / "note.md"
    note.write_text("[source](https://lost.example/unknown)")
    for quote, expected in [
        ("A distinctive quotation in the main saved article.", 1),
        ("An unrelated iframe contains different quoted words.", 0),
    ]:
        plan = scan_notes(db, [note], {"https://lost.example/unknown": quote})
        assert len(plan["links"][0]["candidates"]) == expected


def test_invalid_mhtml_warns_and_retains_other_files(tmp_path):
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "broken.mht").write_bytes(b"Content-Type: multipart/related\n\nno MIME body")
    (archive / "good.html").write_text("<p>A usable archived document.</p>")
    result = build_index(archive, tmp_path / "index.sqlite")
    assert result["documents"] == 1
    assert result["warnings"]


def test_mime_charset_is_used_when_html_has_no_meta(tmp_path):
    message = EmailMessage()
    message.set_content(
        "<p>Un passage français contient des caractères accentués.</p>",
        subtype="html",
        charset="iso-8859-1",
        cte="base64",
    )
    message["Content-Location"] = "https://example.org/fr"
    doc = mhtml_document(message.as_bytes(), tmp_path / "fr.mhtml")
    assert "français" in doc.body and "caractères" in doc.body


def test_missing_declared_root_cannot_claim_an_iframe_as_the_article(tmp_path):
    data = related_capture().replace(b'start="<root@archive>"', b'start="<missing@archive>"')
    with pytest.raises(ValueError, match="declared main part is missing"):
        mhtml_document(data, tmp_path / "missing-root.mhtml")
