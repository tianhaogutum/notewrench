# notewrench joplin-dupes

Find duplicate notes in Joplin exports — typically left behind by importing the
same JEX twice or merging notebooks from several machines — and review them
before anything is merged. Use it when you want an auditable duplicate **review
plan** for one or more JEX archives or raw export directories, and optionally a
new, deduplicated JEX built only from keepers you explicitly chose.

`notewrench joplin-dupes` is read-only toward your data: it never changes source
exports, imports into Joplin or deletes live notes. It needs no API, credentials,
Joplin installation, database access, model, network or server. Repeated JEX
imports motivated this tool; [Joplin issue 3724](https://github.com/laurent22/joplin/issues/3724)
is a historical 2020 report, **not proof of a current Joplin defect**.

Formerly the standalone `joplin-dedupe` tool.

**Platforms:** Linux and macOS (POSIX directory-descriptor reads with
`O_NOFOLLOW`); Windows is not supported. Input and output paths with a symlinked
ancestor are refused, so on macOS, where `/tmp` and `/var` are symlinks, use real
paths such as `/private/tmp/...` or a directory under your home folder.

**Reads / writes:** reads JEX archives (never extracted) and flat raw export
directories, never modifying them. It writes the review to stdout or to a new `-o`
file, a new decision template with `--decision-template`, and, only with
`--apply-decisions`, a new directory containing `deduplicated.jex` and
`rewrite-report.json`. Nothing is ever overwritten.

## Quick start

```sh
pip install notewrench
notewrench joplin-dupes --help
```

From a clone of the repository, review the bundled synthetic samples. They
contain invented notes and attachment text, with no user or upstream fixture
data; their bytes use the actual Joplin serialization layout.

```sh
notewrench joplin-dupes examples/joplin-dupes/synthetic.jex
notewrench joplin-dupes examples/joplin-dupes/raw --format markdown
notewrench joplin-dupes examples/joplin-dupes/synthetic.jex --format markdown -o review.md
```

The first two commands find three notes: one exact group containing note IDs
`00000000000000000000000000000001` and `00000000000000000000000000000002`;
no near group; the same-title third note is excluded because its body differs.
The two copies use different attachment IDs with equal bytes, NFC/decomposed
Unicode and LF/CRLF, different notebook origins and creation timestamps.
Those contextual differences remain in the group's conflicts.

`examples/joplin-dupes/raw/` and `examples/joplin-dupes/synthetic.jex` encode the
same data. The JEX is an uncompressed USTAR archive generated with Python's
standard library, with fixed mtime=0. Records use Joplin's title/body/terminal-metadata
serialization; binaries live in `resources/`. The raw records intentionally contain
mixed CRLF/LF line endings and legitimate empty `key: ` metadata values (the
repository's `.gitattributes` keeps these bytes exact); do not normalize them. The
generator is `example_files()` in `tests/joplindupes/synthetic.py`, and the test
suite checks that the shipped bytes match it.

For your own offline exports, use Joplin's **JEX** or **RAW — Joplin Export
Directory** export, rather than Markdown or a live profile/sync directory:

```sh
notewrench joplin-dupes backup.jex other-raw-export -o review.json
```

Input ordering assigns `s1`, `s2`, …; every occurrence has a source-qualified key
such as `s1:<note-id>`. The same ID in two snapshots remains two occurrences;
changed content or metadata under that ID appears in `identity_conflicts`.
Reports are deterministic for unchanged input bytes, absolute paths, source order
and Python Unicode database. JSON goes to stdout by default. `-o` creates a new
UTF-8 file with mode 0600, outside all raw input directories, and refuses to
replace any existing file, symlink or hardlink. Output directories must exist and their ancestors must not be symlinks.
The default action is review-only. Archive creation requires the separate
decision workflow below; there is no delete command or script execution.

The review is also available from Python:

```python
from notewrench.joplindupes import analyze, load_export

report = analyze([load_export("first.jex"), load_export("second-raw-export")])
```

## Inspect one note without changing the review scope

```sh
notewrench joplin-dupes examples/joplin-dupes/synthetic.jex --inspect-note s1:00000000000000000000000000000001
notewrench joplin-dupes left.jex right-raw --target-pair s1 s2 --inspect-note s2:00000000000000000000000000000001 -o note.json
```

`--inspect-note` selects exactly one occurrence by its full lowercase
`sN:<32-hex-note-id>` key. Input order still assigns source keys. Supply the
option once; bare IDs, uppercase IDs, malformed keys, unknown sources and
unknown or non-note IDs fail with exit 2. The view requires JSON (the default)
and cannot be combined with `--decision-template`, `--apply-decisions` or
`--output-dir`. `-o` retains the report path, private-mode and no-overwrite
protections above. Invalid input or selection produces no report stdout or file.

Every input is loaded and the complete analysis runs once **before lookup**.
An invalid later export still fails even if the selected note is in an earlier
valid input. `--target-pair` retains its existing correspondence meaning: it
does not restrict loading, group membership or third-source evidence.

The separate JSON schema has `schema_version: 1`, `view: "note_inspection"`,
`read_only: true` and `selected_key` exactly matching the selector. Its `scope`
declares `analysis: "all_inputs"`, `selection: "one_source_qualified_note"` and
`relationships: "complete_groups_containing_selected_note"`. `note` is the
existing complete note assessment, including metadata, context, signatures,
resource evidence, ordered outgoing `note_links` and `blocked_reasons`.
`exact_duplicates`, `near_candidates` and `identity_conflicts` contain every
group containing that key, with **all** members, conflicts, reasons and actions
preserved in their original order. Other members' complete note assessments
and unrelated groups/items are omitted; this is not a complete global inventory.

`incoming_note_links` lists literal incoming occurrences from the completed
analysis: only existing `note_links` whose `target_key` equals `selected_key`.
Each entry has `referring_key` (the source-qualified referring note key),
`link_index` (zero-based index in that note's complete ordered `note_links`)
and `link` (the unchanged existing link evidence, including status, candidates,
counts and correspondence scope). Entries follow full-report note order
(input source order, then note ID), then per-note link order. Repeated links,
self-links and cyclic or blocked references remain separate occurrences.
For example, two links from the same note produce two entries with distinct
indices. Resource occurrences do not occupy `note_links` indices.
Same IDs in another source and content-equivalent `candidate_targets` do not
create incoming edges, including candidates outside `--target-pair`.
An empty array means no literal incoming occurrences in the existing analysis;
it proves neither uniqueness nor a complete interpretation of all references.
This inventory uses the existing literal URL scanner and does not recompute
matching, resolve new reference forms or change blocking/group evidence.

`assessment_status` is `"blocked"` when the note has blocking reasons and
`"assessed"` otherwise. `has_review_relationships` says whether any of the three
group inventories is nonempty. An assessed singleton has `false` and empty
groups; a blocked note can also have empty groups and has not been proven unique.
The view retains the full analysis's `sources`, `target_pair`, `policy`,
`summary`, `metrics` and `warnings`, plus an explicit scope/approval `notice`.
Exit status uses that full analysis: an unrelated warning, unknown item or
blocked note still yields exit 3 even when inspecting an assessed singleton.
This read-only projection chooses no counterpart or keeper and approves no merge.
Omitting the option preserves the ordinary full report and decision workflows.

## Inspect one existing exact group

Copy the full `signature` from an `exact_duplicates` entry in a complete JSON
review, then use the same inputs, input order and correspondence scope:

```sh
notewrench joplin-dupes first.jex second.jex --inspect-exact-group '<full-signature-from-exact_duplicates>' -o group.json
```

`--inspect-exact-group` accepts exactly one full lowercase 64-hex signature.
Prefixes, uppercase, malformed or unknown signatures, repeated selectors and
signatures belonging only to singleton notes or near families fail with exit 2.
The mode requires JSON and cannot accompany `--inspect-note`, Markdown,
`--decision-template`, `--apply-decisions` or `--output-dir`. Invalid selections
or inputs produce no report stdout or file. `-o` retains the private mode,
outside-input, anchored-path and no-overwrite protections above.

Every input is loaded once and the complete analysis runs once before lookup.
An invalid later input still fails. No matching is recomputed or member selected
as a keeper. With `--target-pair`, excluded-source members and all global
correspondence evidence remain; use the same scope as the original review.

The separate schema has `schema_version: 1`, `view: "exact_group_inspection"`,
`read_only: true` and `selected_signature` exactly matching the selector.
Its `scope` declares `analysis: "all_inputs"`,
`selection: "one_existing_exact_group"` and
`relationships: "complete_groups_intersecting_selected_group"`.
`group` is the unchanged existing exact group, including every member, conflict
value and action. `notes` contains each member's complete existing assessment in
`group.members` order, with source-qualified keys; equal IDs in separate exports
remain separate occurrences. `near_candidates` and `identity_conflicts` contain
every group intersecting those members, in original order with complete members,
conflicts, reasons and actions. Thus an exact subgroup's larger near family
remains visible. Full assessments of nonmembers and unrelated inventories are
omitted; the focused view is not a complete global inventory.

`sources`, `summary`, `warnings`, `metrics`, `policy`, `target_pair` and `tool`
retain the full analysis context. Exit status is still 0 or 3 from that full
analysis: unrelated blocked notes, warnings and unknown items still matter.
The notice requires review of all conflicts, a human keeper decision and
explicit approval before merging. This projection grants no approval and is
not a decision template. Default and note-inspection output are unchanged.
The view uses existing reader/analyzer evidence only, with no new reference or
matching interpretation. Full loading/analysis cost remains, and the complete
encoded view must fit the existing 64 MiB report limit; it is never truncated.
Validated with original synthetic raw/JEX exports, without a live Joplin or
production-data claim.

## Create a new JEX from human decisions

First generate a full review and an **unapproved** decision template:

```sh
notewrench joplin-dupes examples/joplin-dupes/synthetic.jex --decision-template decisions.json > review.json
```

Read the complete conflicts in `review.json`. Edit `decisions.json` yourself:
for each group you approve, set `"approved": true` (a JSON boolean) and set
`"keeper"` to one of its full source-qualified member keys. For this example,
the only group's keeper can be `"s1:00000000000000000000000000000001"`.
Keep `signature`, `members`, `sources` and `schema_version` unchanged.
If you used `--target-pair`, also keep `target_pair` unchanged and pass the same
pair when applying decisions. Scope is bound even when the exact groups happen
to be identical under another scope. Existing templates without `target_pair`
remain valid for the default all-input assessment only.
Remove entire groups you do not want to merge: omitted groups and all other
notes remain separate. A group left in the file with `approved: false` fails;
there is no default keeper or automatic approval option.

```sh
notewrench joplin-dupes examples/joplin-dupes/synthetic.jex --apply-decisions decisions.json --output-dir reviewed-copy
notewrench joplin-dupes reviewed-copy/deduplicated.jex
```

Expected: a new private directory containing `deduplicated.jex` (two notes,
both resource binaries, both tag relations and both notebooks) and
`rewrite-report.json` (all ten old item IDs mapped to new IDs, one explicitly
omitted duplicate record, reference rewrites and validation results). The
original JEX remains unchanged. Applying the initial, unapproved template
instead fails with exit 2 and creates nothing.

The same flags support multiple input snapshots, including overlapping old IDs.
Every retained item gets a new deterministic, source-qualified ID; approved
non-keepers map to their chosen keeper. The keeper's complete parsed title,
body and metadata are selected, preserving text without NFC normalization;
serialization uses LF. All other notes, folders, resources, tags and tag
relations are retained. Tag relations from every merged occurrence point to
the keeper; notebook placement comes from the keeper. No arbitrary field merge
is attempted. The report preserves complete parsed records of omitted notes,
so their conflicting metadata and original text remain reviewable.

Known structural fields (`parent_id`, `conflict_original_id`, `note_id`,
`tag_id`) and literal `:/<32-hex-id>` URLs in titles, bodies and metadata are
rewritten, including incoming links to merged notes. Each occurrence is logged
with its field and old/new target; URL occurrences also have an offset. Every
item has a source-qualified `old_key`, `old_id`, `new_id`, type, action and
keeper in `id_map`. All resource bytes, including unreferenced ones, are copied
and checked against their original SHA-256 and size. Resource coalescing is
deliberately outside this workflow.

Decisions must exactly match complete current exact groups. Subsets, near
groups, repeated members, invalid keepers and duplicate JSON keys fail. Source
paths, order, format and a manifest fingerprint of **every member's original
bytes and path** bind approval to the reviewed snapshots. A serialization-only
record change or a change to any attachment invalidates it; tar header/padding
differences alone do not. Resources are copied through a second safe read and
the complete source fingerprint is rechecked. No decision can bypass missing
resources or unresolved context/links. All inputs must be supported, unencrypted
and free of warnings. Notes blocked **only** by empty/whitespace bodies, cyclic
note links, ambiguous note-target correspondence or incoming links to those
notes may remain in the new archive. They cannot participate in approved exact
groups. Their original source-local target identity is preserved through the ID
map; the writer never chooses an equivalent target for an ambiguous link.
`retained_blocked_notes` in the rewrite report lists each such note's `old_key`,
`new_id` and original `blocked_reasons`. A `target_blocked` reason is permitted
only if every dependency is also supported and all references/resources validate.
Missing targets/bytes, unsupported markup/state, encryption, unknown item types
and other blocked evidence still fail, even on unrelated notes. Review mode and
its exit 3 semantics are unchanged; a validated approved copy returns 0 even
when a subsequent review still finds these comparison blocks.
Re-reviewing the combined archive can also find additional correspondence
ambiguity: previously separate equivalent targets now belong to one export.
For the partial-approval fixture below, two original blocked notes become four
blocked notes on output review. `retained_blocked_notes` records the original
assessment; reachability validation does not claim that output review is clear.
`post_rewrite_assessment` records a separate review of the reparsed combined
archive: its `summary`, `blocked_notes` (output `id`, `blocked_reasons`,
`origins` and `note_links`),
`warnings` and `review_exit_code`. `archive` names `deduplicated.jex` and
`archive_sha256` binds this evidence to its bytes. This assessment uses default
single-export correspondence (`target_pair: null`); the top-level `target_pair`
still describes the original approval scope. Output reasons contain remapped
target IDs. Each blocked output note's `origins` lists every source occurrence
mapped to it, including approved non-keepers, ordered by `old_key`. Each origin
contains its `old_key` and original source `blocked_reasons`; empty source
reasons identify an origin that became blocked only after combining exports.
The output note's ordered `note_links` are copied from the reparsed archive's
assessment, with literal URL/target identity, status, candidate targets,
correspondence counts/scope and any proven target signature. Keys such as
`s1:<id>` within these links refer to the **single output export**, while origin
keys refer to the original inputs. Ambiguous links retain their actual target
and all candidates without choosing a counterpart; incoming blocked dependencies
may have no candidates. Empty notes have an empty link list. These fields contain
no full note bodies, metadata or staging paths; `id_map` still records every item.
The nested review code can be 3 while successful archive creation returns 0;
it reports remaining comparison evidence, without approving further merges.

`post_rewrite_assessment.remaining_exact_groups` lists exact groups still present
in that same reparsed output, in review signature order. Each group contains its
output `signature`, `action: "review_only"` and ordered `members`; each member has
an output `id` and all source `origins` in the format above. The partial-approval
fixture retains one four-member Detail group, alongside its four blocked notes.
Fully approved clean output has an empty inventory, as does the retained
cycle/empty fixture; blocked notes cannot be exact members. These groups are
informational and separate from the original `approved_groups`: no keeper is
chosen and no new approval is inferred. Exact still describes content/resources/
state agreement. Each group's `conflict_fields` lists the sorted keys of that
reparsed review group's `conflicts`, including context and metadata field names
when they differ. For the partial-approval fixture these are
`["normalized_body_sha256", "resources"]`, reflecting the different literal
resource IDs. A remaining group with no conflicting fields has `[]`; it still
requires human review. Only names are included, without conflict values. Run a
separate review of `deduplicated.jex` for complete conflicts before preparing
any further decisions. This compact inventory omits
full note bodies and metadata, shares the existing report size budget, and
does not change archive bytes. Its provenance is the local reparsed archive and
the complete source-qualified ID map, verified with original synthetic fixtures.

`post_rewrite_assessment.remaining_near_candidates` separately lists the reparsed
output's near families in review signature order. Each entry contains its output
`signature`, unchanged ordered `reasons`, `action: "manual_comparison_required"`
and ordered `members` with output `id` and complete source `origins` in the same
format above, including approved non-keepers and original blocked reasons.
Each entry's `conflict_fields` contains exactly the sorted keys of that reparsed
near group's `conflicts`, without values. Names include context and metadata
differences, even when the family's reason describes only task-state differences;
missing versus explicitly empty metadata is preserved as a field-name conflict.
These are informational candidates for manual comparison; they do not establish
duplicates, select keepers or approve merges. Their signatures and membership
come from the output review, independently of the original approval scope.
The partial-approval fixture has two such families (Index and Middle), each
with two output members and three original occurrences, and reasons
`["note_link_target_differs_or_unproven", "incomplete_or_unsupported_evidence"]`.
Both have `conflict_fields: ["blocked_reasons", "content_signature",
"normalized_body_sha256", "note_links"]`. These names identify evidence to
compare manually; they do not select a corresponding target or a keeper.
All four blocked output notes and their link evidence remain available in
`blocked_notes`. Near families can also have no blocked notes, such as a
remaining task-state variant; near candidates alone keep review exit 0.
Clean fully approved output and the retained cycle/empty fixture both have
`remaining_near_candidates: []`. The inventory shares the existing report
budget and omits full bodies, metadata and conflict values. Review the output
archive separately for complete evidence before making further decisions.

Before creating the output directory, the staged archive is reparsed by this
project under the configured limits. Selected records must round-trip exactly,
all known structural references and literal internal URLs must reach the
correct item types, and every resource must match. The combined output must
fit the same per-export limits; increase those explicitly for large combined
snapshots. Decision and rewrite JSON each have the existing 64 MiB budget.
Validation errors return 2 without creating the directory. The output directory
must be new, outside raw inputs, with an existing parent and no symlink
ancestors. It is mode 0700; its two files are 0600. Existing paths are never
replaced. Ordinary publication errors remove this run's partial outputs;
process termination or machine failure during publication can leave an
incomplete directory, which must be inspected before reuse. Multi-file source
reads are not atomic snapshots. No live Joplin import has been tested;
reachability is established by this project's pinned-format reader and local
synthetic fixtures. Custom plugin ID fields/schemes are not interpreted.

Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | Assessment complete, or an approved JEX and rewrite report were created. Near groups alone still return 0. |
| 3 | A report was produced, but warnings, blocked notes or unknown item types require attention. It may still contain independently assessable exact groups. |
| 2 | Structural, safety, limit, decision, argument or output error. Invalid input produces no review; failed archive validation creates no output directory. Existing outputs are never replaced. |

## Matching policy and evidence

1. Read terminal `key: value` metadata, preserving all keys as strings. Interpret
   type_ 1/2/4/5/6 as note/folder/resource/tag/note_tag. Body colons and blank lines
   are body data. See [Format sources and scope](#format-sources-and-scope) for
   pinned upstream format evidence and explicit scope narrowing.
2. Normalize note body **only** with Unicode NFC and CRLF/CR → LF. Case, spaces,
   Markdown, link labels, trailing newlines and blank lines remain significant.
3. Scan literal `:/<32-hex-id>` internal URLs. A known resource becomes a
   structured signature segment containing its binary SHA-256 and MIME. This
   permits remapped attachment IDs, while preserving reference order,
   multiplicity and surrounding text. Every binary file is hashed once per
   input, including unreferenced ones, and then reused for its references.
4. Compare known note-link targets from the leaves upward. Replace a target ID
   with its exact content/resource/state signature **only** when that target's
   entire dependency chain is assessable and its signature identifies at most
   one note per export in the link's correspondence scope (all inputs by
   default; see the pair option below). Two content/state-equivalent targets in
   any scoped export make correspondence ambiguous; links to them stay blocked
   for manual review. Cycles, incoming dependencies on cycles, missing targets and
   blocked targets also prevent exactness. A target's title/context may differ,
   as with ordinary exact candidates; their conflicts remain visible.
5. Exact signatures require equal body/resource/note-target segments, Markdown mode and
   `is_todo`, `todo_due`, `todo_completed`, `is_conflict`,
   `conflict_original_id`, `deleted_time`. Missing legacy state values default
   conservatively to 0/empty; explicit malformed state blocks exactness. TODO,
   conflict or trash differences **never** share an exact group.
6. Near candidates share the identical normalized body shape after internal URL
   tokens are replaced with placeholders, but differ in resources/MIME/state,
   note targets or have incomplete evidence. They are **not duplicates**. An exact subgroup can
   also appear inside a broader near family. Same title alone and arbitrary
   text similarity do not qualify a note as a near candidate.

“Exact” is content/state agreement under this policy, not full-record equality
or permission to delete. Title, timestamps, tags, notebook paths and arbitrary
metadata may differ; every differing field is retained with per-source values,
including missing vs empty metadata. Titles are intentionally review fields;
a renamed note can share content. Empty/whitespace-only bodies cannot be exact.
Tags preserve tag IDs, titles, metadata and note_tag relation IDs. Notebook
ancestry preserves IDs, names and metadata; missing/cyclic ancestry and dangling
tag relations generate warnings. All non-note metadata and binary hash/size/path
inventories are included even if unreferenced. Unknown numeric item types retain
their complete parsed record in `unknown_items` and cause exit 3.

Reports include `exact_duplicates`, `near_candidates`, `identity_conflicts`,
`notes`, `non_note_items`, `resource_files`, `unknown_items`, `warnings`, summary
and counters. Each note's `note_links` retains the original URL token (including
ID casing), lowercase ID, source-qualified `target_key`, `status`,
`candidate_targets` and proven `target_exact_signature` (otherwise null).
Every link also records `correspondence_sources`, `candidate_counts_by_source`,
`ambiguous_sources` across all inputs and `scope_ambiguous_sources` that actually
block normalization. Counts/candidates cover assessable equivalent targets;
blocked targets remain in the complete `notes` inventory with their own reasons.
Statuses are `normalized`, `ambiguous_target`, `target_blocked`, `unresolved` or
`cyclic_dependency`; the last also covers incoming dependencies on a cycle.
Unknown internal IDs cannot be classified as notes versus resources, so their
unresolved evidence appears in both inventories. Link order, repeated references,
labels, fragments and surrounding text remain significant. Changed target
content/state produces distinct signatures and near reason
`note_link_target_differs_or_unproven`; it does not join an exact group.
Markdown includes readable group lists and the **complete** JSON
evidence in a safely sized code fence. Reports contain private metadata if your
input does; keep them local as appropriate.
The separate **Blocked notes** section lists exactly the source-qualified note
keys with nonempty `blocked_reasons`, copying notes and reasons in report order
as inline code. These entries require manual review; they do not establish
duplicate status or target correspondence, choose a keeper or approve a merge.
An empty inventory says `No blocked notes.` Complete note and link evidence
remains in the embedded JSON, separately from identity, exact and near overviews.
Each review uses its own input namespace: reviewing a newly created archive
uses `s1` and remapped output IDs, while the original review's keys refer to
the original exports. The overview does not join these namespaces or change
review exit codes, decision policy or archive creation.
Each exact-group overview bullet preserves member order, the conflict count and
the instruction to review all conflicts. It adds sorted conflicting field names
in inline code, labeled manual review, including context and metadata differences
such as missing versus explicitly empty values. `none` means an exact group has
no conflicting fields; it still requires review. An absent exact inventory
instead says `No exact duplicate groups found.` These names contain no values
and do not choose a keeper or approve a merge.
Each near-candidate overview bullet keeps the review's member and reason order
and lists its sorted conflicting field names in inline code, including metadata
and context fields. `none` means the conflict field set is empty; the group still
requires manual comparison. The overview contains names only, without conflict
values, keeper selection or approval. Read the complete evidence before decisions.
The separate **Identity conflicts** section lists each original note ID, its
source-qualified occurrences in report order, and sorted conflicting field names
in inline code. These entries require manual review: the same ID across snapshots
does not imply equal content, select a keeper or approve a merge. Values stay in
the complete JSON evidence. Context/state changes and missing versus empty
metadata remain visible as conflict names. An empty inventory says
`No identity conflicts found.`; exact and near inventories remain separate.

### Select a pair for note-target correspondence

When reviewing two exports alongside an unrelated third snapshot, you can limit
target uniqueness checks for the two selected exports:

```sh
notewrench joplin-dupes first.jex second.jex third.jex --target-pair s1 s2 -o pair-review.json
```

`s1`, `s2`, … refer to positional input order. Supply exactly one pair of
distinct existing source keys; reversed order is equivalent and reports use
input order. Invalid/unknown/repeated selections fail with exit 2 and no output.
The Python API accepts `analyze(exports, target_pair=("s1", "s2"))`.
The report's `target_pair` is the canonical pair, or null for the default.

Only links originating in the selected exports use pair uniqueness. Excluded
exports still use the all-input rule. Every source, note, conflict and resource
stays in the report, and `candidate_targets`/counts still include equivalent
targets outside the pair. `ambiguous_sources` can therefore be nonempty on a
normalized link when `scope_ambiguous_sources` is empty. No arbitrary target
correspondence is chosen. The option does not filter exact-group membership:
otherwise assessable content-equivalent notes, including excluded leaf notes,
can still share exact groups.

Concrete synthetic example (fixtures in `tests/joplindupes/synthetic.py`): compare
`linked_files(0)`, `linked_files(1000)` and `linked_files(2000)` with a second
identical Detail note (`uid(2004)`) added to the third export. Default: one four-note
Detail exact group, six blocked Index/Middle notes, exit 3. With
`--target-pair s1 s2`: three exact groups (the Index pair, the Middle pair and
all four Detail notes), two blocked third-export notes, exit 3. The normalized
selected Middle links still list all four target candidates and `s3` ambiguity;
the excluded Middle link remains `ambiguous_target`, its Index `target_blocked`.

Duplicates **inside** the selected pair still block incoming chains. Changed
target content/state/resources cannot match; missing, blocked and cyclic
targets remain blocked for exact comparison. Excluded warnings/blocked notes
still affect review exit status. Reachable, supported ambiguous/cyclic/empty
notes can remain unmerged during writing under the preservation rules above.
To review or write only two exports, supply only those input paths. Pair scope
alone never approves a merge.

Archive contract for the same three-export fixture: approve only the complete
Index and Middle pairs and omit the four-member Detail group from decisions.
Ten input notes become eight output notes, with all three resource binaries
retained. The excluded Index still links to its own Middle; that Middle still
links to its original Detail, and all four unapproved Detail notes stay distinct.
Archive creation returns 0 after reachability validation; the source review
still returns 3 with two blocked notes. The rewrite report records those two
original blocks and four post-rewrite blocks, matching a separate review of
`deduplicated.jex` (exit 3). Adding either excluded note to an
approved group fails with exit 2 and no output directory.

## Safety boundaries and limitations

- This is a deliberately narrow **flat raw/JEX** reader: lowercase 32-hex
  `<id>.md` root records and `resources/<id>[.extension]` binaries. Unsupported
  nested Markdown/front-matter exports, `.resource` sync layouts, SQLite
  profiles, sidecars (including note-lock keys), compressed tar, malformed
  metadata, duplicate paths/IDs, ambiguous resource files and multiline titles
  fail explicitly. No fallback interpretation silently drops records.
- Encrypted or locked notes, encrypted resources, missing attachment bytes,
  unresolved internal URLs, resource size/extension inconsistencies and HTML
  notes are unassessable for exactness, retained with blocked reasons (exit 3).
  Re-export decrypted Markdown notes for a complete assessment. Other readable
  notes may still be assessed. Unknown item types are reported, not decrypted.
- Note-link normalization is conservative: cycles and duplicate-content target
  ambiguity require manual review, even when original IDs agree. Ambiguity in
  any supplied export blocks that target correspondence by default; the explicit
  pair option limits uniqueness checks for selected-source links while keeping
  excluded-source checks and evidence. No arbitrary match is selected. External
  URLs/files are never fetched. All literal internal URL
  tokens, even in code spans, are inspected; this can conservatively block a
  note with a literal unresolved ID. Custom plugin attachment schemes outside
  `:/<id>` are not interpreted; their body text remains literal evidence.
- Tar is never extracted. Traversal, absolute paths, backslashes, symlinks,
  hardlink tar members, special files, sparse entries, duplicate member names,
  nonzero trailing/concatenated archives and missing tar trailers are rejected.
  Raw input symlinks (including ancestors), special files and nested directories
  are rejected; reads use directory descriptors and `O_NOFOLLOW`.
- Default limits **per input**: 20,000 entries, 32 MiB per file, 4 MiB per metadata
  record and 256 MiB total payload. Override with `--max-entries`,
  `--max-file-bytes`, `--max-metadata-bytes`, `--max-total-bytes` (positive bytes).
  Tar physical size is also bounded by total payload + 4096 bytes per allowed
  entry + 10240 trailer bytes. JSON evidence has a fixed 64 MiB output budget;
  exceeding it fails rather than truncating. Split snapshots for larger reviews.
  Folder ancestry stops at 128 levels with a visible warning.
- Use stable, trusted export snapshots. Changes observed during reads cause
  errors, but reading multiple files is not an atomic filesystem snapshot.
  Body/metadata stay in memory within the above budgets; resources are hashed
  in 128 KiB chunks. SHA-256 collision resistance is assumed, not proven.
- No live Joplin connection, notebook merging, live restore/import, automatic
  deletion, fuzzy semantic matching, attachment decryption or performance claim
  for production libraries.
- POSIX only (Linux and macOS); see **Platforms** at the top of this page for the
  symlinked-ancestor rule (for example, `/private/tmp/backup.jex` rather than
  `/tmp/backup.jex` on macOS).

## Reproducible benchmark

From a clone, `python3 benchmarks/joplin-dupes/benchmark.py --families 40 --repeats 3`
generates a labeled synthetic corpus in a temporary directory and compares a
title-only baseline with this tool's body + resources + state policy. It
generates 40 independent families / 240 notes / 721 files with
120 gold duplicate pairs. Each family has three content/state copies (one
renamed), one changed attachment, one completed TODO, and one different body
with the same title. Gold labels are constructed independently of predictions.
Title-only reads all metadata and no resource bytes. This tool's pipeline
includes safe reads, hashes, grouping and report construction. Trials are
sequential, warm-cache, generation excluded; perf_counter records all samples.
It measures pair precision/recall and real hashing work, not promised speedup.

[Recorded run](../benchmarks/joplin-dupes/results.json), Linux x86_64/Python 3.14.4:

| Method | TP / FP / FN | Precision | Recall | Resource hashes / bytes | Note SHA-256 operations | Median seconds |
| --- | --- | --- | --- | --- | --- | --- |
| Title-only baseline | 40 / 360 / 80 | 0.100 | 0.333 | 0 / 0 | 0 | 0.004932 |
| Body + resources + state | 120 / 0 / 0 | 1.000 | 1.000 | 240 / 295760 | 960 | 0.020048 |

Times will vary on replay; deterministic counts/labels must agree (the test suite
replays a small corpus and checks them). The richer
method is slower on this small synthetic corpus. Results establish behavior on
these cases only, not general precision/recall, production scale or superiority
over other software. `note_signatures` counts notes evaluated; `note_hashes`
counts actual body/content/shape/exact digest operations (four per fully
assessable note), separately from per-file resource hashes.

## Format sources and scope

Checked 2026-11-11 against Joplin dev commit
`c8efb344252b5f84580c839491158c097f77f212`. These are original observations,
not copied implementation or fixtures.

- [Official import/export documentation](https://joplinapp.org/help/apps/import_export/)
  describes JEX as tar containing notes and metadata, and raw as the same data
  written to a directory. This is distinct from Markdown/front-matter exports.
- [Raw exporter](https://github.com/laurent22/joplin/blob/c8efb344252b5f84580c839491158c097f77f212/packages/lib/services/interop/InteropService_Exporter_Raw.ts)
  writes serialized records at the root and copies binary resources to
  `resources/`, preserving each binary's basename.
- [JEX exporter](https://github.com/laurent22/joplin/blob/c8efb344252b5f84580c839491158c097f77f212/packages/lib/services/interop/InteropService_Exporter_Jex.ts)
  delegates to raw export and creates an uncompressed tar from the resulting
  relative file paths.
- [BaseItem](https://github.com/laurent22/joplin/blob/c8efb344252b5f84580c839491158c097f77f212/packages/lib/models/BaseItem.ts)
  defines `<id>.md` item paths. Serialized title and nonempty body precede the
  terminal property block, separated by blank lines. Deserialization examines
  properties from the end, then treats the first header line as title. Properties
  are not Markdown front matter, and body colons are not metadata delimiters.
  A note_tag record has only the property block. The upstream serializer does
  not append a final newline; this reader tolerates final metadata newlines.
- [Model types](https://github.com/laurent22/joplin/blob/c8efb344252b5f84580c839491158c097f77f212/packages/lib/BaseModel.ts)
  assign 1=note, 2=folder, 4=resource, 5=tag, 6=note_tag. Other types exist; this
  tool retains their complete parsed records under `unknown_items` and exits 3.
- [Resource path utilities](https://github.com/laurent22/joplin/blob/c8efb344252b5f84580c839491158c097f77f212/packages/lib/models/utils/resourceUtils.ts)
  construct binary filenames from the resource ID plus an extension. Extension
  can come from metadata or MIME fallback, or be absent. Internal URLs use
  `:/<id>`. The tool locates a unique exported binary by its ID; it does not invent a
  MIME-to-extension table. A present explicit extension must match the file.
- [Raw importer](https://github.com/laurent22/joplin/blob/c8efb344252b5f84580c839491158c097f77f212/packages/lib/services/interop/InteropService_Importer_Raw.ts)
  reads root metadata and remaps imported item IDs/internal links. Thus byte
  equality of metadata files is insufficient for detecting re-imported content.
  Its note-lock handling also establishes that locked ciphertext is not readable
  note content. The tool never decrypts it or treats it as exact evidence.

Only the flat, plaintext Markdown raw/JEX subset is assessed for exactness.
No recursive notebook-per-directory Markdown export, `.resource` sync target,
SQLite profile, compressed tar, multiline title, uppercase/nonhex item filename,
or new note-lock key sidecar is guessed into this format. Unsupported paths or
malformed records fail with exit 2. Known encrypted/locked notes and HTML notes
are retained as blocked evidence with exit 3. Resource encryption flags, absent
binaries, size/extension inconsistencies and unresolved internal links prevent
exactness. Missing notebook/tag context remains visible as warnings. Notes with
empty/whitespace-only bodies also lack sufficient evidence for exactness.
The resource layout is `resources/`, following the actual exporter, not
`.resource/` or `_resources/`.

## Related tools

[Joplin Notebook Manager](https://github.com/Abhishek-Amogha/Joplin-Notebook-Manager)
is related existing work with duplicate-management overlap.
[python-duplicate-file-finder](https://github.com/LuredbyLogic/python-duplicate-file-finder)
is a related file deduplication tool. This tool's scope is specifically Joplin
note records, remapped attachment IDs, resource bytes and state/context review,
rather than raw duplicate files. No code from those projects was reused and no
comparative performance was measured. Joplin is an independent upstream project;
notewrench is not affiliated with it. Attribution details are in
[THIRD_PARTY.md](../THIRD_PARTY.md).

Review and rewrite reports keep the original identifier `"tool": "joplin-dedupe"`,
all schema versions are unchanged (decision templates made before the rename remain
valid), and rewritten item IDs use the same deterministic derivation.
