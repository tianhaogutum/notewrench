"""Read the documented Calibre relational projection without importing Calibre."""

from dataclasses import dataclass, field
import hashlib
import json
from os import PathLike
from pathlib import Path
import re
import sqlite3

from .normalize import isbn13, known_text


class ReviewError(Exception):
    """An input cannot safely be reviewed; no partial plan should be emitted."""


# Original projection, not a copy of the upstream schema or its SQL.
SCHEMA = {
    "books": {"id": "INTEGER", "title": "TEXT", "path": "TEXT"},
    "authors": {"id": "INTEGER", "name": "TEXT"},
    "books_authors_link": {"id": "INTEGER", "book": "INTEGER", "author": "INTEGER"},
    "identifiers": {"id": "INTEGER", "book": "INTEGER", "type": "TEXT", "val": "TEXT"},
    "data": {"id": "INTEGER", "book": "INTEGER", "format": "TEXT",
             "name": "TEXT", "uncompressed_size": "INTEGER"},
}


LANGUAGE_SCHEMA = {
    "languages": {"id": "INTEGER", "lang_code": "TEXT"},
    "books_languages_link": {"id": "INTEGER", "book": "INTEGER", "lang_code": "INTEGER"},
}


def json_sha256(value: dict) -> str:
    """Canonical UTF-8 JSON identity; metadata only, never an ebook hash."""
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass
class Book:
    library: str
    id: int
    title: str
    path: str
    authors: list[str]
    identifiers: list[dict]
    data: list[dict]
    languages: list[str] = field(default_factory=list)
    publication_date: str | None = None

    @property
    def key(self) -> str:
        return f"{self.library}:{self.id}"

    @property
    def title_key(self) -> str:
        return known_text(self.title)

    @property
    def author_key(self) -> tuple[str, ...]:
        return tuple(sorted({k for name in self.authors if (k := known_text(name))}))

    @property
    def authors_complete(self) -> bool:
        return bool(self.authors) and all(known_text(a) for a in self.authors)

    @property
    def isbns(self) -> set[str]:
        return {v for item in self.identifiers if item["type"].casefold() == "isbn"
                and (v := isbn13(item["value"])) is not None}

    @property
    def invalid_isbns(self) -> list[str]:
        return [i["value"] for i in self.identifiers
                if i["type"].casefold() == "isbn" and isbn13(i["value"]) is None]

    @property
    def formats(self) -> set[str]:
        return {item["format"] for item in self.data}

    def as_dict(self) -> dict:
        result = {"ref": self.key, "library": self.library, "id": self.id,
                "title": self.title, "authors": self.authors, "path": self.path,
                "normalizedTitle": self.title_key, "normalizedAuthors": list(self.author_key),
                "identifiers": self.identifiers, "canonicalISBN13": sorted(self.isbns),
                "invalidISBN": self.invalid_isbns, "formats": sorted(self.formats),
                "formatRecords": self.data, "languages": self.languages,
                "publicationDate": self.publication_date}
        return {**result, "memberSHA256": json_sha256(result)}


@dataclass
class Library:
    label: str
    root: Path
    database: Path
    books: list[Book]
    sha256: str
    user_version: int

    def as_dict(self) -> dict:
        return {"label": self.label, "root": str(self.root), "database": str(self.database),
                "databaseSHA256": self.sha256, "userVersion": self.user_version,
                "bookCount": len(self.books)}


def _library_path(value: str | PathLike[str]) -> Path:
    """Validate supplied path type/text before expansion or filesystem work."""
    try:
        path = Path(value)
    except TypeError as exc:
        raise ReviewError("library path must be a string or a path-like object returning a string") from exc
    if "\0" in str(path):
        raise ReviewError("library path must not contain NUL characters")
    return path


def locate_library(value: str | PathLike[str]) -> tuple[Path, Path]:
    supplied_path = _library_path(value)
    try:
        path = supplied_path.expanduser().absolute()
        if path.is_dir():
            root = path.resolve(strict=True)
            db = (root / "metadata.db").resolve(strict=True)
        else:
            if path.name != "metadata.db":
                raise ReviewError("input must be a library directory or a file named metadata.db")
            root = path.parent.resolve(strict=True)
            db = path.resolve(strict=True)
        if db.parent != root or not db.is_file():
            raise ReviewError("metadata.db must be a regular file directly inside its explicit library")
        return root, db
    except (OSError, RuntimeError) as exc:
        raise ReviewError(f"cannot locate library {supplied_path}: {exc}") from exc


def _affinity(declaration: str) -> str:
    declaration = declaration.upper()
    if "INT" in declaration:
        return "INTEGER"
    if any(x in declaration for x in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    return "unsupported"


def _validate_schema(conn: sqlite3.Connection, schema: dict = SCHEMA) -> None:
    for table, columns in schema.items():
        obj = conn.execute("SELECT type, sql FROM sqlite_master WHERE name=?", (table,)).fetchone()
        if not obj or obj[0] != "table" or not obj[1] or "VIRTUAL TABLE" in obj[1].upper():
            raise ReviewError(f"unsupported schema: {table} must be an ordinary table")
        info = {row[1]: row for row in conn.execute(f'PRAGMA table_info("{table}")')}
        for name, affinity in columns.items():
            if name not in info or _affinity(info[name][2]) != affinity:
                raise ReviewError(f"unsupported schema: {table}.{name} requires {affinity} affinity")
        if info["id"][5] != 1 or sum(bool(row[5]) for row in info.values()) != 1:
            raise ReviewError(f"unsupported schema: {table}.id must be the sole primary key")


def _rows(conn: sqlite3.Connection, table: str, schema: dict = SCHEMA):
    columns = schema[table]
    for row in conn.execute(f'SELECT {", ".join(columns)} FROM "{table}"'):
        record = dict(zip(columns, row))
        for name, affinity in columns.items():
            if type(record[name]) is not (int if affinity == "INTEGER" else str):
                raise ReviewError(f"invalid data: {table}.{name} has NULL or unexpected storage type")
        yield record


def _read_optional_metadata(conn: sqlite3.Connection, books: dict[int, Book]) -> None:
    # Pinned upstream fields. Legacy minimal projections can omit them, which
    # means unknown metadata; a present but incomplete relation is an error.
    present = {r[0] for r in conn.execute("SELECT name FROM sqlite_master")}
    language_tables = set(LANGUAGE_SCHEMA) & present
    if language_tables:
        if language_tables != set(LANGUAGE_SCHEMA):
            raise ReviewError("unsupported schema: languages and books_languages_link must appear together")
        _validate_schema(conn, LANGUAGE_SCHEMA)
        languages = {r["id"]: r["lang_code"] for r in _rows(conn, "languages", LANGUAGE_SCHEMA)}
        seen = set()
        for row in _rows(conn, "books_languages_link", LANGUAGE_SCHEMA):
            book, language = row["book"], row["lang_code"]
            if book not in books or language not in languages:
                raise ReviewError("invalid data: dangling books_languages_link reference")
            if (book, language) in seen:
                raise ReviewError("invalid data: duplicate book/language link")
            seen.add((book, language))
            books[book].languages.append(languages[language])
    info = {r[1]: r for r in conn.execute('PRAGMA table_info("books")')}
    if "pubdate" in info:
        declaration = info["pubdate"][2].upper()
        if declaration != "TIMESTAMP" and _affinity(declaration) != "TEXT":
            raise ReviewError("unsupported schema: books.pubdate requires TIMESTAMP or TEXT")
        for book, date in conn.execute("SELECT id, pubdate FROM books"):
            if date is not None and type(date) is not str:
                raise ReviewError("invalid data: books.pubdate must be text or NULL")
            books[book].publication_date = date
    for book in books.values():
        book.languages.sort()


def _clean_database(db: Path) -> None:
    # mode=ro alone can still create WAL shared-memory files. Never use immutable=1
    # to bypass locking or ignore a WAL. Refuse these states explicitly instead.
    if any(Path(str(db) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise ReviewError("unsupported database state: WAL/SHM/journal present; use a clean, closed rollback-mode library")
    with db.open("rb") as stream:
        header = stream.read(100)
    if not header.startswith(b"SQLite format 3\x00") or len(header) != 100:
        raise ReviewError("invalid database: not a SQLite 3 file")
    if header[18:20] != b"\x01\x01":
        raise ReviewError("unsupported database state: WAL or unknown journal mode; use a clean rollback-mode copy")


def read_library(value: str | PathLike[str], label: str = "A", *, timeout: float = 1.0) -> Library:
    if (type(timeout) is not int and type(timeout) is not float) or not 0 <= timeout <= 30:
        raise ReviewError("timeout must be a finite int or float between 0 and 30 seconds (excluding bool)")
    root, db = locate_library(value)
    conn = None
    try:
        _clean_database(db)
        conn = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=timeout)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA trusted_schema=OFF")
        conn.execute("BEGIN")
        _validate_schema(conn)  # acquires a snapshot/shared lock before the hash
        _clean_database(db)
        books = {r["id"]: Book(label, r["id"], r["title"], r["path"], [], [], [])
                 for r in _rows(conn, "books")}
        authors = {r["id"]: r["name"].replace("|", ",") for r in _rows(conn, "authors")}
        seen = set()
        for r in _rows(conn, "books_authors_link"):
            if r["book"] not in books or r["author"] not in authors:
                raise ReviewError("invalid data: dangling books_authors_link reference")
            pair = r["book"], r["author"]
            if pair in seen:
                raise ReviewError("invalid data: duplicate book/author link")
            seen.add(pair)
            books[r["book"]].authors.append(authors[r["author"]])
        seen.clear()
        for r in _rows(conn, "identifiers"):
            if r["book"] not in books:
                raise ReviewError("invalid data: dangling identifiers.book reference")
            pair = r["book"], r["type"].casefold()
            if pair in seen:
                raise ReviewError("invalid data: duplicate identifier type on a book")
            seen.add(pair)
            books[r["book"]].identifiers.append({"type": r["type"], "value": r["val"]})
        seen.clear()
        for r in _rows(conn, "data"):
            if r["book"] not in books:
                raise ReviewError("invalid data: dangling data.book reference")
            fmt = r["format"].upper()
            if not re.fullmatch(r"[A-Z0-9_]+", fmt) or r["uncompressed_size"] < 0:
                raise ReviewError("invalid data: unsupported format token or negative format size")
            pair = r["book"], fmt
            if pair in seen:
                raise ReviewError("invalid data: duplicate format on a book")
            seen.add(pair)
            books[r["book"]].data.append({"format": fmt, "name": r["name"],
                                         "uncompressedSize": r["uncompressed_size"]})
        _read_optional_metadata(conn, books)
        for b in books.values():
            b.authors.sort()
            b.identifiers.sort(key=lambda x: (x["type"], x["value"]))
            b.data.sort(key=lambda x: x["format"])
        with db.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        return Library(label, root, db, sorted(books.values(), key=lambda b: b.id),
                       digest, conn.execute("PRAGMA user_version").fetchone()[0])
    except (OSError, sqlite3.Error, UnicodeError) as exc:
        raise ReviewError(f"cannot read {db}: {exc}") from exc
    finally:
        if conn is not None:
            conn.close()
