import pytest

from notewrench.relink.urls import normalized_text, url_key, useful_quote


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("HTTPS://ExAmPlE.org:443", "https://example.org/"),
        ("http://example.org/%7eperson?q=%2f#fragment", "http://example.org/~person?q=%2F"),
        ("https://例え.jp/本", "https://xn--r8jz45g.jp/%E6%9C%AC"),
        ("https://[::1]:443/a", "https://[::1]/a"),
        ("https://example.org:8080/a", "https://example.org:8080/a"),
        ("https://user:password@example.org/a", None),
        ("https://example.org:invalid/a", None),
        ("file:///private/archive.html", None),
    ],
)
def test_keys(url, expected):
    assert url_key(url) == expected


@pytest.mark.parametrize(
    ("one", "two"),
    [
        ("http://example.org/a", "https://example.org/a"),
        ("https://example.org/a", "https://example.org/a/"),
        ("https://example.org/A", "https://example.org/a"),
        ("https://example.org/?a=1&b=2", "https://example.org/?b=2&a=1"),
        ("https://example.org/?utm_source=x", "https://example.org/"),
        ("https://example.org/a%2Fb", "https://example.org/a/b"),
    ],
)
def test_important_distinctions_preserved(one, two):
    assert url_key(one) != url_key(two)


def test_quotation_normalization_and_threshold():
    assert normalized_text("Cafe\u0301  WORDS\nnext") == "café words next"
    assert useful_quote("私人保存的网页副本里面包含原文引述")
    assert not useful_quote("short title")


def test_idna_2008_hosts_do_not_collide_with_transitional_names():
    assert url_key("https://faß.de/a") == "https://xn--fa-hia.de/a"
    assert url_key("https://faß.de/a") != url_key("https://fass.de/a")


def test_empty_query_is_not_absent_query():
    assert url_key("https://example.org/?#anchor") == "https://example.org/?"
    assert url_key("https://example.org/?") != url_key("https://example.org/")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/a\tb",
        "https://example.org/a\nb",
        "https://exa mple.org/",
        "https://example.org\\other.org/",
        "https://example.org/\x00",
    ],
)
def test_ambiguous_or_control_character_urls_are_rejected(url):
    assert url_key(url) is None
