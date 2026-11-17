# SPDX-License-Identifier: MIT
"""Shared fixtures for the ``notewrench zotero-dupes`` suite (needs the ``zotero-dupes`` extra)."""
import pytest

from .paths import EXAMPLES


@pytest.fixture(scope="session")
def realistic_review():
    from notewrench.zoterodupes.matching import build_review
    from notewrench.zoterodupes.readers import load_library

    records, libraries = [], []
    for label, file in [("personal", "personal.json"), ("group", "group.json")]:
        items, info = load_library(label, EXAMPLES / file)
        records.extend(items); libraries.append(info)
    return build_review(records, libraries)
