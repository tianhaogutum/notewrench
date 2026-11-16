# SPDX-License-Identifier: AGPL-3.0-only
"""Port of test/tabs.test.js."""
from __future__ import annotations

import pytest

from notewrench.citekeys import rekey_markdown

MAPPING = {'A': 'B', 'B': 'Longer', 'Same': 'Same'}
VALUES = [
    (' \t[@A; -@B; @A; @Same]\t ', ' \t[@B; -@Longer; @B; @Same]\t '),
    (' \t@A, -@B, @A, @Same\t ', ' \t@B, -@Longer, @B, @Same\t '),
    (' \t@*\t ', ' \t@*\t '),
]
CONTENTS = ['\t', 'Study\t[@unknown]', 'literal\ttab', 'Study \ttab', 'Study \ttab', '\t[@A]\t[@unknown]\t',
            r'\t\n\r\b\f\/\"A' + '\t', *('\\' * n + '\t[@unknown]' for n in (0, 2, 4, 6))]


@pytest.mark.parametrize('bom', ['', '﻿'], ids=['no BOM', 'BOM'])
def test_opaque_double_quoted_tabs_retain_bytes_and_json_escapes(bom):
    for newline in ['\n', '\r\n']:
        for content in CONTENTS:
            for ending in ['', newline, newline + 'Body [@A].']:
                for value, expected_value in VALUES:
                    text = newline.join([bom + '--- \t', f'title:\t"{content}" \t# [@unknown]', f"nocite: '{value}'", '... \t']) + ending
                    expected = newline.join([bom + '--- \t', f'title:\t"{content}" \t# [@unknown]', f"nocite: '{expected_value}'", '... \t']) \
                        + ending.replace('[@A]', '[@B]', 1)
                    result = rekey_markdown(text, MAPPING)
                    assert result['text'].encode() == expected.encode()
                    assert result['unresolved'] == []
                    assert result['unsupported'] == []
                    assert [edit['key'] for edit in result['edits']] == (
                        ([] if '@*' in value else ['A', 'B', 'A']) + (['A'] if 'Body' in ending else []))
