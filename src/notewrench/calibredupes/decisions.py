"""Validate human keeper/donor selections against fresh candidate identities.

Only describe additions and unresolved metadata. Never mutate a library or
interpret a decision as an instruction to copy/delete/update anything.
"""

import hashlib
import json
from pathlib import Path

from .reader import ReviewError


def _library_identities(plan: dict) -> list[dict]:
    return [{key: library[key] for key in ("label", "database", "databaseSHA256")}
            for library in plan["libraries"]]


def _members(cluster: dict) -> list[dict]:
    return [{key: member[key] for key in ("ref", "memberSHA256")}
            for member in cluster["members"]]


def decision_template(plan: dict) -> dict:
    return {"schemaVersion": 1, "libraries": _library_identities(plan),
            "decisions": [{"clusterId": cluster["id"], "members": _members(cluster),
                           "keep": None, "formatSources": {}}
                          for cluster in plan["clusters"]]}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReviewError(f"duplicate JSON key in decisions: {key}")
        result[key] = value
    return result


def _nonfinite(value):
    raise ReviewError(f"non-finite JSON value in decisions: {value}")


def _shape(value, keys: set[str], location: str) -> None:
    if type(value) is not dict or set(value) != keys:
        raise ReviewError(f"invalid {location}: expected exactly {', '.join(sorted(keys))}")


def _validate(document: dict, plan: dict) -> None:
    _shape(document, {"schemaVersion", "libraries", "decisions"}, "decision document")
    if type(document["schemaVersion"]) is not int or document["schemaVersion"] != 1:
        raise ReviewError("unsupported decision schemaVersion; expected 1")
    if document["libraries"] != _library_identities(plan):
        raise ReviewError("stale or wrong library snapshot: labels, paths, order and database hashes must match")
    if type(document["decisions"]) is not list:
        raise ReviewError("decisions must be a list")
    clusters = {c["id"]: c for c in plan["clusters"]}
    seen = set()
    for index, decision in enumerate(document["decisions"]):
        location = f"decisions[{index}]"
        keys = {"clusterId", "members", "keep"}
        if type(decision) is dict and "formatSources" in decision:
            keys.add("formatSources")
        _shape(decision, keys, location)
        cluster_id = decision["clusterId"]
        if type(cluster_id) is not str or cluster_id not in clusters:
            raise ReviewError(f"{location}: unknown candidate clusterId")
        if cluster_id in seen:
            raise ReviewError(f"{location}: duplicate candidate decision")
        seen.add(cluster_id)
        members = decision["members"]
        if type(members) is not list:
            raise ReviewError(f"{location}: members must be a list")
        for member in members:
            _shape(member, {"ref", "memberSHA256"}, location + ".members")
            if any(type(value) is not str for value in member.values()):
                raise ReviewError(f"{location}: member ref and hash must be strings")
        # Canonical ordering is supplied by the template, but hand reordering
        # members is harmless. Exact length/content also refuses duplicates.
        if sorted(members, key=lambda m: m["ref"]) != _members(clusters[cluster_id]):
            raise ReviewError(f"{location}: stale or wrong candidate members/member hashes")
        if type(decision["keep"]) is not str or decision["keep"] not in {m["ref"] for m in members}:
            raise ReviewError(f"{location}: keep must select a candidate member; remove unselected decisions")
        cluster = clusters[cluster_id]
        by_ref = {m["ref"]: m for m in cluster["members"]}
        missing = set(cluster["formatUnion"]) - set(by_ref[decision["keep"]]["formats"])
        choices = decision.get("formatSources", {})
        if type(choices) is not dict:
            raise ReviewError(f"{location}.formatSources: expected an object mapping formats to donor refs")
        for fmt, ref in choices.items():
            if fmt not in missing:
                raise ReviewError(f"{location}.formatSources: {fmt!r} is not a keeper-missing format")
            if type(ref) is not str or ref not in by_ref or fmt not in by_ref[ref]["formats"]:
                raise ReviewError(f"{location}.formatSources: {fmt} donor must be a candidate member declaring that format")


def _metadata_review(cluster: dict) -> tuple[list[dict], list[str]]:
    members = cluster["members"]
    conflicts = []
    manual = {"edition-identity-unverified"}
    fields = (("title", "normalizedTitle", "title-conflict"),
              ("authors", "normalizedAuthors", "author-conflict"),
              ("languages", "languages", "language-conflict"),
              ("publicationDate", "publicationDate", "publication-date-conflict"),
              ("canonicalISBN13", "canonicalISBN13", "isbn-conflict"))
    for field, normalized, code in fields:
        values = []
        known = set()
        for member in members:
            value = member[normalized]
            if isinstance(value, list):
                value = tuple(sorted({v.strip().casefold() for v in value if v.strip()}))
            elif isinstance(value, str):
                value = value.strip()
            if value:
                known.add(value)
            values.append({"ref": member["ref"], "value": member[field]})
        if len(known) > 1:
            conflicts.append({"field": field, "code": code,
                              "values": values, "action": "manual-review"})
            manual.add(code)
            if field in ("title", "publicationDate", "canonicalISBN13"):
                manual.add("edition-evidence-conflict")
    if any(not m["languages"] or not all(v.strip() for v in m["languages"]) for m in members):
        manual.add("language-unknown")
    if any(not (m["publicationDate"] or "").strip() for m in members):
        manual.add("publication-date-unknown")
    if cluster["hasUnevidencedPairs"]:
        manual.add("candidate-has-unevidenced-pairs")
    # Also check same-library pairs excluded by a cross-library comparison.
    direct = {frozenset((e["a"], e["b"])) for e in cluster["pairs"]}
    if len(direct) < len(members) * (len(members) - 1) // 2:
        manual.add("candidate-has-unevidenced-pairs")
    if cluster["overlappingFormats"]:
        manual.add("overlapping-formats-retained-without-replacement")
    manual.update(cluster["conflicts"])
    return conflicts, sorted(manual)


def merge_plan(plan: dict, decision_path: Path) -> dict:
    try:
        path = decision_path.expanduser().resolve(strict=True)
    except RuntimeError as exc:
        raise ReviewError(f"cannot resolve decision input: {exc}") from exc
    raw = path.read_bytes()
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                              parse_constant=_nonfinite)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ReviewError(f"invalid decision JSON: {exc}") from exc
    _validate(document, plan)  # Validate everything before constructing any result.
    clusters = {c["id"]: c for c in plan["clusters"]}
    verified = {(r["ref"], r["format"]): r for r in plan["fileVerification"]["records"]}
    results = []
    for index, decision in enumerate(document["decisions"]):
        cluster = clusters[decision["clusterId"]]
        keeper = next(m for m in cluster["members"] if m["ref"] == decision["keep"])
        additions = []
        for fmt in sorted(set(cluster["formatUnion"]) - set(keeper["formats"])):
            sources = []
            for member in cluster["members"]:
                for record in member["formatRecords"]:
                    if record["format"] == fmt:
                        sources.append({"ref": member["ref"], "memberSHA256": member["memberSHA256"],
                                        "formatRecord": record,
                                        "verification": verified.get((member["ref"], fmt),
                                                                     {"status": "not-inspected"})})
            chosen_ref = decision.get("formatSources", {}).get(fmt)
            selected = (next(s for s in sources if s["ref"] == chosen_ref) if chosen_ref is not None
                        else sources[0] if len(sources) == 1 else None)
            status = "proposed" if selected is not None else "manual-source-selection"
            if selected is not None and selected["verification"]["status"] not in ("verified", "not-inspected"):
                status = "source-unavailable"
            additions.append({"format": fmt, "from": selected["ref"] if selected is not None else None,
                              "status": status,
                              "sources": sources})
        conflicts, manual = _metadata_review(cluster)
        if any(a["status"] == "manual-source-selection" for a in additions):
            manual = sorted({*manual, "format-source-selection-required"})
        if any(a["status"] == "source-unavailable" for a in additions):
            manual = sorted({*manual, "format-source-unavailable"})
        results.append({"decisionIndex": index, "inputDecision": decision,
                        "clusterId": cluster["id"], "keep": keeper["ref"],
                        "retainedFormats": keeper["formats"], "formatAdditions": additions,
                        "metadataConflicts": conflicts, "candidateConflicts": cluster["conflicts"],
                        "manualReview": manual})
    return {"schemaVersion": 1, "action": "plan-only",
            "decisionInput": {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest()},
            "decisions": results}
