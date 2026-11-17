# SPDX-License-Identifier: MIT
# Copyright (c) 2026 archive-relink contributors
"""A markdown-it-py span plugin; upstream rules decide what is a link."""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass, field

from markdown_it import MarkdownIt
from markdown_it.rules_block.reference import reference as upstream_reference
from markdown_it.rules_inline import StateInline
from markdown_it.rules_inline.autolink import autolink as upstream_autolink
from markdown_it.rules_inline.image import image as upstream_image
from markdown_it.rules_inline.link import link as upstream_link
from mdit_py_plugins.front_matter import front_matter_plugin

from .urls import url_key, useful_quote


def _is_http(href: str) -> bool:
    return href.partition(":")[0].casefold() in {"http", "https"}


@dataclass
class Citation:
    url: str
    start: int
    end: int
    kind: str
    line: int
    label: str
    reference_id: str | None = None
    quotes: list[str] = field(default_factory=list)
    uses: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _capture_link(state: StateInline, silent: bool) -> bool:
    start = state.pos
    token_start = len(state.tokens)
    label_end = -1
    if state.src[start : start + 1] == "[":
        label_end = state.md.helpers.parseLinkLabel(state, start, True)
    success = upstream_link(state, silent)
    if success and not silent and not state.env.get("_ignore_link_spans"):
        opened = next((t for t in state.tokens[token_start:] if t.type == "link_open"), None)
        if opened and _is_http(opened.attrGet("href") or ""):
            label = opened.meta.get("label")
            record = {
                "url": opened.attrGet("href"),
                "label": label,
                "text": state.src[start + 1 : label_end],
                "kind": "reference" if label else "inline",
                "start": start,
                "end": state.pos,
            }
            if not label:
                pos = label_end + 2
                while pos < state.pos and state.src[pos] in " \t\n":
                    pos += 1
                dest = state.md.helpers.parseLinkDestination(state.src, pos, state.pos)
                if not dest.ok:
                    return success
                record.update(start=pos, end=dest.pos)
            state.env.setdefault("_link_spans", []).append(record)
    return success


def _capture_autolink(state: StateInline, silent: bool) -> bool:
    start = state.pos
    token_start = len(state.tokens)
    success = upstream_autolink(state, silent)
    if success and not silent and not state.env.get("_ignore_link_spans"):
        opened = next((t for t in state.tokens[token_start:] if t.type == "link_open"), None)
        if opened and _is_http(opened.attrGet("href") or ""):
            state.env.setdefault("_link_spans", []).append(
                {
                    "url": opened.attrGet("href"),
                    "label": None,
                    "text": state.src[start + 1 : state.pos - 1],
                    "kind": "autolink",
                    "start": start,
                    "end": state.pos,
                }
            )
    return success


def _capture_image(state: StateInline, silent: bool) -> bool:
    # Image alt text has a nested inline parser, but no clickable child links.
    previous = state.env.get("_ignore_link_spans", False)
    token_start = len(state.tokens)
    state.env["_ignore_link_spans"] = True
    try:
        success = upstream_image(state, silent)
        if success and not silent:
            for token in state.tokens[token_start:]:
                if token.type == "image" and token.meta.get("label"):
                    state.env.setdefault("_image_references", set()).add(token.meta["label"])
        return success
    finally:
        state.env["_ignore_link_spans"] = previous


def _capture_reference(state, start_line: int, end_line: int, silent: bool) -> bool:
    token_start = len(state.tokens)
    success = upstream_reference(state, start_line, end_line, silent)
    if not success or silent:
        return success
    definition = next((t for t in state.tokens[token_start:] if t.type == "definition"), None)
    if definition is None:
        return success
    reference = state.env["references"][definition.meta["id"]]
    if reference["map"] != definition.map:
        return success  # First definition wins, including its destination span.
    # Container offsets are active here, before the block parser restores them.
    content = ""
    mapping = []
    finish = state.bMarks[start_line]
    for line in range(start_line, state.line):
        begin = state.bMarks[line] + state.tShift[line]
        finish = min(state.eMarks[line] + 1, len(state.src))
        content += state.src[begin:finish]
        mapping.extend(range(begin, finish))
    mapping.append(finish)
    position = len(definition.meta["label"]) + 3
    while position < len(content) and content[position] in " \t\n":
        position += 1
    parsed = state.md.helpers.parseLinkDestination(content, position, len(content))
    if parsed.ok and state.md.normalizeLink(parsed.str) == reference["href"]:
        reference["_destination_span"] = (mapping[position], mapping[parsed.pos])
    return success


def parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"store_labels": True, "inline_definitions": True})
    md.use(front_matter_plugin)
    md.block.ruler.at("reference", _capture_reference)
    md.inline.ruler.at("link", _capture_link)
    md.inline.ruler.at("autolink", _capture_autolink)
    md.inline.ruler.at("image", _capture_image)
    return md


def _line_offsets(source: str) -> list[int]:
    return [0, *(m.end() for m in re.finditer("\n", source))]


class _ContentMap:
    def __init__(self):
        self.starts = []
        self.raw_starts = []
        self.length = 0
        self.end = 0

    def append(self, raw_start: int, length: int) -> None:
        if length:
            self.starts.append(self.length)
            self.raw_starts.append(raw_start)
            self.length += length
        self.end = raw_start + length

    def __getitem__(self, position: int) -> int:
        if position == self.length:
            return self.end
        index = bisect_right(self.starts, position) - 1
        return self.raw_starts[index] + position - self.starts[index]


def _content_map(content: str, source: str, start: int, end: int) -> _ContentMap | None:
    """Map stripped container lines back to their original, unchanged characters.

    markdown-it removes list/blockquote prefixes. Locate each content line within
    its block line range, in order. Refuse unusual ambiguous transformations.
    """
    mapping = _ContentMap()
    cursor = start
    for line in content.splitlines(keepends=True):
        text = line.rstrip("\n")
        found = source.find(text, cursor, end) if text else cursor
        if found < 0:
            return None
        mapping.append(found, len(text))
        cursor = found + len(text)
        if line.endswith("\n"):
            newline = source.find("\n", cursor, end)
            if newline < 0:
                return None
            mapping.append(newline, 1)
            cursor = newline + 1
    mapping.end = cursor
    return mapping


def _quotes(content: str) -> list[str]:
    # Explicit quotations only; a link's title/label never becomes body evidence.
    result = []
    for match in re.finditer(r'"([^"\n]+)"|“([^”]+)”|「([^」]+)」|«([^»]+)»', content):
        quote = next(g for g in match.groups() if g is not None)
        if useful_quote(quote):
            result.append(quote)
    return result


def _narrative(children: list) -> list[str]:
    """Contiguous prose, excluding links, images, code and raw HTML."""
    result = []
    text = ""
    link_depth = 0
    for token in children:
        if token.type == "link_open":
            link_depth += 1
            result.append(text)
            text = ""
        elif token.type == "link_close":
            link_depth -= 1
        elif not link_depth and token.type == "text":
            text += token.content
        elif not link_depth and token.type in {"softbreak", "hardbreak"}:
            text += "\n"
        elif not link_depth and token.type in {"code_inline", "html_inline", "image"}:
            result.append(text)
            text = ""
    result.append(text)
    return result


def citations(source: str) -> tuple[list[Citation], list[str]]:
    """Return byte-preserving character spans for HTTP(S) citations.

    The caller reads/writes UTF-8 with newline translation disabled. Code,
    images, and HTML do not produce citations. Reference uses share one edit.
    """
    # Preserve offsets for CRLF: markdown-it normally normalizes line endings.
    bom_shift = int(source.startswith("\ufeff"))
    normalized = source[bom_shift:].replace("\r\n", "\n").replace("\r", "\n")
    crlf_positions = [m.start() - i - bom_shift for i, m in enumerate(re.finditer("\r\n", source))]
    raw_lines = [0, *(m.end() for m in re.finditer(r"\r\n|\r|\n", source))]

    md = parser()
    md.core.ruler.disable("inline")
    env: dict = {}
    tokens = md.parse(normalized, env)
    offsets = _line_offsets(normalized) + [len(normalized)]
    result: dict[tuple[int, int], Citation] = {}
    warnings: list[str] = []
    for token_index, token in enumerate(tokens):
        if token.type != "inline" or not token.map:
            continue
        block_start, block_end = (offsets[i] for i in token.map)
        mapping = _content_map(token.content, normalized, block_start, block_end)
        if mapping is None:
            warnings.append(f"line {token.map[0] + 1}: unsupported source mapping; left unchanged")
            continue
        env["_link_spans"] = []
        children: list = []
        md.inline.parse(token.content, md, env, children)
        quotes = [quote for prose in _narrative(children) for quote in _quotes(prose)]
        # A neighboring Markdown blockquote is a conventional quoted citation.
        for neighbor in tokens[max(0, token_index - 5) : token_index + 6]:
            if neighbor.type == "inline" and neighbor.map:
                if neighbor.map[0] > token.map[1] + 1 or neighbor.map[1] < token.map[0] - 1:
                    continue
                if neighbor.map != token.map:
                    raw_block = normalized[offsets[neighbor.map[0]] : offsets[neighbor.map[1]]]
                    if re.match(r"\s*>", raw_block) and useful_quote(neighbor.content):
                        neighbor_children = []
                        previous = env.get("_ignore_link_spans", False)
                        env["_ignore_link_spans"] = True
                        md.inline.parse(neighbor.content, md, env, neighbor_children)
                        env["_ignore_link_spans"] = previous
                        prose = _narrative(neighbor_children)
                        if len(prose) == 1 and useful_quote(prose[0]):
                            quotes.append(prose[0])
        for span in env["_link_spans"]:
            if not url_key(span["url"]):
                warnings.append(
                    f"line {token.map[0] + 1}: unsupported or invalid HTTP(S) URL; left unchanged"
                )
                continue
            if span["kind"] == "reference":
                ref = env["references"][span["label"]]
                if "_destination_span" not in ref:
                    warnings.append(f"line {ref['map'][0] + 1}: cannot map reference destination")
                    continue
                begin, finish = ref["_destination_span"]
            else:
                begin, finish = mapping[span["start"]], mapping[span["end"]]
                # Cross-line destinations cannot safely include stripped prefixes.
                if normalized[begin:finish] != token.content[span["start"] : span["end"]]:
                    warnings.append(
                        f"line {token.map[0] + 1}: non-contiguous link span; left unchanged"
                    )
                    continue
            begin += bisect_left(crlf_positions, begin) + bom_shift
            finish += bisect_left(crlf_positions, finish) + bom_shift
            key = (begin, finish)
            if key not in result:
                result[key] = Citation(
                    url=span["url"],
                    start=begin,
                    end=finish,
                    kind=span["kind"],
                    line=bisect_right(raw_lines, begin),
                    label=span["text"],
                    reference_id=span.get("label"),
                )
            citation = result[key]
            citation.quotes = list(dict.fromkeys([*citation.quotes, *quotes]))
            use_line = token.map[0] + 1
            if use_line not in citation.uses:
                citation.uses.append(use_line)
    safe = []
    for citation in result.values():
        if citation.reference_id and citation.reference_id in env.get("_image_references", set()):
            warnings.append(
                f"line {citation.line}: reference also used by an image; left unchanged"
            )
        else:
            safe.append(citation)
    return sorted(safe, key=lambda c: c.start), warnings
