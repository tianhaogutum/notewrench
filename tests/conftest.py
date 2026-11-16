"""Shared configuration for all tool suites.

* Temporary directories are created under the *real* temp path. On macOS ``/var``
  and ``/tmp`` are symlinks to ``/private/...``; several tools deliberately refuse
  inputs and outputs reached through a symlinked ancestor, and some tests compare
  paths for equality, so the suites must not see the symlinked spelling. The
  environment variable makes subprocesses (``python -m notewrench ...``) agree.
* Suites for tools that need Linux-only kernel interfaces are skipped elsewhere:
  ``notewrench archive`` publishes with ``renameat2(RENAME_NOREPLACE)`` and
  ``notewrench zotero-attachments`` opens SQLite snapshots through ``/proc/self/fd``.
"""
import os
import sys
import tempfile

_REAL_TMP = os.path.realpath(tempfile.gettempdir())
tempfile.tempdir = _REAL_TMP
os.environ["TMPDIR"] = _REAL_TMP

collect_ignore = []
if not sys.platform.startswith("linux"):
    collect_ignore += ["archive", "zoteroattachments"]
