# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (see licenses/browser-bookmarks-MIT.txt)
"""``notewrench browser-bookmarks``: offline review of duplicate browser and linkding bookmarks.

Scans browser Netscape-HTML bookmark exports and linkding HTML exports, groups
bookmarks by a conservative URL key, and requires an explicit merge-or-keep
decision per duplicate group before writing merged HTML with complete receipts.

This module stays dependency-free (the command's ``browser-bookmarks`` extra
provides Beautiful Soup, html5lib, webencodings, filelock and ijson); import
``parsing``, ``review``, ``merging`` or ``verification`` for the library API.
"""

# Top-level import names provided by `pip install 'notewrench[browser-bookmarks]'`.
REQUIRES = ("bs4", "html5lib", "webencodings", "filelock", "ijson")

__all__ = ["REQUIRES"]
__version__ = "0.1.0"
