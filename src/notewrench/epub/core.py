"""Build planning, provenance reporting and publication of new local output files."""
from collections.abc import Sequence
from datetime import datetime
import hashlib
import io
import os
from pathlib import Path
import re
import tempfile

from . import BundleError
from .epub import DEFAULT_MODIFIED, json_bytes, make_epub, validate_epub
from .htmlclean import check_text, clean_body, safe_url
from .inputs import load_input
from .images import ImageManifest
from .markdown import render_markdown
from .ordering import MAX_ORDER_BYTES, order_chapters, validate_chapter_order


def prepare(inputs, *, format="auto", title="Reading bundle", author="notewrench",
            language="en", modified=DEFAULT_MODIFIED, tags=(), ids=(), exclude_ids=(), skip_missing=False,
            text_mode="literal", image_manifest=None, bagkeep_bundle=None, chapter_order=None):
    """Build in memory; chapter_order is an optional complete sequence, never a path."""
    if text_mode not in {"literal", "markdown"}:
        raise BundleError("text_mode must be literal or markdown")
    for value in (title, author, language, modified):
        check_text(value)
    if not title.strip() or not author.strip():
        raise BundleError("title and author must be nonempty")
    if not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", language):
        raise BundleError("language must be a simple BCP 47 tag, e.g. en or zh-Hans")
    try:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", modified):
            raise ValueError()
        datetime.strptime(modified, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise BundleError("modified must be a valid UTC timestamp YYYY-MM-DDTHH:MM:SSZ") from exc
    chapters, ready, images, report = _select(
        inputs, format=format, tags=tags, ids=ids, exclude_ids=exclude_ids, skip_missing=skip_missing,
        text_mode=text_mode, image_manifest=image_manifest, bagkeep_bundle=bagkeep_bundle)
    report["metadata"] = {"title": title, "author": author, "language": language, "modified": modified}
    # Validate explicit order only after all existing input/resource/body work.
    # Existing missing-body/no-chapter reports take precedence over order errors.
    if chapter_order is not None:
        try:
            order = validate_chapter_order(chapter_order)
        except BundleError:
            if not report["errors"]:
                raise
        else:
            report["chapter_order"] = order
            if not report["errors"]:
                chapters, ready = order_chapters(chapters, ready, order)
    if "bagkeep_archive" in report:
        output_indices = {entry["key"]: index for index, entry in enumerate(ready)}
        for entry in report["bagkeep_archive"]["articles"]:
            entry["output_index"] = output_indices.get(entry["key"]) if not report["errors"] else None
    if report["errors"]:
        report["not_built"] = ready
        return None, report
    payload, identifier = make_epub(chapters, title=title, author=author,
                                    language=language, modified=modified,
                                    images=images.selected_images() if images else ())
    report["validation"] = validate_epub(io.BytesIO(payload))
    report.update(status="completed", included=ready, identifier=identifier,
                  epub_sha256=hashlib.sha256(payload).hexdigest())
    return payload, report


def _select(inputs, *, format="auto", tags=(), ids=(), exclude_ids=(), skip_missing=False,
            text_mode="literal", image_manifest=None, bagkeep_bundle=None):
    """Load/validate resources, select and clean bodies without EPUB construction."""
    if text_mode not in {"literal", "markdown"}:
        raise BundleError("text_mode must be literal or markdown")
    articles, warnings, archive = [], [], None
    if bagkeep_bundle is not None:
        if inputs or image_manifest is not None or format not in {"auto", "wallabag"}:
            raise BundleError("--archive-bundle requires no positional inputs/image manifest and auto or wallabag format")
        from .archive_bundle import load_bundle
        articles, images, archive = load_bundle(bagkeep_bundle)
    else:
        for number, path in enumerate(inputs, 1):
            articles.extend(load_input(Path(path), number, format, warnings=warnings))
        images = ImageManifest(image_manifest, {a.key for a in articles}) if image_manifest is not None else None
    known = {a.key for a in articles}
    unknown = set(ids) - known
    if unknown:
        raise BundleError(f"unknown selection keys: {', '.join(sorted(unknown))}")
    # Validate and snapshot only after all input/resource loading. Repeated
    # exclusions are idempotent; only exact known qualified identities match.
    if isinstance(exclude_ids, (str, bytes, bytearray)) or not isinstance(exclude_ids, Sequence):
        raise BundleError("exclude_ids must be a sequence of qualified keys")
    exclusions = set()
    for key in exclude_ids:
        if not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]*:.+", key, re.DOTALL):
            raise BundleError("exclude_ids entries must be nonempty exact qualified keys INPUT_NUMBER:ID")
        check_text(key)
        exclusions.add(key)
    unknown = exclusions - known
    if unknown:
        raise BundleError(f"unknown exclusion keys: {', '.join(sorted(unknown))}")
    report = {"schema_version": 1, "status": "failed",
        "inputs": [str(p) for p in inputs], "included": [], "skipped": [],
        "excluded": [], "changes": [], "warnings": warnings, "errors": [],
        "text_mode": text_mode}
    if archive is not None:
        report["bagkeep_archive"] = archive
        report["inputs"] = [str(Path(bagkeep_bundle) / "wallabag.json")]
    chapters, ready = [], []
    for article in articles:
        entry = article.provenance()
        # An explicit exclusion wins over either inclusion filter, including its
        # provenance reason. Never clean or count its body as selected/missing.
        if article.key in exclusions:
            report["excluded"].append({**entry, "reason": "explicit_id_exclusion"})
            continue
        # Repeat tags are OR; the tag filter and ID filter are ANDed.
        if (tags and not set(tags).intersection(article.tags)) or (ids and article.key not in ids):
            report["excluded"].append({**entry, "reason": "selection_filter"})
            continue
        for value in (article.title, article.url, *article.tags, article.key):
            check_text(value)
        if article.body is None or not article.body.strip():
            report["skipped"].append({**entry, "reason": article.missing_reason})
            continue
        if not article.is_html and text_mode == "markdown":
            body, events = render_markdown(article.body, article.url)
            entry.update(text_mode="markdown", original_text=article.body,
                         original_text_sha256=hashlib.sha256(article.body.encode("utf-8")).hexdigest())
        else:
            body, events = clean_body(article.body, article.is_html, article.url,
                                      image_resolver=(lambda src: images.resolve(article.key, src)) if images else None)
        report["changes"].extend({"key": article.key, **e} for e in events)
        if article.url and not safe_url(article.url, mailto=False):
            report["changes"].append({"key": article.key, "code": "inactive_source_url", "value": article.url})
        if not "".join(body.itertext()).strip() and next(body.iter("img"), None) is None:
            report["skipped"].append({**entry, "reason": "empty_after_cleaning"})
            continue
        chapters.append((article, body))
        ready.append(entry)
    if images is not None:
        if image_manifest is not None:
            report["image_manifest"] = str(image_manifest)
        report["resources"], report["unused_images"] = images.report()
    if report["skipped"] and not skip_missing:
        report["errors"].append("Selected entries have no usable body; explicitly use --skip-missing to omit them.")
    if not chapters:
        report["errors"].append("No usable chapters selected.")
    return chapters, ready, images, report


def plan(inputs, *, format="auto", tags=(), ids=(), exclude_ids=(), skip_missing=False,
         text_mode="literal", image_manifest=None, bagkeep_bundle=None, chapter_order=None):
    """Return usable provenance in source or proposed complete order; no EPUB.

    Policy failures retain usable candidates in included. Archive output_index is
    always null; plan_index describes candidates without claiming publication.
    On policy failure, preserve the source-order plan without inspecting a supplied
    sequence. File parsing is the CLI's responsibility; the library accepts keys.
    """
    chapters, ready, _, report = _select(
        inputs, format=format, tags=tags, ids=ids, exclude_ids=exclude_ids, skip_missing=skip_missing,
        text_mode=text_mode, image_manifest=image_manifest, bagkeep_bundle=bagkeep_bundle)
    order = validate_chapter_order([entry["key"] for entry in ready])
    if len(json_bytes(order)) > MAX_ORDER_BYTES:
        raise BundleError(f"planned chapter_order exceeds {MAX_ORDER_BYTES} bytes")
    if chapter_order is not None and not report["errors"]:
        order = validate_chapter_order(chapter_order)
        _, ready = order_chapters(chapters, ready, order)
    report.update(status="failed" if report["errors"] else "planned",
                  included=ready, chapter_order=order)
    if "bagkeep_archive" in report:
        indices = {key: index for index, key in enumerate(order)}
        for entry in report["bagkeep_archive"]["articles"]:
            entry["plan_index"] = indices.get(entry["key"])
    return report


def check_destinations(outputs, inputs):
    resolved = [Path(p).resolve() for p in outputs]
    if len(set(resolved)) != len(resolved):
        raise BundleError("EPUB and report output paths conflict")
    input_paths = {Path(p).resolve() for p in inputs}
    for path, dest in zip(outputs, resolved):
        if dest in input_paths:
            raise BundleError(f"output conflicts with input: {path}")
        if os.path.lexists(path):
            raise BundleError(f"output already exists (no overwrite): {path}")
        if not Path(path).parent.is_dir():
            raise BundleError(f"output parent directory does not exist: {path}")


def publish_new(files):
    """Stage complete bytes, then link exclusively; roll back our files on failure.

    Two files cannot be committed as a single filesystem transaction. A process or
    machine crash between links can leave one complete file; existing files are never replaced.
    """
    staged, published = [], []
    try:
        for path, data in files:
            path = Path(path)
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".readbundle-", delete=False) as stream:
                temp = Path(stream.name)
                staged.append((temp, path))
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        for temp, path in staged:
            os.link(temp, path)
            published.append(path)
    except BaseException:
        for path in published:
            path.unlink(missing_ok=True)
        raise
    finally:
        for temp, _ in staged:
            temp.unlink(missing_ok=True)
