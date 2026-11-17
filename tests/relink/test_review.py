from bs4 import BeautifulSoup
from markdown_it import MarkdownIt
from .test_workflow import setup_index

from notewrench.relink.workflow import review_markdown, scan_notes


def test_review_shows_evidence_excerpt_and_quote_disagreement(tmp_path):
    _, note, db = setup_index(tmp_path)
    plan = scan_notes(
        db,
        [note],
        {
            "https://old.example/story": (
                "A sufficiently distinctive quotation missing from this copy."
            ),
        },
    )
    text = review_markdown(plan)
    assert "Open local copy" in text
    assert "Recorded original URL" in text
    assert "NOT FOUND in body" in text
    assert "article body." in text


def test_review_treats_archive_text_as_literal_content(tmp_path):
    _, note, db = setup_index(tmp_path)
    plan = scan_notes(db, [note])
    candidate = plan["links"][0]["candidates"][0]
    candidate["title"] = '` <script>alert("title")</script> `'
    candidate["preview"] = '<img src="https://tracking.example/pixel"> **invented emphasis**'
    rendered = MarkdownIt("commonmark").render(review_markdown(plan))
    soup = BeautifulSoup(rendered, "html.parser")
    assert not soup.find_all(["script", "img"])
    assert "<script>" in soup.get_text()
    assert "**invented emphasis**" in soup.get_text()
