# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Library Bridge contributors (see licenses/zotero-dupes-MIT.txt)
"""Stable source-scoped identities and preservation-oriented record model."""

from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote, urlsplit

import isbnlib


class InputError(ValueError):
    """An actionable input problem, suitable for a CLI message."""


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def stable_id(prefix: str, *parts: str) -> str:
    return prefix + "-" + digest(parts)[:24]


def zotero_item_key(uri: Any) -> str:
    if not isinstance(uri, str):
        return ""
    parsed = urlsplit(uri)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"zotero.org", "www.zotero.org", "api.zotero.org"}:
        return ""
    match = re.fullmatch(r"/(?:users/(?:\d+|local/[^/]+)|groups/\d+)/items/([A-Z0-9]{8})/?", parsed.path)
    return match.group(1) if match else ""


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in {"script", "style"}:
            self.hidden += 1
            return
        if self.hidden:
            return
        if tag in {"br", "p", "div", "li"}:
            self.parts.append("\n")
        if tag == "img":
            alt = dict(attrs).get("alt")
            self.parts.append(f"[Image: {alt}]" if alt else "[Image reference retained in exact export]")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1
            return
        if self.hidden:
            return
        if tag in {"p", "div", "li"}:
            self.parts.append("\n")


def plain_text(value: Any) -> str:
    text = value if isinstance(value, str) else str(value or "")
    parser = _Text()
    parser.feed(text)
    return " ".join(html.unescape("".join(parser.parts)).split())


def readable_note(value: Any) -> str:
    if isinstance(value, dict):
        body = value.get("data", value)
        if not isinstance(body, dict) or not isinstance(body.get("note"), str):
            return json.dumps(value, ensure_ascii=False, indent=2)
        value = body["note"]
    parser = _Text()
    parser.feed(str(value))
    return "\n".join(line.strip() for line in html.unescape("".join(parser.parts)).splitlines() if line.strip())


def normalized_title(value: str) -> str:
    text = unicodedata.normalize("NFKC", plain_text(value)).casefold()
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())


def canonical_item_type(value: str) -> str:
    """Equate known CSL/Zotero types, without conflating different item kinds."""
    return {"article-journal": "journalArticle", "article-magazine": "magazineArticle",
            "article-newspaper": "newspaperArticle", "chapter": "bookSection",
            "paper-conference": "conferencePaper", "entry-encyclopedia": "encyclopediaArticle",
            "entry-dictionary": "dictionaryEntry", "post-weblog": "blogPost",
            "software": "computerProgram", "legal_case": "case", "legislation": "statute",
            "speech": "presentation"}.get(value, value)


def normalize_doi(value: Any) -> str:
    text = unquote(str(value or "").strip()).casefold()
    text = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", "", text)
    return text if re.fullmatch(r"10\.\d{4,9}/\S+", text) else ""


def normalize_isbn(value: Any) -> str:
    return isbnlib.to_isbn13(isbnlib.canonical(str(value or "")))


def isbn_values(value: Any) -> tuple[str, ...]:
    """Use isbnlib's actual extractor and checksum/conversion functions offline."""
    return tuple(sorted({isbn for raw in isbnlib.get_isbnlike(str(value or "")) if (isbn := normalize_isbn(raw))}))


def normalize_pmid(value: Any) -> str:
    text = str(value or "").strip()
    if text.casefold().startswith(("http://", "https://")):
        try:
            parsed = urlsplit(text)
        except ValueError:
            return ""
        if parsed.hostname == "pubmed.ncbi.nlm.nih.gov":
            text = parsed.path.strip("/")
        elif parsed.hostname in {"www.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov"} and parsed.path.startswith("/pubmed/"):
            text = parsed.path.removeprefix("/pubmed/").strip("/")
    text = re.sub(r"(?i)^PMID\s*:\s*", "", text)
    return text.lstrip("0") if re.fullmatch(r"\d{1,10}", text) and int(text) > 0 else ""


def identifier_values(metadata: dict) -> dict[str, tuple[str, ...]]:
    """Native fields take precedence; recognize narrow documented Extra lines.

    CSL DOI/PMID: value and Better BibTeX tex.doi=value are the supported forms.
    No metadata lookup, eprint guessing or native-field override is performed.
    """
    extra = metadata.get("extra", "")
    if isinstance(extra, list):
        extra = "\n".join(line for line in extra if isinstance(line, str))
    extra = extra if isinstance(extra, str) else ""
    fields: dict[str, list[str]] = {"DOI": [], "PMID": []}
    for match in re.finditer(r"(?im)^\s*(?:tex\.)?(DOI|PMID)\s*[:=]\s*(\S[^\r\n]*)\s*$", extra):
        fields[match.group(1).upper()].append(match.group(2).strip())
    result = {"ISBN": isbn_values(metadata.get("ISBN", ""))}
    for name, normalizer in [("DOI", normalize_doi), ("PMID", normalize_pmid)]:
        native = metadata.get(name, "")
        values = [native] if native not in (None, "") else fields[name]
        result[name] = tuple(sorted({identifier for value in values if (identifier := normalizer(value))}))
    return result


def year_of(value: Any) -> str:
    if isinstance(value, dict):
        parts = value.get("date-parts", [])
        if parts and isinstance(parts[0], list) and parts[0]:
            return str(parts[0][0])
        value = value.get("literal", "")
    match = re.search(r"\b([12]\d{3})\b", str(value or ""))
    return match.group(1) if match else ""


@dataclass
class Record:
    library: str
    source_id: str
    identity_quality: str
    metadata: dict[str, Any]
    raw: dict[str, Any]
    tags: list[Any] = field(default_factory=list)
    notes: list[Any] = field(default_factory=list)
    annotations: list[Any] = field(default_factory=list)
    attachments: list[Any] = field(default_factory=list)

    @property
    def id(self) -> str:
        return stable_id("item", self.library, self.source_id)

    def to_dict(self) -> dict:
        value = asdict(self)
        value["id"] = self.id
        value["fingerprint"] = digest(value)
        return value

    def matching_row(self) -> dict[str, str]:
        m = self.metadata
        item_type = canonical_item_type(m.get("type", ""))
        entry_type = {"journalArticle": "article", "magazineArticle": "article", "newspaperArticle": "article",
                      "book": "book", "bookSection": "inbook", "encyclopediaArticle": "inbook",
                      "dictionaryEntry": "inbook", "conferencePaper": "inproceedings",
                      "report": "techreport", "thesis": "thesis", "webpage": "online",
                      "blogPost": "online", "computerProgram": "software",
                      "manuscript": "unpublished", "preprint": "unpublished"}.get(item_type, "misc")
        publication = str(m.get("publication", ""))
        return {
            "ID": self.id,
            "ENTRYTYPE": entry_type,
            "title": plain_text(m.get("title", "")),
            "author": str(m.get("authors", "")),
            "year": year_of(m.get("date", "")),
            "journal": publication,
            "booktitle": publication,
            "container_title": publication,
            "volume": str(m.get("volume", "")),
            "number": str(m.get("issue", "")),
            "pages": str(m.get("pages", "")),
            "doi": next(iter(identifier_values(m)["DOI"]), ""),
            "abstract": plain_text(m.get("abstract", "")),
            "search_set": self.library,
        }

