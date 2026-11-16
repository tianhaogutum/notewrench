from contextlib import ExitStack, chdir, redirect_stderr, redirect_stdout
import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from notewrench.calibredupes import cli, files, reader
from notewrench.calibredupes.compare import make_plan
from notewrench.calibredupes.reader import ReviewError, locate_library, read_library
from .helpers import create_library


DIAGNOSTIC = "library path must be a string or a path-like object returning a string"
NUL_DIAGNOSTIC = "library path must not contain NUL characters"
MISSING_HOME = "~__notewrench_missing_user__/metadata.db"
NUL_TEXT = ("\0", "nul\0parent/metadata.db", "parent/meta\0data.db",
            "parent/metadata.db\0", "nul\0directory", "~/nul\0parent/metadata.db",
            "/nul\0parent/metadata.db")


class PathLike(os.PathLike):
    def __init__(self, value):
        self.value = value

    def __fspath__(self):
        return self.value


class StringSubclass(str):
    pass


class TextPathLikeTripwire(PathLike):
    def __init__(self, value):
        super().__init__(value)
        self.conversions = 0

    def __fspath__(self):
        self.conversions += 1
        if self.conversions > 1:
            raise AssertionError("path-like converted more than once")
        return self.value

    def __str__(self):
        raise AssertionError("path-like stringified instead of validated path")

    def __repr__(self):
        raise AssertionError("path-like repr evaluated instead of validated path")


class StringificationTripwire:
    def __str__(self):
        raise AssertionError("unsupported path stringified")

    def __repr__(self):
        raise AssertionError("unsupported path repr evaluated")

    def __bool__(self):
        raise AssertionError("unsupported path truthiness evaluated")


class PathTripwire:
    def __fspath__(self):
        raise AssertionError("path converted before timeout rejection")


INVALID = (None, True, False, 0, 1, -1, 1.0, float("nan"), float("inf"), 1j,
           b"", b"metadata.db", b"nul\0path", bytearray(b"metadata.db"), memoryview(b"metadata.db"),
           [], ["metadata.db"], (), ("metadata.db",), {}, {"path": "metadata.db"},
           set(), {"metadata.db"}, object(), StringificationTripwire())
INVALID_RETURNS = (None, True, 1, 1.0, b"metadata.db", b"nul\0path", [], {}, Path("metadata.db"))


def snapshot(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            if p.is_file() else None for p in root.rglob("*")}


class LibraryPathTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)

    def test_nul_paths_fail_before_expansion_filesystem_or_database_work(self):
        targets = ((Path, "expanduser"), (Path, "absolute"), (Path, "resolve"),
                   (Path, "is_dir"), (Path, "is_file"), (Path, "stat"),
                   (Path, "lstat"), (Path, "exists"), (Path, "open"),
                   (os, "getcwd"), (os, "stat"), (os, "lstat"),
                   (os, "readlink"), (os, "open"),
                   (reader, "_clean_database"), (reader.sqlite3, "connect"),
                   (reader, "_validate_schema"), (reader, "_rows"),
                   (reader, "_read_optional_metadata"))
        create_library(self.base / "good", [])
        before = snapshot(self.base)
        for api in (locate_library, read_library):
            for raw in NUL_TEXT:
                for value in (raw, Path(raw), StringSubclass(raw), PathLike(raw),
                              PathLike(StringSubclass(raw))):
                    with self.subTest(api=api.__name__, raw=raw,
                                      value_type=type(value).__name__), ExitStack() as stack:
                        mocks = [stack.enter_context(patch.object(
                            owner, name, side_effect=AssertionError(f"{name} reached")))
                            for owner, name in targets]
                        with self.assertRaises(ReviewError) as caught:
                            api(value)
                        self.assertEqual(str(caught.exception), NUL_DIAGNOSTIC)
                        for mocked in mocks:
                            mocked.assert_not_called()
        self.assertEqual(snapshot(self.base), before)

    def test_timeout_precedes_nul_but_valid_timeouts_reach_nul_check(self):
        for raw in NUL_TEXT:
            for value in (raw, Path(raw), PathLike(raw)):
                for timeout in (None, True, "1", -1, 31, float("nan")):
                    with self.subTest(raw=raw, timeout=timeout):
                        with patch.object(reader, "locate_library") as locate:
                            with self.assertRaisesRegex(ReviewError, "^timeout must be"):
                                read_library(value, timeout=timeout)
                        locate.assert_not_called()
                for timeout in (0, 0.0, 0.125, 30, 30.0):
                    with self.subTest(raw=raw, timeout=timeout):
                        with self.assertRaises(ReviewError) as caught:
                            read_library(value, timeout=timeout)
                        self.assertEqual(str(caught.exception), NUL_DIAGNOSTIC)

    def test_unsupported_paths_fail_before_filesystem_or_database_work(self):
        targets = ((Path, "expanduser"), (Path, "absolute"), (Path, "resolve"),
                   (Path, "stat"), (Path, "exists"), (Path, "open"),
                   (reader, "_clean_database"), (reader.sqlite3, "connect"),
                   (reader, "_validate_schema"), (reader, "_rows"),
                   (reader, "_read_optional_metadata"))
        values = INVALID + tuple(PathLike(value) for value in INVALID_RETURNS)
        for api in (locate_library, read_library):
            for index, value in enumerate(values):
                with self.subTest(api=api.__name__, index=index), ExitStack() as stack:
                    mocks = [stack.enter_context(patch.object(
                        owner, name, side_effect=AssertionError(f"{name} reached")))
                        for owner, name in targets]
                    with self.assertRaises(ReviewError) as caught:
                        api(value)
                    self.assertEqual(str(caught.exception), DIAGNOSTIC)
                    self.assertIsInstance(caught.exception.__cause__, TypeError)
                    for mocked in mocks:
                        mocked.assert_not_called()

    def test_invalid_timeout_precedes_invalid_path_and_path_protocol(self):
        for path in INVALID + (PathLike(None), PathTripwire()):
            for timeout in (None, True, "1", -1, 31, float("nan")):
                with self.subTest(path_type=type(path).__name__, timeout=timeout):
                    with patch.object(reader, "locate_library") as locate:
                        with self.assertRaisesRegex(ReviewError, "^timeout must be"):
                            read_library(path, timeout=timeout)
                    locate.assert_not_called()
        for timeout in (0, 0.0, 0.125, 30, 30.0):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ReviewError) as caught:
                    read_library(None, timeout=timeout)
                self.assertEqual(str(caught.exception), DIAGNOSTIC)

    def test_text_path_types_preserve_directory_database_and_plan_behavior(self):
        record = {"title": "Synthetic", "authors": ["Writer"], "formats": ["EPUB"]}
        db = create_library(self.base / "库 with spaces", [record, record])
        before = snapshot(self.base)
        expected = read_library(db)
        plan = make_plan([expected])
        for path in (db, db.parent):
            for value in (path, str(path), StringSubclass(str(path)), PathLike(str(path)),
                          PathLike(StringSubclass(str(path)))):
                with self.subTest(path=path.name, value_type=type(value).__name__):
                    self.assertEqual(locate_library(value), (db.parent, db))
                    self.assertEqual(read_library(value), expected)
                    self.assertEqual(make_plan([read_library(value)]), plan)
        with patch.dict(os.environ, {"HOME": str(self.base)}):
            for value in ("~/库 with spaces", Path("~/库 with spaces/metadata.db"),
                          PathLike("~/库 with spaces")):
                self.assertEqual(read_library(value), expected)
        with chdir(self.base):
            for value in ("库 with spaces", Path("库 with spaces/metadata.db"),
                          PathLike("./库 with spaces/metadata.db")):
                self.assertEqual(locate_library(value), (db.parent, db))
                self.assertEqual(read_library(value), expected)
        self.assertEqual(snapshot(self.base), before)

    def test_valid_text_paths_keep_filesystem_and_database_diagnostics(self):
        empty = self.base / "empty"
        empty.mkdir()
        corrupt = self.base / "corrupt" / "metadata.db"
        corrupt.parent.mkdir()
        corrupt.write_bytes(b"not SQLite")
        journal = create_library(self.base / "journal", [])
        Path(str(journal) + "-journal").write_bytes(b"synthetic sidecar")
        before = snapshot(self.base)
        cases = ((self.base / "missing", "input must be a library directory"),
                 (self.base / "other.db", "file named metadata.db"),
                 (empty, "cannot locate library"),
                 (self.base / "absent/metadata.db", "cannot locate library"))
        for api in (locate_library, read_library):
            for path, diagnostic in cases:
                with self.subTest(api=api.__name__, path=path.name):
                    with self.assertRaisesRegex(ReviewError, diagnostic):
                        api(PathLike(str(path)))
        for path, diagnostic in ((corrupt, "not a SQLite"), (journal, "WAL/SHM/journal")):
            with self.assertRaisesRegex(ReviewError, diagnostic):
                read_library(PathLike(str(path)))
        with patch.object(Path, "resolve", side_effect=PermissionError("synthetic denied")):
            with self.assertRaisesRegex(ReviewError, "cannot locate library.*synthetic denied"):
                locate_library(journal)
        self.assertEqual(snapshot(self.base), before)

    def test_cli_bad_text_paths_exit_two_without_stdout_or_report(self):
        create_library(self.base / "good", [])
        before = snapshot(self.base)
        for path in (self.base / "missing", self.base / "absent/metadata.db", self.base / "other.db"):
            for to_file in (False, True):
                with self.subTest(path=path.name, to_file=to_file):
                    output = self.base / "must-not-exist.json"
                    args = [str(path)] + (["-o", str(output)] if to_file else [])
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        code = cli.main(args)
                    self.assertEqual((code, stdout.getvalue()), (2, ""))
                    self.assertTrue(stderr.getvalue().startswith("notewrench calibre-dupes: "))
                    self.assertFalse(output.exists())
        self.assertEqual(snapshot(self.base), before)

    def test_in_process_cli_nul_paths_exit_two_without_stdout_or_report(self):
        # Python argv lists can contain NUL; OS subprocess argv cannot.
        create_library(self.base / "good", [])
        before = snapshot(self.base)
        for path in NUL_TEXT:
            for to_file in (False, True):
                with self.subTest(path=path, to_file=to_file):
                    output = self.base / "must-not-exist.json"
                    args = [path] + (["-o", str(output)] if to_file else [])
                    stdout, stderr = io.StringIO(), io.StringIO()
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        code = cli.main(args)
                    self.assertEqual((code, stdout.getvalue()), (2, ""))
                    self.assertEqual(stderr.getvalue(), f"notewrench calibre-dupes: {NUL_DIAGNOSTIC}\n")
                    self.assertFalse(output.exists())
        self.assertEqual(snapshot(self.base), before)


class ReaderPathDiagnosticTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.db = create_library(self.base / "库 with spaces", [])

    def test_location_errors_use_validated_text_and_keep_original_cause(self):
        before = snapshot(self.base)
        cases = (("expanduser", RuntimeError("controlled missing home")),
                 ("absolute", OSError("controlled filesystem failure")),
                 ("resolve", PermissionError("controlled denied")),
                 ("is_file", OSError("controlled file check failure")))
        for api in (locate_library, read_library):
            for operation, error in cases:
                raw = "~/库 with spaces/metadata.db" if operation == "expanduser" else str(self.db)
                value = TextPathLikeTripwire(raw)
                with self.subTest(api=api.__name__, operation=operation), ExitStack() as stack:
                    failed = stack.enter_context(patch.object(Path, operation, side_effect=error))
                    header = stack.enter_context(patch.object(
                        reader, "_clean_database", side_effect=AssertionError("header reached")))
                    connect = stack.enter_context(patch.object(
                        reader.sqlite3, "connect", side_effect=AssertionError("SQLite reached")))
                    with self.assertRaises(ReviewError) as caught:
                        api(value)
                    self.assertEqual(str(caught.exception), f"cannot locate library {raw}: {error}")
                    self.assertIs(caught.exception.__cause__, error)
                    self.assertEqual(value.conversions, 1)
                    self.assertTrue(failed.called)
                    header.assert_not_called()
                    connect.assert_not_called()
        self.assertEqual(snapshot(self.base), before)

    def test_real_missing_database_uses_supplied_text_without_stringification(self):
        before = snapshot(self.base)
        for api in (locate_library, read_library):
            # A relative path must remain relative in the diagnostic even after
            # absolute() succeeded; it must not become an object repr either.
            with self.subTest(api=api.__name__), chdir(self.base):
                raw = "absent 库/metadata.db"
                value = TextPathLikeTripwire(raw)
                with self.assertRaises(ReviewError) as caught:
                    api(value)
                self.assertTrue(str(caught.exception).startswith(f"cannot locate library {raw}: "))
                self.assertIsInstance(caught.exception.__cause__, FileNotFoundError)
                self.assertEqual(value.conversions, 1)
        self.assertEqual(snapshot(self.base), before)

    def test_preflight_precedes_location_and_does_not_format_pathlike(self):
        before = snapshot(self.base)
        for api in (locate_library, read_library):
            for raw, diagnostic in ((b"nul\0path", DIAGNOSTIC),
                                    ("~/nul\0path/metadata.db", NUL_DIAGNOSTIC)):
                value = TextPathLikeTripwire(raw)
                with self.subTest(api=api.__name__, diagnostic=diagnostic), patch.object(
                        Path, "expanduser", side_effect=AssertionError("expanded")) as expand:
                    with self.assertRaises(ReviewError) as caught:
                        api(value)
                    self.assertEqual(str(caught.exception), diagnostic)
                    self.assertEqual(value.conversions, 1)
                    expand.assert_not_called()
        value = TextPathLikeTripwire(str(self.db))
        with self.assertRaisesRegex(ReviewError, "^timeout must be"):
            read_library(value, timeout=True)
        self.assertEqual(value.conversions, 0)
        self.assertEqual(snapshot(self.base), before)

    def test_successful_reads_convert_pathlike_once_without_stringification(self):
        before = snapshot(self.base)
        expected = read_library(self.db)
        for path in (self.db, self.db.parent):
            with self.subTest(path=path.name):
                value = TextPathLikeTripwire(str(path))
                self.assertEqual(locate_library(value), (self.db.parent, self.db))
                self.assertEqual(value.conversions, 1)
                value = TextPathLikeTripwire(str(path))
                self.assertEqual(read_library(value), expected)
                self.assertEqual(value.conversions, 1)
        self.assertEqual(snapshot(self.base), before)


class VerificationInputPathTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.db = create_library(self.base / "库 with spaces", [])

    def preflight_tripwires(self, stack, *, expansion=True):
        targets = ((Path, "expanduser"), (Path, "absolute"), (Path, "resolve"),
                   (Path, "is_dir"), (Path, "is_file"), (Path, "stat"),
                   (Path, "lstat"), (Path, "exists"), (Path, "open"),
                   (os, "getcwd"), (os, "stat"), (os, "lstat"),
                   (os, "readlink"), (os, "open"),
                   (files, "_require_safe_open"), (files, "_root_descriptor"),
                   (cli, "locate_library"), (cli, "read_library"),
                   (reader, "_clean_database"), (reader.sqlite3, "connect"),
                   (reader, "_validate_schema"), (reader, "_rows"),
                   (reader, "_read_optional_metadata"))
        return [stack.enter_context(patch.object(
            owner, name, side_effect=AssertionError(f"{name} reached")))
            for owner, name in targets if expansion or (owner, name) != (Path, "expanduser")]

    def test_home_expansion_runtime_error_precedes_filesystem_and_descriptors(self):
        before = snapshot(self.base)
        for raw in ("~/library", "~/library/metadata.db", MISSING_HOME):
            for value in (raw, Path(raw), StringSubclass(raw), PathLike(raw),
                          TextPathLikeTripwire(raw)):
                with self.subTest(raw=raw, value_type=type(value).__name__), ExitStack() as stack:
                    mocks = self.preflight_tripwires(stack, expansion=False)
                    error = RuntimeError("controlled missing home")
                    expand = stack.enter_context(patch.object(Path, "expanduser", side_effect=error))
                    with self.assertRaises(ReviewError) as caught:
                        files.validate_verification_input(value)
                    self.assertEqual(str(caught.exception),
                                     f"cannot expand library path {raw}: controlled missing home")
                    self.assertIs(caught.exception.__cause__, error)
                    expand.assert_called_once_with()
                    for mocked in mocks:
                        mocked.assert_not_called()
        self.assertEqual(snapshot(self.base), before)

    @unittest.skipUnless(os.name == "posix", "unknown-user home lookup uses POSIX pwd")
    def test_unknown_user_home_paths_fail_before_filesystem_and_descriptors(self):
        import pwd
        with self.assertRaises(KeyError):
            pwd.getpwnam(MISSING_HOME[1:].split("/")[0])
        before = snapshot(self.base)
        for value in (MISSING_HOME, Path(MISSING_HOME), PathLike(MISSING_HOME),
                      TextPathLikeTripwire(MISSING_HOME)):
            with self.subTest(value_type=type(value).__name__), ExitStack() as stack:
                mocks = self.preflight_tripwires(stack, expansion=False)
                with self.assertRaises(ReviewError) as caught:
                    files.validate_verification_input(value)
                self.assertTrue(str(caught.exception).startswith(
                    f"cannot expand library path {MISSING_HOME}: "))
                self.assertIsInstance(caught.exception.__cause__, RuntimeError)
                for mocked in mocks:
                    mocked.assert_not_called()
        self.assertEqual(snapshot(self.base), before)

    def test_verified_cli_home_expansion_exit_two_before_library_work(self):
        before = snapshot(self.base)
        output = self.base / "must-not-exist.json"
        for to_file in (False, True):
            with self.subTest(to_file=to_file), ExitStack() as stack:
                prepared_parser = cli.parser()
                stack.enter_context(patch.object(cli, "parser", return_value=prepared_parser))
                mocks = self.preflight_tripwires(stack, expansion=False)
                stack.enter_context(patch.object(Path, "expanduser",
                                                side_effect=RuntimeError("controlled missing home")))
                stdout, stderr = io.StringIO(), io.StringIO()
                args = [MISSING_HOME, "--verify-files"] + (["-o", str(output)] if to_file else [])
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = cli.main(args)
                self.assertEqual((code, stdout.getvalue()), (2, ""))
                self.assertEqual(stderr.getvalue(),
                                 f"notewrench calibre-dupes: cannot expand library path {MISSING_HOME}: "
                                 "controlled missing home\n")
                for mocked in mocks:
                    mocked.assert_not_called()
            self.assertFalse(output.exists())
        self.assertEqual(snapshot(self.base), before)

    def test_reader_timeout_precedes_home_expansion(self):
        for value in (MISSING_HOME, Path(MISSING_HOME), PathLike(MISSING_HOME)):
            for timeout in (None, True, "1", -1, 31, float("nan")):
                with self.subTest(value_type=type(value).__name__, timeout=timeout), \
                        patch.object(Path, "expanduser", side_effect=AssertionError("expanded")) as expand:
                    with self.assertRaisesRegex(ReviewError, "^timeout must be"):
                        read_library(value, timeout=timeout)
                    expand.assert_not_called()

    def test_unsupported_helper_paths_fail_before_all_path_work(self):
        before = snapshot(self.base)
        for index, value in enumerate(INVALID + tuple(PathLike(v) for v in INVALID_RETURNS)):
            with self.subTest(index=index), ExitStack() as stack:
                mocks = self.preflight_tripwires(stack)
                with self.assertRaises(ReviewError) as caught:
                    files.validate_verification_input(value)
                self.assertEqual(str(caught.exception), DIAGNOSTIC)
                self.assertIsInstance(caught.exception.__cause__, TypeError)
                for mocked in mocks:
                    mocked.assert_not_called()
        self.assertEqual(snapshot(self.base), before)

    def test_nul_helper_paths_fail_before_all_path_work(self):
        before = snapshot(self.base)
        for raw in NUL_TEXT:
            for value in (raw, Path(raw), StringSubclass(raw), PathLike(raw),
                          PathLike(StringSubclass(raw))):
                with self.subTest(raw=raw, value_type=type(value).__name__), ExitStack() as stack:
                    mocks = self.preflight_tripwires(stack)
                    with self.assertRaises(ReviewError) as caught:
                        files.validate_verification_input(value)
                    self.assertEqual(str(caught.exception), NUL_DIAGNOSTIC)
                    for mocked in mocks:
                        mocked.assert_not_called()
        self.assertEqual(snapshot(self.base), before)

    def test_in_process_verified_cli_nul_before_locate_without_report(self):
        # In-process only: OS subprocess argv cannot represent NUL.
        before = snapshot(self.base)
        output = self.base / "must-not-exist.json"
        for raw in NUL_TEXT:
            for to_file in (False, True):
                with self.subTest(raw=raw, to_file=to_file), ExitStack() as stack:
                    # argparse/gettext may stat locale files while constructing
                    # the parser; tripwires cover library work after that setup.
                    prepared_parser = cli.parser()
                    stack.enter_context(patch.object(cli, "parser", return_value=prepared_parser))
                    mocks = self.preflight_tripwires(stack)
                    stdout, stderr = io.StringIO(), io.StringIO()
                    args = [raw, "--verify-files"] + (["-o", str(output)] if to_file else [])
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        code = cli.main(args)
                    self.assertEqual((code, stdout.getvalue()), (2, ""))
                    self.assertEqual(stderr.getvalue(), f"notewrench calibre-dupes: {NUL_DIAGNOSTIC}\n")
                    for mocked in mocks:
                        mocked.assert_not_called()
                self.assertFalse(output.exists())
        self.assertEqual(snapshot(self.base), before)

    def test_cli_argument_errors_precede_verification_path_preflight(self):
        before = snapshot(self.base)
        cases = ((["bad\0path", "--verify-files", "--timeout", "31"], "--timeout"),
                 (["bad\0path", "--verify-files", "--max-pairs", "0"], "--max-pairs"),
                 (["bad\0path", "--verify-files", "--max-file-records", "-1"], "--max-file-records"),
                 (["bad\0path", "--verify-files", "--max-total-file-bytes", "-1"], "--max-total-file-bytes"),
                 (["bad\0path", "--max-file-records", "0"], "requires --verify-files"),
                 (["bad\0path", "--max-total-file-bytes", "0"], "requires --verify-files"),
                 (["bad\0path", "a", "b", "--verify-files"], "provide one or two"))
        cases += tuple(([MISSING_HOME, *args[1:]], diagnostic) for args, diagnostic in cases)
        output = self.base / "must-not-exist.json"
        for args, diagnostic in cases:
            with self.subTest(args=args), patch.object(cli, "validate_verification_input") as validate:
                stdout, stderr = io.StringIO(), io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
                    cli.main(args + ["-o", str(output)])
                self.assertEqual(caught.exception.code, 2)
                self.assertEqual(stdout.getvalue(), "")
                self.assertIn(diagnostic, stderr.getvalue())
                validate.assert_not_called()
                self.assertFalse(output.exists())
        self.assertEqual(snapshot(self.base), before)

    def test_supported_helper_paths_convert_once_and_keep_no_follow_checks(self):
        before = snapshot(self.base)
        for path in (self.db, self.db.parent):
            for value in (path, str(path), StringSubclass(str(path)), PathLike(str(path)),
                          PathLike(StringSubclass(str(path)))):
                with self.subTest(path=path.name, value_type=type(value).__name__):
                    self.assertIsNone(files.validate_verification_input(value))
        with patch.dict(os.environ, {"HOME": str(self.base)}):
            for value in ("~/库 with spaces", Path("~/库 with spaces/metadata.db"),
                          PathLike("~/库 with spaces")):
                self.assertIsNone(files.validate_verification_input(value))
        with chdir(self.base):
            for value in ("库 with spaces", Path("库 with spaces/metadata.db"),
                          PathLike("./库 with spaces/metadata.db")):
                self.assertIsNone(files.validate_verification_input(value))
        with patch.object(PathLike, "__fspath__", autospec=True, return_value=str(self.db)) as convert:
            files.validate_verification_input(PathLike(str(self.db)))
            self.assertEqual(convert.call_count, 1)
        self.assertEqual(snapshot(self.base), before)
        alias = self.base / "alias"
        alias.symlink_to(self.db.parent, target_is_directory=True)
        for path in (alias, alias / "metadata.db"):
            with self.assertRaisesRegex(ReviewError, "without symlinks"):
                files.validate_verification_input(PathLike(str(path)))
        self.db.rename(self.db.with_name("original.db"))
        self.db.symlink_to("original.db")
        with self.assertRaisesRegex(ReviewError, "regular, non-symlink metadata.db"):
            files.validate_verification_input(self.db)
        with patch.object(files, "_require_safe_open", side_effect=ReviewError("requires POSIX")):
            with self.assertRaisesRegex(ReviewError, "requires POSIX"):
                files.validate_verification_input(self.db)


if __name__ == "__main__":
    unittest.main()
