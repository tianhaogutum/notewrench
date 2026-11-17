# SPDX-License-Identifier: MIT
# Copyright (c) 2026 bookmark-receipts contributors (see licenses/browser-bookmarks-MIT.txt)
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from notewrench.browserbookmarks.cli import interactive_review, main
from notewrench.browserbookmarks.verification import verify_export
from notewrench.browserbookmarks.merging import apply_review, make_outputs
from notewrench.browserbookmarks.parsing import ReviewError, parse_html
from notewrench.browserbookmarks.review import create_review, load_review, save_review, status

FIXTURES = Path(__file__).parent / "fixtures"


def real_review(tmp_path):
    directory = tmp_path / "review"
    plan = create_review([
        (FIXTURES / "firefox.html", "html"),
        (FIXTURES / "chrome.html", "html"),
        (FIXTURES / "linkding-conflicts.html", "linkding"),
    ], directory)
    return directory / "review.json", plan


def approve(plan, action="merge"):
    for group in plan["groups"]:
        if len(group["members"]) > 1:
            group["decision"]["action"] = action


def test_real_two_browsers_and_linkding_end_to_end(tmp_path):
    path, plan = real_review(tmp_path)
    assert status(plan)["records"] == 19
    assert status(plan)["duplicate_groups"] >= 4
    with pytest.raises(ReviewError, match="still pending"):
        apply_review(plan, path, tmp_path / "pending")
    assert not (tmp_path / "pending").exists()
    approve(plan)
    save_review(path, plan)
    result = apply_review(load_review(path), path, tmp_path / "result")
    assert result["source_records"] == 19
    assert result["outputs"] < 19
    receipt = json.loads((tmp_path / "result/receipts.json").read_text())
    contributed = [member for output in receipt["outputs"] for member in output["source_records"]]
    assert sorted(contributed) == sorted(record["id"] for record in plan["snapshot"]["records"])
    notes_output = next(output for output in receipt["outputs"] if "s3:1" in output["source_records"])
    assert notes_output["fields"]["tags"] == ["Research", "Read", "research", "Later"]
    assert "Keep this first note" in notes_output["fields"]["notes"]
    assert "Keep this second note" in notes_output["fields"]["notes"]
    assert "s3:1" in notes_output["fields"]["notes"] and "s3:2" in notes_output["fields"]["notes"]
    generated = (tmp_path / "result/bookmarks.html").read_bytes()
    parsed, _ = parse_html(generated, "output", "linkding")
    note_record = next(record for record in parsed if record["url"] == "https://example.com/story")
    assert note_record["notes"] == notes_output["fields"]["notes"]
    assert note_record["description"] == notes_output["fields"]["description"]
    assert note_record["attributes"]["data-receipt"] == notes_output["id"]
    for index, fixture in enumerate(("firefox.html", "chrome.html", "linkding-conflicts.html"), 1):
        assert (tmp_path / f"result/originals/s{index}.html").read_bytes() == (FIXTURES / fixture).read_bytes()
    assert verify_export(tmp_path / "result")["verified_files"] == 6
    with (tmp_path / "result/sources.csv").open(newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 19


def test_keep_all_then_choose_one_note_preserves_alternative_in_receipt(tmp_path):
    source = FIXTURES / "linkding-conflicts.html"
    path = tmp_path / "review/review.json"
    plan = create_review([(source, "linkding")], path.parent)
    approve(plan, "keep")
    assert len(make_outputs(plan)) == 2
    group = plan["groups"][0]
    group["decision"]["action"] = "merge"
    group["decision"]["fields"]["notes"] = "@s1:2"
    group["decision"]["fields"]["title"] = {"value": "My merged title"}
    save_review(path, plan)
    apply_review(load_review(path), path, tmp_path / "result")
    receipt = json.loads((tmp_path / "result/receipts.json").read_text())
    assert receipt["outputs"][0]["fields"]["notes"] == "Keep this second note"
    assert receipt["outputs"][0]["fields"]["title"] == "My merged title"
    assert receipt["original_records"][0]["notes"] == "Keep this first note"


def test_cli_noninteractive_workflow_and_no_overwrite(tmp_path, capsys):
    review = tmp_path / "review"
    assert main(["scan", "--linkding", str(FIXTURES / "linkding-conflicts.html"), "--out", str(review)]) == 0
    assert main(["apply", str(review / "review.json"), "--out", str(tmp_path / "out")]) == 2
    assert main(["decide", str(review / "review.json"), "--all", "--action", "merge", "--field", "title=@s1:2"]) == 0
    assert main(["apply", str(review / "review.json"), "--out", str(tmp_path / "out")]) == 0
    original = (tmp_path / "out/bookmarks.html").read_bytes()
    assert main(["apply", str(review / "review.json"), "--out", str(tmp_path / "out")]) == 2
    assert (tmp_path / "out/bookmarks.html").read_bytes() == original
    assert main(["verify", str(tmp_path / "out")]) == 0
    assert main(["verify", str(review / "review.json")]) == 0


def test_interactive_review_is_resumable_and_preserves_notes(tmp_path):
    path = tmp_path / "review/review.json"
    create_review([(FIXTURES / "linkding-conflicts.html", "linkding")], path.parent)
    answers = iter(["m"] + [""] * 10)
    assert interactive_review(path, ask=lambda _: next(answers), emit=lambda _: None) == 0
    outputs = make_outputs(load_review(path))
    assert "Keep this first note" in outputs[0]["fields"]["notes"]
    assert "Keep this second note" in outputs[0]["fields"]["notes"]


def test_interrupted_group_stays_pending(tmp_path):
    path = tmp_path / "review/review.json"
    create_review([(FIXTURES / "linkding-conflicts.html", "linkding")], path.parent)
    calls = 0

    def answer(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            return "m"
        raise EOFError

    interactive_review(path, ask=answer, emit=lambda _: None)
    assert status(load_review(path))["pending"] == 1


def test_integrity_changes_rejected(tmp_path):
    path, plan = real_review(tmp_path)
    original = path.read_bytes()
    plan["snapshot"]["records"][0]["notes"] = "edited by mistake"
    path.write_text(json.dumps(plan))
    with pytest.raises(ReviewError, match="checksum"):
        load_review(path)
    path.write_bytes(original)
    loaded = load_review(path)
    loaded["groups"][0]["members"] = []
    path.write_text(json.dumps(loaded))
    with pytest.raises(ReviewError, match="Group IDs"):
        load_review(path)
    path.write_bytes(original)
    (path.parent / "sources/s1.html").write_text("changed")
    with pytest.raises(ReviewError, match="bundled source changed"):
        load_review(path)


def test_html_and_csv_escaping_with_exact_receipt(tmp_path):
    source = tmp_path / "input.html"
    source.write_text('''<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><p>
<DT><A HREF="https://example.org/?q=&quot;quoted&quot;&amp;x=1" TAGS="&quot;,&amp;">=HYPERLINK("evil")</A>
<DD>&lt;script&gt;alert(1)&lt;/script&gt;[linkding-notes]@SUM(1,1)\nsecond line[/linkding-notes]
</DL>''')
    path = tmp_path / "review/review.json"
    plan = create_review([(source, "linkding")], path.parent)
    apply_review(plan, path, tmp_path / "result")
    exported = (tmp_path / "result/bookmarks.html").read_text()
    soup = BeautifulSoup(exported, "html5lib")
    assert soup.find("script") is None
    assert soup.find("a")["href"] == 'https://example.org/?q="quoted"&x=1'
    review_soup = BeautifulSoup((path.parent / "review.html").read_text(), "html5lib")
    assert review_soup.find("script") is None
    assert review_soup.find("a") is None
    with (tmp_path / "result/sources.csv").open(newline="") as handle:
        row = next(csv.DictReader(handle))
    assert row["original_title"].startswith("'=")
    assert row["original_notes"].startswith("'@")
    receipt = json.loads((tmp_path / "result/receipts.json").read_text())
    assert receipt["original_records"][0]["title"].startswith("=")
    assert receipt["original_records"][0]["notes"].startswith("@")


def test_export_verification_detects_modified_html(tmp_path):
    path, plan = real_review(tmp_path)
    approve(plan)
    apply_review(plan, path, tmp_path / "result")
    (tmp_path / "result/bookmarks.html").write_text("corrupted")
    with pytest.raises(ReviewError, match="checksum mismatch"):
        verify_export(tmp_path / "result")
