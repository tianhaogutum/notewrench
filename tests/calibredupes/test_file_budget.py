from contextlib import redirect_stderr, redirect_stdout
import errno
import hashlib
import io
import json
import os
from pathlib import Path
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


class FileBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def library(self, name, contents):
        db = create_library(self.base / name, [
            {"title": f"Unmatched {name} {i}", "authors": ["Writer"], "formats": ["EPUB"]}
            for i in range(len(contents))])
        files = []
        for i, content in enumerate(contents, 1):
            file = db.parent / f"Synthetic/Book ({i})/synthetic-{i}.epub"
            file.parent.mkdir(parents=True)
            if content is not None:
                file.write_bytes(content)
            files.append(file)
        return db, files

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(map(str, args)))
        return code, out.getvalue(), err.getvalue()

    def snapshot(self):
        return {str(p.relative_to(self.base)): p.read_bytes() if p.is_file() else None
                for p in self.base.rglob("*")}

    def test_invalid_byte_api_values_rejected_before_verification_io(self):
        a, _ = self.library("a", [b"AAAA"])
        b, _ = self.library("b", [b"BBBB"])
        libraries = [read_library(a, "A"), read_library(b, "B")]
        before = self.snapshot()
        for api in ("verify_libraries", "make_plan"):
            for enabled in (True, False):
                for value in (-1, True, False, 0.0, 1.0, 1.5, -1.5, float("nan"),
                              float("inf"), float("-inf"), "2", "abc", [], {}):
                    with self.subTest(api=api, enabled=enabled, value=value):
                        with patch.object(files, "_root_descriptor") as root, \
                                patch.object(files, "_inspect") as inspect, \
                                patch("os.open") as opened, patch("os.read") as read:
                            try:
                                with self.assertRaisesRegex(
                                        ReviewError,
                                        "^--max-total-file-bytes must be a nonnegative integer$"):
                                    if api == "verify_libraries":
                                        files.verify_libraries(libraries, enabled=enabled,
                                                               max_total_file_bytes=value)
                                    else:
                                        make_plan(libraries, verify_files=enabled,
                                                  max_total_file_bytes=value)
                            finally:
                                root.assert_not_called()
                                inspect.assert_not_called()
                                opened.assert_not_called()
                                read.assert_not_called()
        self.assertEqual(self.snapshot(), before)

    def test_byte_api_requires_opt_in_before_verification_io(self):
        db, _ = self.library("a", [b"AAAA"])
        libraries = [read_library(db, "A")]
        for api in ("verify_libraries", "make_plan"):
            for value in (0, 4):
                with self.subTest(api=api, value=value), \
                        patch.object(files, "_root_descriptor") as root, \
                        patch.object(files, "_inspect") as inspect, \
                        patch("os.open") as opened, patch("os.read") as read:
                    with self.assertRaisesRegex(ReviewError, "requires --verify-files"):
                        if api == "verify_libraries":
                            files.verify_libraries(libraries, enabled=False,
                                                   max_total_file_bytes=value)
                        else:
                            make_plan(libraries, max_total_file_bytes=value)
                    root.assert_not_called()
                    inspect.assert_not_called()
                    opened.assert_not_called()
                    read.assert_not_called()
            # None is the only permitted disabled budget; keep the output shape.
            with self.subTest(api=api, value=None), \
                    patch.object(files, "_root_descriptor") as root:
                result = (files.verify_libraries(libraries, enabled=False)
                          if api == "verify_libraries" else make_plan(libraries)["fileVerification"])
                self.assertEqual(result, {"enabled": False, "evidenceLevel": "metadata-only", "records": []})
                root.assert_not_called()

    def test_valid_byte_api_budgets_preserve_values_counts_and_inputs(self):
        a, _ = self.library("a", [b"AAAA"])
        b, _ = self.library("b", [b"BBBB"])
        empty, _ = self.library("empty", [b"", None])
        # Use empty/missing declarations in both inputs for the zero budget.
        empty_b, _ = self.library("empty-b", [None, b""])
        full = [read_library(a, "A"), read_library(b, "B")]
        zero = [read_library(empty, "A"), read_library(empty_b, "B")]
        before = self.snapshot()
        for api in ("verify_libraries", "make_plan"):
            for libraries, limit, count, total in ((full, None, 2, 8), (full, 8, 2, 8),
                                                    (zero, 0, 4, 0)):
                with self.subTest(api=api, limit=limit):
                    if api == "verify_libraries":
                        result = files.verify_libraries(libraries, enabled=True,
                                                       max_total_file_bytes=limit,
                                                       max_file_records=count)
                    else:
                        result = make_plan(libraries, verify_files=True,
                                           max_total_file_bytes=limit,
                                           max_file_records=count)["fileVerification"]
                    self.assertEqual(result["maxTotalFileBytes"], limit)
                    if limit is not None:
                        self.assertIs(type(result["maxTotalFileBytes"]), int)
                    self.assertEqual(result["totalBytesRead"], total)
                    self.assertIs(type(result["totalBytesRead"]), int)
                    self.assertEqual((result["maxFileRecords"], result["fileRecordsInspected"]),
                                     (count, count))
                    self.assertEqual(len(result["records"]), count)
                    self.assertEqual(result["summary"], {"verified": 2} if total else
                                     {"missing": 2, "verified": 2})
        self.assertEqual(self.snapshot(), before)

    def test_cumulative_cross_library_overflow_has_no_partial_output(self):
        a, _ = self.library("a", [b"AAAA"])
        b, _ = self.library("b", [b"BBBB"])
        before = self.snapshot()
        original_read = os.read
        for output in (None, self.base / "must-not-exist.json"):
            with self.subTest(output=output):
                payloads = []

                def tracked_read(fd, size):
                    chunk = original_read(fd, size)
                    payloads.append(chunk)
                    return chunk

                args = [a, b, "--verify-files", "--max-total-file-bytes", "7"]
                if output is not None:
                    args.extend(["-o", output])
                with patch("os.read", side_effect=tracked_read):
                    code, out, err = self.call(*args)
                self.assertEqual((code, out), (2, ""))
                self.assertIn("--max-total-file-bytes", err)
                self.assertIn("B:1", err)
                self.assertIn("EPUB", err)
                self.assertEqual(b"".join(payloads), b"AAAA")
                self.assertEqual(self.snapshot(), before)

    def test_exact_fit_counts_short_reads_and_ignores_declared_sizes(self):
        a, _ = self.library("a", [b"AAAA"])
        b, _ = self.library("b", [b"BBBB"])
        original_read = os.read
        payloads = []

        def short_read(fd, size):
            chunk = original_read(fd, min(size, 2))
            payloads.append(chunk)
            return chunk

        with patch("os.read", side_effect=short_read):
            code, out, err = self.call(a, b, "--verify-files", "--max-total-file-bytes", "8")
        self.assertEqual((code, err), (0, ""))
        plan = json.loads(out)
        self.assertEqual(plan["clusters"], [])  # Unmatched records also consume budget.
        verification = plan["fileVerification"]
        self.assertEqual(verification["maxTotalFileBytes"], 8)
        self.assertEqual(verification["totalBytesRead"], 8)
        self.assertEqual(b"".join(payloads), b"AAAABBBB")
        self.assertTrue(all(r["status"] == "verified" and not r["declaredSizeMatches"]
                            for r in verification["records"]))

    def test_oversized_observed_file_is_rejected_before_payload_read(self):
        db, files = self.library("a", [b""])
        with files[0].open("r+b") as stream:
            stream.truncate(2 * 1024 * 1024 + 1)
        with patch("os.read", side_effect=AssertionError("over-budget file was read")):
            code, out, err = self.call(db, "--verify-files", "--max-total-file-bytes", 2 * 1024 * 1024)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("--max-total-file-bytes", err)
        self.assertIn("A:1", err)

    def test_zero_budget_allows_empty_missing_and_blocked_files(self):
        db, files = self.library("a", [b"", None, None])
        files[2].symlink_to(files[0])
        with patch("os.read", side_effect=AssertionError("zero budget read payload")):
            code, out, err = self.call(db, "--verify-files", "--max-total-file-bytes", "0")
        self.assertEqual((code, err), (0, ""))
        verification = json.loads(out)["fileVerification"]
        self.assertEqual(verification["maxTotalFileBytes"], 0)
        self.assertEqual(verification["totalBytesRead"], 0)
        self.assertEqual([r["status"] for r in verification["records"]],
                         ["verified", "missing", "blocked-path"])
        self.assertEqual(verification["records"][0]["sha256"], hashlib.sha256(b"").hexdigest())

    def test_zero_budget_rejects_nonempty_file_without_reading(self):
        db, _ = self.library("a", [b"x"])
        with patch("os.read", side_effect=AssertionError("zero budget read payload")):
            code, out, err = self.call(db, "--verify-files", "--max-total-file-bytes", "0")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("A:1", err)

    def test_failed_file_reads_still_consume_cumulative_budget(self):
        original_read = os.read
        for failure, limit, expected_bytes in (("changed", 7, 4), ("read-error", 5, 2)):
            with self.subTest(failure=failure):
                db, files = self.library(failure, [b"AAAA", b"BBBB"])
                payloads = []
                calls = 0

                def failing_read(fd, size):
                    nonlocal calls
                    calls += 1
                    if failure == "read-error" and calls == 2:
                        raise OSError(errno.EIO, "synthetic read error")
                    chunk = original_read(fd, min(size, 2) if failure == "read-error" else size)
                    payloads.append(chunk)
                    if failure == "changed" and calls == 1:
                        files[0].write_bytes(b"grew during read")
                    return chunk

                with patch("os.read", side_effect=failing_read):
                    code, out, err = self.call(db, "--verify-files", "--max-total-file-bytes", limit)
                self.assertEqual((code, out), (2, ""))
                self.assertIn("A:2", err)
                self.assertEqual(b"".join(payloads), b"A" * expected_bytes)

    def test_growth_at_exact_limit_is_unverified_without_extra_read(self):
        db, files = self.library("a", [b"AAAA"])
        original_read = os.read
        payloads = []

        def growing_read(fd, size):
            chunk = original_read(fd, size)
            payloads.append(chunk)
            files[0].write_bytes(b"AAAAextra")
            return chunk

        with patch("os.read", side_effect=growing_read):
            code, out, err = self.call(db, "--verify-files", "--max-total-file-bytes", "4")
        self.assertEqual((code, err), (0, ""))
        verification = json.loads(out)["fileVerification"]
        self.assertEqual(verification["totalBytesRead"], 4)
        self.assertEqual(b"".join(payloads), b"AAAA")
        self.assertEqual(verification["records"][0]["status"], "changed-during-read")
        self.assertNotIn("sha256", verification["records"][0])

    def test_omitted_budget_retains_unlimited_verification(self):
        db, _ = self.library("a", [b"AAAA"])
        code, out, err = self.call(db, "--verify-files")
        self.assertEqual((code, err), (0, ""))
        verification = json.loads(out)["fileVerification"]
        self.assertIsNone(verification["maxTotalFileBytes"])
        self.assertEqual(verification["totalBytesRead"], 4)

    def test_invalid_cli_budgets_and_missing_opt_in_leave_no_report(self):
        db, _ = self.library("a", [b"AAAA"])
        output = self.base / "must-not-exist.json"
        for flags, diagnostic in ((["--verify-files", "--max-total-file-bytes", "-1"], "nonnegative"),
                                  (["--verify-files", "--max-total-file-bytes", "1.5"], "integer"),
                                  (["--verify-files", "--max-total-file-bytes", "abc"], "integer"),
                                  (["--max-total-file-bytes", "4"], "requires --verify-files")):
            with self.subTest(flags=flags):
                result = subprocess.run([sys.executable, "-m", "notewrench", "calibre-dupes", str(db),
                                         *flags, "-o", str(output)],
                                        cwd=ROOT, capture_output=True, text=True, timeout=120)
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                self.assertIn(diagnostic, result.stderr)
                self.assertFalse(output.exists())

    def test_cli_outside_repository_budget_outside_worktree(self):
        a, _ = self.library("a", [b"AAAA"])
        b, _ = self.library("b", [b"BBBB"])
        command = [sys.executable, "-m", "notewrench", "calibre-dupes", str(a), str(b), "--verify-files",
                   "--max-total-file-bytes"]
        result = subprocess.run([*command, "8"], cwd=self.base, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["fileVerification"]["totalBytesRead"], 8)
        output = self.base / "must-not-exist.json"
        result = subprocess.run([*command, "7", "-o", str(output)],
                                cwd=self.base, capture_output=True, text=True, timeout=120)
        self.assertEqual((result.returncode, result.stdout), (2, ""))
        self.assertIn("B:1/EPUB", result.stderr)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
