# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (see licenses/zotero-dupes-MIT.txt)
"""Read Zotero/Better BibTeX/CSL JSON and native Zotero RDF exports.

RDF parsing is delegated to RDFLib; this module maps the actual Zotero exporter
vocabulary, not arbitrary RDF. Original JSON and RDF statements are retained.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
from pathlib import Path
from typing import Any

from defusedxml import ElementTree
from rdflib import BNode, Graph, Literal, Namespace, RDF, URIRef
from rdflib.compare import to_canonical_graph

from .model import InputError, Record, canonical, digest, identifier_values, zotero_item_key

DC = Namespace("http://purl.org/dc/elements/1.1/")
DCT = Namespace("http://purl.org/dc/terms/")
Z = Namespace("http://www.zotero.org/namespaces/export#")
BIB = Namespace("http://purl.org/net/biblio#")
FOAF = Namespace("http://xmlns.com/foaf/0.1/")
PRISM = Namespace("http://prismstandard.org/namespaces/1.2/basic/")
LINK = Namespace("http://purl.org/rss/1.0/modules/link/")
VCARD = Namespace("http://nwalsh.com/rdf/vCard#")
EXPORT_BASE = "https://library-bridge.invalid/export"
MAX_BYTES = 16 * 1024 * 1024
MAX_RECORDS = 10_000


def read_json(path: Path, max_bytes: int = MAX_BYTES, *, max_depth: int = 64) -> Any:
    if not path.is_file():
        raise InputError(f"JSON input is not a regular file: {path}")
    if path.stat().st_size > max_bytes:
        raise InputError(f"{path.name}: exceeds the {max_bytes // 1024**2} MiB input limit; export a smaller collection")
    try:
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate object key {key!r}")
                result[key] = value
            return result
        with path.open(encoding="utf-8-sig") as stream:
            payload = json.load(stream, object_pairs_hook=unique_object, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f"invalid JSON number {x}")))
        def check_depth(value, depth=0):
            if depth > max_depth:
                raise ValueError(f"JSON nesting exceeds {max_depth} levels")
            if isinstance(value, dict):
                for key, child in value.items():
                    check_depth(key, depth + 1)
                    check_depth(child, depth + 1)
            elif isinstance(value, list):
                for child in value:
                    check_depth(child, depth + 1)
            elif isinstance(value, str):
                try:
                    value.encode("utf-8")
                except UnicodeEncodeError as exc:
                    raise ValueError("JSON contains an unpaired Unicode surrogate") from exc
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError("JSON number exceeds the finite floating-point range")
        check_depth(payload)
        return payload
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InputError(f"{path.name}: invalid UTF-8 JSON: {exc}") from exc


def _list(value: Any, name: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise InputError(f"{name} must be an array")
    return value


def _content_list(value: Any, name: str, *, strings: bool = True) -> list:
    values = _list(value, name)
    for entry in values:
        if not isinstance(entry, dict) and not (strings and isinstance(entry, str)):
            raise InputError(f"{name} entries must be {'strings or objects' if strings else 'objects'}")
        if isinstance(entry, dict) and "note" in entry and not isinstance(entry["note"], str):
            raise InputError(f"{name} note values must be strings")
    return values


def _tags(data: dict) -> list:
    value = data.get("tags", data.get("keyword", []))
    # CSL's keyword field can be one unstructured string. Do not invent a
    # delimiter: retain the complete value as one entry and preserve raw too.
    if "tags" not in data and isinstance(value, str):
        value = [value] if value else []
    tags = _content_list(value, "tags")
    for tag in tags:
        if isinstance(tag, dict) and not isinstance(tag.get("tag"), str):
            raise InputError("tag objects must contain a string tag value")
    return tags


def _creators(value: list) -> str:
    names = []
    if any(not isinstance(creator, dict) for creator in value):
        raise InputError("creator entries must be objects")
    primary = [creator for creator in value if creator.get("creatorType", "author") == "author"]
    if primary:
        value = primary
    for creator in value:
        for key in ["firstName", "lastName", "name", "given", "family", "literal", "creatorType"]:
            if creator.get(key) is not None and not isinstance(creator[key], str):
                raise InputError(f"creator {key} values must be strings")
        family = creator.get("lastName", creator.get("family", ""))
        given = creator.get("firstName", creator.get("given", ""))
        names.append(str(creator.get("name", creator.get("literal", "")) or "") or ", ".join(str(x) for x in [family, given] if x))
    return " and ".join(names)


def _json_record(library: str, original: dict) -> Record:
    data = original.get("data", original)
    if not isinstance(data, dict):
        raise InputError("Zotero API item data must be an object")
    for name in ["key", "itemKey", "id", "itemID", "citationKey", "citation-key"]:
        value = data.get(name, original.get(name))
        if value is not None and (not isinstance(value, (str, int)) or isinstance(value, bool)):
            raise InputError(f"{name} must be a string or integer")
    source_id, quality = "", ""
    uri = data.get("uri")
    if not uri and zotero_item_key(data.get("id")):
        uri = data["id"]
    if not uri and isinstance(original.get("links"), dict):
        alternate = original["links"].get("alternate", {})
        uri = alternate.get("href") if isinstance(alternate, dict) else None
    if uri:
        if not isinstance(uri, str):
            raise InputError("Item uri must be a string")
        uri_key = zotero_item_key(uri)
        explicit_key = data.get("key", data.get("itemKey", original.get("key")))
        if uri_key and explicit_key and str(explicit_key) != uri_key:
            raise InputError("Zotero item key and URI disagree; regenerate this export")
        if uri_key:
            source_id, quality = "key:" + uri_key, "zotero-uri"
        elif explicit_key is not None and explicit_key != "":
            source_id, quality = "key:" + str(explicit_key), "zotero-key" if re.fullmatch(r"[A-Z0-9]{8}", str(explicit_key)) else "export-key"
        else:
            source_id, quality = "uri:" + uri, "export-uri"
    else:
        for key, kind in [("key", "zotero-key"), ("itemKey", "zotero-key"), ("id", "export-id"), ("itemID", "local-item-id"), ("citationKey", "citation-key"), ("citation-key", "citation-key")]:
            value = data.get(key, original.get(key))
            if value is not None and value != "":
                if not isinstance(value, (str, int)) or isinstance(value, bool):
                    raise InputError(f"{key} must be a string or integer")
                source_id, quality = key + ":" + str(value), kind
                if kind == "zotero-key":
                    source_id = "key:" + str(value)
                if kind == "zotero-key" and not re.fullmatch(r"[A-Z0-9]{8}", str(value)):
                    quality = "export-key"
                break
    if not source_id:
        source_id, quality = "content:" + digest(original), "content-fallback"
    title = data.get("title", "")
    if not isinstance(title, str):
        raise InputError(f"{source_id}: title must be a string")
    aliases = {
        "type": ["itemType", "type"], "date": ["date", "issued"],
        "publication": ["publicationTitle", "container-title"],
        "pages": ["pages", "page"], "issue": ["issue", "number"],
        "abstract": ["abstractNote", "abstract"], "DOI": ["DOI", "doi"],
        "ISBN": ["ISBN", "isbn"], "volume": ["volume"], "url": ["url", "URL"],
        "PMID": ["PMID", "pmid"],
    }
    metadata: dict[str, Any] = {"title": title}
    for dest, sources in aliases.items():
        for key in sources:
            if key in data and data[key] is not None:
                metadata[dest] = data[key]
                break
    if not isinstance(metadata.get("type", ""), str):
        raise InputError(f"{source_id}: itemType/type must be a string")
    for key in ["DOI", "ISBN", "PMID", "volume", "issue", "pages", "url", "abstract"]:
        if key in metadata and (not isinstance(metadata[key], (str, int, float)) or isinstance(metadata[key], bool)):
            raise InputError(f"{source_id}: {key} must be a string or number")
    date = metadata.get("date", "")
    if not isinstance(date, (str, int, dict)) or isinstance(date, bool):
        raise InputError(f"{source_id}: date/issued must be a string, year number or CSL date object")
    if isinstance(date, dict):
        parts = date.get("date-parts", [])
        if not isinstance(parts, list) or any(not isinstance(row, list) for row in parts):
            raise InputError(f"{source_id}: CSL date-parts must be an array of arrays")
    creators = _list(data.get("creators", data.get("author", [])), "creators/author")
    metadata["authors"] = _creators(creators)
    if creators:
        metadata["creators"] = creators
    # Fields absent from the matching schema still belong in field review.
    excluded = {"title", "creators", "author", "tags", "notes", "attachments", "annotations", "data"}
    excluded.update(key for keys in aliases.values() for key in keys)
    excluded.update({"uri", "id", "itemID", "key", "itemKey", "parentItem", "sourceItemID", "relations", "collections", "dateAdded", "dateModified", "version"})
    for key, value in data.items():
        if key not in excluded:
            metadata[key] = value
    notes = _content_list(data.get("notes"), "notes")[:]
    if isinstance(data.get("note"), str) and data["note"]:
        notes.append({"note": data["note"]})
    annotations = _content_list(data.get("annotations"), "annotations")[:]
    attachments = _list(data.get("attachments"), "attachments")[:]
    for attachment in attachments:
        if not isinstance(attachment, dict):
            raise InputError("attachments must contain objects")
        body = attachment.get("data", attachment)
        if not isinstance(body, dict):
            raise InputError("attachment data must be an object")
        annotations.extend(_content_list(body.get("annotations"), "attachment annotations"))
        if body.get("note"):
            if not isinstance(body["note"], str):
                raise InputError("attachment note must be a string")
            notes.append({"attachment": body.get("key", attachment.get("key", body.get("title", ""))), "note": body["note"]})
    return Record(library, source_id, quality, metadata, original, _tags(data), notes, annotations, attachments)


def _json_records(library: str, payload: Any) -> tuple[list[Record], list[str]]:
    warnings: list[str] = []
    if isinstance(payload, dict) and "items" in payload:
        items = _list(payload["items"], "items")
    elif isinstance(payload, list):
        items = payload
    else:
        raise InputError("Expected a JSON array or a Better BibTeX object with an items array")
    if len(items) > MAX_RECORDS:
        raise InputError(f"More than {MAX_RECORDS:,} JSON items; export a smaller collection")
    records: list[Record] = []
    children: list[tuple[dict, dict]] = []
    parent_index: dict[str, Record] = {}
    for item in items:
        if not isinstance(item, dict):
            raise InputError("Every JSON item must be an object")
        data = item.get("data", item)
        if not isinstance(data, dict):
            raise InputError("Every item data value must be an object")
        if data is not item and item.get("key") and data.get("key") and item["key"] != data["key"]:
            raise InputError("Zotero API wrapper and data keys disagree")
        item_type = data.get("itemType", "")
        if not isinstance(item_type, str):
            raise InputError("itemType must be a string")
        if item_type in {"note", "attachment", "annotation"}:
            children.append((item, data))
            continue
        record = _json_record(library, item)
        records.append(record)
        aliases = [data.get(key, item.get(key)) for key in ["key", "itemKey", "itemID", "id", "uri"]]
        if record.source_id.startswith("key:"):
            aliases.append(record.source_id.removeprefix("key:"))
        for value in aliases:
            if value is not None:
                existing = parent_index.get(str(value))
                if existing is not None and existing.id != record.id:
                    raise InputError(f"Ambiguous parent item reference {value!r}; export IDs/keys must identify one item")
                parent_index[str(value)] = record
    attachment_parents: dict[str, Record] = {}
    for record in records:
        for attachment in record.attachments:
            body = attachment.get("data", attachment)
            for key in ["key", "itemID", "id"]:
                alias = body.get(key, attachment.get(key))
                if alias is not None:
                    if str(alias) in attachment_parents and attachment_parents[str(alias)].id != record.id:
                        raise InputError(f"Ambiguous attachment parent reference {alias!r}")
                    attachment_parents[str(alias)] = record
    # Attachment pass first so top-level annotations can find their owning item.
    for original, child in sorted(children, key=lambda x: x[1].get("itemType") != "attachment"):
        parent_key = child.get("parentItem", child.get("sourceItemID"))
        item_parent = parent_index.get(str(parent_key))
        attachment_parent = attachment_parents.get(str(parent_key))
        if item_parent is not None and attachment_parent is not None and item_parent.id != attachment_parent.id:
            raise InputError(f"Ambiguous parent reference {parent_key!r} matches both a bibliographic item and an attachment")
        parent = item_parent or attachment_parent
        if not parent:
            warnings.append(f"Unattached {child.get('itemType')} {child.get('key', child.get('itemID', '?'))} is not a bibliographic candidate; include its parent in the export")
            continue
        kind = child.get("itemType")
        if kind == "note":
            if not isinstance(child.get("note", ""), str):
                raise InputError("child note values must be strings")
            parent.notes.append(original)
        elif kind == "annotation":
            parent.annotations.append(original)
        else:
            parent.attachments.append(original)
            parent.annotations.extend(_content_list(child.get("annotations"), "attachment annotations"))
            if child.get("note"):
                parent.notes.append({"attachment": child.get("key", ""), "note": child["note"]})
            for key in ["key", "itemID", "id"]:
                alias = child.get(key, original.get(key))
                if alias is not None:
                    if str(alias) in attachment_parents and attachment_parents[str(alias)].id != parent.id:
                        raise InputError(f"Ambiguous attachment parent reference {alias!r}")
                    attachment_parents[str(alias)] = parent
    if isinstance(payload, list) and any("type" in item and "itemType" not in item for item in items):
        warnings.append("CSL JSON usually omits Zotero child notes, annotation objects and automatic tag types. Missing export data cannot be reconstructed.")
    return records, warnings


def _rdf_records(library: str, path: Path) -> tuple[list[Record], list[str]]:
    data = path.read_bytes()
    # DefusedXML rejects DTDs/entities before RDFLib sees the document. No URI is
    # fetched: only this local byte string is passed to the RDF/XML parser.
    ElementTree.fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    graph = Graph().parse(source=io.BytesIO(data), format="xml", publicID=EXPORT_BASE)
    if len(graph) > 150_000:
        raise InputError("RDF export exceeds 150,000 triples; export a smaller collection")
    graph = to_canonical_graph(graph)
    warnings = ["RDF subjects are export identifiers when z:uri is absent. Keep library labels fixed; numeric #item_N subjects can change on re-export. Original RDF statements are retained.",
                "PDF bytes and embedded PDF highlights are not read. Only notes and annotation metadata present in the export can be compared."]

    def value(subject, predicate) -> str:
        return str(next(iter(graph.objects(subject, predicate)), ""))

    structural = {DCT.isPartOf, DCT.isReferencedBy, DC.publisher, BIB.presentedAt, LINK.link, VCARD.adr}
    expanded_nodes = 0

    def raw_node(subject, seen=None, depth=0) -> dict:
        nonlocal expanded_nodes
        expanded_nodes += 1
        if expanded_nodes > 100_000:
            raise InputError("RDF structure expands beyond 100,000 nested objects; export a smaller collection")
        seen = set() if seen is None else seen
        if depth > 12:
            raise InputError("RDF structural nesting exceeds 12 levels; export a smaller, standard Zotero RDF context")
        if subject in seen:
            return {"resource": str(subject)}
        seen = seen | {subject}
        result: dict[str, Any] = {"@id": str(subject)}
        for predicate, obj in sorted(graph.predicate_objects(subject), key=lambda pair: tuple(map(str, pair))):
            if isinstance(obj, Literal):
                v = {"value": str(obj)}
                if obj.datatype:
                    v["datatype"] = str(obj.datatype)
                if obj.language:
                    v["language"] = obj.language
            elif isinstance(obj, BNode) or (predicate in structural and any(graph.triples((obj, None, None)))):
                v = raw_node(obj, seen, depth + 1)
            else:
                v = {"resource": str(obj)}
            result.setdefault(str(predicate), []).append(v)
        return result

    records = []
    for subject, item_type in sorted(graph.subject_objects(Z.itemType), key=lambda pair: str(pair[0])):
        item_type = str(item_type)
        if item_type in {"attachment", "note", "annotation"}:
            continue
        metadata: dict[str, Any] = {"title": value(subject, DC.title), "type": item_type}
        mapping = {"date": DC.date, "pages": BIB.pages, "abstract": DCT.abstract, "extra": DC.description, "rights": DC.rights,
                   "accessDate": DCT.dateSubmitted, "issued": DCT.issued, "language": Z.language, "archiveLocation": DC.coverage}
        for key, predicate in mapping.items():
            if val := value(subject, predicate):
                metadata[key] = val
        creators = []
        for predicate, creator_seq in sorted(graph.predicate_objects(subject), key=lambda pair: str(pair[0])):
            if not any(graph.triples((creator_seq, RDF.type, RDF.Seq))):
                continue
            role = {BIB.authors: "author", BIB.editors: "editor", BIB.contributors: "contributor"}.get(predicate)
            if role is None and str(predicate).startswith(str(Z)):
                role = str(predicate)[len(str(Z)):]
            if not role:
                continue
            memberships = [(int(str(p).rsplit("_", 1)[1]), o) for p, o in graph.predicate_objects(creator_seq) if str(p).startswith(str(RDF) + "_") and str(p).rsplit("_", 1)[1].isdigit()]
            for _, author in sorted(memberships):
                creators.append({"creatorType": role, "lastName": value(author, FOAF.surname) or value(author, FOAF.name), "firstName": value(author, FOAF.givenName)})
        metadata["authors"] = _creators(creators)
        if creators:
            metadata["creators"] = creators
        containers, queue = [], list(graph.objects(subject, DCT.isPartOf))
        while queue and len(containers) < 12:
            container = queue.pop(0)
            if container in containers:
                continue
            containers.append(container)
            queue.extend(graph.objects(container, DCT.isPartOf))
        for node in [subject] + containers:
            if node != subject and value(node, DC.title):
                if (node, RDF.type, BIB.Part) in graph:
                    metadata["section"] = value(node, DC.title)
                elif (node, RDF.type, BIB.Series) in graph:
                    metadata["series"] = value(node, DC.title)
                elif not metadata.get("publication"):
                    metadata["publication"] = value(node, DC.title)
            if val := value(node, DCT.alternative):
                metadata.setdefault("publicationAbbreviation", val)
            for key, predicate in {"volume": PRISM.volume, "issue": PRISM.number, "edition": PRISM.edition}.items():
                if val := value(node, predicate):
                    metadata.setdefault(key, val)
            for identifier in graph.objects(node, DC.identifier):
                if isinstance(identifier, Literal):
                    text = str(identifier)
                    for prefix in ["DOI", "ISBN", "ISSN"]:
                        if text.upper().startswith(prefix + " "):
                            metadata.setdefault(prefix, text[len(prefix) + 1:])
                elif (identifier, RDF.type, DCT.URI) in graph:
                    metadata["url"] = value(identifier, RDF.value)
        for publisher in graph.objects(subject, DC.publisher):
            if isinstance(publisher, Literal):
                metadata["publisher"] = str(publisher)
            else:
                metadata["publisher"] = value(publisher, FOAF.name)
                for address in graph.objects(publisher, VCARD.adr):
                    if val := value(address, VCARD.locality):
                        metadata["place"] = val
        for conference in graph.objects(subject, BIB.presentedAt):
            metadata["conferenceName"] = value(conference, DC.title)
        for predicate, obj in graph.predicate_objects(subject):
            if str(predicate).startswith(str(Z)) and predicate not in {Z.itemType, Z.uri, Z.key, Z.itemKey} and isinstance(obj, Literal):
                metadata[str(predicate)[len(str(Z)):]] = str(obj)
        tags = []
        for tag in graph.objects(subject, DC.subject):
            if isinstance(tag, Literal):
                tags.append({"tag": str(tag), "type": 0})
            elif (tag, RDF.type, Z.AutomaticTag) in graph:
                tags.append({"tag": value(tag, RDF.value), "type": 1})
        notes = [{"source_id": str(note), "note": value(note, RDF.value), "rdf": raw_node(note)} for note in graph.objects(subject, DCT.isReferencedBy)]
        attachments, annotations = [], []
        for attachment in graph.objects(subject, LINK.link):
            attachments.append({"source_id": str(attachment), "rdf": raw_node(attachment)})
            if val := value(attachment, DC.description):
                notes.append({"attachment": str(attachment), "note": val})
        uri = value(subject, Z.uri)
        key = zotero_item_key(uri)
        explicit_key = value(subject, Z.key) or value(subject, Z.itemKey)
        if key and explicit_key and key != explicit_key:
            raise InputError("RDF Zotero URI and key disagree")
        source_id = "key:" + (key or explicit_key) if key or explicit_key else uri or str(subject).replace(EXPORT_BASE, "")
        quality = "zotero-uri" if key else "zotero-key" if re.fullmatch(r"[A-Z0-9]{8}", explicit_key) else "rdf-subject"
        records.append(Record(library, source_id, quality, metadata,
                              {"subject": str(subject).replace(EXPORT_BASE, ""), "rdf": raw_node(subject)},
                              sorted(tags, key=canonical), sorted(notes, key=canonical), annotations, sorted(attachments, key=canonical)))
    return records, warnings


def load_library(label: str, path: Path) -> tuple[list[Record], dict]:
    if not label.strip() or len(label) > 100 or any(ord(c) < 32 for c in label):
        raise InputError("Library labels must contain 1–100 printable characters")
    if not path.is_file():
        raise InputError(f"Export does not exist or is not a regular file: {path}")
    initial_stat = path.stat()
    if initial_stat.st_size > MAX_BYTES:
        raise InputError(f"{path.name}: exceeds 16 MiB; export a smaller collection")
    with path.open("rb") as stream:
        prefix = stream.read(128).lstrip(b"\xef\xbb\xbf \t\r\n")
    try:
        if prefix.startswith((b"<", b"\xff\xfe", b"\xfe\xff", b"\x00\x00\xfe\xff")):
            records, warnings = _rdf_records(label, path)
            fmt = "zotero-rdf"
        else:
            records, warnings = _json_records(label, read_json(path, max_depth=48))
            fmt = "json"
    except InputError:
        raise
    except Exception as exc:
        raise InputError(f"{path.name}: cannot parse export: {exc}") from exc
    ids = [record.id for record in records]
    if len(ids) != len(set(ids)):
        seen, duplicates = set(), []
        for record in records:
            if record.id in seen and record.source_id not in duplicates:
                duplicates.append(record.source_id)
            seen.add(record.id)
        examples = ", ".join(duplicates[:3])
        raise InputError(f"{label}: duplicate source identities ({examples}); include each item once. CSL/citation export IDs can collide across different papers: prefer an export with Zotero keys/URIs. Items were not coalesced.")
    if not records:
        warnings.append("No bibliographic parent items in this export")
    weak = sum(record.identity_quality not in {"zotero-uri", "zotero-key"} for record in records)
    if weak:
        warnings.append(f"{weak} item(s) use export-local or content identities. IDs are deterministic but may change if the exporter renumbers, a citation key changes, or a keyless item is edited.")
    for name in ["DOI", "ISBN", "PMID"]:
        invalid = sum(record.metadata.get(name) not in (None, "") and not identifier_values(record.metadata)[name] for record in records)
        if invalid:
            warnings.append(f"{invalid} nonempty {name} field(s) contain no valid normalized identifier; they are retained for review but not used as identifier evidence")
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            sha.update(chunk)
    final_stat = path.stat()
    if (initial_stat.st_size, initial_stat.st_mtime_ns, initial_stat.st_ino) != (final_stat.st_size, final_stat.st_mtime_ns, final_stat.st_ino):
        raise InputError(f"{path.name}: export changed while it was being read; compare fixed snapshot copies instead of an actively updating export")
    return records, {"label": label, "file": path.name, "sha256": sha.hexdigest(), "format": fmt, "records": len(records), "warnings": warnings}

