# SPDX-License-Identifier: AGPL-3.0-only
"""Port of test/blocks.test.js."""
from __future__ import annotations

import pytest

from notewrench.citekeys import RekeyError, rekey_markdown

from .conftest import js_slice, load_fixture


def run(text):
    return rekey_markdown(text, {'A': 'Longer'})


def test_container_and_restricted_frontmatter_integration_preserves_protected_bytes_and_original_locations():
    fixture = load_fixture('containers')
    before, expected = fixture['input'], fixture['expected']
    result = run(before)
    assert result['text'] == expected
    assert result['unresolved'] == []
    assert result['unsupported'] == []
    assert [[edit['line'], edit['column']] for edit in result['edits']] == [[2, 12], [6, 17], [7, 13], [8, 20], [16, 11], [17, 14], [25, 56]]
    for edit in result['edits']:
        assert js_slice(before, edit['offset'], edit['offset'] + edit['length']) == 'A'
        assert before.encode('utf-8')[edit['byteOffset']:edit['byteOffset'] + edit['byteLength']].decode() == 'A'


def test_nested_ordered_lists_and_quotes_keep_container_fences_and_relative_indented_code_protected():
    text = '12. Item [@A].\n    > Quote [@A].\n    > - Child [@A].\n    >\n    >       [@unknown]\n    >\n    >   ~~~\n    >   [@unknown]\n    >   ~~~\n\nEnd [@A].'
    result = run(text)
    assert result['text'] == text.replace('[@A]', '[@Longer]')
    assert len(result['edits']) == 4
    assert result['unresolved'] == []


def test_new_list_items_stop_multiline_code_matching_same_container_continuations_allow_it():
    text = '- Opening `[@unknown]\n  closing` then [@A].\n- Literal ` [@A].\n- Later ` then [@A].'
    result = run(text)
    assert result['text'] == text.replace('[@A]', '[@Longer]')
    assert len(result['edits']) == 3
    assert result['unresolved'] == []


@pytest.mark.parametrize('region', [
    '<!-- [@unknown] -->', '<script>"<x>"; [@unknown]</script>', '<https://example.org/[@unknown]>', '$[@unknown]$',
    '$$[@unknown]$$', '\\([@unknown]\\)', '\\[[@unknown]\\]', '<span title=">">[@unknown]<b>[@unknown]</b></span>',
    '<span><![CDATA[</span> [@unknown]]]></span>', '<!doctype html [@unknown]>',
])
def test_html_comments_raw_elements_autolinks_and_inline_display_math_are_opaque(region):
    result = run(f'Before {region} after [@A].')
    assert result['text'] == f'Before {region} after [@Longer].'
    assert result['unresolved'] == []
    assert result['unsupported'] == []


@pytest.mark.parametrize('text', ['[@A; $[@unknown]$]', '[@A; <span>[@unknown]</span>]'])
def test_protected_inline_contents_inside_citation_groups_cannot_become_editable_citations(text):
    result = run(text)
    assert result['text'] == text
    assert len(result['edits']) == 0
    assert len(result['unsupported']) == 1


def test_frontmatter_only_scans_the_explicitly_supported_nocite_scalar():
    for quote in ["'", '"']:
        text = f'---\nnocite: {quote}[@A]{quote}\nother: {quote}[@unknown]{quote}\n---\nBody [@A].'
        result = run(text)
        assert result['text'] == text.replace('[@A]', '[@Longer]', 1).replace('Body [@A]', 'Body [@Longer]', 1)
        assert result['unresolved'] == []
    assert run("---\nnocite: '@*'\n---\n[@A]")['text'] == "---\nnocite: '@*'\n---\n[@Longer]"


@pytest.mark.parametrize('text', [
    '---\nnocite: |\n  [@A]\n---', '---\ncustom:\n  child: "[@A]"\n---',
    '---\nnocite: "[@A]"\nnocite: "[@A]"\n---', '---\nnocite: "[@A]"',
    '---\nnocite: &anchor "[@A]"\n---', '---\nnocite: "@A,"\n---',
    '---\ncustom: ["[@A]"]\n---', '-\t[@A]', '> $[@A]', '$[@A]',
    '> Opening `[@A]\nclosing` [@A]', '- Opening `[@A]\nclosing` [@A]',
])
def test_unsafe_metadata_ambiguous_container_indentation_and_unclosed_math_fail_before_edits(text):
    with pytest.raises(RekeyError):
        run(text)


@pytest.mark.parametrize('text', [
    '> Opening `[@unknown]\n> closing` then [@A].',
    '- Opening `[@unknown]\n  \tclosing` [@unknown]\n  Prose [@A].',
    '-     [@unknown]\n\nEnd [@A].',
    '<div>\n---\n- $[@unknown]\n</div>\n\nEnd [@A].',
    '<hr>\n[@unknown]\n\nEnd [@A].',
    '> <div>[@unknown]</div>\n> [@unknown]\n>\n> End [@A].',
    '> $$\n> \\command{[@unknown]}\n> $$\n\nEnd [@A].',
])
def test_quote_and_list_multiline_code_extra_list_padding_and_html_block_contents_stay_opaque(text):
    result = run(text)
    assert result['text'] == text.replace('[@A]', '[@Longer]', 1)
    assert result['unresolved'] == []
    assert result['unsupported'] == []


@pytest.mark.parametrize('text', ['- \t[@A]', '-     \t[@A]', '1. \t[@A]'])
def test_tabs_in_list_markers_fail(text):
    with pytest.raises(RekeyError) as caught:
        run(text)
    assert caught.value.code == 'UNSUPPORTED_MARKDOWN'


@pytest.mark.parametrize('prefix,indent', [('- ', '  '), ('12. ', '    '), ('> - ', '>   ')])
def test_unindented_blank_lines_do_not_terminate_list_contained_code_fences(prefix, indent):
    blank = '>' if prefix.startswith('>') else ''
    text = f'{prefix}```\n{indent}[@A; @unknown]\n{blank}\n{indent}[@A; @unknown]\n{indent}```\n\nEnd [@A].'
    result = run(text)
    assert result['text'] == text.replace('End [@A]', 'End [@Longer]', 1)
    assert len(result['edits']) == 1
    assert result['unresolved'] == []
