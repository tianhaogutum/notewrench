# SPDX-License-Identifier: AGPL-3.0-only
"""Port of test/citations.test.js."""
from __future__ import annotations

import json

import pytest

from notewrench.citekeys import RekeyError, build_mapping, rekey_markdown

from .conftest import byte_length, index_of, js_slice, length

_PLAN = build_mapping([{'id': 'A', 'DOI': '10.123/a'}, {'id': 'B', 'DOI': '10.123/b'}],
                      [{'id': 'B', 'DOI': '10.123/a'}, {'id': 'Longer', 'DOI': '10.123/b'}])
assert _PLAN['conflicts'] == []
MAPPING = _PLAN['mapping']


def run(text):
    return rekey_markdown(text, MAPPING)


def test_same_line_citation_affixes_exact_requested_contract_and_simultaneous_key_spans():
    result = run('[see @A, pp. 4–6; -@B, chap. 2]')
    assert result['text'] == '[see @B, pp. 4–6; -@Longer, chap. 2]'
    assert [{k: edit[k] for k in ('key', 'replacement', 'offset', 'length')} for edit in result['edits']] == [
        {'key': 'A', 'replacement': 'B', 'offset': 6, 'length': 1},
        {'key': 'B', 'replacement': 'Longer', 'offset': 20, 'length': 1},
    ]
    assert result['unresolved'] == []
    assert result['unsupported'] == []


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_same_line_citation_affixes_preserve_unicode_punctuation_escapes_and_whitespace_verbatim(newline):
    affixed = r'[参见 \@literal \{note\}\; @A, pp\. 4–6\; also \\; cf. -@B suffix\! \* \]]'
    text = f'﻿中文 🐝{newline}> {affixed}'
    result = run(text)
    assert result['text'] == f'﻿中文 🐝{newline}> ' + r'[参见 \@literal \{note\}\; @B, pp\. 4–6\; also \\; cf. -@Longer suffix\! \* \]]'
    assert result['unsupported'] == []
    assert result['unresolved'] == []
    for edit in result['edits']:
        assert js_slice(text, edit['offset'], edit['offset'] + edit['length']) == edit['key']
        assert edit['byteOffset'] == byte_length(js_slice(text, 0, edit['offset']))
        assert text.encode()[edit['byteOffset']:edit['byteOffset'] + edit['byteLength']].decode() == edit['key']
        assert edit['line'] == 2
        assert edit['column'] == edit['offset'] - index_of(text, '\n')


def test_same_line_citation_affixes_unknown_keys_remain_located_identical_confirmed_keys_resolve():
    text = '[see @A, p. 1; compare -@unknown, appendix]'
    result = rekey_markdown(text, [*MAPPING, {'old': 'Same', 'new': 'Same'}])
    assert result['text'] == '[see @B, p. 1; compare -@unknown, appendix]'
    assert result['unresolved'] == [{'key': 'unknown', 'offset': text.index('unknown'), 'line': 1,
                                     'column': text.index('unknown') + 1, 'reason': 'no_mapping'}]
    assert result['unsupported'] == []
    assert rekey_markdown('[see @Same, p. 1]', [{'old': 'Same', 'new': 'Same'}]) == {
        'text': '[see @Same, p. 1]', 'edits': [], 'unresolved': [], 'unsupported': []}


@pytest.mark.parametrize('text,expected', [
    ('[@A, p. 4]', '[@B, p. 4]'),
    ('[see @A]', '[see @B]'),
    ('[\tcf.\t-@A\t, p. 1\t;\t@B ]', '[\tcf.\t-@B\t, p. 1\t;\t@Longer ]'),
    (r'[@A, literal \@unknown]', r'[@B, literal \@unknown]'),
])
def test_same_line_citation_affixes_simple_forms_tabs_and_escaped_literal_markers(text, expected):
    result = run(text)
    assert result['text'] == expected
    assert result['unsupported'] == []
    assert result['unresolved'] == []


# Validate the whole group before proposing any edits or unknown-key diagnostics.
AMBIGUOUS = [
    '[@A @B]', '[@A, @B]', '[see-@A; @B]', '[@A; prose]', '[@A;; @B]',
    '[@A; @unknown @B]', '[@A; @B--part]', '[@A; @B.]', '[@A; @B/part]',
    '[@A; @Bé]', '[@A; @{B}]', '[@A; [@B]]', '[@A; `@B`]',
    '[@A; $@B$]', r'[\[@A\]]', '[@A; <span>@B</span>]', '[*see* @A]',
    r'[see \@A]', r'[see \word @A]',
    '[@A\n; @B]', '[@A\r\n; @B]', '[@A; @B',
    '[see @A](https://example.org/)', '![see @A]', '^[@A, p. 1]',
    '[see @A] [@B]', '[@A, p. 1]{.citation}',
]


@pytest.mark.parametrize('group', AMBIGUOUS, ids=[json.dumps(group) for group in AMBIGUOUS])
def test_same_line_citation_ambiguity_stays_whole_with_original_range(group):
    prefix = '中文 🐝: '
    text = prefix + group
    result = run(text)
    assert result['text'] == text
    assert result['edits'] == []
    assert result['unresolved'] == []
    assert len(result['unsupported']) == 1
    diagnostic = result['unsupported'][0]
    assert diagnostic['offset'] == length(prefix)
    assert diagnostic['length'] == length(group)
    assert js_slice(text, diagnostic['offset'], diagnostic['offset'] + diagnostic['length']) == group
    assert diagnostic['line'] == 1
    assert diagnostic['column'] == length(prefix) + 1


def test_same_line_citation_affixes_do_not_expose_protected_code_math_html_or_metadata():
    text = '---\ntitle: "[see @A, p. 1]"\n---\n`[see @A, p. 1]` $[see @A, p. 1]$ <span>[see @A, p. 1]</span>. Then [see @A, p. 1].'
    result = run(text)
    assert result['text'] == text[:-len('Then [see @A, p. 1].')] + 'Then [see @B, p. 1].'
    assert len(result['edits']) == 1
    assert result['unsupported'] == []
    assert result['unresolved'] == []
    # Frontmatter retains its explicitly smaller bare-key-only contract.
    with pytest.raises(RekeyError) as caught:
        run("---\nnocite: '[see @A, p. 1]'\n---")
    assert caught.value.code == 'UNSUPPORTED_FRONTMATTER'
