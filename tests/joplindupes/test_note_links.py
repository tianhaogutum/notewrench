# SPDX-License-Identifier: AGPL-3.0-only
from pathlib import Path
import tempfile
import unittest

from notewrench.joplindupes import analyze, load_export
from notewrench.joplindupes.report import render_json, render_markdown
from .synthetic import linked_files, record, uid, write_jex, write_raw


class NoteLinkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.counter = 0

    def compare(self, left, right):
        self.counter += 1
        return analyze([
            load_export(write_raw(self.root / f"left-{self.counter}", left)),
            load_export(write_jex(self.root / f"right-{self.counter}.jex", right)),
        ])

    def assert_parent_not_exact(self, report):
        for group in report["exact_duplicates"]:
            self.assertNotIn(f"s1:{uid(1)}", group["members"])
            self.assertNotIn(f"s2:{uid(1001)}", group["members"])
        self.assertTrue(any(set(g["members"]) == {f"s1:{uid(1)}", f"s2:{uid(1001)}"}
                            for g in report["near_candidates"]))

    def test_remapped_link_chain_has_three_exact_groups_and_evidence(self):
        report = self.compare(linked_files(), linked_files(1000))
        self.assertEqual({frozenset(g["members"]) for g in report["exact_duplicates"]},
                         {frozenset((f"s1:{uid(n)}", f"s2:{uid(1000+n)}")) for n in (1, 2, 3)})
        self.assertEqual(report["near_candidates"], [])
        self.assertEqual(report["summary"]["blocked_notes"], 0)
        parent, middle = report["notes"][:2]
        self.assertEqual(len(parent["note_links"]), 2)
        link = parent["note_links"][0]
        self.assertEqual(link["original_url"], f":/{uid(2)}")
        self.assertEqual(link["target_key"], f"s1:{uid(2)}")
        self.assertEqual(link["status"], "normalized")
        self.assertEqual(link["target_exact_signature"], middle["exact_signature"])
        self.assertEqual(link["candidate_targets"], [f"s1:{uid(2)}", f"s2:{uid(1002)}"])
        self.assertIn("note_links", next(g for g in report["exact_duplicates"]
                                        if parent["key"] in g["members"])["conflicts"])
        self.assertIn("original_url", render_markdown(report))
        self.assertEqual(render_json(report), render_json(analyze([
            load_export(self.root / "left-1"), load_export(self.root / "right-1.jex")
        ])))

    def test_changed_target_body_bytes_mime_and_state_do_not_match_parents(self):
        for change in ("body", "bytes", "mime", "is_todo", "todo_due", "todo_completed",
                       "is_conflict", "deleted_time", "conflict_original_id"):
            with self.subTest(change=change):
                right = linked_files(1000)
                name = uid(1003) + ".md"
                if change == "body":
                    right[name] = right[name].replace(b"detail\n", b"changed detail\n")
                elif change == "bytes":
                    path = f"resources/{uid(1101)}.txt"
                    right[path] = b"x" * len(right[path])
                elif change == "mime":
                    path = uid(1101) + ".md"
                    right[path] = right[path].replace(b"text/plain", b"text/html")
                elif change == "is_todo":
                    right[name] = right[name].replace(b"is_todo: 1", b"is_todo: 0")
                else:
                    value = uid(999) if change == "conflict_original_id" else "1"
                    right[name] += f"\n{change}: {value}".encode()
                report = self.compare(linked_files(), right)
                self.assert_parent_not_exact(report)
                self.assertEqual(report["summary"]["blocked_notes"], 0)
                self.assertIn("note_link_target_differs_or_unproven",
                              next(g for g in report["near_candidates"]
                                   if f"s1:{uid(1)}" in g["members"])["reasons"])

    def test_missing_or_blocked_target_propagates_without_losing_original_ids(self):
        for change in ("missing_note", "missing_bytes", "locked", "html", "invalid_state"):
            with self.subTest(change=change):
                right = linked_files(1000)
                if change == "missing_note":
                    del right[uid(1003) + ".md"]
                elif change == "missing_bytes":
                    del right[f"resources/{uid(1101)}.txt"]
                else:
                    field = {"locked": "is_locked: 1", "html": "markup_language: 2",
                             "invalid_state": "todo_due: bad"}[change]
                    right[uid(1003) + ".md"] += ("\n" + field).encode()
                report = self.compare(linked_files(), right)
                self.assert_parent_not_exact(report)
                parent = next(n for n in report["notes"] if n["key"] == f"s2:{uid(1001)}")
                self.assertIsNone(parent["exact_signature"])
                self.assertEqual(parent["note_links"][0]["status"], "target_blocked")
                self.assertTrue(parent["blocked_reasons"])
                middle = next(n for n in report["notes"] if n["key"] == f"s2:{uid(1002)}")
                self.assertEqual(middle["note_links"][0]["original_url"], f":/{uid(1003)}")
                if change == "missing_note":
                    self.assertEqual(middle["note_links"][0]["status"], "unresolved")

    def test_ambiguous_target_in_one_export_blocks_both_link_chains(self):
        right = linked_files(1000)
        right[uid(1004) + ".md"] = right[uid(1003) + ".md"].replace(uid(1003).encode(), uid(1004).encode())
        report = self.compare(linked_files(), right)
        self.assert_parent_not_exact(report)
        self.assertEqual(report["summary"]["blocked_notes"], 4)
        self.assertEqual(len(report["exact_duplicates"]), 1)  # Leaves remain content duplicates.
        middle = report["notes"][1]
        link = middle["note_links"][0]
        self.assertEqual(link["status"], "ambiguous_target")
        self.assertEqual(link["candidate_targets"], [f"s1:{uid(3)}", f"s2:{uid(1003)}", f"s2:{uid(1004)}"])
        self.assertIsNone(link["target_exact_signature"])

    def test_cycles_and_incoming_links_stay_pending_even_with_identical_ids(self):
        for self_link in (False, True):
            with self.subTest(self_link=self_link):
                files = linked_files()
                target = uid(3 if self_link else 1)
                files[uid(3) + ".md"] = record(uid(3), 1, "Detail", f"[cycle](:/{target})")
                report = self.compare(files, files)
                self.assertEqual(report["exact_duplicates"], [])
                self.assertEqual(report["summary"]["blocked_notes"], 6)
                self.assertTrue(all(n["note_links"][0]["status"] == "cyclic_dependency"
                                    for n in report["notes"]))

    def test_parent_text_label_fragment_and_multiplicity_remain_significant(self):
        for old, new in ((b"Read ", b"read "), (b"[middle]", b"[renamed]"),
                         (f" twice: [middle](:/{uid(1002)})".encode(), b"")):
            right = linked_files(1000)
            right[uid(1001) + ".md"] = right[uid(1001) + ".md"].replace(old, new)
            report = self.compare(linked_files(), right)
            self.assertFalse(any(f"s1:{uid(1)}" in g["members"] for g in report["exact_duplicates"]))
        right = linked_files(1000)
        right[uid(1002) + ".md"] = right[uid(1002) + ".md"].replace(b"#section", b"#other")
        self.assert_parent_not_exact(self.compare(linked_files(), right))

    def test_link_order_and_same_id_changed_target_are_not_equivalent(self):
        left, right = {}, {}
        for files, offset in ((left, 0), (right, 1000)):
            for n, body in ((3, "alpha"), (4, "beta")):
                files[uid(offset+n) + ".md"] = record(uid(offset+n), 1, "Target", body)
            first, second = (3, 4) if not offset else (4, 3)
            files[uid(offset+1) + ".md"] = record(uid(offset+1), 1, "Index",
                f"[n](:/{uid(offset+first)}) [n](:/{uid(offset+second)})")
        self.assert_parent_not_exact(self.compare(left, right))
        right = linked_files()
        right[uid(3) + ".md"] = right[uid(3) + ".md"].replace(b"detail\n", b"changed detail\n")
        report = self.compare(linked_files(), right)
        self.assertEqual(report["exact_duplicates"], [])
        self.assertEqual(len(report["identity_conflicts"]), 3)

    def test_long_chain_uses_no_python_recursion(self):
        def chain(offset):
            return {uid(offset + n) + ".md": record(uid(offset + n), 1, str(n),
                    f"[next](:/{uid(offset+n+1)})" if n < 1100 else "leaf")
                    for n in range(1, 1101)}
        report = self.compare(chain(0), chain(10000))
        self.assertEqual(report["summary"]["exact_groups"], 1100)
        self.assertEqual(report["summary"]["blocked_notes"], 0)
