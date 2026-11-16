# SPDX-License-Identifier: AGPL-3.0-only
"""Port of test/flows.test.js."""
from __future__ import annotations

import pytest

from notewrench.citekeys import RekeyError, rekey_markdown

from .conftest import assert_edit_location, js_slice, load_fixture, unknown_location

FIXTURE = load_fixture('frontmatter-flows')
MAPPING = {'A': 'B', 'B': 'Longer', 'Same': 'Same'}


def test_nocite_flow_sequence_exact_requested_bytes_and_original_simultaneous_spans():
    example = FIXTURE['examples'][0]
    result = rekey_markdown(example['input'], MAPPING)
    assert result['text'] == example['expected']
    assert [[edit['key'], edit['replacement']] for edit in result['edits']] == [['A', 'B'], ['B', 'Longer'], ['A', 'B'], ['A', 'B']]


@pytest.mark.parametrize('quote', ["'", '"'])
@pytest.mark.parametrize('ws', ['', ' ', '\t', ' \t '])
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_nocite_flow_sequence_mixed_quotes_ascii_separators_single_repeated_suppressed_and_complete_keys(quote, ws, newline):
    for items, expected_items in [
        ([quote + '@A' + quote], [quote + '@B' + quote]),
        (["'@A'", '"-@B"', "'@A'", '"@Same"'], ["'@B'", '"-@Longer"', "'@B'", '"@Same"']),
        (["'@a.b'", '"-@x:y_2"'], ["'@Dot'", '"-@Colon"']),
    ]:
        value = '[' + ws + (ws + ',' + ws).join(items) + ws + ']'
        expected_value = '[' + ws + (ws + ',' + ws).join(expected_items) + ws + ']'
        for suffix in dict.fromkeys(['', ws, ws + '# @A [@unknown]']):
            text = newline.join(['---', 'nocite: ' + value + suffix, '---', 'Body [@A].'])
            result = rekey_markdown(text, {**MAPPING, 'a.b': 'Dot', 'x:y_2': 'Colon'})
            assert result['text'] == text.replace(value, expected_value, 1).replace('Body [@A]', 'Body [@B]', 1)
            assert result['unresolved'] == []
            assert result['unsupported'] == []


def test_nocite_flow_sequence_rich_bytes_untouched_segments_original_edits_and_unknown_locations():
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


def test_nocite_flow_sequence_reject_complete_malformed_values_and_retain_other_metadata_refusal():
    for invalid in FIXTURE['invalid']:
        with pytest.raises(RekeyError) as caught:
            rekey_markdown(invalid['input'], MAPPING)
        assert caught.value.code == invalid['code'], invalid['input']
