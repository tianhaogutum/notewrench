from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from notewrench.calibredupes.cli import main
from notewrench.calibredupes.compare import make_plan, pair_evidence
from notewrench.calibredupes.reader import ReviewError, read_library
from .helpers import create_library, sample_libraries

ROOT = Path(__file__).resolve().parents[2]


def record(title="Fictional Title", author="Example Author", isbn=None, formats=None):
    result = {"title": title, "authors": [] if author is None else [author], "formats": formats or []}
    if isbn is not None:
        result["identifiers"] = {"isbn": isbn}
    return result


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def libraries(self, a=None, b=None):
        return (create_library(self.base / "a", a if a is not None else [record(isbn="0306406152", formats=["EPUB"])]),
                create_library(self.base / "b", b if b is not None else [record(isbn="9780306406157", formats=["PDF"])]))

    def plan(self, a, b):
        return make_plan([read_library(a, "A"), read_library(b, "B")])

    def call(self, *args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(list(map(str, args)))
        return code, stdout.getvalue(), stderr.getvalue()

    def mutate(self, db, sql, params=()):
        conn = sqlite3.connect(db)
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()

    def test_two_real_sqlite_libraries_e2e_read_only_matrix(self):
        a, b = self.libraries()
        before = {db: hashlib.sha256(db.read_bytes()).hexdigest() for db in (a, b)}
        listing = {db.parent: sorted(db.parent.iterdir()) for db in (a, b)}
        code, stdout, stderr = self.call(a.parent, b)
        self.assertEqual((code, stderr), (0, ""))
        plan = json.loads(stdout)
        self.assertEqual(plan["summary"]["strongPairs"], 1)
        cluster = plan["clusters"][0]
        self.assertEqual(cluster["pairs"][0]["matchEvidence"], ["isbn-equivalent", "title-normalized", "author-normalized"])
        self.assertEqual(cluster["formats"], {"A": ["EPUB"], "B": ["PDF"]})
        self.assertEqual(cluster["missingByLibrary"], {"A": ["PDF"], "B": ["EPUB"]})
        self.assertEqual(cluster["missingByBook"], {"A:1": ["PDF"], "B:1": ["EPUB"]})
        for db, lib in zip((a, b), plan["libraries"]):
            self.assertEqual(hashlib.sha256(db.read_bytes()).hexdigest(), before[db])
            self.assertEqual(lib["databaseSHA256"], before[db])
            self.assertEqual(sorted(db.parent.iterdir()), listing[db.parent])

    def test_same_title_different_known_authors_is_negative(self):
        a, b = self.libraries([record(author="Alice")], [record(author="Bob")])
        self.assertEqual(self.plan(a, b)["clusters"], [])

    def test_unicode_title_author_is_only_candidate_without_isbn(self):
        a, b = self.libraries([record("ＣＡＦÉ—Maps", "Zoë")], [record("café maps", "Zoë")])
        edge = self.plan(a, b)["clusters"][0]["pairs"][0]
        self.assertEqual(edge["level"], "candidate")
        self.assertEqual(edge["matchEvidence"], ["title-normalized", "author-normalized"])

    def test_missing_author_downgrades_even_valid_isbn(self):
        a, b = self.libraries([record(author=None, isbn="0306406152")], [record(isbn="9780306406157")])
        edge = self.plan(a, b)["clusters"][0]["pairs"][0]
        self.assertEqual(edge["level"], "candidate")
        self.assertIn("missing-author", edge["conflicts"])

    def test_placeholder_unknown_and_partial_author_set_are_incomplete(self):
        a = record(author="Unknown", isbn="0306406152")
        a["authors"].append("Example Author")
        a, b = self.libraries([a], [record(isbn="9780306406157")])
        self.assertEqual(self.plan(a, b)["clusters"][0]["pairs"][0]["level"], "candidate")

    def test_invalid_isbn_is_not_strong_evidence(self):
        a, b = self.libraries([record(isbn="9780306406158")], [record(isbn="9780306406158")])
        plan = self.plan(a, b)
        edge = plan["clusters"][0]["pairs"][0]
        self.assertEqual(edge["level"], "candidate")
        self.assertNotIn("isbn-exact", edge["matchEvidence"])
        self.assertIn("invalid-isbn", edge["conflicts"])
        self.assertEqual(len(plan["diagnostics"]), 2)

    def test_invalid_isbn_on_unmatched_book_is_reported(self):
        a, b = self.libraries([record("One", isbn="broken")], [record("Other")])
        plan = self.plan(a, b)
        self.assertEqual(plan["clusters"], [])
        self.assertEqual(plan["diagnostics"][0]["invalidISBN"], ["broken"])

    def test_valid_but_conflicting_isbn_downgrades_title_author_match(self):
        a, b = self.libraries([record(isbn="9780306406157")], [record(isbn="9780131103627")])
        edge = self.plan(a, b)["clusters"][0]["pairs"][0]
        self.assertEqual(edge["level"], "candidate")
        self.assertIn("isbn-conflict", edge["conflicts"])

    def test_shared_isbn_with_conflicting_title_and_author_is_candidate(self):
        a, b = self.libraries([record(isbn="9780306406157")],
                             [record("Other Title", "Other Author", "0306406152")])
        edge = self.plan(a, b)["clusters"][0]["pairs"][0]
        self.assertEqual(edge["level"], "candidate")
        self.assertEqual(edge["conflicts"], ["title-conflict", "author-conflict"])

    def test_author_sets_ignore_order_and_decode_calibre_pipe(self):
        left = record(author="Family| Given", isbn="0306406152")
        right = record(author="Family, Given", isbn="9780306406157")
        left["authors"].append("Second Writer")
        right["authors"].insert(0, "Second Writer")
        a, b = self.libraries([left], [right])
        self.assertEqual(self.plan(a, b)["summary"]["strongPairs"], 1)

    def test_single_library_duplicates_and_empty_library(self):
        db = create_library(self.base / "a", [record(), record(), record(author="Other")])
        plan = make_plan([read_library(db)])
        self.assertEqual(plan["scope"], "within-library")
        self.assertEqual(plan["summary"]["matchedPairs"], 1)
        empty = create_library(self.base / "empty", [])
        self.assertEqual(make_plan([read_library(empty)])["summary"]["books"], 0)

    def test_two_library_scope_excludes_internal_duplicates(self):
        a, b = self.libraries([record(), record()], [record("Unrelated")])
        self.assertEqual(self.plan(a, b)["summary"]["matchedPairs"], 0)

    def test_transitive_groups_preserve_pair_conflicts_and_missing_edges(self):
        # A:1 matches B:1 by title/author but has a conflicting ISBN.
        # A:2 matches B:1 by ISBN; A:1/A:2 are not compared across two libraries.
        a, b = self.libraries([record(isbn="9780131103627"), record("Other", isbn="0306406152")],
                             [record(isbn="9780306406157"), record("Other")])
        cluster = self.plan(a, b)["clusters"][0]
        self.assertEqual(cluster["level"], "candidate")
        self.assertIn("cluster-isbn-conflict", cluster["conflicts"])
        self.assertEqual(len(cluster["pairs"]), 3)
        self.assertTrue(cluster["hasUnevidencedPairs"])
        self.assertEqual({m["ref"] for m in cluster["members"]}, {"A:1", "A:2", "B:1", "B:2"})

    def test_overlapping_formats_never_imply_equal_contents(self):
        a, b = self.libraries([record(isbn="0306406152", formats=["EPUB"])],
                             [record(isbn="9780306406157", formats=["epub"])])
        cluster = self.plan(a, b)["clusters"][0]
        self.assertIn("overlapping-format-content-unverified", cluster["conflicts"])
        self.assertEqual(cluster["formatUnion"], ["EPUB"])

    def test_raw_database_paths_and_other_identifiers_are_not_executed(self):
        item = record(title="$(touch HACKED)")
        item["path"] = "../../outside; rm -rf /"
        item["identifiers"] = {"doi": "arbitrary", "isbn13": "9780306406157"}
        a, b = self.libraries([item], [item])
        plan = self.plan(a, b)
        member = plan["clusters"][0]["members"][0]
        self.assertEqual(member["path"], item["path"])
        self.assertEqual(member["canonicalISBN13"], [])
        self.assertEqual(sorted(p.name for p in self.base.iterdir()), ["a", "b"])

    def test_deterministic_plan(self):
        a, b = self.libraries()
        self.assertEqual(self.plan(a, b), self.plan(a, b))

    def test_read_only_directory_and_database_are_supported(self):
        a, b = self.libraries()
        before = a.read_bytes()
        a.chmod(0o444)
        a.parent.chmod(0o555)
        try:
            self.assertEqual(self.plan(a, b)["summary"]["strongPairs"], 1)
            self.assertEqual(a.read_bytes(), before)
            self.assertEqual(sorted(p.name for p in a.parent.iterdir()), ["metadata.db"])
        finally:
            a.parent.chmod(0o755)
            a.chmod(0o600)

    def test_duplicate_identifier_and_format_rows_are_not_silently_collapsed(self):
        a, b = self.libraries()
        self.mutate(a, "INSERT INTO identifiers(book,type,val) VALUES(1,'ISBN','9780306406157')")
        with self.assertRaisesRegex(ReviewError, "duplicate identifier"):
            read_library(a)
        self.mutate(b, "INSERT INTO data(book,format,name,uncompressed_size) VALUES(1,'pdf','x',10)")
        with self.assertRaisesRegex(ReviewError, "duplicate format"):
            read_library(b)

    def test_actual_live_wal_is_refused_with_all_bytes_unchanged(self):
        a, b = self.libraries()
        conn = sqlite3.connect(a)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("UPDATE books SET title='Committed in WAL'")
            conn.commit()
            self.assertTrue(Path(str(a) + "-wal").exists())
            before = {p: p.read_bytes() for p in a.parent.iterdir()}
            self.assertEqual(self.call(a)[0], 2)
            self.assertEqual({p: p.read_bytes() for p in a.parent.iterdir()}, before)
        finally:
            conn.close()

    def test_schema_missing_table_wrong_type_and_view_refused(self):
        for index, statement in enumerate(("DROP TABLE data", "ALTER TABLE data RENAME COLUMN format TO fmt",
                                           "DROP TABLE authors")):
            db = create_library(self.base / str(index), [record()])
            self.mutate(db, statement)
            if index == 2:
                self.mutate(db, "CREATE VIEW authors AS SELECT 1 AS id, 'Fake' AS name")
            with self.subTest(statement=statement), self.assertRaisesRegex(ReviewError, "unsupported schema"):
                read_library(db)
        db = create_library(self.base / "wrongtype", [])
        self.mutate(db, "DROP TABLE data")
        self.mutate(db, "CREATE TABLE data(id INTEGER PRIMARY KEY, book INTEGER, format BLOB, name TEXT, uncompressed_size INTEGER)")
        with self.assertRaisesRegex(ReviewError, "data.format requires TEXT"):
            read_library(db)

    def test_invalid_rows_dangling_links_and_negative_size_refused(self):
        mutations = [
            ("INSERT INTO books_authors_link(book, author) VALUES(1,999)", "dangling"),
            ("INSERT INTO identifiers(book,type,val) VALUES(999,'isbn','bad')", "dangling"),
            ("INSERT INTO data(book,format,name,uncompressed_size) VALUES(999,'EPUB','x',1)", "dangling"),
            ("INSERT INTO data(book,format,name,uncompressed_size) VALUES(1,'EPUB','x',-1)", "negative"),
            ("INSERT INTO data(book,format,name,uncompressed_size) VALUES(1,'../../EPUB','x',1)", "format"),
            ("UPDATE books SET title=x'ff'", "storage type"),
        ]
        for i, (sql, expected) in enumerate(mutations):
            db = create_library(self.base / str(i), [record()])
            self.mutate(db, sql)
            with self.subTest(sql=sql), self.assertRaisesRegex(ReviewError, expected):
                read_library(db)

    def test_custom_collation_and_extra_columns_need_no_calibre_extension(self):
        db = create_library(self.base / "a", [record()])
        conn = sqlite3.connect(db)
        conn.create_collation("icucollate", lambda a, b: (a > b) - (a < b))
        conn.execute("CREATE TABLE unrelated(value TEXT COLLATE icucollate)")
        conn.execute("ALTER TABLE books ADD COLUMN sort TEXT")
        conn.commit()
        conn.close()
        self.assertEqual(len(read_library(db).books), 1)

    def test_locked_database_returns_explicit_error_without_output(self):
        a, b = self.libraries()
        conn = sqlite3.connect(a)
        try:
            conn.execute("BEGIN EXCLUSIVE")
            result = subprocess.run([sys.executable, "-m", "notewrench", "calibre-dupes", str(a), str(b), "--timeout", "0.01"],
                                    cwd=ROOT, capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertIn("locked", result.stderr)
        finally:
            conn.close()

    def test_corrupt_missing_and_unreadable_database_errors(self):
        root = self.base / "corrupt"
        root.mkdir()
        (root / "metadata.db").write_bytes(b"not SQLite")
        code, out, error = self.call(root)
        self.assertEqual((code, out), (2, ""))
        self.assertIn("not a SQLite", error)
        self.assertEqual(self.call(self.base / "absent")[0], 2)
        a, b = self.libraries()
        a.chmod(0)
        try:
            with self.assertRaisesRegex(ReviewError, "Permission denied"):
                read_library(a)
        finally:
            a.chmod(0o600)

    def test_wal_and_sidecars_are_refused_without_changes(self):
        a, b = self.libraries()
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(a) + suffix)
            sidecar.write_bytes(b"synthetic sidecar")
            with self.assertRaisesRegex(ReviewError, "WAL/SHM/journal"):
                read_library(a)
            self.assertEqual(sidecar.read_bytes(), b"synthetic sidecar")
            sidecar.unlink()
        self.mutate(a, "PRAGMA journal_mode=WAL")
        before = a.read_bytes()
        with self.assertRaisesRegex(ReviewError, "WAL"):
            read_library(a)
        self.assertEqual(a.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in a.parent.iterdir()), ["metadata.db"])

    def test_pair_budget_aborts_instead_of_truncating(self):
        a, b = self.libraries([record(), record()], [record(), record()])
        code, out, err = self.call(a, b, "--max-pairs", "1")
        self.assertEqual((code, out), (2, ""))
        self.assertIn("pair budget exceeded", err)

    def test_output_is_new_and_outside_libraries(self):
        a, b = self.libraries()
        output = self.base / "plan.json"
        self.assertEqual(self.call(a, b, "-o", output), (0, "", ""))
        before = output.read_bytes()
        self.assertEqual(self.call(a, b, "-o", output)[0], 2)
        self.assertEqual(output.read_bytes(), before)
        for target in (a, a.parent / "plan.json", b):
            with self.subTest(target=target):
                self.assertEqual(self.call(a, b, "-o", target)[0], 2)
        alias = self.base / "link.json"
        alias.symlink_to(a)
        self.assertEqual(self.call(a, b, "-o", alias)[0], 2)
        alias.unlink()
        alias.hardlink_to(a)
        self.assertEqual(self.call(a, b, "-o", alias)[0], 2)

    def test_same_database_and_escaping_database_symlink_are_refused(self):
        a, b = self.libraries()
        self.assertEqual(self.call(a, a.parent)[0], 2)
        alias = self.base / "alias"
        alias.mkdir()
        (alias / "metadata.db").symlink_to(a)
        self.assertEqual(self.call(alias)[0], 2)

    def test_failure_does_not_create_output(self):
        a, b = self.libraries()
        self.mutate(b, "DROP TABLE data")
        output = self.base / "no-plan.json"
        self.assertEqual(self.call(a, b, "-o", output)[0], 2)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
