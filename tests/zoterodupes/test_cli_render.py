import copy
import hashlib
import json
import re
from pathlib import Path

from notewrench.zoterodupes.cli import main
from notewrench.zoterodupes.decisions import decision_template
from notewrench.zoterodupes.render import render_review
from .paths import EXAMPLES


def test_offline_html_escapes_untrusted_export_content(realistic_review):
    review = copy.deepcopy(realistic_review)
    first = next(iter(review["records"].values()))
    payload = '</script><script>globalThis.PWNED=true</script> /*BRIDGE_JS*/ & <img src=x onerror=alert(1)>'
    first["notes"].append({"note": payload})
    first["metadata"]["title"] = payload
    rendered = render_review(review, decision_template(review))
    assert payload not in rendered
    embedded = re.search(r'<script type="application/json" id="bridge-data">(.*?)</script>', rendered, re.S).group(1)
    restored = json.loads(embedded)
    assert next(iter(restored["review"]["records"].values()))["notes"][-1]["note"] == payload
    assert "connect-src 'none'" in rendered
    assert "<script src=" not in rendered


def test_cli_compare_finalize_and_input_immutability(tmp_path):
    personal = EXAMPLES / "personal.json"
    group = EXAMPLES / "group.json"
    before = [hashlib.sha256(path.read_bytes()).hexdigest() for path in [personal, group]]
    review_dir = tmp_path / "review"
    assert main(["compare", "--library", f"personal={personal}", "--library", f"group={group}", "--output", str(review_dir)]) == 0
    review = json.loads((review_dir / "review.json").read_text())
    decisions = json.loads((review_dir / "decisions.json").read_text())
    decisions["decisions"][review["pairs"][0]["id"]]["status"] = "same"
    decisions_path = tmp_path / "downloaded.json"
    decisions_path.write_text(json.dumps(decisions))
    out = tmp_path / "confirmed"
    assert main(["finalize", "--review", str(review_dir / "review.json"), "--decisions", str(decisions_path), "--output", str(out)]) == 0
    confirmed = json.loads((out / "correspondences.json").read_text())
    assert len(confirmed["correspondences"]) == 1
    assert (out / "correspondences.csv").is_file()
    assert (out / "suggestions.json").is_file()
    assert [hashlib.sha256(path.read_bytes()).hexdigest() for path in [personal, group]] == before
    assert main(["compare", "--library", f"personal={personal}", "--library", f"group={group}", "--output", str(review_dir)]) == 2
    assert json.loads((review_dir / "review.json").read_text()) == review


def test_cli_failure_does_not_create_partial_review(tmp_path):
    out = tmp_path / "failed"
    bad = tmp_path / "invalid.json"
    bad.write_text('{"items": [')
    assert main(["compare", "--library", f"a={bad}", "--library", f"b={EXAMPLES / 'group.json'}", "--output", str(out)]) == 2
    assert not out.exists()
    assert not list(tmp_path.glob(".library-bridge-*"))


def test_same_file_cannot_masquerade_as_two_libraries(tmp_path):
    path = EXAMPLES / "personal.json"
    assert main(["compare", "--library", f"a={path}", "--library", f"b={path}", "--output", str(tmp_path / "review")]) == 2


def test_output_budget_failure_cleans_staging_without_partial_review(tmp_path, monkeypatch):
    import notewrench.zoterodupes.cli as cli
    monkeypatch.setattr(cli, "MAX_OUTPUT_BYTES", 100)
    out = tmp_path / "review"
    assert main(["compare", "--library", f"a={EXAMPLES / 'personal.json'}", "--library", f"b={EXAMPLES / 'group.json'}", "--output", str(out)]) == 2
    assert not out.exists()
    assert not list(tmp_path.glob(".library-bridge-*"))


def test_html_escaping_budget_is_enforced(realistic_review, monkeypatch):
    import notewrench.zoterodupes.render as renderer
    import pytest
    from notewrench.zoterodupes.model import InputError
    monkeypatch.setattr(renderer, "MAX_HTML_DATA_BYTES", 100)
    with pytest.raises(InputError, match="safe escaping"):
        render_review(realistic_review, decision_template(realistic_review))


def test_inspect_reports_content_without_output_files(capsys):
    assert main(["inspect", str(EXAMPLES / "personal.json"), "--library", "personal"]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["records"] == 2
    assert info["annotations"] == 1
    assert info["notes"] == 1
    assert info["identity_quality"]["zotero-uri"] == 1


def test_existing_output_rejected_before_parsing_inputs(tmp_path, capsys):
    out = tmp_path / "existing"
    out.mkdir()
    assert main(["compare", "--library", "a=missing.json", "--library", "b=missing-too.json", "--output", str(out)]) == 2
    assert "Output already exists" in capsys.readouterr().err


def test_json_budget_includes_trailing_newline(tmp_path, monkeypatch):
    import notewrench.zoterodupes.cli as cli
    import pytest
    from notewrench.zoterodupes.model import InputError
    payload = {"a": "x"}
    exact_payload_size = len(json.dumps(payload, indent=2).encode())
    monkeypatch.setattr(cli, "MAX_OUTPUT_BYTES", exact_payload_size)
    with pytest.raises(InputError, match="exceeds"):
        cli._write_json(tmp_path / "bounded.json", payload)

