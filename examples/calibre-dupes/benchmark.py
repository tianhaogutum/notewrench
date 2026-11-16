#!/usr/bin/env python3
"""Deterministic labeled cross-library comparison; no speedup claims."""

import argparse
from collections import defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import statistics
import tempfile
import time

from notewrench.calibredupes.compare import find_pairs
from notewrench.calibredupes.reader import read_library

# Run as `python examples/calibre-dupes/benchmark.py` with notewrench installed.
# The sibling synthetic.py is a script, not a package: load it by path under a unique
# module name instead of putting this directory on the import path (a bare `synthetic`
# import could collide with other helper modules of that name).
_SPEC = importlib.util.spec_from_file_location(
    "_notewrench_calibredupes_synthetic", Path(__file__).resolve().parent / "synthetic.py")
_synthetic = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_synthetic)
create_library = _synthetic.create_library


def generated_isbn(serial: int) -> str:
    body = "978" + f"{serial:09d}"
    return body + str((-sum(int(c) * (1 if i % 2 == 0 else 3)
                                for i, c in enumerate(body))) % 10)


def ten_digit(isbn: str) -> str:
    body = isbn[3:12]
    check = (-sum((10 - i) * int(c) for i, c in enumerate(body))) % 11
    return body + ("X" if check == 10 else str(check))


def fixture(groups: int) -> tuple[list[dict], list[dict], set[tuple[str, str]]]:
    left, right, truth = [], [], set()
    for group in range(groups):
        def book(case, *, side="", author="Ari Example", isbn=None):
            result = {"title": f"Case {case} Group {group}{side}",
                      "authors": [] if author is None else [author],
                      "formats": ["EPUB"]}
            if isbn:
                result["identifiers"] = {"isbn": isbn}
            return result

        isbn = generated_isbn(group * 10 + 1)
        cases = [
            (book(1, isbn=ten_digit(isbn)), book(1, isbn=isbn), True),
            ({"title": f"ＣＡＦÉ—Garden {group}", "authors": ["Zoë Example"], "formats": ["EPUB"]},
             {"title": f"café garden {group}", "authors": ["Zoë Example"], "formats": ["PDF"]}, True),
            (book(3), book(3, author="Other Author"), False),
            (book(4, isbn=generated_isbn(group * 10 + 2)),
             book(4, isbn=generated_isbn(group * 10 + 3)), False),
            (book(5, isbn="bad-checksum"), book(5, isbn="bad-checksum"), True),
            (book(6, author=None), book(6), True),
            (book(7, isbn=generated_isbn(group * 10 + 4)),
             book(7, side=" different title", author="Other Author", isbn=generated_isbn(group * 10 + 4)), False),
            (book(8), book(8), True),
            (book(9), book(9, side=" unrelated", author="Other Author"), False),
        ]
        for a, b, positive in cases:
            left.append(a)
            right.append(b)
            if positive:
                truth.add((f"A:{len(left)}", f"B:{len(right)}"))
    return left, right, truth


def baseline(books) -> tuple[set[tuple[str, str]], int]:
    index = defaultdict(list)
    for book in books:
        if book.library == "B":
            index[book.title].append(book.key)
    result = {(a.key, b) for a in books if a.library == "A" for b in index[a.title]}
    return result, len(result)


def metrics(pairs: set, truth: set) -> dict:
    true = len(pairs & truth)
    return {"candidateCount": len(pairs), "truePositives": true,
            "falsePositives": len(pairs - truth), "falseNegatives": len(truth - pairs),
            "precision": true / len(pairs) if pairs else None,
            "recall": true / len(truth) if truth else None}


def run(groups: int, repeats: int) -> dict:
    left, right, truth = fixture(groups)
    fixture_json = json.dumps({"A": left, "B": right, "truth": sorted(truth)}, sort_keys=True, ensure_ascii=False)
    with tempfile.TemporaryDirectory(prefix="calibre-dupes-benchmark-") as temp:
        base = Path(temp)
        a = create_library(base / "a", left)
        b = create_library(base / "b", right)
        start = time.perf_counter()
        libraries = [read_library(a, "A"), read_library(b, "B")]
        load_seconds = time.perf_counter() - start
        books = [book for lib in libraries for book in lib.books]
        baseline_times, matcher_times = [], []
        for _ in range(repeats):
            start = time.perf_counter()
            raw, raw_examined = baseline(books)
            baseline_times.append(time.perf_counter() - start)
            start = time.perf_counter()
            edges, examined = find_pairs(books, cross_library=True, max_pairs=groups * 20)
            matcher_times.append(time.perf_counter() - start)
        predicted = {(edge["a"], edge["b"]) for edge in edges}
        strong = {(edge["a"], edge["b"]) for edge in edges if edge["level"] == "strong"}
        return {"fixtureVersion": 1, "groups": groups, "repeats": repeats,
                "fixtureSHA256": hashlib.sha256(fixture_json.encode()).hexdigest(),
                "python": platform.python_version(), "platform": platform.platform(),
                "booksPerLibrary": len(left), "labeledPositivePairs": len(truth),
                "possibleCrossPairs": len(left) * len(right),
                "sharedReadSeconds": load_seconds,
                "timingScope": "matching only, same loaded records; sequential raw baseline then matcher per repeat; no warmup",
                "baselineRawTitleEquality": {**metrics(raw, truth), "examinedPairs": raw_examined,
                                             "seconds": baseline_times, "medianSeconds": statistics.median(baseline_times)},
                "isbnAndNormalizedAuthor": {**metrics(predicted, truth), "examinedPairs": examined,
                                            "seconds": matcher_times, "medianSeconds": statistics.median(matcher_times)},
                "strongEvidenceSubset": metrics(strong, truth),
                "limitations": ["Synthetic fictional works; labels are fixture identities, not real bibliographic judgments.",
                                "Candidates intentionally include conflicting ISBN/title cases for review; strong subset is conservative.",
                                "Small single-process microbenchmark; no claim of general speed or superiority over plugins."]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--groups", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.groups <= 1000 or not 1 <= args.repeats <= 10:
        parser.error("groups must be 1–1000 and repeats 1–10")
    print(json.dumps(run(args.groups, args.repeats), indent=2))


if __name__ == "__main__":
    main()
