"""Format validation and deterministic conversion, with no filesystem writes.

SPDX-License-Identifier: AGPL-3.0-only
"""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
from html import escape
import json
import math
import re

from . import __version__
from .html import document, sanitize, web_url

MAX_INPUT_BYTES = 64 * 1024 * 1024
DATE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})\Z")


class InputError(ValueError):
    """The input does not match a supported file format."""


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def payload_metadata(files):
    result = {}
    for name, content in sorted(files.items()):
        if name != "manifest.json":
            raw = content.encode("utf-8") if isinstance(content, str) else content
            result[name] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    return result


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def decode(raw: bytes):
    if len(raw) > MAX_INPUT_BYTES:
        raise InputError("input exceeds the 64 MiB limit; split the export first")
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_object,
                           parse_constant=lambda x: (_ for _ in ()).throw(InputError(f"invalid JSON number: {x}")))
        pending = [(value, 0)]
        while pending:
            node, depth = pending.pop()
            if depth > 128:
                raise InputError("JSON nesting exceeds the 128-level limit")
            if isinstance(node, dict):
                pending.extend((child, depth + 1) for child in node.values())
            elif isinstance(node, list):
                pending.extend((child, depth + 1) for child in node)
        # Reject unpaired surrogates early, including escaped ones in JSON.
        json_text(value).encode("utf-8")
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InputError(f"invalid UTF-8 JSON: {exc}") from exc


def require(condition, path, message):
    if not condition:
        raise InputError(f"{path}: {message}")


def strings(value, path):
    require(isinstance(value, list) and all(isinstance(x, str) for x in value), path, "expected array of strings")


def iso_date(value, path):
    require(isinstance(value, str) and DATE.fullmatch(value), path, "expected ISO 8601 timestamp with timezone (up to 6 fractional digits)")
    offset = re.search(r"[+-](\d{2}):?(\d{2})$", value)
    if offset:
        require(int(offset[1]) < 24 and int(offset[2]) < 60, path, "invalid timezone offset")
    try:
        datetime.fromisoformat(value)
    except ValueError as exc:
        raise InputError(f"{path}: invalid date: {exc}") from exc


def seconds_date(value, path):
    require(type(value) in {int, float} and (type(value) is int or math.isfinite(value)), path, "expected finite UNIX seconds, not milliseconds or a string")
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError) as exc:
        raise InputError(f"{path}: UNIX seconds outside supported date range") from exc


def wallabag(data):
    require(isinstance(data, list), "$", "Wallabag v2 export must be an array")
    for i, entry in enumerate(data):
        path = f"$[{i}]"
        require(isinstance(entry, dict), path, "expected entry object")
        for key in ("url", "title"):
            require(isinstance(entry.get(key), str), f"{path}.{key}", "expected string")
        if "content" in entry:
            require(entry["content"] is None or isinstance(entry["content"], str), f"{path}.content", "expected HTML string or null")
        strings(entry.get("tags", []), f"{path}.tags")
        if "id" in entry:
            require(type(entry["id"]) in {int, str}, f"{path}.id", "expected integer or string")
        for key in ("created_at", "updated_at", "published_at"):
            if key in entry and entry[key] is not None:
                iso_date(entry[key], f"{path}.{key}")
        for key in ("is_archived", "is_starred"):
            if key in entry:
                require(type(entry[key]) is bool or (type(entry[key]) is int and entry[key] in (0, 1)), f"{path}.{key}", "expected boolean or 0/1")
        annotations = entry.get("annotations", [])
        require(isinstance(annotations, list), f"{path}.annotations", "expected array")
        for j, note in enumerate(annotations):
            np = f"{path}.annotations[{j}]"
            require(isinstance(note, dict), np, "expected annotation object")
            for key in ("quote", "text"):
                if key in note:
                    require(isinstance(note[key], str), f"{np}.{key}", "expected string")
            for key in ("created_at", "updated_at"):
                if key in note and note[key] is not None:
                    iso_date(note[key], f"{np}.{key}")
    return data


def karakeep(data):
    require(isinstance(data, dict) and isinstance(data.get("bookmarks"), list), "$", "Karakeep native export must be an object with bookmarks array")
    lists = data.get("lists", [])
    require(isinstance(lists, list), "$.lists", "expected array")
    for i, item in enumerate(lists):
        path = f"$.lists[{i}]"
        require(isinstance(item, dict), path, "expected native list object")
        for key in ("id", "name", "icon"):
            require(isinstance(item.get(key), str), f"{path}.{key}", "expected string")
        for key in ("description", "query", "parentId"):
            require(key in item and (item[key] is None or isinstance(item[key], str)), f"{path}.{key}", "expected string or null")
        require(item.get("type") in ("manual", "smart"), f"{path}.type", "expected manual or smart")
    for i, entry in enumerate(data["bookmarks"]):
        path = f"$.bookmarks[{i}]"
        require(isinstance(entry, dict), path, "expected bookmark object")
        seconds_date(entry.get("createdAt"), f"{path}.createdAt")
        for key in ("title", "note"):
            require(key in entry and (entry[key] is None or isinstance(entry[key], str)), f"{path}.{key}", "expected string or null")
        strings(entry.get("tags"), f"{path}.tags")
        strings(entry.get("lists", []), f"{path}.lists")
        if "archived" in entry:
            require(type(entry["archived"]) is bool, f"{path}.archived", "expected boolean")
        require("content" in entry, f"{path}.content", "required nullable content field missing")
        content = entry["content"]
        if content is None:
            continue
        require(isinstance(content, dict), f"{path}.content", "expected object or null")
        kind = content.get("type")
        require(kind in ("link", "text", "asset"), f"{path}.content.type", "unknown content type; supported native types: link, text, null (asset is reported as unsupported)")
        if kind in ("link", "text"):
            key = "url" if kind == "link" else "text"
            require(isinstance(content.get(key), str), f"{path}.content.{key}", "expected string")
    return data["bookmarks"]


def stable_ids(entries):
    counts = Counter()
    for entry in entries:
        digest = hashlib.sha256(json_text(entry).encode("utf-8")).hexdigest()
        counts[digest] += 1
        yield digest + (f"-{counts[digest]}" if counts[digest] > 1 else "")


def issue(code, position, message, severity="loss", **extra):
    return {"code": code, "entry": position, "message": message, "severity": severity, **extra}


def duplicate_issues(entries, mode):
    positions = defaultdict(list)
    for i, entry in enumerate(entries):
        url = entry.get("url") if mode == "rescue" else (entry.get("content") or {}).get("url")
        if isinstance(url, str) and url:
            positions[url].append(i)
    return [issue("duplicate_url", None, "Exact URL repeated; records are retained. Wallabag may skip duplicate URLs on import.",
                  "warning" if mode == "rescue" else "loss", url=url, entries=indices)
            for url, indices in sorted(positions.items()) if len(indices) > 1]


def render_article(entry, body, body_present):
    title = entry["title"] or entry["url"] or "Untitled"
    metadata = {key: entry[key] for key in ("created_at", "updated_at", "published_at", "is_archived", "is_starred") if key in entry}
    source = escape(entry["url"])
    if web_url(entry["url"]):
        source = f'<a href="{escape(entry["url"], quote=True)}" rel="noreferrer noopener">{source}</a>'
    sections = [f"<h1>{escape(title)}</h1><p>Source: {source}</p>",
                "<p>Tags: " + ", ".join(escape(t) for t in entry.get("tags", [])) + "</p>",
                "<pre>" + escape(json_text(metadata)) + "</pre>"]
    if not body_present:
        sections.append("<p><strong>No readable cached body available. See manifest.json.</strong></p>")
    sections.append(f"<article>{body}</article>")
    if entry.get("annotations"):
        sections.append("<section><h2>Annotations</h2>")
        for note in entry["annotations"]:
            sections.append(f'<blockquote>{escape(note.get("quote", ""))}</blockquote><pre>{escape(note.get("text", ""))}</pre>')
            other = {k: v for k, v in note.items() if k not in ("quote", "text")}
            if other:
                sections.append("<pre>" + escape(json_text(other)) + "</pre>")
        sections.append("</section>")
    return document(title, "".join(sections))


def build(mode: str, raw: bytes, image_assets=None):
    """Return text/binary payloads and an auditable manifest; no writes."""
    require(mode in {"rescue", "k2w"}, "mode", "expected rescue or k2w")
    require(image_assets is None or mode == "rescue", "mode", "image assets require rescue")
    data = decode(raw)
    entries = wallabag(data) if mode == "rescue" else karakeep(data)
    files = dict(image_assets.files) if image_assets else {}
    records = []
    issues = duplicate_issues(entries, mode)
    if image_assets:
        issues.extend(image_assets.issues)
    converted = []
    index = []
    for i, (entry, identity) in enumerate(zip(entries, stable_ids(entries))):
        record = {"entry": i, "stable_id": identity}
        if mode == "rescue":
            filename = f"article-{identity}.html"
            base = entry["url"] if web_url(entry["url"]) else ""
            if not base:
                issues.append(issue("unsafe_source_url", i, "Source URL is not a safe absolute HTTP(S) URL; displayed as text."))
            references = []

            def resolve_image(reference):
                resolved = (image_assets.resolve(reference) if image_assets else
                            {"reference": reference, "status": "undeclared", "file": None, "sha256": None})
                references.append(resolved)
                return resolved["file"]

            try:
                body, changes, text = sanitize(entry.get("content") or "", base, resolve_image)
            except (ValueError, AssertionError) as exc:
                raise InputError(f"$[{i}].content: cannot parse HTML: {exc}") from exc
            body_present = bool(text or changes.get("archived_images"))
            if not body_present:
                issues.append(issue("missing_cached_body", i, "No readable cached body remains; placeholder article emitted."))
            for code, count in changes.items():
                severity = "info" if code in {"resolved_relative_links", "archived_images"} else "loss"
                message = ("Image source missing or not available in the explicit local asset manifest; no fetch attempted."
                           if code == "missing_image_asset" else "HTML normalization; original retained in provenance.json.")
                issues.append(issue(code, i, message, severity, count=count))
            rendered = {"title", "url", "tags", "content", "created_at", "updated_at", "published_at", "is_archived", "is_starred", "annotations"}
            extra = sorted(set(entry) - rendered)
            if extra:
                issues.append(issue("provenance_only_fields", i, "Fields retained in provenance, not rendered as article metadata.", "info", fields=extra))
            if any(n.get("ranges") for n in entry.get("annotations", [])):
                issues.append(issue("annotation_ranges_not_anchored", i, "Annotation ranges displayed verbatim; no highlight positioning attempted.", "info"))
            files[filename] = render_article(entry, body, body_present)
            record.update(file=filename, title=entry["title"], url=entry["url"], cached_body=body_present)
            if references:
                record["image_references"] = references
            index.append(f'<li><a href="{filename}">{escape(entry["title"] or entry["url"] or "Untitled")}</a>' + (" (no cached body)" if not body_present else "") + "</li>")
        else:
            content = entry["content"]
            kind = content["type"] if content else "null"
            record["kind"] = kind
            if kind != "link":
                code = {"text": "text_without_url", "null": "null_content", "asset": "unsupported_asset"}[kind]
                issues.append(issue(code, i, "No native link URL; not emitted to Wallabag. Full record retained in provenance.json."))
                record["status"] = "skipped"
            elif not web_url(content["url"]):
                issues.append(issue("unsafe_link_url", i, "Only absolute HTTP(S) URLs without credentials/controls are emitted; record retained in provenance.json."))
                record["status"] = "skipped"
            else:
                created = seconds_date(entry["createdAt"], f"$.bookmarks[{i}].createdAt")
                if datetime.fromisoformat(created).timestamp() != entry["createdAt"]:
                    issues.append(issue("timestamp_precision_loss", i, "UNIX seconds rounded to datetime microsecond precision; original number retained in provenance."))
                note = entry["note"]
                html = "<section><h2>Karakeep note</h2><pre>" + escape(note) + "</pre></section>" if note else ""
                converted.append({"url": content["url"], "title": entry["title"] if entry["title"] is not None else content["url"],
                                  "content": html, "tags": entry["tags"], "created_at": created,
                                  "is_archived": entry.get("archived", False), "is_starred": False, "mimetype": "text/html"})
                record.update(status="converted", output_index=len(converted) - 1, url=content["url"])
                issues.append(issue("native_link_has_no_cached_body", i, "Native link export contains no cached HTML; content contains only escaped note, if any.", "info"))
                if entry["title"] is None:
                    issues.append(issue("null_title_fallback", i, "URL used as title; original null retained in provenance.", "info"))
            if entry.get("lists"):
                issues.append(issue("list_membership_not_mapped", i, "Wallabag tags kept separate from Karakeep lists; membership retained only in provenance."))
            known = {"createdAt", "title", "tags", "lists", "content", "note", "archived"}
            extra = sorted(set(entry) - known)
            content_extra = sorted(set(content or {}) - ({"type", "url"} if kind == "link" else {"type", "text"}))
            if extra or content_extra:
                issues.append(issue("provenance_only_fields", i, "Unmapped fields retained in provenance; no API htmlContent interpretation.", "info", fields=extra, content_fields=content_extra))
        records.append(record)
    if mode == "rescue":
        files["index.html"] = document("Offline archive", "<h1>Offline archive</h1><p>See manifest.json for losses and provenance.json for original records.</p><ul>" + "".join(index) + "</ul>")
    else:
        files["wallabag.json"] = json_text(converted)
        if data.get("lists"):
            issues.append(issue("list_definitions_not_mapped", None, "List structure and queries retained only in provenance."))
        extra = sorted(set(data) - {"bookmarks", "lists"})
        if extra:
            issues.append(issue("provenance_only_fields", None, "Unknown top-level fields retained in provenance.", "info", fields=extra))
        if not converted:
            issues.append(issue("no_convertible_links", None, "Output array is empty; Wallabag importer may reject an empty import."))
    provenance = {"schema_version": 1, "source_format": "wallabag-v2" if mode == "rescue" else "karakeep-native",
                  "input_sha256": hashlib.sha256(raw).hexdigest(), "source": data}
    if image_assets:
        provenance["image_assets"] = image_assets.provenance()
    files["provenance.json"] = json_text(provenance)
    manifest = {"schema_version": 1, "tool": "bagkeep", "version": __version__, "mode": mode,
                "input_sha256": provenance["input_sha256"], "input_count": len(entries),
                "output_count": len(entries) if mode == "rescue" else len(converted),
                "skipped_count": 0 if mode == "rescue" else len(entries) - len(converted),
                "issues": issues, "records": records,
                "files": payload_metadata(files)}
    if image_assets:
        manifest["image_assets"] = {"input_sha256": image_assets.input_sha256, "mapping": image_assets.mapping}
    files["manifest.json"] = json_text(manifest)
    return files, manifest
