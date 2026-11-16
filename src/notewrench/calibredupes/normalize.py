"""Conservative, locale-independent matching primitives."""

import unicodedata


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = "".join(" " if unicodedata.category(c).startswith("P") else c for c in value)
    return " ".join(value.split())


def known_text(value: str) -> str:
    normalized = normalize_text(value)
    return "" if normalized == "unknown" else normalized


def isbn13(value: str) -> str | None:
    """Validate one ISBN, returning canonical ISBN13; never repair a checksum.

    Accept whitespace and dash separators, ASCII digits and an ISBN10 X.
    Prefixes/URLs, multiple ISBNs, and other punctuation are invalid.
    """
    compact = "".join(c for c in value.upper() if not c.isspace()
                      and unicodedata.category(c) != "Pd")
    if len(compact) == 10:
        if not all(c in "0123456789" for c in compact[:9]):
            return None
        if compact[-1] not in "0123456789X":
            return None
        digits = [10 if c == "X" else int(c) for c in compact]
        if sum((10 - i) * n for i, n in enumerate(digits)) % 11:
            return None
        prefix = "978" + compact[:9]
        check = (-sum(int(c) * (1 if i % 2 == 0 else 3)
                      for i, c in enumerate(prefix))) % 10
        return prefix + str(check)
    if len(compact) == 13 and compact.startswith(("978", "979")):
        if all(c in "0123456789" for c in compact):
            if sum(int(c) * (1 if i % 2 == 0 else 3)
                   for i, c in enumerate(compact)) % 10 == 0:
                return compact
    return None


def isbn10(value: str) -> str | None:
    """Canonical ISBN10 equivalent, if a valid ISBN has a 978 prefix."""
    canonical = isbn13(value)
    if canonical is None or not canonical.startswith("978"):
        return None
    body = canonical[3:12]
    check = (-sum((10 - i) * int(c) for i, c in enumerate(body))) % 11
    return body + ("X" if check == 10 else str(check))
