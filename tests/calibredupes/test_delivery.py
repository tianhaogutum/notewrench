import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from .helpers import sample_libraries
from .helpers import benchmark

ROOT = Path(__file__).resolve().parents[2]


class DeliveryTests(unittest.TestCase):
    def test_labeled_benchmark_actual_sqlite_counts(self):
        bench = benchmark()
        report = bench.run(groups=2, repeats=2)
        self.assertEqual(report["booksPerLibrary"], 18)
        self.assertEqual(report["labeledPositivePairs"], 10)
        self.assertEqual(report["baselineRawTitleEquality"]["candidateCount"], 12)
        self.assertEqual(report["baselineRawTitleEquality"]["truePositives"], 8)
        self.assertEqual(report["isbnAndNormalizedAuthor"]["candidateCount"], 14)
        self.assertEqual(report["isbnAndNormalizedAuthor"]["truePositives"], 10)
        self.assertEqual(report["isbnAndNormalizedAuthor"]["falsePositives"], 4)
        self.assertEqual(report["isbnAndNormalizedAuthor"]["examinedPairs"], 16)
        self.assertEqual(report["strongEvidenceSubset"]["candidateCount"], 2)
        self.assertEqual(bench.fixture(2), bench.fixture(2))
        for method in ("baselineRawTitleEquality", "isbnAndNormalizedAuthor"):
            self.assertEqual(len(report[method]["seconds"]), 2)
            self.assertGreaterEqual(report[method]["medianSeconds"], 0)

    def test_cli_real_e2e_outside_repository(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            a, b = sample_libraries(base)
            output = base / "plan.json"
            result = subprocess.run([sys.executable, "-m", "notewrench", "calibre-dupes", str(a), str(b), "-o", str(output)],
                                    cwd=base, capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(plan["summary"]["strongPairs"], 1)
            self.assertEqual(plan["summary"]["candidatePairs"], 4)
            self.assertEqual(plan["summary"]["clusters"], 5)
            self.assertEqual(plan["clusters"][0]["formats"], {"A": ["EPUB"], "B": ["PDF"]})

    def test_cli_argument_errors_are_nonzero_without_json(self):
        for args in (("one", "two", "three"), ("one", "--max-pairs", "0"),
                     ("one", "--timeout", "nan"), ("one", "--timeout", "31"), ()):
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, "-m", "notewrench", "calibre-dupes", *args],
                                        cwd=ROOT, capture_output=True, text=True, timeout=120)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
