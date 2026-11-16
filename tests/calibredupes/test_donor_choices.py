from contextlib import closing, redirect_stderr, redirect_stdout
import copy
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from notewrench.calibredupes.cli import main
from .helpers import create_library, decision_libraries, donor_libraries


class DonorChoiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.a, self.b = donor_libraries(self.base)
        self.path = self.base / "decisions.json"
        self.output = self.base / "plan.json"

    def call(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main([str(self.a), str(self.b), *map(str, extra)])
        return code, out.getvalue(), err.getvalue()

    def document(self):
        code, out, err = self.call()
        self.assertEqual((code, err), (0, ""))
        document = json.loads(out)["decisionTemplate"]
        document["decisions"][0]["keep"] = "A:1"
        return document

    def run_choice(self, document, *extra):
        self.path.write_text(json.dumps(document), encoding="utf-8")
        return self.call("--decisions", self.path, *extra)

    def item(self, document, *extra):
        code, out, err = self.run_choice(document, *extra)
        self.assertEqual((code, err), (0, ""))
        plan = json.loads(out)["mergePlan"]
        self.assertEqual(plan["action"], "plan-only")
        return plan["decisions"][0]

    def snapshot(self):
        return {str(p.relative_to(self.base)): p.read_bytes()
                for p in self.base.rglob("*") if p.is_file()}

    def mutate(self, sql):
        with closing(sqlite3.connect(self.b)) as conn, conn:
            conn.execute(sql)

    def assert_rejected(self, document, diagnostic):
        code, out, err = self.run_choice(document, "-o", self.output)
        self.assertEqual((code, out), (2, ""))
        self.assertIn(diagnostic, err)
        self.assertFalse(self.output.exists())
        # stdout mode must also reject without leaking a partial plan.
        self.assertEqual(self.call("--decisions", self.path)[:2], (2, ""))

    def test_template_advertises_empty_optional_choices_and_legacy_input_still_works(self):
        document = self.document()
        self.assertEqual(document["decisions"][0]["formatSources"], {})
        del document["decisions"][0]["formatSources"]
        item = self.item(document)
        self.assertEqual([a["from"] for a in item["formatAdditions"]], [None, None])
        self.assertIn("format-source-selection-required", item["manualReview"])

    def test_independent_choices_preserve_all_evidence_and_inputs(self):
        document = self.document()
        document["decisions"][0]["formatSources"] = {"PDF": "B:2", "MOBI": "B:1"}
        item = self.item(document)
        before = self.snapshot()
        first = self.call("--decisions", self.path)
        self.assertEqual(self.call("--decisions", self.path), first)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(item["inputDecision"], document["decisions"][0])
        self.assertEqual(item["retainedFormats"], ["EPUB"])
        additions = {a["format"]: a for a in item["formatAdditions"]}
        for fmt, ref in document["decisions"][0]["formatSources"].items():
            addition = additions[fmt]
            self.assertEqual((addition["from"], addition["status"]), (ref, "proposed"))
            self.assertEqual([s["ref"] for s in addition["sources"]], ["B:1", "B:2"])
            selected = next(s for s in addition["sources"] if s["ref"] == ref)
            identity = next(m for m in document["decisions"][0]["members"] if m["ref"] == ref)
            self.assertEqual(selected["memberSHA256"], identity["memberSHA256"])
            self.assertEqual(selected["verification"], {"status": "not-inspected"})
        self.assertNotIn("format-source-selection-required", item["manualReview"])
        self.assertIn("edition-identity-unverified", item["manualReview"])
        self.assertIn("candidate-has-unevidenced-pairs", item["manualReview"])
        self.assertIn("overlapping-formats-retained-without-replacement", item["manualReview"])

    def test_partial_choices_leave_remaining_ambiguity(self):
        document = self.document()
        document["decisions"][0]["formatSources"] = {"PDF": "B:2"}
        item = self.item(document)
        mobi, pdf = item["formatAdditions"]
        self.assertEqual((mobi["from"], mobi["status"]), (None, "manual-source-selection"))
        self.assertEqual((pdf["from"], pdf["status"]), ("B:2", "proposed"))
        self.assertIn("format-source-selection-required", item["manualReview"])

    def test_wrong_donor_format_and_input_types_rejected(self):
        original = self.document()
        for choices in (None, [], "B:2", {"PDF": None}, {"PDF": []}, {"PDF": 2},
                        {"PDF": "B:999"}, {"PDF": "B:4"}, {"PDF": "A:1"},
                        {"PDF": "B:3"}, {"EPUB": "B:3"}, {"AZW3": "B:1"},
                        {"pdf": "B:1"}):
            with self.subTest(choices=choices):
                document = copy.deepcopy(original)
                document["decisions"][0]["formatSources"] = choices
                self.assert_rejected(document, "formatSources")

    def test_stale_selected_donor_snapshot_rejected(self):
        document = self.document()
        document["decisions"][0]["formatSources"] = {"PDF": "B:2"}
        self.mutate("UPDATE data SET name='changed-donor' WHERE book=2 AND format='PDF'")
        self.assert_rejected(document, "snapshot")

    def test_stale_selected_member_hash_rejected_even_with_fresh_library_hash(self):
        document = self.document()
        document["decisions"][0]["formatSources"] = {"PDF": "B:2"}
        self.mutate("UPDATE data SET name='changed-donor' WHERE book=2 AND format='PDF'")
        document["libraries"] = self.document()["libraries"]
        self.assert_rejected(document, "member")

    def test_selected_unavailable_donor_never_falls_back_to_verified_alternative(self):
        folder = self.b.parent / "Synthetic/Book (1)"
        folder.mkdir(parents=True)
        (folder / "synthetic-1.pdf").write_bytes(b"synthetic PDF bytes")
        document = self.document()
        document["decisions"][0]["formatSources"] = {"PDF": "B:2"}
        item = self.item(document, "--verify-files")
        pdf = next(a for a in item["formatAdditions"] if a["format"] == "PDF")
        self.assertEqual((pdf["from"], pdf["status"]), ("B:2", "source-unavailable"))
        self.assertEqual([s["verification"]["status"] for s in pdf["sources"]],
                         ["verified", "missing"])
        self.assertNotIn("sha256", pdf["sources"][1]["verification"])
        self.assertIn("format-source-unavailable", item["manualReview"])
        document["decisions"][0]["formatSources"] = {"PDF": "B:1"}
        pdf = next(a for a in self.item(document, "--verify-files")["formatAdditions"]
                   if a["format"] == "PDF")
        self.assertEqual((pdf["from"], pdf["status"]), ("B:1", "proposed"))

    def test_duplicate_format_key_and_unknown_decision_field_rejected(self):
        document = self.document()
        document["decisions"][0]["formatSources"] = {"PDF": "B:1"}
        self.path.write_text(json.dumps(document).replace('"PDF": "B:1"',
                             '"PDF": "B:1", "PDF": "B:2"'))
        code, out, err = self.call("--decisions", self.path, "-o", self.output)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("duplicate JSON key", err)
        self.assertFalse(self.output.exists())
        document["decisions"][0]["formatSource"] = {"PDF": "B:1"}
        self.assert_rejected(document, "invalid decisions[0]")

    def test_invalid_later_donor_rejects_whole_document_and_single_source_choice_works(self):
        self.a, self.b = decision_libraries(self.base / "two-groups")
        document = self.document()
        document["decisions"][0]["formatSources"] = {"PDF": "B:1"}
        document["decisions"][1].update(keep="A:2", formatSources={"PDF": "B:1"})
        self.assert_rejected(document, "decisions[1].formatSources")
        document["decisions"][1]["formatSources"] = {"PDF": "B:2"}
        code, out, err = self.run_choice(document)
        self.assertEqual((code, err), (0, ""))
        items = json.loads(out)["mergePlan"]["decisions"]
        self.assertEqual([i["formatAdditions"][0]["from"] for i in items], ["B:1", "B:2"])

    def test_within_library_choice_accepts_reordered_member_identities(self):
        common = {"title": "Synthetic within-library", "authors": ["Writer"]}
        db = create_library(self.base / "within", [
            {**common, "formats": ["EPUB"]},
            {**common, "formats": ["PDF"]}, {**common, "formats": ["PDF"]}])
        def call(*extra):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main([str(db), *map(str, extra)])
            self.assertEqual((code, err.getvalue()), (0, ""))
            return json.loads(out.getvalue())
        document = call()["decisionTemplate"]
        document["decisions"][0].update(keep="A:1", formatSources={"PDF": "A:3"})
        document["decisions"][0]["members"].reverse()
        self.path.write_text(json.dumps(document))
        addition = call("--decisions", self.path)["mergePlan"]["decisions"][0]["formatAdditions"][0]
        self.assertEqual((addition["from"], addition["status"]), ("A:3", "proposed"))


if __name__ == "__main__":
    unittest.main()
