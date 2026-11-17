# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum (MIT; see licenses/bookmark-migration-MIT.txt)
"""Conservative identity keys; intentionally no tracking-parameter heuristics."""

import re
from urllib.parse import urlsplit

from .model import AuditError


def url_key(url: str, mode: str = "conservative") -> str:
    if not isinstance(url, str) or not url or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        raise AuditError("URL must be a non-empty string without whitespace or control characters (percent-encode them)")
    if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", url):
        raise AuditError(f"URL has no absolute scheme: {url!r}")
    if mode not in {"raw", "conservative"}:
        raise AuditError(f"unknown URL mode: {mode}")
    if not re.match(r"(?i)^https?:", url):
        return url  # file:, mailto:, bookmarks' other schemes remain byte-for-byte.
    try:
        parsed = urlsplit(url)
        if not parsed.netloc or not parsed.hostname:
            raise ValueError("HTTP(S) URL has no host")
        port = parsed.port
    except ValueError as exc:
        raise AuditError(f"invalid HTTP(S) URL {url!r}: {exc}") from exc
    match = re.match(r"(?i)^(https?)://([^/?#]*)(.*)$", url)
    if not match:
        raise AuditError(f"HTTP(S) URL needs //: {url!r}")
    scheme, authority, suffix = match.groups()
    if "\\" in authority:
        raise AuditError(f"HTTP(S) host must not contain a backslash: {url!r}")
    userinfo, sep, hostport = authority.rpartition("@")
    if not sep:
        hostport = authority
    if hostport.startswith("["):
        end = hostport.find("]")
        host, port_text = hostport[: end + 1], hostport[end + 1 :]
        if port_text and not re.fullmatch(r":\d+", port_text):
            raise AuditError(f"invalid URL port: {url!r}")
    else:
        host, sep_port, port_part = hostport.partition(":")
        port_text = (":" + port_part) if sep_port else ""
        if port_text and not re.fullmatch(r":\d+", port_text):
            raise AuditError(f"invalid URL port: {url!r}")
    scheme = scheme.lower()
    if mode == "raw":
        return url
    if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
        port_text = ""
    if not suffix or suffix.startswith(("?", "#")):
        suffix = "/" + suffix
    # IPv6 zone/interface identifiers may be case-sensitive; preserve the zone.
    address, zone_sep, zone = host.partition("%") if host.startswith("[") else (host, "", "")
    host_key = address.lower() + zone_sep + zone
    return scheme + "://" + ((userinfo + "@") if sep else "") + host_key + port_text + suffix
