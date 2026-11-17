import copy

import pytest
from hypothesis import given, strategies as st

from notewrench.ankimerge.merge import merge
from notewrench.ankimerge.text import InputError


def test_independent_edits_and_personal_fields(deck):
    old = deck([["1", "question", "answer"]])
    new = deck([["1", "corrected", "answer"]])
    student = deck([["1", "question", "my answer", "my lecture notes"]],
                   names=("CourseID", "Front", "Back", "Personal"), guids={"1": "studentGuid"})
    result = merge(old, new, student, "CourseID")
    assert result.imports[0].fields == {"CourseID": "1", "Front": "corrected", "Back": "my answer", "Personal": "my lecture notes"}
    assert result.imports[0].guid == "studentGuid"
    assert result.plan["unresolved_count"] == 0


@pytest.mark.parametrize("personal,status", [("", "unchanged"), ("My notes", "retained_local")])
def test_empty_extra_schema_is_not_reported_as_a_personal_edit(deck, personal, status):
    old = deck([["1", "question", "answer"]])
    student = deck([["1", "question", "answer", personal]], names=("CourseID", "Front", "Back", "Personal"))
    result = merge(old, old, student, "CourseID")
    assert result.plan["notes"][0]["status"] == status
    assert not result.imports


@pytest.mark.parametrize("teacher,student,expected,conflict", [
    ("old", "old", "old", False), ("new", "old", "new", False),
    ("old", "mine", "mine", False), ("same", "same", "same", False),
    ("new", "mine", "mine", True), ("", "old", "", False),
    ("", "mine", "mine", True), ("new", "", "", True),
])
def test_three_way_field_truth_table(deck, teacher, student, expected, conflict):
    result = merge(deck([["1", "q", "old"]]), deck([["1", "q", teacher]]),
                   deck([["1", "q", student]]), "CourseID")
    note = result.plan["notes"][0]
    assert note["merged"]["fields"]["Back"] == expected
    assert bool(note["issues"]) is conflict
    assert not conflict or not result.imports


def test_teacher_deletion_retains_student_work(deck):
    result = merge(deck([["1", "q", "old"]]), deck([]), deck([["1", "q", "mine"]]), "CourseID")
    assert not result.imports
    assert result.plan["notes"][0]["status"] == "retained_teacher_deleted"
    assert result.plan["notes"][0]["merged"]["fields"]["Back"] == "mine"


def test_student_deletion_is_not_resurrected(deck):
    old, new, student = deck([["1", "q", "old"]]), deck([["1", "q", "new"]]), deck([])
    result = merge(old, new, student, "CourseID")
    assert not result.imports
    assert result.plan["unresolved_count"] == 1
    choices = {"fingerprint": result.plan["fingerprint"], "notes": {"1": {"action": "add"}}}
    added = merge(old, new, student, "CourseID", resolutions=choices)
    assert added.imports[0].fields["Back"] == "new"


def test_divergent_concurrent_add_is_withheld(deck):
    result = merge(deck([]), deck([["1", "q", "teacher"]]), deck([["1", "q", "mine"]]), "CourseID")
    assert result.plan["notes"][0]["issues"][0]["kind"] == "concurrent_add"
    assert not result.imports


def test_no_foreign_guid_replaces_student_identity(deck):
    old = deck([["1", "q", "old"]], guids={"1": "teacherOld"})
    new = deck([["1", "q", "new"]], guids={"1": "teacherNew"})
    local = deck([["1", "q", "old"]], guids={"1": "student"})
    assert merge(old, new, local, "CourseID").imports[0].guid == "student"


def test_first_field_change_needs_student_guid(deck):
    names = ("Front", "CourseID", "Back")
    old = deck([["q", "1", "a"]], names=names, guids={"1": "teacherGuid"})
    new = deck([["q revised", "1", "a"]], names=names, guids={"1": "teacherGuid"})
    student = deck([["q", "1", "a"]], names=names)
    result = merge(old, new, student, "CourseID")
    assert not result.imports
    assert result.plan["notes"][0]["issues"][0]["kind"] == "first_field_changed_without_guid"
    assert not merge(old, new, student, "CourseID", protect=("Front",)).plan["unresolved_count"]


def test_empty_first_field_is_withheld_even_with_a_student_guid(deck):
    names = ("Front", "CourseID", "Back")
    old = deck([["question", "1", "old"]], names=names)
    new = deck([["", "1", "new"]], names=names)
    student = deck([["question", "1", "old"]], names=names, guids={"1": "localGuid"})
    result = merge(old, new, student, "CourseID")
    assert not result.imports
    assert result.plan["notes"][0]["issues"][0]["kind"] == "empty_first_field"


def test_whitespace_first_field_matches_anki_literal_behavior_with_guid(deck):
    names = ("Front", "CourseID", "Back")
    old = deck([["question", "1", "old"]], names=names)
    new = deck([[" ", "1", "new"]], names=names)
    student = deck([["question", "1", "old"]], names=names, guids={"1": "localGuid"})
    result = merge(old, new, student, "CourseID")
    assert result.imports[0].fields["Front"] == " "


def test_new_note_must_not_hijack_student_guid(deck):
    result = merge(deck([["1", "q", "a"]]),
                   deck([["1", "q", "a"], ["2", "new", "b"]], guids={"2": "localGuid"}),
                   deck([["1", "q", "a"]], guids={"1": "localGuid"}), "CourseID")
    assert not result.imports
    assert any(issue["kind"] == "guid_collision" for note in result.plan["notes"] for issue in note["issues"])


def test_first_field_collision_with_untouched_note(deck):
    names = ("Front", "CourseID", "Back")
    old = deck([["same", "1", "a"]], names=names)
    new = deck([["same", "1", "a"], ["same", "2", "b"]], names=names)
    result = merge(old, new, old, "CourseID")
    assert not result.imports
    assert result.plan["notes"][1]["issues"][0]["kind"] == "first_field_collision"


def test_first_field_freed_by_another_update_is_order_dependent(deck):
    """A new no-GUID note could overwrite an old note before it gets renamed."""
    names = ("Front", "CourseID", "Back")
    old = deck([["shared", "Z", "old"]], names=names)
    student = deck([["shared", "Z", "old"]], names=names, guids={"Z": "local"})
    new = deck([["renamed", "Z", "old"], ["shared", "A", "new"]], names=names)
    result = merge(old, new, student, "CourseID")
    assert [note.fields["CourseID"] for note in result.imports] == ["Z"]
    assert result.plan["notes"][0]["issues"][0]["kind"] == "first_field_collision"


def test_blocked_candidate_must_not_free_first_field(deck):
    """A withheld rename leaves its original first field in the collection."""
    names = ("Front", "CourseID", "Back")
    old = deck([["shared", "Z", "old"]], names=names)
    student = deck([["shared", "Z", "old"], ["other", "S", "personal"]], names=names, guids={"S": "taken"})
    new = deck([["shared", "Z", "old"], ["other", "A", "new"]], names=names)
    # Existing plain-first matching must consider the entire original snapshot.
    result = merge(old, new, student, "CourseID")
    assert not result.imports
    assert result.plan["unresolved_count"] == 1


def test_teacher_guid_reuse_under_another_course_id_is_rejected(deck):
    old = deck([["1", "q", "a"]], guids={"1": "sameGuid"})
    new = deck([["2", "q", "a"]], guids={"2": "sameGuid"})
    with pytest.raises(InputError, match="course ID"):
        merge(old, new, old, "CourseID")


def test_plain_first_field_cannot_hide_normalized_html_collision(deck):
    names = ("Front", "CourseID", "Back")
    old = deck([["<b>q</b>", "1", "a"]], names=names)
    new = deck([["<b>q</b>", "1", "a"], ["q", "2", "b"]], names=names)
    result = merge(old, new, old, "CourseID")
    assert not result.imports
    assert result.plan["notes"][1]["issues"][0]["kind"] == "html_identity_ambiguity"


def test_proposed_html_first_field_can_collide_with_a_later_plain_addition(deck):
    """The original snapshot is plain; an earlier GUID update introduces HTML."""
    names = ("Front", "CourseID", "Back")
    old = deck([["different", "1", "old"]], names=names)
    student = deck([["different", "1", "old"]], names=names, guids={"1": "localGuid"})
    new = deck([["<b>same</b>", "1", "old"], ["same", "2", "new"]], names=names)
    result = merge(old, new, student, "CourseID")
    assert [n.fields["CourseID"] for n in result.imports] == ["1"]
    assert result.plan["notes"][1]["issues"][0]["kind"] == "html_identity_ambiguity"


def test_unicode_normalized_first_field_collision_is_withheld(deck):
    """Real Anki import normalizes decomposed e+acute to composed é."""
    names = ("Front", "CourseID", "Back")
    old = deck([["é", "1", "a"]], names=names)
    new = deck([["é", "1", "a"], ["e\u0301", "2", "b"]], names=names)
    result = merge(old, new, old, "CourseID")
    assert not result.imports
    assert result.plan["notes"][1]["issues"][0]["kind"] == "first_field_collision"


def test_merge_does_not_mutate_inputs(deck):
    old, new, local = (deck([["1", "q", value]]) for value in ("old", "new", "mine"))
    before = copy.deepcopy([d.notes for d in (old, new, local)])
    merge(old, new, local, "CourseID")
    assert [d.notes for d in (old, new, local)] == before


def test_parse_options_invalidate_review_fingerprint(deck):
    old, new, student = (deck([["1", "q", value]]) for value in ("old", "new", "mine"))
    fingerprint = merge(old, new, student, "CourseID").plan["fingerprint"]
    student.header_row = True
    with pytest.raises(InputError, match="stale"):
        merge(old, new, student, "CourseID", resolutions={"fingerprint": fingerprint, "notes": {}})


def test_review_choices_and_staleness(deck):
    old, new, student = (deck([["1", "q", back]]) for back in ("old", "new", "mine"))
    preview = merge(old, new, student, "CourseID")
    choice = {"fingerprint": preview.plan["fingerprint"], "notes": {"1": {"fields": {"Back": {"value": "reviewed"}}}}}
    result = merge(old, new, student, "CourseID", resolutions=choice)
    assert result.imports[0].fields["Back"] == "reviewed"
    with pytest.raises(InputError, match="stale"):
        merge(old, new, student, "CourseID", protect=("Back",), resolutions=choice)


def test_next_release_preserves_a_previous_keep_student_decision(deck):
    """The next baseline is the previous teacher release, not the merged copy."""
    v1 = deck([["1", "old question", "old wording"]])
    v2 = deck([["1", "revised question", "teacher wording"]])
    student = deck([["1", "old question", "old wording", "My notes"]],
                   names=("CourseID", "Front", "Back", "Personal"), guids={"1": "localGuid"})
    preview = merge(v1, v2, student, "CourseID")
    choices = {"fingerprint": preview.plan["fingerprint"],
               "notes": {"1": {"fields": {"Back": {"take": "student"}}}}}
    reviewed = merge(v1, v2, student, "CourseID", resolutions=choices)
    applied = reviewed.imports[0]
    student_next = deck([[applied.fields[name] for name in student.fields]],
                        names=student.fields, guids={"1": "localGuid"})
    v3 = deck([["1", "latest question", "teacher wording"]])
    next_update = merge(v2, v3, student_next, "CourseID")
    assert not next_update.plan["unresolved_count"]
    assert next_update.imports[0].fields == {"CourseID": "1", "Front": "latest question",
                                             "Back": "old wording", "Personal": "My notes"}
    assert next_update.imports[0].guid == "localGuid"


@pytest.mark.parametrize("entry", [
    {"fields": {"Back": None}}, {"fields": {"Back": {"take": "unknown"}}},
    {"fields": {"CourseID": {"value": "2"}}}, {"fields": {"Bogus": {"value": "x"}}},
    {"action": "delete"}, {"fields": {"Back": {"value": "\x00"}}},
])
def test_invalid_resolutions_fail_closed(deck, entry):
    old, new, student = (deck([["1", "q", back]]) for back in ("old", "new", "mine"))
    fp = merge(old, new, student, "CourseID").plan["fingerprint"]
    with pytest.raises(InputError):
        merge(old, new, student, "CourseID", resolutions={"fingerprint": fp, "notes": {"1": entry}})


def test_schema_and_html_mismatches_fail(deck):
    old = deck([["1", "q", "a"]])
    with pytest.raises(InputError, match="schema"):
        merge(old, deck([["1", "q"]], names=("CourseID", "Front")), old, "CourseID")
    with pytest.raises(InputError, match="HTML"):
        merge(old, deck([["1", "q", "a"]], html=False), old, "CourseID")


@given(st.text(alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00\x1f"), max_size=100))
def test_student_only_field_is_preserved_property(deck, personal):
    old = deck([["1", "q", "a"]])
    new = deck([["1", "new", "a"]])
    student = deck([["1", "q", "a", personal]], names=("CourseID", "Front", "Back", "Personal"))
    result = merge(old, new, student, "CourseID")
    assert result.imports[0].fields["Personal"] == personal
