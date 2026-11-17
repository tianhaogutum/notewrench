"""Shared configuration for all tool suites.

* Temporary directories are created under the *real* temp path. On macOS ``/var``
  and ``/tmp`` are symlinks to ``/private/...``; several tools deliberately refuse
  inputs and outputs reached through a symlinked ancestor, and some tests compare
  paths for equality, so the suites must not see the symlinked spelling. The
  environment variable makes subprocesses (``python -m notewrench ...``) agree.
* Suites for tools that need Linux-only kernel interfaces are skipped elsewhere:
  ``notewrench archive`` publishes with ``renameat2(RENAME_NOREPLACE)`` and
  ``notewrench zotero-attachments`` opens SQLite snapshots through ``/proc/self/fd``.
* Suites for commands with an optional extra are skipped when its packages (or the
  test-only Hypothesis they use) are not installed; ``pip install -e '.[test]'``
  installs all of them.
"""
import importlib.util
import os
import sys
import tempfile

_REAL_TMP = os.path.realpath(tempfile.gettempdir())
tempfile.tempdir = _REAL_TMP
os.environ["TMPDIR"] = _REAL_TMP

collect_ignore = []
if not sys.platform.startswith("linux"):
    collect_ignore += ["archive", "zoteroattachments"]

# Test directory -> (command, test-only modules it also imports at collection time).
_EXTRA_SUITES = {
    "ankimerge": ("anki-merge", ("hypothesis",)),
    "relink": ("relink", ()),
    "browserbookmarks": ("browser-bookmarks", ("hypothesis",)),
    "zoterodupes": ("zotero-dupes", ()),
    "bibtexkeys": ("bibtex-keys", ()),
}


def _importable(name):
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _extra_suites_to_skip():
    from notewrench.cli import BY_NAME

    for directory, (command, test_only) in _EXTRA_SUITES.items():
        if not all(_importable(name) for name in (*BY_NAME[command].requires, *test_only)):
            yield directory


collect_ignore += list(_extra_suites_to_skip())
