# SPDX-License-Identifier: AGPL-3.0-only
"""Port of test/identity.test.js."""
from __future__ import annotations

import json

import pytest

from notewrench.citekeys import RekeyError, build_mapping, normalize_doi

_ABSENT = object()


def item(id_: str, doi: object = _ABSENT, url: object = _ABSENT) -> dict[str, object]:
    record: dict[str, object] = {'id': id_}
    if doi is not _ABSENT:
        record['DOI'] = doi
    if url is not _ABSENT:
        record['URL'] = url
    return record


def test_doi_normalization_strips_resolver_and_folds_doi_only():
    for doi in ['10.123/demo', '10.123/DEMO', 'https://doi.org/10.123/DEMO', 'HTTP://DX.DOI.ORG/10.123/DEMO']:
        assert normalize_doi(doi) == '10.123/demo'
    result = build_mapping([item('old', '10.123/DEMO')], [item('New', 'https://doi.org/10.123/demo')])
    assert result['mapping'] == [{'old': 'old', 'new': 'New', 'via': 'DOI'}]


def test_exact_url_fallback_without_url_casefolding_or_canonicalization():
    url = 'https://example.org/Case?x=1'
    assert build_mapping([item('a', url=url)], [item('b', url=url)])['mapping'][0]['via'] == 'URL'
    for variant in ['https://example.org/case?x=1', 'https://example.org/Case?x=1#fragment', f'{url}/']:
        assert len(build_mapping([item('a', url=url)], [item('b', url=variant)])['mapping']) == 0


def test_doi_wins_when_url_changed_url_can_bridge_a_missing_doi_on_one_side():
    assert build_mapping([item('a', '10.1/x', 'https://example.org/old')],
                         [item('b', '10.1/x', 'https://example.org/new')])['mapping'][0]['via'] == 'DOI'
    assert build_mapping([item('a', '10.1/x', 'https://example.org/x')],
                         [item('b', url='https://example.org/x')])['mapping'][0]['via'] == 'URL'


def test_missing_identity_and_identical_titles_ids_never_infer_a_match():
    result = build_mapping([{'id': 'a', 'title': 'Same'}, item('b', '10.1/no')], [{'id': 'a', 'title': 'Same'}])
    assert result['mapping'] == []
    assert result['unmatched'] == [{'key': 'a', 'reason': 'missing_identity'}, {'key': 'b', 'reason': 'no_identity_match'}]


@pytest.mark.parametrize('side', ['old', 'new'])
def test_duplicate_id_is_rejected(side):
    dupes = [item('a', '10.1/x'), item('a', '10.1/y')]
    with pytest.raises(RekeyError) as caught:
        build_mapping(dupes if side == 'old' else [], dupes if side == 'new' else [])
    assert caught.value.code == 'DUPLICATE_ID'


@pytest.mark.parametrize('side', ['old', 'new'])
@pytest.mark.parametrize('field', ['DOI', 'URL'])
def test_duplicate_identity_refuses_all_mappings(side, field):
    value = '10.1/x' if field == 'DOI' else 'https://example.org/x'
    dupes = [{'id': 'a', field: value}, {'id': 'b', field: value}]
    result = build_mapping(dupes if side == 'old' else [], dupes if side == 'new' else [])
    assert result['conflicts'][0]['code'] == 'DUPLICATE_IDENTITY'
    assert result['mapping'] == []


def test_contradictory_doi_and_doi_url_disagreement_refuse_all_mappings():
    contradiction = build_mapping([item('a', '10.1/x', 'https://example.org/x')], [item('b', '10.1/y', 'https://example.org/x')])
    assert contradiction['conflicts'][0]['code'] == 'CONTRADICTORY_DOI'
    split = build_mapping([item('a', '10.1/x', 'https://example.org/x')], [item('b', '10.1/x'), item('c', url='https://example.org/x')])
    assert split['conflicts'][0]['code'] == 'IDENTITY_DISAGREEMENT'
    assert split['mapping'] == []


def test_many_old_records_cannot_converge_on_one_new_record_through_different_identities():
    result = build_mapping([item('a', '10.1/x'), item('b', url='https://example.org/x')], [item('c', '10.1/x', 'https://example.org/x')])
    assert result['conflicts'][0]['code'] == 'MANY_TO_ONE'
    assert result['mapping'] == []


def test_unmatched_old_key_cannot_be_captured_by_a_renamed_different_item():
    result = build_mapping([item('a', '10.1/x'), {'id': 'b'}], [item('b', '10.1/x')])
    assert result['conflicts'][0]['code'] == 'UNMATCHED_KEY_CAPTURE'


def test_unchanged_keys_chains_and_swaps_are_valid_bijections():
    old = [item('a', '10.1/a'), item('b', '10.1/b'), item('c', '10.1/c')]
    updated = [item('b', '10.1/a'), item('a', '10.1/b'), item('c', '10.1/c')]
    result = build_mapping(old, updated)
    assert result['conflicts'] == []
    assert [[x['old'], x['new']] for x in result['mapping']] == [['a', 'b'], ['b', 'a'], ['c', 'c']]


INVALID = [None, {}, 'text', [{'id': 42}], [{'id': ''}], [{'id': 'a--b'}], [{'id': 'a.'}], [{'id': 'with space'}],
           [{'id': 'a', 'DOI': None}], [{'id': 'a', 'DOI': 'doi:10.1/a'}], [{'id': 'a', 'DOI': ' 10.1/a'}],
           [{'id': 'a', 'URL': None}], [{'id': 'a', 'URL': 'ftp://example.org/a'}], [{'id': 'a', 'URL': '/relative'}]]


@pytest.mark.parametrize('invalid', INVALID, ids=[json.dumps(value) for value in INVALID])
def test_invalid_csl_identity(invalid):
    with pytest.raises(RekeyError):
        build_mapping(invalid, [])
