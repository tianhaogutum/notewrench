# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (see licenses/browser-bookmarks-MIT.txt)
"""Metadata-preserving adapter around Beautiful Soup's HTML5 parser."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Comment, Doctype, NavigableString, Tag
from webencodings import lookup

# ReviewError lives in a dependency-free module so the CLI can report it without
# importing Beautiful Soup; it is re-exported here for the rest of the package.
from .errors import ReviewError

MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_ANCHORS = 25_000
MAX_DL_DEPTH = 128
MAX_TAGS = 100_000
MAX_ELEMENT_DEPTH = 256
NOTES_OPEN = "[linkding-notes]"
NOTES_CLOSE = "[/linkding-notes]"


def read_limited(path: Path, limit: int = MAX_INPUT_BYTES) -> bytes:
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ReviewError(f"{path}: input must be a regular file (not a pipe, device, or directory)")
            data = handle.read(limit + 1)
    except OSError as exc:
        raise ReviewError(f"Cannot read {path}: {exc.strerror or exc}") from exc
    if len(data) > limit:
        raise ReviewError(f"{path}: exceeds the {limit:,}-byte input limit")
    return data


def url_key(url: str) -> tuple[str, list[str]]:
    """Only normalize HTTP(S) scheme/host, default port, and an empty path.

    Preserve fragment, query order, tracking parameters, slash, percent escapes,
    HTTP-vs-HTTPS, user information, and all opaque/non-HTTP URL spellings.
    """
    if not url or url != url.strip() or re.search(r"[\x00-\x20\x7f]", url):
        return "literal:" + url, ["URL has whitespace/control characters; exact matching only"]
    try:
        parts = urlsplit(url)
        if parts.scheme.lower() not in {"http", "https"}:
            return "literal:" + url, ["Non-HTTP(S) URL; exact matching only"]
        if not parts.hostname or parts.username is not None or parts.password is not None:
            return "literal:" + url, ["Missing host or URL credentials; exact matching only"]
        host = parts.hostname.lower()
        if parts.netloc.startswith("[") and re.match(r"v[0-9a-f]+\.", host):
            return "literal:" + url, ["IPvFuture host; exact matching only"]
        if "%" in host or parts.netloc.endswith(":"):
            return "literal:" + url, ["Unusual host/port spelling; exact matching only"]
        port = parts.port  # validates range and syntax
        if ":" in host:
            host = f"[{host}]"
        scheme = parts.scheme.lower()
        if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}:
            host += f":{port}"
        key = f"{scheme}://{host}{parts.path or '/'}"
        before_fragment = url.split("#", 1)[0]
        if "?" in before_fragment:
            key += "?" + parts.query
        if "#" in url:
            key += "#" + parts.fragment
        return "web:" + key, []
    except ValueError:
        return "literal:" + url, ["Malformed URL; exact matching only"]


def _events(root: Tag):
    """Iterative DOM walk: keep folder boundaries without recursive Python calls."""
    stack = [(root, False)]
    while stack:
        node, ending = stack.pop()
        if not isinstance(node, Tag):
            continue
        yield node, ending
        if not ending:
            stack.append((node, True))
            stack.extend((child, False) for child in reversed(node.contents) if isinstance(child, Tag))


def _dd_text(node: Tag) -> str:
    parts: list[str] = []
    stack = list(reversed(node.contents))
    while stack:
        child = stack.pop()
        if child is None:
            parts.append("\n")
            continue
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            parts.append(str(child))
        elif isinstance(child, Tag):
            if child.name == "br":
                parts.append("\n")
            elif child.name in {"p", "div", "li", "dl", "dt", "dd", "h1", "h2", "h3", "h4", "h5", "h6"}:
                if parts:
                    parts.append("\n")
                stack.append(None)
            stack.extend(reversed(child.contents))
    return "".join(parts).strip()


def split_linkding_notes(text: str, kind: str) -> tuple[str, str, list[str]]:
    if kind != "linkding" or NOTES_OPEN not in text:
        return text, "", []
    start = text.find(NOTES_OPEN)
    end = text.find(NOTES_CLOSE, start + len(NOTES_OPEN))
    if end < 0 or text.count(NOTES_OPEN) != 1 or text.count(NOTES_CLOSE) != 1:
        return text, "", ["Ambiguous linkding note markers; kept entire DD as description"]
    notes = text[start + len(NOTES_OPEN) : end]
    tail = text[end + len(NOTES_CLOSE) :]
    warnings = ["Text after linkding note marker retained in description"] if tail else []
    return text[:start] + tail, notes, warnings


class _ComplexityBudget(HTMLParser):
    """Cheap lexical limits before allocating an HTML5 DOM."""

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.anchors = 0
        self.depth = 0
        self.tags = 0
        self.containers = []

    def handle_starttag(self, tag, attrs):
        self.tags += 1
        if self.tags > MAX_TAGS:
            raise ReviewError(f"Input exceeds the {MAX_TAGS:,} HTML-tag complexity limit")
        optional_or_void = {
            "html", "head", "body", "p", "li", "dt", "dd", "rt", "rp", "optgroup", "option",
            "colgroup", "thead", "tbody", "tfoot", "tr", "td", "th", "area", "base", "br",
            "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr",
        }
        if tag not in optional_or_void:
            self.containers.append(tag)
            if len(self.containers) > MAX_ELEMENT_DEPTH:
                raise ReviewError(f"Input exceeds the {MAX_ELEMENT_DEPTH}-level HTML nesting limit")
        if tag == "a":
            self.anchors += 1
            if self.anchors > MAX_ANCHORS:
                raise ReviewError(f"Input exceeds the {MAX_ANCHORS:,} HTML-anchor complexity limit")
        elif tag == "dl":
            self.depth += 1
            if self.depth > MAX_DL_DEPTH:
                raise ReviewError(f"Input exceeds the {MAX_DL_DEPTH}-level DL nesting limit")

    def handle_endtag(self, tag):
        if tag == "dl":
            self.depth = max(0, self.depth - 1)
        if self.containers and self.containers[-1] == tag:
            self.containers.pop()
        elif tag in self.containers:
            reverse_index = self.containers[::-1].index(tag)
            del self.containers[len(self.containers) - reverse_index - 1 :]


def _check_complexity(data: bytes) -> None:
    # Markup bytes are ASCII in UTF-8 and common legacy encodings. A UTF-16 BOM
    # needs decoding before the lexical count; real decoding is validated below.
    try:
        lexical = data.decode("utf-16", "strict") if data.startswith((b"\xff\xfe", b"\xfe\xff")) else data.decode("latin-1")
    except UnicodeError as exc:
        raise ReviewError("Invalid UTF-16 input") from exc
    counter = _ComplexityBudget()
    counter.feed(lexical)
    counter.close()


def parse_html(data: bytes, source_id: str, kind: str) -> tuple[list[dict], list[str]]:
    if not isinstance(kind, str) or kind not in {"html", "linkding"}:
        raise ReviewError(f"Unsupported input format: {kind}")
    _check_complexity(data)
    soup = BeautifulSoup(data, "html5lib")
    try:
        if soup.contains_replacement_characters:
            raise ReviewError(f"{source_id}: decoding lost characters; export UTF-8 HTML")
        # html5lib performs replacement decoding without setting Beautiful Soup's
        # contains_replacement_characters flag. Validate the chosen encoding
        # strictly so an invalid UTF-8 byte cannot silently corrupt note text.
        encoding = lookup(soup.original_encoding or "utf-8")
        try:
            if encoding is None:
                raise UnicodeError("unsupported encoding")
            decoded = encoding.codec_info.decode(data, "strict")[0]
        except UnicodeError as exc:
            raise ReviewError(f"{source_id}: invalid {soup.original_encoding or 'UTF-8'} bytes; export valid HTML") from exc
        if "\x00" in decoded:
            raise ReviewError(f"{source_id}: NUL characters are not supported; export valid HTML")
        netscape = any(isinstance(node, Doctype) and str(node).strip().lower().split(None, 1)[:1] == ["netscape-bookmark-file-1"] for node in soup.contents)
        if netscape:
            # Real Safari exports omit the outer DL, including when H3 folders
            # precede child DLs. A neutral DOM root also handles already-wrapped
            # Chrome/Firefox/linkding exports without inventing a folder name.
            body = soup.body
            root = soup.new_tag("dl")
            for child in list(body.contents):
                root.append(child.extract())
            body.append(root)
        if soup.find("dl") is None:
            raise ReviewError(f"{source_id}: expected Netscape bookmark HTML (DOCTYPE or DL)")
        records: list[dict] = []
        warnings: list[str] = []
        contexts: list[dict] = []
        active_description = None
        for tag, ending in _events(soup):
            if active_description is not None:
                if tag is active_description and ending:
                    active_description = None
                # An attached DD is one description. Nested definition lists,
                # headings and links are content, not bookmark/folder records.
                continue
            if tag.name == "dl":
                if ending:
                    contexts.pop()
                else:
                    parent = contexts[-1] if contexts else None
                    path = list(parent["path"]) if parent else []
                    if parent and parent["pending"] is not None:
                        path.append(parent["pending"])
                        parent["pending"] = None
                    contexts.append({"path": path, "pending": None, "last": None})
                continue
            if ending:
                continue
            description_parent = tag.find_parent("dd") if tag.name in {"a", "h3"} else None
            if description_parent is not None and description_parent.find_parent("dl") is tag.find_parent("dl"):
                # Inline HTML links/headings inside a DD are description content,
                # not new bookmark/folder records. A child folder DL is separate.
                continue
            if tag.name == "a":
                if not contexts:
                    raise ReviewError(f"{source_id}: bookmark anchor outside DL; refusing to silently skip it")
                if not tag.has_attr("href") or not str(tag["href"]):
                    raise ReviewError(f"{source_id}: bookmark #{len(records) + 1} has no HREF")
                attrs = {key: list(value) if isinstance(value, list) else str(value) for key, value in tag.attrs.items()}
                url = str(attrs["href"])
                key, record_warnings = url_key(url)
                record = {
                    "id": f"{source_id}:{len(records) + 1}",
                    "source_id": source_id,
                    "index": len(records) + 1,
                    "url": url,
                    "key": key,
                    "title": tag.get_text(),
                    "description": "",
                    "notes": "",
                    "tags": [value for value in attrs.get("tags", "").split(",") if value],
                    "folder": list(contexts[-1]["path"]),
                    "added": attrs.get("add_date"),
                    "modified": attrs.get("last_modified"),
                    "private": attrs.get("private"),
                    "toread": attrs.get("toread"),
                    "attributes": attrs,
                    "dd_html": [],
                    "warnings": record_warnings,
                }
                for flag in ("private", "toread"):
                    if record[flag] not in {None, "0", "1"}:
                        record_warnings.append(f"Unrecognized {flag.upper()} value; original retained in receipt")
                records.append(record)
                contexts[-1]["last"] = record
                contexts[-1]["pending"] = None
            elif tag.name == "h3" and contexts:
                contexts[-1]["pending"] = tag.get_text()
                contexts[-1]["last"] = None
            elif tag.name == "dd" and contexts and contexts[-1]["last"] is not None:
                record = contexts[-1]["last"]
                text = _dd_text(tag)
                description, notes, extra_warnings = split_linkding_notes(text, kind)
                if kind == "html" and (NOTES_OPEN in text or NOTES_CLOSE in text):
                    extra_warnings.append("Linkding note markers found in browser HTML; use --linkding for a linkding export to separate its notes")
                if record["dd_html"]:
                    record["warnings"].append("Multiple DD descriptions combined in source order")
                record["dd_html"].append(str(tag))
                for field, value in (("description", description), ("notes", notes)):
                    if value:
                        record[field] += ("\n" if record[field] else "") + value
                record["warnings"].extend(extra_warnings)
                active_description = tag
        if not records:
            warnings.append("Empty bookmark export (no anchors)")
        return records, warnings
    finally:
        soup.decompose()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_file(path: Path, limit: int = 128 * 1024 * 1024) -> str:
    """Bounded streaming hash for large receipts and exports."""
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ReviewError(f"{path}: expected a regular file")
        while chunk := handle.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                raise ReviewError(f"{path}: exceeds the {limit:,}-byte verification limit")
            digest.update(chunk)
    return digest.hexdigest()
