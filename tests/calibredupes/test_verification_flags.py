from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from notewrench.calibredupes import compare, files
from notewrench.calibredupes.reader import Library, ReviewError, read_library
from .helpers import create_library


class TruthinessTripwire:
    def __bool__(self):
        raise AssertionError("flag truthiness evaluated")


class VerificationFlagTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)

    def call(self, api, libraries, flag, **budgets):
        if api == "make_plan":
            return compare.make_plan(libraries, verify_files=flag, **budgets)["fileVerification"]
        return files.verify_libraries(libraries, enabled=flag, **budgets)

    def test_nonboolean_flags_fail_before_library_and_filesystem_work(self):
        values = (None, 0, 1, -1, 0.0, 1.0, float("nan"), float("inf"),
                  "", "false", "true", b"false", [], [False], (), (False,),
                  {}, {"enabled": False}, set(), {False}, TruthinessTripwire())
        for api in ("make_plan", "verify_libraries"):
            for count in (1, 2):
                for flag in values:
                    for budgets in ({}, {"max_file_records": 0},
                                    {"max_total_file_bytes": 0},
                                    {"max_file_records": 2, "max_total_file_bytes": 2}):
                        with self.subTest(api=api, count=count, flag=flag, budgets=budgets):
                            books = MagicMock()
                            books.__iter__.side_effect = AssertionError("books traversed")
                            libraries = [Library(label, self.base / label,
                                                 self.base / label / "metadata.db",
                                                 books, "", 0) for label in ("A", "B")[:count]]
                            targets = ((Path, "samefile"), (Path, "stat"),
                                       (compare, "find_pairs"), (files, "_root_descriptor"),
                                       (files, "_component"), (files, "_inspect"),
                                       (files.os, "open"), (files.os, "read"))
                            with ExitStack() as stack:
                                mocks = [stack.enter_context(patch.object(
                                    owner, name, side_effect=AssertionError(name + " reached")))
                                    for owner, name in targets]
                                with self.assertRaisesRegex(
                                        ReviewError, "^--verify-files must be a boolean$"):
                                    self.call(api, libraries, flag, **budgets)
                                for mock in mocks:
                                    mock.assert_not_called()
                                books.__iter__.assert_not_called()
        self.assertEqual(list(self.base.iterdir()), [])

    def test_flag_and_budget_diagnostic_precedence(self):
        # Pair validation remains first in make_plan. Flag errors precede
        # verification budgets and even malformed/inaccessible libraries.
        with self.assertRaisesRegex(ReviewError, "^--max-pairs must be a positive integer$"):
            compare.make_plan([], max_pairs=False, verify_files="false", max_file_records=True)
        for api in ("make_plan", "verify_libraries"):
            for budgets in ({"max_file_records": True, "max_total_file_bytes": True},
                            {"max_file_records": 0, "max_total_file_bytes": True},
                            {"max_total_file_bytes": True}):
                libraries = MagicMock()
                libraries.__len__.side_effect = AssertionError("library count accessed")
                libraries.__iter__.side_effect = AssertionError("libraries traversed")
                with self.subTest(api=api, budgets=budgets), self.assertRaisesRegex(
                        ReviewError, "^--verify-files must be a boolean$"):
                    self.call(api, libraries, None, **budgets)
                libraries.__len__.assert_not_called()
                libraries.__iter__.assert_not_called()

    def test_exact_booleans_preserve_metadata_and_verification_results(self):
        groups = []
        for name, payload in (("full", b"AA"), ("empty", b"")):
            libraries = []
            for label in ("A", "B"):
                db = create_library(self.base / name / label, [
                    {"title": "Example", "authors": ["Writer"], "formats": ["EPUB"]}])
                path = db.parent / "Synthetic/Book (1)/synthetic-1.epub"
                path.parent.mkdir(parents=True)
                path.write_bytes(payload)
                libraries.append(read_library(db, label))
            groups.append(libraries)

        def snapshot():
            return {str(p.relative_to(self.base)): p.read_bytes() if p.is_file() else None
                    for p in self.base.rglob("*")}

        before = snapshot()
        for api in ("make_plan", "verify_libraries"):
            with self.subTest(api=api, flag=False), \
                    patch.object(files, "_root_descriptor", side_effect=AssertionError("root opened")), \
                    patch.object(files.os, "read", side_effect=AssertionError("payload read")):
                result = self.call(api, groups[0], False)
                self.assertEqual(result, {"enabled": False, "evidenceLevel": "metadata-only", "records": []})
                if api == "make_plan":
                    self.assertEqual(compare.make_plan(groups[0]),
                                     compare.make_plan(groups[0], verify_files=False))
            for libraries, limit, total in ((groups[0], None, 4), (groups[0], 4, 4),
                                             (groups[1], 0, 0)):
                with self.subTest(api=api, flag=True, limit=limit):
                    result = self.call(api, libraries, True, max_total_file_bytes=limit,
                                       max_file_records=2)
                    self.assertIs(result["enabled"], True)
                    self.assertEqual((result["maxTotalFileBytes"], result["totalBytesRead"]),
                                     (limit, total))
                    self.assertEqual((result["fileRecordsInspected"], result["summary"]),
                                     (2, {"verified": 2}))
        self.assertEqual(snapshot(), before)


if __name__ == "__main__":
    unittest.main()
