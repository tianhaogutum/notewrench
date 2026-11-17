# SPDX-License-Identifier: MIT
# Copyright (c) 2026 citekey-audit contributors (MIT; see licenses/bibtex-keys-MIT.txt)
"""Find source spans without reserializing Markdown or YAML."""

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from html.parser import HTMLParser
import json
from pathlib import PurePosixPath
import re
from urllib.parse import quote, unquote, urlsplit

from markdown_it import MarkdownIt
from markdown_it.rules_inline.autolink import autolink
from markdown_it.rules_inline.backticks import backtick
from markdown_it.rules_inline.escape import escape
from markdown_it.rules_inline.html_inline import html_inline
from markdown_it.rules_inline.image import image
from markdown_it.rules_inline.link import link
from mdit_py_plugins.attrs import attrs_plugin
from mdit_py_plugins.attrs.parse import ParseError as AttributeParseError, parse as parse_attributes
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.dollarmath.index import math_inline_dollar
from mdit_py_plugins.footnote import footnote_plugin
from pylatexenc.latexwalker import LatexWalker, LatexWalkerError
import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from .bibliography import lf_lines

PLAIN_KEY = re.compile(r"\w+(?:[:.#$%&+?<>~/\-]\w+)*\Z", re.UNICODE)
CITATION = re.compile(r"(?<![\w@/])@(?:\{(?P<braced>[^{}\r\n]+)\}|(?P<plain>\w+(?:[:.#$%&+?<>~/\-]\w+)*))")
WIKI = re.compile(r"!?\[\[(?P<target>[^\[\]\r\n]+)\]\]")
KEY_LINK = re.compile(r"zotero://select/items/@(?P<key>[^\s<>\"'()\[\]`]+)")
OTHER_URI = re.compile(r"(?<![\w+.-])(?:[A-Za-z][A-Za-z0-9+.-]*://|mailto:)[^\s<>]+")
EMAIL_DOMAIN = re.compile(r"[\w](?:[\w.\-]*[\w])?\.[A-Za-z]{2,}(?![\w@])")
EMAIL_LOCAL_PUNCTUATION = frozenset(".!#$%&'*+/=?^_`{|}~-")
METADATA_FIELDS = frozenset({"citekey", "citekeys", "citation-key", "citation_key"})
RAW_HTML_ELEMENTS = frozenset({"script", "style", "pre", "code", "textarea", "math"})
RAW_TEX_START = re.compile(r"\\[A-Za-z]+\*?")


@dataclass(frozen=True)
class Edit:
    start: int
    end: int
    replacement: str


class _IntervalIndex:
    """Merged protected source spans, queried in logarithmic time."""
    def __init__(self, spans):
        merged = []
        for left, right, *_ in sorted(spans):
            if merged and left <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
            else:
                merged.append((left, right))
        self.starts = [left for left, _ in merged]
        self.ends = [right for _, right in merged]

    def overlaps(self, start, end):
        index = bisect_left(self.starts, end) - 1
        return index >= 0 and self.ends[index] > start

    def add(self, start, end):
        if not self.starts or start > self.ends[-1]:
            self.starts.append(start)
            self.ends.append(end)
        elif start >= self.starts[-1]:
            self.ends[-1] = max(self.ends[-1], end)
        else:
            self.__init__([*zip(self.starts, self.ends), (start, end)])


class _TagEvents(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.events = []

    def handle_starttag(self, tag, attrs):
        self.events.append(("start", tag))

    def handle_endtag(self, tag):
        self.events.append(("end", tag))

    def handle_startendtag(self, tag, attrs):
        pass


def _raw_html_ranges(content, spans):
    stack = []
    for left, right, kind, *_ in sorted(spans):
        if kind != "html_inline":
            continue
        parser = _TagEvents()
        parser.feed(content[left:right])
        for event, tag in parser.events:
            if tag not in RAW_HTML_ELEMENTS:
                continue
            if event == "start":
                stack.append((tag, left))
            else:
                index = next((i for i in range(len(stack) - 1, -1, -1) if stack[i][0] == tag), None)
                if index is not None:
                    yield stack[index][1], right, tag
                    del stack[index:]
    for tag, left in stack:
        yield left, len(content), tag


def _email_ranges(content):
    # Anchoring at each @ avoids retrying an unbounded email-local-part regex
    # at every character of a long word. Local parts cannot contain @.
    for marker in re.finditer("@", content):
        domain = EMAIL_DOMAIN.match(content, marker.end())
        if not domain:
            continue
        left = marker.start()
        while left and (content[left - 1].isalnum() or content[left - 1] in EMAIL_LOCAL_PUNCTUATION):
            left -= 1
        if left < marker.start() and any(char.isalnum() or char == "_" for char in content[left:marker.start()]):
            yield left, domain.end()


def note_target_key(target: str) -> str:
    """Recognize explicit citekey filenames, including MarkDB's @key Title form."""
    name = PurePosixPath(target).name
    name = re.sub(r"\.(?:md|markdown|qmd|rmd)\Z", "", name, flags=re.I)
    if name.startswith("@"):
        return name[1:].split(maxsplit=1)[0] if name[1:].strip() else ""
    return name


def _decode_key(encoded: str) -> str:
    if re.search(r"%(?![0-9a-fA-F]{2})", encoded):
        raise ValueError("invalid percent escape")
    if "?" in encoded or "#" in encoded:
        raise ValueError("query/fragment is not part of a Better BibTeX select-key link; encode it in the key")
    return unquote(encoded, encoding="utf-8", errors="strict")


def _parser():
    md = MarkdownIt("commonmark", {"html": True, "inline_definitions": True})
    md.use(dollarmath_plugin, allow_space=False, allow_digits=False, double_inline=True)
    md.use(attrs_plugin)
    md.use(footnote_plugin, inline=False, move_to_end=False, always_match_refs=True)
    rules = [("backticks", backtick), ("escape", escape), ("link", link),
             ("autolink", autolink), ("image", image), ("html_inline", html_inline),
             ("math_inline", math_inline_dollar(False, False, True))]
    # attrs_plugin registers a configured partial; use the registered rule so
    # its established parser, rather than a second attribute grammar, runs.
    registered = dict(zip(md.inline.ruler.get_active_rules(), md.inline.ruler.getRules("")))
    rules.extend((name, registered[name]) for name in ("attr", "footnote_ref"))
    for name, function in rules:
        def wrapper(state, silent, function=function, name=name):
            start, token_start = state.pos, len(state.tokens)
            result = function(state, silent)
            if result and not silent and state.src is state.env.get("_audit_source"):
                href = None
                destination = None
                if name in {"link", "autolink"}:
                    href = next((token.attrGet("href") for token in state.tokens[token_start:]
                                 if token.type == "link_open"), None)
                    if name == "autolink":
                        destination = (start + 1, state.pos - 1)
                    else:
                        # Ask markdown-it for the actual destination; labels and
                        # titles can themselves contain a Zotero-looking string.
                        label_end = state.md.helpers.parseLinkLabel(state, start, True)
                        if label_end >= 0 and state.src[label_end + 1:label_end + 2] == "(":
                            left = label_end + 2
                            while left < state.pos and state.src[left].isspace():
                                left += 1
                            parsed = state.md.helpers.parseLinkDestination(state.src, left, state.pos)
                            if parsed.ok:
                                destination = (left, parsed.pos)
                state.env["_audit_protected"].append((start, state.pos, name, href, destination))
            return result
        md.inline.ruler.at(name, wrapper)
    return md


class NoteScanner:
    def __init__(self, states: dict):
        self.states = states
        self.md = _parser()

    def scan(self, text: str):
        edits, occurrences, issues = [], [], []
        raw_tex_index = _IntervalIndex([])
        tex_walker = None
        line_starts = [0]
        line_starts.extend(m.end() for m in re.finditer("\n", text))

        def emit(key, start, end, kind, replacement=None, manual=False):
            if raw_tex_index.overlaps(start, end):
                return
            state = self.states.get(key)
            if state is None or state["status"] == "unchanged":
                return
            line_index = bisect_right(line_starts, start) - 1
            status = state["status"]
            if status == "renamed":
                status = "manual_review" if manual else "replace"
            record = {"kind": kind, "old_key": key, "new_key": state["new_key"],
                      "identity": state["identity"], "status": status,
                      "line": line_index + 1, "column": start - line_starts[line_index] + 1,
                      "start": start, "end": end, "old_text": text[start:end],
                      "new_text": replacement if status == "replace" else None}
            if status == "replace":
                if replacement is None or any(c in state["new_key"] for c in "{}\r\n\x00"):
                    record["status"], record["new_text"] = "unsafe_target_key", None
                else:
                    edits.append(Edit(start, end, replacement))
            occurrences.append(record)

        body_start = 0
        raw_lines = lf_lines(text)
        if raw_lines and raw_lines[0].removeprefix("\ufeff").rstrip("\r\n \t") == "---":
            closing = next((i for i in range(1, len(raw_lines)) if raw_lines[i].rstrip("\r\n \t") in {"---", "..."}), None)
            if closing is None:
                return [], [], [{"kind": "invalid_frontmatter", "message": "Opening --- has no closing delimiter; file not patched."}]
            yaml_start = len(raw_lines[0])
            yaml_end = sum(map(len, raw_lines[:closing]))
            body_start = yaml_end + len(raw_lines[closing])
            self._metadata(text[yaml_start:yaml_end], yaml_start, emit, issues)

        body = text[body_start:]
        source_lines = lf_lines(body)
        source_starts = [body_start]
        for source_line in source_lines:
            source_starts.append(source_starts[-1] + len(source_line))
        env = {}
        parsed_tokens = self.md.parse(body, env)
        for token_index, token in enumerate(parsed_tokens):
            if token.type == "definition" and token.map:
                href = token.meta["url"]
                if href.startswith("zotero://select/items/@"):
                    left_line, right_line = token.map
                    start = source_starts[left_line]
                    raw = "".join(source_lines[left_line:right_line])
                    marker = raw.find("]:")
                    match = KEY_LINK.search(raw, marker + 2) if marker >= 0 else None
                    try:
                        key = _decode_key(href[len("zotero://select/items/@"):])
                    except ValueError as exc:
                        issues.append({"kind": "invalid_citekey_link", "message": str(exc)})
                        continue
                    if match and key in self.states:
                        replacement = quote(self.states[key]["new_key"], safe="") if self.states[key]["new_key"] else None
                        emit(key, start + match.start("key"), start + match.end("key"),
                             "zotero_reference_definition", replacement)
                    elif key in self.states:
                        issues.append({"kind": "unsupported_reference_definition", "old_key": key,
                                       "message": "Destination is not a literal Zotero URI; review manually."})
                else:
                    try:
                        uri = urlsplit(href)
                    except ValueError:
                        continue
                    if not uri.scheme and not uri.netloc:
                        target = note_target_key(unquote(uri.path))
                        start = source_starts[token.map[0]]
                        end = source_starts[token.map[1]]
                        emit(target, start, end, "markdown_reference_definition", manual=True)
                continue
            if token.type != "inline" or not token.map:
                continue
            content = token.content
            heading = token_index > 0 and parsed_tokens[token_index - 1].type == "heading_open"
            initial_occurrences = len(occurrences)
            explicit_heading_id = False
            segments = []
            cursor, logical_start = token.map[0], 0
            for logical_line in content.split("\n"):
                while cursor < min(token.map[1], len(source_lines)):
                    raw = source_lines[cursor].rstrip("\r\n")
                    # Container prefixes (notably footnote labels) may contain
                    # the same string as the actual inline body. The body is
                    # the final occurrence after those prefixes.
                    found = raw.rfind(logical_line)
                    if found >= 0:
                        segments.append((logical_start, logical_start + len(logical_line), source_starts[cursor] + found))
                        cursor += 1
                        break
                    cursor += 1
                else:
                    issues.append({"kind": "unsupported_source_map", "line": token.map[0] + 1,
                                   "message": "Markdown parser content could not be mapped exactly; paragraph skipped."})
                    segments = []
                    break
                logical_start += len(logical_line) + 1
            if not segments:
                continue
            starts = [segment[0] for segment in segments]

            def source_range(start, end):
                index = bisect_right(starts, start) - 1
                if index < 0 or end > segments[index][1]:
                    return None
                return (segments[index][2] + start - segments[index][0],
                        segments[index][2] + end - segments[index][0])

            def local_emit(key, start, end, kind, replacement=None, manual=False):
                span = source_range(start, end)
                if span:
                    emit(key, *span, kind, replacement, manual)
                elif key in self.states and self.states[key]["status"] != "unchanged":
                    issues.append({"kind": "unsupported_source_span", "old_key": key,
                                   "message": "A reference crosses source lines; review manually."})

            local_env = dict(env, _audit_source=content, _audit_protected=[])
            self.md.inline.parse(content, self.md, local_env, [])
            protected = local_env["_audit_protected"]
            raw_html_ranges = list(_raw_html_ranges(content, protected))
            for left, right, tag in raw_html_ranges:
                protected.append((left, right, "raw_html", None, None))
                if tag not in {"script", "style"}:
                    for candidate in CITATION.finditer(content, left, right):
                        group = "braced" if candidate["braced"] is not None else "plain"
                        local_emit(candidate[group], *candidate.span(group), "html_code_or_citation", manual=True)
            # Pandoc-style trailing heading/block attributes are not inline
            # elements in CommonMark. Reuse the plugin's attribute parser.
            attribute_start = content.rfind("{")
            if attribute_start >= 0 and content.endswith("}"):
                try:
                    end, attributes = parse_attributes(content[attribute_start:])
                except AttributeParseError:
                    pass
                else:
                    if attributes and end == len(content) - attribute_start - 1:
                        protected.append((attribute_start, len(content), "attributes", None, None))
                        explicit_heading_id = bool(attributes.get("id"))
            index = _IntervalIndex(protected)
            uris = list(OTHER_URI.finditer(content))
            uri_starts = [uri.start() for uri in uris]
            braced_candidates = []
            for candidate in CITATION.finditer(content):
                if candidate["braced"] is None or index.overlaps(candidate.start(), candidate.start() + 1):
                    continue
                uri_index = bisect_right(uri_starts, candidate.start()) - 1
                if uri_index >= 0 and uris[uri_index].end() > candidate.start():
                    continue
                braced_candidates.append(candidate)
            braced_index = _IntervalIndex(candidate.span() for candidate in braced_candidates)
            # Pandoc treats raw TeX as literal content. Use LatexWalker's
            # established macro/environment/group parser at Markdown-approved
            # source positions, retaining ranges across paragraph boundaries.
            tex_context = _IntervalIndex(span for span in protected
                                         if span[2] != "escape" or not (span[1] - span[0] == 2 and content[span[0] + 1].isalpha()))
            for marker in RAW_TEX_START.finditer(content):
                if tex_context.overlaps(*marker.span()) or braced_index.overlaps(*marker.span()):
                    continue
                span = source_range(*marker.span())
                if not span or raw_tex_index.overlaps(*span):
                    continue
                if tex_walker is None:
                    tex_walker = LatexWalker(text, tolerant_parsing=False)
                start = span[0]
                try:
                    nodes, position, length = tex_walker.get_latex_nodes(pos=start, read_max_nodes=1)
                    end = position + length
                    # Unknown TeX macros have no declared argument spec. Their
                    # following groups are still parsed by LatexWalker.
                    while end < len(text):
                        candidate = end
                        while candidate < len(text) and text[candidate] in " \t":
                            candidate += 1
                        if candidate >= len(text) or text[candidate] not in "{[":
                            break
                        _, position, length = tex_walker.get_latex_braced_group(candidate, brace_type=text[candidate])
                        end = position + length
                except (LatexWalkerError, RecursionError) as exc:
                    end = len(text)
                    issues.append({"kind": "invalid_raw_tex", "message": "Raw TeX could not be parsed; remaining source requires manual review.",
                                   "line": bisect_right(line_starts, start), "detail": str(exc)[:500]})
                if end > start:
                    raw_tex_index.add(start, end)

            # Braced keys own their interior syntax, even when a key contains
            # dollar signs, backticks, links, or a URL. Outer code/links/TeX
            # still protect the entire citation.
            for candidate in braced_candidates:
                key = candidate["braced"]
                state = self.states.get(key)
                if state:
                    local_emit(key, *candidate.span("braced"), "pandoc_citation", state["new_key"])
                protected.append((*candidate.span(), "braced_citation", None, None))
            index = _IntervalIndex(protected)

            def keylinks(start, end, inside_link=False):
                for match in KEY_LINK.finditer(content, start, end):
                    left, right = match.span("key")
                    encoded = match["key"]
                    try:
                        key = _decode_key(encoded)
                    except ValueError as exc:
                        issues.append({"kind": "invalid_citekey_link", "message": str(exc)})
                        continue
                    if key not in self.states and not inside_link:
                        trimmed = encoded.rstrip(".,;")
                        key, right = _decode_key(trimmed), left + len(trimmed)
                    state = self.states.get(key)
                    if state:
                        replacement = quote(state["new_key"], safe="") if state["new_key"] else None
                        local_emit(key, left, right, "zotero_citekey_link", replacement)

            for left, right, kind, href, destination in list(protected):
                if kind in {"link", "autolink"}:
                    if braced_index.overlaps(left, right):
                        continue
                    if destination and href and href.startswith("zotero://select/items/@"):
                        try:
                            key = _decode_key(href[len("zotero://select/items/@"):])
                        except ValueError as exc:
                            issues.append({"kind": "invalid_citekey_link", "message": str(exc)})
                            continue
                        state = self.states.get(key)
                        if state:
                            start, end = destination
                            if content[start:start + 1] == "<":
                                start, end = start + 1, end - 1
                            prefix = "zotero://select/items/@"
                            if content.startswith(prefix, start):
                                replacement = quote(state["new_key"], safe="") if state["new_key"] else None
                                local_emit(key, start + len(prefix), end, "zotero_citekey_link", replacement)
                            else:
                                local_emit(key, start, end, "encoded_zotero_link", manual=True)
                    if href:
                        try:
                            uri = urlsplit(href)
                        except ValueError:
                            continue
                        if not uri.scheme and not uri.netloc:
                            target = note_target_key(unquote(uri.path))
                            local_emit(target, left, right, "markdown_note_link", manual=True)
            for match in WIKI.finditer(content):
                if index.overlaps(*match.span()):
                    continue
                target = re.split(r"[|#]", match["target"], maxsplit=1)[0]
                target = note_target_key(target)
                local_emit(target, *match.span(), "wiki_note_link", manual=True)
                protected.append((*match.span(), "wiki", None, None))
            index = _IntervalIndex(protected)
            for match in uris:
                if not index.overlaps(*match.span()):
                    keylinks(*match.span())
                    protected.append((*match.span(), "uri", None, None))
            index = _IntervalIndex(protected)
            for left, right in _email_ranges(content):
                protected.append((left, right, "email", None, None))
                for candidate in CITATION.finditer(content, left, right):
                    if index.overlaps(*candidate.span()):
                        continue
                    group = "braced" if candidate["braced"] is not None else "plain"
                    local_emit(candidate[group], *candidate.span(group), "email_or_citation", manual=True)
            index = _IntervalIndex(protected)
            for match in CITATION.finditer(content):
                if index.overlaps(*match.span()):
                    continue
                group = "braced" if match["braced"] is not None else "plain"
                key = match[group]
                if match.end() < len(content) and content[match.end()] == "@":
                    continue
                state = self.states.get(key)
                if state:
                    replacement = state["new_key"]
                    if replacement and group == "plain" and not PLAIN_KEY.fullmatch(replacement):
                        replacement = "{" + replacement + "}"
                    local_emit(key, *match.span(group), "pandoc_citation", replacement)
            if heading and not explicit_heading_id and any(item["status"] == "replace" for item in occurrences[initial_occurrences:]):
                issues.append({"kind": "heading_anchor_may_change", "line": token.map[0] + 1,
                               "message": "Changing citation text in this heading may change its automatic anchor; review section backlinks."})
        occurrences.sort(key=lambda item: (item["start"], item["kind"]))
        edits.sort(key=lambda edit: edit.start)
        for previous, current in zip(edits, edits[1:]):
            if current.start < previous.end:
                return [], occurrences, issues + [{"kind": "overlapping_edits", "message": "File not patched because reference spans overlap."}]
        return edits, occurrences, issues

    def _metadata(self, source, offset, emit, issues):
        try:
            tokens = list(yaml.scan(source))
            node = yaml.compose(source, Loader=yaml.SafeLoader)
        except yaml.YAMLError as exc:
            issues.append({"kind": "invalid_frontmatter", "message": str(exc)})
            return
        if node is None:
            return
        if not isinstance(node, MappingNode):
            issues.append({"kind": "invalid_frontmatter", "message": "Expected a YAML mapping."})
            return
        if any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)) for token in tokens):
            issues.append({"kind": "yaml_alias_or_anchor", "message": "Metadata aliases/anchors require manual review; body can still be patched."})
            return
        seen = set()
        for key, value in node.value:
            if not isinstance(key, ScalarNode) or key.value in seen:
                issues.append({"kind": "duplicate_or_complex_metadata_key", "message": "Metadata not patched."})
                return
            seen.add(key.value)
        for key, value in node.value:
            if key.value not in METADATA_FIELDS:
                continue
            values = value.value if isinstance(value, SequenceNode) else [value]
            for scalar in values:
                if not isinstance(scalar, ScalarNode) or scalar.style in {"|", ">"}:
                    issues.append({"kind": "unsupported_metadata_value", "field": key.value,
                                   "message": "Expected a scalar citation key or sequence of scalars."})
                    continue
                if scalar.tag == "tag:yaml.org,2002:null":
                    continue
                if scalar.tag != "tag:yaml.org,2002:str":
                    issues.append({"kind": "unsupported_metadata_value", "field": key.value,
                                   "message": "Citation keys must be YAML strings; quote numeric or boolean values."})
                    continue
                old_key = scalar.value.removeprefix("@")
                state = self.states.get(old_key)
                replacement = None
                if state and state["new_key"]:
                    replacement = ("@" if scalar.value.startswith("@") else "") + state["new_key"]
                    replacement = json.dumps(replacement, ensure_ascii=False)
                emit(old_key, offset + scalar.start_mark.index, offset + scalar.end_mark.index,
                     "yaml_citekey", replacement)


def apply_edits(text: str, edits: list[Edit]) -> str:
    pieces, cursor = [], 0
    for edit in edits:
        pieces.extend((text[cursor:edit.start], edit.replacement))
        cursor = edit.end
    pieces.append(text[cursor:])
    return "".join(pieces)
