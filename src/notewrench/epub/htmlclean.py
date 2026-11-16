"""Conservative HTML-to-XHTML conversion using a strict element/attribute allowlist."""
from html.parser import HTMLParser
import re
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

from . import BundleError

XHTML = "http://www.w3.org/1999/xhtml"
ALLOWED = frozenset("p div span section article h1 h2 h3 h4 h5 h6 blockquote ul ol li dl dt dd pre code em strong b i u s sub sup br hr a table caption thead tbody tfoot tr th td figure figcaption abbr q cite time kbd samp var small mark".split())
VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
BLOCKED = frozenset("script style iframe object embed svg math canvas audio video form input button textarea select option template noscript head".split())
BLOCK = frozenset("p div section article h1 h2 h3 h4 h5 h6 blockquote ul ol dl pre table figure".split())


def check_text(text):
    if any(not (c in "\t\n\r" or 0x20 <= ord(c) <= 0xD7FF or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF) for c in text):
        raise BundleError("text contains a character forbidden by XML 1.0")


def safe_url(value, base="", *, mailto=True):
    if not value or any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value:
        return None
    try:
        value = urljoin(base, value)
        parts = urlsplit(value)
        if parts.scheme.lower() in {"http", "https"} and parts.hostname:
            parts.port  # reject malformed ports
            return value
        if mailto and parts.scheme.lower() == "mailto" and parts.path and not parts.netloc:
            return value
    except ValueError:
        pass
    return None


class Cleaner(HTMLParser):
    def __init__(self, source_url, image_resolver=None):
        super().__init__(convert_charrefs=True)
        self.root = ET.Element("div")
        self.stack = [self.root]
        self.suppressed = []
        self.events = []
        self.base = safe_url(source_url, mailto=False) or ""
        self.ids = set()
        self.image_resolver = image_resolver

    def event(self, code, **details):
        self.events.append({"code": code, **details})

    def text(self, value):
        parent = self.stack[-1]
        if len(parent):
            parent[-1].tail = (parent[-1].tail or "") + value
        else:
            parent.text = (parent.text or "") + value

    def handle_data(self, data):
        if not self.suppressed:
            check_text(data)
            self.text(data)

    def handle_starttag(self, tag, attrs):
        if self.suppressed:
            if tag not in VOID:
                self.suppressed.append(tag)
            if len(self.suppressed) > 128:
                raise BundleError("HTML nesting exceeds 128")
            return
        if tag in BLOCKED:
            self.event("removed_active_element", element=tag)
            if tag not in VOID:
                self.suppressed.append(tag)
            return
        if tag == "img":
            attrs = dict(attrs)
            src = attrs.get("src") or ""
            alt = attrs.get("alt") or ""
            srcset = attrs.get("srcset") or ""
            for value in (src, alt, srcset):
                check_text(value)
            href = self.image_resolver(src) if self.image_resolver else None
            if href:
                ET.SubElement(self.stack[-1], "img", {"src": href, "alt": alt})
                self.event("embedded_resource", element="img", url=src, href=href, alt=alt)
                if srcset:
                    self.event("missing_resource", element="img", attribute="srcset", srcset=srcset,
                               reason="srcset_not_loaded")
                for name in sorted(attrs.keys() - {"src", "alt", "srcset"}):
                    self.event("removed_attribute", element=tag, attribute=name)
                return
            self.event("missing_resource", element="img", url=src, srcset=srcset, alt=alt,
                       reason="image_not_declared" if self.image_resolver else "images_are_never_loaded")
            self.text(f"[Image omitted: {alt or src or srcset or 'no description'}]")
            return
        if tag in {"html", "body"}:
            return
        if tag not in ALLOWED:
            self.event("unwrapped_element", element=tag)
            return
        # Repair common HTML optional end tags without pretending to be an HTML5 browser.
        if tag in BLOCK and self.stack[-1].tag == "p":
            self.stack.pop()
        if tag in {"li", "dt", "dd", "p", "tr", "td", "th"}:
            for i in range(len(self.stack) - 1, 0, -1):
                current = self.stack[i].tag
                if current == tag:
                    del self.stack[i:]
                    break
                if current in {"ul", "ol", "dl", "table", "tr"}:
                    break
        element = ET.SubElement(self.stack[-1], tag)
        for name, value in attrs:
            value = value or ""
            check_text(value)
            if name == "href" and tag == "a":
                if value.startswith("#") and re.fullmatch(r"#[A-Za-z][A-Za-z0-9_.:-]*", value):
                    element.set("href", value)
                else:
                    href = safe_url(value, self.base)
                    if href:
                        element.set("href", href)
                    else:
                        self.event("removed_unsafe_uri", element=tag, attribute=name, value=value)
            elif name == "id" and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", value) and value not in self.ids:
                self.ids.add(value)
                element.set("id", value)
            elif name == "title":
                element.set(name, value)
            elif name in {"colspan", "rowspan"} and tag in {"td", "th"} and len(value) <= 3 and value.isascii() and value.isdigit() and 1 <= int(value) <= 100:
                element.set(name, value)
            elif name == "start" and tag == "ol" and re.fullmatch(r"-?[0-9]{1,6}", value):
                element.set(name, value)
            else:
                self.event("removed_attribute", element=tag, attribute=name)
        if tag not in VOID:
            if len(self.stack) >= 128:
                raise BundleError("HTML nesting exceeds 128")
            self.stack.append(element)

    def handle_endtag(self, tag):
        if self.suppressed:
            if tag in self.suppressed:
                index = len(self.suppressed) - 1 - self.suppressed[::-1].index(tag)
                del self.suppressed[index:]
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_decl(self, decl):
        if decl.lower() != "doctype html":
            raise BundleError("unsupported HTML declaration (only <!DOCTYPE html> is accepted)")
        self.event("removed_doctype")

    def unknown_decl(self, data):
        raise BundleError("unsupported HTML marked declaration")

    def handle_pi(self, data):
        raise BundleError("HTML processing instructions are unsupported")

    def handle_comment(self, data):
        self.event("removed_comment")


def clean_body(body, is_html, source_url="", *, image_resolver=None):
    check_text(body)
    if not is_html:
        root = ET.Element("div")
        ET.SubElement(root, "pre").text = body
        return root, []
    parser = Cleaner(source_url, image_resolver)
    try:
        parser.feed(body)
        parser.close()
    except (RecursionError, AssertionError) as exc:
        raise BundleError(f"unsupported malformed HTML: {exc}") from exc
    for link in parser.root.iter("a"):
        href = link.get("href", "")
        if href.startswith("#") and href[1:] not in parser.ids:
            del link.attrib["href"]
            parser.event("removed_broken_fragment", value=href)
    return parser.root, parser.events
