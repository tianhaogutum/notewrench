# SPDX-License-Identifier: AGPL-3.0-only
"""``notewrench citekeys``: migrate Pandoc citation keys in Markdown between two CSL JSON exports.

Python port of the ``citekey-rekey`` Node.js tool. Items are matched by DOI, then
exact URL; only supported bracket citation key spans are rewritten, and output
copies go to a new directory. The pure API performs no filesystem I/O::

    from notewrench.citekeys import build_mapping, rekey_markdown
    plan = build_mapping([{'id': 'a', 'DOI': '10.123/demo'}], [{'id': 'b', 'DOI': '10.123/DEMO'}])
    rekey_markdown('See [@a].', plan['mapping'])['text']  # 'See [@b].'

Report offsets, lengths and columns count UTF-16 code units (as in the original).
"""
from .cli import main
from .diff import unified_diff
from .identity import KEY_SOURCE, RekeyError, build_mapping, normalize_doi
from .markdown import rekey_markdown

# camelCase names of the original JavaScript API.
buildMapping = build_mapping
normalizeDOI = normalize_doi
rekeyMarkdown = rekey_markdown
unifiedDiff = unified_diff

__all__ = ['KEY_SOURCE', 'RekeyError', 'build_mapping', 'main', 'normalize_doi', 'rekey_markdown', 'unified_diff',
           'buildMapping', 'normalizeDOI', 'rekeyMarkdown', 'unifiedDiff']
