# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (see licenses/zotero-dupes-MIT.txt)
"""``notewrench zotero-dupes``: read-only cross-library duplicate review of Zotero exports.

Formerly the standalone ``library-bridge`` tool (MIT). Compares a personal library
export with one or more group library exports (Better BibTeX JSON, CSL JSON, Zotero
RDF), renders an offline HTML review, and exports only explicitly confirmed
correspondences. Matching uses BibDedupe, RDFLib, RapidFuzz and isbnlib from the
optional ``zotero-dupes`` extra; this module itself imports nothing third-party so
the ``notewrench`` dispatcher can locate it cheaply.
"""

__version__ = "0.1.0"
