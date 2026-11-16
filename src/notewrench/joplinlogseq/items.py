# SPDX-License-Identifier: AGPL-3.0-only
"""Original parser for Joplin's title/body + terminal property record format."""

from dataclasses import dataclass
import re

from .archive import ConversionError

ID = re.compile(r"[a-fA-F0-9]{32}\Z")
TYPES = {1: "note", 2: "folder", 4: "resource", 5: "tag", 6: "note_tag"}


@dataclass(frozen=True)
class Item:
    id: str
    type: int
    title: str
    body: str
    metadata: dict[str, str]
    member: str


def parse_item(name: str, data: bytes) -> Item:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConversionError(f"Item is not UTF-8: {name}") from exc
    if "\x00" in text:
        raise ConversionError(f"NUL in item: {name}")
    # Normalize CRLF framing, but never split arbitrary body lines on colons.
    text = text.replace("\r\n", "\n").rstrip("\n")
    prefix, separator, tail = text.rpartition("\n\n")
    if not separator:
        prefix, tail = "", text
    metadata = {}
    for line in tail.split("\n"):
        match = re.fullmatch(r"([a-zA-Z_][a-zA-Z0-9_]*):[ \t]*(.*)", line)
        if not match:
            raise ConversionError(f"Malformed terminal metadata in {name}: {line!r}")
        key, value = match.groups()
        if key in metadata:
            raise ConversionError(f"Duplicate metadata key {key} in {name}")
        metadata[key] = value.strip()
    item_id = metadata.get("id", "")
    if not ID.fullmatch(item_id) or item_id.lower() != name[:-3].lower():
        raise ConversionError(f"Item id does not match filename: {name}")
    raw_type = metadata.get("type_", "")
    if not re.fullmatch(r"[0-9]{1,3}", raw_type):
        raise ConversionError(f"Missing or malformed type_ in {name}")
    item_type = int(raw_type)
    if item_type not in TYPES:
        raise ConversionError(f"Unsupported item type_ {item_type} in {name}")
    for key in ("encryption_applied", "encryption_blob_encrypted", "is_locked"):
        if metadata.get(key, "") not in ("", "0", "1"):
            raise ConversionError(f"Invalid {key} in {name}")
        if metadata.get(key) == "1":
            raise ConversionError(f"Encrypted or locked item ({key}): {name}")
    if metadata.get("encryption_cipher_text"):
        raise ConversionError(f"Encrypted item: {name}")
    for key in ("parent_id", "note_id", "tag_id"):
        if metadata.get(key) and not ID.fullmatch(metadata[key]):
            raise ConversionError(f"Invalid {key} in {name}")
    title, body = "", ""
    if separator:
        title, has_body, body = prefix.partition("\n\n")
        if "\n" in title or "\r" in title:
            raise ConversionError(f"Unsupported multiline title: {name}")
        if item_type != 1 and (has_body or body):
            raise ConversionError(f"Unexpected body in non-note item: {name}")
    if item_type == 6 and title:
        raise ConversionError(f"Unexpected title in note_tag: {name}")
    if item_type == 1:
        for key in ("is_todo", "markup_language"):
            allowed = ("", "0", "1") if key == "is_todo" else ("", "1")
            if metadata.get(key, "") not in allowed:
                raise ConversionError(f"Unsupported {key} in {name} (only Markdown notes supported)")
        completed = metadata.get("todo_completed", "0") or "0"
        if not re.fullmatch(r"[0-9]{1,20}", completed):
            raise ConversionError(f"Invalid todo_completed timestamp in {name}")
    return Item(item_id.lower(), item_type, title, body, metadata, name)
