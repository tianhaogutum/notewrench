"""`notewrench bookmark-migration`: parsers, URL keys, pairing, attachment manifests, reports and CLI."""
import copy
import hashlib
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from notewrench.bookmarkmigration.attachments import attach_manifest
from notewrench.bookmarkmigration.cli import main
from notewrench.bookmarkmigration.compare import compare_exports
from notewrench.bookmarkmigration.model import AuditError
from notewrench.bookmarkmigration.parsers import load_export, parse_karakeep, timestamp
from notewrench.bookmarkmigration.reports import html_report, markdown_report, write_reports
from notewrench.bookmarkmigration.urls import url_key

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples" / "bookmark-migration"


def bookmark(url="https://example.org/", **kwargs):
    return {"createdAt": 1700000000, "title": "书签", "tags": [], "lists": [], "content": {"type": "link", "url": url}, "note": None, "archived": False, **kwargs}


def exported(items=None, lists=None):
    return parse_karakeep(json.dumps({"bookmarks": items if items is not None else [bookmark()], "lists": lists or []}), Path("virtual.json"))


def list_def(list_id, name, parent=None):
    return {"id": list_id, "name": name, "parentId": parent, "description": None, "icon": "📁", "type": "manual", "query": None}


class UrlTests(unittest.TestCase):
    def test_conservative_rules(self):
        self.assertEqual(url_key("HTTPS://EXAMPLE.org:443?q=中&x=1#段"), "https://example.org/?q=中&x=1#段")
        self.assertEqual(url_key("HTTP://EXAMPLE.org:80"), "http://example.org/")
        self.assertEqual(url_key("https://User:Pass@EXAMPLE.org:8443/a"), "https://User:Pass@example.org:8443/a")
        self.assertEqual(url_key("https://[ABCD::1]:443/#x"), "https://[abcd::1]/#x")

    def test_semantics_are_preserved(self):
        pairs = [("?q=书签", "?q=收藏"), ("?a=1&b=2", "?b=2&a=1"), ("?utm_source=x", ""), ("?", ""), ("#", ""), ("#a", "#b"), ("/A", "/a"), ("/%7E", "/~"), ("/a/../b", "/b"), ("?a=1&a=2", "?a=2&a=1")]
        for left, right in pairs:
            with self.subTest(left=left, right=right):
                self.assertNotEqual(url_key("https://example.org" + left), url_key("https://example.org" + right))
        self.assertNotEqual(url_key("http://example.org"), url_key("https://example.org"))

    def test_raw_and_other_schemes(self):
        url = "HTTPS://EXAMPLE.org:443"
        self.assertEqual(url_key(url, "raw"), url)
        self.assertEqual(url_key("mailto:Foo@EXAMPLE.org"), "mailto:Foo@EXAMPLE.org")
        self.assertEqual(url_key("file:///home/文档.pdf"), "file:///home/文档.pdf")
        self.assertNotEqual(url_key("https://[fe80::1%25ethA]/"), url_key("https://[fe80::1%25etha]/"))
        with self.assertRaises(AuditError):
            url_key("https://example.org:/", "raw")

    def test_invalid_urls(self):
        for value in [None, "", "relative/path", "https://", "https://x:bad/", "https://x:99999/", "https://x:/", " https://x", "https://x/a b", "https://x/\n"]:
            with self.subTest(value=value), self.assertRaises(AuditError):
                url_key(value)


class FormatTests(unittest.TestCase):
    def html(self, content):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bookmarks.html"
            path.write_text(content, encoding="utf-8")
            return load_export(path)

    def test_nested_folders_tags_notes_and_line_numbers(self):
        result = self.html('<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<DL><p>\n<DT><H3>资料</H3><DL><p><DT><H3>研究</H3><DL><p>\n<DT><A HREF="https://example.org/?a=1&amp;b=2" TAGS="中文,开发" ADD_DATE="1700000000">书签 &amp; café</A>\n<DD>保留备注\n<DT><A HREF="https://example.org/2">二</A>\n</DL></DL></DL>')
        entry, second = result.entries
        self.assertEqual(entry.location, "A[1]")
        self.assertEqual(entry.line, 4)
        self.assertEqual(entry.url, "https://example.org/?a=1&b=2")
        self.assertIn("&amp;b=2", entry.raw["start_tag"])
        self.assertEqual(entry.fields["folders"].value, [("资料", "研究")])
        self.assertEqual(entry.fields["title"].value, "书签 & café")
        self.assertEqual(entry.fields["notes"].value, "保留备注")
        self.assertEqual(second.fields["tags"].value, [])
        self.assertEqual(second.fields["notes"].status, "missing")
        self.assertEqual(second.fields["saved_at"].status, "missing")

    def test_no_capability_is_not_empty(self):
        source = self.html('<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><DT><A HREF="https://example.org/">书签</A></DL>')
        target = exported([bookmark(tags=["存在标签"], note="备注")])
        fields = compare_exports(source, target)["matched"][0]["fields"]
        for key in ("tags", "notes", "saved_at", "archived", "attachments"):
            self.assertEqual(fields[key]["status"], "not_comparable", key)
        self.assertEqual(fields["folders"]["status"], "equal")

    def test_historical_folder_notes_and_livemarks(self):
        result = self.html('<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><DT><H3>历史文件夹</H3><DD>文件夹说明<DL><DT><A HREF="https://example.org/">书签</A><DD>第一行<BR>第二行<DT><A FEEDURL="https://example.org/feed">订阅</A><DD>订阅说明</DL></DL>')
        self.assertEqual(result.entries[0].fields["folders"].value, [("历史文件夹",)])
        self.assertEqual(result.entries[0].fields["notes"].value, "第一行\n第二行")
        self.assertEqual(result.metadata["folder_descriptions"][0]["description"], "文件夹说明")
        self.assertEqual(len(result.skipped), 1)
        self.assertEqual(result.skipped[0]["location"], "A[2]")
        self.assertIn("订阅说明", result.skipped[0]["raw"]["description"])

    def test_legacy_lists_unknown(self):
        old = bookmark()
        old.pop("lists")
        old.pop("archived")
        source = parse_karakeep(json.dumps({"bookmarks": [old]}), Path("old.json"))
        self.assertEqual(source.entries[0].fields["folders"].status, "unsupported")
        self.assertEqual(source.entries[0].fields["archived"].status, "unsupported")

    def test_invalid_html_is_rejected(self):
        for content in ['<html><a href="https://example.org/">x</a></html>', '<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><A HREF="https://x">x', '<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><A>x</A></DL>', '<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><H3>lost</H3><A HREF="https://x">x</A></DL>', '<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><DL></DL></DL>', '<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><A HREF="https://x" ADD_DATE="NaN">x</A></DL>']:
            with self.subTest(content=content), self.assertRaises(AuditError):
                self.html(content)

    def test_lists_ids_are_local_paths_are_comparable(self):
        s = exported([bookmark(lists=["child"])], [list_def("root", "阅读"), list_def("child", "技术", "root")])
        t = exported([bookmark(lists=["b"])], [list_def("a", "阅读"), list_def("b", "技术", "a")])
        self.assertEqual(compare_exports(s, t)["coverage"]["folders"]["equal"], 1)

    def test_lists_invalid_refs_cycles_and_duplicate_names(self):
        for lists in [[list_def("a", "a", "b")], [list_def("a", "a", "b"), list_def("b", "b", "a")], [list_def("a", "a"), list_def("a", "b")]]:
            with self.subTest(lists=lists), self.assertRaises(AuditError):
                exported([], lists)
        with self.assertRaises(AuditError):
            exported([bookmark(lists=["unknown"])])
        result = exported([], [list_def("a", "same"), list_def("b", "same")])
        self.assertTrue(any("same name" in item for item in result.warnings))

    def test_time_units_and_invalid_numbers(self):
        self.assertEqual(timestamp(1700000000, "x"), "2023-11-14T22:13:20Z")
        for value in [True, None, "1700000000", float("inf"), float("nan"), 1700000000000, 10**1000]:
            with self.subTest(value=value), self.assertRaises(AuditError):
                timestamp(value, "x")

    def test_json_malformed_and_duplicate_keys(self):
        for raw in ['{"bookmarks": [], "bookmarks": []}', '{"bookmarks":[NaN]}', '{', '[]', '{"bookmarks":[{}]}']:
            with self.subTest(raw=raw), self.assertRaises(AuditError):
                parse_karakeep(raw, Path("bad.json"))

    def test_unknown_content_does_not_silently_pass(self):
        with self.assertRaises(AuditError):
            exported([bookmark(content={"type": "asset", "url": "https://x"})])
        result = exported([bookmark(content={"type": "text", "text": "纯文本"}), bookmark(content=None)])
        self.assertEqual(len(result.skipped), 2)
        self.assertEqual(compare_exports(result, result)["exit_code"], 3)


class MatchingTests(unittest.TestCase):
    def test_cross_format_known_results_and_raw_mode(self):
        root = EXAMPLES / "cross-format"
        source, target = load_export(root / "source.html"), load_export(root / "target.json")
        expected = json.loads((root / "expected.json").read_text())
        for mode in ("conservative", "raw"):
            report = compare_exports(source, target, mode)
            for key, value in expected[mode].items():
                self.assertEqual(report["summary"][key], value, (mode, key))
        report = compare_exports(source, target)
        self.assertEqual(report["summary"]["conflicts"], 2)
        self.assertEqual(report["coverage"]["notes"]["conflict"], 0)
        self.assertEqual(report["coverage"]["tags"]["not_comparable"], 4)
        self.assertEqual({m["location"] for m in report["missing"]}, {"A[3]", "A[5]", "A[6]"})
        self.assertTrue(any("q=收藏" in e["url"] for e in report["added"]))

    def test_karakeep_known_results_and_attachments(self):
        root = EXAMPLES / "karakeep"
        source, target = load_export(root / "source.json"), load_export(root / "target.json")
        attach_manifest(source, root / "source-attachments.json")
        attach_manifest(target, root / "target-attachments.json")
        report = compare_exports(source, target)
        expected = json.loads((root / "expected.json").read_text())
        for key, value in expected["conservative"].items():
            self.assertEqual(report["summary"][key], value, key)
        changed = next(m for m in report["matched"] if m["source_location"] == "$.bookmarks[1]")
        self.assertEqual({k for k, v in changed["fields"].items() if v["status"] == "conflict"}, {"title", "tags", "folders", "notes", "saved_at", "archived"})
        self.assertEqual(report["summary"]["attachment_local_missing"], 1)
        self.assertEqual(report["summary"]["source_skipped"], 1)
        self.assertEqual({e["location"] for e in report["missing"]}, {"$.bookmarks[4]", "$.bookmarks[5]"})

    def test_duplicate_pairing_prefers_equal_fields_and_preserves_counts(self):
        source = exported([bookmark(note="a"), bookmark(note="b"), bookmark(note="c")])
        target = exported([bookmark(note="b"), bookmark(note="a")])
        report = compare_exports(source, target)
        self.assertEqual([(m["source_location"], m["target_location"]) for m in report["matched"]], [("$.bookmarks[0]", "$.bookmarks[1]"), ("$.bookmarks[1]", "$.bookmarks[0]")])
        self.assertTrue(all(m["ambiguous"] for m in report["matched"]))
        self.assertEqual(report["summary"]["conflicts"], 0)
        self.assertEqual(report["missing"][0]["fields"]["notes"]["value"], "c")

    def test_conservation_of_entries(self):
        for left in range(5):
            for right in range(5):
                with self.subTest(left=left, right=right):
                    report = compare_exports(exported([bookmark()] * left), exported([bookmark()] * right))
                    s = report["summary"]
                    self.assertEqual(s["matched"] + s["missing"], left)
                    self.assertEqual(s["matched"] + s["added"], right)

    def test_no_differences_is_not_whole_migration_pass(self):
        report = compare_exports(exported(), exported())
        self.assertEqual(report["exit_code"], 3)  # Attachments are unknown, not verified empty.
        self.assertEqual(report["verdict"], "incomplete")
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "refs.json"
            manifest.write_text('{"schema_version":1,"entries":{}}')
            s, t = exported(), exported()
            attach_manifest(s, manifest)
            attach_manifest(t, manifest)
            report = compare_exports(s, t)
            self.assertEqual(report["exit_code"], 0)
            self.assertEqual(report["verdict"], "no_differences_in_scope")


class AttachmentTests(unittest.TestCase):
    def manifest(self, directory, entries):
        path = Path(directory) / "refs.json"
        path.write_text(json.dumps({"schema_version": 1, "entries": entries}))
        return path

    def test_reference_and_presence_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            s, t = exported(), exported()
            present = Path(directory) / "present.txt"
            present.write_text("example")
            attach_manifest(s, self.manifest(directory, {"$.bookmarks[0]": [{"reference": "a", "path": "present.txt"}]}))
            attach_manifest(t, self.manifest(directory, {"$.bookmarks[0]": [{"reference": "a", "path": "missing.txt"}, {"reference": "b"}]}))
            report = compare_exports(s, t)
            self.assertEqual(report["coverage"]["attachments"]["conflict"], 1)
            self.assertEqual(report["summary"]["attachment_local_missing"], 1)
            self.assertEqual(report["summary"]["attachment_local_not_checked"], 1)

    def test_bad_manifests_and_path_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            for entries in [{"not-a-location": []}, {"$.bookmarks[0]": [{"reference": "a", "path": "../outside"}]}, {"$.bookmarks[0]": [{"reference": "a", "path": "/tmp/outside"}]}, {"$.bookmarks[0]": [{"reference": "a"}, {"reference": "a"}]}, {"$.bookmarks[0]": [{"reference": "a", "unknown": "x"}]}]:
                with self.subTest(entries=entries), self.assertRaises(AuditError):
                    attach_manifest(exported(), self.manifest(directory, entries))
            (Path(directory) / "escape").symlink_to(Path(directory).parent)
            with self.assertRaises(AuditError):
                attach_manifest(exported(), self.manifest(directory, {"$.bookmarks[0]": [{"reference": "a", "path": "escape/whatever"}]}))


class ReportCliTests(unittest.TestCase):
    def invoke(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([str(arg) for arg in args])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_offline_e2e_and_read_only_inputs(self):
        input_paths = sorted(p for p in EXAMPLES.rglob("*") if p.is_file())
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in input_paths}
        with tempfile.TemporaryDirectory() as directory, patch.object(socket, "socket", side_effect=AssertionError("Network forbidden")):
            root = EXAMPLES / "karakeep"
            code, stdout, stderr = self.invoke([root / "source.json", root / "target.json", "--source-attachments", root / "source-attachments.json", "--target-attachments", root / "target-attachments.json", "--output-dir", directory])
            self.assertEqual(code, 1)
            self.assertEqual(stderr, "")
            report = json.loads((Path(directory) / "report.json").read_text())
            self.assertEqual(report["summary"]["matched"], 6)
            self.assertIn("$.bookmarks[1]", (Path(directory) / "report.md").read_text())
            self.assertIn("<details>", (Path(directory) / "report.html").read_text())
            self.assertIn("field conflicts 6", stdout)
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in input_paths})

    def test_invalid_inputs_exit_two_no_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            for source in sorted((EXAMPLES / "bad-input").iterdir()):
                code, stdout, stderr = self.invoke([source, EXAMPLES / "karakeep/target.json", "--output-dir", directory])
                self.assertEqual(code, 2, source)
                self.assertIn("notewrench bookmark-migration: error:", stderr)
                self.assertNotIn("Traceback", stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_cli_incomplete_clean_and_huge_timestamp_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            target = root / "target.json"
            contents = {"bookmarks": [bookmark()], "lists": []}
            source.write_text(json.dumps(contents))
            target.write_text(json.dumps(contents))
            self.assertEqual(self.invoke([source, target, "--output-dir", root / "partial"])[0], 3)
            manifest = root / "refs.json"
            manifest.write_text('{"schema_version":1,"entries":{}}')
            self.assertEqual(self.invoke([source, target, "--source-attachments", manifest, "--target-attachments", manifest, "--output-dir", root / "clean"])[0], 0)
            contents["bookmarks"][0]["createdAt"] = 10**1000
            source.write_text(json.dumps(contents))
            code, _, stderr = self.invoke([source, target, "--output-dir", root / "bad"])
            self.assertEqual(code, 2)
            self.assertNotIn("Traceback", stderr)
            self.assertFalse((root / "bad").exists())

    def test_reports_never_overwrite_inputs_or_attachment_files(self):
        report = compare_exports(exported(), exported())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text("sensitive input")
            with self.assertRaises(AuditError):
                write_reports(report, Path(directory), ["json"], True, [path])
            self.assertEqual(path.read_text(), "sensitive input")
            with self.assertRaises(AuditError):
                write_reports(report, Path(directory), ["json"], False, [])
            path.unlink()
            protected = Path(directory) / "input.json"
            protected.write_text("safe")
            path.symlink_to(protected)
            with self.assertRaises(AuditError):
                write_reports(report, Path(directory), ["json"], True, [])
            self.assertEqual(protected.read_text(), "safe")
            path.unlink()
            os.link(protected, path)
            with self.assertRaises(AuditError):
                write_reports(report, Path(directory), ["json"], True, [protected])
            self.assertEqual(protected.read_text(), "safe")

    def test_force_rewrites_reports_and_format_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            args = [EXAMPLES / "cross-format/source.html", EXAMPLES / "cross-format/target.json", "--output-dir", directory, "--formats", "json"]
            self.assertEqual(self.invoke(args)[0], 1)
            self.assertEqual(self.invoke(args)[0], 2)
            self.assertEqual(self.invoke(args + ["--force"])[0], 1)
            self.assertEqual([p.name for p in Path(directory).iterdir()], ["report.json"])

    def test_html_does_not_interpret_untrusted_titles_or_urls(self):
        payload = '</pre><script>alert(1)</script><img src="https://x" onerror="alert(1)"> | ` **'
        s, t = exported([bookmark(title=payload, note=payload)]), exported([bookmark(title=payload, note=payload)])
        report = compare_exports(s, t)
        rendered = html_report(report)
        self.assertNotIn("<script", rendered)
        self.assertNotIn("<img", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        tags = []
        class Inspect(HTMLParser):
            def handle_starttag(self, tag, attrs):
                tags.append(tag)
        Inspect().feed(rendered)
        self.assertNotIn("a", tags)
        self.assertNotIn("img", tags)
        self.assertIn("default-src 'none'", rendered)
        md = markdown_report(report)
        self.assertIn("\\|", md)
        self.assertIn("`` ", md)

    def test_module_entrypoint_and_version(self):
        for module in (["-m", "notewrench", "bookmark-migration"], ["-m", "notewrench.bookmarkmigration"]):
            result = subprocess.run([sys.executable, *module, "--version"], capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.startswith("notewrench bookmark-migration "), result.stdout)

    def test_help_uses_notewrench_program_name(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout), self.assertRaises(SystemExit) as raised:
            main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertTrue(stdout.getvalue().startswith("usage: notewrench bookmark-migration"))
        self.assertNotIn("bookmark-audit", stdout.getvalue())

    def test_report_records_tool_version_and_schema(self):
        report = compare_exports(exported(), exported())
        self.assertEqual(report["schema_version"], 1)
        self.assertEqual(report["tool_version"], "0.1.0")


if __name__ == "__main__":
    unittest.main()
