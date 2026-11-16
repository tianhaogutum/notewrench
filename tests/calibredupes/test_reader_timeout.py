from contextlib import ExitStack, redirect_stderr, redirect_stdout
from decimal import Decimal
from fractions import Fraction
import hashlib
import io
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from notewrench.calibredupes import cli, reader
from notewrench.calibredupes.compare import make_plan
from notewrench.calibredupes.reader import ReviewError, read_library
from .helpers import create_library


DIAGNOSTIC = "timeout must be a finite int or float between 0 and 30 seconds (excluding bool)"


class CoercionTripwire:
    def __bool__(self):
        raise AssertionError("timeout truthiness evaluated")

    def __float__(self):
        raise AssertionError("timeout converted to float")

    def __index__(self):
        raise AssertionError("timeout converted to int")

    def __le__(self, other):
        raise AssertionError("invalid timeout compared")

    __ge__ = __le__


class IntSubclass(int):
    pass


class FloatSubclass(float):
    pass


class TypeEqualityTripwire(type):
    def __eq__(cls, other):
        raise AssertionError("timeout type compared with equality")


class NonNumeric(metaclass=TypeEqualityTripwire):
    pass


class PathTripwire:
    def __fspath__(self):
        raise AssertionError("input path coerced")


INVALID = (True, False, None, "", "1", b"1", [], [1], (), (1,), {}, {"timeout": 1},
           set(), {1}, object(), CoercionTripwire(), IntSubclass(1), FloatSubclass(1),
           Decimal("1"), Fraction(1, 2), 1+0j, -1, 31, 10**1000, -10**1000,
           -0.001, math.nextafter(30.0, math.inf), float("nan"), float("inf"), -float("inf"),
           NonNumeric())
VALID = (0, 0.0, -0.0, math.nextafter(0.0, 1.0), 0.25, 1, 1.0, 30, 30.0)


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            if p.is_file() else None for p in root.rglob("*")}


class ReaderTimeoutTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)

    def test_invalid_timeouts_fail_before_path_or_database_work(self):
        targets = ((reader, "locate_library"), (Path, "stat"), (Path, "exists"),
                   (Path, "open"), (reader, "_clean_database"), (reader.sqlite3, "connect"),
                   (reader, "_validate_schema"), (reader, "_rows"),
                   (reader, "_read_optional_metadata"))
        for index, timeout in enumerate(INVALID):
            with self.subTest(index=index), ExitStack() as stack:
                mocks = [stack.enter_context(patch.object(
                    owner, name, side_effect=AssertionError(f"{name} reached")))
                    for owner, name in targets]
                with self.assertRaises(ReviewError) as caught:
                    read_library(PathTripwire(), timeout=timeout)
                self.assertEqual(str(caught.exception), DIAGNOSTIC)
                for mocked in mocks:
                    mocked.assert_not_called()

    def test_invalid_timeout_precedes_missing_journal_header_and_schema_errors(self):
        good = create_library(self.base / "good", [])
        journal = create_library(self.base / "journal", [])
        Path(str(journal) + "-journal").write_bytes(b"synthetic sidecar")
        corrupt = self.base / "corrupt" / "metadata.db"
        corrupt.parent.mkdir()
        corrupt.write_bytes(b"not SQLite")
        schema = create_library(self.base / "schema", [])
        conn = sqlite3.connect(schema)
        try:
            conn.execute("DROP TABLE data")
            conn.commit()
        finally:
            conn.close()
        before = snapshot(self.base)
        for path in (good, self.base / "missing", journal, corrupt, schema):
            for index, timeout in enumerate(INVALID):
                with self.subTest(path=path.name, index=index):
                    with self.assertRaises(ReviewError) as caught:
                        read_library(path, timeout=timeout)
                    self.assertEqual(str(caught.exception), DIAGNOSTIC)
        self.assertEqual(snapshot(self.base), before)
        for timeout in (0, 0.25, 30):
            for path, diagnostic in ((self.base / "missing", "input must be a library directory"),
                                     (journal, "WAL/SHM/journal"),
                                     (corrupt, "not a SQLite"), (schema, "unsupported schema")):
                with self.subTest(timeout=timeout, diagnostic=diagnostic):
                    with self.assertRaisesRegex(ReviewError, diagnostic):
                        read_library(path, timeout=timeout)
        self.assertEqual(snapshot(self.base), before)

    def test_default_and_valid_timeouts_preserve_snapshots_and_plans(self):
        record = {"title": "Synthetic", "authors": ["Writer"], "formats": ["EPUB"]}
        a = create_library(self.base / "a", [record, record])
        b = create_library(self.base / "b", [record])
        before = snapshot(self.base)
        connect = sqlite3.connect
        with patch.object(reader.sqlite3, "connect", wraps=connect) as opened:
            default = read_library(a)
        self.assertEqual(opened.call_args.kwargs["timeout"], 1.0)
        self.assertIs(type(opened.call_args.kwargs["timeout"]), float)
        expected_within = make_plan([default])
        expected_cross = make_plan([default, read_library(b, "B")])
        for timeout in VALID:
            with self.subTest(timeout=timeout):
                with patch.object(reader.sqlite3, "connect", wraps=connect) as opened:
                    library = read_library(a, timeout=timeout)
                self.assertIs(opened.call_args.kwargs["timeout"], timeout)
                self.assertEqual(library, default)
                self.assertEqual(make_plan([library]), expected_within)
                self.assertEqual(make_plan([library, read_library(b, "B", timeout=timeout)]),
                                 expected_cross)
        self.assertEqual(snapshot(self.base), before)

    def test_cli_invalid_timeouts_exit_before_location_without_output(self):
        db = create_library(self.base / "a", [])
        before = snapshot(self.base)
        for value in ("-1", "31", "30.0001", "nan", "inf", "-inf", "1e999", "True", "None", "bad"):
            for path in (db, self.base / "missing"):
                for file_output in (False, True):
                    with self.subTest(value=value, missing=path != db, file=file_output):
                        output = self.base / "must-not-exist.json"
                        args = [str(path), f"--timeout={value}"]
                        if file_output:
                            args += ["-o", str(output)]
                        stdout, stderr = io.StringIO(), io.StringIO()
                        with redirect_stdout(stdout), redirect_stderr(stderr), patch.object(
                                cli, "locate_library", side_effect=AssertionError("location reached")) as locate:
                            with self.assertRaises(SystemExit) as caught:
                                cli.main(args)
                        self.assertEqual(caught.exception.code, 2)
                        self.assertEqual(stdout.getvalue(), "")
                        self.assertIn("--timeout", stderr.getvalue())
                        self.assertFalse(output.exists())
                        locate.assert_not_called()
        self.assertEqual(snapshot(self.base), before)


if __name__ == "__main__":
    unittest.main()
