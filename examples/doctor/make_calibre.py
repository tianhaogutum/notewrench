"""Recreate examples/doctor/calibre/metadata.db: a tiny, fictional Calibre library.

Usage: python3 examples/doctor/make_calibre.py NEW_DIRECTORY

Reuses the minimal Calibre projection of examples/calibre-dupes/synthetic.py. The
records are fictional; checksum-valid ISBNs are mathematical examples. No ebook files.
"""
# SPDX-License-Identifier: AGPL-3.0-only
import importlib.util
from pathlib import Path
import sys

RECORDS = [
    {"title": "The Clockwork Estuary", "authors": ["Mira Example"], "identifiers": {"isbn": "0-306-40615-2"}, "formats": ["EPUB"]},
    {"title": "The Clockwork Estuary", "authors": ["Mira Example"], "identifiers": {"isbn": "9780306406157"}, "formats": ["PDF"]},
    {"title": "ＣＡＦÉ—Maps", "authors": ["Zoë Sample"], "formats": ["EPUB"]},
    {"title": "café maps", "authors": ["Zoë Sample"], "formats": ["PDF"]},
    {"title": "Tide Tables for Inland Seas", "authors": ["Alice Example"], "formats": ["EPUB"]},
    {"title": "Bad Checksum", "authors": ["Lee Example"], "identifiers": {"isbn": "9780306406158"}, "formats": ["EPUB"]},
    {"title": "Unattributed Atlas", "authors": [], "identifiers": {"isbn": "9780201633610"}, "formats": ["PDF"]},
]

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    spec = importlib.util.spec_from_file_location(
        "_calibre_synthetic", Path(__file__).resolve().parents[1] / "calibre-dupes" / "synthetic.py")
    synthetic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(synthetic)
    print(synthetic.create_library(Path(sys.argv[1]), RECORDS))
