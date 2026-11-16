"""Deterministic EPUB 3 writer and bounded static-profile structural validator."""
import hashlib
import io
import json
import posixpath
import re
import struct
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET
import zipfile
import zlib

from . import BundleError
from .htmlclean import ALLOWED, XHTML, safe_url
from .images import validate_image

OPF = "http://www.idpf.org/2007/opf"
DC = "http://purl.org/dc/elements/1.1/"
OCF = "urn:oasis:names:tc:opendocument:xmlns:container"
EPUB = "http://www.idpf.org/2007/ops"
XML = "http://www.w3.org/XML/1998/namespace"
MIMETYPE = b"application/epub+zip"
DEFAULT_MODIFIED = "2000-01-01T00:00:00Z"


def q(namespace, name):
    return f"{{{namespace}}}{name}"


def xml_bytes(root):
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def json_bytes(data):
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def document(title, language):
    root = ET.Element("html", {"xmlns": XHTML, "xmlns:epub": EPUB,
                               "lang": language, "xml:lang": language})
    head = ET.SubElement(root, "head")
    ET.SubElement(head, "title").text = title
    body = ET.SubElement(root, "body")
    return root, body


def make_epub(chapters, *, title, author, language, modified, images=()):
    """chapters contains (Article, cleaned ET div); order is the requested book order."""
    identity = {"title": title, "author": author, "language": language, "modified": modified,
                "chapters": [{"key": a.key, "title": a.title, "url": a.url,
                              "content": ET.tostring(body, encoding="unicode")}
                             for a, body in chapters]}
    if images:
        identity["images"] = [image.sha256 for image in images]
    identifier = "urn:sha256:" + hashlib.sha256(json_bytes(identity)).hexdigest()
    files = []
    container = ET.Element("container", {"xmlns": OCF, "version": "1.0"})
    roots = ET.SubElement(container, "rootfiles")
    ET.SubElement(roots, "rootfile", {"full-path": "EPUB/package.opf", "media-type": "application/oebps-package+xml"})
    files.append(("META-INF/container.xml", xml_bytes(container)))
    package = ET.Element("package", {"xmlns": OPF, "xmlns:dc": DC, "version": "3.0", "unique-identifier": "book-id"})
    metadata = ET.SubElement(package, "metadata")
    ET.SubElement(metadata, "dc:identifier", {"id": "book-id"}).text = identifier
    ET.SubElement(metadata, "dc:title").text = title
    ET.SubElement(metadata, "dc:language").text = language
    ET.SubElement(metadata, "dc:creator").text = author
    ET.SubElement(metadata, "meta", {"property": "dcterms:modified"}).text = modified
    manifest = ET.SubElement(package, "manifest")
    ET.SubElement(manifest, "item", {"id": "nav", "href": "nav.xhtml", "media-type": "application/xhtml+xml", "properties": "nav"})
    spine = ET.SubElement(package, "spine")
    nav_root, nav_body = document(title, language)
    nav = ET.SubElement(nav_body, "nav", {"epub:type": "toc", "id": "toc"})
    ET.SubElement(nav, "h1").text = title
    toc = ET.SubElement(nav, "ol")
    for i, (article, content) in enumerate(chapters, 1):
        name = f"chapter-{i:04d}.xhtml"
        chapter_id = f"chapter-{i:04d}"
        ET.SubElement(manifest, "item", {"id": chapter_id, "href": name, "media-type": "application/xhtml+xml"})
        ET.SubElement(spine, "itemref", {"idref": chapter_id})
        li = ET.SubElement(toc, "li")
        ET.SubElement(li, "a", {"href": name}).text = article.title
        root, body = document(article.title, language)
        ET.SubElement(body, "h1").text = article.title
        source = ET.SubElement(body, "p")
        source.text = f"Source ({article.key}): "
        if article.url:
            url = safe_url(article.url, mailto=False)
            if url:
                ET.SubElement(source, "a", {"href": url}).text = article.url
            else:
                source.text += article.url + " [non-web source; inactive]"
        else:
            source.text += "local text bookmark"
        body.append(content)
        files.append((f"EPUB/{name}", xml_bytes(root)))
    for image in images:
        ET.SubElement(manifest, "item", {"id": f"image-{image.sha256}", "href": image.href, "media-type": image.media_type})
        files.append((f"EPUB/{image.href}", image.data))
    files.insert(1, ("EPUB/package.opf", xml_bytes(package)))
    files.insert(2, ("EPUB/nav.xhtml", xml_bytes(nav_root)))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in [("mimetype", MIMETYPE), *files]:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)
    return out.getvalue(), identifier


def _require(condition, message):
    if not condition:
        raise BundleError(message)


def _path(name):
    _require(bool(name) and not name.startswith("/") and "\\" not in name
             and all(p not in {"", ".", ".."} for p in name.split("/")), f"unsafe ZIP path: {name!r}")
    _require(not any(ord(c) < 32 for c in name), "control character in ZIP path")
    return name


def _resolve(base, href):
    parts = urlsplit(href)
    _require(not parts.scheme and not parts.netloc and not parts.query, f"non-local resource reference: {href}")
    target = unquote(parts.path)
    _require("\\" not in target and not target.startswith("/"), f"unsafe resource reference: {href}")
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(base), target)) if target else base
    return _path(resolved), unquote(parts.fragment)


def validate_epub(source):
    """Validate the static XHTML profile emitted by notewrench epub, without extracting ZIPs.

    This is intentionally not a substitute for EPUBCheck or a validator of every EPUB feature.
    """
    try:
        with zipfile.ZipFile(source) as archive:
            return _validate(archive)
    except (OSError, zipfile.BadZipFile, ET.ParseError, UnicodeError, ValueError, KeyError, RuntimeError, NotImplementedError, EOFError, struct.error, zlib.error) as exc:
        if isinstance(exc, BundleError):
            raise
        raise BundleError(f"invalid EPUB: {exc}") from exc


def _validate(archive):
    infos = archive.infolist()
    _require(0 < len(infos) <= 10003, "invalid ZIP member count")
    names = [i.filename for i in infos]
    _require(len(set(names)) == len(names), "duplicate ZIP member")
    for info in infos:
        _path(info.filename)
        _require(not info.flag_bits & 1, "encrypted ZIP members are unsupported")
        _require(info.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}, "unsupported ZIP compression")
        _require(info.file_size <= 16 * 1024 * 1024, "ZIP member exceeds 16 MiB")
        _require((info.external_attr >> 16) & 0o170000 != 0o120000, "ZIP symlinks are unsupported")
    _require(sum(i.file_size for i in infos) <= 64 * 1024 * 1024, "EPUB exceeds 64 MiB uncompressed")
    first = infos[0]
    _require(first.filename == "mimetype" and first.header_offset == 0 and first.compress_type == zipfile.ZIP_STORED and not first.extra, "mimetype must be first, uncompressed, and have no extra field")
    archive.fp.seek(0)
    header = archive.fp.read(30)
    _require(len(header) == 30 and header[:4] == b"PK\x03\x04", "invalid first ZIP header")
    fields = struct.unpack("<4s5H3I2H", header)
    _require(fields[3] == 0 and fields[-2] == 8 and fields[-1] == 0 and not fields[2] & 8, "invalid mimetype local header")
    blobs = {name: archive.read(name) for name in names}  # reads also verify CRCs
    _require(blobs["mimetype"] == MIMETYPE, "wrong mimetype bytes")

    def xml(name):
        _require(name in blobs, f"missing XML resource: {name}")
        text = blobs[name].decode("utf-8")
        _require(not re.search(r"<!DOCTYPE|<!ENTITY|<\?(?!xml\s)", text, re.I), "DTD/entities/processing instructions are unsupported")
        root = ET.fromstring(text)
        _require(sum(1 for _ in root.iter()) <= 200000, "too many XML elements")
        return root

    container = xml("META-INF/container.xml")
    _require(container.tag == q(OCF, "container") and container.get("version") == "1.0", "invalid container root")
    roots = container.findall(f"{q(OCF, 'rootfiles')}/{q(OCF, 'rootfile')}")
    _require(len(roots) == 1 and roots[0].get("media-type") == "application/oebps-package+xml", "expected one OPF rootfile")
    opf_name = _path(roots[0].get("full-path", ""))
    package = xml(opf_name)
    _require(package.tag == q(OPF, "package") and package.get("version") == "3.0", "expected EPUB 3 package")
    metadata = package.find(q(OPF, "metadata"))
    _require(metadata is not None, "missing metadata")
    for field in ("title", "language", "identifier"):
        _require(any((x.text or "").strip() for x in metadata.findall(q(DC, field))), f"missing dc:{field}")
    uid = package.get("unique-identifier")
    _require(uid and any(x.get("id") == uid and (x.text or "").strip() for x in metadata.findall(q(DC, "identifier"))), "invalid unique-identifier")
    mods = [x.text for x in metadata.findall(q(OPF, "meta")) if x.get("property") == "dcterms:modified"]
    _require(len(mods) == 1 and bool(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", mods[0] or "")), "missing/invalid dcterms:modified")
    manifest = package.find(q(OPF, "manifest"))
    spine = package.find(q(OPF, "spine"))
    _require(manifest is not None and spine is not None, "missing manifest/spine")
    items, paths, nav_names, documents, images = {}, set(), [], {}, set()
    for item in manifest:
        _require(item.tag == q(OPF, "item"), "unknown manifest element")
        item_id = item.get("id")
        _require(item_id and item_id not in items, "missing/duplicate manifest id")
        path, fragment = _resolve(opf_name, item.get("href", ""))
        _require(not fragment and path not in paths and path != opf_name, "invalid/duplicate manifest href")
        media_type = item.get("media-type")
        _require(media_type in {"application/xhtml+xml", "image/png", "image/jpeg"}, "validator profile supports XHTML, PNG and JPEG resources only")
        paths.add(path)
        items[item_id] = path
        if media_type in {"image/png", "image/jpeg"}:
            _require(path in blobs and not item.get("properties"), "missing image or unsupported image properties")
            validate_image(blobs[path], media_type)
            images.add(path)
        else:
            documents[path] = xml(path)
        if "nav" in item.get("properties", "").split():
            nav_names.append(path)
    _require(set(names) == {"mimetype", "META-INF/container.xml", opf_name} | paths, "ZIP members and manifest disagree")
    _require(len(nav_names) == 1, "expected exactly one nav document")
    order = []
    for ref in spine:
        _require(ref.tag == q(OPF, "itemref") and ref.get("idref") in items, "invalid spine idref")
        path = items[ref.get("idref")]
        _require(path in documents and path not in order and path not in nav_names and ref.get("linear", "yes") == "yes", "invalid/duplicate spine item")
        order.append(path)
    _require(bool(order), "empty spine")
    _require(set(documents) == set(order) | set(nav_names), "unreferenced chapter in manifest")
    ids = {}
    for path, root in documents.items():
        _require(root.tag == q(XHTML, "html") and root.find(q(XHTML, "head")) is not None and root.find(q(XHTML, "body")) is not None, "invalid XHTML document")
        _require(bool((root.findtext(f"{q(XHTML, 'head')}/{q(XHTML, 'title')}") or "").strip()), "missing XHTML title")
        ids[path] = set()
        for node in root.iter():
            _require(node.tag in {q(XHTML, t) for t in ALLOWED | {"html", "head", "title", "body", "nav", "img"}}, f"unsupported XHTML element: {node.tag}")
            is_image = node.tag == q(XHTML, "img")
            if is_image:
                _require("src" in node.attrib and "alt" in node.attrib and len(node) == 0
                         and not node.text, "invalid XHTML image")
            for attr in node.attrib:
                allowed_attrs = {"id", "title", "href", "lang", "colspan", "rowspan", "start", q(XML, "lang"), q(EPUB, "type")}
                _require(attr in ({"src", "alt"} if is_image else allowed_attrs), f"unsupported/unsafe XHTML attribute: {attr}")
            if "id" in node.attrib:
                _require(node.get("id") not in ids[path], "duplicate XHTML id")
                ids[path].add(node.get("id"))
    used_images = set()
    for path, root in documents.items():
        for node in root.iter():
            if node.tag == q(XHTML, "img"):
                src = node.get("src")
                target, fragment = _resolve(path, src)
                _require(target in images and not fragment, f"broken/non-local XHTML image: {src}")
                used_images.add(target)
            href = node.get("href")
            if href is not None:
                if safe_url(href):
                    continue
                target, fragment = _resolve(path, href)
                _require(target in documents and (not fragment or fragment in ids[target]), f"broken XHTML link: {href}")
    _require(images == used_images, "unreferenced image in manifest")
    nav_root = documents[nav_names[0]]
    navs = [n for n in nav_root.iter(q(XHTML, "nav")) if "toc" in n.get(q(EPUB, "type"), "").split()]
    _require(len(navs) == 1 and navs[0].find(q(XHTML, "ol")) is not None, "missing/duplicate EPUB toc nav")
    links = list(navs[0].iter(q(XHTML, "a")))
    targets = [_resolve(nav_names[0], n.get("href", ""))[0] for n in links]
    _require(targets == order and all("".join(n.itertext()).strip() for n in links), "nav does not match spine order/titles")
    return {"valid": True, "profile": "readbundle-static-xhtml", "chapters": len(order), "members": len(names)}
