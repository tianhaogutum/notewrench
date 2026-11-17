# SPDX-License-Identifier: MIT
# Copyright (c) 2026 bookmark-receipts contributors (see licenses/browser-bookmarks-MIT.txt)
import subprocess
import sys

from notewrench.browserbookmarks.cli import interactive_review, main
from notewrench.browserbookmarks.review import create_review, load_review, status


def test_review_directories_work_for_status_decide_apply_and_verify(tmp_path, capsys):
    source = tmp_path / "input.html"
    source.write_text('<DL><DT><A HREF="https://example.org/">A</A><DT><A HREF="https://example.org/">B</A></DL>')
    directory = tmp_path / "review"
    create_review([(source, "html")], directory)
    assert main(["status", str(directory)]) == 0
    assert main(["decide", str(directory), "--all", "--action", "merge"]) == 0
    assert main(["apply", str(directory), "--out", str(tmp_path / "result")]) == 0
    assert main(["verify", str(directory)]) == 0
    assert main(["verify", str(tmp_path / "result")]) == 0


def test_show_labels_sources_and_explains_empty_duplicate_set(tmp_path, capsys):
    source = tmp_path / "same-name.html"
    source.write_text('<DL><DT><A HREF="https://example.org/">Only one</A></DL>')
    directory = tmp_path / "review"
    create_review([(source, "html")], directory)
    assert main(["show", str(directory)]) == 0
    output = capsys.readouterr().out
    assert "s1 (html)" in output and "same-name.html" in output
    assert "No duplicate groups" in output


def test_piped_output_can_be_stopped_without_traceback_or_failure(tmp_path):
    source = tmp_path / "input.html"
    source.write_text('<DL>' + ''.join(f'<DT><A HREF="https://example.org/{index}">A</A><DT><A HREF="https://example.org/{index}">B</A><DD>' + 'context ' * 30 for index in range(200)) + '</DL>')
    directory = tmp_path / "review"
    create_review([(source, "html")], directory)
    process = subprocess.Popen([sys.executable, "-m", "notewrench", "browser-bookmarks", "show", str(directory)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdout.readline()
    process.stdout.close()
    error = process.stderr.read()
    assert process.wait(timeout=5) == 0
    assert b"Traceback" not in error and b"Broken pipe" not in error


def test_malformed_custom_choices_fail_cleanly_and_leave_decisions_unchanged(tmp_path, capsys):
    source = tmp_path / "input.html"
    source.write_text('<DL><DT><A HREF="https://example.org/">A</A><DT><A HREF="https://example.org/">B</A></DL>')
    directory = tmp_path / "review"
    create_review([(source, "html")], directory)
    path = directory / "review.json"
    before = path.read_bytes()
    choices = ['{"value":' + '9' * 5000 + '}', '{"value":"first","value":"second"}']
    for choice in choices:
        assert main(["decide", str(directory), "--all", "--action", "merge", "--field", "notes=" + choice]) == 2
        assert path.read_bytes() == before
    assert "Traceback" not in capsys.readouterr().err


def test_quoted_source_choice_is_accepted(tmp_path):
    source = tmp_path / "input.html"
    source.write_text('<DL><DT><A HREF="https://example.org/">A</A><DT><A HREF="https://example.org/">B</A></DL>')
    directory = tmp_path / "review"
    create_review([(source, "html")], directory)
    assert main(["decide", str(directory), "--all", "--action", "merge", "--field", 'title="@s1:2"']) == 0


def test_reopened_singleton_can_be_resolved_in_terminal_reviewer(tmp_path):
    source = tmp_path / "input.html"
    source.write_text('<DL><DT><A HREF="https://example.org/">Only bookmark</A></DL>')
    directory = tmp_path / "review"
    plan = create_review([(source, "html")], directory)
    assert main(["decide", str(directory), "--group", plan["groups"][0]["id"], "--action", "pending"]) == 0
    interactive_review(directory / "review.json", ask=lambda _: "k", emit=lambda _: None)
    assert status(load_review(directory / "review.json"))["pending"] == 0
