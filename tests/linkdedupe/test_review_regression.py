# SPDX-License-Identifier: AGPL-3.0-only
# Port of linkdedupe tests/review-regression.test.js.
import pytest

from notewrench.linkdedupe import build_report

from .helpers import js


def row(**flags):
    return {'id': 1, 'url': 'https://example.com/a', 'title': 'A', 'tags': [], 'created_at': '2026-01-01', **flags}


def report(record, **options):
    return build_report([{'name': 'native', 'text': js([record]), 'format': 'wallabag-v2'}], **options)


def test_native_wallabag_boolean_and_0_1_flags_preserve_raw_provenance():
    for is_archived in [False, True, 0, 1]:
        for is_starred in [False, True, 0, 1]:
            original = row(is_archived=is_archived, is_starred=is_starred)
            result = report(original)
            assert result['records']['summary']['acceptedRecords'] == 1
            raw = result['provenance']['records'][0]['raw']
            assert raw == original
            assert type(raw['is_archived']) is type(is_archived) and type(raw['is_starred']) is type(is_starred)


@pytest.mark.parametrize('value', [2, -1, '0', '1', None, [], {}])
def test_invalid_wallabag_state_values_are_rejected_or_quarantined(value):
    with pytest.raises(Exception, match='is_archived'):
        report(row(is_archived=value))
    assert report(row(is_starred=value), skip_invalid=True)['records']['summary']['skippedRecords'] == 1


def test_karakeep_archived_flag_remains_boolean_only():
    document = {'bookmarks': [{'createdAt': 1, 'title': None, 'tags': [], 'note': None, 'content': None, 'archived': 1}]}
    with pytest.raises(Exception, match='archived'):
        build_report([{'name': 'native', 'text': js(document), 'format': 'karakeep'}])
