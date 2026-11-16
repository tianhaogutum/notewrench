"""Conservative HTML serialization with optional explicitly mapped local images.

SPDX-License-Identifier: AGPL-3.0-only
"""

from collections import Counter
from html import escape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


TAGS = frozenset("a abbr article aside b blockquote br caption code col colgroup dd del details div dl dt em figcaption figure h1 h2 h3 h4 h5 h6 hr i kbd li main mark ol p pre q s samp section small span strong sub summary sup table tbody td tfoot th thead time tr u ul var wbr".split())
VOID = frozenset({"br", "hr", "wbr", "col"})
DROP = frozenset("script style iframe object embed svg math template noscript textarea title xmp plaintext noembed noframes audio video canvas frameset frame".split())


def web_url(value: str) -> bool:
    """Only absolute HTTP(S), with a host and no controls or credentials."""
    if not value or any(c.isspace() or ord(c) < 32 or ord(c) == 127 or c == "\\" for c in value):
        return False
    try:
        parts = urlsplit(value)
        return (parts.scheme.lower() in {"http", "https"} and bool(parts.hostname)
                and parts.username is None and parts.password is None
                and parts.port != 0)
    except ValueError:
        return False


def safe_href(value: str, base: str) -> str | None:
    if value.startswith("#") and not any(ord(c) < 32 for c in value):
        return value
    if any(c.isspace() or ord(c) < 32 or c == "\\" for c in value):
        return None
    try:
        resolved = urljoin(base, value)
    except ValueError:
        return None
    return resolved if web_url(resolved) else None


class SafeHTML(HTMLParser):
    """Re-emit only known HTML tags and inert, escaped attribute values.

    Input markup is never copied verbatim. Only an explicit resolver may supply
    image paths; foreign namespaces, CSS, forms and embedded media are excluded.
    """

    def __init__(self, base: str, image_resolver=None):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.image_resolver = image_resolver
        self.parts: list[str] = []
        self.stack: list[str] = []
        self.blocked: list[str] = []
        self.changes: Counter = Counter()
        self.text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self.blocked:
            if tag in DROP and tag not in {"embed", "frame"}:
                self.blocked.append(tag)
            return
        if tag in DROP:
            self.changes["removed_active_elements"] += 1
            if tag not in {"embed", "frame"}:
                self.blocked.append(tag)
            return
        if tag == "img":
            source = next((v for k, v in attrs if k == "src"), None)
            target = self.image_resolver(source) if self.image_resolver else None
            if target:
                # Paths come solely from hash-checked manifest bytes, never HTML.
                clean = [("src", target)]
                seen = {"src"}
                source_seen = False
                for key, value in attrs:
                    if key == "src" and not source_seen:
                        source_seen = True
                        continue
                    if key in {"alt", "title", "lang"} and key not in seen and value is not None:
                        clean.append((key, value))
                        seen.add(key)
                    else:
                        self.changes["removed_attributes"] += 1
                self.parts.append("<img" + "".join(f' {k}="{escape(v, quote=True)}"' for k, v in clean) + ">")
                self.changes["archived_images"] += 1
                return
            self.changes["missing_image_asset"] += 1
        if tag not in TAGS:
            self.changes["removed_elements"] += 1
            if tag == "img":
                alt = next((v for k, v in attrs if k == "alt" and v), "")
                self.handle_data(alt)
            return
        clean = []
        seen = set()
        for key, value in attrs:
            if key in seen or value is None:
                self.changes["removed_attributes"] += 1
                continue
            seen.add(key)
            if key in {"title", "lang"} or (key == "dir" and value in {"ltr", "rtl", "auto"}):
                clean.append((key, value))
            elif tag == "a" and key == "href":
                href = safe_href(value, self.base)
                if href is not None:
                    clean.append(("href", href))
                    if href != value:
                        self.changes["resolved_relative_links"] += 1
                else:
                    self.changes["removed_unsafe_links"] += 1
            elif ((tag in {"td", "th"} and key in {"colspan", "rowspan"})
                  or (tag == "ol" and key == "start")) and value.isascii() and value.isdigit() and len(value) <= 4:
                clean.append((key, value))
            else:
                self.changes["removed_attributes"] += 1
        if tag == "a":
            clean.append(("rel", "noreferrer noopener"))
        self.parts.append("<" + tag + "".join(f' {k}="{escape(v, quote=True)}"' for k, v in clean) + ">")
        if tag not in VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.blocked:
            if tag == self.blocked[-1]:
                self.blocked.pop()
            return
        if tag in self.stack:
            while self.stack:
                opened = self.stack.pop()
                self.parts.append(f"</{opened}>")
                if opened == tag:
                    break

    def handle_data(self, data):
        if not self.blocked:
            self.parts.append(escape(data))
            self.text.append(data)

    def handle_comment(self, data):
        self.changes["removed_comments"] += 1

    def handle_decl(self, decl):
        self.changes["removed_declarations"] += 1

    def unknown_decl(self, data):
        self.changes["removed_declarations"] += 1

    def finish(self):
        self.close()
        while self.stack:
            self.parts.append(f"</{self.stack.pop()}>")
        return "".join(self.parts), dict(sorted(self.changes.items())), "".join(self.text).strip()


def sanitize(content: str, base: str, image_resolver=None):
    parser = SafeHTML(base, image_resolver)
    parser.feed(content)
    return parser.finish()


def document(title: str, body: str) -> str:
    # Only same-directory images; no scripts, styles, forms or documents, even if a
    # browser repairs malformed input differently from HTMLParser.
    return ("<!doctype html>\n<html><head><meta charset=\"utf-8\">"
            "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; "
            "img-src 'self'; base-uri 'none'; form-action 'none'\">"
            "<meta name=\"referrer\" content=\"no-referrer\">"
            f"<title>{escape(title)}</title></head><body>{body}</body></html>\n")
