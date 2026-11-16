"""Indexed pair evidence and connected review groups (never merge identities)."""

from collections import defaultdict
from itertools import combinations, product

from .reader import Book, Library, ReviewError
from .files import content_evidence, validate_verification_budgets, verify_libraries
from .decisions import decision_template


def pair_evidence(a: Book, b: Book) -> dict | None:
    shared = a.isbns & b.isbns
    title_equal = bool(a.title_key) and a.title_key == b.title_key
    authors_equal = (a.authors_complete and b.authors_complete
                     and a.author_key == b.author_key)
    missing_author = not a.authors_complete or not b.authors_complete
    if not shared and not (title_equal and (authors_equal or missing_author)):
        return None
    evidence, conflicts = [], []
    if shared:
        raw_a = {i["value"] for i in a.identifiers if i["type"].casefold() == "isbn"}
        raw_b = {i["value"] for i in b.identifiers if i["type"].casefold() == "isbn"}
        evidence.append("isbn-exact" if raw_a & raw_b else "isbn-equivalent")
    if title_equal:
        evidence.append("title-normalized")
    elif a.title_key and b.title_key:
        conflicts.append("title-conflict")
    else:
        conflicts.append("missing-title")
    if authors_equal:
        evidence.append("author-normalized")
    elif missing_author:
        conflicts.append("missing-author")
    else:
        conflicts.append("author-conflict")
    if a.isbns and b.isbns and not shared:
        conflicts.append("isbn-conflict")
    if a.invalid_isbns or b.invalid_isbns:
        conflicts.append("invalid-isbn")
    strong = bool(shared) and title_equal and authors_equal and not conflicts
    return {"a": a.key, "b": b.key, "level": "strong" if strong else "candidate",
            "matchEvidence": evidence, "sharedISBN13": sorted(shared),
            "conflicts": conflicts, "action": "review"}


def _validate_max_pairs(max_pairs: int) -> None:
    if type(max_pairs) is not int or max_pairs < 1:
        raise ReviewError("--max-pairs must be a positive integer")


def find_pairs(books: list[Book], *, cross_library: bool,
               max_pairs: int = 100_000) -> tuple[list[dict], int]:
    """Block on ISBN or normalized title, then check author/conflict evidence.

    A hard budget aborts rather than truncating an ambiguous, quadratic bucket.
    """
    _validate_max_pairs(max_pairs)
    if type(cross_library) is not bool:
        raise ReviewError("cross_library must be a boolean")
    indexes = defaultdict(list)
    for i, book in enumerate(books):
        if book.title_key:
            indexes[("title", book.title_key)].append(i)
        for isbn in sorted(book.isbns):
            indexes[("isbn", isbn)].append(i)
    candidates = set()
    for members in indexes.values():
        if cross_library:
            left = [i for i in members if books[i].library == "A"]
            right = [i for i in members if books[i].library == "B"]
            pairs = product(left, right)
        else:
            pairs = combinations(members, 2)
        for a, b in pairs:
            candidates.add((min(a, b), max(a, b)))
            if len(candidates) > max_pairs:
                raise ReviewError(f"pair budget exceeded ({max_pairs}); no partial report; "
                                  "raise --max-pairs explicitly after assessing memory/output size")
    result = []
    for a, b in sorted(candidates):
        edge = pair_evidence(books[a], books[b])
        if edge:
            result.append(edge)
    return result, len(candidates)


def make_plan(libraries: list[Library], *, max_pairs: int = 100_000,
              verify_files: bool = False, max_total_file_bytes: int | None = None,
              max_file_records: int | None = None) -> dict:
    # Validate caller options before database stat, book traversal or matching.
    _validate_max_pairs(max_pairs)
    validate_verification_budgets(enabled=verify_files,
                                  max_total_file_bytes=max_total_file_bytes,
                                  max_file_records=max_file_records)
    if len(libraries) not in (1, 2) or [x.label for x in libraries] != ["A", "B"][:len(libraries)]:
        raise ReviewError("provide one or two libraries with labels A and B")
    if len(libraries) == 2 and libraries[0].database.samefile(libraries[1].database):
        raise ReviewError("the two libraries refer to the same database")
    books = [book for lib in libraries for book in lib.books]
    by_key = {book.key: book for book in books}
    edges, examined = find_pairs(books, cross_library=len(libraries) == 2, max_pairs=max_pairs)
    verification = verify_libraries(libraries, enabled=verify_files,
                                    max_total_file_bytes=max_total_file_bytes,
                                    max_file_records=max_file_records)
    file_records = {(r["ref"], r["format"]): r for r in verification["records"]}
    for edge in edges:
        edge["contentEvidence"] = content_evidence(by_key[edge["a"]], by_key[edge["b"]],
                                                   file_records, enabled=verify_files)
    parents = {key: key for key in by_key}

    def root(key):
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for edge in edges:
        parents[root(edge["b"])] = root(edge["a"])
    grouped_edges = defaultdict(list)
    for edge in edges:
        grouped_edges[root(edge["a"])].append(edge)
    clusters = []
    for group in grouped_edges.values():
        keys = sorted({edge[side] for edge in group for side in ("a", "b")})
        members = [by_key[key] for key in keys]
        union = set().union(*(book.formats for book in members))
        formats = {lib.label: sorted(set().union(*(b.formats for b in members
                                                  if b.library == lib.label))) for lib in libraries}
        conflicts = {c for edge in group for c in edge["conflicts"]}
        if len(set().union(*(book.isbns for book in members))) > 1:
            conflicts.add("cluster-isbn-conflict")
        if len({b.title_key for b in members if b.title_key}) > 1:
            conflicts.add("cluster-title-conflict")
        if len({b.author_key for b in members if b.author_key}) > 1:
            conflicts.add("cluster-author-conflict")
        metadata_strong = all(e["level"] == "strong" for e in group) and not conflicts
        overlap = sorted(fmt for fmt in union if sum(fmt in b.formats for b in members) > 1)
        for fmt in overlap:
            checks = [file_records.get((b.key, fmt), {}) for b in members if fmt in b.formats]
            if any(r.get("status") != "verified" for r in checks):
                conflicts.add("overlapping-format-content-unverified")
            if len({(r["bytes"], r["sha256"]) for r in checks if r.get("status") == "verified"}) > 1:
                conflicts.add("overlapping-format-bytes-differ")
        # Only directly evidenced edges are identities under review. Never infer
        # that every member of a connected component matches every other member.
        possible = (sum(b.library == "A" for b in members) * sum(b.library == "B" for b in members)
                    if len(libraries) == 2 else len(members) * (len(members) - 1) // 2)
        clusters.append({"id": f"cluster-{len(clusters) + 1:04d}", "action": "review",
                         "level": "strong" if metadata_strong else "candidate",
                         "members": [b.as_dict() for b in members], "pairs": group,
                         "hasUnevidencedPairs": len(group) < possible,
                         "formats": formats, "formatUnion": sorted(union),
                         "missingByLibrary": {key: sorted(union - set(fmts)) for key, fmts in formats.items()},
                         "missingByBook": {b.key: sorted(union - b.formats) for b in members},
                         "overlappingFormats": overlap, "conflicts": sorted(conflicts)})
    diagnostics = []
    for book in books:
        issues = []
        if book.invalid_isbns:
            issues.append("invalid-isbn")
        if not book.authors_complete:
            issues.append("missing-author")
        if not book.title_key:
            issues.append("missing-title")
        if issues:
            diagnostics.append({"ref": book.key, "issues": issues, "invalidISBN": book.invalid_isbns})
    clustered = {m["ref"] for c in clusters for m in c["members"]}
    plan = {"schemaVersion": 1, "tool": "calibre-dupes", "action": "review",
            "scope": "cross-library" if len(libraries) == 2 else "within-library",
            "libraries": [lib.as_dict() for lib in libraries],
            "policy": {"databaseAccess": "mode=ro, query_only, snapshot per library",
                       "formatEvidence": ("declared paths inspected read-only; observed bytes and SHA256; matrices remain database-declared"
                                          if verify_files else "database-declared; files and content not inspected"),
                       "contentMatch": "all shared declared formats have equal observed size and SHA256; not edition identity or ebook validity",
                       "clusters": "connected review groups; only listed pairs carry evidence",
                       "strong": "valid shared ISBN13, equal known normalized title and complete author set, no identity conflicts",
                       "weak": "candidate only; no automatic merge or executable instructions"},
            "summary": {"books": len(books), "examinedPairs": examined, "matchedPairs": len(edges),
                        "strongPairs": sum(e["level"] == "strong" for e in edges),
                        "candidatePairs": sum(e["level"] == "candidate" for e in edges),
                        "clusters": len(clusters), "unclusteredBooks": len(books) - len(clustered)},
            "diagnostics": diagnostics, "fileVerification": verification, "clusters": clusters}

    plan["decisionTemplate"] = decision_template(plan)
    return plan
