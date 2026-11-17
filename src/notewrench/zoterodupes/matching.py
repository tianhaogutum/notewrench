# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (see licenses/zotero-dupes-MIT.txt)
"""Bounded adapter around BibDedupe, with sparse-export candidate additions."""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from importlib.metadata import version

import pandas as pd
from bib_dedupe.bib_dedupe import block, match, prep
from bib_dedupe.block import block_fields_list
from rapidfuzz import fuzz, process

from .model import InputError, Record, canonical_item_type, digest, identifier_values, normalized_title, stable_id, year_of


def block_upper_bound(frame: pd.DataFrame, limit: int) -> int:
    """Check before upstream block materializes combinations, including same-set pairs.

    It can emit each pair once per rule before dropping duplicates. This upper
    bound deliberately counts repeats to put a bound on transient memory too.
    """
    total = 0
    for fields in block_fields_list:
        cols = sorted(fields)
        selected = frame.loc[frame[cols].ne("").all(axis=1), cols]
        sizes = selected.groupby(cols, dropna=False).size()
        total += sum(int(n) * (int(n) - 1) // 2 for n in sizes)
        if total > limit:
            raise InputError(f"BibDedupe blocking would allocate at least {total:,} intermediate pairs (limit {limit:,}). Export narrower collections or raise --max-block-pairs, up to 100000.")
    return total


def cross_library_block_records(frame: pd.DataFrame) -> pd.DataFrame:
    """Remove records unable to form any cross-library upstream block pair.

    Upstream block() constructs an empty DataFrame for every singleton block,
    then drops same-search-set pairs after allocation. Select only records in
    a nonempty block spanning libraries before calling its unchanged routine.
    Every pair that could survive its same-search-set filter keeps both ends.
    """
    eligible: set[str] = set()
    for fields in block_fields_list:
        cols = sorted(fields)
        selected = frame.loc[frame[cols].ne("").all(axis=1)]
        if selected.empty:
            continue
        libraries = selected.groupby(cols, dropna=False)["search_set"].transform("nunique")
        eligible.update(selected.loc[libraries > 1, "ID"])
    return frame.loc[frame["ID"].isin(eligible)].copy()


def candidate_clusters(pairs: list[dict]) -> list[dict]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for pair in pairs:
        adjacency[pair["left"]].add(pair["right"])
        adjacency[pair["right"]].add(pair["left"])
    seen: set[str] = set()
    clusters = []
    for start in sorted(adjacency):
        if start in seen:
            continue
        stack, members = [start], []
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            members.append(node)
            stack.extend(sorted(adjacency[node] - seen))
        members.sort()
        member_set = set(members)
        clusters.append({"id": stable_id("cluster", *members), "members": members,
                         "pairs": [p["id"] for p in pairs if p["left"] in member_set]})
    return clusters


def find_candidates(records: list[Record], *, title_threshold: float = 92,
                    max_block_pairs: int = 50_000, max_title_comparisons: int = 5_000_000,
                    max_candidates: int = 10_000) -> tuple[list[dict], dict]:
    if not 70 <= title_threshold <= 100:
        raise InputError("Title threshold must be between 70 and 100")
    if not 1 <= max_block_pairs <= 100_000:
        raise InputError("max-block-pairs must be between 1 and 100000")
    if not 1 <= max_title_comparisons <= 25_000_000:
        raise InputError("max-title-comparisons must be between 1 and 25000000")
    if not records:
        return [], {"engine": "bib-dedupe", "version": version("bib-dedupe"), "blocked_upper_bound": 0, "title_comparisons": 0, "isbnlib_version": version("isbnlib")}
    by_id = {r.id: r for r in records}
    pairs: dict[tuple[str, str], dict] = {}
    normalized = {r.id: normalized_title(r.metadata.get("title", "")) for r in records}
    identifiers = {r.id: identifier_values(r.metadata) for r in records}
    library_items: dict[str, list[Record]] = defaultdict(list)
    for record in records:
        library_items[record.library].append(record)
    comparisons = sum(len(a) * len(b) for a, b in combinations(library_items.values(), 2))
    if comparisons > max_title_comparisons:
        raise InputError(f"Title review would compare {comparisons:,} cross-library pairs (limit {max_title_comparisons:,}). Export narrower collections or raise --max-title-comparisons, up to 25000000.")

    def add(a: str, b: str, reason: str, bib_label: str | None = None) -> None:
        if a == b or by_id[a].library == by_id[b].library:
            return
        key = tuple(sorted((a, b)))
        if key not in pairs:
            if len(pairs) >= max_candidates:
                raise InputError(f"More than {max_candidates:,} candidate pairs; export narrower collections")
            left, right = (by_id[i] for i in key)
            warnings = []
            for name in ["DOI", "PMID"]:
                d1, d2 = (set(identifiers[r.id][name]) for r in [left, right])
                if d1 and d2 and not (d1 & d2):
                    warnings.append(f"Conflicting {name} values: this may be a related publication or a different version")
            y1, y2 = (year_of(r.metadata.get("date", "")) for r in [left, right])
            if y1.isdigit() and y2.isdigit() and abs(int(y1) - int(y2)) > 1:
                warnings.append("Publication years differ by more than one")
            if canonical_item_type(left.metadata.get("type", "")) != canonical_item_type(right.metadata.get("type", "")):
                warnings.append("Item types differ; distinguish chapters, preprints and published versions")
            if min(len(normalized[a]), len(normalized[b])) < 12:
                warnings.append("Short or missing title: verify identifiers and authors carefully")
            pairs[key] = {"id": stable_id("pair", *key), "left": key[0], "right": key[1],
                          "title_similarity": round(fuzz.ratio(normalized[a], normalized[b]), 2),
                          "reasons": [], "warnings": warnings, "bib_dedupe_label": None}
        if reason not in pairs[key]["reasons"]:
            pairs[key]["reasons"].append(reason)
        if bib_label:
            pairs[key]["bib_dedupe_label"] = bib_label

    frame = pd.DataFrame([r.matching_row() for r in records])
    prepared = prep(frame, cpu=1, verbosity_level=0)
    blockable = cross_library_block_records(prepared)
    upper_bound = block_upper_bound(blockable, max_block_pairs)
    blocked = block(blockable, cpu=1, verbosity_level=0) if not blockable.empty else pd.DataFrame()
    if not blocked.empty:
        matched = match(blocked, cpu=1, verbosity_level=0)
        for row in matched.to_dict(orient="records"):
            add(str(row["ID_1"]), str(row["ID_2"]), "BibDedupe " + str(row["duplicate_label"]), str(row["duplicate_label"]))
    for name in ["DOI", "ISBN", "PMID"]:
        index: dict[str, list[Record]] = defaultdict(list)
        for record in records:
            for identifier in identifiers[record.id][name]:
                index[identifier].append(record)
        for identifier in sorted(index):
            group = index[identifier]
            for a, b in combinations(group, 2):
                add(a.id, b.id, "Exact " + name)
                if name in {"DOI", "PMID"} and a.library != b.library and any(r.metadata.get(name) in (None, "") for r in [a, b]):
                    add(a.id, b.id, name + " extracted from Extra")
                if name == "ISBN" and a.library != b.library:
                    warning = "An ISBN identifies an edition, not necessarily the same chapter or contribution"
                    pair = pairs[tuple(sorted((a.id, b.id)))]
                    if warning not in pair["warnings"]:
                        pair["warnings"].append(warning)
    for lib_a, lib_b in combinations(sorted(library_items), 2):
        choices = {r.id: normalized[r.id] for r in library_items[lib_b] if normalized[r.id]}
        for record in library_items[lib_a]:
            title = normalized[record.id]
            if not title:
                continue
            # extract_iter does not build an N×M matrix or truncate to top-k.
            for _, score, other_id in process.extract_iter(title, choices, scorer=fuzz.ratio, score_cutoff=title_threshold):
                add(record.id, other_id, "Exact normalized title" if score == 100 else "Similar title")
    result = sorted(pairs.values(), key=lambda p: ("Exact DOI" not in p["reasons"], -p["title_similarity"], p["id"]))
    for pair in result:
        pair["reasons"].sort()
    return result, {"engine": "bib-dedupe", "version": version("bib-dedupe"), "cpu": 1,
                    "title_threshold": title_threshold, "blocked_upper_bound": upper_bound,
                    "block_eligible_records": len(blockable), "title_comparisons": comparisons,
                    "rapidfuzz_version": version("rapidfuzz"), "isbnlib_version": version("isbnlib")}


def build_review(records: list[Record], libraries: list[dict], **options) -> dict:
    labels = [lib["label"] for lib in libraries]
    if len(labels) < 2 or len(labels) != len(set(labels)):
        raise InputError("Provide at least two libraries with distinct, persistent labels")
    if any(record.library not in labels for record in records):
        raise InputError("Every source record must belong to a listed library")
    if len(records) > 10_000:
        raise InputError("More than 10,000 parent items across all libraries; export narrower collections")
    serialized = {record.id: record.to_dict() for record in sorted(records, key=lambda r: r.id)}
    if len(serialized) != len(records):
        raise InputError("Duplicate source identities across input libraries")
    pairs, engine = find_candidates(records, **options)
    review = {"schema_version": 1, "libraries": sorted(libraries, key=lambda lib: lib["label"]),
              "records": serialized, "pairs": pairs, "clusters": candidate_clusters(pairs), "matching": engine}
    review["review_id"] = "review-" + digest(review)[:32]
    return review

