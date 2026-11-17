# SPDX-License-Identifier: MIT
# Copyright (c) 2026 archive-relink contributors
"""``notewrench relink``: reviewable Markdown backlinks to existing private web archives.

Formerly the standalone ``archive-relink`` tool. Finds an existing local copy
(SingleFile/saved HTML, ArchiveBox, nb, WARC, MHTML) for a Markdown citation and
writes a patch only for candidates you explicitly select. Never contacts the web.

This package ``__init__`` stays free of third-party imports so ``notewrench --help``
can locate the command without the optional ``relink`` extra; the modules that do
the work need ``pip install 'notewrench[relink]'``.
"""

# Version of the relink index and plan formats (recorded in saved files as
# "archive-relink <version>"); unrelated to the notewrench release version.
__version__ = "0.1.0"
