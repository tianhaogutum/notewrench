# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (see licenses/browser-bookmarks-MIT.txt)
"""The command's one error type, importable without third-party packages."""


class ReviewError(ValueError):
    """An actionable input or review error, suitable for display without a traceback."""
