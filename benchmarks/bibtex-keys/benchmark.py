# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (MIT; see licenses/bibtex-keys-MIT.txt)
"""Run a bounded, serial scanner benchmark; prints JSON and creates no files."""

import json
from pathlib import Path
import resource
import sys
from time import perf_counter

# Run from a clone with the bibtex-keys extra installed: `python benchmarks/bibtex-keys/benchmark.py`.
ROOT = Path(__file__).resolve().parents[2]
if (ROOT / "src" / "notewrench").is_dir() and str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))  # source checkout without installation
from notewrench.bibtexkeys.notes import NoteScanner  # noqa: E402

scanner = NoteScanner({"Old": {"status": "renamed", "new_key": "New", "identity": "id"}})
rows = []
for repetitions in (100, 500, 1000, 2000, 4000, 8000):
    text = '`@Old` [link](https://example.org/@Old) [@Old] ' * repetitions
    start = perf_counter()
    edits, occurrences, issues = scanner.scan(text)
    rows.append({"repetitions": repetitions, "bytes": len(text), "seconds": round(perf_counter() - start, 4),
                 "edits": len(edits), "maxrss_platform_units": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss})
print(json.dumps(rows, indent=2))
