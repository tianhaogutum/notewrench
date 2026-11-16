"""Create small, genuine SQLite files using an original Calibre projection.

Usage: python examples/calibre-dupes/synthetic.py {sample,verification,decisions,donors} NEW_DIRECTORY

The default sample creates format records only. verification_libraries also
creates explicit synthetic byte payloads for opt-in file verification.
"""

import json
from pathlib import Path
import sqlite3


def create_library(root: Path, records: list[dict]) -> Path:
    root.mkdir(parents=True, exist_ok=False)
    database = root / "metadata.db"
    conn = sqlite3.connect(database)
    try:
        # Independently written minimal tables with the relevant upstream fields.
        conn.executescript("""
            CREATE TABLE books(id INTEGER PRIMARY KEY, title TEXT NOT NULL, path TEXT NOT NULL);
            CREATE TABLE authors(id INTEGER PRIMARY KEY, name TEXT NOT NULL, UNIQUE(name));
            CREATE TABLE books_authors_link(id INTEGER PRIMARY KEY, book INTEGER NOT NULL,
                author INTEGER NOT NULL, UNIQUE(book, author));
            CREATE TABLE identifiers(id INTEGER PRIMARY KEY, book INTEGER NOT NULL,
                type TEXT NOT NULL, val TEXT NOT NULL, UNIQUE(book, type));
            CREATE TABLE data(id INTEGER PRIMARY KEY, book INTEGER NOT NULL, format TEXT NOT NULL,
                name TEXT NOT NULL, uncompressed_size INTEGER NOT NULL, UNIQUE(book, format));
        """)
        authors = {}
        for i, record in enumerate(records, 1):
            conn.execute("INSERT INTO books VALUES(?, ?, ?)",
                         (i, record["title"], record.get("path", f"Synthetic/Book ({i})")))
            for author in record.get("authors", []):
                if author not in authors:
                    authors[author] = len(authors) + 1
                    conn.execute("INSERT INTO authors VALUES(?, ?)", (authors[author], author))
                conn.execute("INSERT INTO books_authors_link(book, author) VALUES(?, ?)", (i, authors[author]))
            for kind, value in record.get("identifiers", {}).items():
                conn.execute("INSERT INTO identifiers(book, type, val) VALUES(?, ?, ?)", (i, kind, value))
            for fmt in record.get("formats", []):
                conn.execute("INSERT INTO data(book, format, name, uncompressed_size) VALUES(?, ?, ?, ?)",
                             (i, fmt, f"synthetic-{i}", 100 + i))
        conn.commit()
    finally:
        conn.close()
    return database


def sample_libraries(base: Path) -> tuple[Path, Path]:
    data = json.loads(Path(__file__).with_name("libraries.json").read_text(encoding="utf-8"))
    return (create_library(base / "library-a", data["A"]),
            create_library(base / "library-b", data["B"]))


def verification_libraries(base: Path) -> tuple[Path, Path]:
    """Missing, same-length/different bytes, and identical bytes; no real ebooks."""
    records = [{"title": title, "authors": ["Synthetic Writer"], "formats": ["EPUB"]}
               for title in ("Missing", "Different", "Same")]
    a = create_library(base / "library-a", records)
    b = create_library(base / "library-b", records)
    for db, contents in ((a, (None, b"AAAA", b"same content")),
                         (b, (b"present", b"BBBB", b"same content"))):
        for i, content in enumerate(contents, 1):
            folder = db.parent / f"Synthetic/Book ({i})"
            folder.mkdir(parents=True)
            if content is not None:
                (folder / f"synthetic-{i}.epub").write_bytes(content)
    return a, b


def decision_libraries(base: Path) -> tuple[Path, Path]:
    """Complementary formats and explicit language/author/edition-cue conflicts."""
    common = {"authors": ["Synthetic Writer"], "identifiers": {"isbn": "0306406152"}}
    a = create_library(base / "library-a", [
        {**common, "title": "Complementary", "formats": ["EPUB"]},
        {"title": "Synthetic Manual, first edition", "authors": ["First Writer"],
         "identifiers": {"isbn": "9780131103627"}, "formats": ["EPUB"]}])
    b = create_library(base / "library-b", [
        {**common, "title": "Complementary", "formats": ["PDF"]},
        {"title": "Synthetic Manual, second edition", "authors": ["Other Writer"],
         "identifiers": {"isbn": "9780131103627"}, "formats": ["PDF"]}])
    for db, second_language, second_date in ((a, "eng", "2020-01-01"), (b, "deu", "2024-01-01")):
        conn = sqlite3.connect(db)
        try:
            conn.executescript("""
                ALTER TABLE books ADD COLUMN pubdate TIMESTAMP;
                CREATE TABLE languages(id INTEGER PRIMARY KEY, lang_code TEXT NOT NULL);
                CREATE TABLE books_languages_link(id INTEGER PRIMARY KEY, book INTEGER NOT NULL,
                    lang_code INTEGER NOT NULL, item_order INTEGER NOT NULL DEFAULT 0);
                INSERT INTO languages VALUES(1,'eng');
                INSERT INTO books_languages_link(book,lang_code) VALUES(1,1);
                UPDATE books SET pubdate='2020-01-01';
            """)
            if second_language != "eng":
                conn.execute("INSERT INTO languages VALUES(2,?)", (second_language,))
            conn.execute("INSERT INTO books_languages_link(book,lang_code) VALUES(2,?)",
                         (1 if second_language == "eng" else 2,))
            conn.execute("UPDATE books SET pubdate=? WHERE id=2", (second_date,))
            conn.commit()
        finally:
            conn.close()
    return a, b


def donor_libraries(base: Path) -> tuple[Path, Path]:
    """Two ambiguous missing formats plus a wrong-format and unrelated donor."""
    common = {"title": "Synthetic donor choice", "authors": ["Synthetic Writer"]}
    a = create_library(base / "library-a", [{**common, "formats": ["EPUB"]}])
    b = create_library(base / "library-b", [
        {**common, "formats": ["PDF", "MOBI"]},
        {**common, "formats": ["PDF", "MOBI"]},
        {**common, "formats": ["EPUB"]},
        {"title": "Unrelated", "authors": ["Other"], "formats": ["PDF"]}])
    return a, b


SETS = {"sample": sample_libraries, "verification": verification_libraries,
        "decisions": decision_libraries, "donors": donor_libraries}


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3 or sys.argv[1] not in SETS:
        raise SystemExit(f"usage: {sys.argv[0]} {{{','.join(SETS)}}} NEW_DIRECTORY")
    for database in SETS[sys.argv[1]](Path(sys.argv[2])):
        print(database)
