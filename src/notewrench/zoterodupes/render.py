# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (see licenses/zotero-dupes-MIT.txt)
"""Generate one self-contained offline review artifact."""

import json
from pathlib import Path

from .model import InputError, readable_note

MAX_HTML_DATA_BYTES = 32 * 1024 * 1024


def render_review(review: dict, decisions: dict) -> str:
    root = Path(__file__).parent
    # JSON in script elements must not contain HTML end-tags, including in notes.
    def safe(payload):
        pieces, size = [], 0
        for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False).iterencode(payload):
            for offset in range(0, len(chunk), 65536):
                piece = chunk[offset:offset + 65536].replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
                size += len(piece.encode("utf-8"))
                if size > MAX_HTML_DATA_BYTES:
                    raise InputError("Offline HTML data exceeds 32 MiB after safe escaping; export a smaller collection")
                pieces.append(piece)
        return "".join(pieces)
    note_text = {item_id: [readable_note(value) for value in record["notes"]] for item_id, record in review["records"].items()}
    return (root / "review.html").read_text(encoding="utf-8").replace("/*BRIDGE_CSS*/", (root / "review.css").read_text()).replace("/*BRIDGE_JS*/", (root / "review.js").read_text()).replace("/*BRIDGE_DATA*/", safe({"review": review, "decisions": decisions, "note_text": note_text}))

