"""``notewrench relink`` suite: needs the optional ``relink`` extra.

The ``test`` extra (``pip install -e '.[test]'``) includes the relink dependencies;
without them this suite is not collected instead of failing on import.
"""
import importlib.util

REQUIRES = ("bs4", "idna", "markdown_it", "mdit_py_plugins", "warcio")

collect_ignore_glob = [] if all(importlib.util.find_spec(name) for name in REQUIRES) else ["test_*.py"]
