"""Explicit book ordering; article identities always belong to their sources."""
from collections.abc import Sequence
import json
from pathlib import Path
import re

from . import BundleError
from .htmlclean import check_text
from .inputs import _unique_object

MAX_ORDER_BYTES = 1024 * 1024
MAX_ORDER_ENTRIES = 10000


def load_chapter_order(path):
    """Read one strict UTF-8 JSON array once, with a 1 MiB byte bound.

    Key/permutation validation belongs to prepare, after its existing missing-body
    policy. File I/O, encoding, JSON and top-level shape errors are fatal here.
    """
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_ORDER_BYTES + 1)
    if len(raw) > MAX_ORDER_BYTES:
        raise BundleError(f"chapter order file exceeds {MAX_ORDER_BYTES} bytes: {path}")
    try:
        order = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=lambda x: (_ for _ in ()).throw(ValueError(f"invalid JSON constant: {x}")))
    except (ValueError, RecursionError) as exc:
        raise BundleError(f"invalid strict UTF-8 chapter order JSON in {path}: {exc}") from exc
    if not isinstance(order, list):
        raise BundleError("chapter order file must contain a JSON array")
    return order


def validate_chapter_order(order):
    """Snapshot a bounded sequence of exact qualified keys; never read a path."""
    if isinstance(order, (str, bytes, bytearray)) or not isinstance(order, Sequence):
        raise BundleError("chapter_order must be a sequence of qualified keys")
    if len(order) > MAX_ORDER_ENTRIES:
        raise BundleError(f"chapter_order exceeds {MAX_ORDER_ENTRIES} entries")
    result, seen = [], set()
    for index, key in enumerate(order):
        if index >= MAX_ORDER_ENTRIES:
            raise BundleError(f"chapter_order exceeds {MAX_ORDER_ENTRIES} entries")
        if not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]*:.+", key, re.DOTALL):
            raise BundleError("chapter_order entries must be nonempty exact qualified keys INPUT_NUMBER:ID")
        check_text(key)
        if key in seen:
            raise BundleError(f"duplicate chapter_order key: {key!r}")
        seen.add(key)
        result.append(key)
    return result


def order_chapters(chapters, ready, order):
    """Require a complete permutation, moving cleaned bodies with provenance."""
    pairs = {entry["key"]: (chapter, entry) for chapter, entry in zip(chapters, ready)}
    extra = set(order) - pairs.keys()
    missing = pairs.keys() - set(order)
    if extra or missing:
        raise BundleError("chapter_order must exactly cover usable selected articles; "
                          f"unknown/excluded/skipped keys: {sorted(extra)!r}; omitted keys: {sorted(missing)!r}")
    return [pairs[key][0] for key in order], [pairs[key][1] for key in order]
