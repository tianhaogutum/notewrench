# SPDX-License-Identifier: MIT
# Copyright (c) 2026 bookmark-receipts contributors (see licenses/browser-bookmarks-MIT.txt)
"""Reproduce the linkding-v*.html fixtures offline from the exact, MIT-licensed upstream exporters.

Run from a clone: `python tests/browserbookmarks/upstream/generate_fixtures.py`.
"""

import ast
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent / "fixtures"

for version, label in (("1.47", "current"), ("1.31", "older"), ("1.15", "legacy")):
    source = HERE / f"linkding_v{version.replace('.', '_')}_exporter.py"
    tree = ast.parse(source.read_text())
    tree.body = [node for node in tree.body if not isinstance(node, ast.ImportFrom) or not (node.module or "").startswith("bookmarks")]
    namespace = {"Bookmark": object}
    exec(compile(tree, str(source), "exec"), namespace)
    bookmark = SimpleNamespace(
        url="https://example.org/" + label, resolved_title="Title " + label,
        resolved_description="Description " + label, notes="Markdown **note**\nsecond line",
        tag_names=["Reading", "linkding:archived"] if label == "older" else ["Reading"],
        is_archived=True, unread=True, shared=False,
        date_added=datetime(2020, 1, 1, tzinfo=UTC), date_modified=datetime(2024, 1, 1, tzinfo=UTC),
    )
    destination = FIXTURES / f"linkding-v{version}.html"
    destination.write_bytes(namespace["export_netscape_html"]([bookmark]).encode())
    print(destination.relative_to(HERE.parent))
