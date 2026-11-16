# `notewrench wallabag-notes`

Wallabag keeps your highlights and annotations inside its JSON export, where no note-taking
app can use them. `notewrench wallabag-notes` turns a local Wallabag v2 JSON export into
Markdown pages, Logseq file-graph blocks and a CSV, with stable anchors, full provenance and
no lost annotations. Use it when you want your read-later highlights in a notes folder or a
Logseq graph, and `--update` when you want to re-run it on newer exports without touching the
notes you wrote by hand.

Formerly the standalone `bagnotes` tool. Python 3.11+, standard library only.

**Reads / writes:** it reads only the Wallabag v2 JSON files you name and never modifies
them. An export creates one **new** output directory (`--output`); `--update` rewrites only
the tool-managed files and regions inside a directory this command created earlier, and
refuses if they were edited. `--dry-run`, `--inspect-conflicts` and `--inspect-managed`
write nothing. Everything happens offline: no account, API key, network access, paid
service or other CLI is used, and no Wallabag or Logseq instance is contacted or modified.

**Platforms:** Linux and macOS; Windows is not supported. `--inspect-managed` refuses a
bundle path with a symlinked ancestor; on macOS `/tmp` and `/var` are symlinks, so use a
real path such as `/private/tmp/...` or a directory under your home folder.

- [Quick start](#quick-start)
- [Multiple accounts and several offline exports](#multiple-accounts-and-several-offline-exports)
- [Read-only check of all input conflicts before exporting](#read-only-check-of-all-input-conflicts-before-exporting)
- [Incremental updates: managed highlight regions and hand-written notes](#incremental-updates-managed-highlight-regions-and-hand-written-notes)
- [Read-only inspection of edited managed regions](#read-only-inspection-of-edited-managed-regions)
- [Input, identity and no-lost-annotation rules](#input-identity-and-no-lost-annotation-rules)
- [Limitations and related tools](#limitations-and-related-tools)
- [Format and schema reference](wallabag-notes/format.md)

## Quick start

```sh
pip install notewrench
notewrench wallabag-notes --help
```

The examples below run from a clone of the repository
(`git clone https://github.com/tianhaogutum/notewrench`); every input is synthetic.

```sh
notewrench wallabag-notes examples/wallabag-notes/wallabag-v2.json --output notes
```

By default `notes` must be a new directory whose parent already exists. Existing directories
(even empty ones), files and symlinks are never overwritten.

[`examples/wallabag-notes/wallabag-v2.json`](../examples/wallabag-notes/wallabag-v2.json) is a
synthetic file in the Wallabag v2 format: 2 articles and 3 annotation records, one of which is
an exact duplicate, with no annotation `id` (matching the upstream export groups). It contains
Chinese text, emoji, quotes, line breaks and an unknown range structure. The run produces 2
unique annotations and 7 files:

```text
notes/
  markdown/article-1.md
  markdown/article-2.md
  logseq/article-1.md
  logseq/article-2.md
  annotations.csv
  provenance.json
  report.json
```

Markdown pages contain the source link, `> quote…` blockquotes, the annotation text, stable HTML
anchors and a JSON code block with the original ranges. Logseq pages use `-` blocks, indented
child blocks and deterministic UUID `id::` properties, so you can reference a highlight in a
file graph with `((UUID))`. Copy the generated `.md` files into your graph's `pages/` yourself
and keep the JSON sidecars for auditing. The command never writes into an existing graph and
does not support Logseq DB graphs. The CSV is UTF-8 with standard quote escaping and CRLF
record separators; `quote` / `comment` cells keep their original line breaks. `ranges_json` is a
JSON array: read it with a JSON parser.

Select a single format, or validate the complete export plan first:

```sh
notewrench wallabag-notes examples/wallabag-notes/with-ids.json -o csv-notes --format csv
notewrench wallabag-notes examples/wallabag-notes/wallabag-v2.json -o proposed-notes --dry-run
```

`--format` is `all` (default), `markdown`, `logseq` or `csv`. Every mode writes
`provenance.json` and `report.json`. `--dry-run` performs the same parsing, conflict checks,
rendering and destination checks, prints the JSON file list with byte counts on stdout, and
creates nothing; nothing from the input is ever executed. Successful runs print a JSON summary
on stdout; diagnostics go to stderr, and the report file lists every warning.

A failing example (exit code 2, no output directory is created):

```sh
notewrench wallabag-notes examples/wallabag-notes/missing-quote.json -o rejected-notes
```

The diagnostic `code` is `missing_quote` and its `path` points at the annotation. Diagnostics for
conflicting records with the same ID include `first_path`, the current `path` and
`differing_fields`, and fail the whole export: there is no "last one wins".

Exit codes: **0** success (possibly with explicit warnings), **2** format/field/conflict/argument
error, **3** file I/O or output failure.

## Multiple accounts and several offline exports

Use the repeatable `--source NAMESPACE FILE` to give each account an explicit namespace; each
file is still a native Wallabag v2 article array. Several exports of the same account use the
same namespace; different accounts must use different namespaces. A namespace is a stable label
you choose; it is never guessed from file names, URLs or article fields, and account ownership is
not verified. Labels are case-sensitive, 1–64 ASCII letters, digits, `.`, `_` or `-`, starting
with a letter or digit.

The two local synthetic samples below **both contain article ID `1` and annotation ID `9`, with
different content**:

```sh
notewrench wallabag-notes \
  --source personal examples/wallabag-notes/accounts/personal.json \
  --source work examples/wallabag-notes/accounts/work.json \
  --source personal examples/wallabag-notes/accounts/personal.json \
  -o merged-notes
```

Expected: 2 namespaces, 3 input files, 2 distinct articles, 2 distinct annotations, 1 duplicate
article and 1 duplicate annotation merged, 7 files written. The two accounts get different
anchors and Logseq UUIDs. As long as namespace and IDs stay the same, identities and article file
names are stable across separate exports, reordered inputs and edits to identified annotations.
Merging follows the order of the arguments and of each file's array: articles in first-seen
order, annotations within an article in first-seen order. Within one multi-file merge, a later
partial export can add annotations to an existing article; older annotations that do not appear
are **not** treated as deleted. Cross-run deletion semantics belong to `--update` (below), which
explicitly works on complete snapshots.

- An article is scoped by `(namespace, normalized article ID)`; an annotation additionally by its
  `identity`. The namespaced anchor is `bagnotes-` + `SHA256(JSON [namespace, source_id, identity])`,
  using the compact, key-sorted UTF-8 encoding described below. The Logseq UUID rule is unchanged.
- Markdown/Logseq file names are `article-<SHA256(JSON [namespace, source_id])>.md`; the full
  digest avoids confusable names, and the namespace is shown in the content. `source_id` means the
  article ID.
- Both JSON sidecars use **schema_version 2**. `inputs` keeps the zero-based `input_order`, the
  `namespace` and the `sha256` of the original file bytes for every input, including duplicate and
  empty exports; no fictitious `input_sha256` of a merged file is invented. Article records in the
  provenance carry `namespace`, the first `input_order` and `file_stem`; every occurrence of an
  article or annotation records its input number and position in that file. The original fields of
  every annotation occurrence are kept. The CSV gains `namespace` and `input_order` columns for the
  first occurrence of each annotation; all occurrences remain in the sidecar.
- Repeated article metadata (including `content`) within one namespace must be identical; so must
  the fields of the same annotation identity, apart from the spelling of numeric IDs (for example
  `9` versus `"009"`). Otherwise the whole export fails with `article_conflict` or
  `annotation_conflict` rather than overwriting by file order. Title, date or HTML differences
  between exports taken at different times can also conflict; the latest version is never chosen
  automatically. Annotations without an ID still use a content digest, so editing an anonymous
  annotation produces a different record.

A failing example: two different accounts mistakenly labelled with the same namespace. This exits
2 and creates no output directory:

```sh
notewrench wallabag-notes \
  --source personal examples/wallabag-notes/accounts/personal.json \
  --source personal examples/wallabag-notes/accounts/work.json \
  -o rejected-merge
```

The diagnostic is `article_conflict`; `first_path` points at `$inputs[0].articles[0]`, `path` at
`$inputs[1].articles[0]`, and it lists the differing fields, the namespace and the current input
number. All parsing and conflict checks finish before any file is published; `--dry-run` rejects
conflicts too.

The single-input form `notewrench wallabag-notes export.json -o notes` and the Python
`parse(data)` keep the implicit **single-account scope**: schema 1, the original anchors, CSV
columns and file names such as `article-1.md`. If different accounts are exported separately this
way, the anchors of equal numeric IDs still collide; do not concatenate their data or merge them
into one graph. Switching to explicit namespaces produces new identities and does not remap old
Logseq references. The positional input cannot be combined with `--source`; to export one
namespaced file, use a single `--source`. Python API:
`notewrench.wallabagnotes.core.parse_sources([("personal", personal_bytes), ("work", work_bytes)])`.

## Read-only check of all input conflicts before exporting

`--inspect-conflicts` takes only the repeatable `--source NAMESPACE FILE` and needs no output
directory. The command below deliberately labels different accounts with the same namespace to
show two conflicts:

```sh
notewrench wallabag-notes \
  --source personal examples/wallabag-notes/accounts/personal.json \
  --source personal examples/wallabag-notes/accounts/work.json \
  --inspect-conflicts
```

stdout is a complete JSON report: `schema_version: 1`, `tool: "bagnotes-conflicts"`,
`status: "conflicts"`, `counts: {"article_conflicts": 1, "annotation_conflicts": 1, "conflicts": 2}`,
plus `inputs`, `conflicts` and the parser `warnings`. Each input records `input_order`,
`namespace`, the `sha256` of the original bytes and the `file` given on the command line. Each
conflict contains `code`, `namespace`, the normalized `source_id` and the sorted
`differing_fields`; annotation conflicts also carry `identity`. `first` / `second` each record
`input_order`, `sha256`, `source_order` and the conceptual position `path`; annotation positions
also have `annotation_order`. For example `$inputs[1].articles[0].annotations[0]` is the first
annotation of the second input.

The check covers **every pair of incompatible occurrences** in one scope, including duplicates
within one file, repeated inputs, differences between three versions, and annotation differences
underneath an article metadata conflict. Order is by current occurrence, then by earlier
occurrence. Identical records do not conflict; three versions or repeated inputs can produce
several conflicts for one identity. Comparison rules are exactly those of the export, including
article `content` and unknown fields; only the spelling of numeric IDs is normalized. Different
namespaces, articles and annotation identities are never compared with each other; an edited
anonymous annotation is a different identity, so such edits cannot be detected as conflicts.

With conflicts the command exits **2** with the complete report on stdout and empty stderr; with no
conflicts it exits **0** with `status: "compatible"` and an empty conflict list. Invalid JSON,
missing required fields and similar errors still fail with exit 2 and a JSON diagnostic on stderr,
**without a partial conflict report**; unreadable files exit 3. Argument errors exit 2 with usage
on stderr. The check never renders or publishes files, never creates or reads an export directory,
and cannot be combined with a positional input, `-o`, `--format`, `--update` or `--dry-run`. Save
stdout with shell redirection if you need it; the command itself writes no report file.

After the check, choose the compatible inputs yourself and run a normal export. The command never
picks the latest version, modifies inputs or resolves conflicts. `compatible` only means the inputs
are compatible with the format and merge rules; it does not validate the output directory, the
managed-region state or snapshot completeness, which the real export/update still check in full.
Python API: `notewrench.wallabagnotes.core.inspect_sources([(namespace, bytes), ...])`, returning the
same report without the CLI's `file` field. The check runs in memory; n occurrences of one identity
require up to n(n−1)/2 comparisons. No large-library performance claim is made.

### Optional: show the values on both sides of each conflicting field

By default the report lists only the names of differing fields. Add `--conflict-values` to see the
values on both sides:

```sh
notewrench wallabag-notes \
  --source personal examples/wallabag-notes/conflicts/values-before.json \
  --source personal examples/wallabag-notes/conflicts/values-after.json \
  --inspect-conflicts --conflict-values
```

This synthetic example exits 2 with the complete conflict JSON on stdout and empty stderr. Each
conflict gains `field_values`, keyed in `differing_fields` order and containing only that
conflict's differing fields. For example an article `title` that changed from null to absent:

```json
{"title":{"first":{"present":true,"value":null},"second":{"present":false}}}
```

`present` is always a boolean and `value` exists only for present fields, so null, absent, empty
string, empty array and empty object are never confused. Values correspond to the original
`first` / `second` positions and include article `content`, unknown fields, the original Unicode
and line breaks, and exact numbers inside opaque ranges. Only top-level article/annotation IDs are
normalized by the existing rules; nested `id` values are not rewritten, and a difference only in
ID spelling within one identity is not a conflict. JSON does not preserve original whitespace, key
order or number spelling. Schema 1, hashes, positions, order, counts and warnings are unchanged;
without the option the report is byte-for-byte unchanged.

`--conflict-values` requires `--inspect-conflicts`; normal export, update, dry-run and
`--inspect-managed` reject it (exit 2, empty stdout). An invalid later input still produces no
partial stdout; read failures still exit 3. Python API: `inspect_sources(inputs, include_values=True)`
(default False).

Values are neither truncated nor redacted and may contain private annotations and full article
HTML; review the report before sharing it. The report is held in memory and every conflict pair
repeats its field values, so long bodies and many repeated occurrences can make it large. No
capacity or timing benchmark is claimed.

## Incremental updates: managed highlight regions and hand-written notes

`--update` is an explicitly chosen **complete-snapshot update** mode. The first call creates a new
directory and its update state; later calls on the same directory replace the managed highlight
regions, refresh the CSV/JSON files, and report additions, modifications and deletions. Without
the flag, exports still refuse existing directories; `--update` cannot adopt an earlier plain
export, an existing Logseq graph or an arbitrary folder of hand-written files.

The two [local synthetic inputs](../examples/wallabag-notes/updates/before.json) follow the
Wallabag v2 array format: `before.json` has highlights 9 and 10;
[`after.json`](../examples/wallabag-notes/updates/after.json) modifies 9, deletes 10 and adds 11.

```sh
notewrench wallabag-notes examples/wallabag-notes/updates/before.json -o evolving-notes --update
# write notes under "Personal notes" in evolving-notes/markdown/article-1.md or logseq/article-1.md
notewrench wallabag-notes examples/wallabag-notes/updates/after.json -o evolving-notes --update --dry-run
notewrench wallabag-notes examples/wallabag-notes/updates/after.json -o evolving-notes --update
# the same input again: status is "unchanged" and nothing is written
notewrench wallabag-notes examples/wallabag-notes/updates/after.json -o evolving-notes --update
```

For the second import, `counts` on stdout and in `changes.json` is
`{"added": 1, "modified": 1, "deleted": 1}`. `modified` contains the original and updated
annotation 9 with `differing_fields: ["quote", "text"]`; `deleted` keeps the old annotation 10 and
`added` contains the new annotation 11. Every entry records namespace, article ID, annotation
identity, anchor and block UUID. Added, disappeared and archived-metadata changes of whole articles
are listed under `articles`; the deliberately omitted article `content` is not compared. The diff
is not a cumulative history: each applied update overwrites `changes.json`, a no-change replay
keeps the previous report, and stdout shows the zero diff of the current run. A dry-run performs
the same validation, shows the diff and the files to be written, and creates nothing.

Each Markdown/Logseq page has exactly one managed region:

```text
<!-- bagnotes:managed:start -->
generated title, link, highlights, annotations and ranges
<!-- bagnotes:managed:end -->

Personal notes (a heading or list item in the page's format)
your own notes go here
```

Everything **outside** the markers, before and after, is preserved byte for byte, including CRLF,
spaces, Unicode and a missing final newline. Any change to the managed region or its markers makes
the whole update fail with exit 2 and `update_conflict`, leaving every file untouched: move your
hand-written text outside the region, restore the original managed region, then retry. Missing
tracked pages, hand-edited CSV/JSON files, symbolic links and path collisions of new pages are
also rejected, in dry-run too. `update-state.json` stores SHA-256 baselines of the regions and tool
files; keep it and the other sidecars intact and do not edit them. It is a local integrity check,
not a signature against deliberate tampering with both pages and state.

- The input must contain **all articles and annotations** in the update's scope: absence means
  deletion. Never use a filtered or partial export as a complete snapshot. Every article must have
  an explicit `annotations` array; omitting it is `incomplete_snapshot`, never guessed as "emptied".
  Several files of the same namespace are first merged by the rules above, then used as the
  snapshot; conflicting records between them are still rejected.
- The namespace set and `--format` (default `all`) must match the initialization; changing them is
  `update_scope`. The positional single-input form keeps the single-account identity rules and
  cannot later switch to explicit namespaces.
- Updates of highlights with IDs keep their anchor/UUID; an edited anonymous highlight can only be
  reported as the old one deleted and a new one added, never as a modification. Replaying the same
  input writes nothing, creates no pages, duplicates no highlights and never rewrites hand-written
  notes.
- When an article disappears, its page and hand-written notes are kept and the managed region is
  replaced by an absence notice; when it reappears, the original path is reused. Pages are never
  deleted and no extra archive pages are created; existing UUID references to deleted highlights
  may stop resolving.
- Before updating, every file is validated, new files and backups are staged, and every file that
  was read is checked again before replacement. An ordinary replacement failure triggers a
  rollback attempt; power loss or a killed process does not guarantee directory-level atomicity,
  and concurrent edits or updates are unsupported. Back up the directory before updating;
  `update-state.json` is written last and marks a completed update.

### Optional: limit how many annotations one update may delete

The complete snapshot is still your explicit choice. Add `--max-deleted-annotations N` to stop an
accidental partial export from deleting more annotations than you allow; without the option,
deletion semantics and output are unchanged:

```sh
notewrench wallabag-notes examples/wallabag-notes/updates/before.json -o budget-notes --update --max-deleted-annotations 0
# after.json deletes one annotation: this exits 2 and changes nothing
notewrench wallabag-notes examples/wallabag-notes/updates/after.json -o budget-notes --update --max-deleted-annotations 0
# check and allow exactly one deletion
notewrench wallabag-notes examples/wallabag-notes/updates/after.json -o budget-notes --update --max-deleted-annotations 1 --dry-run
notewrench wallabag-notes examples/wallabag-notes/updates/after.json -o budget-notes --update --max-deleted-annotations 1
```

`N` is a non-empty string of ASCII decimal digits; `0` and leading zeros are allowed; negative
numbers, signs, decimals, exponents, whitespace and non-ASCII digits are not, nor are values too
large for Python's integer conversion. The option requires `--update`, may be combined with
`--dry-run`, and is rejected for plain exports and every inspection mode. Invalid values exit 2
before any input or output is read, with empty stdout and a usage diagnostic on stderr.

The count is exactly this run's `changes.counts.deleted`: unique annotations, keyed by
`(namespace, article ID, annotation identity)`, present in the old snapshot and missing from the
new one. Repeated inputs or occurrences are not counted twice; the same ID in different articles
or namespaces counts separately. A whole disappeared article counts all its annotations; an edited
anonymous annotation counts as one deletion plus one addition; content changes of identified
annotations are not deletions. Already retired articles are not charged again, and reappearing
ones only count as additions. Initialization has no old annotations, so it deletes zero; a
no-change replay also deletes zero, so a budget of zero passes and writes nothing. A count equal to
the limit is allowed; the limit applies to this invocation only and is not stored in the state or
the success summary.

The existing input, scope, baseline, file/managed-region and new-path checks run first; the budget
is checked before staging or publication. When it is exceeded the command exits 2 with empty
stdout and a JSON diagnostic on stderr, for example:

```json
{"status":"error","code":"deletion_budget_exceeded","message":"Planned annotation deletions exceed the selected limit","path":"budget-notes","max_deleted_annotations":0,"deleted_annotations":1}
```

The bundle, hand-written regions, link targets and mtimes stay untouched; dry-run applies the same
limit and never prints an applicable success plan. Existing local conflicts are still reported
first, and real OS I/O failures still exit 3. The budget does not prove the snapshot complete, and
it does not choose, merge or repair inputs.

Python API: `notewrench.wallabagnotes.update.plan_update(output, export, format_name, *, max_deleted_annotations=None)`.
The default `None` means no limit; otherwise the value must be a non-negative Python `int` (not a
bool, float or string). Invalid values raise `BagnotesError` with code `invalid_deletion_budget`
and path `$.max_deleted_annotations` before any rendering or file I/O; exceeding the budget raises
the same `deletion_budget_exceeded` diagnostic and returns no plan. Callers publish successful
plans with the existing API.

## Read-only inspection of edited managed regions

After an update was refused with `update_conflict`, inspect the existing directory without needing
the next export:

```sh
notewrench wallabag-notes --inspect-managed evolving-notes
```

Without `--propose-update` (next section), the command accepts only one directory initialized by
`--update` and cannot be combined with a positional input, `--source`, `-o`, `--format`,
`--update`, `--dry-run` or `--inspect-conflicts`. The directory path and all its parents must not be
symbolic links; give the real directory path. Before reading any baseline file, the command checks
the path component by component from the root; relative, absolute and `link/../` spellings cannot
bypass this. At the first link it exits **2** with empty stdout and an `update_state` JSON
diagnostic on stderr whose `path` is the absolute lexical path of that link. `.` / `..` paths
without links still work. The command rebuilds the page baseline of the **last successful update**
from the hash-checked `provenance.json` and checks it again against the region SHA-256 values in
the state; directories created by earlier versions are supported without re-initialization.
Disappeared articles use the original retirement notice.

stdout JSON contains `schema_version: 1`, `tool: "bagnotes-managed-conflicts"`, `output`,
`status`, `checked_pages`, `conflicts`, `checked_artifacts` and `artifact_conflicts`. `conflicts`
lists only page differences; `artifact_conflicts` separately lists problems with tool files that
are not part of the baseline. Each list is sorted by relative path, and every problematic tracked
file appears exactly once. With both lists empty, `status` is `"clean"` and the exit code **0**; if
either is non-empty (including sidecar-only problems), `status` is `"conflicts"` and the exit code
**2**. stderr is empty in both cases. `checked_artifacts` includes the verified provenance (4 for
`all`/`csv`, 3 otherwise); `checked_pages` is the number of tracked pages. Each page conflict has:

- `path`, `code`, `message`, `comparison`; an ordinary edit is `managed_edit` / `managed-region`.
- `expected` / `current`: `sha256`, `bytes` (byte count) and `base64` (exact original bytes).
- `unified_diff`: a diff from `--- <path>:expected` to `+++ <path>:current` with three lines of
  context. Raw byte lines are compared and shown as UTF-8, invalid bytes as backslash escapes, and a
  missing final newline is marked explicitly. This is for display and not guaranteed to apply as a
  patch; use the base64 values for exact restoration.

With intact markers, only the managed region is compared and personal prefixes/suffixes never enter
the diff. **By default**, when markers are missing, duplicated or reversed, the boundary cannot be
guessed safely: the entry is `invalid_markers` / `whole-page`, `expected` is the original managed
region and `current` the whole page, so the diff **may contain personal notes**; review the report
before sharing it. Missing pages, non-regular files and symlinks are `unreadable_page` /
`unavailable` with `current` and `unified_diff` set to null; symlinks of pages or format directories
are never read through, and one such page never stops the inspection of others. Real read I/O errors
still fail with exit 3 and no partial report.

Every problem with `report.json`, `changes.json` and (when the format includes it)
`annotations.csv` is collected into `artifact_conflicts` and returned together with all page
differences. Each entry has `path`, `code`, `message`, `expected_sha256` (the hash stored in the
state) and `current_sha256` (the actual hash of a readable file). An edit is `artifact_edit`;
missing files, non-regular files and symlinks are `unreadable_artifact` with a null current hash.
Symlink targets are never read or followed to guess a hash. The state stores only hashes of these
files, so there is no original content or text diff for them.

The baseline must still be complete: a damaged state, a directory that was not created by
`--update`, a missing/edited/symlinked provenance, or a page baseline that cannot be rebuilt to the
stored hashes fails with exit 2, a JSON diagnostic on stderr and empty stdout, even if other file
problems exist. Real OS read or metadata errors (including permission errors on sidecars, later
pages or parent directories) exit 3 with `io_error` on stderr and empty stdout. Old content is never
invented; restore damaged sidecars from a complete backup by hand. State hashes are a local
baseline, not a signature; if rendering rules change in the future, old directories may need the
matching version for inspection. `clean` only means the current tracked files are compatible with
the baseline; by default it does not check the next snapshot, untracked path collisions or whether a
future update will succeed. Concurrent edits or path replacement during inspection are unsupported;
the component-wise `lstat` check gives no isolation guarantee against concurrent replacement. The
parent-directory check applies only to `--inspect-managed`. All content and diffs are processed in
memory; no large-library performance claim is made.

The command never repairs or writes files. Back up the directory, use the diffs to move hand-written
text **manually** outside the markers, restore the managed region's original bytes, then retry the
original update command. Do not treat the next snapshot's content as the old baseline, and do not
overwrite a whole page with the `expected` value of a whole-page entry, which would lose your
personal prefix and suffix.

### Optional: only managed-region diagnostics when markers are damaged

Add `--managed-only` to keep the whole-page content of pages with damaged markers (and its hash,
length, base64 and diff) out of the report:

```sh
notewrench wallabag-notes --inspect-managed evolving-notes --managed-only
# combinable with any valid proposal option:
notewrench wallabag-notes --inspect-managed evolving-notes --managed-only \
  --propose-update examples/wallabag-notes/updates/after.json --region-relationships --local-proposed-regions
```

The option is valid only with `--inspect-managed DIR` and needs no next input; plain exports,
updates, dry-runs and input conflict checks reject it (usage error, exit 2, empty stdout). Python API:
`notewrench.wallabagnotes.update.inspect_managed(path, managed_only=True)` (default False); other
option dependencies are unchanged.

Schema 1 is kept; only in this mode the report gains a top-level `managed_only: true` and every page
conflict gains `unavailable_reason`. When markers are missing, duplicated, reversed or have damaged
line endings, the entry keeps `invalid_markers`, the original path/message and the verified
`expected` managed region, but switches to `comparison: "unavailable"`, `current: null` and
`unified_diff: null`, with reason `{code: "invalid_markers", message: <original message>}`.
Boundaries are never guessed and there is no fallback to a whole-page comparison. Missing, linked or
non-regular pages are still `unreadable_page` and gain the corresponding reason; readable complete
managed regions keep their exact bytes/hash/base64/diff with a null reason.

**No conflict is hidden**: every page, artifact, path collision and reason is still reported;
available managed regions and the optional saved/local/proposed previews and relationship
classifications are independent and keep full precision. Retired and reappearing pages follow the
same rules. The default report is byte-for-byte unchanged; statuses and exit codes are unchanged
(clean 0, conflicts 2, real OS errors 3). Invalid inputs, scopes or unverifiable baselines still
produce no partial stdout; links are not followed, files are not repaired or written, and future
updates are not guaranteed to succeed.

This option keeps **the local whole page of damaged markers** out of the output; it does not redact
the whole report. Verified old managed regions, available local regions, proposed content and raw
values can still contain private annotations, and artifact hashes and paths are still included.
Everything is still held in memory; concurrent edits are unsupported.

### Optional: metadata-only reports for reviewing conflicts and proposed identities

If the report should carry no managed text or proposed raw values, use `--metadata-only`:

```sh
notewrench wallabag-notes --inspect-managed evolving-notes --metadata-only
notewrench wallabag-notes --inspect-managed evolving-notes --metadata-only \
  --propose-update examples/wallabag-notes/updates/after.json --proposed-paths --proposed-regions \
  --region-relationships --local-proposed-regions
```

It is valid only with `--inspect-managed DIR` and needs no next input; it implies `--managed-only`,
and adding that flag explicitly produces identical output. API:
`notewrench.wallabagnotes.update.inspect_managed(path, metadata_only=True)` (default False). All
proposal option dependencies still apply: an explicit complete snapshot with the same
format/namespace scope is required, and `--local-proposed-regions` still requires
`--region-relationships`. Plain exports, updates, dry-runs and input conflict mode reject
metadata-only (usage error, exit 2, empty stdout).

Schema 1 gains `metadata_only: true` and an effective `managed_only: true`. Every conflict, artifact
problem, path collision, reason, count, order, stable identity/anchor/block UUID, changed field name,
lifecycle and requested relationship classification is kept. Specifically:

- Page expected/current, saved-to-proposed before/after and local-to-proposed local/proposed keep
  only `{sha256, bytes}`, with no base64. Fingerprints and classifications in relationship lists are
  unchanged.
- In `proposed_update.changes`, the raw `before` / `after` values of added/modified/deleted/articles
  entries are **omitted as keys** (not null); identities, `differing_fields` and the existing change
  summaries are kept.
- Every `unified_diff` key is omitted, and no diff or base64 is computed. An unavailable `current`
  is still null with the original `{code,message}` reason; available regions have a null reason.
  With damaged markers there is no whole-page fallback, fingerprint or length.
- Without a next input there is no `proposed_update`; optional lists that were not requested are
  still omitted, and requested empty lists are still `[]`.

**The remaining metadata can still be sensitive**: SHA-256 values and byte counts of the verified old
managed regions, available local regions and requested proposed regions (including markers), whole
artifact hashes, next-input hashes, paths/output directory, namespaces/source IDs, anonymous content
identity hashes, anchors/UUIDs, field names and warnings remain visible and can be correlated or used
for dictionary guessing. This reduces content output; it is not anonymization, and private data is
still read and processed in memory. The complete schema and the absent/null rules are in
[the format reference](wallabag-notes/format.md#opt-in-metadata-only-managed-reports-managed-inspection-schema-1-extension).

Without metadata-only, the default detailed report and the `--managed-only` report are byte-for-byte
unchanged. Exit rules are as before: clean or proposal-only changes 0; page/artifact/enabled path
conflicts 2; invalid input, scope or unverifiable baseline 2 with no stdout; real OS errors
3/`io_error` with no stdout.

### Optional: also show the proposed changes of the next complete snapshot

Add `--propose-update` with the next input to see which annotations and articles would be added,
modified or deleted, even while there are local conflicts:

```sh
notewrench wallabag-notes --inspect-managed evolving-notes --propose-update examples/wallabag-notes/updates/after.json
# a directory initialized with explicit namespaces still needs the same namespace set:
notewrench wallabag-notes --inspect-managed merged-notes --propose-update \
  --source personal examples/wallabag-notes/accounts/personal.json --source work examples/wallabag-notes/accounts/work.json
```

The directory must have been initialized by `--update` (the `merged-notes` above must first be
initialized with `--update` in the same scope). The input is one positional file **or** repeated
`--source NAMESPACE FILE`, never both. `--format` defaults to `all` and must match the
initialization; a Markdown-only directory needs `--format markdown`. An empty namespace still needs
an explicit `--source NAMESPACE empty.json` (file content `[]`); the scope is never inferred from
the directory. The option requires `--inspect-managed` and still cannot be combined with `-o`,
`--update`, `--dry-run`, `--inspect-conflicts` or `--conflict-values`.

The existing schema 1 fields, `tool`, page `conflicts` and `artifact_conflicts` are kept; a separate
`proposed_update` contains:

- `format` and `namespaces` (null for the single-input form; the sorted set for explicit namespaces).
- `snapshot`: hashes of the next input's original bytes; `{schema_version: 1, input_sha256: ...}` for
  the single-input form, `{schema_version: 2, inputs: [{input_order, namespace, sha256}, ...]}` for
  explicit namespaces, including empty and repeated inputs.
- `changes`: the same schema 1 as the update's `changes.json`: `added`, `modified`, `deleted`,
  annotation `counts` and article events `articles`, with raw before/after values and stable
  identity/anchor/UUID. Article `content` is still omitted and not compared.
- `warnings`: parse warnings of the next input, in parse order.

Changes compare only the **hash-checked, rebuilt and verified previous provenance** with the
snapshot you supply; neither the new snapshot nor locally edited pages are treated as the old
baseline. Traversal is old article order, then newly seen articles; within an article, old
annotation order, then new annotations. Each added/modified/deleted list keeps that traversal order;
`differing_fields` is sorted. Both local conflict lists remain sorted by path. Modifications with IDs
keep their anchor/UUID; anonymous edits are still a deletion plus an addition.

Without `--proposed-paths` (next section), `status` and the exit code reflect **local conflicts
only**: a locally clean directory exits **0** even with non-empty proposed changes; any non-empty
local conflict list exits **2**; stderr is empty in both cases. Invalid new JSON/fields, conflicting
records between inputs, missing explicit `annotations`, namespace/format mismatches and invalid or
unverifiable baselines exit 2 with only a stderr diagnostic and **no partial stdout**. Real OS read
or metadata errors exit 3 with `io_error` and empty stdout. Argument errors exit 2 with usage on
stderr.

These are complete-snapshot semantics: absent articles/annotations are listed as deletions. The
command never infers whether your export is really complete and never picks files or the latest
version. The inspection is read-only: it does not publish, repair, overwrite or migrate notes, and
bytes and mtimes of files, links and directories stay unchanged. Untracked path collisions of new
pages are not checked by default (enable them below); a clean report does not guarantee that the
update will succeed. The report contains unredacted annotations and article metadata; review it
before sharing. Python API:
`notewrench.wallabagnotes.update.inspect_managed(path, next_export=parsed_export, format_name="all")`;
omitting `next_export` keeps the plain inspection report.

### Optional: check destination collisions of proposed new pages

Add `--proposed-paths` to a proposal check:

```sh
notewrench wallabag-notes --inspect-managed evolving-notes --propose-update next-complete.json --proposed-paths
```

`next-complete.json` is the next complete snapshot you chose; the namespace and format rules above
apply. The option requires `--propose-update`; without a proposed input, or combined with
incompatible arguments, it exits 2 with empty stdout and usage on stderr, without reading the
directory.

Only with this option does `proposed_update` gain `checked_new_pages` and `path_collisions`. The
former is the number of new page destinations (including pages blocked by a parent directory); the
latter is sorted by relative page path. Every blocked new page appears exactly once, never mixed into
page `conflicts`, `artifact_conflicts` or `changes`. For example:

```json
{"path":"markdown/article-2.md","code":"new_page_collision","namespace":null,"source_id":"2","blocking_path":"markdown/article-2.md","kind":"file"}
```

`namespace` / the normalized `source_id` identify the article; `path` is the proposed relative page
path. `new_page_collision` means the destination exists, `blocking_path` is that destination, and
`kind` is `file`, `directory`, `symlink` or `other`. If a format directory is a link or not a
directory, the code is `unsafe_page_parent` with `blocking_path` `markdown` or `logseq`, and nothing
below it is queried. Only `lstat` metadata is read; links (including dangling and looping ones) are
never followed, colliding files are never read, and link targets are never listed.

Only destinations the next snapshot would introduce, and that are not in the saved page table, are
checked. Retired tracked pages reuse their original path when they reappear and are covered by the
existing local page diagnostics. Missing format directories are valid; unrelated untracked files are
not scanned. CSV mode has no pages, so the count is 0 and the list empty. The two page formats are
counted separately.

If either local conflict list or `path_collisions` is non-empty, the top-level `status` is
`conflicts` and the exit code **2**; with all three empty it is `clean` and **0**, even with non-empty
proposed changes. Both outcomes print complete JSON with empty stderr. Invalid new input, scope or
baseline still exit 2 with no partial stdout; real OS errors still exit 3 with `io_error` and no
stdout. Without the option, the existing report fields, status and exit codes are unchanged. The
check remains read-only. Python API:
`inspect_managed(path, next_export=parsed_export, format_name="all", include_paths=True)`; enabling
it without `next_export` raises `update_scope`.

### Optional: preview the exact bytes and diffs of proposed managed regions

Add `--proposed-regions` to an explicit next-snapshot check:

```sh
notewrench wallabag-notes --inspect-managed evolving-notes --propose-update \
  examples/wallabag-notes/updates/after.json --proposed-regions
# --proposed-paths can be added as well; the two checks are independent
```

Only with this option does `proposed_update` gain `region_changes`, sorted by relative page path and
listing only pages whose managed-region bytes actually differ. Each entry has `path`, `change`,
`before_state`, `after_state`, `before`, `after` and `unified_diff`. Both sides are
`{sha256, bytes, base64}`, so the managed region including its markers can be restored losslessly
from base64; the diff uses `:saved` / `:proposed` labels. `before_state` is `absent`, `active` or
`retired`; `after_state` is `active` or `retired`:

| change | before_state → after_state | meaning |
| --- | --- | --- |
| added | absent → active | new page; `before` is zero bytes with its SHA-256/base64 |
| modified | active → active | rendered region of an existing page changed |
| retired | active → retired | article absent; uses the same retirement notice as the update |
| reappeared | retired → active | article is back and reuses its saved path |

The old side comes from the saved baseline, rebuilt from verified provenance and hash-checked,
**never from locally edited pages**; the new side uses the same renderer and retirement rules on the
explicit snapshot. Pages that stay retired or have identical bytes are not listed; CSV mode yields an
empty list. Raw field changes and rendered changes need not correspond one to one: unknown field
changes may not affect a page, while a change of annotation order may affect only the page. Page
identity is expressed by path and lifecycle; namespace, article ID and annotation identity/anchor/UUID
remain in the existing raw changes, and article metadata that is no longer in the provenance is never
guessed from an old retired path.

Even when local pages are missing, linked, have damaged markers or new paths collide, the proposed
rendering changes and every independent diagnostic list are kept in full. `region_changes` **contains
no personal prefixes/suffixes, no "Personal notes" heading of new pages and no colliding path
content**. The default whole-page local diagnostic can still contain personal content when markers
are damaged; add `--managed-only` to disable that fallback. Proposed managed regions themselves may
contain private annotations and are not redacted.

The option requires `--propose-update` with its complete input and the same format/namespace rules.
API: `inspect_managed(path, next_export=parsed_export, format_name="all", include_regions=True)`
(default False; enabling it without `next_export` raises `update_scope`). Without the option, output
is unchanged. Proposed region changes do not affect status or exit code; only local conflicts or
enabled path collisions exit 2, otherwise 0. Invalid arguments/input/scope/baseline exit 2 and real OS
failures exit 3, both without partial stdout.

### Optional: three-way relationships of saved, local and proposed managed regions

```sh
notewrench wallabag-notes --inspect-managed evolving-notes --propose-update \
  examples/wallabag-notes/updates/after.json --region-relationships
# --proposed-regions and --proposed-paths can be enabled independently
```

Only with this option does `proposed_update` gain `region_relationships`, listing **every saved
tracked page** by path, including unchanged, retired and reappearing pages; new untracked paths are
left to the proposed-region/path reports, and CSV yields an empty list. Each entry has `path`,
`saved_state`, `proposed_state` (active/retired), `relationship`, `saved`, `local`, `proposed` and
`unavailable_reason`. The three available sides are `{sha256, bytes}` and cover only managed regions
with complete markers. With S the verified saved region, L the complete local region and P the
snapshot-rendered region or retirement notice:

| relationship | exact byte condition |
| --- | --- |
| unchanged | S = L = P |
| local-only | L != S, P = S |
| proposed-only | L = S, P != S |
| identical-local-and-proposed | L = P, L != S |
| divergent | S, L and P all differ |
| unavailable | the local region cannot be read or extracted safely |

The local page must be a regular file with exactly one complete, correctly ordered pair of markers.
For missing, linked (including parent paths) or non-regular pages, or damaged markers, `local` is null
and `unavailable_reason` is `{code, message}`: `unreadable_page` for links or missing/non-regular
files, `invalid_markers` for damaged or misordered markers. Comparable pages have a null reason. Real
OS permission or read failures still make the whole command exit 3 instead of being reported as
unavailable. **This list never compares or hashes personal prefixes/suffixes, never guesses
boundaries and never follows links**; the default whole-page local diagnostic for damaged markers can
still contain personal content. Relationships are decided purely by byte equality; they say nothing
about mergeability, line overlap or resolutions.

The option requires an explicit complete input and the same format/namespace set. API:
`inspect_managed(path, next_export=export, format_name="all", include_relationships=True)` (default
False; enabling it without `next_export` raises `update_scope`). The existing local, raw-change,
proposed-region and path reports are kept separately. Relationships do not change status or exit
code: only local conflicts or enabled path collisions exit 2, otherwise 0, with complete JSON and
empty stderr. Invalid arguments, input, scope or baseline exit 2; real OS failures exit 3; both
without partial stdout.

### Optional: exact byte differences between local and proposed managed regions

```sh
notewrench wallabag-notes --inspect-managed evolving-notes --propose-update \
  examples/wallabag-notes/updates/after.json --region-relationships --local-proposed-regions
```

`--local-proposed-regions` requires `--region-relationships` and therefore an explicit complete input
with the same format/namespace set as the initialization; it is independent of `--proposed-regions`
and `--proposed-paths`. Only then does the report gain `proposed_update.local_region_changes`, sorted
by saved tracked path, each entry `{path, local, proposed, unified_diff}`. Both sides are
`{sha256, bytes, base64}` with the exact bytes of the complete managed region including markers; the
diff headers are `path:local` / `path:proposed`, invalid UTF-8 is shown escaped, and base64 restores
the bytes losslessly.

Only pages whose local region can be extracted safely and differs from the proposed region are
included (local-only, proposed-only, divergent). `unchanged` and `identical-local-and-proposed`
produce no diff; `unavailable` keeps its explicit reason in the relationship list only, **without
falling back to a whole-page comparison**. Retirement notices and reappearing tracked paths are
included; new untracked pages are excluded; CSV yields an empty list. The same verified baseline,
next rendering/retirement semantics and safe local reads are used; the local page must be a regular
file with exactly one complete, correctly ordered pair of markers. Personal prefixes/suffixes, link
targets and untracked colliding content are never included; the default independent whole-page
diagnostic can still contain personal content of pages with damaged markers.

API: `inspect_managed(path, next_export=export, format_name="all", include_relationships=True,
include_local_regions=True)` (default False; missing the relationship switch or `next_export` raises
`update_scope` before any bundle I/O). Other reports, default output, status and exit codes are
unchanged: local conflicts or enabled path collisions exit 2, otherwise 0; invalid arguments, input,
scope or baseline exit 2 with no stdout; real OS failures exit 3 with no stdout. Nothing is merged,
repaired or written, and no future update is guaranteed to succeed.

## Input, identity and no-lost-annotation rules

Only **Wallabag v2 export arrays** are accepted; API envelopes, JSONL, HTML and other top-level
formats are rejected. Article `id` is a positive decimal integer or string (at most 128 digits after
normalization); file names are never derived from titles. A missing or null title/url stays in the
provenance with a warning; unsafe or missing URLs produce no clickable link. `annotations: []` is
valid; older upstream fixtures omit `annotations`, which yields zero annotations with an
`absent_annotations` warning. An explicit `annotations: null` or any other non-array is an error.

Every annotation needs a string `quote`, a string `text` and an array `ranges`. A missing quote/text
or a wrong field type is an error; an empty quote is kept with a warning, and quotes are never
guessed from HTML, XPath or offsets. `ranges` are kept as opaque JSON and unknown structures only
warn. JSON numbers are kept as integers/Decimals, so unknown range numbers never become strings or
float approximations. Invalid UTF-8, duplicate JSON keys, NaN/Infinity, lone surrogates and
excessively deep structures fail explicitly.

**Upstream format narrowing: annotation IDs are not guaranteed.** In the upstream `Annotation`
entity, `quote/text/ranges` belong to the `entries_for_user`/`export_all` serialization groups while
`id` does not, and the JSON export uses `entries_for_user`. The command therefore never assumes that
every real export has annotation IDs. Sources and pinned commits are in the
[format reference](wallabag-notes/format.md).

- With an annotation ID, the identity is `id:<normalized ID>`. The single-account anchor is
  `bagnotes-` + `SHA256(compact, key-sorted UTF-8 JSON [source_id, identity])`, all strings; the
  Logseq block ID is `uuid5(NAMESPACE_URL, "urn:bagnotes:" + anchor)`. The identity of the same
  source/annotation ID stays stable when quote, comment or ranges change or inputs are reordered.
- Without an annotation ID, the identity is `content-sha256:<digest of the complete annotation
  object>`, with a warning. The digest ignores key order and includes extra fields; changing any
  field changes the identity. **This does not recover an upstream ID**: an anonymous annotation
  before and after an edit cannot be recognized as the same, and identical independent anonymous
  highlights cannot be told apart.
  [`examples/wallabag-notes/with-ids.json`](../examples/wallabag-notes/with-ids.json) shows the
  behaviour when IDs are available.
- Within one namespace/source ID, records with the same identity and identical content are merged;
  every original occurrence and annotation object is kept in the sidecar. ID `9` and `"009"`
  normalize to the same ID; any other differing field is a conflict. The same annotation ID under
  different sources is independent. Repeated source IDs must have identical article metadata
  (including `content`), otherwise a conflict is reported.
- Order is **the first-seen order of articles in the array, then the first-seen order of
  annotations within the article**; positions start at 0. Nothing is sorted by ID, title, date or
  range. Merging annotations of repeated articles follows the same rule.

`provenance.json` keeps the article metadata (including unknown fields), every original annotation
field, ranges, occurrence positions, anchors and the SHA-256 of every input file. **The only article
field deliberately not archived is `content`**, listed explicitly under `omitted_fields` and in the
report; input files are never modified. The CSV does not carry extra fields or empty articles; they
remain in the sidecar. Markdown escapes markup in displayed text and normalizes line breaks; JSON and
CSV are the exact string sources. JSON does not promise identical whitespace, key order or number
spelling. Repeated exports of the same input and mode to different new directories are byte-for-byte
identical; no timestamps or absolute paths enter file contents.

## Limitations and related tools

- This is an offline file export with an optional complete-snapshot update: no network sync, no paid
  Readwise, no fetching of article bodies, no PDF/HTML quote derivation and no writes to online
  instances. XPath is never executed, and the original locators are not guaranteed to stay accurate
  across HTML versions.
- Only Logseq Markdown **file graphs** are supported; imports were not verified in the Logseq GUI,
  and DB graphs are unsupported. HTML/Markdown/Logseq syntax in the input is escaped as text, so rich
  text inside annotations is not preserved.
- Namespaces are maintained by you; reusing one label by mistake can produce conflicts, and
  independent accounts with identical content cannot be distinguished automatically. The
  single-input form is for one account only; merging accounts requires explicit namespaces.
  Conflicts require you to choose input snapshots and retry.
- `--inspect-conflicts` checks input merge conflicts; `--conflict-values` adds the raw values but
  provides no text diff or automatic repair. Managed-region edits in an existing output directory are
  checked separately with `--inspect-managed`.
- Inputs and outputs are processed in memory; there is no capacity or timing benchmark for large
  libraries. Python's JSON recursion and integer length limits apply, and oversized or overly deep
  inputs fail.
- Plain exports create a new directory exclusively and try to delete the files they created on an
  ordinary write failure; `--update` only updates directories it initialized. A killed process or
  power loss does not guarantee directory-level atomicity; `report.json` is written last as the
  completion marker. Leftovers after a failed cleanup are never overwritten automatically, and
  concurrent untrusted filesystem changes are out of scope.
- The CSV keeps string values as they are, including annotations starting with `=`; they are not
  rewritten into spreadsheet-safe values. Import the file as text to avoid automatic formula
  evaluation.

[bagcarry](https://github.com/Rezarys/bagcarry) already covers related migration and annotation
handling, and the overlap is acknowledged. `notewrench wallabag-notes` focuses on local annotation →
Markdown/Logseq/CSV export, range provenance and identity auditing; it is not the full-text archive
of `notewrench archive`, and it claims no unmeasured advantage over other tools.
[Wallabag #8643](https://github.com/wallabag/wallabag/issues/8643) documents annotation problems when
exporting and re-importing; [#1130](https://github.com/wallabag/wallabag/issues/1130) shows the demand
for annotation export.
