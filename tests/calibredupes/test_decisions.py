from contextlib import closing, redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from notewrench.calibredupes.cli import main
from .helpers import create_library, decision_libraries

ROOT = Path(__file__).resolve().parents[2]


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.record = {"title": "Synthetic Work", "authors": ["Writer"],
                       "identifiers": {"isbn": "0306406152"}, "formats": ["EPUB"]}
        self.a = create_library(self.base / "a", [self.record])
        other = {**self.record, "formats": ["PDF"]}
        self.b = create_library(self.base / "b", [other])
        for db in (self.a, self.b):
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.executescript("""
                    ALTER TABLE books ADD COLUMN pubdate TIMESTAMP;
                    UPDATE books SET pubdate='2020-01-01 00:00:00+00:00';
                    CREATE TABLE languages(id INTEGER PRIMARY KEY, lang_code TEXT NOT NULL);
                    CREATE TABLE books_languages_link(id INTEGER PRIMARY KEY, book INTEGER NOT NULL,
                        lang_code INTEGER NOT NULL, item_order INTEGER NOT NULL DEFAULT 0);
                    INSERT INTO languages VALUES(1,'eng');
                    INSERT INTO books_languages_link(book,lang_code) VALUES(1,1);
                """)
        self.decision_path = self.base / "decisions.json"

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = main(list(map(str, args)))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def review(self):
        code, out, err = self.call(self.a, self.b)
        self.assertEqual((code, err), (0, ""))
        return json.loads(out)

    def decisions(self):
        decisions = self.review()["decisionTemplate"]
        decisions["decisions"][0]["keep"] = "A:1"
        return decisions

    def save(self, decisions):
        self.decision_path.write_text(json.dumps(decisions), encoding="utf-8")

    def run_decision(self, decisions=None, *extra):
        if decisions is not None:
            self.save(decisions)
        return self.call(self.a, self.b, "--decisions", self.decision_path, *extra)

    def mutate(self, db, sql):
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute(sql)

    def snapshot(self):
        return {str(p.relative_to(self.base)): p.read_bytes()
                for p in self.base.rglob("*") if p.is_file()}

    def test_complementary_plan_deterministic_and_traceable_without_writes(self):
        decisions = self.decisions()
        # Independently reproduce the documented canonical member identity.
        member = self.review()["clusters"][0]["members"][0]
        expected = member.pop("memberSHA256")
        encoded = json.dumps(member, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), expected)
        self.save(decisions)
        before = self.snapshot()
        code, out, err = self.run_decision()
        self.assertEqual((code, err), (0, ""))
        plan = json.loads(out)
        merge = plan["mergePlan"]
        self.assertEqual(merge["action"], "plan-only")
        self.assertEqual(merge["decisionInput"], {"path": str(self.decision_path),
                         "sha256": hashlib.sha256(self.decision_path.read_bytes()).hexdigest()})
        item = merge["decisions"][0]
        self.assertEqual(item["decisionIndex"], 0)
        self.assertEqual(item["inputDecision"], decisions["decisions"][0])
        self.assertEqual((item["keep"], item["retainedFormats"]), ("A:1", ["EPUB"]))
        addition = item["formatAdditions"][0]
        self.assertEqual((addition["format"], addition["from"], addition["status"]),
                         ("PDF", "B:1", "proposed"))
        self.assertEqual(addition["sources"][0]["formatRecord"]["name"], "synthetic-1")
        self.assertEqual(addition["sources"][0]["verification"], {"status": "not-inspected"})
        self.assertEqual(item["metadataConflicts"], [])
        self.assertIn("edition-identity-unverified", item["manualReview"])
        self.assertEqual(self.run_decision(), (code, out, err))
        self.assertEqual(self.snapshot(), before)

    def test_language_author_and_edition_evidence_conflicts_require_manual_review(self):
        self.mutate(self.b, "UPDATE languages SET lang_code='deu'")
        self.mutate(self.b, "UPDATE authors SET name='Other Writer'")
        self.mutate(self.b, "UPDATE books SET title='Synthetic Work, second edition', pubdate='2024-01-01'")
        code, out, err = self.run_decision(self.decisions())
        self.assertEqual((code, err), (0, ""))
        item = json.loads(out)["mergePlan"]["decisions"][0]
        conflicts = {c["field"]: c for c in item["metadataConflicts"]}
        self.assertEqual(set(conflicts), {"languages", "authors", "title", "publicationDate"})
        for field, conflict in conflicts.items():
            self.assertEqual(conflict["action"], "manual-review")
            self.assertEqual([v["ref"] for v in conflict["values"]], ["A:1", "B:1"])
        self.assertIn("edition-evidence-conflict", item["manualReview"])
        self.assertEqual(conflicts["languages"]["values"][1]["value"], ["deu"])
        self.assertIn("author-conflict", item["candidateConflicts"])

    def test_changed_snapshot_even_outside_candidate_rejects_old_decisions(self):
        decisions = self.decisions()
        self.mutate(self.b, "INSERT INTO books(id,title,path) VALUES(2,'Unrelated','Elsewhere')")
        output = self.base / "must-not-exist.json"
        code, out, err = self.run_decision(decisions, "-o", output)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("snapshot", err)
        self.assertFalse(output.exists())

    def test_member_hash_changes_and_old_member_rejected_even_with_updated_snapshot(self):
        old = self.decisions()
        old_hash = old["decisions"][0]["members"][1]["memberSHA256"]
        self.mutate(self.b, "UPDATE languages SET lang_code='deu'")
        fresh = self.decisions()
        self.assertNotEqual(fresh["decisions"][0]["members"][1]["memberSHA256"], old_hash)
        old["libraries"] = fresh["libraries"]
        code, out, err = self.run_decision(old)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("member", err)

    def test_wrong_candidate_identity_keeper_and_duplicate_selection_rejected(self):
        original = self.decisions()
        variants = []
        for field, value in (("clusterId", "cluster-9999"), ("keep", "A:999"), ("keep", None),
                             ("members", []), ("members", original["decisions"][0]["members"][:1])):
            item = copy.deepcopy(original)
            item["decisions"][0][field] = value
            variants.append(item)
        bad_hash = copy.deepcopy(original)
        bad_hash["decisions"][0]["members"][0]["memberSHA256"] = "0" * 64
        variants.append(bad_hash)
        duplicate = copy.deepcopy(original)
        duplicate["decisions"].append(copy.deepcopy(duplicate["decisions"][0]))
        variants.append(duplicate)
        duplicate_member = copy.deepcopy(original)
        duplicate_member["decisions"][0]["members"].append(copy.deepcopy(duplicate_member["decisions"][0]["members"][0]))
        variants.append(duplicate_member)
        for index, variant in enumerate(variants):
            with self.subTest(index=index):
                output = self.base / f"no-{index}.json"
                code, out, err = self.run_decision(variant, "-o", output)
                self.assertEqual((code, out), (2, ""))
                self.assertTrue(err)
                self.assertFalse(output.exists())

    def test_strict_json_and_shape_fail_cleanly(self):
        decisions = self.decisions()
        invalid = ['{', '[]', '{"schemaVersion":1,"schemaVersion":1}', '{"x":NaN}',
                   json.dumps({**decisions, "schemaVersion": True}),
                   json.dumps({**decisions, "typo": 1}),
                   json.dumps({**decisions, "decisions": [None]}),
                   json.dumps({**decisions, "decisions": "wrong"})]
        for raw in invalid:
            with self.subTest(raw=raw):
                self.decision_path.write_text(raw)
                code, out, err = self.run_decision()
                self.assertEqual((code, out), (2, ""))
                self.assertTrue(err)
        self.decision_path.write_bytes(b'\xff')
        self.assertEqual(self.run_decision()[:2], (2, ""))

    def test_library_order_and_path_are_bound(self):
        self.save(self.decisions())
        code, out, err = self.call(self.b, self.a, "--decisions", self.decision_path)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("snapshot", err)

    def test_overlapping_formats_never_replace_keeper(self):
        self.mutate(self.b, "INSERT INTO data(book,format,name,uncompressed_size) VALUES(1,'EPUB','other',9)")
        code, out, err = self.run_decision(self.decisions())
        self.assertEqual((code, err), (0, ""))
        item = json.loads(out)["mergePlan"]["decisions"][0]
        self.assertEqual([a["format"] for a in item["formatAdditions"]], ["PDF"])
        self.assertIn("overlapping-format-content-unverified", item["candidateConflicts"])
        self.assertIn("overlapping-formats-retained-without-replacement", item["manualReview"])

    def test_multiple_donors_and_indirect_pairs_need_manual_choice(self):
        # A missing-author bridge connects distinct known authors in one library.
        book = {**self.record, "identifiers": {}}
        db = create_library(self.base / "single", [book,
             {**book, "authors": [], "formats": ["PDF"]},
             {**book, "authors": ["Other Writer"], "formats": ["PDF"]}])
        code, out, err = self.call(db)
        self.assertEqual((code, err), (0, ""))
        decisions = json.loads(out)["decisionTemplate"]
        decisions["decisions"][0]["keep"] = "A:1"
        self.save(decisions)
        code, out, err = self.call(db, "--decisions", self.decision_path)
        self.assertEqual((code, err), (0, ""))
        item = json.loads(out)["mergePlan"]["decisions"][0]
        addition = item["formatAdditions"][0]
        self.assertEqual((addition["from"], addition["status"]), (None, "manual-source-selection"))
        self.assertEqual([s["ref"] for s in addition["sources"]], ["A:2", "A:3"])
        self.assertIn("language-unknown", item["manualReview"])
        self.assertIn("publication-date-unknown", item["manualReview"])
        self.assertIn("missing-author", item["candidateConflicts"])
        self.assertIn("candidate-has-unevidenced-pairs", item["manualReview"])

    def test_optional_metadata_bad_links_partial_schema_and_storage_refused(self):
        for statement in ("INSERT INTO books_languages_link(book,lang_code) VALUES(99,1)",
                          "INSERT INTO books_languages_link(book,lang_code) VALUES(1,99)",
                          "INSERT INTO books_languages_link(book,lang_code) VALUES(1,1)",
                          "UPDATE languages SET lang_code=x'ff'", "DROP TABLE languages",
                          "UPDATE books SET pubdate=123"):
            with self.subTest(sql=statement):
                before = self.a.read_bytes()
                self.mutate(self.a, statement)
                code, out, err = self.call(self.a, self.b)
                self.assertEqual((code, out), (2, ""))
                self.assertTrue(err)
                self.a.write_bytes(before)

    def test_optional_file_evidence_does_not_claim_declared_additions_exist(self):
        code, out, err = self.run_decision(self.decisions(), "--verify-files")
        self.assertEqual((code, err), (0, ""))
        item = json.loads(out)["mergePlan"]["decisions"][0]
        self.assertEqual(item["formatAdditions"][0]["status"], "source-unavailable")
        self.assertIn("format-source-unavailable", item["manualReview"])
        source = item["formatAdditions"][0]["sources"][0]
        self.assertEqual(source["verification"]["status"], "missing")
        self.assertNotIn("sha256", source["verification"])

    def test_subset_reordered_members_empty_plan_and_atomic_multi_decision_failure(self):
        a, b = decision_libraries(self.base / "example")
        code, out, err = self.call(a, b)
        self.assertEqual((code, err), (0, ""))
        decisions = json.loads(out)["decisionTemplate"]
        self.assertEqual(len(decisions["decisions"]), 2)
        for index, decision in enumerate(decisions["decisions"], 1):
            decision["keep"] = f"A:{index}"
            decision["members"].reverse()
        self.save(decisions)
        before = self.snapshot()
        code, out, err = self.call(a, b, "--decisions", self.decision_path)
        self.assertEqual((code, err), (0, ""))
        items = json.loads(out)["mergePlan"]["decisions"]
        self.assertEqual([i["decisionIndex"] for i in items], [0, 1])
        self.assertEqual(items[0]["metadataConflicts"], [])
        self.assertEqual({c["field"] for c in items[1]["metadataConflicts"]},
                         {"title", "authors", "languages", "publicationDate"})
        self.assertEqual(self.snapshot(), before)
        invalid = copy.deepcopy(decisions)
        invalid["decisions"][1]["keep"] = "B:999"
        self.save(invalid)
        output = self.base / "no-partial-plan.json"
        self.assertEqual(self.call(a, b, "--decisions", self.decision_path, "-o", output)[:2], (2, ""))
        self.assertFalse(output.exists())
        decisions["decisions"] = decisions["decisions"][1:]
        self.save(decisions)
        code, out, err = self.call(a, b, "--decisions", self.decision_path)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual([i["keep"] for i in json.loads(out)["mergePlan"]["decisions"]], ["A:2"])
        decisions["decisions"] = []
        self.save(decisions)
        code, out, err = self.call(a, b, "--decisions", self.decision_path)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["mergePlan"]["decisions"], [])

    def test_cli_outside_repository_decision_workflow_outside_worktree(self):
        self.save(self.decisions())
        result = subprocess.run([sys.executable, "-m", "notewrench", "calibre-dupes", str(self.a), str(self.b),
                                 "--decisions", str(self.decision_path)], cwd=self.base,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        item = json.loads(result.stdout)["mergePlan"]["decisions"][0]
        self.assertEqual(item["formatAdditions"][0]["from"], "B:1")


if __name__ == "__main__":
    unittest.main()
