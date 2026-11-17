# SPDX-License-Identifier: MIT
# Copyright (c) 2026 archive-relink contributors
"""Generate a bounded realistic archive and measure a full ``notewrench relink`` index/scan round.

Run from a clone with the relink extra installed:
``python benchmarks/relink/benchmark.py --pages 2000 --links 2000``. All generated
files stay under ``.cache/relink-benchmark`` in the repository. No server, network,
subprocess workers, or unbounded in-memory fixture lists. Timings are single local
runs, not claims about other machines.
"""

import argparse
import cProfile
import json
import resource
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if (ROOT / "src" / "notewrench").is_dir() and str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))  # source checkout without installation
from notewrench.relink.workflow import build_index, scan_notes  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=2000)
    parser.add_argument("--links", type=int, default=2000)
    parser.add_argument("--body-kib", type=int, default=4)
    parser.add_argument("--profile", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.pages <= 20000 or not 1 <= args.links <= 20000:
        parser.error("pages and links must be in 1..20000")
    if not 1 <= args.body_kib <= 32:
        parser.error("body-kib must be in 1..32")
    root = ROOT / ".cache/relink-benchmark"
    if root.exists():
        shutil.rmtree(root)
    archive = root / "archive"
    archive.mkdir(parents=True)
    phrase = "This distinctive passage documents a privately archived research article."
    base = "Long-lived personal archives need original addresses and textual evidence. "
    body = (base * ((args.body_kib * 1024) // len(base) + 1))[: args.body_kib * 1024]
    for index in range(args.pages):
        directory = archive / f"{index // 100:03d}"
        directory.mkdir(exist_ok=True)
        (directory / f"{index}.html").write_text(
            "<!doctype html><!--\n Page saved with SingleFile\n"
            f" url: https://old.example/articles/{index}\n--><meta charset=utf-8>"
            f"<title>Article {index}</title><h1>Article {index}</h1><p>{body}</p>"
            + (f"<p>{phrase}</p>" if index % 257 == 0 else "")
        )
    note = root / "notes.md"
    with note.open("w") as stream:
        for index in range(args.links):
            stream.write(
                f"{index}. [Article {index}](https://old.example/articles/{index % args.pages}) "
                "with a sentence of private research notes.\n\n"
            )
        stream.write(f"[Missing source](https://lost.example/article) said “{phrase}”\n")
    database = root / "index.sqlite"
    started = time.perf_counter()
    result = build_index(archive, database)
    index_seconds = time.perf_counter() - started
    started = time.perf_counter()
    if args.profile:
        profiler = cProfile.Profile()
        plan = profiler.runcall(scan_notes, database, [note])
        profiler.dump_stats(str(root / "scan.pstats"))
    else:
        plan = scan_notes(database, [note])
    scan_seconds = time.perf_counter() - started
    assert result["documents"] == args.pages and not result["warnings"]
    assert len(plan["links"]) == args.links + 1
    assert all(link["candidates"] for link in plan["links"])
    stats = {
        "pages": args.pages,
        "citations": args.links + 1,
        "body_kib": args.body_kib,
        "index_seconds": round(index_seconds, 3),
        "scan_seconds": round(scan_seconds, 3),
        "max_rss_mib": round(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            / (1024 * 1024 if sys.platform == "darwin" else 1024),
            1,
        ),
        "index_mib": round(database.stat().st_size / (1024 * 1024), 1),
        "note_kib": round(note.stat().st_size / 1024, 1),
        "quote_results_truncated": plan["links"][-1]["candidates_truncated"],
    }
    print(json.dumps(stats, indent=2))
    (root / "results.json").write_text(json.dumps(stats, indent=2) + "\n")


if __name__ == "__main__":
    main()
