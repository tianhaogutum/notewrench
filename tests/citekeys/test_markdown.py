# SPDX-License-Identifier: AGPL-3.0-only
"""Port of test/markdown.test.js."""
from __future__ import annotations

import json
import re

import pytest

from notewrench.citekeys import RekeyError, rekey_markdown, unified_diff

from .conftest import index_of

MAPPING = [{'old': 'key', 'new': 'Renamed'}, {'old': 'key-long', 'new': 'Long'}, {'old': 'a', 'new': 'b'}, {'old': 'b', 'new': 'c'}]


def run(text):
    return rekey_markdown(text, MAPPING)


def test_exact_keys_with_prefix_overlap_hyphens_periods_colons_and_suppression():
    result = rekey_markdown('See [ @key;\t-@key-long ; @key.long; @key:part ].',
                            [*MAPPING, {'old': 'key.long', 'new': 'Period'}, {'old': 'key:part', 'new': 'Colon'}])
    assert result['text'] == 'See [ @Renamed;\t-@Long ; @Period; @Colon ].'
    assert len(result['edits']) == 4
    assert result['unsupported'] == []


def test_chains_and_swaps_replace_original_spans_simultaneously():
    assert run('[@a; @b]')['text'] == '[@b; @c]'
    assert rekey_markdown('[@a; @b]', [{'old': 'a', 'new': 'b'}, {'old': 'b', 'new': 'a'}])['text'] == '[@b; @a]'


def test_unknown_keys_preserved_and_located_unchanged_known_keys_resolved():
    result = rekey_markdown('One\n[@missing; @key]', [{'old': 'key', 'new': 'key'}])
    assert result['text'] == 'One\n[@missing; @key]'
    assert result['unresolved'] == [{'key': 'missing', 'offset': 6, 'line': 2, 'column': 3, 'reason': 'no_mapping'}]
    assert result['edits'] == []


def test_prose_email_and_bracketed_email_are_untouched():
    text = 'key reader@key [reader@key] [reader+tag@key] user@host.test [user@host.test]'
    assert run(text) == {'text': text, 'edits': [], 'unresolved': [], 'unsupported': []}


def test_backtick_tilde_fences_differing_close_lengths_info_strings_and_unclosed_fences():
    text = '[@key]\n```md\n[@key]\n``\n[@a]\n````\n[@key]\n~~~ x\n[@key]\n~~~~\n[@key]\n```\n[@key]'
    result = run(text)
    assert len(result['edits']) == 3
    edited = [0, text.index('[@key]', text.index('````')), text.index('[@key]', text.index('~~~~'))]
    expected = re.sub(r'^\[@key\]', lambda m: '[@Renamed]' if m.start() in edited else m.group(), text, flags=re.MULTILINE)
    assert result['text'] == expected


def test_fences_up_to_three_spaces_tabs_four_space_indented_code():
    text = '   ~~~\n[@key]\n   ~~~\n    [@key]\n\t[@key]\n[@key]'
    assert run(text)['text'] == text[:-6] + '[@Renamed]'


def test_inline_backtick_runs_embedded_shorter_runs_and_multiline_spans_are_protected():
    text = '`[@key]` ``one ` [@key]`` ``\n[@a]\n`` [@key]'
    result = run(text)
    assert result['text'] == '`[@key]` ``one ` [@key]`` ``\n[@a]\n`` [@Renamed]'
    assert len(result['edits']) == 1


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
@pytest.mark.parametrize('indent', ['    ', '\t'])
@pytest.mark.parametrize('ticks', ['`', '``'])
def test_multiline_code_spans_cross_indented_paragraph_continuations_without_exposing_citations(newline, indent, ticks):
    inner_ticks = '``' if ticks == '`' else '`'
    code = f'Opening {ticks}literal [@key]{newline}{indent}continuation [@missing] {inner_ticks}{newline}closing{ticks}'
    result = run(f'{code} then prose [@key].')
    assert result['text'] == f'{code} then prose [@Renamed].'
    assert len(result['edits']) == 1
    assert result['edits'][0]['offset'] == len(code) + len(' then prose [@')
    assert result['unresolved'] == []
    assert result['unsupported'] == []


def test_a_code_span_can_close_on_an_indented_continuation_while_the_whole_line_stays_protected():
    text = 'Opening `literal [@key]\n    closing` [@missing]\nProse [@key].'
    result = run(text)
    assert result['text'] == 'Opening `literal [@key]\n    closing` [@missing]\nProse [@Renamed].'
    assert len(result['edits']) == 1
    assert result['unresolved'] == []
    assert result['unsupported'] == []


@pytest.mark.parametrize('boundary', ['\n\n', '\n    \n', '\n\t\n', '\n~~~\n`\n~~~\n', '\n[ref]: https://example.org/\n'])
def test_code_span_closers_in_later_blocks_cannot_hide_prose_citations(boundary):
    text = f'Opening `literal [@key].{boundary}    continuation\nclosing` then prose [@key].'
    result = run(text)
    assert result['text'] == f'Opening `literal [@Renamed].{boundary}    continuation\nclosing` then prose [@Renamed].'
    assert len(result['edits']) == 2
    assert result['unresolved'] == []
    assert result['unsupported'] == []


def test_unmatched_backticks_are_literal_and_do_not_hide_real_citations():
    assert run('` unmatched [@key]')['text'] == '` unmatched [@Renamed]'


def test_escaped_brackets_stay_literal_double_escaped_backslash_allows_citation():
    assert run('\\[@key]')['text'] == '\\[@key]'
    assert run('\\\\[@key]')['text'] == '\\\\[@Renamed]'


UNSUPPORTED = ['[@{key}]', '[@key--long]', '[@key.]', '[@key; prose]', '[@key\n; @a]', '[nested [@key]]', '[@key; `[@a]`]',
               '[@key', '@key writes prose', '-@key writes prose']


@pytest.mark.parametrize('text', UNSUPPORTED, ids=[json.dumps(text) for text in UNSUPPORTED])
def test_unsupported_syntax_stays_unchanged(text):
    result = run(text)
    assert result['text'] == text
    assert len(result['unsupported']) > 0
    assert result['edits'] == []


@pytest.mark.parametrize('text', ['[@key](https://example.org/)', '![image [@key]](image.png)', '[@key][@a]', '[@key] [@a]',
                                  '[@key]{.class}', '[link](https://example.org/[@key])', '[link] (https://example.org/[@key])',
                                  '[link][@key]', '[link]{title="[@key]"}'])
def test_link_reference_attribute_context_protected(text):
    result = run(text)
    assert result['text'] == text
    assert len(result['unsupported']) > 0


STRUCTURES = ['---\nnocite: |\n  [@key]\n---', '\ufeff---\ncustom: ["[@key]"]\n---', '+++\nx="[@key]"\n+++', '% Title\n[@key]',
              '> [@key]\nlazy continuation', '-\t[@key]', '<div>[@key]', '$[@key]', '[^note]: [@key]']


@pytest.mark.parametrize('text', STRUCTURES, ids=[json.dumps(text) for text in STRUCTURES])
def test_unsupported_document_structure_rejected(text):
    with pytest.raises(RekeyError):
        run(text)


def test_unsupported_structures_inside_code_are_protected_not_rejected():
    text = '```\n---\n> $[@key]$\n<div>[@key]</div>\n```\n`$[@key]$`'
    assert run(text)['text'] == text


def test_crlf_bom_unicode_and_missing_final_newline_survive_key_only_edits():
    text = '\ufeff中文 🐝\r\nSee [@key].\r\n`[@key]`'
    result = run(text)
    assert result['text'] == '\ufeff中文 🐝\r\nSee [@Renamed].\r\n`[@key]`'
    assert result['edits'][0]['line'] == 2
    assert result['edits'][0]['column'] == 7
    assert result['edits'][0]['offset'] == index_of(text, 'key')


def test_nul_and_bare_cr_rejected():
    for text in ['[@key]\0', '[@key]\r']:
        with pytest.raises(RekeyError) as caught:
            run(text)
        assert caught.value.code == 'UNSUPPORTED_TEXT'


def test_unified_diff_has_merged_context_separated_hunks_and_eof_marker():
    before = '[@key]\n1\n2\n3\n4\n5\n6\n7\n8\n9\n[@a]'
    after = run(before)['text']
    diff = unified_diff(before, after, 'file.md')
    assert diff.startswith('--- "a/file.md"\n+++ "b/file.md"\n@@ -1,4 +1,4 @@\n')
    assert '@@ -8,4 +8,4 @@' in diff
    assert diff.endswith('-[@a]\n\\ No newline at end of file\n+[@b]\n\\ No newline at end of file\n')
    assert unified_diff(before, before, 'file.md') == ''


# JavaScript's plain-object case ({}) becomes a non-mapping object: a Python dict is the Map form.
@pytest.mark.parametrize('mapping', [None, object(), [{'old': 'a', 'new': 'a\n'}], [{'old': 'a', 'new': 'b'}, {'old': 'a', 'new': 'c'}], [None]])
def test_direct_api_rejects_duplicate_and_unsafe_mapping_keys(mapping):
    with pytest.raises(RekeyError) as caught:
        rekey_markdown('[@a]', mapping)
    assert caught.value.code == 'INVALID_MAPPING'


def test_locations_stay_correct_across_repeated_edits_on_different_unicode_lines():
    result = run('α [@a]\r\nβ [@b; @unknown].\n[@key]')
    assert [[edit['line'], edit['column']] for edit in result['edits']] == [[1, 5], [2, 5], [3, 3]]
    assert result['unresolved'][0]['line'] == 2
    assert result['unresolved'][0]['column'] == 9


def test_commented_metadata_delimiters_are_rejected():
    with pytest.raises(RekeyError) as caught:
        run('--- # YAML\ntitle: "[@key]"\n---')
    assert caught.value.code == 'UNSUPPORTED_FRONTMATTER'


@pytest.mark.parametrize('text', ['user-@host.test', '[user-@host.test]', 'user+tag@host.test', 'user!box@host.test', 'user/@host.test'])
def test_email_punctuation_is_protected_and_not_diagnosed_as_citations(text):
    assert run(text) == {'text': text, 'edits': [], 'unsupported': [], 'unresolved': []}


@pytest.mark.parametrize('text', ['^[@key]', '[@/key]', '[@!key]'])
def test_inline_footnote_context_and_unsupported_key_characters_are_reported_intact(text):
    result = run(text)
    assert result['text'] == text
    assert len(result['unsupported']) == 1


@pytest.mark.parametrize('text', ['\\begin{verbatim}\n[@key]\n\\end{verbatim}', '\\texttt{[@key]}', '::: note\n[@key]\n:::'])
def test_raw_tex_and_fenced_div_structures_fail_explicitly(text):
    with pytest.raises(RekeyError) as caught:
        run(text)
    assert caught.value.code == 'UNSUPPORTED_MARKDOWN'


@pytest.mark.parametrize('text', ['[link](https://example.org/ "title ) [@key]")', '[link](https://example.org/){title="[@key]"}',
                                  '[link][ref]{title="} [@key]"}', '[link]{title="} [@key]"}', '[@key]\n[ref]'])
def test_quoted_link_titles_and_trailing_attributes_do_not_expose_citation_like_text(text):
    result = run(text)
    assert result['text'] == text
    assert len(result['unsupported']) == 1


@pytest.mark.parametrize('text', ['[prefix `]` [@key]]', '[prefix `[` [@key]]', '[prefix ``x ` ]`` [@key]]'])
def test_bracket_balancing_ignores_code_delimiters_inside_bracket_regions(text):
    result = run(text)
    assert result['text'] == text
    assert len(result['unsupported']) == 1


def test_astral_offsets_count_utf16_code_units():
    """Python-port regression: offsets/columns/lengths are JavaScript UTF-16 units, bytes are UTF-8."""
    text = '🐝 [@key], [🐝 @x]'
    result = run(text)
    assert result['text'] == '🐝 [@Renamed], [🐝 @x]'
    assert result['edits'] == [{'key': 'key', 'replacement': 'Renamed', 'offset': 5, 'line': 1, 'column': 6, 'length': 3,
                                'byteOffset': 7, 'byteLength': 3}]
    assert result['unresolved'] == [{'key': 'x', 'offset': 16, 'line': 1, 'column': 17, 'reason': 'no_mapping'}]
