import pytest

from notewrench.relink.markdown import citations


@pytest.mark.parametrize(
    ("source", "originals"),
    [
        ('[a](https://example.org/a "title")', ["https://example.org/a"]),
        ("[a](<https://example.org/a>)", ["<https://example.org/a>"]),
        ("[a](https://example.org/a_(b))", ["https://example.org/a_(b)"]),
        (r"[a](https://example.org/a_\(b\))", [r"https://example.org/a_\(b\)"]),
        ("<https://example.org/a>", ["<https://example.org/a>"]),
        ("[a](https://example.org/a?x=1&amp;y=2)", ["https://example.org/a?x=1&amp;y=2"]),
        ("[a][r] and [b][r]\n\n[r]: https://example.org/a 'title'\n", ["https://example.org/a"]),
        ("[r][] and [r]\n\n[r]: <https://example.org/a>\n", ["<https://example.org/a>"]),
        ("> - [a](https://example.org/a)\n>   next line\n", ["https://example.org/a"]),
        (
            "[a](https://example.org/a)\r\n\r\n[a](https://example.org/b)\r\n",
            ["https://example.org/a", "https://example.org/b"],
        ),
        ("# [a](https://example.org/a)\n", ["https://example.org/a"]),
        ("[a](\n  https://example.org/a\n)", ["https://example.org/a"]),
        ("![image](https://example.org/a)\n", []),
        ("`[a](https://example.org/a)`\n", []),
        ("```md\n[a](https://example.org/a)\n```", []),
        ("    [a](https://example.org/a)\n", []),
        ('<a href="https://example.org/a">a</a>', []),
        (r"\[a](https://example.org/a)", []),
        ("[a](file:///tmp/archive.html)", []),
        ("[a](javascript:alert(1))", []),
    ],
)
def test_commonmark_spans(source, originals):
    links, warnings = citations(source)
    assert not warnings
    assert [source[link.start : link.end] for link in links] == originals


def test_shared_references_keep_uses():
    links, _ = citations("[one][r]\n\n[two][r]\n\n[r]: https://example.org\n")
    assert len(links) == 1
    assert links[0].uses == [1, 3]


def test_body_quotes_not_link_labels():
    links, _ = citations(
        "[This is a sufficiently long title to fool a title matcher](https://example.org)"
    )
    assert links[0].quotes == []
    links, _ = citations(
        "[source](https://example.org) said “This is a sufficiently long quoted body passage.”"
    )
    assert links[0].quotes == ["This is a sufficiently long quoted body passage."]


def test_adjacent_blockquote():
    links, _ = citations(
        "> A distinctive quotation from a previously saved body paragraph.\n\n"
        "[source](https://example.org)\n"
    )
    assert "A distinctive quotation from a previously saved body paragraph." in links[0].quotes


@pytest.mark.parametrize(
    "source",
    [
        "![alt [link](https://example.org/story)](image.jpg)",
        "![<https://example.org/story>](image.jpg)",
        "![![alt](https://example.org/story)](image.jpg)",
    ],
)
def test_image_alt_text_is_not_a_citation(source):
    assert citations(source) == ([], [])


@pytest.mark.parametrize(
    "source",
    [
        '[a](https://example.org/a "This is an article title rather than a body quotation.")',
        "[“This is an article title rather than a body quotation.”](https://example.org/a)",
        '[a](https://example.org/a) `"This is an article title rather than a body quotation."`',
    ],
)
def test_titles_labels_and_code_do_not_supply_quote_evidence(source):
    links, warnings = citations(source)
    assert warnings == []
    assert links[0].quotes == []


def test_frontmatter_is_not_prose():
    source = (
        "---\nsecret: [private](https://example.org/metadata)\n---\n\n"
        "[source](https://example.org/body)"
    )
    links, warnings = citations(source)
    assert warnings == []
    assert [link.url for link in links] == ["https://example.org/body"]


@pytest.mark.parametrize(
    "source",
    [
        "[a][r]\n\n> [r]:\n>   https://example.org/story\n",
        "> [a][r]\n>\n> [r]:\n>   https://example.org/story\n",
        "- [a][r]\n\n  [r]:\n      https://example.org/story\n",
        "[a][some\\]:name]\n\n[some\\]:name]: https://example.org/story\n",
    ],
)
def test_container_and_escaped_reference_spans(source):
    links, warnings = citations(source)
    assert warnings == []
    assert [source[link.start : link.end] for link in links] == ["https://example.org/story"]


def test_reference_shared_with_image_is_left_unchanged():
    source = "[link][shared]\n\n![image][shared]\n\n[shared]: https://example.org/article\n"
    links, warnings = citations(source)
    assert links == []
    assert len(warnings) == 1
    assert "also used by an image" in warnings[0]


def test_utf8_bom_does_not_expose_frontmatter_links():
    source = (
        "\ufeff---\r\nprivate: [secret](https://private.example/metadata)\r\n---\r\n\r\n"
        "[source](https://public.example/body)\r\n"
    )
    links, warnings = citations(source)
    assert not warnings
    assert [link.url for link in links] == ["https://public.example/body"]
    assert source[links[0].start : links[0].end] == "https://public.example/body"


@pytest.mark.parametrize(
    "destination",
    [
        "https://user:password@example.org/private",
        "https://example.org:invalid/",
        "https:missing-host",
    ],
)
def test_unsupported_http_destinations_are_reported(destination):
    links, warnings = citations(f"[source]({destination})")
    assert links == []
    assert warnings and "unsupported or invalid HTTP(S) URL" in warnings[0]
