"""Original, standard-library-only Wallabag export parser and identity model.

SPDX-License-Identifier: AGPL-3.0-only
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, DecimalException
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlsplit
import uuid


class BagnotesError(Exception):
    """A structured, user-facing failure; no output should be published."""

    def __init__(self, code: str, message: str, path: str = "$", **details: Any):
        super().__init__(message)
        self.diagnostic = dict(status="error", code=code, message=message, path=path,
                               **details)


def json_encode(value: Any, *, pretty: bool = False, sort_keys: bool = False) -> str:
    """Encode parsed JSON, retaining arbitrary-precision opaque numeric values.

    Decimal is used only for JSON number tokens, never encoded as a string.
    This small recursive writer avoids private JSONEncoder internals.
    """
    def write(item: Any, level: int) -> str:
        if isinstance(item, Decimal):
            if not item.is_finite():
                raise ValueError("Non-finite JSON number")
            return str(item)
        if isinstance(item, (list, dict)):
            is_object = isinstance(item, dict)
            opening, closing = ("{", "}") if is_object else ("[", "]")
            if not item:
                return opening + closing
            if is_object:
                keys = sorted(item) if sort_keys else item
                entries = [json.dumps(k, ensure_ascii=False) + (": " if pretty else ":")
                           + write(item[k], level + 1) for k in keys]
            else:
                entries = [write(part, level + 1) for part in item]
            if pretty:
                prefix = "  " * (level + 1)
                return opening + "\n" + prefix + (",\n" + prefix).join(entries) + "\n" + "  " * level + closing
            return opening + ",".join(entries) + closing
        return json.dumps(item, ensure_ascii=False, allow_nan=False, separators=(",", ":"))

    return write(value, 0)


def canonical(value: Any) -> str:
    return json_encode(value, sort_keys=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise BagnotesError("duplicate_json_key", f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise BagnotesError("invalid_json", f"Non-finite JSON number: {value}")


def decode(data: bytes) -> Any:
    """Read UTF-8 (optional BOM); reject ambiguous or non-standard JSON."""
    try:
        value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=_pairs,
                           parse_constant=_constant, parse_float=Decimal)
        # Escaped lone surrogates are accepted by json.loads. Validate Unicode
        # before rendering. Decimal preserves numeric precision in opaque fields.
        canonical(value).encode("utf-8")
        return value
    except (ValueError, UnicodeError, RecursionError, DecimalException) as exc:
        raise BagnotesError("invalid_json", f"Invalid UTF-8 JSON: {exc}") from exc


def identifier(value: Any, path: str) -> str:
    if type(value) is int and value > 0:
        result = str(value)
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
        result = value.lstrip("0")
    else:
        result = ""
    if not result or len(result) > 128:
        raise BagnotesError("invalid_id", "Expected a positive decimal ID (up to 128 digits)", path)
    return result


def clickable(url: str | None) -> bool:
    if not url or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        return False
    try:
        parts = urlsplit(url)
        return parts.scheme.lower() in {"http", "https"} and bool(parts.hostname)
    except ValueError:
        return False


def _known_range(value: Any) -> bool:
    return (isinstance(value, dict)
            and all(isinstance(value.get(k), str) for k in ("start", "end"))
            and all(type(value.get(k)) is int and value[k] >= 0
                    for k in ("startOffset", "endOffset")))


@dataclass
class Annotation:
    identity: str
    annotation_id: str | None
    anchor: str
    block_id: str
    raw: dict[str, Any]
    occurrences: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Article:
    source_id: str
    raw: dict[str, Any]
    source_order: int
    occurrences: list[int | dict[str, int]] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)
    namespace: str | None = None
    input_order: int = 0

    @property
    def file_stem(self) -> str:
        key = self.source_id if self.namespace is None else digest([self.namespace, self.source_id])
        return "article-" + key

    @property
    def title(self) -> str:
        return self.raw.get("title") or f"Article {self.source_id}"


@dataclass
class Export:
    articles: list[Article]
    warnings: list[dict[str, Any]]
    counts: dict[str, int]
    input_sha256: str | None
    inputs: list[dict[str, Any]] | None = None


def _different(a: dict, b: dict) -> list[str]:
    return sorted(k for k in a.keys() | b.keys()
                  if k not in a or k not in b or canonical(a[k]) != canonical(b[k]))


def validate_namespace(value: str, path: str = "$") -> None:
    """Account labels are explicit, case-sensitive and never inferred from a file."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value):
        raise BagnotesError("invalid_namespace",
                            "Namespace must be 1-64 ASCII letters/digits/._-, starting with a letter or digit", path)


def parse(data: bytes) -> Export:
    """Legacy single-account scope; preserves v1 identities and output schema."""
    return _parse_inputs([(None, data)], scoped=False)


def parse_sources(inputs: list[tuple[str, bytes]]) -> Export:
    """Merge ordered (namespace, export bytes) pairs, retaining all input hashes.

    Reuse a namespace only for exports of the same Wallabag account. Metadata or
    identified annotation differences in that scope fail instead of overwriting.
    """
    return _scoped_inputs(inputs)


def inspect_sources(inputs: list[tuple[str, bytes]], *, include_values: bool = False) -> dict[str, Any]:
    """Validate inputs and report every mismatching occurrence pair, without export.

    Comparisons use the same normalized IDs and field rules as parse_sources.
    Invalid input raises BagnotesError; no partial conflict report is returned.
    Opt-in field_values retain each differing value and its explicit presence.
    """
    inspection = _ConflictInspection(inputs, include_values=include_values)
    export = _scoped_inputs(inputs, inspection)
    conflicts = inspection.conflicts
    counts = {code + 's': sum(c['code'] == code for c in conflicts)
              for code in ('article_conflict', 'annotation_conflict')}
    counts['conflicts'] = len(conflicts)
    return dict(schema_version=1, tool='bagnotes-conflicts',
                status='conflicts' if conflicts else 'compatible',
                inputs=inspection.inputs, counts=counts,
                conflicts=conflicts, warnings=export.warnings)


class _ConflictInspection:
    """All earlier occurrences, not just the first version of an identity."""

    def __init__(self, inputs: list[tuple[str, bytes]], *, include_values: bool = False):
        self.include_values = include_values
        self.inputs = [dict(input_order=i, namespace=namespace, sha256=hashlib.sha256(data).hexdigest())
                       for i, (namespace, data) in enumerate(inputs)]
        self.seen: dict[tuple, list[tuple[dict, dict]]] = {}
        self.conflicts: list[dict[str, Any]] = []

    def observe(self, namespace: str, source_id: str, raw: dict,
                input_order: int, source_order: int, annotation_order: int | None = None,
                identity: str | None = None) -> None:
        code = 'article_conflict' if annotation_order is None else 'annotation_conflict'
        location = dict(input_order=input_order, source_order=source_order,
                        sha256=self.inputs[input_order]['sha256'],
                        path=_article_path(input_order, source_order, True))
        if annotation_order is not None:
            location.update(annotation_order=annotation_order,
                            path=location['path'] + f'.annotations[{annotation_order}]')
        earlier = self.seen.setdefault((namespace, source_id, identity), [])
        for previous, first_location in earlier:
            differences = _different(previous, raw)
            if differences:
                conflict = dict(code=code, namespace=namespace, source_id=source_id,
                                differing_fields=differences, first=first_location, second=location)
                if identity is not None:
                    conflict['identity'] = identity
                if self.include_values:
                    # IDs are already normalized for comparison and cannot differ
                    # within this scope. All other fields retain decoded originals.
                    conflict['field_values'] = {
                        field: {side: dict(present=True, value=record[field]) if field in record
                                else dict(present=False)
                                for side, record in (('first', previous), ('second', raw))}
                        for field in differences}
                self.conflicts.append(conflict)
        earlier.append((raw, location))


def _scoped_inputs(inputs: list[tuple[str, bytes]],
                   inspection: _ConflictInspection | None = None) -> Export:
    if not inputs:
        raise BagnotesError("missing_sources", "At least one namespaced export is required")
    for index, (namespace, _) in enumerate(inputs):
        validate_namespace(namespace, f"$inputs[{index}].namespace")
    try:
        return _parse_inputs(inputs, scoped=True, inspection=inspection)
    except BagnotesError as exc:
        match = re.match(r"\$inputs\[([0-9]+)\]", exc.diagnostic["path"])
        if match:
            index = int(match[1])
            exc.diagnostic.update(input_order=index, namespace=inputs[index][0])
        raise


def _article_path(input_order: int, source_order: int, scoped: bool) -> str:
    prefix = f"$inputs[{input_order}].articles" if scoped else "$"
    return f"{prefix}[{source_order}]"


def _records(inputs: list[tuple[str | None, bytes]], scoped: bool):
    for index, (namespace, data) in enumerate(inputs):
        path = f"$inputs[{index}].articles" if scoped else "$"
        try:
            root = decode(data)
        except BagnotesError as exc:
            exc.diagnostic["path"] = path
            raise
        if not isinstance(root, list):
            raise BagnotesError("unsupported_format",
                                "Expected a Wallabag v2 export array; no API envelope or JSONL", path)
        for order, raw in enumerate(root):
            yield index, namespace, order, raw


def _parse_inputs(inputs: list[tuple[str | None, bytes]], *, scoped: bool,
                  inspection: _ConflictInspection | None = None) -> Export:
    # Inspection's intermediate first-seen model is discarded, never rendered.
    # Continue through conflicting articles to validate/inspect their annotations.
    articles: dict[tuple[str | None, str], Article] = {}
    seen: dict[tuple[str | None, str], dict[str, Annotation]] = {}
    warnings = []
    counts = dict(source_records=0, articles=0, annotation_records=0,
                  annotations=0, duplicate_articles=0, duplicate_annotations=0,
                  content_identities=0)

    def warn(code: str, path: str, **details: Any) -> None:
        if scoped:
            details.update(namespace=namespace, input_order=input_order)
        warnings.append(dict(code=code, path=path, **details))

    for input_order, namespace, i, raw in _records(inputs, scoped):
        counts["source_records"] += 1
        path = _article_path(input_order, i, scoped)
        if not isinstance(raw, dict):
            raise BagnotesError("invalid_article", "Article must be an object", path)
        sid = identifier(raw.get("id"), path + ".id")
        key = (namespace, sid)
        for field_name in ("title", "url"):
            if field_name in raw and raw[field_name] is not None and not isinstance(raw[field_name], str):
                raise BagnotesError("invalid_field", f"{field_name} must be a string or null", path + "." + field_name)
        if "annotations" not in raw:
            warn("absent_annotations", path, reason="Upstream fixture permits omission; no annotations available to export")
        annotations = raw.get("annotations", [])
        if not isinstance(annotations, list):
            raise BagnotesError("invalid_annotations", "annotations must be an array", path + ".annotations")
        # Compare all article fields (including content), normalizing only the ID.
        metadata = {k: v for k, v in raw.items() if k != "annotations"}
        metadata["id"] = sid
        if inspection is not None:
            inspection.observe(namespace, sid, metadata, input_order, i)
        position = dict(input_order=input_order, source_order=i) if scoped else i
        if key in articles:
            article = articles[key]
            first = {**article.raw, "id": sid}
            differences = _different(first, metadata)
            if differences and inspection is None:
                raise BagnotesError("article_conflict", f"Conflicting source ID {sid}", path,
                                    first_path=_article_path(article.input_order, article.source_order, scoped),
                                    differing_fields=differences)
            counts["duplicate_articles"] += 1
            article.occurrences.append(position)
        else:
            article = Article(sid, {k: v for k, v in raw.items() if k != "annotations"}, i, [position],
                              namespace=namespace, input_order=input_order)
            articles[key] = article
            seen[key] = {}
            if not raw.get("title"):
                warn("missing_title", path, fallback=article.title)
            if not clickable(raw.get("url")):
                warn("unlinked_url", path + ".url", reason="No safe HTTP(S) source link; raw value retained")
            if "content" in raw:
                warn("omitted_article_content", path + ".content", reason="Annotation exporter; HTML not archived")
        for j, annotation in enumerate(annotations):
            apath = path + f".annotations[{j}]"
            counts["annotation_records"] += 1
            if not isinstance(annotation, dict):
                raise BagnotesError("invalid_annotation", "Annotation must be an object", apath)
            for field_name in ("quote", "text"):
                if field_name not in annotation:
                    raise BagnotesError("missing_" + field_name, f"Explicit {field_name} is required; never inferred from HTML or ranges", apath)
                if not isinstance(annotation[field_name], str):
                    raise BagnotesError("invalid_field", f"{field_name} must be a string", apath + "." + field_name)
            if not isinstance(annotation.get("ranges"), list):
                raise BagnotesError("invalid_ranges", "ranges must be an array; its elements are kept opaque", apath + ".ranges")
            aid = identifier(annotation["id"], apath + ".id") if "id" in annotation else None
            normalized = dict(annotation)
            if aid is not None:
                normalized["id"] = aid
            identity = "id:" + aid if aid else "content-sha256:" + digest(annotation)
            if inspection is not None:
                inspection.observe(namespace, sid, normalized, input_order, i, j, identity)
            occurrence = dict(source_order=i, annotation_order=j, raw=annotation)
            if scoped:
                occurrence["input_order"] = input_order
            if identity in seen[key]:
                previous = seen[key][identity]
                earlier = dict(previous.raw)
                if aid is not None:
                    earlier["id"] = aid
                differences = _different(earlier, normalized)
                if differences and inspection is None:
                    first = previous.occurrences[0]
                    raise BagnotesError("annotation_conflict", f"Conflicting annotation {identity} in source {sid}", apath,
                                        first_path=_article_path(first.get("input_order", 0), first["source_order"], scoped)
                                        + f".annotations[{first['annotation_order']}]",
                                        differing_fields=differences)
                previous.occurrences.append(occurrence)
                counts["duplicate_annotations"] += 1
                continue
            anchor_key = [namespace, sid, identity] if scoped else [sid, identity]
            anchor = "bagnotes-" + digest(anchor_key)
            block_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "urn:bagnotes:" + anchor))
            item = Annotation(identity, aid, anchor, block_id, annotation, [occurrence])
            seen[key][identity] = item
            article.annotations.append(item)
            counts["annotations"] += 1
            if aid is None:
                counts["content_identities"] += 1
                warn("missing_annotation_id", apath, identity=identity,
                     reason="Content identity changes when annotation fields change")
            if annotation["quote"] == "":
                warn("empty_quote", apath + ".quote", reason="Empty quote retained; no text inferred")
            for r, value in enumerate(annotation["ranges"]):
                if not _known_range(value):
                    warn("opaque_range", apath + f".ranges[{r}]", reason="Unrecognized range retained unchanged")
    counts["articles"] = len(articles)
    descriptors = None
    if scoped:
        counts.update(input_files=len(inputs), namespaces=len({namespace for namespace, _ in inputs}))
        descriptors = [dict(input_order=i, namespace=namespace, sha256=hashlib.sha256(data).hexdigest())
                       for i, (namespace, data) in enumerate(inputs)]
    legacy_hash = None if scoped else hashlib.sha256(inputs[0][1]).hexdigest()
    return Export(list(articles.values()), warnings, counts, legacy_hash, descriptors)
