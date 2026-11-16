from contextlib import ExitStack, chdir, redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from notewrench.calibredupes import cli
from .helpers import verification_libraries

ROOT = Path(__file__).resolve().parents[2]
MISSING_HOME = "~__notewrench_missing_user__/review.json"


class OutputPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.a, self.b = verification_libraries(self.base)
        self.before = self.snapshot()

    def snapshot(self):
        return {str(p.relative_to(self.base)): (
            "directory" if p.is_dir() else hashlib.sha256(p.read_bytes()).hexdigest())
            for root in (self.a.parent, self.b.parent) for p in sorted(root.rglob("*"))}

    def tearDown(self):
        self.assertEqual(self.snapshot(), self.before)

    def call(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main([str(arg) for arg in args])
        return code, out.getvalue(), err.getvalue()

    def test_unknown_user_output_is_cli_error_in_both_modes(self):
        # This tests real NSS/home lookup, separately from the controlled error.
        with self.assertRaises(RuntimeError):
            Path(MISSING_HOME).expanduser()
        for flags in ([], ["--verify-files"]):
            for option in ("-o", "--output"):
                with self.subTest(flags=flags, option=option):
                    result = subprocess.run(
                        [sys.executable, "-m", "notewrench", "calibre-dupes", str(self.a),
                         *flags, option, MISSING_HOME], cwd=ROOT,
                        capture_output=True, text=True, timeout=120)
                    self.assertEqual((result.returncode, result.stdout), (2, ""), result.stderr)
                    self.assertTrue(result.stderr.startswith(
                        f"notewrench calibre-dupes: cannot expand output path {MISSING_HOME}: "))
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertFalse((ROOT / MISSING_HOME).exists())

    def test_controlled_output_expansion_fails_before_output_or_plan_work(self):
        for flags in ([], ["--verify-files"]):
            with self.subTest(flags=flags), ExitStack() as stack:
                locate = stack.enter_context(patch.object(
                    cli, "locate_library", return_value=(self.a.parent, self.a)))
                verify = stack.enter_context(patch.object(cli, "validate_verification_input"))
                expand = stack.enter_context(patch.object(
                    Path, "expanduser", side_effect=RuntimeError("controlled missing home")))
                guards = [stack.enter_context(patch.object(owner, name,
                          side_effect=AssertionError(f"unexpected {name}")))
                          for owner, name in ((Path, "resolve"), (Path, "exists"),
                                              (Path, "is_symlink"), (Path, "open"),
                                              (cli, "read_library"), (cli, "make_plan"))]
                code, out, err = self.call([self.a, *flags, "-o", MISSING_HOME])
                self.assertEqual((code, out, err), (2, "",
                    f"notewrench calibre-dupes: cannot expand output path {MISSING_HOME}: "
                    "controlled missing home\n"))
                locate.assert_called_once_with(str(self.a))
                if flags:
                    verify.assert_called_once_with(str(self.a))
                else:
                    verify.assert_not_called()
                expand.assert_called_once_with()
                for guard in guards:
                    guard.assert_not_called()

    def test_argument_checks_precede_output_expansion(self):
        cases = [([], "required"),
                 ([self.a, self.b, self.a], "provide one or two libraries"),
                 ([self.a, "--max-pairs", "0"], "must be positive"),
                 ([self.a, "--timeout", "nan"], "between 0 and 30"),
                 ([self.a, "--max-file-records", "-1"], "nonnegative integer"),
                 ([self.a, "--max-total-file-bytes", "-1"], "nonnegative integer"),
                 ([self.a, "--max-file-records", "0"], "requires --verify-files"),
                 ([self.a, "--max-total-file-bytes", "0"], "requires --verify-files")]
        for args, expected in cases:
            with self.subTest(args=args), \
                    patch.object(cli, "locate_library", side_effect=AssertionError("located")), \
                    patch.object(cli, "validate_verification_input", side_effect=AssertionError("verified")), \
                    patch.object(Path, "expanduser", side_effect=AssertionError("expanded")):
                out, err = io.StringIO(), io.StringIO()
                with redirect_stdout(out), redirect_stderr(err), self.assertRaises(SystemExit) as raised:
                    cli.main([str(arg) for arg in [*args, "-o", MISSING_HOME]])
                self.assertEqual(raised.exception.code, 2)
                self.assertEqual(out.getvalue(), "")
                self.assertIn(expected, err.getvalue())
                self.assertNotIn("cannot expand output", err.getvalue())

    def test_library_location_checks_precede_output_expansion(self):
        original = Path.expanduser

        def expand(path):
            if path == Path(MISSING_HOME):
                raise AssertionError("output expanded before input validation")
            return original(path)

        for flags in ([], ["--verify-files"]):
            for inputs, expected in (([self.base / "missing/metadata.db"], "cannot locate library"),
                                     ([self.a, self.base / "missing/metadata.db"], "cannot locate library"),
                                     ([self.base / "wrong-name"], "file named metadata.db"),
                                     ([self.a, self.a.parent], "same database")):
                with self.subTest(flags=flags, inputs=inputs), \
                        patch.object(Path, "expanduser", autospec=True, side_effect=expand), \
                        patch.object(cli, "read_library", side_effect=AssertionError("read")):
                    code, out, err = self.call([*inputs, *flags, "-o", MISSING_HOME])
                    self.assertEqual((code, out), (2, ""))
                    self.assertIn("cannot open verification root without symlinks"
                                  if flags and expected != "same database" else expected, err)
        alias = self.base / "alias"
        alias.symlink_to(self.a.parent, target_is_directory=True)
        with patch.object(Path, "expanduser", autospec=True, side_effect=expand):
            code, out, err = self.call([alias, "--verify-files", "-o", MISSING_HOME])
        self.assertEqual((code, out), (2, ""))
        self.assertIn("without symlinks", err)

    def test_unrelated_runtime_errors_are_not_converted(self):
        for name in ("read_library", "make_plan"):
            error = RuntimeError(f"controlled {name} failure")
            with self.subTest(name=name), patch.object(cli, name, side_effect=error), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                with self.assertRaises(RuntimeError) as raised:
                    cli.main([str(self.a), "-o", str(self.base / "no-report.json")])
                self.assertIs(raised.exception, error)
                self.assertFalse((self.base / "no-report.json").exists())

    def test_valid_relative_and_home_outputs_match_stdout(self):
        for flags in ([], ["--verify-files"]):
            code, expected, err = self.call([self.a, self.b, *flags])
            self.assertEqual((code, err), (0, ""))
            for home in (False, True):
                name = f"plan-{bool(flags)}-{home}.json"
                with self.subTest(flags=flags, home=home), chdir(self.base), \
                        patch.dict("os.environ", {"HOME": str(self.base)}):
                    output = f"~/{name}" if home else name
                    self.assertEqual(self.call([self.a, self.b, *flags, "-o", output]), (0, "", ""))
                    self.assertEqual((self.base / name).read_text(encoding="utf-8"), expected)
                    self.assertEqual(json.loads(expected)["scope"], "cross-library")

    def test_output_safety_checks_remain_in_both_modes(self):
        existing = self.base / "existing.json"
        existing.write_bytes(b"keep existing bytes")
        directory = self.base / "directory"
        directory.mkdir()
        symlink = self.base / "existing-link.json"
        symlink.symlink_to(existing)
        dangling = self.base / "dangling.json"
        dangling.symlink_to(self.base / "missing-target.json")
        into_library = self.base / "library-link"
        into_library.symlink_to(self.b.parent, target_is_directory=True)
        cases = [(p, "outside all input libraries") for p in
                 (self.a, self.a.parent / "report.json", self.b.parent / "report.json",
                  into_library / "report.json")]
        cases += [(p, "already exists") for p in (existing, directory, symlink, dangling)]
        for flags in ([], ["--verify-files"]):
            for output, expected in cases:
                with self.subTest(flags=flags, output=output):
                    code, out, err = self.call([self.a, self.b, *flags, "-o", output])
                    self.assertEqual((code, out), (2, ""))
                    self.assertIn(expected, err)
                    self.assertNotIn("Traceback", err)
        self.assertEqual(existing.read_bytes(), b"keep existing bytes")
        self.assertTrue(dangling.is_symlink())
        self.assertFalse(dangling.exists())

    def test_exclusive_create_refuses_racing_file_or_symlink(self):
        original = Path.open
        for symlink in (False, True):
            output = self.base / f"racing-{symlink}.json"
            target = self.base / "racing-target.json"
            target.write_bytes(b"keep target bytes")

            def racing_open(path, mode="r", *args, **kwargs):
                if path == output and mode == "x":
                    if symlink:
                        path.symlink_to(target)
                    else:
                        with original(path, "wb") as stream:
                            stream.write(b"racing bytes")
                return original(path, mode, *args, **kwargs)

            with self.subTest(symlink=symlink), \
                    patch.object(Path, "open", autospec=True, side_effect=racing_open):
                code, out, err = self.call([self.a, "-o", output])
            self.assertEqual((code, out), (2, ""))
            self.assertIn("File exists", err)
            self.assertEqual(output.read_bytes(), b"keep target bytes" if symlink else b"racing bytes")
            self.assertEqual(output.is_symlink(), symlink)
            self.assertEqual(target.read_bytes(), b"keep target bytes")


if __name__ == "__main__":
    unittest.main()
