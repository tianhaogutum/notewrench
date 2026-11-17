# SPDX-License-Identifier: MIT
# Copyright (c) 2019 Sascha Ißbrücker (linkding; see licenses/linkding-MIT.txt)
"""Vendored linkding exporter, MIT (c) 2019 Sascha Ißbrücker.

Source: sissbruecker/linkding@27b7303baf41bb28babc610ac8eaa486e1ddfab5.
Changes: remove Django model dependency; escape URL attribute; allow raw
date/state overrides for browser exports that lack linkding's model fields.
See THIRD_PARTY.md and licenses/linkding-MIT.txt for the complete license.
"""

import html

from typing import Any

Bookmark = Any  # Adapter objects expose the same exporter attributes.

BookmarkDocument = list[str]


def export_netscape_html(bookmarks: list[Bookmark]):
    doc = []
    append_header(doc)
    append_list_start(doc)
    [append_bookmark(doc, bookmark) for bookmark in bookmarks]
    append_list_end(doc)

    return "\n\r".join(doc)


def append_header(doc: BookmarkDocument):
    doc.append("<!DOCTYPE NETSCAPE-Bookmark-file-1>")
    doc.append('<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">')
    doc.append("<TITLE>Bookmarks</TITLE>")
    doc.append("<H1>Bookmarks</H1>")


def append_list_start(doc: BookmarkDocument):
    doc.append("<DL><p>")


def append_bookmark(doc: BookmarkDocument, bookmark: Bookmark):
    url = html.escape(bookmark.url, quote=True)
    title = html.escape(bookmark.resolved_title or "")
    desc = html.escape(bookmark.resolved_description or "")
    if bookmark.notes:
        desc += f"[linkding-notes]{html.escape(bookmark.notes)}[/linkding-notes]"
    tag_names = bookmark.tag_names
    if bookmark.is_archived:
        tag_names.append("linkding:bookmarks.archived")
    tags = ",".join(html.escape(tag) for tag in tag_names)
    toread = "1" if bookmark.unread else "0"
    private = "0" if bookmark.shared else "1"
    added = int(bookmark.date_added.timestamp())
    modified = int(bookmark.date_modified.timestamp())

    attributes = {
        "HREF": url, "ADD_DATE": str(added), "LAST_MODIFIED": str(modified),
        "PRIVATE": private, "TOREAD": toread, "TAGS": tags,
    }
    for name, value in getattr(bookmark, "export_attributes", {}).items():
        if name not in {"ADD_DATE", "LAST_MODIFIED", "PRIVATE", "TOREAD", "DATA-RECEIPT"}:
            raise ValueError("Unsupported export attribute override")
        if value is None:
            attributes.pop(name, None)
        else:
            attributes[name] = html.escape(value, quote=True)
    encoded = " ".join(f'{name}="{value}"' for name, value in attributes.items())
    doc.append(f"<DT><A {encoded}>{title}</A>")

    if desc:
        doc.append(f"<DD>{desc}")


def append_list_end(doc: BookmarkDocument):
    doc.append("</DL><p>")
