# SPDX-License-Identifier: AGPL-3.0-only
"""Reproducible labeled exports; accuracy experiment, not a speedup claim."""
from __future__ import annotations

import argparse
import importlib.util
from collections import defaultdict
from itertools import combinations
import json
from pathlib import Path
import statistics
import sys
import tempfile
from time import perf_counter

# Run from a clone: `python benchmarks/joplin-dupes/benchmark.py --families 40 --repeats 3`.
ROOT = Path(__file__).resolve().parents[2]
if (ROOT / "src" / "notewrench").is_dir() and str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))  # source checkout without installation
from notewrench.joplindupes import analyze, load_export  # noqa: E402
from notewrench.joplindupes.reader import normalize, parse_item  # noqa: E402

# The labeled corpus generator is shared with the test suite. Load it by path under a
# unique module name rather than importing a top-level `tests` package.
_SPEC = importlib.util.spec_from_file_location(
    "_notewrench_joplindupes_synthetic", ROOT / "tests" / "joplindupes" / "synthetic.py")
_synthetic = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_synthetic)
benchmark_files, write_raw = _synthetic.benchmark_files, _synthetic.write_raw


def pairs(groups):
    return {tuple(sorted(pair)) for group in groups for pair in combinations(group, 2)}


def title_baseline(raw: Path):
    # A genuine title-only baseline: reads metadata, never opens resource bytes.
    buckets = defaultdict(list)
    metadata_bytes = 0
    metadata_files = 0
    for path in sorted(raw.glob("*.md")):
        data = path.read_bytes()
        metadata_bytes += len(data)
        metadata_files += 1
        item = parse_item(data, path.name)
        if item.type == 1:
            buckets[normalize(item.title)].append(item.id)
    return pairs(buckets.values()), {"resource_hashes": 0, "resource_bytes_hashed": 0,
                                     "note_signatures": 0, "note_hashes": 0, "metadata_bytes_read": metadata_bytes,
                                     "metadata_files": metadata_files}


def score(predicted, truth):
    tp, fp, fn = len(predicted & truth), len(predicted - truth), len(truth - predicted)
    return {"true_positives": tp, "false_positives": fp, "false_negatives": fn,
            "precision": tp / (tp + fp) if tp + fp else 0.0,
            "recall": tp / (tp + fn) if tp + fn else 0.0,
            "predicted_pairs": len(predicted)}


def run_benchmark(families: int = 40, repeats: int = 3) -> dict:
    if not 1 <= families <= 1000 or not 1 <= repeats <= 10:
        raise ValueError("families must be 1..1000; repeats must be 1..10")
    files, truth = benchmark_files(families)
    baseline_times, signature_times = [], []
    baseline_predictions, signature_predictions = None, None
    with tempfile.TemporaryDirectory(prefix="joplin-dedupe-benchmark-") as temp:
        raw = write_raw(Path(temp) / "raw", files)
        for _ in range(repeats):
            start = perf_counter()
            predicted, baseline_metrics = title_baseline(raw)
            baseline_times.append(perf_counter() - start)
            if baseline_predictions is not None:
                assert predicted == baseline_predictions
            baseline_predictions = predicted
            start = perf_counter()
            report = analyze([load_export(raw)])
            signature_times.append(perf_counter() - start)
            predicted = pairs([[key.split(":", 1)[1] for key in group["members"]]
                               for group in report["exact_duplicates"]])
            if signature_predictions is not None:
                assert predicted == signature_predictions
            signature_predictions = predicted
            assert not report["warnings"] and report["summary"]["blocked_notes"] == 0
    assert signature_predictions == truth, "Synthetic gold labels must match the conservative policy"
    return {
        "dataset": {"families": families, "notes": families * 6, "gold_duplicate_pairs": len(truth),
                    "files": len(files), "repeats": repeats,
                    "cases_per_family": ["three copies, one renamed", "same body/different attachment",
                                         "same body/completed TODO", "same title/different body"]},
        "method": "Sequential warm-cache trials; perf_counter; generation excluded; each method reads its own files. Title baseline reads all metadata and no blobs. Signature method includes reading, hashing and report construction. Times are observations, not speedup claims.",
        "title_only": {**score(baseline_predictions, truth), "counts": baseline_metrics,
                       "elapsed_seconds": baseline_times, "median_seconds": statistics.median(baseline_times)},
        "body_resource_state": {**score(signature_predictions, truth), "counts": report["metrics"],
                                "elapsed_seconds": signature_times,
                                "median_seconds": statistics.median(signature_times)},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--families", type=int, default=40)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    try:
        result = run_benchmark(args.families, args.repeats)
    except ValueError as e:
        parser.error(str(e))
    print(json.dumps(result, indent=2, sort_keys=True))
