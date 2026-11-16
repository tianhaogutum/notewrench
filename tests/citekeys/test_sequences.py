# SPDX-License-Identifier: AGPL-3.0-only
"""Port of test/sequences.test.js."""
from __future__ import annotations

import pytest

from notewrench.citekeys import RekeyError, rekey_markdown

from .conftest import assert_edit_location, js_slice, load_fixture, unknown_location

FIXTURE = load_fixture('frontmatter-sequences')
MAPPING = {'A': 'B', 'B': 'Longer', 'Same': 'Same'}


def test_nocite_block_sequence_exact_requested_simultaneous_output():
    example = FIXTURE['examples'][0]
    result = rekey_markdown(example['input'], MAPPING)
    assert result['text'] == example['expected']
    assert [[edit['key'], edit['replacement']] for edit in result['edits']] == [['A', 'B'], ['B', 'Longer'], ['A', 'B'], ['A', 'B']]


@pytest.mark.parametrize('indent', [' ', '  ', '    ', '        '])
@pytest.mark.parametrize('gap', [' ', '   '])
def test_nocite_block_sequence_consistent_space_indentation_marker_spacing_quotes_and_next_field_boundaries(indent, gap):
    for suffix in ['', ' \t', '# @unknown', ' \t# @unknown']:
        for quote in ["'", '"']:
            text = f'---\nnocite:\n{indent}-{gap}{quote}@a.b{quote}{suffix}\n{indent}-{gap}{quote}-@x:y_2{quote}\ntitle: "[@unknown]"\n---'
            result = rekey_markdown(text, {'a.b': 'Dot', 'x:y_2': 'Colon'})
            assert result['text'] == text.replace('@a.b', '@Dot', 1).replace('@x:y_2', '@Colon', 1)
            assert result['unresolved'] == []
            assert result['unsupported'] == []


def test_nocite_block_sequence_rich_bytes_untouched_segments_original_edits_and_unknown_locations():
    for example in FIXTURE['examples']:
        text = example['input']
        result = rekey_markdown(text, MAPPING)
        assert result['text'].encode() == example['expected'].encode()
        assert len(result['edits']) == example['edits']
        assert result['unsupported'] == []
        cursor = 0
        output = ''
        for edit in result['edits']:
            assert_edit_location(text, edit)
            output += js_slice(text, cursor, edit['offset']) + edit['replacement']
            cursor = edit['offset'] + edit['length']
        assert output + js_slice(text, cursor) == example['expected']
        assert result['unresolved'] == ([unknown_location(text)] if example['exitCode'] else [])


def test_nocite_block_sequence_reject_complete_malformed_values_and_retain_other_metadata_refusal():
    for invalid in FIXTURE['invalid']:
        with pytest.raises(RekeyError) as caught:
            rekey_markdown(invalid['input'], MAPPING)
        assert caught.value.code == invalid['code'], invalid['input']


def test_nocite_block_sequence_blank_and_whole_line_comments_never_reindent_or_end_the_block():
    text = "---\nnocite:\n  # c\n  - '@A'\n\n# d\n\t# e\n  - \"-@B\"   # opaque\n\ntitle: \"[@unknown]\"\n---\nBody [@A]."
    expected = "---\nnocite:\n  # c\n  - '@B'\n\n# d\n\t# e\n  - \"-@Longer\"   # opaque\n\ntitle: \"[@unknown]\"\n---\nBody [@B]."
    result = rekey_markdown(text, MAPPING)
    assert result['text'] == expected
    assert [edit['key'] for edit in result['edits']] == ['A', 'B', 'A']
    assert result['unresolved'] == []
    assert result['unsupported'] == []
