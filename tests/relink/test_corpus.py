"""Compare our span adapter with the upstream parser on the official corpus."""

import json
from pathlib import Path

from markdown_it import MarkdownIt
from mdit_py_plugins.front_matter import front_matter_plugin

from notewrench.relink.markdown import citations
from notewrench.relink.urls import url_key

FIXTURE = Path(__file__).resolve().parent / "fixtures/upstream/commonmark-0.31.2.json"


def actual_links(md, source):
    return {
        token.attrGet("href")
        for block in md.parse(source)
        for token in block.children or []
        if token.type == "link_open" and url_key(token.attrGet("href") or "")
    }


def test_commonmark_corpus_with_destinations_and_containers():
    md = MarkdownIt("commonmark").use(front_matter_plugin)
    matched = 0
    for example in json.loads(FIXTURE.read_text()):
        original = example["markdown"]
        absolute = original
        for before, after in [
            ("/uri", "https://example.org/uri"),
            ("/url", "https://example.org/url"),
            ("my_(url)", "https://example.org/my_(url)"),
        ]:
            absolute = absolute.replace(before, after)
        for body in (original, absolute):
            for source in (
                body,
                body.replace("\n", "\r\n"),
                "\n".join("> " + line for line in body.splitlines()) + "\n",
                "- " + body.replace("\n", "\n  "),
            ):
                expected = actual_links(md, source)
                links, warnings = citations(source)
                assert all("unsupported or invalid HTTP(S) URL" in w for w in warnings), (
                    example["example"],
                    source,
                    warnings,
                )
                assert {link.url for link in links} == expected, (example["example"], source)
                if links:
                    matched += 1
                    rewritten = source
                    replacement = "https://replacement.invalid/repaired"
                    for link in reversed(links):
                        target = f"<{replacement}>" if link.kind == "autolink" else replacement
                        rewritten = rewritten[: link.start] + target + rewritten[link.end :]
                    assert actual_links(md, rewritten) == {replacement}, (
                        example["example"],
                        source,
                        rewritten,
                    )
    assert matched > 200
