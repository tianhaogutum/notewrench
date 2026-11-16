from contextlib import closing, redirect_stderr, redirect_stdout
import hashlib
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

from notewrench.calibredupes.cli import main
from .helpers import create_library

ROOT = Path(__file__).resolve().parents[2]


class FileVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(map(str, args)))
        return code, out.getvalue(), err.getvalue()

    def fixture(self):
        records = [{"title": title, "authors": ["Writer"], "formats": ["EPUB"]}
                   for title in ("Missing", "Different", "Same")]
        a = create_library(self.base / "a", records)
        b = create_library(self.base / "b", records)
        for db, contents in ((a, (None, b"AAAA", b"same content")),
                             (b, (b"present", b"BBBB", b"same content"))):
            for i, content in enumerate(contents, 1):
                folder = db.parent / f"Synthetic/Book ({i})"
                folder.mkdir(parents=True)
                if content is not None:
                    (folder / f"synthetic-{i}.epub").write_bytes(content)
        return a, b

    def snapshot(self):
        return {str(p.relative_to(self.base)): p.read_bytes()
                for p in self.base.rglob("*") if p.is_file()}

    def test_missing_different_same_bytes_cli_and_read_only(self):
        a, b = self.fixture()
        before = self.snapshot()
        code, out, err = self.call(a.parent, b, "--verify-files")
        self.assertEqual((code, err), (0, ""))
        plan = json.loads(out)
        verification = plan["fileVerification"]
        self.assertTrue(verification["enabled"])
        records = {(r["ref"], r["format"]): r for r in verification["records"]}
        self.assertEqual(records["A:1", "EPUB"]["status"], "missing")
        self.assertEqual(records["A:3", "EPUB"]["bytes"], 12)
        self.assertEqual(records["A:3", "EPUB"]["sha256"], hashlib.sha256(b"same content").hexdigest())
        self.assertFalse(records["A:3", "EPUB"]["declaredSizeMatches"])
        evidence = [c["pairs"][0]["contentEvidence"] for c in plan["clusters"]]
        self.assertEqual([e["formats"][0]["status"] for e in evidence],
                         ["unverified", "different-bytes", "same-bytes-sha256"])
        self.assertEqual([e["level"] for e in evidence],
                         ["content-incomplete", "content-different", "content-match"])
        self.assertIn("overlapping-format-content-unverified", plan["clusters"][0]["conflicts"])
        self.assertIn("overlapping-format-bytes-differ", plan["clusters"][1]["conflicts"])
        self.assertNotIn("overlapping-format-content-unverified", plan["clusters"][2]["conflicts"])
        self.assertEqual(self.snapshot(), before)

    def test_default_mode_does_not_open_format_files(self):
        a, b = self.fixture()
        with patch("os.open", side_effect=AssertionError("metadata mode opened a format")):
            code, out, err = self.call(a, b)
        self.assertEqual((code, err), (0, ""))
        plan = json.loads(out)
        self.assertEqual(plan["fileVerification"],
                         {"enabled": False, "evidenceLevel": "metadata-only", "records": []})
        for c in plan["clusters"]:
            self.assertEqual(c["pairs"][0]["contentEvidence"]["level"], "metadata-only")

    def test_different_file_sizes_and_unmatched_missing_declaration(self):
        a, b = self.fixture()
        (b.parent / "Synthetic/Book (2)/synthetic-2.epub").write_bytes(b"longer")
        with closing(sqlite3.connect(a)) as conn, conn:
            conn.execute("INSERT INTO books VALUES(4,'Unmatched','Synthetic/Book (4)')")
            conn.execute("INSERT INTO data(book,format,name,uncompressed_size) VALUES(4,'PDF','absent',5)")
        code, out, err = self.call(a, b, "--verify-files")
        self.assertEqual((code, err), (0, ""))
        plan = json.loads(out)
        self.assertEqual(plan["clusters"][1]["pairs"][0]["contentEvidence"]["formats"][0]["status"], "different-bytes")
        self.assertEqual(plan["fileVerification"]["records"][3]["status"], "missing")
        self.assertEqual(plan["fileVerification"]["records"][3]["ref"], "A:4")

    def test_unsafe_metadata_paths_are_never_opened(self):
        a, b = self.fixture()
        for field, value in (("path", "../../outside"), ("path", str(self.base)),
                             ("path", "Synthetic/../Book"), ("path", "C:\\outside"),
                             ("path", "Synthetic//Book"), ("path", "bad\x00path"),
                             ("name", "../../outside"), ("name", "/absolute"),
                             ("name", ".."), ("name", "C:\\outside"), ("name", "")):
            with self.subTest(field=field, value=value):
                with closing(sqlite3.connect(a)) as conn, conn:
                    conn.execute("UPDATE books SET path='Synthetic/Book (1)' WHERE id=1")
                    conn.execute("UPDATE data SET name='synthetic-1' WHERE book=1")
                    if field == "path":
                        conn.execute("UPDATE books SET path=? WHERE id=1", (value,))
                    else:
                        conn.execute("UPDATE data SET name=? WHERE book=1", (value,))
                code, out, err = self.call(a, b, "--verify-files")
                self.assertEqual((code, err), (0, ""))
                record = json.loads(out)["fileVerification"]["records"][0]
                self.assertEqual(record["status"], "unsafe-path")
                self.assertNotIn("sha256", record)

    def test_symlinks_and_nonregular_files_are_not_followed(self):
        a, b = self.fixture()
        target = self.base / "outside.epub"
        target.write_bytes(b"private outside")
        file = a.parent / "Synthetic/Book (1)/synthetic-1.epub"
        for destination in (target, b.parent / "Synthetic/Book (3)/synthetic-3.epub", self.base / "absent"):
            file.symlink_to(destination)
            code, out, err = self.call(a, b, "--verify-files")
            self.assertEqual((code, err), (0, ""))
            self.assertEqual(json.loads(out)["fileVerification"]["records"][0]["status"], "blocked-path")
            file.unlink()
        folder = file.parent
        folder.rmdir()
        folder.symlink_to(b.parent / "Synthetic/Book (1)", target_is_directory=True)
        code, out, err = self.call(a, b, "--verify-files")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["fileVerification"]["records"][0]["status"], "blocked-path")
        folder.unlink()
        folder.mkdir()
        os.mkfifo(file)
        result = subprocess.run([sys.executable, "-m", "notewrench", "calibre-dupes", str(a), "--verify-files"],
                                cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["fileVerification"]["records"][0]["status"], "not-regular")
        self.assertEqual(target.read_bytes(), b"private outside")

    def test_symlink_root_fails_without_report(self):
        a, b = self.fixture()
        alias = self.base / "alias"
        alias.symlink_to(a.parent, target_is_directory=True)
        output = self.base / "no-report.json"
        code, out, err = self.call(alias, b, "--verify-files", "-o", output)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("verification root", err)
        self.assertFalse(output.exists())

    def test_unreadable_file_is_reported_without_hash(self):
        a, b = self.fixture()
        file = a.parent / "Synthetic/Book (2)/synthetic-2.epub"
        file.chmod(0)
        self.addCleanup(file.chmod, 0o600)
        code, out, err = self.call(a, b, "--verify-files")
        self.assertEqual((code, err), (0, ""))
        record = json.loads(out)["fileVerification"]["records"][1]
        self.assertEqual(record["status"], "unreadable")
        self.assertNotIn("sha256", record)

    def test_change_during_hash_is_not_reported_as_verified(self):
        a, b = self.fixture()
        file = a.parent / "Synthetic/Book (2)/synthetic-2.epub"
        original_read = os.read
        changed = False

        def changing_read(fd, count):
            nonlocal changed
            chunk = original_read(fd, count)
            if not changed:
                changed = True
                file.write_bytes(b"changed during read")
            return chunk

        with patch("os.read", side_effect=changing_read):
            code, out, err = self.call(a, b, "--verify-files")
        self.assertEqual((code, err), (0, ""))
        record = json.loads(out)["fileVerification"]["records"][1]
        self.assertEqual(record["status"], "changed-during-read")
        self.assertNotIn("sha256", record)

    def test_shared_formats_aggregate_separately_from_metadata_strength(self):
        a, b = self.fixture()
        for db, content in ((a, b"AAAA"), (b, b"BBBB")):
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute("INSERT INTO identifiers(book,type,val) VALUES(3,'isbn','0306406152')")
                conn.execute("INSERT INTO data(book,format,name,uncompressed_size) VALUES(3,'PDF','synthetic-3',4)")
            (db.parent / "Synthetic/Book (3)/synthetic-3.pdf").write_bytes(content)

        def evidence():
            code, out, err = self.call(a, b, "--verify-files")
            self.assertEqual((code, err), (0, ""))
            cluster = json.loads(out)["clusters"][2]
            self.assertEqual(cluster["level"], "strong")
            self.assertEqual(cluster["pairs"][0]["level"], "strong")
            return cluster["pairs"][0]["contentEvidence"]

        self.assertEqual(evidence()["level"], "content-different")
        (b.parent / "Synthetic/Book (3)/synthetic-3.pdf").unlink()
        self.assertEqual(evidence()["level"], "content-incomplete")
        with closing(sqlite3.connect(b)) as conn, conn:
            conn.execute("DELETE FROM data WHERE book=3 AND format='PDF'")
        with closing(sqlite3.connect(a)) as conn, conn:
            conn.execute("DELETE FROM data WHERE book=3 AND format='EPUB'")
        self.assertEqual(evidence(), {"level": "metadata-only", "formats": []})

    def test_cli_outside_repository_verification_outside_worktree(self):
        a, b = self.fixture()
        result = subprocess.run([sys.executable, "-m", "notewrench", "calibre-dupes", str(a), str(b), "--verify-files"],
                                cwd=self.base, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual([c["pairs"][0]["contentEvidence"]["level"] for c in plan["clusters"]],
                         ["content-incomplete", "content-different", "content-match"])


if __name__ == "__main__":
    unittest.main()
