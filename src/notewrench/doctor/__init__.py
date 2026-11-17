"""``notewrench doctor``: one read-only health check across PKM exports and libraries.

Detects what each given path is (Zotero snapshot + storage, Joplin JEX/raw export,
Calibre library, Karakeep/Wallabag export, browser/linkding bookmark HTML, CSL-JSON
bibliography + Markdown) and runs
the existing read-only analyses of the other notewrench commands on it. Nothing is
written. AGPL-3.0-only.
"""
# SPDX-License-Identifier: AGPL-3.0-only
from .checks import run_checks
from .detect import UsageError, detect

SCHEMA = "notewrench.doctor/v1"
__all__ = ["SCHEMA", "UsageError", "detect", "run_checks"]
__version__ = "1.0.0"
