# SPDX-License-Identifier: MIT
# Copyright (c) 2026 archive-relink contributors
"""Disposable SQLite index, evidence plans, and explicitly selected patches."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections import deque
from pathlib import Path
from urllib.parse import quote, urlsplit

from . import __version__
from .archive import (
    ArchiveChangedError,
    digest_file,
    documents,
    files,
    read_bounded,
    warc_redirects,
)
from .markdown import citations
from .urls import normalized_text, url_key, useful_quote

SCHEMA_VERSION = 2
MAX_PLAN_CANDIDATES = 20000
MAX_PLAN_LINKS = 20000
MAX_PLAN_BYTES = 32 * 1024 * 1024


def atomic_write(path: Path, data: str) -> None:
    if path.is_symlink():
        raise ValueError(f"output path must not be a symlink: {path}")
    if path.exists() and not path.is_file():
        raise ValueError(f"existing output is not a regular file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def build_index(root: Path, database: Path) -> dict:
    root, database = root.resolve(), database.absolute()
    if not root.is_dir():
        raise ValueError(f"archive directory does not exist: {root}")
    if database.is_symlink():
        raise ValueError("index path must not be a symlink")
    if database.exists():
        try:
            old = open_index(database, allow_older=True)
            old.close()
        except (ValueError, sqlite3.Error) as exc:
            raise ValueError(
                f"refusing to overwrite a file that is not our index: {database}"
            ) from exc
    database.parent.mkdir(parents=True, exist_ok=True)
    cache = database.with_suffix(database.suffix + ".assets")
    if cache.is_symlink():
        raise ValueError("index assets directory must not be a symlink")
    if cache.exists() and not cache.is_dir():
        raise ValueError("index assets path is not a directory")
    if cache.resolve() == root:
        raise ValueError("index assets directory cannot be the archive root")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{database.name}.", dir=database.parent)
    os.close(descriptor)
    warnings: list[dict] = []
    warning_count = 0

    def warn(path: Path, message: str) -> None:
        nonlocal warning_count
        warning_count += 1
        if len(warnings) < 1000:
            warnings.append({"path": str(path)[:4096], "message": message[:2000]})

    exclude = {cache.resolve(), database.resolve(), Path(temporary).resolve()}
    hashes: dict[Path, str] = {}

    def fingerprint(path: Path) -> str:
        if path not in hashes:
            hashes[path] = digest_file(path)
        return hashes[path]

    connection = sqlite3.connect(temporary)
    count = 0
    redirects = 0
    try:
        connection.executescript("""
            PRAGMA cache_size = -4096;
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE documents (
                id TEXT PRIMARY KEY, target TEXT NOT NULL, source TEXT NOT NULL,
                kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
                target_sha256 TEXT NOT NULL, source_sha256 TEXT NOT NULL
            );
            CREATE TABLE urls (
                document_id TEXT NOT NULL, key TEXT NOT NULL, kind TEXT NOT NULL,
                evidence TEXT NOT NULL, UNIQUE(document_id,key,evidence)
            );
            CREATE INDEX urls_key ON urls(key,document_id);
            CREATE VIRTUAL TABLE body_fts USING fts5(
                document_id UNINDEXED, body, tokenize='trigram'
            );
            CREATE TABLE redirects (from_key TEXT, to_key TEXT, evidence TEXT);
            CREATE INDEX redirects_from ON redirects(from_key);
        """)
        for key, value in {
            "application": "archive-relink",
            "schema": SCHEMA_VERSION,
            "root": str(root),
            "version": __version__,
        }.items():
            connection.execute("INSERT INTO meta VALUES (?,?)", (key, json.dumps(value)))
        for doc in documents(root, cache, warn, exclude):
            if doc.parsed_sha256 and fingerprint(doc.target) != doc.parsed_sha256:
                raise ArchiveChangedError(f"archive changed while indexing; retry: {doc.target}")
            identifier = (
                "C"
                + hashlib.sha256(
                    (str(doc.target.resolve()) + "\0" + str(doc.source.resolve())).encode()
                ).hexdigest()[:16]
            )
            connection.execute(
                "INSERT OR IGNORE INTO documents VALUES (?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    str(doc.target.resolve()),
                    str(doc.source.resolve()),
                    doc.kind,
                    doc.title,
                    doc.body,
                    fingerprint(doc.target),
                    fingerprint(doc.source),
                ),
            )
            connection.execute("INSERT INTO body_fts VALUES (?,?)", (identifier, doc.body))
            for item in doc.urls:
                if item:
                    provenance = Path(item.get("source_file", item["source"]))
                    if provenance.is_file():
                        actual = fingerprint(provenance)
                        if item.get("source_sha256", actual) != actual:
                            raise ArchiveChangedError(
                                f"archive metadata changed while indexing; retry: {provenance}"
                            )
                        item["source_sha256"] = actual
                        item["source_file"] = str(provenance.resolve())
                    connection.execute(
                        "INSERT OR IGNORE INTO urls VALUES (?,?,?,?)",
                        (
                            identifier,
                            item["key"],
                            item["kind"],
                            json.dumps(item, ensure_ascii=False),
                        ),
                    )
            count += 1
        for path in files(root, exclude, warn):
            if not path.name.endswith((".warc", ".warc.gz")):
                continue
            try:
                for item in warc_redirects(path):
                    item["source_file"] = str(path.resolve())
                    item["source_sha256"] = fingerprint(path)
                    connection.execute(
                        "INSERT INTO redirects VALUES (?,?,?)",
                        (
                            url_key(item["from"]),
                            url_key(item["to"]),
                            json.dumps(item),
                        ),
                    )
                    redirects += 1
            except Exception as exc:
                warn(path, f"cannot read recorded redirects: {type(exc).__name__}: {exc}")
        if warning_count > len(warnings):
            warnings.append(
                {
                    "path": str(root),
                    "message": (
                        f"{warning_count - len(warnings)} further warnings suppressed "
                        "(first 1000 retained)"
                    ),
                }
            )
        connection.execute("INSERT INTO meta VALUES ('warnings',?)", (json.dumps(warnings),))
        connection.commit()
        connection.close()
        os.replace(temporary, database)
    except BaseException:
        connection.close()
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    return {
        "documents": count,
        "redirects": redirects,
        "database": str(database),
        "assets": str(cache),
        "warnings": warnings,
    }


def open_index(path: Path, allow_older: bool = False) -> sqlite3.Connection:
    if not path.is_file():
        raise ValueError(f"index does not exist: {path}; run index first")
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        version = json.loads(row[0]) if row else None
        if version != SCHEMA_VERSION and not (allow_older and version == 1):
            raise ValueError("unsupported index version; rebuild the index")
        if version == 1:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
            if not {"documents", "urls", "redirects", "body_fts"}.issubset(tables) or not {
                "id",
                "target",
                "source",
                "kind",
                "title",
                "body",
                "target_sha256",
                "source_sha256",
            }.issubset(columns):
                raise ValueError("file is not a recognized legacy relink index (archive-relink format)")
        if version == SCHEMA_VERSION:
            app = connection.execute("SELECT value FROM meta WHERE key='application'").fetchone()
            if not app or json.loads(app[0]) != "archive-relink":
                raise ValueError("file is not a relink index (archive-relink format)")
    except Exception:
        connection.close()
        raise
    return connection


def candidates(
    connection: sqlite3.Connection, url: str, quotes: list[str], limit: int = 20
) -> tuple[list[dict], bool]:
    found: dict[str, dict] = {}
    truncated = False
    priority = {"source_url": 0, "recorded_redirect": 1, "body_quote": 2, "declared_alias": 3}
    checks = [(text, normalized_text(text)) for text in dict.fromkeys(quotes)]
    checks = [
        (text, phrase) for text, phrase in checks if useful_quote(phrase) and len(phrase) <= 2000
    ][:20]

    def rank(candidate: dict) -> tuple:
        return (
            min(priority.get(e["kind"], 9) for e in candidate["evidence"]),
            candidate["target"],
            candidate["id"],
        )

    def add(identifier: str, item: dict) -> None:
        nonlocal truncated
        if identifier not in found:
            row = connection.execute(
                "SELECT id,target,source,kind,title,target_sha256,source_sha256,"
                "substr(body,1,240) AS preview "
                "FROM documents WHERE id=?",
                (identifier,),
            ).fetchone()
            if not row:
                return
            candidate = {**dict(row), "evidence": [item]}
            if len(found) >= limit + 1:
                truncated = True
                worst = max(found.values(), key=rank)
                if rank(candidate) >= rank(worst):
                    return
                del found[worst["id"]]
            found[identifier] = candidate
        elif item not in found[identifier]["evidence"]:
            if len(found[identifier]["evidence"]) < 20:
                found[identifier]["evidence"].append(item)
            else:
                truncated = True

    original = url_key(url)
    queue: deque = deque([(original, [])])
    seen = {original}
    while queue:
        key, chain = queue.popleft()
        matches = connection.execute(
            "SELECT document_id, MIN(CASE kind WHEN 'source_url' THEN 0 ELSE 1 END) AS priority "
            "FROM urls WHERE key=? GROUP BY document_id ORDER BY priority,document_id LIMIT ?",
            (key, limit + 1),
        ).fetchall()
        truncated |= len(matches) > limit
        for match in matches[:limit]:
            evidence_rows = connection.execute(
                "SELECT evidence FROM urls WHERE key=? AND document_id=? "
                "ORDER BY CASE kind WHEN 'source_url' THEN 0 ELSE 1 END LIMIT 21",
                (key, match["document_id"]),
            ).fetchall()
            truncated |= len(evidence_rows) > 20
            for row in evidence_rows[:20]:
                item = json.loads(row["evidence"])
                if chain:
                    item = {"kind": "recorded_redirect", "chain": chain, "target_evidence": item}
                add(match["document_id"], item)
        if len(chain) >= 8:
            if connection.execute(
                "SELECT 1 FROM redirects WHERE from_key=? LIMIT 1",
                (key,),
            ).fetchone():
                truncated = True
            continue
        edges = connection.execute(
            "SELECT to_key,evidence FROM redirects WHERE from_key=? LIMIT 101",
            (key,),
        ).fetchall()
        truncated |= len(edges) > 100
        for row in edges[:100]:
            if row["to_key"] not in seen:
                if len(seen) >= 256:
                    truncated = True
                    continue
                seen.add(row["to_key"])
                queue.append((row["to_key"], [*chain, json.loads(row["evidence"])]))

    def strong(candidate: dict) -> bool:
        return any(
            item["kind"] == "source_url"
            or (
                item["kind"] == "recorded_redirect"
                and item["target_evidence"]["kind"] == "source_url"
            )
            for item in candidate["evidence"]
        )

    strong_matches = [candidate for candidate in found.values() if strong(candidate)]
    corroborated = False
    if checks:
        for candidate in strong_matches:
            body = connection.execute(
                "SELECT body FROM documents WHERE id=?",
                (candidate["id"],),
            ).fetchone()["body"]
            if any(phrase in body for _, phrase in checks):
                corroborated = True
                break
    # A page-declared canonical URL is weaker than recorded provenance. Also
    # keep looking if an explicit quotation contradicts every URL capture.
    if not strong_matches or (checks and not corroborated):
        for text, phrase in checks:
            expression = '"' + phrase.replace('"', '""') + '"'
            rows = connection.execute(
                "SELECT document_id,body FROM body_fts WHERE body_fts MATCH ? LIMIT ?",
                (expression, limit + 1),
            ).fetchall()
            truncated |= len(rows) > limit
            for row in rows[:limit]:
                position = row["body"].find(phrase)
                if position < 0:
                    continue
                add(
                    row["document_id"],
                    {
                        "kind": "body_quote",
                        "quote": text,
                        "normalization": "NFC, casefold, collapse whitespace; exact substring",
                        "excerpt": row["body"][max(0, position - 70) : position + len(phrase) + 70],
                        "identity_verified": False,
                    },
                )
    ordered = sorted(found.values(), key=rank)
    truncated |= len(ordered) > limit
    selected = ordered[:limit]
    # A recorded URL can point to a login page, a later revision, or a soft
    # error. Show quotation corroboration/contradiction without hiding it.
    if checks:
        for candidate in selected:
            row = connection.execute(
                "SELECT body FROM documents WHERE id=?", (candidate["id"],)
            ).fetchone()
            body = row["body"]
            candidate["quote_checks"] = []
            for text, phrase in checks:
                position = body.find(phrase)
                candidate["quote_checks"].append(
                    {
                        "quote": text,
                        "found": position >= 0,
                        "excerpt": body[max(0, position - 70) : position + len(phrase) + 70]
                        if position >= 0
                        else None,
                    }
                )
    return selected, truncated


def scan_notes(
    database: Path,
    notes: list[Path],
    explicit_quotes: dict | None = None,
    only_urls: set[str] | None = None,
    limit: int = 20,
) -> dict:
    if not 1 <= limit <= 100:
        raise ValueError("candidate limit must be between 1 and 100")
    if len(notes) > 2000:
        raise ValueError("scan at most 2000 notes per review plan")
    quote_lookup: dict[str, list[str]] = {}
    for original, supplied in (explicit_quotes or {}).items():
        key = url_key(original)
        if not key:
            raise ValueError(f"quotation key is not a valid HTTP(S) URL: {original}")
        supplied = [supplied] if isinstance(supplied, str) else supplied
        if not isinstance(supplied, list) or not all(isinstance(q, str) for q in supplied):
            raise ValueError("quote values must be strings or lists of strings")
        quote_lookup.setdefault(key, []).extend(supplied)
    plan = {
        "schema": SCHEMA_VERSION,
        "tool": f"archive-relink {__version__}",
        "database": str(database.resolve()),
        "notes": [],
        "warnings": [],
        "links": [],
    }
    connection = open_index(database)
    total_candidates = 0
    plan_bytes = 0
    suppressed_note_warnings = 0
    try:
        index_warnings = connection.execute(
            "SELECT value FROM meta WHERE key='warnings'"
        ).fetchone()
        if index_warnings:
            plan["warnings"].extend(json.loads(index_warnings[0]))
        plan_bytes += len(json.dumps(plan, ensure_ascii=False).encode())
        for path in dict.fromkeys(p.resolve() for p in notes):
            raw = read_bounded(path)
            source = raw.decode("utf-8")
            sha = hashlib.sha256(raw).hexdigest()
            note_record = {"path": str(path), "sha256": sha}
            plan["notes"].append(note_record)
            plan_bytes += len(json.dumps(note_record, ensure_ascii=False).encode())
            links, warnings = citations(source)
            for warning in warnings:
                if len(plan["warnings"]) < 1000:
                    item = {"path": str(path)[:4096], "message": warning[:2000]}
                    plan["warnings"].append(item)
                    plan_bytes += len(json.dumps(item, ensure_ascii=False).encode())
                else:
                    suppressed_note_warnings += 1
            for citation in links:
                if only_urls is not None and url_key(citation.url) not in only_urls:
                    continue
                extra = quote_lookup.get(url_key(citation.url), [])
                options, truncated = candidates(
                    connection,
                    citation.url,
                    [*citation.quotes, *extra],
                    limit,
                )
                total_candidates += len(options)
                if len(plan["links"]) >= MAX_PLAN_LINKS or total_candidates > MAX_PLAN_CANDIDATES:
                    raise ValueError(
                        "review plan exceeds its bounded size; scan fewer notes, "
                        "use --only-urls, or lower --limit"
                    )
                identifier = (
                    "L"
                    + hashlib.sha256(
                        f"{path}\0{sha}\0{citation.start}\0{citation.end}".encode()
                    ).hexdigest()[:12]
                )
                status = (
                    "unresolved"
                    if not options
                    else ("ambiguous" if len(options) > 1 or truncated else "review_required")
                )
                entry = {
                    "id": identifier,
                    "note": str(path),
                    **citation.to_dict(),
                    "original": source[citation.start : citation.end],
                    "status": status,
                    "candidates_truncated": truncated,
                    "candidates": options,
                }
                plan_bytes += len(json.dumps(entry, ensure_ascii=False).encode())
                if plan_bytes > MAX_PLAN_BYTES:
                    raise ValueError(
                        "review plan exceeds its 32 MiB content budget; scan fewer notes, "
                        "use --only-urls, or lower --limit"
                    )
                plan["links"].append(entry)
    finally:
        connection.close()
    if suppressed_note_warnings:
        plan["warnings"].append(
            {
                "path": "notes",
                "message": f"{suppressed_note_warnings} further note warnings suppressed",
            }
        )
    if plan_bytes > MAX_PLAN_BYTES:
        raise ValueError("review plan exceeds its content budget; scan fewer notes")
    return plan


def _stat_key(path: Path) -> tuple:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _digest_once(path: Path, cache: dict) -> str:
    before = _stat_key(path)
    if path in cache and cache[path][0] == before:
        return cache[path][1]
    value = digest_file(path)
    if _stat_key(path) != before:
        raise ValueError(f"input changed while reading: {path}")
    cache[path] = (before, value)
    return value


def _verify_evidence(item: dict, cache: dict) -> None:
    if not isinstance(item, dict):
        raise ValueError("plan evidence entries must be JSON objects")
    if "source_file" in item:
        path = Path(item["source_file"])
        if not path.is_file() or _digest_once(path, cache) != item["source_sha256"]:
            raise ValueError(f"archive evidence changed; rebuild index and scan again: {path}")
    for edge in item.get("chain", []):
        _verify_evidence(edge, cache)
    if "target_evidence" in item:
        _verify_evidence(item["target_evidence"], cache)


def make_patch(plan: dict, selections: dict[str, str], base: Path, absolute: bool = False) -> str:
    if not isinstance(plan, dict):
        raise ValueError("plan must be a JSON object")
    if plan.get("schema") != SCHEMA_VERSION:
        raise ValueError("unsupported plan version")
    if not selections:
        raise ValueError("select at least one link=candidate pair from the plan")
    links = {link["id"]: link for link in plan["links"]}
    notes = {note["path"]: note for note in plan["notes"]}
    if len(links) != len(plan["links"]) or len(notes) != len(plan["notes"]):
        raise ValueError("plan has duplicate link IDs or note paths")
    edits: dict[str, list[tuple[int, int, str]]] = {}
    sources: dict[str, str] = {}
    parsed_sources: dict[str, dict] = {}
    source_stats: dict[Path, tuple] = {}
    file_digests: dict = {}
    base = base.resolve()
    for identifier, candidate_id in selections.items():
        if identifier not in links:
            raise ValueError(f"unknown link: {identifier}")
        link = links[identifier]
        matches = [c for c in link["candidates"] if c["id"] == candidate_id]
        if not matches:
            raise ValueError(f"candidate {candidate_id} does not belong to link {identifier}")
        if len(matches) > 1:
            raise ValueError(f"plan has duplicate candidate IDs for link {identifier}")
        candidate = matches[0]
        path = Path(link["note"])
        if str(path) not in sources:
            if not path.is_relative_to(base):
                raise ValueError(f"note is outside patch base: {path}")
            if any(c in str(path.relative_to(base)) for c in '\n\r\t"\\'):
                raise ValueError(
                    "patch paths containing control characters, quotes or backslashes "
                    "are unsupported"
                )
            source_stats[path] = _stat_key(path)
            raw = read_bounded(path)
            if hashlib.sha256(raw).hexdigest() != notes[str(path)]["sha256"]:
                raise ValueError(f"note changed since scan; scan again: {path}")
            if _stat_key(path) != source_stats[path]:
                raise ValueError(f"note changed while reading: {path}")
            sources[str(path)] = raw.decode("utf-8")
        source = sources[str(path)]
        if source[link["start"] : link["end"]] != link["original"]:
            raise ValueError(f"plan destination span is inconsistent: {identifier}")
        if str(path) not in parsed_sources:
            actual, _ = citations(source)
            parsed_sources[str(path)] = {(c.start, c.end, c.kind, c.url): c for c in actual}
        if (link["start"], link["end"], link["kind"], link["url"]) not in parsed_sources[str(path)]:
            raise ValueError(f"plan edit is not a current Markdown citation: {identifier}")
        target = Path(candidate["target"])
        if (
            target.resolve() != target
            or not target.is_file()
            or _digest_once(target, file_digests) != candidate["target_sha256"]
        ):
            raise ValueError(f"archive target changed; rebuild index and scan again: {target}")
        for item in candidate["evidence"]:
            _verify_evidence(item, file_digests)
        local = (
            target.as_uri()
            if absolute
            else quote(
                os.path.relpath(target, path.parent),
                safe="/~-._",
            )
        )
        fragment = urlsplit(link["url"]).fragment
        if not any(item["kind"] == "source_url" for item in candidate["evidence"]):
            redirect = next(
                (item for item in candidate["evidence"] if item["kind"] == "recorded_redirect"),
                None,
            )
            if redirect:
                for edge in redirect["chain"]:
                    # RFC 9110: an absent fragment inherits the original;
                    # an explicit fragment (including an empty '#') overrides it.
                    if "#" in edge.get("location", edge["to"]):
                        fragment = urlsplit(edge["to"]).fragment
        if fragment:
            local += "#" + quote(fragment, safe="%~.-_:")
        if link["kind"] == "autolink":
            label = (
                link["original"][1:-1].replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
            )
            replacement = f"[{label}]({local})"
        else:
            replacement = f"<{local}>" if link["original"].startswith("<") else local
        edits.setdefault(str(path), []).append((link["start"], link["end"], replacement))

    patches = []
    for filename, changes in sorted(edits.items()):
        old = sources[filename]
        if _stat_key(Path(filename)) != source_stats[Path(filename)]:
            raise ValueError(f"note changed while generating patch: {filename}")
        cursor = 0
        pieces = []
        for start, end, replacement in sorted(changes):
            if start < cursor:
                raise ValueError("overlapping selections in plan")
            pieces.extend([old[cursor:start], replacement])
            cursor = end
        pieces.append(old[cursor:])
        new = "".join(pieces)
        name = Path(filename).relative_to(base).as_posix()
        diff = difflib.unified_diff(
            re.findall(r"[^\n]*\n|[^\n]+$", old),
            re.findall(r"[^\n]*\n|[^\n]+$", new),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
            lineterm="\n",
        )
        for line in diff:
            patches.append(line)
            if not line.endswith("\n"):
                patches.append("\n\\ No newline at end of file\n")
    return "".join(patches)


def protect_outputs(outputs: list[Path | None], inputs: list[Path]) -> None:
    """Do not turn a mistaken output argument into destruction of an input."""
    for output in outputs:
        if output is not None and output.is_symlink():
            raise ValueError(f"output path must not be a symlink: {output}")
    resolved = [p.resolve() for p in outputs if p is not None]
    if len(resolved) != len(set(resolved)):
        raise ValueError("output paths must be distinct")
    protected = {p.resolve() for p in inputs}
    for path in resolved:
        if path in protected:
            raise ValueError(f"output would overwrite an input: {path}")


def plan_inputs(plan: dict) -> list[Path]:
    if not isinstance(plan, dict):
        raise ValueError("plan must be a JSON object")
    paths = [Path(plan["database"]), *(Path(note["path"]) for note in plan["notes"])]

    def provenance(item: dict) -> None:
        if not isinstance(item, dict):
            raise ValueError("plan evidence entries must be JSON objects")
        if "source_file" in item:
            paths.append(Path(item["source_file"]))
        for edge in item.get("chain", []):
            provenance(edge)
        if "target_evidence" in item:
            provenance(item["target_evidence"])

    for link in plan["links"]:
        for candidate in link["candidates"]:
            paths.extend([Path(candidate["target"]), Path(candidate["source"])])
            for item in candidate["evidence"]:
                provenance(item)
    return paths


def _code(value: str) -> str:
    value = str(value).replace("\r", "\\r").replace("\n", "\\n")
    longest = max((len(match[0]) for match in re.finditer(r"`+", value)), default=0)
    fence = "`" * (longest + 1)
    return f"{fence} {value} {fence}"


def _prose(value: str) -> str:
    import html

    escaped = html.escape(str(value), quote=False)
    return re.sub(r"([\\`*_{}\[\]#|])", r"\\\1", escaped)


def review_markdown(plan: dict) -> str:
    lines = [
        "# Local archive repair review",
        "",
        "No notes have been changed.",
        "",
        "Choose a link ID and candidate ID explicitly with the patch command. "
        "A recorded address is metadata; quotation checks help reveal incomplete "
        "captures or a changed page. Body quotations are candidates, not proof of identity.",
        "",
    ]
    for link in plan["links"]:
        lines += [
            f"## {link['id']} — {link['status']}",
            "",
            f"Note: {_code(link['note'])} line {link['line']}",
            "",
            f"Original URL: {_code(link['url'])}",
            "",
        ]
        if not link["candidates"]:
            lines += ["No local candidate found. An explicit quotation may help.", ""]
        for candidate in link["candidates"]:
            local_uri = Path(candidate["target"]).as_uri()
            lines += [
                f"### {candidate['id']}",
                "",
                f"[Open local copy](<{local_uri}>) — {_code(candidate['kind'])}",
                "",
                f"Title: {_code(candidate['title'])}",
                "",
                f"Path: {_code(candidate['target'])}",
                "",
            ]
            if candidate.get("preview"):
                lines += ["> " + _prose(candidate["preview"]), ""]
            for item in candidate["evidence"]:
                kind = item["kind"]
                if kind == "recorded_redirect":
                    lines += ["- Recorded redirect chain:"]
                    for edge in item["chain"]:
                        lines += [
                            f"  - {edge['status']}: {_code(edge['from'])} → {_code(edge['to'])}"
                        ]
                        lines += [f"    Source: {_code(edge['source'])}"]
                elif kind == "body_quote":
                    lines += [
                        f"- Exact normalized body quotation: {_code(item['quote'])}",
                        "  Identity remains unverified.",
                    ]
                else:
                    description = (
                        "Recorded original URL" if kind == "source_url" else "Page-declared alias"
                    )
                    lines += [
                        f"- {description}: {_code(item['url'])}",
                        f"  Source: {_code(item['source'])}",
                    ]
            lines.append("")
            for check in candidate.get("quote_checks", []):
                result = "FOUND in body" if check["found"] else "NOT FOUND in body"
                lines += [f"- Quotation check: **{result}** — {_code(check['quote'])}"]
                if check["excerpt"]:
                    lines += ["", "> " + _prose(check["excerpt"]), ""]
            lines.append("")
        if link["candidates_truncated"]:
            lines += [
                "Search or evidence was truncated; more candidates may exist. "
                "Narrow the archive/quotation or raise --limit.",
                "",
            ]
    if plan["warnings"]:
        lines += ["## Warnings", ""]
        lines += [f"- {_code(w['path'])}: {_prose(w['message'])}" for w in plan["warnings"]]
        lines.append("")
    return "\n".join(lines)
