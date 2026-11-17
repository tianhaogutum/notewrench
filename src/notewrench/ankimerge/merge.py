# SPDX-License-Identifier: MIT
# Copyright (c) 2026 tianhaogutum
"""Anki policies on top of csvdiff's keyed semantic field differences."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import chain

from csvdiff import diff_records

from . import FORMAT_VERSION as __version__  # plan.json tool_version and fingerprint input
from .text import InputError, MAX_FIELD_CHARS, Note, TextDeck


@dataclass
class MergeResult:
    plan: dict
    imports: list[Note]


def _deltas(base: TextDeck, other: TextDeck, id_field: str) -> dict[str, dict]:
    # Student-only fields are intentionally excluded from the course delta.
    records = lambda deck: (
        {name: note.fields[name] for name in base.fields}
        for note in deck.notes.values()
    )
    patch = diff_records(records(base), records(other), [id_field])
    return {change["key"][0]: change["fields"] for change in patch["changed"]}


def _snapshot(note: Note | None):
    return note.snapshot() if note is not None else None


def _issue(kind: str, message: str, **details) -> dict:
    return {"kind": kind, "message": message, **details}


def _fingerprint(decks: tuple[TextDeck, ...], id_field: str, protect: tuple[str, ...]) -> str:
    payload = {"version": __version__, "sources": [d.sha256 for d in decks],
               "fields": [d.fields for d in decks], "id_field": id_field,
               "protect": sorted(protect), "html": [d.html for d in decks],
               "parse": [{"delimiter": d.delimiter, "header_row": d.header_row, "defaults": d.defaults} for d in decks]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _validate(decks: tuple[TextDeck, ...], id_field: str, protect: tuple[str, ...]) -> None:
    base, teacher, student = decks
    if set(base.fields) != set(teacher.fields):
        raise InputError("Teacher field schema changed. Migrate the note type separately before merging text.")
    if not set(base.fields).issubset(student.fields):
        raise InputError("Student export is missing course fields. Export all fields of the same note type.")
    if id_field in protect or not set(protect).issubset(student.fields):
        raise InputError("Protected fields must exist and cannot include the stable course ID.")
    if len({d.html for d in decks}) != 1:
        raise InputError("HTML modes differ. Export all three with the same HTML setting.")
    types = {n.notetype for d in decks for n in d.notes.values() if n.notetype}
    if len(types) > 1:
        raise InputError("Multiple/different note types found. Merge one unchanged note type at a time.")
    old_guid_ids = {n.guid: key for key, n in base.notes.items() if n.guid}
    for key, note in teacher.notes.items():
        if note.guid and note.guid in old_guid_ids and old_guid_ids[note.guid] != key:
            raise InputError(f"Teacher GUID {note.guid!r} changed course ID from {old_guid_ids[note.guid]!r} to {key!r}. Course IDs must remain stable.")


def _resolutions(value: dict | None, fingerprint: str, all_ids: set[str], fields: set[str],
                 id_field: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) != {"fingerprint", "notes"}:
        raise InputError("Resolutions must contain exactly fingerprint and notes.")
    if value["fingerprint"] != fingerprint:
        raise InputError("Resolutions are stale or refer to different inputs/options. Generate a fresh preview.")
    entries = value["notes"]
    if not isinstance(entries, dict) or not set(entries).issubset(all_ids):
        raise InputError("Resolutions contain unknown course IDs, or notes is not an object.")
    for key, entry in entries.items():
        if not isinstance(entry, dict) or not set(entry).issubset({"action", "fields"}):
            raise InputError(f"{key!r}: resolution must contain action and/or fields.")
        if entry.get("action", "merge") not in ("merge", "skip", "add"):
            raise InputError(f"{key!r}: action must be merge, skip, or add.")
        chosen = entry.get("fields", {})
        if not isinstance(chosen, dict) or not set(chosen).issubset(fields - {id_field}):
            raise InputError(f"{key!r}: resolutions contain unknown fields or try to change the stable course ID.")
        for name, choice in chosen.items():
            if not isinstance(choice, dict) or len(choice) != 1:
                raise InputError(f"{key!r}/{name}: replace null with {{\"take\":\"student\"}}, teacher, base, or {{\"value\":\"...\"}}.")
            if "take" in choice and choice["take"] in ("student", "teacher", "base"):
                continue
            if "value" in choice and isinstance(choice["value"], str):
                if len(choice["value"]) <= MAX_FIELD_CHARS and "\x00" not in choice["value"] and "\x1f" not in choice["value"]:
                    continue
            raise InputError(f"{key!r}/{name}: invalid field resolution.")
    return entries


def merge(base: TextDeck, teacher: TextDeck, student: TextDeck, id_field: str,
          protect: tuple[str, ...] = (), resolutions: dict | None = None) -> MergeResult:
    decks = (base, teacher, student)
    _validate(decks, id_field, protect)
    fingerprint = _fingerprint(decks, id_field, protect)
    ids = set(base.notes) | set(teacher.notes) | set(student.notes)
    choices = _resolutions(resolutions, fingerprint, ids, set(student.fields), id_field)
    teacher_delta, student_delta = _deltas(base, teacher, id_field), _deltas(base, student, id_field)
    personal_fields = set(student.fields) - set(base.fields)
    records: list[dict] = []
    candidate_notes: dict[str, Note] = {}
    first = student.fields[0]
    for key in sorted(ids):
        old, new, local = (d.notes.get(key) for d in decks)
        resolution = choices.get(key, {})
        action = resolution.get("action", "merge")
        issues: list[dict] = []
        decisions: list[dict] = []
        record = {"id": key, "base": _snapshot(old), "teacher": _snapshot(new),
                  "student": _snapshot(local), "decisions": decisions, "issues": issues,
                  "action": "keep", "status": "unchanged", "merged": None,
                  "first_field_changed": False, "identity": None, "importable": False}
        records.append(record)
        if action == "skip":
            record["status"] = "skipped_by_review"
            continue
        if action == "add" and (local is not None or new is None):
            raise InputError(f"{key!r}: add is only valid for a teacher note missing from the student export.")
        if new is None:
            record["status"] = "retained_teacher_deleted" if local and old else (
                "retained_student_only" if local else "absent_both")
            record["merged"] = _snapshot(local)
            if resolution.get("fields"):
                raise InputError(f"{key!r}: cannot resolve fields without a teacher note.")
            continue
        if old is not None and local is None and action != "add":
            record["status"] = "missing_student"
            record["action"] = "withheld"
            issues.append(_issue("missing_student", "Not recreated: this may be a student deletion or an incomplete export. Review add/skip."))
            continue
        template = local or new
        values = {name: template.fields.get(name, "") for name in student.fields}
        if local is None:
            record["status"] = "added" if old is None else "restored_by_review"
            record["action"] = "add"
        else:
            record["action"] = "update"
            record["status"] = "merged"
        for name in base.fields:
            old_value = old.fields[name] if old else None
            new_value = new.fields[name]
            local_value = local.fields[name] if local else None
            source = "student"
            if name in protect and local is not None:
                source = "protected_student"
            elif local is None:
                values[name], source = new_value, "teacher"
            elif old is None:
                if local_value != new_value:
                    issues.append(_issue("concurrent_add", "Same course ID added independently with different content.",
                                         field=name, base=None, teacher=new_value, student=local_value))
            elif name in teacher_delta.get(key, {}):
                if name not in student_delta.get(key, {}) or local_value == new_value:
                    values[name], source = new_value, "teacher" if local_value != new_value else "both"
                else:
                    issues.append(_issue("field_conflict", "Teacher and student changed the same field differently.",
                                         field=name, base=old_value, teacher=new_value, student=local_value))
            if old_value != new_value or local_value != old_value:
                decisions.append({"field": name, "source": source, "base": old_value,
                                  "teacher": new_value, "student": local_value, "value": values[name]})
        for name, choice in resolution.get("fields", {}).items():
            if name in protect:
                raise InputError(f"{key!r}/{name}: remove --protect before overriding a protected field.")
            if "value" in choice:
                value = choice["value"]
            else:
                origin = {"base": old, "teacher": new, "student": local}[choice["take"]]
                if origin is None or name not in origin.fields:
                    raise InputError(f"{key!r}/{name}: chosen source has no value.")
                value = origin.fields[name]
            values[name] = value
            issues[:] = [item for item in issues if item.get("field") != name]
            decisions.append({"field": name, "source": "review", "value": value})
        # The student's GUID is the identity of an existing note, regardless
        # of GUIDs in the teacher's independently created collection.
        merged = Note(values, template.guid, template.notetype, template.deck,
                      sorted(set(new.tags) | set(local.tags if local else ())))
        record["merged"] = merged.snapshot()
        record["first_field_changed"] = local is not None and values[first] != local.fields[first]
        record["identity"] = "guid" if merged.guid else "first_field"
        if values[first] == "":
            issues.append(_issue("empty_first_field", "Anki text import refuses an empty first field, including GUID updates. Keep a nonempty student value or review a replacement.", field=first))
        if not merged.guid:
            if record["first_field_changed"]:
                issues.append(_issue("first_field_changed_without_guid", "Export the student notes with GUIDs, or keep the student's first field. Import would risk a duplicate.", field=first))
            if re.search(r"[<&]", values[first]):
                issues.append(_issue("html_first_field_without_guid", "HTML/entity matching needs a student GUID; exact text is insufficient for a safe preflight.", field=first))
        if issues:
            record["action"], record["status"] = "withheld", "needs_review"
        elif local is not None and merged.snapshot() == local.snapshot():
            has_personal_values = any(local.fields[name] for name in personal_fields)
            retained_edit = old is not None and (bool(student_delta.get(key)) or has_personal_values)
            record["action"], record["status"] = "keep", "retained_local" if retained_edit else "unchanged"
        else:
            candidate_notes[key] = merged

    # Check the complete projected student snapshot, including notes that are
    # not being imported. A newly added course ID must not hijack an old GUID.
    guid_owners: dict[str, list[str]] = defaultdict(list)
    first_owners: dict[str, list[str]] = defaultdict(list)
    original_first_owners: dict[str, list[str]] = defaultdict(list)
    has_html_identity = any(re.search(r"[<&]", note.fields[first])
                            for note in chain(student.notes.values(), candidate_notes.values()))
    for key, note in student.notes.items():
        original_first_owners[unicodedata.normalize("NFC", note.fields[first])].append(key)
    for key in sorted(set(student.notes) | set(candidate_notes)):
        note = candidate_notes.get(key) or student.notes[key]
        if note.guid:
            guid_owners[note.guid].append(key)
        first_owners[unicodedata.normalize("NFC", note.fields[first])].append(key)
    by_id = {record["id"]: record for record in records}
    for key, note in list(candidate_notes.items()):
        collisions = []
        if note.guid and len(guid_owners[note.guid]) > 1:
            collisions.append(_issue("guid_collision", "GUID belongs to more than one course ID.", ids=guid_owners[note.guid]))
        if not note.guid:
            # Never depend on a different import row running first to free a
            # matching field. It may be withheld, or Anki may process it later.
            match_key = unicodedata.normalize("NFC", note.fields[first])
            owners = set(first_owners[match_key]) | set(original_first_owners[match_key])
            if owners - {key}:
                collisions.append(_issue("first_field_collision", "First field matches another current/projected course ID; import order must not determine identity.", field=first, ids=sorted(owners)))
            if has_html_identity:
                collisions.append(_issue("html_identity_ambiguity", "The current or proposed collection contains HTML/entity first fields. Export GUIDs to avoid Anki-normalized matching ambiguity."))
        if collisions:
            record = by_id[key]
            record["issues"].extend(collisions)
            record["action"], record["status"] = "withheld", "needs_review"
            del candidate_notes[key]
    imports = []
    for record in records:
        if record["id"] in candidate_notes:
            record["importable"] = True
            imports.append(candidate_notes[record["id"]])
    counts = dict(sorted(Counter(record["status"] for record in records).items()))
    plan = {
        "schema_version": 1, "tool_version": __version__, "fingerprint": fingerprint,
        "sources": {name: {"path": str(deck.path), "sha256": deck.sha256,
                           "fields": list(deck.fields), "notes": len(deck.notes),
                           "delimiter": deck.delimiter, "header_row": deck.header_row,
                           "html": deck.html, "defaults": deck.defaults, "headers": deck.headers}
                    for name, deck in zip(("old", "new", "student"), decks)},
        "id_field": id_field, "protected_fields": list(protect),
        "output_fields": list(student.fields), "html": student.html,
        "counts": counts, "import_count": len(imports),
        "unresolved_count": sum(bool(record["issues"]) for record in records),
        "assumptions": [
            "Student input is a complete export of the intended note type/course scope.",
            "Course IDs are stable and unique in all inputs.",
            "Import into the same collection using Update existing notes and the same note type.",
            "Teacher-deleted notes remain in the student collection; text imports do not delete.",
            "Tags are the union of teacher and student tags; tag removals are not applied.",
        ],
        "notes": records,
    }
    return MergeResult(plan, imports)
