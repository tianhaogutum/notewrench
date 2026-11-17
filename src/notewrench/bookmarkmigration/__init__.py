# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (MIT; see licenses/bookmark-migration-MIT.txt)
"""``notewrench bookmark-migration``: read-only audit of a bookmark migration.

Compares a before and an after export (Netscape bookmarks HTML and Karakeep JSON, in
any combination) offline and reports which URLs moved, which entries are missing or
new, whether duplicate counts changed, and conflicts in titles, tags, folders, notes,
export times, archive state and (with an optional manifest) attachment references.
Writes Markdown, JSON and a self-contained HTML report. Standard library only.

Formerly the standalone ``bookmark-migration-audit`` tool (``bookmark-audit``
command). Library API::

    from notewrench.bookmarkmigration.parsers import load_export
    from notewrench.bookmarkmigration.attachments import attach_manifest
    from notewrench.bookmarkmigration.compare import compare_exports
    from notewrench.bookmarkmigration.reports import write_reports
"""

# Recorded as ``tool_version`` in report.json (the report format is unchanged).
__version__ = "0.1.0"
