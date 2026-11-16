# SPDX-License-Identifier: AGPL-3.0-only
"""Port of test/closers.test.js (also covers the openers/tabs fixtures)."""
from __future__ import annotations

import pytest

from notewrench.citekeys import RekeyError, rekey_markdown

from .conftest import assert_edit_location, js_slice, length, load_fixture, unknown_location

MAPPING = {'A': 'B', 'B': 'Longer', 'Same': 'Same'}
VALUES = [
    (' \t[@A; -@B; @A; @Same]\t ', ' \t[@B; -@Longer; @B; @Same]\t '),
    (' \t@A, -@B, @A, @Same\t ', ' \t@B, -@Longer, @B, @Same\t '),
    (' \t@*\t ', ' \t@*\t '),
]


@pytest.mark.parametrize('closer', ['---', '...'])
def test_initial_frontmatter_closer_ascii_padding_on_lf_crlf_and_at_eof(closer):
    for newline in ['\n', '\r\n']:
        for padding in ['', ' ', '\t', ' \t ']:
            for ending in ['', newline, newline + 'Body [@A].']:
                for quote in ["'", '"']:
                    for value, expected_value in VALUES:
                        text = newline.join(['---', f'nocite:\t{quote}{value}{quote} # opaque', closer + padding]) + ending
                        result = rekey_markdown(text, MAPPING)
                        assert result['text'] == text.replace(value, expected_value, 1).replace('Body [@A]', 'Body [@B]', 1)
                        assert result['unresolved'] == []
                        assert result['unsupported'] == []
                        assert [edit['key'] for edit in result['edits']] == (
                            ([] if '@*' in value else ['A', 'B', 'A']) + (['A'] if 'Body' in ending else []))


@pytest.mark.parametrize('kind', ['closers', 'openers', 'tabs'])
def test_initial_frontmatter_retain_rich_bytes_and_original_edit_review_locations(kind):
    for example in load_fixture(f'frontmatter-{kind}')['examples']:
        text = example['input']
        result = rekey_markdown(text, MAPPING)
        assert result['text'].encode() == example['expected'].encode()
        assert len(result['edits']) == example['edits']
        assert result['unsupported'] == []
        assert result['unresolved'] == ([unknown_location(text)] if example['exitCode'] else [])
        original_cursor = 0
        output_cursor = 0
        for edit in result['edits']:
            assert_edit_location(text, edit)
            unchanged = js_slice(text, original_cursor, edit['offset'])
            assert js_slice(result['text'], output_cursor, output_cursor + length(unchanged)) == unchanged
            output_cursor += length(unchanged) + length(edit['replacement'])
            original_cursor = edit['offset'] + edit['length']
        assert js_slice(result['text'], output_cursor) == js_slice(text, original_cursor)


@pytest.mark.parametrize('kind', ['closers', 'openers', 'tabs'])
def test_initial_frontmatter_reject_malformed_delimiters_and_retain_metadata_preflight(kind):
    for invalid in load_fixture(f'frontmatter-{kind}')['invalid']:
        with pytest.raises(RekeyError) as caught:
            rekey_markdown(invalid['input'], MAPPING)
        assert caught.value.code == invalid['code'], invalid['input']
