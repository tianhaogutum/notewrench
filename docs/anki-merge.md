# `notewrench anki-merge`

A teacher publishes a new version of an Anki course, but students have already edited
some cards, added personal fields and deleted notes they did not want. Re-importing the
new deck blindly overwrites their work or resurrects deleted notes. `notewrench anki-merge`
is an offline three-way preflight for that update: it compares the **old teacher**, **new
teacher** and **student** note text exports by a stable course ID field, combines
independent field edits, keeps personal fields, and withholds conflicting or unsafe
updates for review. Use it before importing a course update into a student's collection.

It never opens or edits an Anki collection or package. It writes a new review bundle with
an `import.tsv` containing only the safe additions and updates; Anki performs the actual
import and keeps scheduling when the import matches existing notes.

Formerly the standalone `anki-course-merge` tool (MIT license; see
[THIRD_PARTY.md](../THIRD_PARTY.md#notewrench-anki-merge-formerly-anki-course-merge)).

**Needs an extra:** the merge uses [csvdiff](https://github.com/larsyencken/csvdiff) for keyed
field differences. Install it with `pip install 'notewrench[anki-merge]'`; without it,
`--help` and `--version` still work and every other run prints that install hint.

**Reads / writes:** reads three UTF-8 text files (regular files only) and an optional
review-choices JSON file. Writes one new output directory, published atomically; an
existing path is never overwritten.

## Quick start

```sh
pip install 'notewrench[anki-merge]'
notewrench anki-merge --help
```

From a clone of the repository, run the bundled sample exports (real Anki 26.9.4 "Notes in
Plain Text" exports of invented course data; see
[`examples/anki-merge/PROVENANCE.md`](../examples/anki-merge/PROVENANCE.md)):

```sh
notewrench anki-merge examples/anki-merge/old.txt examples/anki-merge/new.txt examples/anki-merge/student.txt \
  --id CourseID --fields Front Back CourseID \
  --student-fields Front Back CourseID Personal --out review-out
```

It prints `3 safe additions/updates; 2 notes need review. review-out/review.txt` and exits
**1** on purpose:
some notes need review. The teacher notes have the fields `Front Back CourseID`; the
student's note type adds a `Personal` field, hence the separate `--student-fields`.

The output directory contains:

- `review.txt`: additions, field decisions, conflicts, retained and deleted records.
- `plan.json`: complete old/new/student values, reasons, counts and input hashes.
- `import.tsv`: only safe additions and updates, with Anki metadata headers.
- `resolutions.json`: a fingerprint-bound review template; fill its `null` choices before use.

Exit status: **0** no unresolved notes; **1** a bundle was written but some notes need
review; **2** invalid input or output, and no new bundle was written.

## Preparing the inputs

Export **Notes in Plain Text** from the student's original collection with *Include HTML*
and *Include unique identifier* (GUID) enabled. Keep the same old course version as the
baseline. Anki's normal exports omit field names, so supply the regular field names in the
note type's actual order with `--fields`; metadata columns (GUID, note type, deck, tags) are
excluded from `--fields`. `#columns` labels, or `--header-row` for ordinary CSV, also work.

| Option | Meaning |
| --- | --- |
| `OLD NEW STUDENT` | the three text exports (positional, in this order) |
| `--id FIELD` | stable course ID field name (required) |
| `--fields NAME ...` | regular field names, in order, for all three inputs |
| `--old-fields` / `--new-fields` / `--student-fields NAME ...` | per-input override when the orders differ |
| `--delimiter NAME` | separator when there is no `#separator` header (default tab), e.g. `comma` |
| `--old-delimiter` / `--new-delimiter` / `--student-delimiter` | separator for one input only |
| `--header-row` | **all three** inputs have an ordinary CSV header row |
| `--old-header-row` / `--new-header-row` / `--student-header-row` (and `--no-...`) | header row for one input only |
| `--notetype NAME`, `--deck NAME` | fill **missing** note-type/deck metadata (for authoring CSVs mixed with Anki exports) |
| `--protect FIELD ...` | always keep the student's value of these fields |
| `--resolutions FILE` | review choices bound to this exact preview's fingerprint |
| `--out DIR` | new output directory (required; never overwritten) |

With explicit field names, a header row must match them; a possible note is never silently
discarded as a header. Mixed output rows where some note-type or deck names are missing are
rejected until you fill them with `--notetype` / `--deck`.

## Review and re-run

Edit the generated template, then re-run the same command with a **fresh** output directory
and `--resolutions review-out/resolutions.json`:

```json
{
  "fingerprint": "COPY FROM THE GENERATED TEMPLATE",
  "notes": {
    "C03": {"fields": {"Back": {"take": "student"}}},
    "C05": {"action": "skip"}
  }
}
```

Field choices are `{"take": "student"}`, `"teacher"`, `"base"`, or
`{"value": "reviewed combined answer"}`. `"action": "skip"` leaves a note untouched;
`"action": "add"` deliberately restores a teacher note missing from the student export.
Omit entries you are not resolving yet. Changes to the inputs, field mappings or protected
fields invalidate old choices. `--protect Personal` always keeps that field for existing
students; student-only fields are kept automatically.

After completing an update, keep the **new teacher file** as the old baseline for the next
release, and use a fresh student export on the student side. Reusing the original baseline
forever can make a previously declined teacher change look new again.

## Importing the result

Before importing `import.tsv`, read the review and check Anki's import preview. Choose
**Update existing notes**, the original student note type and the correct field mapping.
The file presets `#if matches:update current` and `#match scope:notetype`; keep these
settings. Existing notes keep the student's GUID, never one from the teacher's
independently created collection. First-field edits require the student's exported GUID;
unsafe first-field matches are refused. Course IDs must be unique and never change. The
student export must cover the entire intended course: notes outside it are invisible to the
preflight. Keep the collection unchanged between the final student export and the import.

### Tested Anki versions

Import of the generated files is verified with Anki **2.1.66 and 26.9.4**. Anki 2.1.54 text
exports are readable, but its headless importer was observed creating an orphan card
(`nid=0`) when the same first-field update was imported twice; use a newer release for
importing. This is a local observation, not a diagnosed or reported upstream defect.

## Authoring pipelines (CSV, Brain Brew, CrowdAnki, Ki)

Keep an existing authoring pipeline such as Brain Brew; this command does not replace its
GUID generation, templates, media handling or CrowdAnki conversion. Save old/new CSV
snapshots of the same note type with a stable regular `CourseID` field, and export the
student's notes with HTML, GUID, note type, deck and tags enabled. CSV header rows and Anki
exports can be mixed:

```sh
notewrench anki-merge course-old.csv course-new.csv student.txt \
  --id CourseID --fields Front Back CourseID \
  --student-fields Front Back CourseID Personal \
  --old-header-row --new-header-row \
  --old-delimiter comma --new-delimiter comma \
  --notetype 'Course Facts' --deck Default --out course-review
```

The regular CSV columns here must be exactly `Front,Back,CourseID`. Put `#html:true` above
the CSV header when the fields contain HTML, matching the student export; plain-text sources
must use the same HTML mode on all three sides. If a CSV also contains Anki metadata, name
its positions above the ordinary header row, for example for `guid,Front,Back,CourseID,tags`:

```text
#separator:comma
#html:true
#guid column:1
#tags column:5
guid,Front,Back,CourseID,tags
```

Exported Anki GUIDs stay GUIDs; do not put course IDs into Anki's internal GUID column.
Without GUIDs, export every note of the note type that could take part in first-field
matching, including notes outside the course if necessary.

CrowdAnki and Ki already support personal-field protection and version history, and
AnkiCollab/AnkiHub provide hosted contribution workflows. Use them when they cover your
workflow; `anki-merge` is for the case where the available artifacts are ordinary note text
exports.

## Policies and limitations

- Exact field comparisons: no HTML rewriting, fuzzy matching or line-level merging.
- Student identity wins; existing notes never take the teacher's GUID.
- Teacher-deleted notes stay in the student collection (text imports do not delete).
- Notes missing from the student export are withheld until explicitly added or skipped.
- Concurrent additions with different content require review.
- Tags are the union of teacher and student tags; removals are not applied.
- One unchanged note type per run. Adding or removing fields needs a separate note-type
  migration; extra student fields are supported.
- Student columns must follow the student's actual note-type field order. The teacher's
  order may differ.
- UTF-8 only. Ragged rows, duplicate IDs or GUIDs, malformed quotes, conflicting metadata,
  mixed HTML modes and unsupported headers fail closed. Collection/package files
  (`.apkg`, `.colpkg`, `.anki2`) get an "export text first" error. Historical first-line
  `tags:` exports are supported.
- Course IDs must be Unicode NFC (Anki normalizes imported note text). First-field
  collisions without GUIDs are checked after NFC normalization against both the original
  and projected snapshots. HTML first-field ambiguity requires GUIDs.
- Fields are limited to 4 Mi characters, physical lines to 8 Mi characters and review-choice
  files to 16 MiB. Parsing, hashing and reports stream, but the keyed note indexes and the
  merge plan stay in memory.
- Text files contain no scheduling or review history; this command cannot recover lost
  history or guarantee identity outside the given student export.
- Media references stay as text; media files and packages are untouched. Plain-text
  exports cannot recover markup the exporter already removed; use HTML exports.
- Cloze content is preserved as text. Existing reviewed cards stay intact when clozes are
  edited, added, renumbered or removed; new cloze numbers create new cards without reviews,
  and removed numbers can leave empty cards. See Anki's
  [card generation documentation](https://docs.ankiweb.net/templates/generation.html).

## Sample data

`examples/anki-merge/` holds genuine Anki exports: the main set (Anki 26.9.4, all metadata,
HTML and GUIDs) and the variants `anki-2.1.54/`, `plain-text/`, `no-guid/` and
`fields-only/`. [`make_examples.py`](../examples/anki-merge/make_examples.py) regenerates them
with Anki's headless library (`pip install anki`); it never reads a real profile. The test
suite's integration tests also import into disposable collections when `anki` is installed
and are skipped otherwise.

## Related tools

This is a thin adapter around [csvdiff](https://github.com/larsyencken/csvdiff) and Python's
`csv` module that adds Anki-specific identity and review rules. Anki itself, CrowdAnki,
Brain Brew, Ki, Special Fields, AnkiCollab and AnkiHub cover other parts of course
collaboration; see [THIRD_PARTY.md](../THIRD_PARTY.md#notewrench-anki-merge-formerly-anki-course-merge)
for the surveyed versions and what each leaves open.
