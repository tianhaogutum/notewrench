from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, PropertyMock

from notewrench.calibredupes import compare
from notewrench.calibredupes.cli import main
from notewrench.calibredupes.reader import Book, ReviewError, read_library
from .helpers import create_library


class PairBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def library(self, name, count, author="Writer"):
        return read_library(create_library(self.base / name, [
            {"title": "Shared title", "authors": [author],
             "identifiers": {"isbn": "0306406152"}, "formats": []}
            for _ in range(count)]), "A" if name == "a" else "B")

    def snapshot(self):
        return {str(p.relative_to(self.base)): p.read_bytes() if p.is_file() else None
                for p in self.base.rglob("*")}

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = main(list(map(str, args)))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_invalid_api_values_rejected_before_pair_indexing(self):
        library = self.library("a", 1)
        before = self.snapshot()
        # Empty inputs must reject too; no bucket or overflow is needed.
        for populated in (False, True):
            library.books = library.books if populated else []
            if populated:
                library = read_library(library.database, "A")
            for api in ("find_pairs", "make_plan"):
                for value in (None, True, False, 0, -1, 0.0, 1.0, 1.5, -1.5,
                              float("nan"), float("inf"), float("-inf"),
                              "2", "abc", [], {}):
                    with self.subTest(api=api, populated=populated, value=value), \
                            patch.object(Book, "title_key", new_callable=PropertyMock,
                                         side_effect=AssertionError("pair indexing started")) as title, \
                            patch.object(compare, "pair_evidence") as evidence, \
                            patch.object(compare, "verify_libraries") as verify:
                        with self.assertRaisesRegex(
                                ReviewError, "^--max-pairs must be a positive integer$"):
                            if api == "find_pairs":
                                compare.find_pairs(library.books, cross_library=False,
                                                   max_pairs=value)
                            else:
                                compare.make_plan([library], max_pairs=value, verify_files=True)
                        title.assert_not_called()
                        evidence.assert_not_called()
                        verify.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_integer_exact_fit_deduplicates_blocks_and_preserves_scope(self):
        a, b = self.library("a", 3), self.library("b", 2)
        before = self.snapshot()
        for libraries, count in (([a], 3), ([a, b], 6)):
            books = [book for lib in libraries for book in lib.books]
            baseline = compare.make_plan(libraries)
            for limit in (count, count + 1, 100_000, 10 ** 100):
                with self.subTest(libraries=len(libraries), limit=limit):
                    edges, examined = compare.find_pairs(
                        books, cross_library=len(libraries) == 2, max_pairs=limit)
                    self.assertEqual((len(edges), examined), (count, count))
                    # Every pair shares both title and ISBN; charge it just once.
                    plan = compare.make_plan(libraries, max_pairs=limit)
                    self.assertEqual(plan, baseline)
                    self.assertEqual(plan["summary"]["examinedPairs"], count)
                    self.assertEqual(plan["summary"]["matchedPairs"], count)
                    if len(libraries) == 2:
                        self.assertTrue(all(e["a"].startswith("A:") and e["b"].startswith("B:")
                                            for e in edges))
            for api in ("find_pairs", "make_plan"):
                with self.subTest(api=api, count=count), \
                        patch.object(compare, "pair_evidence") as evidence, \
                        patch.object(compare, "verify_libraries") as verify:
                    with self.assertRaisesRegex(ReviewError, "pair budget exceeded"):
                        if api == "find_pairs":
                            compare.find_pairs(books, cross_library=len(libraries) == 2,
                                               max_pairs=count - 1)
                        else:
                            compare.make_plan(libraries, max_pairs=count - 1, verify_files=True)
                    evidence.assert_not_called()
                    verify.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_budget_counts_candidates_even_when_no_pair_matches(self):
        a, b = self.library("a", 2), self.library("b", 2, author="Other")
        # Same title blocks all four cross-library pairs, but known different
        # authors without shared ISBN provide no match evidence.
        for book in a.books + b.books:
            book.identifiers = []
        plan = compare.make_plan([a, b], max_pairs=4)
        self.assertEqual((plan["summary"]["examinedPairs"], plan["summary"]["matchedPairs"]),
                         (4, 0))
        with self.assertRaisesRegex(ReviewError, "pair budget exceeded"):
            compare.make_plan([a, b], max_pairs=3)

    def test_cli_exact_fit_and_rejections_preserve_inputs_and_output(self):
        a, b = self.library("a", 2), self.library("b", 2)
        before = self.snapshot()
        code, out, err = self.call(a.database, b.database, "--max-pairs", 4)
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["summary"]["examinedPairs"], 4)
        for value, diagnostic in ((3, "pair budget exceeded"), (0, "positive"),
                                  (-1, "positive"), ("1.5", "--max-pairs"),
                                  ("True", "--max-pairs"), ("abc", "--max-pairs")):
            for output in (None, self.base / "must-not-exist.json"):
                with self.subTest(value=value, output=output):
                    args = [a.database, b.database, "--max-pairs", value]
                    if output is not None:
                        args.extend(["-o", output])
                    code, out, err = self.call(*args)
                    self.assertEqual((code, out), (2, ""))
                    self.assertIn(diagnostic, err)
                    self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
