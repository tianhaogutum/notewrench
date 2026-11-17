# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Anki Course Merge contributors
"""``notewrench anki-merge``: offline three-way preflight for Anki course note text exports.

Merges old-teacher, new-teacher and student "Notes in Plain Text" exports by a stable
course ID field and writes a reviewable bundle (``review.txt``, ``plan.json``,
``import.tsv``, ``resolutions.json``). Collection import remains Anki's job.

Formerly the standalone ``anki-course-merge`` tool (MIT; see THIRD_PARTY.md). This
module stays light: the ``csvdiff`` dependency (``pip install 'notewrench[anki-merge]'``)
is imported only when a merge runs.
"""

# Version written to plan.json as ``tool_version`` and hashed into the review
# fingerprint. Kept at the standalone tool's value so existing resolutions stay valid.
FORMAT_VERSION = "0.1.0"
__version__ = FORMAT_VERSION
