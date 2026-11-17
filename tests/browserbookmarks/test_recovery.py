# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (see licenses/browser-bookmarks-MIT.txt)
import errno
import json
import os
import re
from pathlib import Path

import pytest
from filelock import FileLock

from notewrench.browserbookmarks import merging, review
from notewrench.browserbookmarks.cli import main
from notewrench.browserbookmarks.merging import apply_review
from notewrench.browserbookmarks.parsing import ReviewError, read_limited, sha256
from notewrench.browserbookmarks.review import create_review, load_review, refresh_view, save_review
from notewrench.browserbookmarks.verification import verify_export

DOC = '<DL><DT><A HREF="https://x.example/">X1</A><DT><A HREF="https://x.example/">X2</A><DT><A HREF="https://y.example/">Y1</A><DT><A HREF="https://y.example/">Y2</A></DL>'


def setup_review(tmp_path):
    source = tmp_path / "input.html"
    source.write_text(DOC)
    path = tmp_path / "review/review.json"
    plan = create_review([(source, "html")], path.parent)
    return path, plan


def approve(plan):
    for group in plan["groups"]:
        group["decision"]["action"] = "merge"


def test_stale_save_cannot_erase_another_reviewers_decision(tmp_path):
    path, _ = setup_review(tmp_path)
    first, second = load_review(path), load_review(path)
    first["groups"][0]["decision"]["action"] = "merge"
    second["groups"][1]["decision"]["action"] = "keep"
    save_review(path, first)
    with pytest.raises(ReviewError, match="changed since it was loaded"):
        save_review(path, second)
    assert [group["decision"]["action"] for group in load_review(path)["groups"]] == ["merge", "pending"]


def test_another_process_lock_is_reported_and_changes_nothing(tmp_path, capsys):
    path, _ = setup_review(tmp_path)
    before = path.read_bytes()
    with FileLock(str(path) + ".lock"):
        assert main(["decide", str(path), "--all", "--action", "merge"]) == 2
    assert "another process" in capsys.readouterr().err
    assert path.read_bytes() == before


def test_view_refresh_obeys_the_same_lock_as_decision_edits(tmp_path):
    path, _ = setup_review(tmp_path)
    before = (path.parent / "review.html").read_bytes()
    with FileLock(str(path) + ".lock"):
        with pytest.raises(ReviewError, match="another process"):
            refresh_view(path)
    assert (path.parent / "review.html").read_bytes() == before


@pytest.mark.parametrize("failure", ["prepare", "replace"])
def test_failed_decision_write_leaves_old_json_and_cleans_temps(tmp_path, monkeypatch, failure):
    path, plan = setup_review(tmp_path)
    before_json = path.read_bytes()
    before_html = (path.parent / "review.html").read_bytes()
    approve(plan)

    def fail(*_, **__):
        raise OSError(errno.ENOSPC, "simulated full disk")

    if failure == "prepare":
        monkeypatch.setattr(review.tempfile, "mkstemp", fail)
    else:
        monkeypatch.setattr(review.os, "replace", fail)
    with pytest.raises(OSError, match="simulated full disk"):
        save_review(path, plan)
    assert path.read_bytes() == before_json
    assert (path.parent / "review.html").read_bytes() == before_html
    assert not list(path.parent.glob(".review-*.json"))


def test_html_refresh_failure_reports_saved_json_and_is_repairable(tmp_path, monkeypatch):
    path, plan = setup_review(tmp_path)
    approve(plan)
    real_replace = os.replace

    def replace(source, target):
        if Path(target).name == "review.html":
            raise OSError(errno.ENOSPC, "simulated full disk")
        return real_replace(source, target)

    with monkeypatch.context() as context:
        context.setattr(review.os, "replace", replace)
        with pytest.raises(ReviewError, match="Decision JSON saved"):
            save_review(path, plan)
    assert all(group["decision"]["action"] == "merge" for group in load_review(path)["groups"])
    refresh_view(path)
    assert "pending ·" not in (path.parent / "review.html").read_text()


def test_full_disk_during_export_never_publishes_partial_result(tmp_path, monkeypatch):
    path, plan = setup_review(tmp_path)
    approve(plan)

    def fail(*_, **__):
        raise OSError(errno.ENOSPC, "simulated full disk")

    monkeypatch.setattr(merging, "export_csv", fail)
    with pytest.raises(OSError, match="simulated full disk"):
        apply_review(plan, path, tmp_path / "result")
    assert not (tmp_path / "result").exists()
    assert not list(tmp_path.glob(".result-*"))
    assert (tmp_path / "input.html").read_text() == DOC


def test_pipe_input_is_rejected_without_waiting(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("named pipes unavailable")
    fifo = tmp_path / "input.html"
    os.mkfifo(fifo)
    with pytest.raises(ReviewError, match="regular file"):
        read_limited(fifo)


def test_empty_manifest_cannot_verify_as_a_complete_export(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"schema": "bookmark-receipts/manifest-v1", "files": {}}))
    with pytest.raises(ReviewError, match="required files"):
        verify_export(tmp_path)


def rehash(directory, name):
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["files"][name] = sha256((directory / name).read_bytes())
    path.write_text(json.dumps(manifest))


@pytest.mark.parametrize("damage", ["mapping", "html_id", "extra_file"])
def test_semantic_verification_checks_mappings_even_if_file_hashes_were_updated(tmp_path, damage):
    path, plan = setup_review(tmp_path)
    approve(plan)
    result = tmp_path / "result"
    apply_review(plan, path, result)
    if damage == "mapping":
        csv_path = result / "sources.csv"
        csv_path.write_text(csv_path.read_text().replace("s1:1", "s1:4"))
        rehash(result, "sources.csv")
    elif damage == "html_id":
        html_path = result / "bookmarks.html"
        html_path.write_text(html_path.read_text().replace('DATA-RECEIPT="b000001"', 'DATA-RECEIPT="wrong"'))
        rehash(result, "bookmarks.html")
    else:
        (result / "extra.txt").write_text("untracked")
    with pytest.raises(ReviewError):
        verify_export(result)


@pytest.mark.parametrize("damage", ["notes", "tags", "private", "added"])
def test_exported_content_is_checked_even_when_manifest_hash_was_recomputed(tmp_path, damage):
    source = tmp_path / "input.html"
    source.write_text('<DL><DT><A HREF="https://example.org/" TAGS="Research,Read" ADD_DATE="1704067200" PRIVATE="1">Title</A><DD>Summary[linkding-notes]Keep this note[/linkding-notes]</DL>')
    path = tmp_path / "review/review.json"
    plan = create_review([(source, "linkding")], path.parent)
    result = tmp_path / "result"
    apply_review(plan, path, result)
    html_path = result / "bookmarks.html"
    text = html_path.read_text()
    if damage == "notes":
        text = re.sub(r"\[linkding-notes\].*?\[/linkding-notes\]", "", text, flags=re.S)
    elif damage == "tags":
        text = text.replace('TAGS="Research,Read"', 'TAGS="Lost"')
    elif damage == "private":
        text = text.replace('PRIVATE="1"', 'PRIVATE="0"')
    else:
        text = text.replace('ADD_DATE="1704067200"', 'ADD_DATE="0"')
    html_path.write_text(text)
    rehash(result, "bookmarks.html")
    with pytest.raises(ReviewError, match="differs? from the receipt"):
        verify_export(result)


def test_custom_folder_line_endings_follow_html_normalization(tmp_path):
    path, plan = setup_review(tmp_path)
    approve(plan)
    for group in plan["groups"]:
        group["decision"]["fields"]["folder"] = {"value": ["Research\r\nNotes"]}
    apply_review(plan, path, tmp_path / "result")
    assert verify_export(tmp_path / "result")["verified_outputs"] == 2
