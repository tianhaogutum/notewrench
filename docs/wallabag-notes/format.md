# `notewrench wallabag-notes`: format and schema reference

This page records the upstream format sources the command was written against
(checked 2026-11-11) and the exact schemas of every file and report it writes.
These are original observations, not copied source or article fixtures. Runtime code
never contacts these URLs. Input examples and tests were independently authored.
The command was formerly the standalone tool `bagnotes`; on-disk identifiers such as the
`bagnotes-` anchor prefix, `bagnotes:managed` markers and `tool` values keep that name so
existing bundles remain compatible.

## Wallabag v2

Repository commit (read via `git ls-remote`, no checkout or source bundled):
`2c15fef98033c43d86cf3dcdd97bfe53f3b4f22d`.

- [Import fixture](https://github.com/wallabag/wallabag/blob/2c15fef98033c43d86cf3dcdd97bfe53f3b4f22d/tests/fixtures/Import/wallabag-v2.json):
  top-level article array, string/integer article IDs, title/url/content metadata.
  The six-article fixture has both omitted annotations and empty annotations arrays;
  it provides no non-empty annotation payload to copy or test against.
  `wallabag-notes` accepts omission with an `absent_annotations` warning; explicit non-arrays fail.
- [Entry entity](https://github.com/wallabag/wallabag/blob/2c15fef98033c43d86cf3dcdd97bfe53f3b4f22d/src/Entity/Entry.php):
  source ID and nullable title/URL participate in export serialization; annotations are associated with entries.
- [Annotation entity](https://github.com/wallabag/wallabag/blob/2c15fef98033c43d86cf3dcdd97bfe53f3b4f22d/src/Entity/Annotation.php):
  quote and text are strings, ranges is an array. These three fields belong to both
  `entries_for_user` and `export_all`; annotation ID has no such group declaration.
- [Export helper](https://github.com/wallabag/wallabag/blob/2c15fef98033c43d86cf3dcdd97bfe53f3b4f22d/src/Helper/EntriesExport.php):
  JSON serialization uses `entries_for_user`. Inference from these group declarations:
  exported annotations need not include ID. This was verified by reading the source contract,
  not by running a PHP instance. No claim that every Wallabag version omits ID.

`wallabag-notes` therefore accepts optional annotation IDs and reports a content-based fallback
instead of inventing stable upstream IDs. Versions/other tools that include numeric IDs
are supported, using those IDs for identity and conflict detection.

The `ranges` array is always opaque. A familiar range object with string start/end and
nonnegative integer startOffset/endOffset avoids an opaque-range warning, but this is
only a shape observation, not a locator interpreter or correctness check. Unknown shapes
remain in every output format unchanged as JSON values. No XPath or HTML evaluation occurs.

Missing quote/text fails. An explicitly empty quote is preserved with warning. Article HTML
is intentionally omitted, disclosed in report/provenance; all annotation fields survive.
No assumption that a quote can be reconstructed from a range is made.

## Logseq file graph

Documentation repository commit: `628cdde0b6f1d90c4722e2bfebb2fd00a5186825`.
[Properties](https://github.com/logseq/docs/blob/628cdde0b6f1d90c4722e2bfebb2fd00a5186825/pages/Properties.md)
documents properties in blocks, `key:: value` syntax, UUID `id::` examples and the
restriction that property values cannot contain newlines.

Output uses Markdown list blocks, two-space nesting and single-line deterministic UUID
`id::` on each annotation block. Quotes/comments are escaped text in child blockquotes;
ranges are fenced JSON. No DB graph structures, plugin calls, app configuration or direct
writes to a graph are generated. Tests validate emitted files and IDs; Logseq GUI itself
was not installed or automated.

## Identity encoding v1

JSON identity encoding uses UTF-8, no ASCII escaping, sorted object keys and compact
separators, with exact Decimal number values. It is a `wallabag-notes` encoding, not a claim of
RFC 8785 conformance. Numeric spelling such as decimal trailing zeros can distinguish
content identities; use upstream IDs when available. Anonymous identity covers every
annotation field so that unknown metadata is not silently thrown away in deduplication.

Source/annotation decimal IDs normalize leading zeros; source filenames use normalized IDs.
Anchor is the full SHA-256 of JSON `[source_id, identity]` prefixed with `bagnotes-`.
Logseq UUID is UUIDv5, URL namespace, name `urn:bagnotes:<anchor>`.
The legacy single-file entry point retains provenance schema version 1 and its original
filenames, anchors, UUIDs and CSV columns. It has one implicit account scope and cannot
distinguish independently exported accounts with overlapping numeric IDs.

## Explicit namespaces and offline merge (schema 2)

`--source NAMESPACE FILE` is repeatable; the Python equivalent is
`parse_sources([(namespace, file_bytes), ...])`. Each file independently obeys the pinned
Wallabag v2 array contract above. There is no new upstream envelope or adapter.
Namespaces are user-owned, case-sensitive labels matching `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`.
The old positional input cannot be mixed with these explicit sources.

Article key: `(namespace, normalized source_id)`; annotation key adds `identity`.
The namespaced anchor hashes JSON `[namespace, source_id, identity]` using encoding v1
above; its UUID rule is unchanged. Article filenames use `article-` plus the full hash
of JSON `[namespace, source_id]`, then `.md`. Neither identity depends on filename, input
position, other accounts, or article metadata. Changing a namespace changes identity.

Schema 2 report/provenance use `inputs: [{input_order, namespace, sha256}, ...]` instead
of `input_sha256`, including repeated or empty inputs. Hashes cover each original file's
bytes, not a reconstructed or concatenated document. Article records add `namespace`,
first `input_order`, and `file_stem`; `source_occurrences` becomes a list of
`{input_order, source_order}` objects. Annotation occurrences retain their raw objects
and file-local orders and add `input_order`. The raw upstream data is never modified
to inject namespaces. Markdown/Logseq display namespace; CSV adds `namespace` and
`input_order` columns for the first occurrence. Subsequent occurrences remain in JSON.

Input order, file-local article order, then annotation order govern first appearances.
Repeated/partial exports in the same namespace union their annotations, with exact
duplicates merged. Metadata comparison includes all fields except `annotations`, even
intentionally unarchived article `content`. Identified annotation comparison includes
all fields. Only numeric ID representation is normalized. Any differing field fails
the entire export; there is no timestamp inference or last-writer selection. Anonymous
content hashes retain the same edit/identical-highlight limitations described above.

Diagnostics/warnings use conceptual paths `$inputs[i].articles[j].annotations[k]` for
namespaced files, with input index and namespace; conflicts also include `first_path`
and `differing_fields`. Legacy paths remain `$[j].annotations[k]`. All validation and
rendering finishes before the output directory is reserved. No timestamps, absolute
input paths or random identifiers enter exported files.

`examples/wallabag-notes/accounts/personal.json` and `work.json` are independently authored local
synthetic integration fixtures, both with article `1` and annotation `9`. The tests
also construct anonymous, normalized-ID, partial-export and cross-file conflict cases.

## Read-only input conflict inspection (report schema 1)

`--inspect-conflicts` requires explicit `--source` pairs, forbids positional input and
all export options (`--output`, `--format`, `--update`, `--dry-run`), and requires no
output directory. `inspect_sources([(namespace, bytes), ...])` is the Python API.
It shares the export parser's full input validation and comparison rules, including
article content/unknown fields, numeric ID normalization and opaque Decimal values.
It continues through mismatching article metadata to inspect the annotations beneath.

Each occurrence is compared with every earlier occurrence in the same
`(namespace, source_id)` article scope, or `(namespace, source_id, identity)` annotation
scope. Exactly one record is emitted per mismatching occurrence pair. Identical pairs
emit nothing; repeated inputs can repeat conflicts at distinct locations. Comparisons
include within-file occurrences and all later variants, not just a first-version
baseline. Ordering is current occurrence order (article then its annotations), then
earlier occurrence order. Anonymous edits change identity and do not count as conflicts.
The inspector never returns its intermediate merge model for rendering/publication.

Report keys:

- `schema_version: 1`, `tool: "bagnotes-conflicts"`, `status: "conflicts"` or `"compatible"`.
- `inputs`: `{input_order, namespace, sha256}` for every original input including empty
  and repeated files. CLI adds `file`, exactly as supplied in the corresponding argument;
  the API has no file paths. Hashes cover original bytes, not normalized JSON.
- `counts`: `article_conflicts`, `annotation_conflicts`, and their sum `conflicts`.
- `conflicts`: records with `code`, `namespace`, normalized `source_id`, sorted
  `differing_fields`, `first` and `second`; annotation conflicts additionally have
  `identity`. Each side contains `input_order`, `sha256`, `source_order`, `path`, and,
  for annotations, `annotation_order`. Paths use the same `$inputs[i].articles[j]...`
  convention as merge diagnostics. No raw field values or value diffs are included.
- `warnings`: normal parser warnings for first-seen identities, plus per-occurrence
  absent-annotations warnings; this is not an exhaustive warning scan of each variant.

Conflict reports go to stdout with exit 2 and empty stderr. Compatible reports have
zero counts, an empty conflict list, and exit 0. Invalid input aborts the entire check:
exit 2, structured diagnostic on stderr, no partial report on stdout. File I/O failure
uses exit 3; argument errors use exit 2 and argparse's stderr usage text. The API raises
BagnotesError for invalid data instead of returning a partial report.

Inspection does not render, check an output path, read managed output state, or write
files. Users may redirect stdout themselves and select compatible snapshots for a
subsequent normal export. Compatibility covers input merging only; it does not imply
complete-snapshot suitability, update-state validity or output-path availability.
Normal export/update/dry-run still fail on the first input conflict. No automatic
resolution or latest-snapshot selection is offered. The report is held in memory;
n occurrences of one identity require n(n-1)/2 comparisons and potentially that many
conflict records. No large-library capacity or performance claim is made.

`tests/wallabagnotes/test_conflicts.py` covers three-version pairwise differences,
conflicting article/annotation combinations, hashes/positions, malformed later inputs
and zero writes.

### Optional differing field values (schema 1 extension)

`--conflict-values` requires `--inspect-conflicts`; the API equivalent is
`inspect_sources(inputs, include_values=True)` (keyword-only, default False).
Without opt-in, the report is unchanged. With opt-in, each conflict adds
`field_values`, keyed in `differing_fields` order, with exactly those fields.
Each entry contains `first` and `second`, each with `present: boolean` and a
`value` member if and only if present. Explicit null is `{present: true, value: null}`;
an absent field is `{present: false}`. Empty strings, arrays and objects remain values.
The existing schema version, locations, original hashes, ordering, warnings and
summary remain unchanged. Compatible reports have no additional keys.

Values match decoded original records at the reported locations, including article
content and opaque/nested fields. Comparison still normalizes only top-level article
and annotation IDs; nested IDs are opaque. Equivalent ID spelling produces no
conflict and therefore no field-value entry. Numeric precision is retained with
Decimal/integer JSON numbers, not strings; original number spelling/JSON formatting
is not promised. Values are neither truncated nor redacted. Long content repeated
in many pair reports can substantially increase serialized size and peak memory;
all content/report/serialization remains in memory. Reports may expose private data.

The flag outside input inspection is a usage error (exit 2, stderr only), including
export, update, dry-run and managed inspection. All existing inspection restrictions
and exit codes apply. Any invalid later input or I/O failure still prevents all
partial stdout. No input writes or automatic selection/resolution are introduced.
Synthetic fixtures are `examples/wallabag-notes/conflicts/values-{before,after}.json`;
focused tests are in `tests/wallabagnotes/test_conflict_values.py`. No capacity benchmark is claimed.

## Managed complete-snapshot updates (local state schema 1)

`--update` does not change the pinned upstream input format or either provenance schema.
All input files are parsed/merged first, then treated as a complete snapshot of the same
legacy account scope or exact explicit namespace set. Every article must explicitly
supply `annotations`; omission is rejected instead of inferred as deletion. Format and
scope are immutable for an initialized directory. Ordinary exports remain byte-compatible.

Pages wrap the existing Markdown/Logseq rendering in exactly one LF-delimited pair:
`<!-- bagnotes:managed:start -->` and `<!-- bagnotes:managed:end -->`. State hashes cover
both markers and the full intervening bytes. The prefix and suffix are opaque bytes,
never decoded or newline-normalized. Missing, reordered or duplicate markers fail.
An article absent from the snapshot gets a managed retirement notice in its original
page; personal bytes survive and the same path is reused if the article reappears.

`update-state.json` has `schema_version: 1`, `tool: "bagnotes-update"`, `format`,
`namespaces` (null for legacy scope, otherwise a sorted list), `pages` (relative path to
managed-region SHA-256), and `artifacts` (relative path to whole-file SHA-256).
Artifacts include provenance, report, changes, and CSV when selected. The state file
is written last. It is a local baseline, not an authenticated/tamper-proof manifest.
Do not hand-edit it; retain the complete bundle for subsequent updates.

`changes.json` schema 1 includes `added`, `modified`, `deleted`, highlight `counts`,
and `articles` metadata/lifecycle events. Records use namespace/source ID/annotation
identity; highlight records include the stable anchor and block UUID. Added records
have raw `after`, deleted records raw `before`; modified records have both and sorted
`differing_fields`. Normalized decimal ID spelling, occurrence/input order, and input
byte hashes do not themselves constitute highlight modifications. All other annotation
fields, including opaque ranges and unknown metadata, participate. Anonymous edits are
necessarily delete/add. Article content remains intentionally omitted, not diffed.
Decimal values retain precision in state-related JSON and stdout as in existing output.

A byte-identical rendered snapshot (allowing arbitrary personal edits outside regions)
is a no-op: stdout contains empty changes, all files/mtimes stay unchanged, and the last
applied changes.json remains available. Otherwise changes.json describes only that
application, not a cumulative history. Dry-run validates the same read set and shows the
proposed changes without reserving directories or staging files.

Existing files are all checked before updates; unexpected managed/sidecar edits,
missing files, symlinks and untracked new-page collisions fail before publication.
All new bytes and backups are staged beside the output directory, followed by another
read-set check. Replacements use os.replace, with ordinary-error rollback attempted;
this is not a concurrent-editor protocol or a directory-level crash-atomic transaction.
State cannot be used to adopt a legacy export or an arbitrary existing graph.

The independently authored `examples/wallabag-notes/updates/{before,after}.json` and
`tests/wallabagnotes/test_updates.py` exercise the input contract with no external
credentials, Wallabag/PHP instance, or Logseq GUI.

### Opt-in annotation-deletion budget

`--max-deleted-annotations N` requires `--update`, supports `--dry-run`, and is
rejected with ordinary exports or either inspection mode. N is a nonempty ASCII
decimal digit string, including zero/leading zeros; signs, whitespace, fractions,
exponents and Unicode digits are invalid. Python integer-conversion capacity also
applies. Invalid CLI arguments exit 2 with usage on stderr and no stdout, before I/O.
The keyword-only API is `plan_update(output, export, format_name, *,
max_deleted_annotations=None)`. None disables the guard; otherwise an exact Python
int >= 0 is required (bool, float, string etc. are invalid). Bad API arguments raise
`BagnotesError` code `invalid_deletion_budget`, path `$.max_deleted_annotations`,
before export access, rendering or I/O.

The count is exactly `changes.counts.deleted` / `len(changes.deleted)`: unique
removed (namespace, normalized article source_id, annotation identity) tuples after
merging all explicitly selected next inputs. Occurrences/duplicate inputs count
once. Identical IDs in different articles/accounts count independently. Retiring an
article includes all its annotations; anonymous edits are delete/add. Identified
edits, metadata changes, additions and reordering are not deletions. Already-retired
identities are absent from saved current provenance and are not charged again;
reappearance is addition. Initialization and no-op replay have zero deletions.

After the existing input/scope/state/artifact/page/new-path checks, but before any
staging or publication, deleted > N raises `BagnotesError` with code
`deletion_budget_exceeded`, path equal to the output string, and integer fields
`max_deleted_annotations` and `deleted_annotations`. CLI exits 2, emits that JSON
on stderr, and emits no stdout. Bundle bytes, paths, link targets and mtimes stay
unchanged. Dry-run enforces the same limit and does not return a successful plan on
refusal. Equality passes. Existing conflicts/invalid baselines and actual OS failures
retain their diagnostics and exits. The limit is per invocation, never persisted or
added to successful summaries/sidecars. Defaults and managed inspection are unchanged.
This does not infer completeness, select a snapshot, or repair conflicts.

`tests/wallabagnotes/test_deletion_budget.py` covers the guard with synthetic offline inputs.

### Opt-in managed-only local diagnostics (managed inspection schema 1 extension)

`--managed-only` requires `--inspect-managed DIR`. No next input or proposed flag
is required; it can combine independently with any otherwise valid proposal options.
Export/update/dry-run/input-conflict inspection reject it with usage/2 and no stdout.
Python: `inspect_managed(path, managed_only=True, ...)`, default False, no new
next_export dependency. All existing flag/API dependencies still apply before I/O.

Only with opt-in, schema 1 gains top-level `managed_only: true` and every local
`conflicts` entry gains `unavailable_reason`. No existing conflict is dropped:

| local condition | code | comparison | current / unified_diff | unavailable_reason |
| --- | --- | --- | --- | --- |
| intact ordered marker pair, edited region | managed_edit | managed-region | exact description / diff | null |
| damaged, missing, duplicate, reversed or newline-broken markers | invalid_markers | unavailable | null / null | {code, message} matching conflict |
| missing, linked (including parents), nonregular tracked path | unreadable_page | unavailable | null / null | {code, message} matching conflict |

Keep every original path, code and message. `expected` always retains the verified
saved managed-region `{sha256, bytes, base64}`. For invalid markers, do not compute
or emit a current whole-page description/hash/length/base64/diff, infer boundaries,
or substitute saved/next bytes for local. Use the same existing marker grammar and
safe read; no link following or nonregular reads. Intact current managed bytes,
including invalid UTF-8, retain exact base64/hash/length and display diff.

Artifacts, tracked-page counts/order, raw proposals, proposed paths, saved-to-proposed
regions, relationships and available local-to-proposed regions stay complete and
independent, including retirement/reappearance. No status/exit change: clean 0,
local/artifact/enabled-path conflicts 2, full JSON and empty stderr; invalid next,
scope or verified baseline 2/no stdout; actual OS errors 3/io_error/no stdout,
never downgraded to unavailable. With mode disabled, no new keys, byte-for-byte
unchanged default output (including legacy whole-page diagnostics).

This mode suppresses damaged-marker local whole-page output only. It does not
redact private managed content, raw proposed values, verified expected regions,
paths or artifact hashes. No repair/writes, completeness inference, update-success
guarantee or concurrency protection. Reports remain in memory.
Tests: `tests/wallabagnotes/test_managed_only.py`.

### Opt-in metadata-only managed reports (managed inspection schema 1 extension)

`--metadata-only` requires `--inspect-managed DIR` and implies `--managed-only`.
There is no next-input dependency; explicitly adding `--managed-only` is redundant
and produces identical bytes. Python: `inspect_managed(path, metadata_only=True, ...)`,
default False. All existing proposal dependencies remain: paths/regions/relationships
require an explicit complete next snapshot, and local-to-proposed summaries require
relationships. The CLI still requires `--propose-update` plus input(s) for these flags.
Export, update, dry-run and input-conflict modes reject metadata-only with usage/2
and empty stdout, before I/O. Invalid API dependencies raise `update_scope` before I/O.

Schema 1 gains `metadata_only: true` and effective `managed_only: true`. It is the
following projection of the full managed-only report, with all lists, ordering,
counts, paths, codes, messages, identities, warnings and status retained:

| Location | Metadata-only representation |
| --- | --- |
| `conflicts[].expected`, available `conflicts[].current` | exactly `{sha256, bytes}` for the verified saved / extracted local managed region |
| unavailable `conflicts[].current` | null; comparison `unavailable`, original code/message, `{code,message}` unavailable_reason |
| available `conflicts[].unavailable_reason` | null |
| `proposed_update.changes.{added,modified,deleted,articles}[]` | omit raw `before` / `after` keys; retain all other fields, including identity/anchor/block_id where present, differing_fields and article change/lifecycle |
| `proposed_update.region_changes[].{before,after}` | exactly `{sha256, bytes}`; retain path/change/before_state/after_state |
| `proposed_update.local_region_changes[].{local,proposed}` | exactly `{sha256, bytes}`; retain path |
| `proposed_update.region_relationships[]` | unchanged classification, lifecycle, saved/local/proposed fingerprints and unavailable_reason |
| artifact issues, path collisions, snapshot input descriptors | unchanged |

No `base64` or `unified_diff` keys are emitted in these lists. Diffs and base64 are
not computed. Raw change `before` / `after` and all `unified_diff` keys are **absent**,
not null; the region-change `before` / `after` keys remain fingerprint objects.
Unavailable current/local fingerprints remain **null**; no whole-page fingerprint,
length, inferred boundary or text is substituted for damaged/unreadable regions.
`proposed_update` stays absent without next input; optional lists remain absent
unless requested, and enabled empty lists stay `[]`. Legacy namespaces remain null.

Retained fingerprints cover complete managed-region bytes including markers: verified
saved, safely extracted local, and proposed regions only when requested (new-page
absent-before uses the empty-byte SHA-256/length). Existing relationship fingerprints,
artifact whole-file hashes and next-input hashes remain visible. Also retained:
paths/output directory, namespace/source IDs, annotation identity (including anonymous
content SHA-256), anchors, block UUIDs, field names, parser warning metadata and reasons.
These may be sensitive and permit correlation or dictionary attacks; metadata-only
is content minimization, not anonymity or authenticated integrity. It still reads
private data and holds snapshots/regions in memory; it is not a streaming mode.

Without metadata-only, both default and `--managed-only` detailed output remain
byte-for-byte unchanged. No status/exit change: clean/proposed-only 0; local/artifact/
enabled-path conflicts 2 with complete JSON and empty stderr; invalid input/scope/
verified baseline 2 with no stdout; actual OS failures 3/io_error with no stdout.
No extra path scans, link following, whole-page fallback, writes, repair or merge;
existing snapshot-completeness and concurrency limitations still apply.

Tests: `tests/wallabagnotes/test_metadata_only.py` (metadata combinations,
private managed/raw-value omission, complete independent diagnostics, retirement/
reappearance, deterministic output and unchanged bytes/link targets/mtimes).

### Read-only proposed updates (managed inspection schema 1 extension)

`--inspect-managed DIR --propose-update INPUT` or repeated `--source NAMESPACE FILE`
accepts an explicit next complete snapshot without applying it. `--propose-update`
requires managed inspection and exactly one of these input forms. `--format` is
allowed only with proposal opt-in (default `all`); it and the exact namespace set
must match initialization. Empty namespaces require explicit `[]` inputs. Export
options `-o`, `--update`, `--dry-run` and input-conflict inspection options are refused.

The existing schema 1 `bagnotes-managed-conflicts` report gains `proposed_update`:
`format`, `namespaces` (null or sorted labels), `snapshot` (existing provenance input
metadata: schema 1/input_sha256 or schema 2/inputs), `changes` (changes.json schema 1),
and next-parser `warnings`. No local diagnostic is removed or merged into the
proposal. The saved hash-verified, reconstructible provenance is the old baseline;
neither next inputs nor current page bytes are substituted. Stable identities,
normalization, anonymous delete/add, omitted content and numeric precision follow
updates. Traversal is saved articles followed by new articles in first-seen order;
within each article saved annotations precede newly seen annotations. Each changes
list retains that traversal; differing_fields are sorted. Local artifact/page
diagnostics remain separately sorted by path. Input descriptors and warnings keep
next-parser order, including duplicate/empty inputs and their original byte hashes.

Without `--proposed-paths`, local status alone determines exit: clean is 0 even with nonempty proposed changes;
conflicts is 2 with full stdout and empty stderr. Invalid JSON/fields, conflicting
next inputs, omitted annotations, mismatched format/namespaces or an invalid baseline
fail with exit 2 and no stdout. Actual OS errors remain 3/io_error with no stdout.
No partial report is returned. Without opt-in, managed inspection is unchanged.
Python: `inspect_managed(path, next_export=export, format_name='all')`.

All snapshots are user-selected. Absence means deletion; completeness is a user
assertion, not inferred from data. No publication, repair, force overwrite, snapshot
selection or future-update guarantee. Untracked destination checks require the separate opt-in below.
File/link/directory bytes and mtimes are preserved. Raw private before/after values
remain in memory and are not redacted. Tests in `tests/wallabagnotes/test_proposed_updates.py`
and `tests/wallabagnotes/test_managed_conflicts.py` cover these contracts.

### Opt-in proposed destinations (managed inspection schema 1 extension)

`--proposed-paths` requires `--propose-update` and all its explicit-input/scope rules.
Only with this flag, `proposed_update` adds `checked_new_pages` (count of candidate
page destinations, including blocked ones) and `path_collisions` (sorted by page path).
One issue per blocked page has `path`, `code`, `namespace` (null or label), normalized
`source_id`, `blocking_path` and `kind` (`file`, `directory`, `symlink`, `other`).
`new_page_collision` means the destination exists; `blocking_path` equals `path`.
`unsafe_page_parent` means its format parent is linked/non-directory; `blocking_path`
is `markdown` or `logseq`. Parent issues take precedence; no child lookup occurs.
No link targets or collision contents are read or disclosed. All metadata uses lstat.
Dangling/loop links and special objects also block. Actual OS errors propagate.

Candidates use next Article.file_stem in the initialized page formats, excluding
all saved state.pages paths (including retired tracked paths on reappearance).
CSV has zero. Missing format parents and absent destinations are valid. No directory
scan or unrelated-path inspection. Existing tracked local diagnostics and proposal
changes remain complete, separate and in their prior order with verified old provenance.
With this flag only, top-level status/exit includes path_collisions: any of the three
conflict lists makes conflicts/2; otherwise clean/0, even with proposed changes.
Both return complete JSON and empty stderr. All input/scope/baseline errors still
suppress stdout (2); actual OS failures suppress stdout (3/io_error). Argument errors
are usage/2 before I/O. Without flag, report/status/exit are unchanged.

Python: `inspect_managed(path, next_export=export, format_name='all', include_paths=True)`.
True without next_export raises BagnotesError/update_scope. Default is False.
No plan/publication/repair/overwrite/input selection/completeness inference; file,
link and directory bytes/mtimes stay unchanged. Concurrent replacements unsupported.
Tests: `tests/wallabagnotes/test_proposed_paths.py`.

### Opt-in proposed managed regions (managed inspection schema 1 extension)

`--proposed-regions` requires `--propose-update` and its explicit complete snapshot,
matching format and namespace set. It is independent of `--proposed-paths`.
Only with the flag, `proposed_update.region_changes` is added: changed managed
pages sorted lexically by relative path. Entries contain `path`, `change`,
`before_state`, `after_state`, `before`, `after`, `unified_diff`.
Both sides have `{sha256, bytes, base64}`; base64 preserves exact region bytes
including markers. Diff headers use `path:saved` and `path:proposed`.

Lifecycle transitions: `added` is absent -> active (empty before bytes, with
SHA-256 of empty bytes and empty base64); `modified` active -> active;
`retired` active -> retired; `reappeared` retired -> active.
The saved side is reconstructed from verified provenance and checked against every
saved state.pages hash. The next side uses managed(render(next)) for active paths,
and the same retirement notice as updates for all saved paths absent next.
Verified retirement bytes distinguish inactive saved pages from active renders.
Reappearance reuses saved paths; the union of saved and next paths is considered.
Byte-identical regions (including still-retired pages) are omitted. CSV has none.
Path/transition identify the region; namespace/source_id and annotation identities
remain in existing raw changes. Historical retired metadata is not inferred from hashes.
Raw-only unknown field changes may have no preview; order-only changes may have
previews without raw changes. No personal prefix/suffix/new-page heading or
untracked collision contents are included, even with damaged local markers.
Default whole-page local diagnostics may still contain personal bytes;
`--managed-only` suppresses that fallback.

Local diagnostics, raw changes and optional path collisions remain separate and
complete. Local edits/missing/linked/marker-damaged pages do not alter previews.
No plan or publication is called. Region changes never affect status/exit rules:
local or enabled path conflicts -> 2, otherwise 0, complete stdout/empty stderr.
Invalid next/scope/baseline suppresses stdout (2); actual OS failure suppresses
stdout (3/io_error), even after preview rendering. Invalid flags fail usage/2
before I/O. Without flag, output unchanged. No writes/repair/selection/completeness
inference; no future-update guarantee or concurrency safety. Unredacted managed
content and diffs remain in memory. Python: `inspect_managed(path,
next_export=export, format_name='all', include_regions=True)`; default False,
True without next_export raises BagnotesError/update_scope.
Tests: `tests/wallabagnotes/test_proposed_regions.py`.

### Opt-in three-way region relationships (managed inspection schema 1 extension)

`--region-relationships` requires `--propose-update` and explicit next complete
snapshot(s) matching the initialized format/namespace set. It is independent of
`--proposed-regions` and `--proposed-paths`. Python API: `inspect_managed(path,
next_export=export, format_name='all', include_relationships=True)`, default False;
True without next_export fails update_scope before bundle I/O.
Only with opt-in, `proposed_update.region_relationships` lists **every saved tracked
page**, path-sorted, including unchanged, retired and reappearing paths. New untracked
paths belong to the separate proposed-region/path reports. CSV yields an empty list.
Each entry has `path`, `saved_state`, `proposed_state` (active/retired), `relationship`,
`saved`, `local`, `proposed`, `unavailable_reason`. Each available side is
`{sha256, bytes}` covering the exact managed region **including both markers**.
Saved bytes are reconstructed and hash-verified; proposed bytes use the next render
or the update retirement notice. Equality uses bytes, not just hashes, decoded text,
raw semantic changes, timestamps, or annotation identity inference.

For S=saved, L=local, P=proposed, categories are exhaustive and exclusive:

| relationship | exact condition |
| --- | --- |
| unchanged | S = L = P |
| local-only | L != S and P = S |
| proposed-only | L = S and P != S |
| identical-local-and-proposed | L = P and L != S |
| divergent | S, L, P pairwise unequal |
| unavailable | local managed region cannot safely be read/extracted |

Local requires a regular file with exactly one intact ordered start/end marker pair.
Personal prefixes/suffixes never participate. Missing, linked (including ancestors),
nonregular paths or damaged/duplicate/reordered markers yield `local: null`,
`relationship: unavailable` and `unavailable_reason: {code, message}`. Code is
`unreadable_page` (message identifies a symbolic link or missing/nonregular file)
or `invalid_markers` (message identifies marker damage/order). Otherwise reason is
null. Never guess boundaries, compare whole-page bytes, follow links, or read a
nonregular object for relationships. Saved/proposed hashes remain available.
Default independent local whole-page diagnostics may still include personal bytes
for marker damage unless `--managed-only` is enabled; the relationship list contains no personal bytes or their hashes.

This list never alters status/exits or any other diagnostic list. Local conflicts or
enabled path collisions still mean complete JSON/empty stderr/2, otherwise 0,
even for proposed-only changes. Invalid flags/input/scope/baseline suppress stdout
(usage or structured error/2); actual OS errors still suppress stdout (3/io_error),
never downgraded to unavailable. Without opt-in, existing output is unchanged.
No writes, merge, line-overlap analysis, resolution, repair, identity inference or
future-update guarantee; complete snapshots remain user assertions. Concurrent
edits/replacements unsupported; hashes are integrity checks, not signatures.
Tests: `tests/wallabagnotes/test_region_relationships.py`.

### Opt-in local-to-proposed regions (managed inspection schema 1 extension)

`--local-proposed-regions` requires `--region-relationships` (and therefore
`--propose-update`, explicit complete snapshots and matching initialized scope).
Python: `inspect_managed(path, next_export=export, format_name='all',
include_relationships=True, include_local_regions=True)`. Default False; enabling
without relationships or next_export raises update_scope before any bundle I/O.
It is independent of `--proposed-regions` and `--proposed-paths`.

Only with this flag, `proposed_update.local_region_changes` is added. It is a list
of `{path, local, proposed, unified_diff}`, sorted by saved tracked relative path.
Both sides contain `{sha256, bytes, base64}` for exact managed bytes including both
markers. Diff headers are `path:local` and `path:proposed`; display diffs use the
existing invalid-UTF-8 escaping, with base64 authoritative for lossless bytes.
Include exactly available tracked regions where local != proposed: local-only,
proposed-only and divergent relationships. Omit unchanged and identical-local-and-
proposed regions, even when they differ from saved. Unavailable regions are omitted
from this list and retain explicit reasons in region_relationships; never fall
back to whole-page bytes. CSV produces an empty list. Exclude new untracked pages.

Use the same verified saved scope and next rendering/retirement bytes as the
relationship report, including retirement and reappearance. Local must be a regular
file with exactly one intact ordered marker pair, using the existing safe read;
no personal prefix/suffix bytes, link following, extra destination I/O, plan,
publication or merge. Default independent whole-page diagnostics for damaged
markers may still contain personal bytes unless `--managed-only` is enabled. Other local/raw/saved-to-proposed/path
reports remain complete and unchanged, as do status/exits (clean 0; local or enabled
path conflicts 2). Invalid flags fail usage/2 before I/O; invalid next/scope/baseline
fail 2 with no stdout; actual OS errors fail 3/io_error with no stdout, even after
some comparisons. No update-success guarantee, repair, completeness inference or
concurrency protection. Content is unredacted and held in memory.
Tests: `tests/wallabagnotes/test_local_proposed_regions.py`.

## Evidence and related work

- [Wallabag #8643](https://github.com/wallabag/wallabag/issues/8643): annotation import concern.
- [Wallabag #1130](https://github.com/wallabag/wallabag/issues/1130): annotation export request.
- [bagcarry](https://github.com/Rezarys/bagcarry): related migration tool; overlap acknowledged.

No competitor implementation was used to implement or test this command.
