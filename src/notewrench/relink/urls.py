# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum
"""Conservative URL keys: keep distinctions an archive may care about."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from urllib.parse import quote, urlsplit, urlunsplit

import idna

_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")


@lru_cache(maxsize=4096)
def _host_ascii(host: str) -> str:
    return idna.encode(host, uts46=True, transitional=False).decode("ascii")


def _percent(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        char = chr(int(match[0][1:], 16))
        return char if char in _UNRESERVED else match[0].upper()

    return re.sub(r"%[0-9a-fA-F]{2}", replace, value)


def url_key(url: str) -> str | None:
    """RFC-equivalent normalization only; fragments identify locations within a page."""
    try:
        if re.search(r"[\x00-\x1f\x7f]", url):
            return None
        parts = urlsplit(url.strip())
        scheme = parts.scheme.lower()
        if scheme not in {"http", "https"} or not parts.hostname:
            return None
        if parts.username is not None or parts.password is not None:
            return None
        if re.search(r"[\s\\%]", parts.hostname):
            return None
        host = parts.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        else:
            host = _host_ascii(host)
        port = parts.port
        if port is not None and (scheme, port) not in {("http", 80), ("https", 443)}:
            host += f":{port}"
        path = quote(_percent(parts.path or "/"), safe="/%:@!$&'()*+,;=-._~")
        query = quote(_percent(parts.query), safe="/%?:@!$&'()*+,;=-._~[]")
        result = urlunsplit((scheme, host, path, query, ""))
        # An empty query differs from an absent query under RFC 3986.
        if not query and "?" in url.split("#", 1)[0]:
            result += "?"
        return result
    except (ValueError, UnicodeError):
        return None


def normalized_text(text: str) -> str:
    """Ignore presentation whitespace and case, not words or punctuation."""
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def useful_quote(text: str) -> bool:
    normalized = normalized_text(text)
    letters = sum(c.isalnum() for c in normalized)
    cjk = sum("\u3400" <= c <= "\u9fff" for c in normalized)
    return letters >= 24 or cjk >= 12
