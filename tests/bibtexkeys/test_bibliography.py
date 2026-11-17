from pathlib import Path

import pytest

from notewrench.bibtexkeys.bibliography import DEFAULT_ID_FIELDS, InputError, Record, identity_value, load_snapshot, match_snapshots, read_utf8

FIXTURES = Path(__file__).parent / "fixtures" / "upstream"


def test_real_bbt_exports_do_not_invent_item_identity():
    for filename, expected in (("bbt-sample.bib", 1), ("bbt-stable.bib", 6)):
        _, records, issues = load_snapshot(FIXTURES / filename, DEFAULT_ID_FIELDS)
        assert len(records) == expected
        assert all(record.identity is None for record in records)
        assert len(issues) == expected
        assert {issue["kind"] for issue in issues} == {"missing_identity"}


def test_macro_concatenation_and_case_insensitive_field_names(tmp_path):
    path = tmp_path / "export.bib"
    path.write_text('@string{prefix="http://zotero.org/"}\n@article{Old, ZOTERO_URI=prefix # "users/1/items/ABCD1234/", title={A {Nested} Title}}')
    _, records, issues = load_snapshot(path, DEFAULT_ID_FIELDS)
    assert records == [Record("Old", "https://zotero.org/users/1/items/ABCD1234")]
    assert issues == []


@pytest.mark.parametrize("source", [
    '@article{A, title={Missing brace}',
    '@article{A, title={One}}\n@article{A, title={Two}}',
    '@article{A, title={One}, title={Two}}',
    '@article{A, zotero_uri=missing_macro}',
    '@article{A, title={One}}\n@article{a, title={Two}}',
    'Just a comment, no entries',
])
def test_invalid_bibliography_is_fatal(tmp_path, source):
    path = tmp_path / "bad.bib"
    path.write_text(source)
    with pytest.raises(InputError):
        load_snapshot(path, DEFAULT_ID_FIELDS)


def test_conflicting_aliases_never_match(tmp_path):
    path = tmp_path / "export.bib"
    path.write_text('@article{A,zotero-uri={https://zotero.org/users/1/items/AAA},zotero_uri={https://zotero.org/users/2/items/AAA}}')
    _, records, issues = load_snapshot(path, DEFAULT_ID_FIELDS)
    assert records[0].identity is None
    assert issues[0]["kind"] == "conflicting_identity_fields"


def test_library_scopes_stay_distinct():
    old = [Record("Old", "https://zotero.org/users/1/items/ABC")]
    new = [Record("New", "https://zotero.org/groups/1/items/ABC")]
    mappings, states, conflicts = match_snapshots(old, new)
    assert mappings == []
    assert states["Old"]["status"] == "removed_item"
    assert conflicts[0]["kind"] == "removed_item"


def test_duplicate_identity_blocks_every_affected_key():
    old = [Record("A", "one"), Record("B", "two")]
    new = [Record("C", "one"), Record("D", "one"), Record("E", "two")]
    mappings, states, conflicts = match_snapshots(old, new)
    assert mappings == [{"old_key": "B", "new_key": "E", "identity": "two"}]
    assert states["A"]["status"] == "conflict"
    assert conflicts[0]["kind"] == "duplicate_identity"


def test_swap_mapping_is_simultaneous():
    mappings, states, _ = match_snapshots([Record("A", "one"), Record("B", "two")], [Record("B", "one"), Record("A", "two")])
    assert states["A"]["new_key"] == "B"
    assert states["B"]["new_key"] == "A"
    assert len(mappings) == 2


@pytest.mark.parametrize("value", ["https://example.org/users/1/items/ABC", "https://zotero.org/users/1/items/ABC?x=1", "zotero://select/library/items/ABC", "https://zotero.org/users/not-a-user-id/items/ABC", "https://zotero.org/groups/123/items/has space", "", "\n"])
def test_default_identity_is_not_an_arbitrary_url(value):
    with pytest.raises(ValueError):
        identity_value("zotero-uri", value)


def test_nonregular_and_oversized_inputs_fail_before_reading(tmp_path):
    import os
    fifo = tmp_path / 'pipe.bib'
    os.mkfifo(fifo)
    with pytest.raises(InputError, match='regular file'):
        read_utf8(fifo, 100)
    large = tmp_path / 'large.bib'
    with large.open('wb') as stream:
        stream.truncate(1024)
    with pytest.raises(InputError, match='exceeds'):
        read_utf8(large, 100)


def test_custom_identity_is_exact_after_standard_bibtex_parsing(tmp_path):
    assert identity_value('stable-id', 'CaseSensitive-ID') == 'CaseSensitive-ID'
    assert identity_value('stable-id', ' CaseSensitive-ID ') == ' CaseSensitive-ID '
    path = tmp_path / 'custom.bib'
    path.write_text('@article{A,stable-id={ item-123 }}')
    _, records, issues = load_snapshot(path, ('stable-id',))
    # Pybtex applies BibTeX's own whitespace semantics before identity matching.
    assert records[0].identity == 'item-123'
    assert not issues
