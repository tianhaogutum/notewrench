# notewrench zotero-logseq

Keep a Logseq Markdown file graph in step with your Zotero library without
touching your notes. `notewrench zotero-logseq` reads a **CSL-JSON export** (for
example from Better BibTeX) and gives each stable citekey one Logseq page. Updates
replace only an owned, hash-checked bibliography region; everything you write
outside it stays byte-for-byte intact. Use it when you keep literature notes in
Logseq and want re-exports to refresh metadata safely, with conflicts reported
instead of overwritten.

Deleted source items are reported as stale and never deleted. A separate, explicit
plan/apply workflow can move verified stale pages into a recovery archive and
restore them later; a read-only audit inspects interrupted archive or restore
attempts.

Formerly the standalone `zotlog-sync` tool.

**Reads / writes:** reads the CSL-JSON file (or, in restore and audit modes, an
archive batch) and the graph; writes only inside the graph's `pages/`, its
manifest/lock files and the recovery archive, plus decision files you name outside
the graph. The CSL-JSON input is never modified. No running Zotero or Logseq,
network access, credentials, external command or model is used. Close Logseq and
other graph writers before applying changes, and back up an existing graph first.

**Platforms:** Linux and macOS (it uses POSIX `fcntl` advisory locks and atomic
renames); Windows is not supported. Paths are not resolved through symbolic
links: a graph, input or plan path with a symlinked ancestor is refused. On
macOS, `/tmp` and `/var` are symlinks, so use `/private/tmp/...` or a directory
under your home folder.

## Quick start

```sh
pip install notewrench
```

From a clone of the repository (the sample export is under `examples/zotero-logseq/`):

```sh
notewrench zotero-logseq --help
notewrench zotero-logseq examples/zotero-logseq/library.json --graph ./my-graph --dry-run
notewrench zotero-logseq examples/zotero-logseq/library.json --graph ./my-graph
```

`my-graph` is a file graph root, not its `pages/` subdirectory. A new directory is
allowed. Reports are JSON on stdout. Dry-run validates and plans without creating
the graph, lock, manifest, temporary files, or pages. It does not acquire a lock;
a concurrent sync can make its preview obsolete. `python -m notewrench
zotero-logseq ...` works the same way.

The synchronizer is also importable:

```python
from pathlib import Path
from notewrench.zoterologseq import synchronize
report = synchronize(Path("examples/zotero-logseq/library.json"), Path("my-graph"), dry_run=True)
```

## Command reference

```text
notewrench zotero-logseq SOURCE --graph GRAPH [--dry-run]
notewrench zotero-logseq SOURCE --graph GRAPH --archive-plan FILE
notewrench zotero-logseq SOURCE --graph GRAPH --archive-apply FILE
notewrench zotero-logseq BATCH  --graph GRAPH --restore-plan FILE
notewrench zotero-logseq BATCH  --graph GRAPH --restore-apply FILE
notewrench zotero-logseq BATCH  --graph GRAPH --recovery-audit [--fail-on-ambiguity]
```

| Option | Meaning |
| --- | --- |
| `SOURCE` | CSL-JSON array (sync and archive modes) |
| `BATCH` | a completed archive batch directory (restore and audit modes) |
| `--graph GRAPH` | Logseq file graph root (contains `pages/`); required |
| `--dry-run` | plan the sync without writing or locking the graph |
| `--archive-plan FILE` | write a new dry-run orphan decision file outside the graph; no sync |
| `--archive-apply FILE` | revalidate an orphan decision and move pages into a recovery archive; no sync |
| `--restore-plan FILE` | write a new restore decision outside the graph |
| `--restore-apply FILE` | revalidate a restore decision and move batch pages back without overwriting |
| `--recovery-audit` | read-only audit of archive/restore evidence; no lock, moves or writes |
| `--fail-on-ambiguity` | with `--recovery-audit`, exit 2 for ambiguous evidence |
| `--version` | print the version |

The mode options are mutually exclusive. Exit codes: **0** success, **2** invalid
or unsupported input, conflicts or opted-in audit ambiguity, **3** I/O failure.
No pages are ever deleted.

## Input contract and real example

Export a library or collection to a UTF-8 **CSL-JSON array**. Inspect the export:
`id` must already be your intended stable string citekey. CSL also permits numeric
IDs; this tool deliberately rejects them. **A Zotero internal item key is not
inferred to be a Better BibTeX citekey.** Better BibTeX documents export translators
and auto-export, but the user must verify their selected export's actual `id`
values. If a separate `citation-key` is present, it must equal `id`; a mismatch is
an error, not an automatic rekey. We do not parse BibTeX or Zotero's native JSON.

[examples/zotero-logseq/library.json](../examples/zotero-logseq/library.json) is wholly synthetic data in the
published CSL shape: two works with identical titles and distinct IDs. A minimal
accepted record is:

```json
[{"id":"Doe2024","type":"article-journal","title":"Local notes","DOI":"10.1/demo","author":[{"family":"Doe"}],"issued":{"date-parts":[[2024]]}}]
```

`type` and `id` are required by CSL; optional missing fields remain missing. An
example generated page is [examples/zotero-logseq/Doe2024.md](../examples/zotero-logseq/Doe2024.md). Output uses:

| CSL field | Logseq output |
| --- | --- |
| `id` | `pages/<id>.md`, top-level `citekey::`, stable SHA-256 identity |
| `type` | managed block property `csl-type::` |
| `title` | `bibliography-title::` (never used as the page filename/title) |
| `DOI` | `doi::` (unchanged; no resolver requests or DOI matching) |
| `author` | `authors::`, ordered personal/literal names separated by semicolons |
| `issued` | `issued::`, formatted date-parts or explicitly labelled raw/literal text |
| `URL` | `url::` (text, no fetching) |
| `keyword` | `keywords::`, one unmodified keyword string; no delimiter guessing or tag creation |

Source format and identity properties provide provenance. Bibliographic properties
belong to the managed **block**, while citekey/source/identity are **page**
properties at the beginning of the file. Use Logseq block-property queries for
bibliographic fields. Metadata is rendered inert: HTML/Markdown delimiters,
control characters and line separators are entity-escaped, not executed or
interpreted as macros. CSL rich text is displayed literally, not typeset.

Every complete record is also included as fenced JSON inside its managed region.
Unmapped fields such as abstract, editor, container-title and custom data are
**preserved there** and listed in `preserved_unmapped` in the report, not silently
dropped. Their semantics/types are not otherwise validated or mapped; this is
not a complete CSL schema validator. JSON whitespace/object order may change;
JSON values, strings and array order are preserved. Duplicate object members,
non-finite/overflowing floats, invalid UTF-8, and lone surrogates are rejected.
Fractional/exponent numbers must round-trip through Python float serialization
with the same decimal value; otherwise they are explicitly rejected rather than
rounded. Encode arbitrary-precision metadata as strings. Extremely long integer
tokens may also exceed Python's configured JSON digit limit and are rejected.

Supported `issued`: one or two date-parts endpoints, each containing year,
optional month and optional day; integer strings are accepted. Years -9999..-1
and 1..9999, calendar-valid months/days, and forward ranges are checked.
`literal`/`raw` strings are kept verbatim and labelled; their EDTF/natural-language
meaning is **not parsed**. `circa` and `season` (1..4) are retained/displayed.
Dates encoded as bare strings, unknown date members, year zero, month season
codes (e.g. 13), and other unsupported forms produce explicit errors. This is a
documented subset, not a claim that the other forms are invalid in every CSL
processor. Personal names support family/given, particles, suffix and CSL flags;
flags remain in the full source record and do not implement citation-style name
ordering. Mixed literal/personal names and unknown author members are rejected.

## Updates, notes, conflicts, and stale items

The graph has `pages/*.md`, `.zotlog-sync.json` (versioned manifest), and a persistent
empty `.zotlog-sync.lock`. Manifest entries map each exact citekey to its derived
identity, relative page path, and managed-region SHA-256. No timestamps or random
values enter pages or the manifest. Identical input, or only reordered records,
produces identical bytes and does not rewrite unchanged files.

Add notes beneath `- Notes`, or anywhere **outside** the matching begin/end marker
lines. Human text before/after the region is preserved as bytes, including line
endings and a missing final newline. Extra frontmatter can follow the three
stable provenance lines; keep those first three lines unchanged. Treat everything
from the begin marker through the end marker as owned by this tool. Editing it
causes a conflict rather than overwriting your edit. No adoption/force option is
provided. Moving a tracked page also causes a conflict.

All planned pages and the manifest are validated before applying any page writes.
A report with `status: "conflict"` contains a `conflicts` list; **no page or manifest
is changed**. Its created/updated lists are proposed actions only. Rejected cases:

- Duplicate or missing IDs, unsupported fields/types in the mapped subset.
- Untracked files at the desired path, even if they contain copied tool markers.
- Filename/title/alias collisions among IDs, stale IDs, and existing supported
  pages and journals (see the identity preflight contract below); matching uses
  Unicode NFKC + casefold.
- Unsafe IDs: only NFC Unicode alphanumeric characters and `._-`, an alphanumeric
  first character, no trailing dot, maximum 120 UTF-8 bytes, no Windows device
  basenames. Slashes, spaces, namespaces, percent escapes, control characters and
  punctuation outside this set, and the namespace filename separator `___` are
  explicitly rejected, never sanitized together.
- Missing/damaged/duplicate markers, changed managed content or provenance,
  corrupted manifests, missing tracked files, symlinks/hardlinks or nonregular
  source/managed files, and symlinked graph directories/ancestors.

### Existing graph identity preflight

Before creating **or updating** any literature page, the tool reads flat `.md`,
`.markdown`, and `.org` files in both `pages/` and `journals/` (extensions are case
insensitive). Supported identity properties are:

- Markdown: a contiguous initial block of unindented `name:: value` lines,
  including `alias::` and `title::`; leading blank lines and a UTF-8 BOM are allowed.
- Org: a contiguous initial block of `#+NAME: value` directives, including
  `#+ALIAS:` and `#+TITLE:`. Property names are case insensitive in both formats.
- `alias` accepts one literal name, comma-separated literal names, or whole
  `[[page references]]` separated by commas. A reference can contain a comma:
  `alias:: [[Reading, list]], Local reading`. An empty alias has no claims.
  `title` accepts one nonempty literal name, without link wrappers.
- Names may contain Unicode text, ordinary spaces and punctuation except
  brackets, braces, `#`, quotes, backticks, angle brackets, `|`, backslash, `%`,
  `:`, and `;`. Control characters and other whitespace are unsupported.
  Slash-separated names must have nonempty components with no surrounding spaces.
- `Topic/Sub/Child` reserves its full name and parent names `Topic` and
  `Topic/Sub`. Filenames `Topic___Sub___Child.md` use the same reservations.
  Namespace aliases also reserve parents conservatively. The unqualified leaf
  `Child` is **not** inferred to be an alias or citekey.

Filenames stay reserved even if a `title` property overrides their display name.
Only collisions with active or retained stale citekeys are reported; this is not
an audit of all unrelated collisions in a graph. A page may alias its own citekey.
Aliases/titles never establish ownership, change a record's ID, adopt a page,
rekey a manifest entry, or merge literature records. Valid frontmatter and human
notes stay byte-for-byte intact; properties in later blocks do not claim page
identity. All indexed files and the page inventory are checked again before writes.

For example, start with `pages/Reading.md`:

```text
alias:: [[New2026]], Reading list

- My notes
```

Then sync a source containing `{"id":"New2026","type":"book"}` (in the CSL array).
The CLI exits **2** with `status: "conflict"` and a `graph-identity-collision`:
its `sources` name both `pages/New2026.md` (`citekey`) and `pages/Reading.md:1`
(`alias`, value `New2026`). Any other pending updates are blocked too. Every page
and the manifest remain unchanged, including in dry-run. Removing the collision
allows the batch to proceed; the tool never edits aliases to resolve it.

#### YAML frontmatter subset (read-only)

Markdown pages and journals may instead start with a YAML document delimited by
exact, unindented `---` lines (optional BOM/leading blank lines; LF or CRLF).
This is a restricted identity reader, not a general YAML loader:

- `alias` or `aliases` accepts one scalar, a single-line flow list
  (`alias: [Reading, "Local notes"]`), or a block list whose items start with
  exactly two spaces and `- `. `[]` means no aliases. Use only one alias spelling.
- Complete single-line flat flow lists may have **one optional trailing comma**,
  e.g. `alias: [Other, New2026,] # reading`. This reserves `New2026` at the
  original key line (line 2 after an opening `---`). With a CSL array containing
  `New2026` and a pending `Existing` update, dry-run and apply both exit **2** with
  `graph-identity-collision`, leaving every page and the manifest unchanged.
  Noncolliding `alias: [Other,] # reading` permits that update and preserves the
  YAML page's exact bytes and mtime; dry-run changes no graph bytes or mtimes.
  Ordinary spaces before/after the trailing comma are allowed. The same rule
  applies to nonidentity flat lists. Leading/interior empty elements (`[,Other]`,
  `[Other,,Local]`), repeated trailing commas (`[Other,,]`), and comma-only lists
  (`[,]`) still produce `graph-identity-coverage`/exit **2** with no writes.
  Nesting, malformed quotes and title lists remain unsupported, including with
  a trailing comma; comment boundaries and literal-name restrictions are unchanged.
- `title` accepts one nonempty scalar; title lists are unsupported. Keys are case
  insensitive. Scalar/flow-list diagnostics point to the key line; block-list
  diagnostics point to the item line (line numbers include BOM/leading blanks).
- A scalar is a literal name with the same character/namespace restrictions as
  above, additionally **without commas**. Optional matching single or double
  quotes are removed for indexing only; escapes and embedded quotes are unsupported.
  Unquoted values must start with a letter or `_`; YAML booleans/null words
  (`true`, `false`, `yes`, `no`, `on`, `off`, `null`, case insensitive) are rejected.
  Quote such names and names starting with numbers, e.g. `title: "2026 reading"`.
  YAML alias scalars are single names, never comma-separated aliases or `[[refs]]`.
- Other ASCII keys matching `[A-Za-z][A-Za-z0-9_-]*` may carry the same scalar or
  flat-list values; they do not reserve identities. Blank lines and full-line
  comments are allowed. Empty documents are allowed; empty values/items are not.
  All keys must be unique, ignoring case. Unknown structures are rejected even
  under nonidentity keys, so nested identities cannot be silently skipped.
- Trailing inline comments are allowed after scalars, complete flow lists and
  two-space block-list items, including nonidentity metadata. A `#` starts a
  comment only outside matching single/double quotes and after an ordinary space
  (or at the start of the value). For example, `alias: New2026 # reading` and
  `alias: [Other, "New2026"] # reading` both reserve `New2026` at the key line.
  `aliases: # reading` may introduce a block list; `  - New2026 # reading`
  reserves the item at its own line. Comments may contain arbitrary quotes or
  brackets. A `#` inside quotes or attached to a name (`New2026#reading`) remains
  part of the value and fails the existing literal-name restriction; it is never
  silently truncated. Empty scalars/items, malformed quotes and incomplete flow
  lists still fail coverage. Delimiter lines must still be exactly `---`.
- Missing closing delimiters, tabs (even in comments), multiline/folded scalars,
  nested collections/maps, anchors, YAML aliases, tags, merge keys, complex or
  quoted keys, YAML directives, nonexact delimiters, and mixed YAML/native
  property headers are coverage failures.
  Org YAML and YAML following native page properties remain unsupported.

For example, `pages/Reading.md` containing:

```yaml
---
aliases: # reading
  - Local reading # local
  - "New2026" # reserved
title: Reading list # display name
---
- 我的笔记 (human notes)
```

With an input array containing `{"id":"New2026","type":"book"}`, dry-run and
apply both exit **2** with `graph-identity-collision`, citing
`pages/Reading.md:4`, property `aliases`, value `New2026`. Every pending page
update and the manifest remain untouched. With noncolliding aliases, an unrelated
bibliography update succeeds and leaves this entire YAML page byte-for-byte and
mtime unchanged, including BOM, CRLF and a missing final newline. YAML is never
serialized or migrated. Managed literature pages still require their original
three provenance lines first; prepending YAML does not bypass ownership checks.
Properties in the document body are not page identity claims.

Unsupported identity shapes produce a `graph-identity-coverage` conflict with a
path and explanation, **blocking the entire batch**. Examples: YAML outside the
subset above, property drawers, `aliases::`, repeated native alias/title properties,
indented/bulleted initial native properties, multiline continuations,
quoted/markup/collection native values, and URL-escaped filenames. Invalid UTF-8, linked/nonregular page files and nested
directories also block preflight. These are coverage limits, not assertions that
the forms are invalid in Logseq. Only the modern triple-lowbar filename format
is supported; legacy dot-as-namespace mode and custom graph configuration are not
interpreted. Other file extensions, reference-only identities in body text, DB
graphs, and custom graph directories are outside this check. A successful plan
is not a claim of whole-graph Logseq parser equivalence.

An exclusive advisory lock prevents overlapping runs of this tool. Writes use
same-directory temporary files, fsync and atomic per-file replacement; existing
permission bits are preserved. Caught write failures trigger rollback of prior
changes if their bytes still match this run's output. Failed rollback is reported
explicitly. New files are private (`0600`). This is **not a multi-file crash-proof
transaction**: process termination/power loss can leave partial pages with the
old manifest. A later run refuses mismatching hashes/untracked pages. Restore the
pages and manifest together from a consistent backup, preserving any new human
notes separately, then rerun. Do not delete the manifest to reset ownership.
No automated destructive repair is attempted. Advisory locks cannot stop other
applications; keep all graph writers closed. Network filesystems and hostile
concurrent path swaps are outside the supported operating conditions.

Removing an item from the export produces `stale: [{"id": ..., "path": ...}]` on
every run. The page **and manifest mapping remain**; reintroducing the same ID
reactivates it. Renaming a citekey creates a new page and leaves the old one
stale. There is no DOI/title-based merge or automatic note migration. An empty
array marks every tracked item stale without deleting anything.

### Explicit orphan archive plan and apply

Archival is opt-in and never runs during ordinary sync. Use a **complete, current
export** of the same library/collection: absence from that file is the evidence
of removal. The tool cannot distinguish deletion from an accidentally incomplete
export. Both archive modes validate the usual ownership and graph identity rules;
they do not apply pending bibliographic updates or create new literature pages.

For example, sync `examples/zotero-logseq/library.json` first, then export only `Doe2024` to
`current-library.json` (omit `Lin2025`). Run:

```sh
notewrench zotero-logseq current-library.json --graph ./my-graph --archive-plan ./archive-plan.json
# Inspect archive-plan.json before applying it:
python3 -m json.tool archive-plan.json
notewrench zotero-logseq current-library.json --graph ./my-graph --archive-apply ./archive-plan.json
```

The plan command reports `status: "planned"`, `dry_run: true`, and one
`archive_candidates` entry for `pages/Lin2025.md`. It writes **only** the explicitly
named decision file, with private permissions; it does not lock or modify the
graph. The output must be a new file outside the graph, distinct from the input;
existing files are never overwritten. An existing ownership manifest is required.
An empty source array explicitly plans all tracked pages. No subset selection or
hand-edited decision files are supported.

Only manifest-owned pages absent from the validated source, with unchanged
managed regions and provenance, enter the plan. Missing or damaged tracked pages
block the entire operation, including other eligible pages. Untracked handwritten
pages never become candidates. Notes outside the managed region may already
exist when planning; the archive preserves them. The decision binds the absolute
graph location, exact input and manifest bytes, and **full page** SHA-256 for every
candidate, plus a SHA-256 of the canonical decision body. These hashes detect
changes, not authenticity against someone who can rewrite both graph and plan.

Apply rechecks the decision against the current source and manifest under the
same advisory lock as sync, then checks page snapshots and inventory again before
moving. A new note after planning, a managed/provenance edit, a missing page, any
manifest byte change, or any source byte change (including reintroducing an ID)
causes exit **2** with a JSON error and no page/manifest changes. Reformatting the
source or manifest also requires a new plan. Active pending updates remain
pending. Archive flags and `--dry-run` are mutually exclusive because planning
is already a dry run.

Successful apply uses same-filesystem rename into
`.zotlog-sync-archive/<decision_sha256>/pages/`, preserving file bytes, inode and
permission bits. It removes just those entries from the active manifest, so
later ordinary sync no longer reports them stale. The JSON result gives
`archive_path` and `archived`. The batch also contains:

- `plan.json`: the reviewed decision, including original paths and page hashes.
- `manifest-before.json` and `manifest-after.json`: exact ownership snapshots.
- `receipt.json`: before/after manifest hashes, entries, and status (`prepared`,
  `complete`, `rolled-back`, or `rollback-incomplete`).

Archives sit outside `pages/` and `journals/` and are not included in this tool's
page identity index. No page is deleted. An empty candidate plan applies as a
no-op. Reusing an applied plan is rejected; an existing batch is never overwritten.
Reintroducing an archived citekey in a later source creates a fresh page; it does
not restore the archived notes automatically.

### Explicit restore plan and apply

Restore takes a **completed archive batch directory** as its positional input,
instead of a CSL export. It restores the entire batch only while the live manifest
still equals the exact saved `manifest-after.json`; there is no manifest merge.
For the `archive_path` returned above, run:

```sh
batch=./my-graph/.zotlog-sync-archive/DECISION_SHA256
notewrench zotero-logseq "$batch" --graph ./my-graph --restore-plan ./restore-plan.json
python3 -m json.tool restore-plan.json
notewrench zotero-logseq "$batch" --graph ./my-graph --restore-apply ./restore-plan.json
```

Replace `DECISION_SHA256` with the actual batch directory name. Planning reports
`status: "planned"`, `dry_run: true`, and `restore_candidates` with original paths
and full-page hashes. It writes only a new private decision file outside the graph;
it never acquires a lock or changes graph files/mtimes. Apply reports
`status: "applied"`, `restored`, and `archive_path`, moves the original whole files
back (preserving bytes, inode and mode), and restores the exact pre-archive manifest.
It does not read a CSL export or update bibliographic content. Reintroduce the
records in your export before the next sync; otherwise they will be reported stale.

Both modes require the batch at its original graph location, an intact `complete`
archive receipt, consistent plan and before/after manifest snapshots, matching full
archived page hashes and managed/provenance checks, absent original destinations,
and the usual live graph ownership/identity coverage checks. New aliases, titles or
namespace filenames that collide with restored citekeys also block the whole batch;
archived aliases are checked against live and restored citekeys too. The decision
binds the batch evidence bytes, graph, exact current/restored manifest hashes and
all candidates. It cannot select arbitrary paths or a subset, even if its self-hash
is recomputed. Hashes detect changes, not authenticity against rewritten evidence.

Changed/missing archived pages (including new notes), changed manifest/evidence,
occupied paths (including handwritten pages or dangling links), incomplete/failed
batches, symlinks/hardlinks/nonregular inputs, modified decisions, identity conflicts
and an already attempted restore all produce exit **2** without moving pages or
changing the manifest. Existing decision files are never overwritten. Apply uses
the sync lock and rechecks snapshots/inventory before writes; keep other writers
closed. Modes are mutually exclusive with sync dry-run and archive modes.

A separate `restore-receipt.json` records `prepared`, `complete`, `rolled-back` or
`rollback-incomplete`. Original archive evidence stays intact. A caught I/O error
attempts guarded rollback without overwriting concurrent handwritten edits, reporting
incomplete rollback explicitly. Failed attempts retain evidence and are not retried
automatically. This is not a multi-file crash-proof transaction: partial/prepared
batches, later manifest changes, and edited archives require manual reconciliation
against a consistent backup, not force restore or automatic note merging.

Caught I/O failures attempt to move pages back and restore the old manifest only
if their bytes still match this operation's output. Concurrent edits prevent
unsafe rollback and are reported explicitly. Recovery metadata remains, including
after successful rollback; inspect it and move a failed batch aside before retrying
the same plan. As with sync, this is **not** a multi-file crash-proof transaction.
An interrupted operation may leave a `prepared` receipt and partial moves; preserve
the batch and use its snapshots with a consistent backup for manual recovery.
Use a local filesystem and keep other graph writers closed. No live Logseq archive
or restore API is used or claimed.

### Read-only recovery audit

Inspect a batch, including `prepared` or `rollback-incomplete` archive/restore
attempts, without a source export or decision output:

```sh
notewrench zotero-logseq ./my-graph/.zotlog-sync-archive/<decision_sha256> --graph ./my-graph --recovery-audit
```

This mode performs no writes, moves, adoption, deletion, or lock creation. It
reads only the batch metadata, its candidate paths, their two parent directories
and the live manifest; it is
not a whole-graph identity audit. Aliases never establish ownership. Close other
writers: this unlocked observation is not a transaction or authorization to act.

For automation that must not treat ambiguous evidence as success, add
`--fail-on-ambiguity` to the same command. The flag is valid only with
`--recovery-audit`; with any other mode it is a CLI usage error (stderr, exit **2**,
no dispatch). Default behavior is unchanged. With the flag, an otherwise
`audited` result whose report-level `ambiguous` is `true` exits **2** instead of **0**.
The JSON is identical to the default audit of the same evidence, the same bounded
reads and single completion recheck run, and no lock, write, mutation or repair
advice is added. Precedence is unchanged otherwise: `partial` with I/O failure stays
exit **3**, conflicts and invalid or unsafe metadata stay exit **2**, and an
unambiguous `audited` result stays exit **0**.

The version-1 JSON contract is:

- `status: "audited"`, `read_only: true`, `conflicts: []`, exit **0** means all
  requested evidence was safely observed; file presence/safety/bytes/identity and
  candidate-parent directory presence/identity matched on a completion recheck,
  even when the attempt is incomplete.
  It does not mean recovery is safe. `archive_path` is graph-relative.
- `receipts.archive` and `receipts.restore` report the recorded status
  (`prepared`, `complete`, `rolled-back`, `rollback-incomplete`); restore is `null`
  if absent. Receipt fields, hashes, entries and manifest transitions must agree.
- `manifest` has `presence` (`present`, `missing`, `unsafe`), `sha256` (null when
  unread), and `classification` (`before`, `after`, `neither`), comparing exact
  live bytes with **archive** before/after snapshots, also for restore attempts.
  Missing, malformed or different live bytes classify as `neither`.
- `pages` is ordered by exact citekey. Each row has `id`, `path`, `expected_sha256`,
  `original` and `archive` observations, `locations` (`original-only`,
  `archive-only`, `both`, `missing`, `unsafe`), and `ambiguous`. Each observation
  has `presence`, `sha256`, `full_hash` and `ownership` (`match`, `mismatch`, or
  `unknown`), and `diagnostic` (null or an explanation). Ownership checks the
  saved pre-archive manifest's managed hash, exact provenance and markers,
  independently of the full-file hash. New handwritten notes yield full-hash
  `mismatch` even if ownership still `match`; they are preserved evidence.
  Both/missing/unsafe locations or any mismatch are `ambiguous: true`.
- `ambiguous` at report level is true for any ambiguous page, a `neither`
  manifest, an incomplete receipt, or locations/manifest inconsistent with a
  completed/rolled-back receipt. No action or overwrite suggestions are
  emitted. Duplicate copies and missing pages are observations, not repair plans.
- Validated reports and every page row include `ambiguity_reasons` arrays.
  An empty array corresponds to `ambiguous: false`. These are explanations of
  retained observations, not authorization or repair advice. They add no reads,
  probes, retries, identity assumptions, locks or writes. Error-only metadata
  failures below include neither arrays nor candidate observations.

  Each reason has exactly the fields listed here; all paths in reason objects
  are absolute. `id` is the saved citekey, `location` is `original` or `archive`,
  `receipt` is the active `restore` receipt when present, otherwise `archive`,
  and `status` is its initially validated status. No diagnostic message is copied
  into reasons; the original diagnostics remain available unchanged.

  | `code` | Other fields | Meaning |
  | --- | --- | --- |
  | `incomplete-receipt` | `path`, `receipt`, `status` | Active receipt is `prepared` or `rollback-incomplete`. |
  | `manifest-mismatch` | `path`, `receipt`, `status`, `expected`, `observed` | Initial manifest classification differs from the receipt direction; `observed` can be `before`, `after`, `neither`, or `unknown`. |
  | `location-mismatch` | `id`, `original_path`, `archive_path`, `receipt`, `status`, `expected`, `observed` | Initial row locations differ from the receipt direction; `observed` is the retained `locations` string. |
  | `both-copies`, `missing-copies` | `id`, `original_path`, `archive_path` | Both safely observed candidate files exist, or both are safely absent. |
  | `full-hash-mismatch` | `id`, `path`, `location`, `expected`, `observed` | Saved full SHA-256 differs from the initially read SHA-256. |
  | `ownership-mismatch` | `id`, `path`, `location` | Initially read bytes fail saved managed-region/provenance checks, independently of the full hash. |
  | `unsafe-initial-directory`, `unsafe-evidence`, `changed-during-audit`, `unreadable-initial-directory`, `unreadable-initial-evidence`, `unreadable-during-recheck` | `path` | Same code/path as an existing conflict, initial error, or recheck error. |

  Direction is `original-only` / manifest `before` for a rolled-back archive or
  completed restore; it is `archive-only` / `after` for a completed archive or
  rolled-back restore. The existing comparison for an incomplete archive uses
  `original-only` / `before`, and an incomplete restore uses `archive-only` /
  `after`; this describes a comparison, not proof of where interrupted work
  should have reached. These three receipt/direction reason codes are
  **report-only**: an otherwise unambiguous row keeps `ambiguous: false` and `[]`
  even when its location disagrees with a receipt. Existing flags do not change.

  Ordering is deterministic. The report lists incomplete receipt, then manifest
  mismatch, then each page in validated order: location mismatch, both/missing,
  original full-hash/ownership mismatch, archive full-hash/ownership mismatch.
  It then appends diagnostics from `conflicts`, `initial_errors`, and
  `recheck_errors`, in their existing order. Rows list their own both/missing and
  hash/ownership reasons in the same order, then applicable diagnostics. A
  diagnostic applies to a row only at its candidate path or immediate parent;
  metadata/live-manifest uncertainty stays report-only. Every row reason appears
  identically in the report. Each array deduplicates equal complete reason
  objects, keeping the first occurrence (different codes on one path remain).

  Unsafe/unknown initial parents use their retained directory diagnostic, never
  invented child read failures or missing-copy/identity claims. Unsafe/unreadable
  individual files use their retained path diagnostic. Independent known hash or
  ownership mismatches are still included. Changed and unreadable rechecks use
  existing diagnostics without interpreting newer bytes or filling unknown
  initial fields. Status and exit **2**/**3** precedence remain unchanged.

- Unsafe page or live-manifest evidence gives `status: "conflict"`, exit **2**,
  an `unsafe-evidence` conflict with its path, and an `unsafe` observation with
  unknown hashes/ownership. Other safely readable candidates are still reported.
  Symlinks (including dangling ones), hardlinks, directories and special files
  are refused without reading their contents; linked ancestors are refused too.
- A change observed between an initial read and the completion recheck gives
  `status: "conflict"`, `ambiguous: true`, exit **2**. Each changed path has
  `{"code":"changed-during-audit","path":"<absolute path>","message":"evidence changed during audit; initial observation retained"}`
  in `conflicts`. Initial receipt, manifest and page observations remain intact;
  affected page rows also become ambiguous. All four required metadata files,
  the optional restore receipt (including its absence), live manifest, and both
  locations of every candidate are rechecked. Missing/present transitions count
  as changes. For safely opened regular files, a different `(st_dev, st_ino)`
  also counts as a change, even with identical bytes and mtime. Identity comes
  from `fstat` on the same open descriptor used to read those bytes, at both
  observations; it is internal comparison evidence, not a new JSON field. This
  covers candidates, live manifest and every validated metadata file, including
  a present restore receipt. No identity is inferred for missing, unsafe or
  unreadable evidence. Stable identity and identical bytes do not conflict: an
  mtime-only change or an identical-byte rewrite of the same inode is accepted.
  New bytes are not interpreted as ownership, receipt status or
  permission to repair. Newly unsafe evidence adds `unsafe-evidence` with its
  path and diagnostic without reading its contents. Initially invalid metadata
  still follows the error contract below; later invalid bytes yield a change
  conflict while preserving the validated initial observations.
- The bounded directory evidence set is exactly `graph/pages` and
  `batch/pages`, observed before metadata/candidate reads and rechecked once at
  completion, after file rechecks. Safe directories compare descriptor device/inode;
  missing directories compare presence only, with no invented identity. A safe
  replacement, disappearance or missing-to-present transition adds the same
  path-specific `changed-during-audit` conflict/exit **2**, even when all candidate
  files beneath that path remain absent or retain their bytes/mtime/inodes.
  All descendant candidate rows become ambiguous; their initial locations,
  hashes, ownership, receipt and manifest classifications remain unchanged.
  Initial unsafe candidate parents (symlink, dangling link, regular or special
  file) are held until independent strict batch metadata fully validates. Then
  the audit retains validated receipts, live manifest and safe opposite candidates,
  returns `status: "conflict"`, `ambiguous: true`, exit **2**, and adds exactly
  `{"code":"unsafe-initial-directory","path":"<absolute parent path>","message":"<safety error text>"}`.
  These directory conflicts appear in `graph/pages`, `batch/pages` order before
  live-manifest/ordered candidate conflicts and completion diagnostics. Each
  descendant has exactly
  `{"presence":"unsafe","sha256":null,"diagnostic":"initial parent directory is unsafe: <absolute parent path>","full_hash":"unknown","ownership":"unknown"}`.
  Affected rows have `locations: "unsafe"`, `ambiguous: true`; unreadable opposite
  observations take precedence with `locations: "unknown"`. Both parents may be
  unsafe. Neither unsafe parents nor descendants are retried/rechecked, even if
  they become safe; descendants receive no `lstat`, open or read, missing
  classification, or inferred identity. Unsafe graph/archive/batch **metadata
  ancestors** still give error-only/exit **2** without following links or opening
  special files. Original-location, strict paths/receipts and the entire ordered
  manifest transition must validate before trusting any candidate.
  Unexpected initial directory I/O is likewise held until metadata validates. Invalid
  metadata still gives error-only/exit **2**; unreadable metadata (including an
  optional restore receipt) still gives error-only/exit **3**, without exposing
  partial observations. When metadata validates, directory I/O gives
  `status: "partial"`, `ambiguous: true`, exit **3**. Its `initial_errors` item is
  exactly `{"code":"unreadable-initial-directory","path":"<absolute parent path>","message":"<I/O error text>"}`.
  Directory errors appear first, in `graph/pages`, `batch/pages` order, followed
  by initial file errors in read order. There is no invented directory identity.
  Descendants of an unknown parent are **not observed or rechecked**. Each has
  exactly `{"presence":"unreadable","sha256":null,"diagnostic":"initial parent directory observation unavailable: <absolute parent path>","full_hash":"unknown","ownership":"unknown"}`;
  each affected row has `locations: "unknown"` and `ambiguous: true`, even if the
  opposite location is readable or unsafe. Unknown is never classified missing.
  Validated receipts, live manifest and safe opposite-location observations are
  retained and independently rechecked. Neither the unknown parent nor its
  descendants are retried; later success cannot fill in initial unknowns.
  Both parents may be unknown. Safely observed missing parents keep the existing
  missing-presence contract. Mixed initial-file/recheck I/O and independent
  change/unsafe conflicts remain partial/exit **3**, retaining all diagnostics.
  Recheck directory I/O adds `unreadable-during-recheck` at the directory path,
  marks descendant rows ambiguous and retains initial observations; partial/exit
  **3** takes precedence over any observed changes. Newly unsafe directories add
  `unsafe-evidence` plus a change conflict without following them.
  Both observations reject symlink/non-directory ancestors; directory descriptors
  use no-follow/directory-only opens and `fstat`, checked against `lstat`.
  Stable directory identity/presence alone does not conflict: mtime changes and
  unrelated children are outside this comparison. Other ancestors are checked
  for safety, but their identities are not compared. No directory listing or
  whole-graph inventory is added to the audit.
- Unexpected I/O on an **initial live-manifest or candidate read**, after all
  batch metadata and receipts validate, gives `status: "partial"`,
  `ambiguous: true`, exit **3**, and a nonempty `initial_errors` list in read
  order. Each item is exactly
  `{"code":"unreadable-initial-evidence","path":"<absolute path>","message":"<I/O error text>"}`.
  That observation has `presence: "unreadable"`, `sha256: null` and the error
  text as `diagnostic`; page `full_hash`/`ownership` are `"unknown"`. An unreadable
  live manifest has `classification: "unknown"`; a row with either location
  unreadable has `locations: "unknown"` and `ambiguous: true`, even if the other
  location is readable or unsafe. No missing classification or ownership is
  inferred from a failed read. Validated receipts and every safe initial
  observation are retained, and other derived candidate/manifest reads continue.
  The failed initial paths are not retried or compared for change; every safely
  observed file path (including missing/unsafe files and metadata) still receives
  its completion recheck. Later success cannot fill in an unknown initial field.
  Mixed initial/recheck I/O and observed-change/unsafe conflicts remain partial
  with exit **3**, retaining both error lists and all conflicts. Without initial
  I/O, `initial_errors` is absent. No lock, writes or repair are introduced.
- Unexpected I/O **during the completion recheck** retains the full initial
  report with `status: "partial"`, `ambiguous: true`, and exit **3**. It adds
  `recheck_errors`, a nonempty list containing one
  `{"code":"unreadable-during-recheck","path":"<absolute path>","message":"<I/O error text>"}`
  per unreadable path, in initial observation order. Receipt statuses, manifest
  classification, page presence/hashes/ownership and location classifications
  stay as initially observed; affected page rows also become ambiguous.
  An unreadable recheck is neither missing evidence nor proof of a change, so it
  adds no change conflict for that path. Other independent paths are still
  rechecked, retaining any `changed-during-audit`/`unsafe-evidence` conflicts.
  If both I/O failures and conflicts occur, `partial`/exit **3** takes precedence.
  Unreadable or newer bytes are never interpreted, and no repair is suggested.
  With no recheck I/O failures, `recheck_errors` is absent and the existing
  `audited`/exit **0** or `conflict`/exit **2** contract remains unchanged.
- Untrusted batch metadata gives only `status: "error", error: <message>`, exit
  **2**, with no candidate observations. This includes missing/corrupt metadata,
  invalid receipts, moved/mismatched batches, unsafe or recomputed arbitrary
  paths, duplicate/subset entries and inconsistent saved manifest transitions.
  Missing restore receipt alone is allowed. A restore receipt requires a complete
  archive receipt and its exact reconstructed restore decision hash.
  Metadata symlinks/hardlinks/nonregular files are refused. Unexpected I/O while
  reading/validating batch metadata (including the optional
  restore receipt) gives the same error-only shape and exit **3**; no candidate
  or live-manifest observation begins until metadata validation succeeds.
  CLI usage errors remain stderr/exit 2.

Archive metadata must be intact at its original graph location. Self-hashes
check consistency, not authenticity against someone rewriting all evidence.
This audit cannot reconstruct missing metadata, discover copies at other paths,
merge changed notes/manifests, retry an attempt, or guarantee a consistent view
under concurrent edits. No graph lock is acquired, even if one is already held.
Rechecking detects observed races, not a transactional snapshot: changes that
revert before a recheck or happen after that path's recheck can escape detection.
Modification-time-only changes are not detected. Device/inode identity is a local
filesystem observation, not a generation counter: inode reuse or a replacement
reverted to the original identity/bytes before recheck can escape detection.
Only the two candidate-parent directory identities above are compared; safe
replacement of other ancestors can escape detection when all bounded directory
and observed file identities/bytes and candidate absences remain the same.

Exit codes: **0** successful plan/apply (stale is informational); **2** invalid or
unsupported input, conflict, opted-in recovery-audit ambiguity (`--fail-on-ambiguity`
only), or CLI argument error; **3** I/O failure. Sync results
and data/I/O errors are JSON on stdout; argparse usage errors go to stderr.

## Scope and alternatives

This is complementary to the existing [zoterolocal](https://github.com/benjypng/logseq-zoterolocal-plugin)
and [logtero](https://github.com/vyleung/logseq-logtero-plugin) Logseq plugins: its
narrow workflow is an offline export → owned file-graph region sync with conflict
reports. There is overlap; we claim neither absence of alternatives nor measured
superiority. The motivating citekey/title issue is
[Logseq #13902](https://github.com/logseq/logseq/issues/13902).

Not supported: Logseq **DB graph** writes, a live API/plugin, attachment download,
PDF extraction, a CSL citation-style engine, BibTeX parsing, rekeying (see
[`notewrench citekeys`](citekeys.md) for migrating citekeys in Markdown documents),
arbitrary filename repair, recursive page discovery, arbitrary Logseq property
parsing, legacy filename mode, a GUI, or Windows. Identity indexing covers the
subset above, not all possible Logseq aliases, references or configuration. Output
is tested against the documented Markdown file format with synthetic records, not
against a running Logseq. Exports and graphs are held in memory; no large-library
performance claim is made.

## Format decisions and sources

These are references, not copied source or fixture data. The parser is an original
restricted implementation, not copied upstream code.

- [CSL input schema](https://github.com/citation-style-language/schema/blob/master/schemas/input/csl-data.json):
  the outer container is an array; `id` can be string or number; `type` and `id`
  are required. DOI, title, URL and keyword are strings; names and dates are
  objects. A stable string `id` is required because a numeric ID does not
  establish a citekey.
- [citeproc-js CSL JSON documentation](https://citeproc-js.readthedocs.io/en/latest/csl-json/markup.html):
  item identity, personal and literal names, date-parts and raw dates. The date
  renderer checks a conservative calendar subset and labels raw/literal dates
  without guessing their interpretation. Not all valid CSL extensions are
  supported; unsupported mapped structures are rejected explicitly.
- [Logseq properties documentation](https://github.com/logseq/docs/blob/master/pages/Properties.md)
  (pinned: [docs commit 628cdde](https://github.com/logseq/docs/blob/628cdde0b6f1d90c4722e2bfebb2fd00a5186825/pages/Properties.md)):
  frontmatter properties belong to the page; indented properties in later blocks
  belong to their block; property values are single-line; property names are case
  insensitive and built-in aliases are comma separated. The first three page
  properties are stable provenance; variable bibliography fields live inside the
  owned later block so all text outside that region survives updates.
- [Better BibTeX exporting](https://retorque.re/zotero-better-bibtex/exporting/)
  and [automatic export](https://retorque.re/zotero-better-bibtex/exporting/auto/):
  the export workflow. Exported arrays are consumed, not their API or application
  internals, and no claim is made that every export translator places citekeys in
  `id`. Check your export. A distinct `citation-key` is never silently substituted
  for `id`.
- [Logseq issue 13902](https://github.com/logseq/logseq/issues/13902): motivates
  avoiding bibliographic titles as stable page identities. Filename identity
  depends solely on the exact stable `id`; title and DOI changes do not rename
  pages or trigger inferred merges.
- [Logseq 0.10.9 page extraction at c67b8b5](https://github.com/logseq/logseq/blob/c67b8b5fa47f8fe1e1954226c9bdfabd46ebb968/deps/graph-parser/src/logseq/graph_parser/extract.cljc):
  `get-page-name` gives an explicit title property priority over filename parsing;
  `extract-page-alias-and-tags` handles aliases; namespace pages are extracted from
  names. The preflight conservatively keeps filename reservations as well as
  explicit titles, and reserves namespace alias parents, without merging records.
- [Logseq 0.10.9 filename/namespace utilities](https://github.com/logseq/logseq/blob/c67b8b5fa47f8fe1e1954226c9bdfabd46ebb968/deps/graph-parser/src/logseq/graph_parser/util.cljs):
  `split-namespace-pages`, `decode-namespace-underlines` and `title-parsing`
  establish slash prefixes and modern `___` filename decoding. The old dot-based
  filename mode and URL-encoded names are outside the supported subset. New CSL
  IDs containing `___` are rejected because a literal citekey filename would
  represent a namespace; existing such manifest keys fail preflight, and no
  automatic rekey or migration is attempted.

The contract deliberately narrows generic CSL: a string safe-filename `id` is
required, numeric IDs and unsafe/namespaced citekeys are rejected, raw dates are
not parsed, and JSON records are preserved rather than fully interpreted.

Compatibility note: the on-disk names written into graphs (`.zotlog-sync.json`,
`.zotlog-sync.lock`, `.zotlog-sync-archive/`, the `zotlog-sync:begin`/`end` region
markers and the `zotlog-source::`/`zotlog-identity::` page properties) keep the
original `zotlog-sync` names, so graphs created before the rename keep working.
