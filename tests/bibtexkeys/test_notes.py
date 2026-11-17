import pytest

from notewrench.bibtexkeys.notes import NoteScanner, apply_edits


def scanner(mapping=None):
    mapping = mapping or {"Old": "New"}
    return NoteScanner({key: {"status": "renamed", "new_key": value, "identity": "id:" + key} for key, value in mapping.items()})


def rewrite(text, mapping=None):
    edits, occurrences, issues = scanner(mapping).scan(text)
    return apply_edits(text, edits), occurrences, issues


def test_citation_locators_narrative_and_suppress_author():
    text = "Read @Old and [see @Old, pp. 4–8; -@Other]. @Older is distinct."
    result, occurrences, issues = rewrite(text, {"Old": "New", "Other": "Second"})
    assert result == "Read @New and [see @New, pp. 4–8; -@Second]. @Older is distinct."
    assert len(occurrences) == 3
    assert issues == []


def test_code_escapes_urls_comments_and_markdown_links_are_protected():
    text = '''`@Old` ``code ` @Old`` \\@Old user@Old.com https://example.org/@Old
[@Old](https://example.org) ![@Old](image.png)
<!-- @Old -->

```markdown
@Old
```

    @Old

actual @Old
'''
    result, occurrences, issues = rewrite(text)
    assert result == text.replace("actual @Old", "actual @New")
    assert len(occurrences) == 1
    assert issues == []


def test_nested_list_and_blockquote_source_positions_preserve_crlf():
    text = "> - See @Old.\r\n>   Continued [@Old].\r\n"
    result, occurrences, issues = rewrite(text)
    assert result == text.replace("@Old", "@New")
    assert [(o["line"], o["column"]) for o in occurrences] == [(1, 10), (2, 17)]
    assert issues == []


def test_swap_and_chain_do_not_cascade():
    result, _, _ = rewrite("@A @B @C", {"A": "B", "B": "C", "C": "A"})
    assert result == "@B @C @A"


def test_braced_keys_and_new_keys_requiring_braces():
    result, _, _ = rewrite("@{Old} @Old @Old--suffix", {"Old": "New--Key"})
    assert result == "@{New--Key} @{New--Key} @{New--Key}--suffix"


def test_metadata_source_spans_quotes_lists_and_bom():
    text = '\ufeff---\ncitekey: Old # keep comment\ncitekeys: ["@Old", \'Other\']\ntitle: "Old @Old"\n---\nBody @Old\n'
    result, occurrences, issues = rewrite(text, {"Old": "New", "Other": "Second"})
    assert result == '\ufeff---\ncitekey: "New" # keep comment\ncitekeys: ["@New", "Second"]\ntitle: "Old @Old"\n---\nBody @New\n'
    assert len(occurrences) == 4
    assert issues == []


@pytest.mark.parametrize("metadata", ["citekey: [", "citekey: Old\ncitekey: Other", "title: &x Old\ncitekey: *x", "citekey: |\n  Old"])
def test_unsupported_metadata_is_flagged_without_modifying_it(metadata):
    text = "---\n" + metadata + "\n---\nBody @Old"
    result, _, issues = rewrite(text)
    assert result == text.replace("Body @Old", "Body @New")
    assert issues


def test_unclosed_frontmatter_does_not_treat_yaml_as_prose():
    text = "---\ncitekey: Old\n@Old\n"
    result, _, issues = rewrite(text)
    assert result == text
    assert issues[0]["kind"] == "invalid_frontmatter"


def test_bbt_links_are_encoded_and_stable_links_untouched():
    text = '[open](zotero://select/items/@Old) <zotero://select/items/@Old> zotero://select/items/@Old\n[stable](zotero://select/library/items/Old)'
    result, occurrences, issues = rewrite(text, {"Old": "Néw:2026"})
    assert result == text.replace("items/@Old", "items/@N%C3%A9w%3A2026")
    assert len(occurrences) == 3
    assert issues == []


def test_percent_encoded_original_link():
    result, occurrences, issues = rewrite('[open](zotero://select/items/@Old%3A2020)', {"Old:2020": "New:2020"})
    assert result == '[open](zotero://select/items/@New%3A2020)'
    assert len(occurrences) == 1
    assert issues == []


def test_wiki_and_file_links_require_target_rename():
    text = "[[@Old|label]] [[folder/Old#section]] [read](folder/%40Old.md) @Old"
    result, occurrences, issues = rewrite(text)
    assert result == text[:-4] + "@New"
    assert [o["status"] for o in occurrences] == ["manual_review"] * 3 + ["replace"]
    assert issues == []


def test_missing_or_ambiguous_identity_never_produces_edits():
    scan = NoteScanner({"Old": {"status": "conflict", "identity": "duplicate", "new_key": None}})
    edits, occurrences, issues = scan.scan("@Old")
    assert not edits
    assert occurrences[0]["status"] == "conflict"
    assert not issues


def test_zotero_looking_link_label_and_title_are_not_destinations():
    text = '[zotero://select/items/@Old](https://example.org) [label](zotero://select/items/@Old "zotero://select/items/@Old")'
    result, occurrences, issues = rewrite(text)
    assert result == '[zotero://select/items/@Old](https://example.org) [label](zotero://select/items/@New "zotero://select/items/@Old")'
    assert len(occurrences) == 1
    assert not issues


@pytest.mark.parametrize("text", [
    '[ref]: zotero://select/items/@Old\n\nSee [ref].',
    '[ref]:\n  <zotero://select/items/@Old>\n  "zotero://select/items/@Old"\n\nSee [ref].',
    '> [ref]: zotero://select/items/@Old\r\n>\r\n> See [ref].',
])
def test_reference_definition_destination_is_changed_once(text):
    result, occurrences, issues = rewrite(text)
    assert result == text.replace('items/@Old', 'items/@New', 1)
    assert len(occurrences) == 1
    assert not issues


def test_yaml_null_never_becomes_a_citation_key():
    text = '---\ncitekey: null\n---\n'
    result, occurrences, issues = rewrite(text, {"null": "New"})
    assert result == text
    assert not occurrences
    assert not issues


def test_emails_with_punctuation_in_local_part_are_never_citations():
    text = 'user+@Old.com name-@Old.com user.name@Old.com and @Old.com'
    result, occurrences, issues = rewrite(text, {"Old.com": "New"})
    assert result == 'user+@Old.com name-@Old.com user.name@Old.com and @New'
    assert len(occurrences) == 3
    assert [o['status'] for o in occurrences] == ['manual_review', 'manual_review', 'replace']
    assert not issues


def test_entity_encoded_markdown_destination_uses_decoded_href():
    text = '[open](zotero://select/items/@Old&amp;X)'
    result, occurrences, issues = rewrite(text, {"Old&X": "New&X"})
    assert result == '[open](zotero://select/items/@New%26X)'
    assert len(occurrences) == 1
    assert not issues


def test_unused_file_reference_definition_is_reported():
    text = '[unused]: folder/@Old.md "title"\n'
    result, occurrences, issues = rewrite(text)
    assert result == text
    assert occurrences[0]["kind"] == "markdown_reference_definition"
    assert occurrences[0]["status"] == "manual_review"
    assert not issues


def test_author_suppression_with_domain_shaped_citekey_is_not_email():
    result, occurrences, issues = rewrite('[-@Old.com] and @Old.com', {"Old.com": "New"})
    assert result == '[-@New] and @New'
    assert len(occurrences) == 2
    assert not issues


def test_citekey_prefixed_note_titles_are_reported():
    text = '[[@Old Paper Notes]] [read](folder/%40Old%20Paper%20Notes.md)'
    result, occurrences, issues = rewrite(text)
    assert result == text
    assert len(occurrences) == 2
    assert {o["status"] for o in occurrences} == {"manual_review"}
    assert not issues


def test_large_single_paragraph_keeps_protected_regions_and_all_citations():
    segment = '`@Old` [link](https://example.org/@Old) [@Old] '
    result, occurrences, issues = rewrite(segment * 4000)
    assert result == segment.replace('[@Old]', '[@New]') * 4000
    assert len(occurrences) == 4000
    assert not issues


def test_long_plain_word_does_not_hide_a_following_citation():
    text = 'x' * 250000 + ' @Old'
    result, occurrences, issues = rewrite(text)
    assert result.endswith(' @New')
    assert len(occurrences) == 1
    assert not issues


def test_indented_thematic_breaks_are_code_not_frontmatter():
    text = '    ---\n    citekey: Old\n    ---\n'
    result, occurrences, issues = rewrite(text)
    assert result == text
    assert not occurrences
    assert not issues


def test_indented_yaml_delimiter_in_block_scalar_does_not_end_frontmatter():
    text = '---\ntitle: |\n  ---\n  @Old\ncitekey: Old\n---\nBody @Old'
    result, occurrences, issues = rewrite(text)
    assert result == text.replace('citekey: Old', 'citekey: "New"').replace('Body @Old', 'Body @New')
    assert len(occurrences) == 2
    assert not issues


@pytest.mark.parametrize('separator', ['\u2028', '\u2029', '\x85', '\v', '\f', '\x1c'])
def test_unicode_and_control_line_separators_are_not_lf_source_lines(separator):
    text = 'Before' + separator + '@Old'
    result, occurrences, issues = rewrite(text)
    assert result == 'Before' + separator + '@New'
    assert len(occurrences) == 1
    assert occurrences[0]['line'] == 1
    assert not issues


@pytest.mark.parametrize('math', ['$@Old$', '$$@Old$$', '$$\n@Old\n$$', '> $$\n> @Old\n> $$'])
def test_dollar_math_is_literal(math):
    text = math + '\n\nActual @Old'
    result, occurrences, issues = rewrite(text)
    assert result == math + '\n\nActual @New'
    assert len(occurrences) == 1
    assert not issues


@pytest.mark.parametrize('tag', ['script', 'style', 'code', 'pre', 'textarea', 'math'])
def test_raw_html_element_bodies_are_literal(tag):
    text = f'Inline <{tag}>@Old</{tag}> and @Old'
    result, occurrences, issues = rewrite(text)
    assert result == f'Inline <{tag}>@Old</{tag}> and @New'
    assert len(occurrences) == (1 if tag in {'script', 'style'} else 2)
    assert not issues


def test_html_looking_code_does_not_open_a_raw_element():
    text = '`<script>` then @Old'
    result, occurrences, issues = rewrite(text)
    assert result == '`<script>` then @New'
    assert len(occurrences) == 1
    assert not issues


@pytest.mark.parametrize('text,expected', [
    ('# Header @Old {#stable title="@Old"}', '# Header @New {#stable title="@Old"}'),
    ('[link](url){title="@Old"} and @Old', '[link](url){title="@Old"} and @New'),
    ('`code`{title="@Old"} and @Old', '`code`{title="@Old"} and @New'),
])
def test_parsed_attributes_are_not_citations(text, expected):
    result, occurrences, issues = rewrite(text)
    assert result == expected
    assert len(occurrences) == 1
    assert not issues


def test_footnote_labels_are_not_citations_but_their_bodies_are():
    text = 'Here[^@Old].\n\n[^@Old]: @Old\n'
    result, occurrences, issues = rewrite(text)
    assert result == 'Here[^@Old].\n\n[^@Old]: @New\n'
    assert len(occurrences) == 1
    assert occurrences[0]['column'] == 11
    assert not issues


def test_inline_footnote_content_respects_code_and_citations():
    text = 'Inline ^[Read @Old and literal `@Old`].'
    result, occurrences, issues = rewrite(text)
    assert result == 'Inline ^[Read @New and literal `@Old`].'
    assert len(occurrences) == 1
    assert not issues


def test_undefined_footnote_label_is_left_literal():
    text = 'Undefined[^@Old] and @Old.'
    result, occurrences, issues = rewrite(text)
    assert result == 'Undefined[^@Old] and @New.'
    assert len(occurrences) == 1
    assert not issues


def test_metadata_only_accepts_one_optional_at_prefix():
    text = '---\ncitekey: "@@Old"\n---\n@Old'
    result, occurrences, issues = rewrite(text)
    assert result == text.replace('\n@Old', '\n@New')
    assert len(occurrences) == 1
    assert not issues


@pytest.mark.parametrize('uri', ['zotero://select/items/@%FF', 'zotero://select/items/@Old?x=1', 'zotero://select/items/@Old#part'])
def test_invalid_or_unsupported_select_link_never_guesses_a_key(uri):
    text = '[open](' + uri + ')'
    result, occurrences, issues = rewrite(text, {'Old': 'New', '\ufffd': 'Replacement'})
    assert result == text
    assert not occurrences
    assert issues[0]['kind'] == 'invalid_citekey_link'


def test_malformed_percent_in_bare_select_link_is_reported():
    text = 'zotero://select/items/@Old%ZZ'
    result, occurrences, issues = rewrite(text)
    assert result == text
    assert not occurrences
    assert issues[0]['kind'] == 'invalid_citekey_link'


def test_markdown_percent_normalization_uses_the_parsed_destination():
    text = '[open](zotero://select/items/@Old%ZZ)'
    result, occurrences, issues = rewrite(text, {'Old%ZZ': 'New'})
    assert result == '[open](zotero://select/items/@New)'
    assert len(occurrences) == 1
    assert not issues


@pytest.mark.parametrize('tex', [r'\texttt{@Old}', r'\unknown{@Old}', r'\unknown[opt]{nested {@Old}}', '\\begin{equation}\n@Old\n\n@Old\n\\end{equation}'])
def test_raw_tex_macros_and_environments_preserve_literal_content(tex):
    text = tex + '\n\nActual @Old'
    result, occurrences, issues = rewrite(text)
    assert result == tex + '\n\nActual @New'
    assert len(occurrences) == 1
    assert not issues


def test_unclosed_raw_tex_is_reported_and_not_guessed():
    text = r'Inline \texttt{unclosed @Old'
    result, occurrences, issues = rewrite(text)
    assert result == text
    assert not occurrences
    assert issues[0]['kind'] == 'invalid_raw_tex'


def test_raw_tex_looking_inline_code_does_not_mask_following_citation():
    text = r'`\begin{not-an-environment}` and @Old'
    result, occurrences, issues = rewrite(text)
    assert result == text.replace('and @Old', 'and @New')
    assert len(occurrences) == 1
    assert not issues


def test_heading_citation_changes_report_automatic_anchor_impact():
    result, occurrences, issues = rewrite('# Heading @Old')
    assert result == '# Heading @New'
    assert len(occurrences) == 1
    assert issues[0]['kind'] == 'heading_anchor_may_change'
    result, _, issues = rewrite('# Heading @Old {#stable}')
    assert result == '# Heading @New {#stable}'
    assert not issues


def test_braced_url_citation_key_is_not_hidden_by_url_protection():
    key = 'https://example.org/paper?x=1'
    result, occurrences, issues = rewrite('@{' + key + '}', {key: 'New'})
    assert result == '@{New}'
    assert len(occurrences) == 1
    assert not issues


def test_braced_citation_inside_an_ordinary_url_stays_literal():
    text = 'https://example.org/?ref=@{Old}'
    result, occurrences, issues = rewrite(text)
    assert result == text
    assert not occurrences
    assert not issues


def test_email_looking_code_does_not_create_a_manual_review_false_positive():
    text = '`user+@Old.com`'
    result, occurrences, issues = rewrite(text, {'Old.com': 'New'})
    assert result == text
    assert not occurrences
    assert not issues


def test_unicode_citation_keys_keep_exact_unicode_identity():
    result, occurrences, issues = rewrite('Read @Müller2020 and @旧键。', {'Müller2020': 'Mueller2020', '旧键': '新键'})
    assert result == 'Read @Mueller2020 and @新键。'
    assert len(occurrences) == 2
    assert not issues


@pytest.mark.parametrize('key', ['foo$bar$', 'foo`bar`', 'foo[bar](url)', r'foo\unknown[opt]'])
def test_braced_keys_own_their_interior_markdown_syntax(key):
    result, occurrences, issues = rewrite('@{' + key + '}', {key: 'New'})
    assert result == '@{New}'
    assert len(occurrences) == 1
    assert not issues


def test_unknown_braced_key_cannot_be_modified_as_an_inner_saved_link():
    text = '@{foo[open](zotero://select/items/@Old)}'
    result, occurrences, issues = rewrite(text)
    assert result == text
    assert not occurrences
    assert not issues
