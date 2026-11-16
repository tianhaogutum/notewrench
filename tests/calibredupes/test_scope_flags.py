from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

from notewrench.calibredupes import compare
from notewrench.calibredupes.reader import Book, ReviewError, read_library
from .helpers import create_library


class TruthinessTripwire:
    def __bool__(self):
        raise AssertionError("scope truthiness evaluated")


class ScopeFlagTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.libraries = [read_library(create_library(self.base / label, [
            {"title": "Shared title", "authors": ["Writer"],
             "identifiers": {"isbn": "0306406152"}, "formats": []}
            for _ in range(2)]), label) for label in ("A", "B")]

    def snapshot(self):
        return {str(p.relative_to(self.base)): p.read_bytes() if p.is_file() else None
                for p in self.base.rglob("*")}

    def test_nonboolean_scope_rejected_before_book_and_candidate_work(self):
        values = (None, 0, 1, -1, 0.0, 1.0, float("nan"), float("inf"),
                  "", "false", "true", b"false", [], [False], (), (False,),
                  {}, {"cross_library": False}, set(), {False}, TruthinessTripwire())
        before = self.snapshot()
        for books in ([], self.libraries[0].books):
            for value in values:
                for budgets in ({}, {"max_pairs": 1}, {"max_pairs": 100_000},
                                {"max_pairs": 10 ** 100}):
                    with self.subTest(populated=bool(books), value=value, budgets=budgets), \
                            ExitStack() as stack:
                        properties = [stack.enter_context(patch.object(
                            Book, name, new_callable=PropertyMock,
                            side_effect=AssertionError(name + " accessed")))
                            for name in ("title_key", "isbns")]
                        mocks = [stack.enter_context(patch.object(
                            compare, name, side_effect=AssertionError(name + " reached")))
                            for name in ("combinations", "product", "pair_evidence")]
                        with self.assertRaisesRegex(
                                ReviewError, "^cross_library must be a boolean$"):
                            compare.find_pairs(books, cross_library=value, **budgets)
                        for mock in properties + mocks:
                            mock.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_preflight_precedence_and_no_traversal_or_index_allocation(self):
        books = MagicMock()
        books.__iter__.side_effect = AssertionError("books traversed")
        books.__len__.side_effect = AssertionError("book count accessed")
        with patch.object(compare, "defaultdict", side_effect=AssertionError("index allocated")) as index:
            for scope in (False, True, None, TruthinessTripwire()):
                for limit in (None, True, False, 0, -1, 1.0, float("nan"), "1", [], {}):
                    with self.subTest(scope=scope, limit=limit), self.assertRaisesRegex(
                            ReviewError, "^--max-pairs must be a positive integer$"):
                        compare.find_pairs(books, cross_library=scope, max_pairs=limit)
            for budgets in ({}, {"max_pairs": 1}):
                with self.subTest(budgets=budgets), self.assertRaisesRegex(
                        ReviewError, "^cross_library must be a boolean$"):
                    compare.find_pairs(books, cross_library=TruthinessTripwire(), **budgets)
            index.assert_not_called()
        books.__iter__.assert_not_called()
        books.__len__.assert_not_called()

    def test_exact_booleans_preserve_scopes_plans_and_pair_budgets(self):
        a, b = self.libraries
        before = self.snapshot()
        for books, scope, expected in (([], False, 0), ([], True, 0),
                                       (a.books, False, 1), (a.books, True, 0),
                                       (a.books + b.books, False, 6),
                                       (a.books + b.books, True, 4)):
            baseline = compare.find_pairs(books, cross_library=scope)
            self.assertEqual((len(baseline[0]), baseline[1]), (expected, expected))
            for limit in (max(1, expected), 100_000, 10 ** 100):
                with self.subTest(scope=scope, expected=expected, limit=limit):
                    self.assertEqual(compare.find_pairs(books, cross_library=scope,
                                                        max_pairs=limit), baseline)
            if scope:
                self.assertTrue(all(e["a"].startswith("A:") and e["b"].startswith("B:")
                                    for e in baseline[0]))
            if expected > 1:
                with patch.object(compare, "pair_evidence") as evidence:
                    with self.assertRaisesRegex(ReviewError, "pair budget exceeded"):
                        compare.find_pairs(books, cross_library=scope, max_pairs=expected - 1)
                    evidence.assert_not_called()
        for libraries, scope, expected in (([a], "within-library", 1),
                                            ([a, b], "cross-library", 4)):
            plan = compare.make_plan(libraries)
            self.assertEqual(plan["scope"], scope)
            self.assertEqual((plan["summary"]["examinedPairs"], plan["summary"]["matchedPairs"]),
                             (expected, expected))
            self.assertEqual(plan, compare.make_plan(libraries, max_pairs=expected))
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
