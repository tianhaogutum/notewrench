"""Original adapters for documented JSON structures; no upstream code reused."""
from dataclasses import dataclass
import json
from pathlib import Path

from . import BundleError

MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_ENTRIES = 10000


@dataclass(frozen=True)
class Article:
    key: str
    source_id: str
    input_name: str
    format: str
    title: str
    url: str
    tags: tuple[str, ...]
    body: str | None
    is_html: bool
    missing_reason: str = "missing_body"

    def provenance(self):
        return {"key": self.key, "id": self.source_id, "input": self.input_name,
                "format": self.format, "title": self.title, "url": self.url,
                "tags": list(self.tags)}


def _string(value, field, *, nullable=False):
    if value is None and nullable:
        return ""
    if not isinstance(value, str):
        raise BundleError(f"{field} must be a string")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BundleError(f"duplicate JSON property: {key}")
        result[key] = value
    return result


def load_input(path: Path, number: int, format="auto", *, warnings=None) -> list[Article]:
    with path.open("rb") as stream:
        raw = stream.read(MAX_INPUT_BYTES + 1)
    return load_input_bytes(raw, path, number, format, warnings=warnings)


def load_input_bytes(raw, path, number, format="auto", *, warnings=None):
    """Parse the exact bytes already read and verified by an explicit adapter."""
    if len(raw) > MAX_INPUT_BYTES:
        raise BundleError(f"input exceeds {MAX_INPUT_BYTES} bytes: {path}")
    try:
        data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                          parse_constant=lambda x: (_ for _ in ()).throw(BundleError(f"invalid JSON constant: {x}")))
    except (ValueError, RecursionError) as exc:
        raise BundleError(f"invalid UTF-8 JSON in {path}: {exc}") from exc
    if format == "auto":
        if isinstance(data, list) and data and all(isinstance(x, dict) and "url" in x and "content" in x for x in data):
            format = "wallabag"
        elif isinstance(data, dict) and isinstance(data.get("bookmarks"), list):
            rows = data["bookmarks"]
            # Native exports have no IDs; API bookmark objects have IDs.
            if "nextCursor" in data or (rows and any(isinstance(x, dict) and "id" in x for x in rows)):
                format = "karakeep-api"
            else:
                format = "karakeep-export"
        elif isinstance(data, dict) and "id" in data and isinstance(data.get("content"), dict):
            format = "karakeep-api"
        else:
            raise BundleError("unknown/empty input format; use an explicit --format for empty arrays or API arrays")
    if format == "wallabag":
        rows = data
    elif format == "karakeep-export":
        if not isinstance(data, dict) or "bookmarks" not in data:
            raise BundleError("Karakeep export must be an object with bookmarks array")
        rows = data["bookmarks"]
    elif format == "karakeep-api":
        rows = data.get("bookmarks", [data]) if isinstance(data, dict) else data
        if isinstance(data, dict) and data.get("nextCursor") is not None and warnings is not None:
            warnings.append({"code": "api_more_pages", "input": str(path),
                             "reason": "Saved API page has nextCursor; only this local page is processed."})
    else:
        raise BundleError(f"unsupported format: {format}")
    if not isinstance(rows, list) or len(rows) > MAX_ENTRIES:
        raise BundleError(f"expected array of at most {MAX_ENTRIES} entries")
    result, seen = [], set()
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise BundleError(f"entry {index} must be an object")
        title = _string(row.get("title"), "title", nullable=True).strip()
        tags = row.get("tags", [])
        if not isinstance(tags, list):
            raise BundleError(f"entry {index}: tags must be an array")
        if format == "karakeep-api":
            if not all(isinstance(t, dict) and isinstance(t.get("name"), str) for t in tags):
                raise BundleError(f"entry {index}: API tags must be objects with name")
            tags = [t["name"] for t in tags]
        elif not all(isinstance(t, str) for t in tags):
            raise BundleError(f"entry {index}: tags must be strings")
        reason = "missing_body"
        if format == "wallabag":
            source_id = row.get("id", f"entry-{index}")
            url = _string(row.get("url"), "url")
            body = row.get("content")
            if body is not None and not isinstance(body, str):
                raise BundleError(f"entry {index}: Wallabag content must be HTML string or null")
            is_html = True
        else:
            if format == "karakeep-export" and "id" in row:
                raise BundleError("native Karakeep export has no id; use --format karakeep-api for saved API objects")
            source_id = f"entry-{index}" if format == "karakeep-export" else row.get("id")
            if format == "karakeep-api" and not isinstance(source_id, str):
                raise BundleError(f"entry {index}: API id must be a string")
            content = row.get("content")
            if content is None:
                url, body, is_html = "", None, False
            elif not isinstance(content, dict):
                raise BundleError(f"entry {index}: content must be object or null")
            elif content.get("type") == "text":
                url = _string(content.get("sourceUrl"), "sourceUrl", nullable=True) if format == "karakeep-api" else ""
                body = _string(content.get("text"), "content.text")
                is_html = False
            elif content.get("type") == "link":
                url = _string(content.get("url"), "content.url")
                if format == "karakeep-export" and "htmlContent" in content:
                    raise BundleError("htmlContent is not part of native Karakeep export; use --format karakeep-api for a saved API snapshot")
                body = content.get("htmlContent") if format == "karakeep-api" else None
                if body is not None and not isinstance(body, str):
                    raise BundleError(f"entry {index}: htmlContent must be string or null")
                title = title or _string(content.get("title"), "content.title", nullable=True)
                reason = "native_link_has_no_body" if format == "karakeep-export" else "missing_api_htmlContent"
                is_html = True
            elif content.get("type") in ("asset", "unknown") and format == "karakeep-api":
                url, body, is_html, reason = "", None, False, "unsupported_content_type"
            else:
                raise BundleError(f"entry {index}: unknown content.type {content.get('type')!r}")
        if isinstance(source_id, bool) or not isinstance(source_id, (str, int)) or not str(source_id):
            raise BundleError(f"entry {index}: id must be nonempty string or integer")
        source_id = str(source_id)
        if source_id in seen:
            raise BundleError(f"duplicate entry id {source_id!r} in {path}")
        seen.add(source_id)
        result.append(Article(f"{number}:{source_id}", source_id, str(path), format,
                              title or url or f"Untitled {source_id}", url,
                              tuple(tags), body, is_html, reason))
    return result
