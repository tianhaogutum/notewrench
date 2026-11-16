# SPDX-License-Identifier: AGPL-3.0-only
"""Port of test/openers.test.js."""
from __future__ import annotations

import pytest

from notewrench.citekeys import rekey_markdown

MAPPING = {'A': 'B', 'B': 'Longer', 'Same': 'Same'}
VALUES = [
    (' \t[@A; -@B; @A; @Same]\t ', ' \t[@B; -@Longer; @B; @Same]\t '),
    (' \t@A, -@B, @A, @Same\t ', ' \t@B, -@Longer, @B, @Same\t '),
    (' \t@*\t ', ' \t@*\t '),
]


@pytest.mark.parametrize('bom', ['', '﻿'], ids=['no BOM', 'one BOM'])
def test_initial_frontmatter_opener_ascii_padding(bom):
    for newline in ['\n', '\r\n']:
        for padding in ['', ' ', '\t', ' \t']:
            for closer in ['---', '...']:
                for ending in ['', newline, newline + 'Body [@A].']:
                    for quote in ["'", '"']:
                        for value, expected_value in VALUES:
                            text = newline.join([bom + '---' + padding, f'nocite:\t{quote}{value}{quote} # opaque', closer + ' \t']) + ending
                            result = rekey_markdown(text, MAPPING)
                            assert result['text'] == text.replace(value, expected_value, 1).replace('Body [@A]', 'Body [@B]', 1)
                            assert result['unresolved'] == []
                            assert result['unsupported'] == []
                            assert [edit['key'] for edit in result['edits']] == (
                                ([] if '@*' in value else ['A', 'B', 'A']) + (['A'] if 'Body' in ending else []))
