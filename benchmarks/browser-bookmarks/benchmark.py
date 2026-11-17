# SPDX-License-Identifier: MIT
# Copyright (c) 2026 bookmark-receipts contributors (see licenses/browser-bookmarks-MIT.txt)
"""Run one bounded scan/decide/apply/verify pipeline on synthetic linkding exports; temporary data is removed.

Run from a clone (needs the browser-bookmarks extra):
`python benchmarks/browser-bookmarks/benchmark.py --groups 5000`. Timings depend on the host.
"""

import argparse
import gc
import json
import resource
import shutil
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
if (ROOT / "src" / "notewrench").is_dir() and str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))  # source checkout without installation
from notewrench.browserbookmarks import _linkding_exporter as producer  # noqa: E402
from notewrench.browserbookmarks.merging import apply_review  # noqa: E402
from notewrench.browserbookmarks.review import create_review, load_review, save_review  # noqa: E402
from notewrench.browserbookmarks.verification import verify_export  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--groups", type=int, default=1000)
args = parser.parse_args()
if not 1 <= args.groups <= 12_500:
    parser.error("groups must be 1..12500 (two records per group)")
if shutil.disk_usage(tempfile.gettempdir()).free < 64 * 1024 * 1024 + args.groups * 12_000:
    parser.error("insufficient free space for this temporary benchmark")
metrics = {"groups": args.groups, "records": args.groups * 2}
epoch = datetime(2024, 1, 1, tzinfo=UTC)
with tempfile.TemporaryDirectory(prefix="browser-bookmarks-benchmark-") as temporary:
    directory = Path(temporary)
    inputs = []
    for copy in (1, 2):
        bookmarks = [SimpleNamespace(
            url=f"https://example.org/paper/{index}?a=1&b=2", resolved_title=f"Paper {index}, copy {copy}",
            resolved_description=f"Description from library {copy}",
            notes=f"Import {copy}: context matters.\n- Keep this source-specific note.",
            tag_names=["Research", "Read", f"import:{copy}"], is_archived=False, unread=True, shared=False,
            date_added=epoch, date_modified=epoch,
        ) for index in range(args.groups)]
        source = directory / f"linkding-{copy}.html"
        source.write_text(producer.export_netscape_html(bookmarks), encoding="utf-8")
        inputs.append((source, "linkding"))
    metrics["input_bytes"] = sum(path.stat().st_size for path, _ in inputs)
    started = time.perf_counter()
    plan = create_review(inputs, directory / "review")
    metrics["scan_seconds"] = round(time.perf_counter() - started, 4)
    for group in plan["groups"]:
        group["decision"]["action"] = "merge"
    path = directory / "review/review.json"
    started = time.perf_counter()
    save_review(path, plan)
    metrics["decide_seconds"] = round(time.perf_counter() - started, 4)
    del plan, bookmarks
    gc.collect()
    started = time.perf_counter()
    plan = load_review(path)
    result = apply_review(plan, path, directory / "result")
    metrics["load_and_apply_seconds"] = round(time.perf_counter() - started, 4)
    del plan
    gc.collect()
    started = time.perf_counter()
    verified = verify_export(directory / "result")
    metrics["verify_seconds"] = round(time.perf_counter() - started, 4)
    metrics.update(verified)
    metrics["output_records"] = result["outputs"]
    metrics["review_json_bytes"] = path.stat().st_size
    metrics["receipt_json_bytes"] = (directory / "result/receipts.json").stat().st_size
    metrics["peak_rss_mib"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)
    metrics["notes"] = "One process; actual linkding producer; temporary inputs/results removed"
print(json.dumps(metrics))
