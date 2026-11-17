# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (MIT; see licenses/bookmark-migration-MIT.txt)
"""Values with availability status and provenance for every audited entry."""

from dataclasses import asdict, dataclass, field
from typing import Any


class AuditError(Exception):
    """An actionable input or output error, safe to show without a traceback."""


@dataclass
class Field:
    value: Any = None
    status: str = "available"
    reason: str = ""


def unavailable(reason: str, status: str = "unsupported") -> Field:
    return Field(status=status, reason=reason)


@dataclass
class Entry:
    location: str
    url: str
    fields: dict[str, Field]
    raw: dict[str, Any]
    line: int | None = None
    attachments: Field = field(default_factory=lambda: unavailable("native exports have no attachment list"))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Export:
    path: str
    format: str
    entries: list[Entry]
    skipped: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
