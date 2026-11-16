"""Render inert Markdown, Logseq file-graph blocks and lossless CSV cells.

SPDX-License-Identifier: AGPL-3.0-only
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any
from urllib.parse import quote

from . import __version__
from .core import Article, Export, clickable, json_encode

FORMATS = ("all", "markdown", "logseq", "csv")
CSV_FIELDS = ("source_id", "annotation_id", "identity", "anchor", "block_id", "title",
              "url", "source_order", "annotation_order", "quote", "comment", "ranges_json")


def json_text(value: Any) -> str:
    return json_encode(value, pretty=True) + "\n"


def literal(value: str) -> str:
    # Escape markup and Logseq properties/page refs/macros; raw strings live in JSON/CSV.
    return re.sub(r"([\\`*_{}\[\]<>()!#|~:+\-])", r"\\\1", value)


def lines(value: str) -> list[str]:
    return value.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def title(article: Article) -> str:
    return literal(" ".join(lines(article.title)))


def source_link(article: Article) -> str:
    url = article.raw.get("url")
    if clickable(url):
        return "[Source](<" + quote(url, safe=":/?#@!$&'*+,;=%-._~") + ">)"
    return "Source URL unavailable as an HTTP(S) link; see provenance.json"


def fence(value: str, indent: str = "") -> list[str]:
    runs = re.findall(r"`+", value)
    marker = "`" * max(3, max((len(s) + 1 for s in runs), default=0))
    return [indent + marker + "json", *(indent + s for s in value.rstrip("\n").split("\n")),
            indent + marker]


def markdown(article: Article) -> str:
    out = ["# " + title(article), "", source_link(article), "",
           f"Source ID: {article.source_id}", ""]
    if article.namespace is not None:
        out += ["Source namespace: " + literal(article.namespace), ""]
    if not article.annotations:
        out += ["No annotations.", ""]
    for annotation in article.annotations:
        raw = annotation.raw
        label = annotation.annotation_id or annotation.identity
        out += [f'<a id="{annotation.anchor}"></a>', "", "## Annotation " + label, ""]
        out += ["> " + literal(s) for s in lines(raw["quote"])] if raw["quote"] else ["> [Empty quote supplied]"]
        out += ["", "Comment:", ""]
        out += [literal(s) for s in lines(raw["text"])]
        out += ["", "Ranges (opaque JSON):", "", *fence(json_text(raw["ranges"])), ""]
    return "\n".join(out)


def logseq(article: Article) -> str:
    out = ["- # " + title(article), "  bagnotes-source-id:: " + article.source_id]
    if article.namespace is not None:
        out += ["  bagnotes-namespace:: " + literal(article.namespace)]
    out += ["  - " + source_link(article)]
    if not article.annotations:
        out += ["  - No annotations."]
    for annotation in article.annotations:
        raw = annotation.raw
        label = annotation.annotation_id or annotation.identity
        out += ["- Annotation " + label, "  id:: " + annotation.block_id,
                "  bagnotes-anchor:: " + annotation.anchor, "  - Quote"]
        out += ["    > " + literal(s) for s in lines(raw["quote"])] if raw["quote"] else ["    > [Empty quote supplied]"]
        out += ["  - Comment"]
        out += ["    > " + literal(s) for s in lines(raw["text"])]
        out += ["  - Ranges (opaque JSON)", *fence(json_text(raw["ranges"]), "    ")]
    return "\n".join(out) + "\n"


def csv_text(export: Export) -> str:
    handle = io.StringIO(newline="")
    fields = ("namespace", "input_order", *CSV_FIELDS) if export.inputs is not None else CSV_FIELDS
    writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\r\n")
    writer.writeheader()
    for article in export.articles:
        for annotation in article.annotations:
            position = annotation.occurrences[0]
            row = dict(
                source_id=article.source_id, annotation_id=annotation.annotation_id or "",
                identity=annotation.identity, anchor=annotation.anchor, block_id=annotation.block_id,
                title=article.raw.get("title") or "", url=article.raw.get("url") or "",
                source_order=position["source_order"], annotation_order=position["annotation_order"],
                quote=annotation.raw["quote"], comment=annotation.raw["text"],
                ranges_json=json_encode(annotation.raw["ranges"]))
            if export.inputs is not None:
                row.update(namespace=article.namespace, input_order=position["input_order"])
            writer.writerow(row)
    return handle.getvalue()


def input_metadata(export: Export) -> dict:
    if export.inputs is not None:
        return dict(schema_version=2, inputs=export.inputs)
    return dict(schema_version=1, input_sha256=export.input_sha256)


def provenance(export: Export) -> dict:
    articles = [
        dict(source_id=article.source_id, source_order=article.source_order,
             source_occurrences=article.occurrences,
             metadata={k: v for k, v in article.raw.items() if k != "content"},
             omitted_fields=["content"] if "content" in article.raw else [],
             annotations=[dict(identity=a.identity, anchor=a.anchor, block_id=a.block_id,
                               raw=a.raw, occurrences=a.occurrences) for a in article.annotations])
        for article in export.articles]
    if export.inputs is not None:
        for item, article in zip(articles, export.articles):
            item.update(namespace=article.namespace, input_order=article.input_order,
                        file_stem=article.file_stem)
    return dict(**input_metadata(export), articles=articles)


def render(export: Export, format_name: str = "all") -> tuple[dict[str, bytes], dict]:
    if format_name not in FORMATS:
        raise ValueError(f"Unsupported output format: {format_name}")
    files = {}
    for article in export.articles:
        name = article.file_stem + ".md"
        if format_name in ("all", "markdown"):
            files["markdown/" + name] = markdown(article).encode("utf-8")
        if format_name in ("all", "logseq"):
            files["logseq/" + name] = logseq(article).encode("utf-8")
    if format_name in ("all", "csv"):
        files["annotations.csv"] = csv_text(export).encode("utf-8")
    files["provenance.json"] = json_text(provenance(export)).encode("utf-8")
    order = "First source occurrence, then first annotation occurrence; zero-based positions"
    if export.inputs is not None:
        order = "Input order, then first article occurrence within namespace, then first annotation occurrence; zero-based positions"
    report = dict(schema_version=1, tool="bagnotes", version=__version__, status="ok", format=format_name)
    report.update(input_metadata(export))
    report.update(order=order, counts=export.counts, warnings=export.warnings,
                  files=sorted([*files, "report.json"]))
    files["report.json"] = json_text(report).encode("utf-8")
    return files, report
