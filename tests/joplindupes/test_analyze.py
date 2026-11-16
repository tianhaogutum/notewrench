# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
import tempfile
import unittest

from notewrench.joplindupes import ExportError, analyze, load_export
from notewrench.joplindupes.report import render_json, render_markdown
from .synthetic import example_files, record, uid, write_raw


class AnalyzeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.counter = 0

    def analyze_files(self, files):
        self.counter += 1
        return analyze([load_export(write_raw(self.root / str(self.counter), files))])

    def pair(self, body1="same", body2="same", **second_fields):
        return {uid(1) + ".md": record(uid(1), 1, "Title", body1, is_todo=0, is_conflict=0),
                uid(2) + ".md": record(uid(2), 1, "Title", body2, is_todo=0, is_conflict=0, **second_fields)}

    def test_same_body_same_bytes_reid_unicode_crlf_and_conflicts(self):
        report = self.analyze_files(example_files())
        self.assertEqual(report["summary"], {"notes": 3, "exact_groups": 1, "near_groups": 0,
                         "blocked_notes": 0, "unknown_items": 0, "identity_conflicts": 0})
        group = report["exact_duplicates"][0]
        self.assertEqual(group["members"], [f"s1:{uid(1)}", f"s1:{uid(2)}"])
        self.assertIn("metadata.created_time", group["conflicts"])
        self.assertIn("notebook", group["conflicts"])
        self.assertIn("resources", group["conflicts"])
        self.assertIn("tags", group["conflicts"])
        self.assertEqual(report["notes"][0]["metadata"]["synthetic_extra"], "retained: yes")
        self.assertEqual(report["notes"][0]["tags"][0]["title"], "synthetic")
        self.assertEqual(report["metrics"]["resource_hashes"], 2)

    def test_title_only_match_excluded(self):
        report = self.analyze_files(self.pair("alpha", "beta"))
        self.assertEqual(report["exact_duplicates"], [])
        self.assertEqual(report["near_candidates"], [])
        self.assertIn("No exact duplicate groups found.", render_markdown(report))
        self.assertIn("No near candidate groups found.", render_markdown(report))

    def test_newlines_nfc_only_no_whitespace_case_or_compatibility_folding(self):
        for a, b, expected in [("Café\r\nx", "Cafe\u0301\nx", 1), ("A", "a", 0),
                                (" a", "a", 0), ("x\n", "x", 0), ("①", "1", 0),
                                ("a\n\nb", "a\nb", 0)]:
            with self.subTest(a=a, b=b):
                report = self.analyze_files(self.pair(a, b))
                self.assertEqual(len(report["exact_duplicates"]), expected)

    def test_same_body_different_attachment_is_near(self):
        files = example_files()
        files[f"resources/{uid(302)}.txt"] = b"x" * len(files[f"resources/{uid(302)}.txt"])
        report = self.analyze_files(files)
        self.assertFalse(report["exact_duplicates"])
        self.assertEqual(report["near_candidates"][0]["reasons"], ["resource_content_or_mime_differs"])
        self.assertEqual(report["near_candidates"][0]["members"], [f"s1:{uid(1)}", f"s1:{uid(2)}"])

    def test_same_bytes_different_mime_not_exact(self):
        files = example_files()
        files[uid(302) + ".md"] = files[uid(302) + ".md"].replace(b"text/plain", b"text/html")
        report = self.analyze_files(files)
        self.assertFalse(report["exact_duplicates"])
        self.assertIn("resource_content_or_mime_differs", report["near_candidates"][0]["reasons"])

    def test_todo_conflict_and_trash_status_are_not_exact(self):
        for field, value in (("is_todo", "1"), ("todo_completed", "1700000000000"),
                             ("todo_due", "1700000000001"), ("is_conflict", "1"),
                             ("conflict_original_id", uid(99)), ("deleted_time", "1700000000002")):
            files = self.pair()
            item = files[uid(2) + ".md"].decode()
            if field in ("is_todo", "is_conflict"):
                item = item.replace(f"{field}: 0", f"{field}: {value}")
            else:
                item += f"\n{field}: {value}"
            files[uid(2) + ".md"] = item.encode()
            with self.subTest(field=field):
                report = self.analyze_files(files)
                self.assertFalse(report["exact_duplicates"])
                self.assertIn("todo_conflict_or_trash_state_differs", report["near_candidates"][0]["reasons"])
                self.assertIn("metadata." + field, report["near_candidates"][0]["conflicts"])
                self.assertIn("state", report["near_candidates"][0]["conflicts"])

    def test_missing_encrypted_or_inconsistent_resources_block_exact(self):
        for scenario, reason in (("missing", "missing_resource_file"), ("encrypted", "encrypted_resource"),
                                 ("size", "resource_size_mismatch"), ("extension", "resource_extension_mismatch"),
                                 ("metadata", "unresolved_internal_link")):
            files = example_files()
            if scenario == "missing":
                del files[f"resources/{uid(302)}.txt"]
            elif scenario == "metadata":
                del files[uid(302) + ".md"]
            elif scenario == "encrypted":
                files[uid(302) + ".md"] = files[uid(302) + ".md"].replace(b"encryption_blob_encrypted: 0", b"encryption_blob_encrypted: 1")
            elif scenario == "size":
                files[f"resources/{uid(302)}.txt"] += b"x"
            else:
                files[uid(302) + ".md"] = files[uid(302) + ".md"].replace(b"file_extension: txt", b"file_extension: bin")
            with self.subTest(scenario=scenario):
                report = self.analyze_files(files)
                self.assertFalse(report["exact_duplicates"])
                self.assertEqual(report["summary"]["blocked_notes"], 1)
                self.assertTrue(any(s.startswith(reason) for s in report["notes"][1]["blocked_reasons"]))
                self.assertIn("incomplete_or_unsupported_evidence", report["near_candidates"][0]["reasons"])

    def test_encrypted_locked_unsupported_and_empty_notes_are_unassessable(self):
        for fields in ({"encryption_applied": 1}, {"encryption_cipher_text": "ciphertext"},
                       {"is_locked": 1}, {"markup_language": 2}, {"todo_due": "bad"}):
            files = self.pair(**fields)
            with self.subTest(fields=fields):
                report = self.analyze_files(files)
                self.assertFalse(report["exact_duplicates"])
                self.assertEqual(report["summary"]["blocked_notes"], 1)
        report = self.analyze_files(self.pair("", ""))
        self.assertFalse(report["exact_duplicates"])
        self.assertEqual(report["summary"]["blocked_notes"], 2)

    def test_multiple_exact_subgroups_in_one_near_family(self):
        files = example_files()
        files[uid(4) + ".md"] = record(uid(4), 1, "Renamed", f"Café observations\n\n![sample](:/{uid(301)})\n\ncode: this is body text", is_todo=1)
        report = self.analyze_files(files)
        self.assertEqual(len(report["exact_duplicates"]), 1)
        self.assertEqual(len(report["near_candidates"]), 1)
        self.assertEqual(len(report["near_candidates"][0]["members"]), 3)

    def test_note_links_retained_not_treated_as_missing_attachments(self):
        files = self.pair(f"[n](:/{uid(3)})", f"[n](:/{uid(3)})")
        files[uid(3) + ".md"] = record(uid(3), 1, "Target", "Target content")
        report = self.analyze_files(files)
        self.assertEqual(report["summary"]["exact_groups"], 1)
        self.assertEqual(report["summary"]["blocked_notes"], 0)
        self.assertEqual(report["notes"][0]["resources"], [])
        exports = [load_export(write_raw(self.root / name, files)) for name in ("left", "right")]
        combined = analyze(exports)
        # The single leaf target in each export establishes correspondence.
        linked_group_sizes = [len(g["members"]) for g in combined["exact_duplicates"]]
        self.assertEqual(sorted(linked_group_sizes), [2, 4])

    def test_order_and_multiplicity_of_resource_links_matters(self):
        files = example_files()
        files[uid(2) + ".md"] = files[uid(2) + ".md"].replace(b"![sample]", b"![different label]")
        report = self.analyze_files(files)
        self.assertFalse(report["exact_duplicates"])
        self.assertFalse(report["near_candidates"])
        files = example_files()
        files[uid(2) + ".md"] = files[uid(2) + ".md"].replace(b"code: this is body text", f"[extra](:/{uid(302)})".encode())
        self.assertFalse(self.analyze_files(files)["exact_duplicates"])

    def test_identity_conflicts_across_exports_retain_occurrences(self):
        left = {uid(1) + ".md": record(uid(1), 1, "Title", "A")}
        right = {uid(1) + ".md": record(uid(1), 1, "Title", "B")}
        report = analyze([load_export(write_raw(self.root / "a", left)), load_export(write_raw(self.root / "b", right))])
        self.assertEqual(report["summary"]["notes"], 2)
        self.assertFalse(report["exact_duplicates"])
        self.assertEqual(report["identity_conflicts"][0]["members"], [f"s1:{uid(1)}", f"s2:{uid(1)}"])
        self.assertIn("normalized_body_sha256", report["identity_conflicts"][0]["conflicts"])

    def test_missing_folder_cycles_and_dangling_tags_reported(self):
        files = self.pair()
        files[uid(1) + ".md"] += f"\nparent_id: {uid(10)}".encode()
        files[uid(2) + ".md"] += f"\nparent_id: {uid(11)}".encode()
        files[uid(10) + ".md"] = record(uid(10), 2, "Cycle", parent_id=uid(10))
        files[uid(20) + ".md"] = record(uid(20), 6, note_id=uid(1), tag_id=uid(21))
        report = self.analyze_files(files)
        self.assertEqual({w["code"] for w in report["warnings"]}, {"folder_cycle", "missing_folder", "dangling_note_tag"})
        self.assertEqual(report["notes"][0]["tags"][0]["id"], uid(21))

    def test_unknown_items_preserved_and_deterministic_reports(self):
        files = example_files()
        files[uid(999) + ".md"] = record(uid(999), 99, "```\n\nunknown body", "", private_field="a:b")
        report = self.analyze_files(files)
        self.assertEqual(report["unknown_items"][0]["metadata"]["private_field"], "a:b")
        self.assertEqual(report["summary"]["unknown_items"], 1)
        self.assertEqual(render_json(report), render_json(report))
        markdown = render_markdown(report)
        self.assertIn("````json", markdown)
        export = load_export(self.root / "1")
        export.items = dict(reversed(list(export.items.items())))
        self.assertEqual(render_json(report), render_json(analyze([export])))

    def test_report_size_limit_fails_instead_of_truncating_evidence(self):
        report = self.analyze_files(example_files())
        with self.assertRaisesRegex(ExportError, "Report size limit"):
            render_json(report, max_bytes=100)

    def test_unreferenced_metadata_and_files_have_inventory(self):
        files = self.pair()
        files[uid(40) + ".md"] = record(uid(40), 4, "unused", file_extension="txt")
        files[f"resources/{uid(40)}.txt"] = b"unreferenced"
        files[uid(41) + ".md"] = record(uid(41), 2, "unused folder")
        report = self.analyze_files(files)
        self.assertEqual({i["id"] for i in report["non_note_items"]}, {uid(40), uid(41)})
        self.assertEqual(report["resource_files"][0]["size"], len(b"unreferenced"))

    def test_no_notes_clear_empty_report(self):
        report = self.analyze_files({uid(1) + ".md": record(uid(1), 2, "Only folder")})
        self.assertEqual(report["summary"]["notes"], 0)
        self.assertEqual(report["exact_duplicates"], [])
        self.assertEqual(report["near_candidates"], [])
        with self.assertRaises(ValueError):
            analyze([])
