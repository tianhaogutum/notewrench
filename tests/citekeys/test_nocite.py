# SPDX-License-Identifier: AGPL-3.0-only
"""Port of test/nocite.test.js."""
from __future__ import annotations

import json

import pytest

from notewrench.citekeys import RekeyError, rekey_markdown

from .conftest import assert_edit_location, index_of, js_slice, last_index_of, load_fixture, unknown_location

MAPPING = {'A': 'B', 'B': 'Longer', 'Same': 'Same'}
EXTENDED = {**MAPPING, 'a.b': 'Dot', 'x:y_2': 'Colon'}
FIXTURE = load_fixture('nocite')


def run(text):
    return rekey_markdown(text, MAPPING)


def refuses(text):
    with pytest.raises(RekeyError) as caught:
        run(text)
    assert caught.value.code == 'UNSUPPORTED_FRONTMATTER', text


def unknown_at(text, line=2, column=16):
    return {'key': 'unknown', 'offset': index_of(text, 'unknown'), 'line': line, 'column': column, 'reason': 'no_mapping'}


def test_quoted_nocite_comma_list_exact_requested_contract_uses_original_simultaneous_spans():
    text = '---\nnocite: \'@A, @B\'\ntitle: "Study [@A]"\n---\nBody [@A].'
    result = run(text)
    assert result['text'] == '---\nnocite: \'@B, @Longer\'\ntitle: "Study [@A]"\n---\nBody [@B].'
    assert [{k: edit[k] for k in ('key', 'replacement', 'offset', 'line', 'column')} for edit in result['edits']] == [
        {'key': 'A', 'replacement': 'B', 'offset': 14, 'line': 2, 'column': 11},
        {'key': 'B', 'replacement': 'Longer', 'offset': 18, 'line': 2, 'column': 15},
        {'key': 'A', 'replacement': 'B', 'offset': text.rindex('A'), 'line': 5, 'column': 8},
    ]
    assert result['unresolved'] == []
    assert result['unsupported'] == []


@pytest.mark.parametrize('quote', ["'", '"'])
def test_quoted_nocite_comma_list_preserve_quotes_whitespace_repeats_comments_and_key_grammar(quote):
    for content, expected in [('@A', '@B'), (' \t@A\t,\t-@B , @A \t', ' \t@B\t,\t-@Longer , @B \t'),
                              ('@Same, @A', '@Same, @B'), ('@a.b, -@x:y_2', '@Dot, -@Colon')]:
        text = f'---\nnocite: {quote}{content}{quote} # [@unknown] @*\n---'
        result = rekey_markdown(text, EXTENDED)
        assert result['text'] == f'---\nnocite: {quote}{expected}{quote} # [@unknown] @*\n---'
        assert result['unresolved'] == []
        assert result['unsupported'] == []


def test_quoted_nocite_comma_list_bom_crlf_opaque_metadata_and_unknown_keys_retain_exact_original_ranges():
    for text, expected, unknown in [(FIXTURE['input'], FIXTURE['expected'], False),
                                    (FIXTURE['reviewInput'], FIXTURE['reviewExpected'], True)]:
        result = run(text)
        assert result['text'] == expected
        assert result['unsupported'] == []
        assert len(result['edits']) == (2 if unknown else 4)
        for edit in result['edits']:
            assert_edit_location(text, edit)
        assert result['unresolved'] == ([unknown_at(text)] if unknown else [])


@pytest.mark.parametrize('quote', ["'", '"'])
def test_quoted_nocite_standalone_wildcard_stays_opaque_while_body_citations_migrate(quote):
    text = f'---\nnocite: {quote}@*{quote} # @A\nother: {quote}@A, @unknown{quote}\n---\nBody [@A].'
    result = run(text)
    assert result['text'] == text.replace('Body [@A]', 'Body [@B]', 1)
    assert len(result['edits']) == 1
    assert result['unresolved'] == []
    assert result['unsupported'] == []


INVALID = [
    '', '@A,', ', @A', '@A,, @B', '@A @B', '@A; @B', '@A, prose',
    '@A, @B--part', '@A, @B.', '@A, @B/part', '@A, @Bé', '@A, @{B}',
    '@A, @*', '@*, @A', '@*, @*', '-@*', '[@A, @B]', '[@A; @*]',
    '[see @A]', '@A, - @B', '@A, @@B', '@A, [@B]', '@A, # @B',
    r'\@A, @B', r'@A, \@B', r'@A, \u0042',
    "@A, ''@B''", '@A\n, @B',
]


@pytest.mark.parametrize('value', INVALID, ids=[json.dumps(value) for value in INVALID])
def test_quoted_nocite_rejects_the_whole_malformed_scalar(value):
    for quote in ["'", '"']:
        refuses(f'---\nnocite: {quote}{value}{quote}\n---\nBody [@A].')


@pytest.mark.parametrize('fields', [
    'nocite: @A, @B', 'nocite: "@A, @B"\ncustom:\n  child: value',
    'nocite: "@A, @B"\nnocite: "@A"', 'nocite: &alias "@A, @B"',
    'nocite: "@A, @B" trailing', r'nocite: "@A, \u0040B"',
    "nocite: '@A, @B'\ncustom: [value]",
])
def test_quoted_nocite_does_not_enable_other_collections_escapes_duplicates_or_nested_metadata(fields):
    refuses(f'---\n{fields}\n---\nBody [@A].')


@pytest.mark.parametrize('quote', ["'", '"'])
def test_existing_quoted_bracket_nocite_retains_bare_key_spans_unknown_locations_and_whitespace(quote):
    text = f'---\nnocite: {quote}[ \t@A ;\t-@unknown; @B ]{quote} # @A\n---\nBody [@A].'
    result = run(text)
    assert result['text'] == f'---\nnocite: {quote}[ \t@B ;\t-@unknown; @Longer ]{quote} # @A\n---\nBody [@B].'
    assert len(result['edits']) == 3
    assert result['unsupported'] == []
    assert result['unresolved'] == [unknown_at(text, 2, 20)]
    for edit in result['edits']:
        assert js_slice(text, edit['offset'], edit['offset'] + edit['length']) == edit['key']


def test_unknown_scalar_decoding_rules_and_case_sensitive_nocite_selection_remain_unchanged():
    text = "---\nnocite: '@A, @B'\nNocite: '@A, @unknown'\ncustom: \"\\u0040unknown, [@A]\"\n---"
    result = run(text)
    assert result['text'] == text.replace("nocite: '@A, @B'", "nocite: '@B, @Longer'", 1)
    assert len(result['edits']) == 2
    assert result['unresolved'] == []
    assert result['unsupported'] == []
    refuses("---\nnocite: '@A, @B'\n" + r'custom: "\q"' + '\n---')


@pytest.mark.parametrize('quote', ["'", '"'])
def test_quoted_scalar_suffix_whitespace_tabs_before_comments_and_line_ends_preserve_original_spans(quote):
    for suffix in ['\t# keep @unknown [@A] " \' opaque', ' \t \t# @unknown', '\t', ' \t \t']:
        for content, expected, count in [('@A, @B', '@B, @Longer', 3), ('[@A; -@B]', '[@B; -@Longer]', 3), ('@*', '@*', 1)]:
            text = f'---\nnocite: {quote}{content}{quote}{suffix}\nother: {quote}[@unknown]{quote}{suffix}\n---\nBody [@A].'
            result = run(text)
            assert result['text'] == f'---\nnocite: {quote}{expected}{quote}{suffix}\nother: {quote}[@unknown]{quote}{suffix}\n---\nBody [@B].'
            assert len(result['edits']) == count
            assert result['unresolved'] == []
            assert result['unsupported'] == []


def test_quoted_scalar_suffix_whitespace_fixtures():
    for text, expected, unknown in [(FIXTURE['whitespaceInput'], FIXTURE['whitespaceExpected'], False),
                                    (FIXTURE['whitespaceReviewInput'], FIXTURE['whitespaceReviewExpected'], True)]:
        result = run(text)
        assert result['text'] == expected
        assert len(result['edits']) == (2 if unknown else 4)
        assert result['unsupported'] == []
        assert result['unresolved'] == ([unknown_at(text)] if unknown else [])
        for edit in result['edits']:
            assert_edit_location(text, edit)


@pytest.mark.parametrize('quote', ["'", '"'])
def test_quoted_scalar_suffix_whitespace_trailing_tokens_and_ambiguous_metadata_still_refuse_the_document(quote):
    for suffix in ['\ttrailing', ' \t, # comment', '\t: value', '\t' + quote + ' # extra quote', '\u00a0# not ASCII whitespace']:
        for field in ['nocite', 'other']:
            refuses(f'---\n{field}: {quote}@A, @B{quote}{suffix}\n---\nBody [@A].')
    for content in ['@A,', '@*, @A', '@A @unknown', r'@A, \@B']:
        refuses(f'---\nnocite: {quote}{content}{quote}\t# comment\n---')
    refuses(f'---\nnocite: {quote}@A, @B{quote}\t# comment\nother:\n  child: value\n---')


@pytest.mark.parametrize('field', ['other: "unterminated\t# comment', r'other: "\q"' + '\t# comment', "other: 'unterminated\t# comment"])
def test_quoted_scalar_suffix_malformed_other_fields_refuse(field):
    refuses(f"---\nnocite: '@A, @B'\t# comment\n{field}\n---")


@pytest.mark.parametrize('separator', ['\t', ' \t', '\t ', ' \t \t'])
def test_field_colon_separators_tabs_and_mixed_spaces_preserve_quoted_scalars_and_original_spans(separator):
    for quote in ["'", '"']:
        for suffix in ['\t# @unknown [@A]', ' \t']:
            for value, expected, count in [('@A, @B', '@B, @Longer', 3), ('[@A; -@B]', '[@B; -@Longer]', 3), ('@*', '@*', 1)]:
                text = (f'---\nnocite:{separator}{quote}{value}{quote}{suffix}\ncustom:{separator}{quote}[@unknown]{quote}{suffix}'
                        f'\nplain:{separator}opaque @unknown\n---\nBody [@A].')
                result = run(text)
                assert result['text'] == (f'---\nnocite:{separator}{quote}{expected}{quote}{suffix}\ncustom:{separator}{quote}[@unknown]'
                                          f'{quote}{suffix}\nplain:{separator}opaque @unknown\n---\nBody [@B].')
                assert len(result['edits']) == count
                assert result['unresolved'] == []
                assert result['unsupported'] == []
                for edit in result['edits']:
                    assert js_slice(text, edit['offset'], edit['offset'] + edit['length']) == edit['key']


def test_field_colon_separator_fixtures():
    for text, expected, unknown in [(FIXTURE['separatorInput'], FIXTURE['separatorExpected'], False),
                                    (FIXTURE['separatorReviewInput'], FIXTURE['separatorReviewExpected'], True)]:
        result = run(text)
        assert result['text'] == expected
        assert len(result['edits']) == (2 if unknown else 4)
        assert result['unsupported'] == []
        assert result['unresolved'] == ([unknown_at(text)] if unknown else [])
        for edit in result['edits']:
            assert_edit_location(text, edit)


@pytest.mark.parametrize('fields', FIXTURE['separatorInvalidFields'])
def test_field_colon_separators_empty_and_structural_values_cannot_hide_behind_whitespace(fields):
    refuses(f'---\n{fields}\n---\nBody [@A].')


def test_plain_scalar_comments_exact_requested_contract_keeps_unknown_metadata_opaque():
    text = "---\ntitle:\tStudy # keep [@unknown] opaque\nnocite:\t'@A, @B'\n---\nBody [@A]."
    result = run(text)
    assert result['text'] == "---\ntitle:\tStudy # keep [@unknown] opaque\nnocite:\t'@B, @Longer'\n---\nBody [@B]."
    assert len(result['edits']) == 3
    assert result['unresolved'] == []
    assert result['unsupported'] == []


@pytest.mark.parametrize('separator', [' ', '\t', ' \t', '\t '])
def test_plain_scalar_comments_ascii_boundaries_preserve_bytes_and_original_nocite_spans(separator):
    for quote in ["'", '"']:
        for value in ['Study', 'Study#hash', 'Study#']:
            text = f'---\ntitle:{separator}{value}{separator}# [@unknown] {{key: " \' \\q\nnocite:{separator}{quote}@A, @B{quote}\n---\nBody [@A].'
            result = run(text)
            assert result['text'] == text.replace(f'{quote}@A, @B{quote}', f'{quote}@B, @Longer{quote}', 1).replace('Body [@A]', 'Body [@B]', 1)
            assert len(result['edits']) == 3
            assert result['unresolved'] == []
            assert result['unsupported'] == []


def test_plain_scalar_comment_fixtures():
    for text, expected, unknown in [(FIXTURE['commentInput'], FIXTURE['commentExpected'], False),
                                    (FIXTURE['commentReviewInput'], FIXTURE['commentReviewExpected'], True)]:
        result = run(text)
        assert result['text'] == expected
        assert len(result['edits']) == (2 if unknown else 4)
        assert result['unsupported'] == []
        assert result['unresolved'] == ([unknown_at(text)] if unknown else [])
        for edit in result['edits']:
            assert_edit_location(text, edit)


@pytest.mark.parametrize('field', ['custom: "Study # [@unknown]"', "custom: 'Study # [@unknown]'", r'custom: "Study # \" [@unknown]"',
                                   "custom: 'Study # It''s [@unknown]'", 'custom: "Study # literal\ttab"'])
def test_plain_scalar_comments_hashes_in_quoted_scalars_retain_their_existing_grammar(field):
    text = f"---\n{field}\nnocite: '@A, @B'\n---"
    result = run(text)
    assert result['text'] == text.replace("nocite: '@A, @B'", "nocite: '@B, @Longer'", 1)
    assert result['unresolved'] == []
    assert result['unsupported'] == []


def test_plain_scalar_comments_invalid_json_escape_in_quoted_scalar_refuses():
    refuses('---\n' + r'custom: "Study # \q"' + '\n---')


@pytest.mark.parametrize('fields', FIXTURE['commentInvalidFields'])
def test_plain_scalar_comments_structural_and_malformed_values_still_refuse_the_document(fields):
    refuses(f'---\n{fields}\n---\nBody [@A].')


@pytest.mark.parametrize('separator', ['\u2028', '\u2029'])
@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_unicode_metadata_comments_exact_contract_keeps_separators_opaque_on_physical_lines(separator, newline):
    for field in [f'# note{separator}[@unknown] {{key: " \' \\q', f'title:\tStudy # note{separator}[@unknown] {{key: " \' \\q',
                  f'title:\t\'Study\' # note{separator}[@unknown] {{key: " \' \\q', f'title:\t"Study" # note{separator}[@unknown] {{key: " \' \\q']:
        text = newline.join(['---', field, "nocite:\t'@A, @B'", '---', 'Body [@A].'])
        expected = text.replace("'@A, @B'", "'@B, @Longer'", 1).replace('Body [@A]', 'Body [@B]', 1)
        result = run(text)
        assert result['text'] == expected
        assert len(result['edits']) == 3
        assert result['unresolved'] == []
        assert result['unsupported'] == []


def _rich(name_input, name_expected, count, unknown_count, line=3):
    text = FIXTURE[name_input]
    result = run(text)
    assert result['text'].encode() == FIXTURE[name_expected].encode()
    assert len(result['edits']) == count
    assert result['unsupported'] == []
    if count == unknown_count:
        offset = index_of(text, '-@unknown') + 2
        assert result['unresolved'] == [{'key': 'unknown', 'offset': offset, 'line': line, 'column': 16, 'reason': 'no_mapping'}]
    else:
        assert result['unresolved'] == []
    for edit in result['edits']:
        assert_edit_location(text, edit)


def test_unicode_metadata_comments_bom_crlf_bytes_and_original_spans_stay_exact():
    _rich('unicodeCommentInput', 'unicodeCommentExpected', 4, 2)
    _rich('unicodeCommentReviewInput', 'unicodeCommentReviewExpected', 2, 2)


@pytest.mark.parametrize('fields', FIXTURE['unicodeCommentInvalidFields'])
def test_unicode_metadata_comments_malformed_scalars_and_structural_metadata_still_fail_closed(fields):
    refuses(f'---\n{fields}\n---\nBody [@A].')


@pytest.mark.parametrize('separator', ['\u2028', '\u2029'])
@pytest.mark.parametrize('quote', ["'", '"'])
def test_unicode_quoted_metadata_literal_separator_stays_opaque(separator, quote):
    for newline in ['\n', '\r\n']:
        for value, migrated, count in [('@A, @B', '@B, @Longer', 3), ('[@A; -@B]', '[@B; -@Longer]', 3), ('@*', '@*', 1)]:
            text = newline.join(['---', f'title:\t{quote}Study{separator}[@unknown]{quote} # opaque', f'nocite:\t{quote}{value}{quote}', '---', 'Body [@A].'])
            expected = text.replace(f'{quote}{value}{quote}', f'{quote}{migrated}{quote}', 1).replace('Body [@A]', 'Body [@B]', 1)
            result = run(text)
            assert result['text'] == expected
            assert len(result['edits']) == count
            assert result['unresolved'] == []
            assert result['unsupported'] == []
    # Separator code points also remain literal at the scalar edges. Retain
    # doubled-single-quote and JSON-compatible escaped-quote/backslash rules.
    content = f"{separator}It''s [@unknown]{separator}" if quote == "'" else f'{separator}Study \\" [@unknown] \\\\{separator}'
    text = f"---\ntitle: {quote}{content}{quote}\nnocite: '@A, @B'\n---"
    result = run(text)
    assert result['text'] == text.replace("'@A, @B'", "'@B, @Longer'", 1)
    assert result['unresolved'] == []
    assert result['unsupported'] == []


def test_unicode_quoted_metadata_exact_bom_crlf_bytes_and_original_locations():
    _rich('unicodeQuotedInput', 'unicodeQuotedExpected', 4, 2)
    _rich('unicodeQuotedReviewInput', 'unicodeQuotedReviewExpected', 2, 2)


@pytest.mark.parametrize('fields', FIXTURE['unicodeQuotedInvalidFields'])
def test_unicode_quoted_metadata_malformed_escapes_multiline_scalars_and_non_ascii_separators_fail_closed(fields):
    refuses(f'---\n{fields}\n---\nBody [@A].')


@pytest.mark.parametrize('separator', ['\u2028', '\u2029'])
def test_unicode_plain_metadata_literal_separator_stays_opaque(separator):
    for newline in ['\n', '\r\n']:
        for quote in ["'", '"']:
            for value, migrated, count in [('@A, @B', '@B, @Longer', 3), ('[@A; -@B]', '[@B; -@Longer]', 3), ('@*', '@*', 1)]:
                for scalar in [f'Study{separator}Notes # [@unknown] opaque',
                               f'Study{separator}#token\t# [@unknown] {{child: value}} " \' opaque',
                               f'{separator}Study#{separator}Notes{separator}']:
                    text = newline.join(['---', f'title:\t{scalar}', f'nocite:\t{quote}{value}{quote}', '---', 'Body [@A].'])
                    expected = text.replace(f'{quote}{value}{quote}', f'{quote}{migrated}{quote}', 1).replace('Body [@A]', 'Body [@B]', 1)
                    result = run(text)
                    assert result['text'] == expected
                    assert len(result['edits']) == count
                    assert result['unresolved'] == []
                    assert result['unsupported'] == []


def test_unicode_plain_metadata_bom_crlf_bytes_and_original_locations():
    _rich('unicodePlainInput', 'unicodePlainExpected', 4, 2)
    _rich('unicodePlainReviewInput', 'unicodePlainReviewExpected', 2, 2)


@pytest.mark.parametrize('fields', FIXTURE['unicodePlainInvalidFields'])
def test_unicode_plain_metadata_structure_token_hashes_and_non_ascii_separators_fail_closed(fields):
    refuses(f'---\n{fields}\n---\nBody [@A].')


@pytest.mark.parametrize('quote', ["'", '"'])
def test_padded_nocite_wildcard_ascii_only_padding_preserves_the_standalone_token(quote):
    for newline in ['\n', '\r\n']:
        for left in ['', ' ', '\t', ' \t']:
            for right in ['', ' ', '\t', '\t ']:
                text = newline.join(['---', 'title:\tStudy\u2028Notes # [@unknown] opaque',
                                     f'nocite:\t{quote}{left}@*{right}{quote} \t# [@unknown]\u2029', '---', 'Body [@A; @B].'])
                result = run(text)
                assert result['text'] == text.replace('Body [@A; @B]', 'Body [@B; @Longer]', 1)
                assert len(result['edits']) == 2
                assert result['unresolved'] == []
                assert result['unsupported'] == []
                wildcard_only = text[:text.index('Body')]
                assert rekey_markdown(wildcard_only, {})['edits'] == []


@pytest.mark.parametrize('name', ['wildcard', 'wildcardDouble', 'wildcardReview'])
def test_padded_nocite_wildcard_bom_crlf_bytes_simultaneous_body_edits_and_unknown_locations(name):
    text = FIXTURE[f'{name}Input']
    result = run(text)
    assert result['text'].encode() == FIXTURE[f'{name}Expected'].encode()
    assert len(result['edits']) == 2
    assert result['unsupported'] == []
    offset = index_of(text, '-@unknown') + 2
    before = js_slice(text, 0, offset)
    expected = [{'key': 'unknown', 'offset': offset, 'line': 5, 'column': offset - last_index_of(before, '\n'), 'reason': 'no_mapping'}]
    assert result['unresolved'] == (expected if name == 'wildcardReview' else [])
    for edit in result['edits']:
        assert_edit_location(text, edit)


@pytest.mark.parametrize('fields', FIXTURE['wildcardInvalidFields'])
def test_padded_nocite_wildcard_non_ascii_padding_malformed_wildcards_and_unsafe_metadata_fail_closed(fields):
    refuses(f'---\n{fields}\n---\nBody [@A].')


@pytest.mark.parametrize('quote', ["'", '"'])
def test_padded_nocite_bracket_ascii_outer_padding(quote):
    for newline in ['\n', '\r\n']:
        for leading in ['', ' ', '\t', ' \t ']:
            for trailing in ['', ' ', '\t', '\t ']:
                for group, expected, keys in [
                    ('[@A; -@B]', '[@B; -@Longer]', ['A', 'B', 'A']),
                    ('[ \t@A; -@B ; @A; @Same ]', '[ \t@B; -@Longer ; @B; @Same ]', ['A', 'B', 'A', 'A']),
                    ('[@a.b; -@x:y_2]', '[@Dot; -@Colon]', ['a.b', 'x:y_2', 'A']),
                ]:
                    text = newline.join(['---', 'title:\tStudy\u2028Notes # [@unknown] opaque',
                                         f'nocite:\t{quote}{leading}{group}{trailing}{quote} # opaque', '---', 'Body [@A].'])
                    result = rekey_markdown(text, EXTENDED)
                    assert result['text'] == text.replace(group, expected, 1).replace('Body [@A]', 'Body [@B]', 1)
                    assert [edit['key'] for edit in result['edits']] == keys
                    assert result['unresolved'] == []
                    assert result['unsupported'] == []
                    for edit in result['edits']:
                        assert js_slice(text, edit['offset'], edit['offset'] + edit['length']) == edit['key']


@pytest.mark.parametrize('name', ['bracketPaddingSingle', 'bracketPaddingDouble', 'bracketPaddingSingleReview', 'bracketPaddingDoubleReview'])
def test_padded_nocite_bracket_bom_crlf_bytes_unknown_keys_and_original_spans(name):
    text = FIXTURE[f'{name}Input']
    result = run(text)
    assert result['text'].encode() == FIXTURE[f'{name}Expected'].encode()
    review = name.endswith('Review')
    assert len(result['edits']) == (3 if review else 4)
    assert result['unsupported'] == []
    expected = unknown_location(text)
    assert expected['line'] == 3 or not review
    assert result['unresolved'] == ([expected] if review else [])
    for edit in result['edits']:
        assert_edit_location(text, edit)


@pytest.mark.parametrize('fields', FIXTURE['bracketPaddingInvalidFields'])
def test_padded_nocite_bracket_reject_non_ascii_padding_affixes_malformed_groups_and_unsafe_metadata(fields):
    refuses(f'---\n{fields}\n---\nBody [@A].')
