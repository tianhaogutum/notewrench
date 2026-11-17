# SPDX-License-Identifier: MIT
# Copyright (c) 2026 citekey-audit contributors (MIT; see licenses/bibtex-keys-MIT.txt)
"""``notewrench bibtex-keys``: review citation-key changes between two stable-ID snapshots.

An offline review adapter for citation-key changes, never a key generator. It compares
two BibTeX/BibLaTeX exports or Better BibTeX JSON snapshots by an explicit stable item
identity (by default the Zotero item URI), scans a Markdown notes directory and writes a
review bundle (``review.json``, ``changes.patch``, ``REVIEW.md``); notes are never changed.

Formerly the standalone ``citekey-audit`` tool. Needs the optional ``bibtex-keys``
dependencies (``pip install 'notewrench[bibtex-keys]'``); this module itself imports
none of them, so the command's help works without them. Library API::

    from notewrench.bibtexkeys.planner import make_plan, write_bundle
    from notewrench.bibtexkeys.verify import verify_bundle
"""

# Recorded as ``tool_version`` in review.json (the bundle format is unchanged).
__version__ = "0.1.0"
