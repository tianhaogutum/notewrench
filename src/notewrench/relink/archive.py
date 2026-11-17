# SPDX-License-Identifier: MIT
# Copyright (c) 2026 archive-relink contributors
"""Bounded, local-only adapters for existing web archive formats."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from email import policy
from email.parser import BytesParser
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment
from markdown_it import MarkdownIt
from warcio.archiveiterator import ArchiveIterator
from warcio.exceptions import ArchiveLoadFailed

from .urls import normalized_text, url_key

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_TEXT_CHARS = 1024 * 1024
HTML_SUFFIXES = {".html", ".htm", ".xhtml"}


class ArchiveChangedError(RuntimeError):
    """An input changed between parsing and fingerprinting; rebuild consistently."""


def _regular_stream(path: Path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError(f"input is not a regular file: {path}")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with _regular_stream(path) as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def confined(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root.resolve()) and not path.is_symlink()
    except (OSError, ValueError):
        return False


def read_bounded(path: Path, limit: int = MAX_FILE_BYTES) -> bytes:
    with _regular_stream(path) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"file exceeds {limit} bytes")
    return data


@dataclass
class Document:
    target: Path
    source: Path
    kind: str
    title: str
    body: str
    urls: list[dict] = field(default_factory=list)
    parsed_sha256: str | None = None


def evidence(url: str, kind: str, source: Path | str) -> dict | None:
    key = url_key(url)
    if not key:
        return None
    item = {"url": url, "key": key, "kind": kind, "source": str(source)}
    if isinstance(source, Path):
        item["source_file"] = str(source.resolve())
    return item


def html_document(
    data: bytes,
    target: Path,
    source: Path | None = None,
    encoding: str | None = None,
) -> Document:
    soup = BeautifulSoup(data, "html.parser", from_encoding=encoding)
    source = source or target
    title_tag = soup.title or soup.h1
    title = title_tag.get_text(" ", strip=True) if title_tag else target.name
    urls: list[dict] = []
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        text = str(comment)
        if "Page saved with SingleFile" in text or "SingleFile" in text[:60]:
            match = re.search(r"(?:^|\n)\s*url:\s*(https?://[^\r\n]+)", text)
            if match and (item := evidence(match[1].strip(), "source_url", source)):
                urls.append(item)
        match = re.search(r"saved from url=\(\d+\)(https?://\S+)", text, re.I)
        if match and (item := evidence(match[1], "source_url", source)):
            urls.append(item)
    for node in soup.find_all("link", href=True):
        if "canonical" in [str(rel).lower() for rel in node.get("rel", [])]:
            if item := evidence(str(node["href"]), "declared_alias", source):
                urls.append(item)
    for node in soup.find_all("meta"):
        if str(node.get("property", "")).lower() == "og:url" and node.get("content"):
            if item := evidence(str(node["content"]), "declared_alias", source):
                urls.append(item)
    # Metadata and navigation are not the cited article. In particular a page
    # with a similar title/heading alone must never become a quotation match.
    for node in soup.find_all(
        [
            "head",
            "title",
            "script",
            "style",
            "noscript",
            "template",
            "nav",
            "header",
            "footer",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
        ]
    ):
        node.decompose()
    body = normalized_text(soup.get_text(" ", strip=True))[:MAX_TEXT_CHARS]
    return Document(
        target,
        source,
        "html",
        title[:1000],
        body,
        urls,
        hashlib.sha256(data).hexdigest(),
    )


def nb_document(data: bytes, target: Path) -> Document | None:
    source = data.decode("utf-8-sig")
    tokens = MarkdownIt("commonmark").parse(source)
    title = target.name
    original = None
    section = ""
    body: list[str] = []
    heading = None
    for token in tokens:
        if token.type == "heading_open":
            heading = token.tag
        elif token.type == "inline":
            if heading:
                if heading == "h1" and not section:
                    title = token.content
                elif heading == "h2" and token.content.casefold() in {
                    "description",
                    "quote",
                    "comment",
                    "related",
                    "tags",
                    "content",
                    "source",
                }:
                    section = token.content.casefold()
                heading = None
                continue
            for child in token.children or []:
                if child.type == "link_open" and child.markup == "autolink" and original is None:
                    original = child.attrGet("href")
            if section in {"content", "quote"}:
                body.append(
                    " ".join(
                        c.content for c in token.children or [] if c.type in {"text", "code_inline"}
                    )
                )
        elif token.type in {"fence", "code_block"} and section in {"content", "quote"}:
            body.append(token.content)
        elif token.type == "fence" and section == "source" and token.info.strip() == "html":
            # nb can store only ## Source when no HTML-to-Markdown converter
            # is installed. This is still the captured article, not note prose.
            body.append(html_document(token.content.encode("utf-8"), target).body)
    if not original or not (item := evidence(original, "source_url", target)):
        return None
    return Document(
        target,
        target,
        "nb_bookmark",
        title[:1000],
        normalized_text(" ".join(body))[:MAX_TEXT_CHARS],
        [item],
        hashlib.sha256(data).hexdigest(),
    )


def mhtml_document(
    data: bytes,
    target: Path,
    warn: Callable[[Path, str], None] | None = None,
) -> Document:
    """Let Python's MIME parser select the related root, not an iframe/attachment."""
    message = BytesParser(policy=policy.default).parsebytes(data)
    if message.get_content_type() == "multipart/related" and message.get_param("start"):
        start = message.get_param("start")
        if not any(part.get("Content-ID") == start for part in message.iter_parts()):
            # email.get_body() falls back to the first part for a missing ID.
            # In an incomplete browser capture that may be an unrelated iframe.
            raise ValueError("MHTML declared main part is missing; refusing iframe fallback")
    root = message.get_body(preferencelist=("html",))
    if root is None:
        raise ValueError("MHTML has no usable main HTML body")
    payload = root.get_payload(decode=True)
    if not isinstance(payload, bytes):
        raise ValueError("MHTML main body is not a decodable MIME payload")
    doc = html_document(payload, target, encoding=root.get_content_charset())
    doc.kind = "mhtml"
    doc.parsed_sha256 = hashlib.sha256(data).hexdigest()
    if doc.title == target.name and message.get("Subject"):
        doc.title = str(message["Subject"])[:1000]
    if warn:
        for defect in [*message.defects, *root.defects]:
            warn(target, f"MHTML recovered with MIME defect: {type(defect).__name__}")
    # Snapshot-Content-Location is a Chromium provenance field. The chosen
    # root's own Content-Location is RFC 2557 metadata. No child URL is claimed.
    for location in (
        root.get("Content-Location"),
        message.get("Snapshot-Content-Location"),
    ):
        if location and (item := evidence(str(location), "source_url", target)):
            if item not in doc.urls:
                doc.urls.append(item)
    return doc


def files(
    root: Path,
    exclude: set[Path],
    warn: Callable[[Path, str], None] | None = None,
) -> Iterator[Path]:
    def on_error(error: OSError) -> None:
        if Path(error.filename or root).resolve() == root.resolve():
            raise error
        if warn:
            warn(Path(error.filename or root), f"cannot read directory: {error}")
        else:
            raise error

    for parent, dirs, names in os.walk(root, followlinks=False, onerror=on_error):
        dirs[:] = sorted(
            d
            for d in dirs
            if not d.startswith(".")
            and d not in {"node_modules", "vendor-tmp"}
            and not (Path(parent) / d).is_symlink()
            and (Path(parent) / d).resolve() not in exclude
        )
        for name in sorted(names):
            path = Path(parent) / name
            if not name.startswith(".") and confined(path, root) and path.resolve() not in exclude:
                if path.is_file():
                    yield path
                elif warn:
                    warn(path, "skipped non-regular file")


def _metadata_records(path: Path, warn: Callable[[Path, str], None]) -> Iterator[dict]:
    if path.name == "index.jsonl":
        source_digest = digest_file(path)
        with path.open("rb") as stream:
            line_number = 0
            while line := stream.readline(MAX_FILE_BYTES + 1):
                line_number += 1
                if len(line) > MAX_FILE_BYTES:
                    while not line.endswith(b"\n"):
                        line = stream.readline(MAX_FILE_BYTES + 1)
                        if not line:
                            break
                    warn(path, f"JSONL line {line_number} exceeds size limit; skipped")
                    continue
                if line.strip():
                    try:
                        value = json.loads(line)
                    except (ValueError, UnicodeError) as exc:
                        warn(path, f"invalid JSONL line {line_number}: {exc}")
                        continue
                    if isinstance(value, dict):
                        yield {**value, "_metadata_sha256": source_digest}
    else:
        raw = read_bounded(path)
        source_digest = hashlib.sha256(raw).hexdigest()
        value = json.loads(raw)
        if isinstance(value, dict) and "url" in value:
            yield {**value, "_metadata_sha256": source_digest}
        else:
            links = (
                value
                if isinstance(value, list)
                else (value.get("links") if isinstance(value, dict) else None)
            )
            if isinstance(links, list):
                for link in links:
                    if not isinstance(link, dict) or not isinstance(link.get("url"), str):
                        continue
                    timestamp = str(link.get("timestamp", ""))
                    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", timestamp):
                        warn(path, "collection entry has no safe ArchiveBox timestamp; skipped")
                        continue
                    yield {
                        **link,
                        "_collection_timestamp": timestamp,
                        "_metadata_sha256": source_digest,
                    }


def _result_targets(record: dict, parent: Path) -> list[Path]:
    """Resolve main HTML outputs from ArchiveBox's published result schema."""
    plugin = record.get("plugin")
    if plugin not in {"singlefile", "dom", "wget", "readability", "mercury"}:
        return []
    if record.get("status") != "succeeded":
        return []
    output_files = record.get("output_files", {})
    if not isinstance(output_files, dict):
        output_files = {}
    outputs = []
    raw = record.get("output_str", record.get("output"))
    if isinstance(raw, str) and raw and "\n" not in raw and "://" not in raw:
        if raw.startswith(f"{plugin}/"):
            outputs.append(parent / raw)
        else:
            outputs.extend([parent / plugin / raw, parent / raw])
    if any(p.is_file() for p in outputs):
        return outputs
    preferred = {
        "index.html",
        "index.htm",
        "singlefile.html",
        "output.html",
        "content.html",
        "article.html",
    }
    for filename, metadata in output_files.items():
        if not isinstance(filename, str) or Path(filename).name.lower() not in preferred:
            continue
        root_relative = isinstance(metadata, dict) and metadata.get("root_relative") is True
        base = parent if root_relative or filename.startswith(f"{plugin}/") else parent / plugin
        outputs.append(base / filename)
    return outputs


class ArchiveMetadata(dict):
    def __init__(self):
        super().__init__()
        self.navigation: set[Path] = set()


def metadata_targets(
    root: Path,
    warn: Callable[[Path, str], None],
    exclude: set[Path],
) -> ArchiveMetadata:
    """Map actual content paths, never ArchiveBox's generated index UI, to URLs."""
    result = ArchiveMetadata()
    for path in files(root, exclude, warn):
        if path.name not in {"index.json", "index.jsonl"}:
            continue
        try:
            snapshots = {}
            for record in _metadata_records(path, warn):
                try:
                    original = record.get("url")
                    if record.get("type") == "Snapshot" and isinstance(original, str):
                        snapshots[str(record.get("id"))] = original
                    if record.get("type") == "ArchiveResult":
                        original = snapshots.get(str(record.get("snapshot_id")))
                    if not isinstance(original, str) or not url_key(original):
                        continue
                    parent = path.parent
                    if "_collection_timestamp" in record:
                        result.navigation.add((path.parent / "index.html").resolve())
                        timestamp = record["_collection_timestamp"]
                        if not isinstance(timestamp, str) or not re.fullmatch(
                            r"[0-9]+(?:\.[0-9]+)?",
                            timestamp,
                        ):
                            warn(path, "unsafe collection timestamp; skipped")
                            continue
                        parent = path.parent / "archive" / timestamp
                        if not confined(parent, root):
                            warn(path, "collection snapshot escapes archive root; skipped")
                            continue
                    targets: list[Path] = []
                    # Current extractor resource manifest: actual recorded path.
                    if record.get("type") == "ArchiveResult":
                        targets.extend(_result_targets(record, parent))
                    elif isinstance(record.get("path"), str):
                        status = record.get("status", 200)
                        if status != 200:
                            continue
                        targets.append(parent / record["path"])
                    else:
                        # Snapshot indexes (legacy and JSONL Snapshot objects).
                        if record.get("type") not in {
                            None,
                            "Snapshot",
                            "core.snapshot",
                            "snapshot",
                        }:
                            continue
                        result.navigation.add((parent / "index.html").resolve())
                        targets += [
                            parent / p
                            for p in (
                                "singlefile.html",
                                "output.html",
                                "readability/content.html",
                                "singlefile/singlefile.html",
                                "singlefile/index.html",
                                "dom/output.html",
                                "dom/index.html",
                            )
                        ]
                        canonical = record.get("canonical", {})
                        if isinstance(canonical, dict):
                            targets += [
                                parent / p for p in canonical.values() if isinstance(p, str)
                            ]
                        history = record.get("history", {})
                        if isinstance(history, dict):
                            for name in ("wget", "singlefile", "dom", "readability"):
                                entries = history.get(name, [])
                                if not isinstance(entries, list):
                                    continue
                                for entry in entries:
                                    if (
                                        isinstance(entry, dict)
                                        and entry.get("status") == "succeeded"
                                    ):
                                        output = entry.get("output")
                                        if isinstance(output, str):
                                            targets.append(parent / output)
                        for item in record.get("archive_results", []):
                            if isinstance(item, dict):
                                targets.extend(_result_targets(item, parent))
                    for target in targets:
                        if target.suffix.lower() not in HTML_SUFFIXES:
                            continue
                        if target.resolve() in result.navigation:
                            continue
                        if not confined(target, root):
                            warn(path, f"content path escapes archive root: {target}")
                            continue
                        if target.is_file():
                            item = evidence(original, "source_url", path)
                            item["source_sha256"] = record["_metadata_sha256"]
                            result.setdefault(target.resolve(), []).append(item)
                except (OSError, ValueError, TypeError, RecursionError) as exc:
                    warn(path, f"invalid metadata record: {exc}")
        except (OSError, ValueError, TypeError, RecursionError) as exc:
            warn(path, f"invalid metadata: {exc}")
    return result


def warc_documents(
    path: Path, cache: Path, warn: Callable[[Path, str], None]
) -> Iterator[Document]:
    """Stream records; materialize only bounded HTML bodies for direct backlinks."""
    source_digest = digest_file(path)
    with path.open("rb") as stream:
        for index, record in enumerate(ArchiveIterator(stream)):
            if record.rec_type != "response" or not record.http_headers:
                continue
            url = record.rec_headers.get_header("WARC-Target-URI")
            if not url or not url_key(url):
                continue
            status = record.http_headers.get_statuscode()
            if status != "200":
                continue
            media_type = (record.http_headers.get_header("Content-Type") or "").lower()
            if not any(t in media_type for t in ("text/html", "application/xhtml+xml")):
                continue
            payload = record.content_stream().read(MAX_FILE_BYTES + 1)
            if len(payload) > MAX_FILE_BYTES:
                warn(path, f"WARC HTML record {index} exceeds size limit")
                continue
            identifier = hashlib.sha256(
                str(path).encode() + str(index).encode() + payload
            ).hexdigest()
            target = cache / f"{identifier}.html"
            cache.mkdir(parents=True, exist_ok=True, mode=0o700)
            if target.is_symlink():
                warn(path, f"refusing symlink in materialized assets: {target}")
                continue
            if not target.exists():
                descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(payload)
            elif digest_file(target) != hashlib.sha256(payload).hexdigest():
                warn(path, f"materialized asset was changed; remove it and reindex: {target}")
                continue
            doc = html_document(payload, target, path)
            doc.kind = "warc_response"
            original = evidence(url, "source_url", path)
            original["record_index"] = index
            original["source_sha256"] = source_digest
            doc.urls.append(original)
            yield doc


def warc_redirects(path: Path) -> Iterator[dict]:
    with path.open("rb") as stream:
        for index, record in enumerate(ArchiveIterator(stream)):
            if record.rec_type != "response" or not record.http_headers:
                continue
            status = record.http_headers.get_statuscode()
            original = record.rec_headers.get_header("WARC-Target-URI")
            location = record.http_headers.get_header("Location")
            if status in {"301", "302", "303", "307", "308"} and original and location:
                destination = urljoin(original, location)
                if url_key(original) and url_key(destination):
                    yield {
                        "from": original,
                        "to": destination,
                        "location": location,
                        "status": status,
                        "source": f"{path}#record={index}",
                    }


def documents(
    root: Path, cache: Path, warn: Callable[[Path, str], None], exclude: set[Path]
) -> Iterator[Document]:
    metadata = metadata_targets(root, warn, exclude)
    for path in files(root, exclude, warn):
        try:
            suffix = path.suffix.lower()
            if suffix in HTML_SUFFIXES:
                # ArchiveBox navigation indexes are not saved page bodies.
                if path.resolve() in metadata.navigation:
                    continue
                doc = html_document(read_bounded(path), path)
                doc.urls.extend(metadata.get(path.resolve(), []))
                yield doc
            elif path.name.endswith(".bookmark.md"):
                if doc := nb_document(read_bounded(path), path):
                    yield doc
            elif suffix in {".mhtml", ".mht"}:
                yield mhtml_document(read_bounded(path), path, warn)
            elif path.name.endswith((".warc", ".warc.gz")):
                yield from warc_documents(path, cache, warn)
        except (
            OSError,
            ValueError,
            TypeError,
            UnicodeError,
            EOFError,
            ArchiveLoadFailed,
            RecursionError,
            LookupError,
        ) as exc:
            warn(path, f"cannot index: {type(exc).__name__}: {exc}")
