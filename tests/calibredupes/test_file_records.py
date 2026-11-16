from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from notewrench.calibredupes import files
from notewrench.calibredupes.cli import main
from notewrench.calibredupes.compare import make_plan
from notewrench.calibredupes.reader import ReviewError, read_library
from .helpers import create_library

ROOT = Path(__file__).resolve().parents[2]


class FileRecordBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def library(self, name, contents):
        db = create_library(self.base / name, [
            {"title": f"Unmatched {name} {i}", "authors": ["Writer"],
             "path": f"{name}-book-{i}", "formats": ["EPUB"]}
            for i in range(1, len(contents) + 1)])
        paths = []
        for i, content in enumerate(contents, 1):
            path = db.parent / f"{name}-book-{i}/synthetic-{i}.epub"
            path.parent.mkdir()
            if content is not None:
                path.write_bytes(content)
            paths.append(path)
        return db, paths

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(map(str, args)))
        return code, out.getvalue(), err.getvalue()

    def snapshot(self):
        return {str(p.relative_to(self.base)): p.read_bytes() if p.is_file() else None
                for p in self.base.rglob("*")}

    def test_empty_and_missing_cross_library_cap_stops_before_next_path(self):
        for kind, content in (("empty", b""), ("missing", None)):
            with self.subTest(kind=kind):
                a, _ = self.library(f"{kind}-a", [content, content])
                b, _ = self.library(f"{kind}-b", [content, content])
                before = self.snapshot()
                for output in (None, self.base / "must-not-exist.json"):
                    with self.subTest(output=output):
                        original_open, original_stat = os.open, os.stat
                        with patch.object(files, "_inspect", wraps=files._inspect) as inspect, \
                                patch("os.open", wraps=original_open) as opened, \
                                patch("os.stat", wraps=original_stat) as stated, \
                                patch.object(os, "supports_dir_fd", os.supports_dir_fd | {opened}), \
                                patch("os.read", side_effect=AssertionError("zero byte budget read payload")):
                            args = [a, b, "--verify-files", "--max-file-records", 3,
                                    "--max-total-file-bytes", 0]
                            if output is not None:
                                args.extend(["-o", output])
                            code, out, err = self.call(*args)
                        self.assertEqual((code, out), (2, ""))
                        self.assertIn("--max-file-records limit 3", err)
                        self.assertIn("B:2/EPUB", err)
                        self.assertIn("3 declarations already inspected", err)
                        self.assertEqual([(c.args[1].key, c.args[2]["format"])
                                          for c in inspect.call_args_list],
                                         [("A:1", "EPUB"), ("A:2", "EPUB"), ("B:1", "EPUB")])
                        # Root validation is allowed, but N+1's directory/filename
                        # must not reach even an open/stat attempt.
                        for call in [*opened.call_args_list, *stated.call_args_list]:
                            self.assertNotIn(f"{kind}-b-book-2", str(call.args[0]))
                        self.assertEqual(self.snapshot(), before)

    def test_exact_fit_empty_and_missing_records_with_zero_byte_budget(self):
        for kind, content, status in (("empty", b"", "verified"), ("missing", None, "missing")):
            with self.subTest(kind=kind):
                a, _ = self.library(f"{kind}-a", [content, content])
                b, _ = self.library(f"{kind}-b", [content, content])
                before = self.snapshot()
                with patch("os.read", side_effect=AssertionError("empty/missing payload read")):
                    code, out, err = self.call(a, b, "--verify-files", "--max-file-records", 4,
                                               "--max-total-file-bytes", 0)
                self.assertEqual((code, err), (0, ""))
                plan = json.loads(out)
                self.assertEqual(plan["clusters"], [])  # Unmatched books count too.
                verification = plan["fileVerification"]
                self.assertEqual(verification["maxFileRecords"], 4)
                self.assertEqual(verification["fileRecordsInspected"], 4)
                self.assertEqual(verification["totalBytesRead"], 0)
                self.assertEqual(verification["maxTotalFileBytes"], 0)
                self.assertEqual(verification["summary"], {status: 4})
                self.assertEqual([r["ref"] for r in verification["records"]],
                                 ["A:1", "A:2", "B:1", "B:2"])
                self.assertEqual(self.snapshot(), before)

    def test_zero_limit_never_inspects_first_declaration(self):
        a, _ = self.library("a", [None])
        before = self.snapshot()
        with patch.object(files, "_inspect", side_effect=AssertionError("first path inspected")):
            code, out, err = self.call(a, "--verify-files", "--max-file-records", 0,
                                       "-o", self.base / "must-not-exist.json")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("A:1/EPUB", err)
        self.assertIn("limit 0", err)
        self.assertEqual(self.snapshot(), before)

    def test_zero_limit_succeeds_with_no_declarations(self):
        a, _ = self.library("a", [])
        b, _ = self.library("b", [])
        with patch.object(files, "_inspect", side_effect=AssertionError("no declaration")):
            code, out, err = self.call(a, b, "--verify-files", "--max-file-records", 0)
        self.assertEqual((code, err), (0, ""))
        verification = json.loads(out)["fileVerification"]
        self.assertEqual(verification["maxFileRecords"], 0)
        self.assertEqual(verification["fileRecordsInspected"], 0)
        self.assertEqual(verification["records"], [])

    def test_rejected_declarations_consume_records_before_later_library(self):
        a = create_library(self.base / "a", [
            {"title": "Unsafe", "path": "../escape", "formats": ["EPUB"]},
            {"title": "Blocked", "path": "blocked", "formats": ["EPUB"]}])
        (a.parent / "blocked").symlink_to(self.base, target_is_directory=True)
        b, _ = self.library("b", [None, b""])
        # Avoid following the fixture symlink while taking the input snapshot.
        before = {p: p.read_bytes() for p in (a, b)}
        code, out, err = self.call(a, b, "--verify-files", "--max-file-records", 4,
                                   "--max-total-file-bytes", 0)
        self.assertEqual((code, err), (0, ""))
        verification = json.loads(out)["fileVerification"]
        self.assertEqual(verification["fileRecordsInspected"], 4)
        self.assertEqual([r["status"] for r in verification["records"]],
                         ["unsafe-path", "blocked-path", "missing", "verified"])
        with patch.object(files, "_inspect", wraps=files._inspect) as inspect:
            code, out, err = self.call(a, b, "--verify-files", "--max-file-records", 2,
                                       "--max-total-file-bytes", 0)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("B:1/EPUB", err)
        self.assertEqual(inspect.call_count, 2)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual(os.readlink(a.parent / "blocked"), str(self.base))

    def test_count_is_per_format_declaration_not_book_or_unique_path(self):
        a, paths = self.library("a", [b"", b""])
        conn = sqlite3.connect(a)
        try:
            conn.execute("INSERT INTO data(book,format,name,uncompressed_size) VALUES(1,'PDF','synthetic-1',0)")
            conn.execute("UPDATE books SET path='a-book-1' WHERE id=2")
            conn.execute("UPDATE data SET name='synthetic-1' WHERE book=2")
            conn.commit()
        finally:
            conn.close()
        paths[0].with_suffix(".pdf").write_bytes(b"")
        b, _ = self.library("b", [None])
        libraries = [read_library(a, "A"), read_library(b, "B")]
        with patch.object(files, "_inspect", wraps=files._inspect) as inspect:
            with self.assertRaisesRegex(ReviewError, "A:1/PDF"):
                make_plan(libraries, verify_files=True, max_file_records=1)
        self.assertEqual(inspect.call_count, 1)
        plan = make_plan(libraries, verify_files=True, max_file_records=4, max_total_file_bytes=0)
        verification = plan["fileVerification"]
        self.assertEqual(verification["fileRecordsInspected"], 4)
        self.assertEqual([(r["ref"], r["format"]) for r in verification["records"]],
                         [("A:1", "EPUB"), ("A:1", "PDF"), ("A:2", "EPUB"), ("B:1", "EPUB")])
        self.assertEqual(verification["records"][0]["relativePath"],
                         verification["records"][2]["relativePath"])

    def test_omitted_limit_is_unlimited_and_reports_count(self):
        a, _ = self.library("a", [None, b""])
        b, _ = self.library("b", [b"payload"])
        code, out, err = self.call(a, b, "--verify-files")
        self.assertEqual((code, err), (0, ""))
        verification = json.loads(out)["fileVerification"]
        self.assertIsNone(verification["maxFileRecords"])
        self.assertEqual(verification["fileRecordsInspected"], 3)
        self.assertEqual(verification["totalBytesRead"], 7)

    def test_record_and_byte_budgets_are_independent(self):
        a, _ = self.library("a", [b"", b"payload"])
        for count, diagnostic in ((1, "--max-file-records"), (2, "--max-total-file-bytes")):
            with self.subTest(count=count), patch("os.read", side_effect=AssertionError("payload read")):
                code, out, err = self.call(a, "--verify-files", "--max-file-records", count,
                                           "--max-total-file-bytes", 0)
                self.assertEqual((code, out), (2, ""))
                self.assertIn(diagnostic, err)
                self.assertIn("A:2/EPUB", err)

    def test_invalid_cli_counts_and_missing_opt_in_leave_no_output(self):
        a, _ = self.library("a", [None])
        before = self.snapshot()
        for flags, diagnostic in ((["--verify-files", "--max-file-records", "-1"], "nonnegative"),
                                  (["--verify-files", "--max-file-records", "1.5"], "integer"),
                                  (["--verify-files", "--max-file-records", "abc"], "integer"),
                                  (["--verify-files", "--max-file-records"], "expected one argument"),
                                  (["--max-file-records", "0"], "requires --verify-files")):
            with self.subTest(flags=flags):
                result = subprocess.run([sys.executable, "-m", "notewrench", "calibre-dupes", str(a),
                                         *flags, "-o", str(self.base / "must-not-exist.json")],
                                        cwd=ROOT, capture_output=True, text=True, timeout=120)
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                self.assertIn(diagnostic, result.stderr)
                self.assertEqual(self.snapshot(), before)

    def test_api_rejects_invalid_counts_and_missing_opt_in_before_inspection(self):
        with patch.object(files, "_root_descriptor", side_effect=AssertionError("opened root")):
            for value in (-1, 1.5, "2", True, False):
                with self.subTest(value=value), self.assertRaisesRegex(ReviewError, "nonnegative integer"):
                    files.verify_libraries([], enabled=True, max_file_records=value)
            with self.assertRaisesRegex(ReviewError, "requires --verify-files"):
                files.verify_libraries([], enabled=False, max_file_records=0)


if __name__ == "__main__":
    unittest.main()
