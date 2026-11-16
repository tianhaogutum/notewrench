"""Offline, read-only duplicate review for Joplin exports (AGPL-3.0-only)."""

from .analyze import analyze
from .reader import ExportError, Limits, load_export

__all__ = ["analyze", "ExportError", "Limits", "load_export"]
__version__ = "1.0.0"
