from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from notewrench.calibredupes import compare, files
from notewrench.calibredupes.reader import Library, ReviewError


class BudgetPreflightTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)

    def libraries(self):
        # These databases do not exist. Traversing books must also fail loudly.
        books = MagicMock()
        books.__iter__.side_effect = AssertionError("books traversed")
        return [Library(label, self.base / label, self.base / label / "metadata.db",
                        books, "", 0) for label in ("A", "B")]

    def assert_preflight_error(self, libraries, diagnostic, **kwargs):
        with patch.object(Path, "samefile", side_effect=AssertionError("database stat")) as same, \
                patch.object(compare, "find_pairs", side_effect=AssertionError("pair indexing")) as pairs, \
                patch.object(files, "_root_descriptor", side_effect=AssertionError("root open")) as root, \
                patch.object(files, "_inspect", side_effect=AssertionError("file inspection")) as inspect:
            with self.assertRaisesRegex(ReviewError, "^" + diagnostic + "$"):
                compare.make_plan(libraries, **kwargs)
            same.assert_not_called()
            pairs.assert_not_called()
            root.assert_not_called()
            inspect.assert_not_called()
        for lib in libraries:
            lib.books.__iter__.assert_not_called()
        self.assertEqual(list(self.base.iterdir()), [])

    def test_invalid_budgets_precede_database_stat_and_book_traversal(self):
        invalid = (True, False, -1, 0.0, 1.0, 1.5, float("nan"),
                   float("inf"), float("-inf"), "2", "abc", [], {}, ())
        for count in (1, 2):
            for enabled in (False, True):
                for option in ("max_pairs", "max_file_records", "max_total_file_bytes"):
                    values = invalid + (None, 0) if option == "max_pairs" else invalid
                    diagnostic = "--" + option.replace("_", "-") + " must be a " + (
                        "positive integer" if option == "max_pairs" else "nonnegative integer")
                    for value in values:
                        with self.subTest(count=count, enabled=enabled, option=option, value=value):
                            self.assert_preflight_error(
                                self.libraries()[:count], diagnostic,
                                verify_files=enabled, **{option: value})

    def test_explicit_verification_budgets_require_opt_in_before_library_work(self):
        for option in ("max_file_records", "max_total_file_bytes"):
            for value in (0, 1, 10 ** 100):
                with self.subTest(option=option, value=value):
                    self.assert_preflight_error(
                        self.libraries(), "--" + option.replace("_", "-") +
                        " requires --verify-files", **{option: value})

    def test_budget_diagnostic_precedence_matches_direct_verification(self):
        # Budget arguments precede even malformed library counts/labels.
        for libraries in ([], self.libraries()[1:]):
            with self.subTest(libraries=len(libraries)):
                with self.assertRaisesRegex(ReviewError, "^--max-pairs must be a positive integer$"):
                    compare.make_plan(libraries, max_pairs=True, max_file_records=True,
                                      max_total_file_bytes=True)
        cases = (
            ({"max_file_records": True, "max_total_file_bytes": True},
             "--max-file-records must be a nonnegative integer"),
            ({"max_file_records": 0, "max_total_file_bytes": True},
             "--max-file-records requires --verify-files"),
            ({"max_total_file_bytes": True},
             "--max-total-file-bytes must be a nonnegative integer"),
            ({"max_total_file_bytes": 0},
             "--max-total-file-bytes requires --verify-files"),
        )
        for kwargs, diagnostic in cases:
            with self.subTest(kwargs=kwargs):
                self.assert_preflight_error(self.libraries(), diagnostic, **kwargs)
                with self.assertRaisesRegex(ReviewError, "^" + diagnostic + "$"):
                    files.verify_libraries([], enabled=False, **kwargs)

    def test_valid_budgets_preserve_library_and_same_database_diagnostics(self):
        for kwargs in ({}, {"max_pairs": 10 ** 100},
                       {"verify_files": True, "max_total_file_bytes": 0, "max_file_records": 0}):
            for libraries in ([], self.libraries()[1:], self.libraries() + self.libraries()[:1]):
                with self.subTest(kwargs=kwargs, count=len(libraries)):
                    with self.assertRaisesRegex(ReviewError, "provide one or two libraries with labels A and B"):
                        compare.make_plan(libraries, **kwargs)
            libraries = self.libraries()
            database = self.base / "metadata.db"
            database.touch()
            for lib in libraries:
                lib.database = database
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(ReviewError, "the two libraries refer to the same database"):
                    compare.make_plan(libraries, **kwargs)
                for lib in libraries:
                    lib.books.__iter__.assert_not_called()


if __name__ == "__main__":
    unittest.main()
