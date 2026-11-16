# SPDX-License-Identifier: AGPL-3.0-only
# Port of linkdedupe tests/normalize.test.js.
import pytest

from notewrench.linkdedupe import normalize_url, validate_options


def key(url, options=None):
    return normalize_url(url, options)['url']


def test_host_case_default_ports_tracking_reasons_and_unicode_hosts():
    result = normalize_url('HTTPS://EXAMPLE.org:443/a?utm_source=x')
    assert result['url'] == 'https://example.org/a'
    assert [item['rule'] for item in result['normalizationReasons']] == ['scheme-case', 'host-case', 'default-port', 'tracking-parameter']
    assert key('http://EXAMPLE.org:80/a') == 'http://example.org/a'
    assert key('https://bücher.example/读物') == 'https://xn--bcher-kva.example/读物'
    assert key('https://[2001:DB8::1]:443/a') == 'https://[2001:db8::1]/a'


def test_raw_query_values_duplicate_keys_and_empty_tokens_are_preserved():
    suffix = '?q=a+b&q=a%20b&v=%2f&v=%2F&v=%26%3D&blank=&flag&&x=%FF#片段'
    assert key(f'https://example.org/a{suffix}') == f'https://example.org/a{suffix}'
    assert key('https://example.org/a?a=1&utm_source=x&a=2') == 'https://example.org/a?a=1&a=2'
    assert key('https://example.org/a?%75tm_source=x&q=%252F') == 'https://example.org/a?q=%252F'
    assert key('https://example.org/a?') == 'https://example.org/a?'
    assert key('https://example.org/a?utm_source=x&') == 'https://example.org/a?'
    assert key('https://example.org/a?UTM_source=x') == 'https://example.org/a?UTM_source=x'


@pytest.mark.parametrize('a,b', [
    ('http://example.org/a', 'https://example.org/a'),
    ('https://example.org/a', 'https://example.org/a/'),
    ('https://example.org', 'https://example.org/'),
    ('https://example.org/a#x', 'https://example.org/a#y'),
    ('https://example.org/a?x=1&y=2', 'https://example.org/a?y=2&x=1'),
    ('https://example.org/a?x=1&x=2', 'https://example.org/a?x=2&x=1'),
    ('https://example.org/a?ref=1', 'https://example.org/a?ref=2'),
    ('https://example.org/a?x=a+b', 'https://example.org/a?x=a%20b'),
    ('https://example.org/a/../b', 'https://example.org/b'),
    ('https://example.org:8443/a', 'https://example.org/a'),
    ('https://example.org./a', 'https://example.org/a'),
    ('https://127.1/a', 'https://127.0.0.1/a'),
])
def test_defaults_retain_url_distinctions_including_dot_segments_and_nondefault_ports(a, b):
    assert key(a) != key(b), f'{a} vs {b}'


@pytest.mark.parametrize('name', ['sig', 'Signature', 'X-Amz-Signature', 'x-goog-credential', '%73ig', 'token',
                                  'access_token', 'expires', 'oauth_nonce', 'hdnts'])
def test_known_signed_authenticated_urls_are_entirely_unchanged_with_all_opt_ins(name):
    url = f'HTTP://EXAMPLE.org:80/a/?b=2&{name}=value&utm_source=x&a=1#frag'
    result = normalize_url(url, {'stripFragment': True, 'https': True, 'sortQuery': True, 'stripTrailingSlash': True,
                                 'dropParams': ['pk_campaign']})
    assert result['url'] == url
    assert len(result['normalizationReasons']) == 0
    assert result['protections'][0]['rule'] == 'signed-or-authenticated-url'


def test_explicit_rules_are_reported_duplicate_key_value_order_stays_stable():
    result = normalize_url('http://example.org:80/a///?z=1&a=2&a=1#x',
                           {'stripFragment': True, 'https': True, 'sortQuery': True, 'stripTrailingSlash': True})
    assert result['url'] == 'https://example.org/a?a=2&a=1&z=1'
    assert [item['rule'] for item in result['normalizationReasons']] == [
        'default-port', 'query-order-opt-in', 'fragment-opt-in', 'trailing-slash-opt-in', 'http-to-https-opt-in']
    assert key('https://example.org/a?utm_source=x', {'keepTracking': True}) == 'https://example.org/a?utm_source=x'
    assert key('https://example.org/a?pk_campaign=x&x=1', {'dropParams': ['pk_campaign']}) == 'https://example.org/a?x=1'


@pytest.mark.parametrize('url', ['', None, 'relative/a', 'ftp://example.org/a', 'javascript:alert(1)', 'https://',
                                 'https://example.org:99999/a', 'https://example.org/a b', 'https://example.org/a\\b',
                                 'https://example.org/a?x=%ZZ', 'https://example.org/a?%FF=1', 'https://u:p@example.org/a',
                                 'https://example.org:/a', 'https://exa%mple.org/'])
def test_bad_urls_fail_instead_of_being_repaired(url):
    with pytest.raises(Exception):
        key(url)


@pytest.mark.parametrize('options', [
    *({'dropParams': [param]} for param in ['*', '.*', 'id', 'url', 'ref', 'utm_*', 'utm_', 'token', 'x-amz-signature', 'utm_signature']),
    {'dropParams': 'utm_source'},
    {'https': 'yes'},
    {'keepTracking': True, 'dropParams': ['utm_source']},
    {'arbitraryScript': 'rm -rf /'},
    {'dropParams': ['utm_source'] * 33},
])
def test_unsafe_custom_rules_unknown_options_and_invalid_types_fail_closed(options):
    with pytest.raises(Exception):
        validate_options(options)


def test_normalization_is_idempotent_and_every_actual_transformation_has_an_explanation():
    for url in ['HTTPS://BÜCHER.example:443/a?utm_source=x&b=%2f#f', 'http://EXAMPLE.org:80/a/?b=2&a=1&a=0&utm_medium=x#x',
                'https://example.org/a?utm_source=x&', 'HTTP://EXAMPLE.org:80/a?sig=x&utm_source=one',
                'https://example.org', 'http://example.org:443/a']:
        for options in [{}, {'stripFragment': True, 'sortQuery': True, 'https': True, 'stripTrailingSlash': True}]:
            first = normalize_url(url, options)
            assert normalize_url(first['url'], options)['url'] == first['url']
            if first['url'] != url:
                assert len(first['normalizationReasons']) > 0


def test_https_opt_in_removes_newly_default_443_ports_in_the_same_pass():
    result = normalize_url('http://example.org:443/a', {'https': True})
    assert result['url'] == 'https://example.org/a'
    assert [reason['rule'] for reason in result['normalizationReasons']] == ['http-to-https-opt-in', 'https-default-port-opt-in']
