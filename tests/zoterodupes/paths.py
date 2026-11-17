# SPDX-License-Identifier: MIT
"""Paths shared by the ``notewrench zotero-dupes`` suite."""
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EXAMPLES = ROOT / "examples" / "zotero-dupes"
FIXTURES = HERE / "fixtures"
UPSTREAM = FIXTURES / "upstream"
