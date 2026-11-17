# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 tianhaogutum
"""``notewrench links``: offline, provenance-preserving duplicate candidates across
Karakeep and Wallabag exports.

Python port of the ``linkdedupe`` Node.js tool. The library API mirrors the
original ``index.js`` exports; report formats, IDs and schema names are unchanged.
"""

__version__ = "1.0.0"

from .adapters import detect_format  # noqa: E402
from .normalize import TRACKING_PARAMS, normalize_url, validate_options  # noqa: E402
from .report import build_report  # noqa: E402
from .rules import parse_rule_file  # noqa: E402
from .strictjson import parse_json  # noqa: E402
from .wallabag_export import WALLABAG_EXPORT_ADAPTER, build_wallabag_export  # noqa: E402
from .cli import main  # noqa: E402

__all__ = [
    "TRACKING_PARAMS", "WALLABAG_EXPORT_ADAPTER", "build_report", "build_wallabag_export", "detect_format",
    "main", "normalize_url", "parse_json", "parse_rule_file", "validate_options",
]
