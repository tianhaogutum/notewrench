# SPDX-License-Identifier: AGPL-3.0-only
"""Review-only unified diffs of key edits."""
from __future__ import annotations

from ._js import stringify


def _split_lf(value: str) -> list[str]:
    """``value.match(/[^\\n]*(?:\\n|$)/g).filter(Boolean)`` (not str.splitlines)."""
    parts = value.split('\n')
    lines = [part + '\n' for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


# Key edits cannot change line counts. This avoids a quadratic general-purpose LCS.
def unified_diff(before: str, after: str, name: str, context: int = 3) -> str:
    if before == after:
        return ''
    old_lines = _split_lf(before)
    new_lines = _split_lf(after)
    if len(old_lines) != len(new_lines):
        raise ValueError('Diff requires key-only edits')
    ranges: list[dict[str, int]] = []
    for i, (old, new) in enumerate(zip(old_lines, new_lines)):
        if old == new:
            continue
        start = max(0, i - context)
        end = min(len(old_lines), i + context + 1)
        if ranges and start <= ranges[-1]['end']:
            ranges[-1]['end'] = end
        else:
            ranges.append({'start': start, 'end': end})
    # JSON quoting makes filenames with control characters unambiguous.
    out = [f'--- {stringify("a/" + name)}\n+++ {stringify("b/" + name)}\n']

    def add(prefix: str, line: str) -> str:
        return prefix + line + ('' if line.endswith('\n') else '\n\\ No newline at end of file\n')

    for item in ranges:
        start, end = item['start'], item['end']
        out.append(f'@@ -{start + 1},{end - start} +{start + 1},{end - start} @@\n')
        for i in range(start, end):
            if old_lines[i] == new_lines[i]:
                out.append(add(' ', old_lines[i]))
            else:
                out.append(add('-', old_lines[i]) + add('+', new_lines[i]))
    return ''.join(out)


unifiedDiff = unified_diff
