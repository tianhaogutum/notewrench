# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (MIT; see licenses/bookmark-migration-MIT.txt)
"""Strict, read-only readers for Netscape bookmarks HTML and Karakeep JSON exports."""

import json
import math
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from .model import AuditError, Entry, Export, Field, unavailable
from .urls import url_key

MAX_INPUT_BYTES = 50 * 1024 * 1024
FOLDER_NOTES_WARNING = "folder DD descriptions are kept as metadata but not audited"


def read_text(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise AuditError(f"{path}: larger than the 50 MiB input limit")
        return raw.decode("utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise AuditError(f"cannot read UTF-8 file {path}: {exc}") from exc


def parse_json(text: str, path: Path):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise AuditError(f"{path}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    def invalid(value):
        raise AuditError(f"{path}: JSON must not contain {value}")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid)
    except (ValueError, RecursionError) as exc:
        raise AuditError(f"{path}: invalid JSON: {exc}") from exc


def timestamp(value, location: str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (isinstance(value, float) and not math.isfinite(value)):
        raise AuditError(f"{location}: time must be a finite number of Unix seconds")
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError) as exc:
        raise AuditError(f"{location}: time out of the supported range: {value!r}") from exc


def require(condition: bool, location: str, message: str):
    if not condition:
        raise AuditError(f"{location}: {message}")


def string_list(value, location: str) -> list[str]:
    require(isinstance(value, list) and all(isinstance(v, str) for v in value), location, "expected an array of strings")
    return value


def parse_karakeep(text: str, path: Path) -> Export:
    data = parse_json(text, path)
    require(isinstance(data, dict) and isinstance(data.get("bookmarks"), list), str(path), "a Karakeep JSON export needs a top-level bookmarks array")
    lists = data.get("lists", [])
    require(isinstance(lists, list), "$.lists", "expected an array")
    by_id = {}
    for index, item in enumerate(lists):
        loc = f"$.lists[{index}]"
        require(isinstance(item, dict), loc, "list must be an object")
        for key in ("id", "name", "icon", "type"):
            require(isinstance(item.get(key), str), loc, f"{key} must be a string")
        for key in ("parentId", "description", "query"):
            require(key in item and (item[key] is None or isinstance(item[key], str)), loc, f"{key} must be a string or null")
        require(item["type"] in {"manual", "smart"}, loc, "unknown list type")
        require(item["id"] not in by_id, loc, "duplicate list id")
        by_id[item["id"]] = item
    paths = {}
    for list_id in by_id:
        current, seen, chain = list_id, set(), []
        while current is not None and current not in paths:
            require(current in by_id, "$.lists", f"parent list does not exist: {current}")
            require(current not in seen, "$.lists", f"list parent cycle: {current}")
            seen.add(current)
            chain.append(current)
            current = by_id[current]["parentId"]
        prefix = paths.get(current, [])
        for node in reversed(chain):
            prefix = prefix + [by_id[node]["name"]]
            paths[node] = prefix
    result = Export(str(path), "karakeep", [])
    result.metadata = {"time_semantics": "createdAt = last saved time, not the time the bookmark was first saved", "attachment_coverage": "a plain Karakeep JSON export contains no assets or attachment references", "list_definitions": lists}
    if any(item["type"] == "smart" for item in lists):
        result.warnings.append("smart lists are exported as definitions only; their dynamic members are not in a plain export, so only explicit list memberships are compared")
    if len({tuple(p) for p in paths.values()}) != len(paths):
        result.warnings.append("several lists share the same name and parent path; the name path cannot tell them apart")
    unknown = set(data) - {"bookmarks", "lists"}
    if unknown:
        result.warnings.append("unrecognized top-level fields, not audited: " + ", ".join(sorted(unknown)))
    for index, item in enumerate(data["bookmarks"]):
        loc = f"$.bookmarks[{index}]"
        require(isinstance(item, dict), loc, "bookmark must be an object")
        for key in ("title", "note"):
            require(key in item and (item[key] is None or isinstance(item[key], str)), loc, f"{key} must be a string or null")
        require("createdAt" in item, loc, "missing createdAt")
        saved_at = timestamp(item["createdAt"], loc + ".createdAt")
        tags = string_list(item.get("tags"), loc + ".tags")
        memberships = string_list(item.get("lists", []), loc + ".lists")
        for member in memberships:
            require(member in by_id, loc, f"list membership refers to an unknown list: {member}")
        require(isinstance(item.get("archived", False), bool), loc, "archived must be a boolean")
        require("content" in item, loc, "missing content")
        content = item["content"]
        if content is None:
            result.skipped.append({"location": loc, "reason": "content is null, so there is no URL; plain exports do not cover assets", "raw": item})
            continue
        require(isinstance(content, dict), loc, "content must be an object or null")
        if content.get("type") == "text":
            require(isinstance(content.get("text"), str), loc, "text content must be a string")
            result.skipped.append({"location": loc, "reason": "text bookmarks are outside URL matching", "raw": item})
            continue
        require(content.get("type") == "link", loc, "unknown content.type; only link, text or null are accepted")
        if set(content) - {"type", "url"}:
            result.warnings.append(f"{loc}.content: unrecognized extra fields kept as raw data but not audited")
        try:
            url_key(content.get("url"))
        except AuditError as exc:
            raise AuditError(f"{loc}.content.url: {exc}") from exc
        fields = {
            "title": Field(item["title"] or ""),
            "tags": Field(sorted(set(tags))),
            "folders": Field(sorted({tuple(paths[m]) for m in memberships})),
            "notes": Field(item["note"] or ""),
            "saved_at": Field(saved_at),
            "archived": Field(item.get("archived", False)),
        }
        # A link in no lists has the same root membership as a root HTML bookmark.
        if not memberships:
            fields["folders"] = Field([()])
        if "lists" not in data or "lists" not in item:
            fields["folders"] = unavailable("older export without list fields; folder membership unknown")
        if "archived" not in item:
            fields["archived"] = unavailable("older export without archived; archive state unknown")
        extra = set(item) - {"createdAt", "title", "tags", "lists", "content", "note", "archived"}
        if extra:
            result.warnings.append(f"{loc}: unrecognized fields, not audited: {', '.join(sorted(extra))}")
        result.entries.append(Entry(loc, content["url"], fields, item))
    return result


class NetscapeParser(HTMLParser):
    def __init__(self, path: Path):
        super().__init__(convert_charrefs=True)
        self.result = Export(str(path), "netscape", [])
        self.stack: list[str | None] = []
        self.pending_folder: str | None = None
        self.capture: str | None = None
        self.parts: list[str] = []
        self.anchor_attrs: dict = {}
        self.anchor_markup = ""
        self.anchor_line = 0
        self.anchor_count = 0
        self.has_tags = False
        self.has_dd = False
        self.last_entry: Entry | None = None
        self.last_skipped: dict | None = None
        self.seen_dl = False

    def finish_capture(self):
        text = "".join(self.parts)
        kind = self.capture
        self.capture, self.parts = None, []
        if kind == "h3":
            self.pending_folder = text
            self.last_entry = None
            self.last_skipped = None
        elif kind == "a":
            self.anchor_count += 1
            loc = f"A[{self.anchor_count}]"
            attrs = self.anchor_attrs
            require(bool(self.stack), loc, "bookmark A must be inside a DL")
            if "feedurl" in attrs:
                skipped = {"location": loc, "line": self.anchor_line, "reason": "historical Livemark / FEEDURL subscription, not a URL bookmark; not audited", "raw": {"attributes": attrs, "start_tag": self.anchor_markup, "title": text}}
                self.result.skipped.append(skipped)
                self.last_skipped, self.last_entry = skipped, None
                return
            try:
                url_key(attrs.get("href"))
            except AuditError as exc:
                raise AuditError(f"{loc}, line {self.anchor_line}: {exc}") from exc
            saved = unavailable("no ADD_DATE on this entry; time unknown", "missing")
            if "add_date" in attrs:
                value = attrs["add_date"]
                require(isinstance(value, str) and bool(re.fullmatch(r"\d+", value)), loc, "ADD_DATE must be a non-negative whole number of Unix seconds")
                saved = Field(timestamp(int(value), loc + ".ADD_DATE"))
            if "tags" in attrs:
                require(isinstance(attrs["tags"], str), loc, "TAGS attribute has no value")
                self.has_tags = True
            fields = {
                "title": Field(text),
                "tags": Field(sorted(set(filter(None, (attrs.get("tags") or "").split(","))))),
                "folders": Field([tuple(s for s in self.stack if s is not None)]),
                "notes": unavailable("no DD on this entry; cannot tell whether the exporter writes notes", "missing"),
                "saved_at": saved,
                "archived": unavailable("Netscape HTML has no archive state"),
            }
            entry = Entry(loc, attrs["href"], fields, {"attributes": attrs, "start_tag": self.anchor_markup, "title": text}, self.anchor_line)
            self.result.entries.append(entry)
            self.last_entry = entry
            self.last_skipped = None
        elif kind == "dd":
            if self.last_entry is not None:
                self.has_dd = True
                self.last_entry.fields["notes"] = Field(text.strip())
                self.last_entry.raw["description"] = text.strip()
            elif self.last_skipped is not None:
                self.last_skipped["raw"]["description"] = text.strip()
            elif self.pending_folder is not None:
                self.result.metadata.setdefault("folder_descriptions", []).append({"path": [s for s in self.stack if s is not None] + [self.pending_folder], "description": text.strip()})
                if FOLDER_NOTES_WARNING not in self.result.warnings:
                    self.result.warnings.append(FOLDER_NOTES_WARNING)
            else:
                raise AuditError("DD: description has no preceding bookmark or folder")

    def handle_starttag(self, tag, attrs):
        if self.capture == "dd" and tag in {"dt", "dl", "h3", "a"}:
            self.finish_capture()
        if tag in {"a", "h3"}:
            require(self.capture is None, f"line {self.getpos()[0]}", "nested or unclosed A/H3")
            require(self.pending_folder is None, f"line {self.getpos()[0]}", "H3 folder title not followed by a DL")
            self.capture, self.parts = tag, []
            if tag == "a":
                keys = [key for key, _ in attrs]
                require(len(keys) == len(set(keys)), f"line {self.getpos()[0]}", "duplicate A attribute")
                self.anchor_attrs, self.anchor_line = dict(attrs), self.getpos()[0]
                self.anchor_markup = self.get_starttag_text()
        elif tag == "dl":
            require(self.capture is None, "DL", "DL inside an unclosed entry")
            require(not self.stack or self.pending_folder is not None, "DL", "nested DL without an H3 folder title")
            self.seen_dl = True
            self.stack.append(self.pending_folder)
            self.pending_folder = None
            self.last_entry = None
            self.last_skipped = None
        elif tag == "dd":
            require(self.capture is None, "DD", "invalid nested DD")
            self.capture, self.parts = "dd", []
        elif tag == "br" and self.capture:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == self.capture:
            self.finish_capture()
        elif tag == "dl":
            if self.capture == "dd":
                self.finish_capture()
            require(self.capture is None, "DL", "unclosed bookmark or folder title")
            require(self.pending_folder is None, "DL", "H3 folder title not followed by a DL")
            require(bool(self.stack), "DL", "unexpected </DL>")
            self.stack.pop()
            self.last_entry = None
            self.last_skipped = None

    def handle_data(self, data):
        if self.capture:
            self.parts.append(data)

    def finalize(self):
        self.close()
        if self.capture == "dd":
            self.finish_capture()
        require(self.capture is None and not self.stack and self.seen_dl and self.pending_folder is None, str(self.result.path), "incomplete bookmark HTML: A, H3 and DL must be closed")
        if not self.has_tags:
            for entry in self.result.entries:
                entry.fields["tags"] = unavailable("no TAGS anywhere in the file; unknown whether the exporter writes tags")
        if not self.has_dd:
            for entry in self.result.entries:
                entry.fields["notes"] = unavailable("no DD anywhere in the file; unknown whether the exporter writes notes")
        self.result.metadata.update({"tags_observed": self.has_tags, "descriptions_observed": self.has_dd, "time_semantics": "ADD_DATE = bookmark time written by the exporter (seconds)", "attachment_coverage": "Netscape HTML has no attachment list"})
        return self.result


def load_export(path: Path, format_name: str = "auto") -> Export:
    text = read_text(path)
    if format_name == "auto":
        format_name = "karakeep" if text.lstrip().startswith("{") else "netscape"
    if format_name == "karakeep":
        result = parse_karakeep(text, path)
    elif format_name == "netscape":
        require(bool(re.match(r"\s*<!DOCTYPE\s+NETSCAPE-Bookmark-file-1\s*>", text, re.I)), str(path), "not a Netscape bookmarks HTML file (the standard DOCTYPE is missing); for a JSON file pass --source-format/--target-format karakeep")
        parser = NetscapeParser(path)
        try:
            parser.feed(text)
            result = parser.finalize()
        except RecursionError as exc:
            raise AuditError(f"{path}: HTML nested too deeply") from exc
    else:
        raise AuditError(f"unknown format: {format_name}")
    if not result.entries:
        result.warnings.append("no URL bookmarks to compare; an empty result does not prove a complete migration")
    return result
