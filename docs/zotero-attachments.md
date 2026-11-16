# notewrench zotero-attachments

Find out what is really in your Zotero `storage/` folder before you clean it up.
`notewrench zotero-attachments` audits an explicitly supplied, frozen copy of
`zotero.sqlite` together with the matching `storage/` directory and reports, as
verifiable JSON or Markdown: duplicate bytes, missing references, orphan-candidate
directories, unreadable / unauthorized / unsupported inputs, and audit errors. Use
it before deduplicating or pruning attachments, after migrating a library, or to
compare two backups over time. It **never moves or deletes files**.

Formerly the standalone `zotero-attach-audit` tool.

No Zotero installation, account, API key, network access or third-party package is
needed. **Reads / writes:** it reads only the snapshot, `storage/` tree, linked
roots or saved reports you name on the command line (the snapshot is opened
read-only and immutable), never modifies them, and writes only to stdout.

**Platforms:** audits need **Linux** with a usable `/proc/self/fd` (the snapshot is
opened through a pinned file descriptor). On other systems an audit stops with the
structured error `unsupported_platform` (exit 2). `notewrench zotero-attachments
compare`, which reads only saved JSON reports, also works on macOS. Every path
component is opened without following symbolic links, so inputs reached through a
symlinked ancestor are refused (on macOS, `/tmp` and `/var` are symlinks, so use
`/private/tmp/...` or a directory under your home folder). Windows is not supported.

## Quick start

```sh
pip install notewrench
```

From a clone of the repository, audit the synthetic sample (a real SQLite file and
synthetic attachment bytes; `.pdf`/`.png` are only names for byte auditing, not
renderable documents):

```sh
notewrench zotero-attachments --snapshot examples/zotero-attachments/synthetic/zotero.sqlite \
  --storage examples/zotero-attachments/synthetic/storage
notewrench zotero-attachments --snapshot examples/zotero-attachments/synthetic/zotero.sqlite \
  --storage examples/zotero-attachments/synthetic/storage --format markdown
```

The sample yields one duplicate group (`a.pdf` and `b.pdf`), one missing reference
and the orphan candidate `ZZZZZZZZ`. The directories of the trashed attachment
`BCDEFGHJ` and of the regular parent item `DEFGHJKL` are retained. To generate
another synthetic fixture, run
`python examples/zotero-attachments/make_fixture.py ./my-new-fixture`.

On a production copy (replace with the paths of your own **copies**):

```sh
notewrench zotero-attachments --snapshot /backup/zotero.sqlite --storage /backup/storage > audit.json
notewrench zotero-attachments --snapshot /backup/zotero.sqlite --storage /backup/storage \
  --allow-linked-root /backup/linked --format markdown > audit.md
```

Reports go to stdout only; put the shell redirection target outside the input
directories so you never overwrite an input yourself.

The Python API is `from notewrench.zoteroattachments import audit, compare_reports`.

## Command reference

```text
notewrench zotero-attachments --snapshot FILE --storage DIR [options]
notewrench zotero-attachments compare BEFORE.json AFTER.json [--map-linked-root BEFORE_ROOT AFTER_ROOT]... [--format json|markdown]
```

Audit options:

| Option | Meaning |
| --- | --- |
| `--snapshot FILE` | explicit standalone snapshot of `zotero.sqlite` (no WAL/SHM/journal); required |
| `--storage DIR` | storage directory that corresponds to the snapshot; required |
| `--allow-linked-root DIR` | allow reading linked files below this directory; repeatable; symlinks are still refused |
| `--linked-base-directory DIR` | map `attachments:relative/path` references to this absolute POSIX directory; also needs `--allow-linked-root` |
| `--map-linked-root SOURCE DESTINATION` | relocate absolute linked paths from SOURCE to DESTINATION once; repeatable for disjoint roots; authorize the destination separately |
| `--format json\|markdown` | output format (default `json`) |
| `--hash-all` | reference baseline: hash every inventoried regular file |
| `--comparison-scope SOURCE_PROFILE` | record a caller-declared source-profile namespace and all item identities so the report can be compared later; forces a fresh SHA-256 of every file |
| `--version` | print the version |

`compare` options: `--map-linked-root BEFORE_ROOT AFTER_ROOT` (repeatable) and
`--format json|markdown`. Exit codes are listed under [Exit codes](#exit-codes).

## Linked attachments

`--allow-linked-root DIRECTORY` may be repeated. It authorizes only descendants of
that directory, matched by path component: `/papers` does not authorize
`/papers-other`. Symlinks are refused even when authorized. By default unauthorized
linked targets are never stat'ed, opened or hashed; they are reported as
`blocked_linked`, not disguised as missing.

### Explicit mapping for relative linked attachments

If the snapshot contains `linkMode=2, path='attachments:papers/a.pdf'`, map Zotero's
linked-attachment base directory to the directory in your backup:

```sh
notewrench zotero-attachments --snapshot /backup/zotero.sqlite --storage /backup/storage \
  --linked-base-directory /backup/linked --allow-linked-root /backup/linked
```

The reference resolves to `/backup/linked/papers/a.pdf`. If it exists it is reported
`present` and takes part in byte verification together with storage and absolute
linked paths; relative and absolute references to the same path are inventoried
once. If the file does not exist it is reported `missing`, which on its own does not
make the audit incomplete; present identical bytes enter `duplicateGroups`. The
Python API equivalent is `audit(snapshot, storage, linked_base_directory='/backup/linked',
allow_linked_roots=('/backup/linked',))`.

The mapping **does not grant read permission**: an `--allow-linked-root` covering the
target is still required (it may authorize only a subdirectory of the base). Without
it the reference is `blocked_linked` and the target is not inspected. Without a
mapping the reference is `unsupported_path`; the base is never guessed from storage,
the snapshot location, the current directory or Zotero settings.
`policy.linkedBaseDirectory` records the mapping you supplied (`null` if none), and
every reference keeps its original `path` and, when resolvable, its `resolvedPath`.

The base must be an absolute POSIX path. After `attachments:` only a nonempty,
`/`-separated relative file path is supported; empty components, `.`, `..`,
leading/trailing `/`, backslashes, Windows drive/UNC syntax and NUL are rejected, and
URL escapes, environment variables and `~` are not expanded. Out-of-bounds or
ambiguous paths and an invalid base are reported as `invalid_path` or
`unsupported_path`; symlinks at the base, its ancestors, subdirectories or the file
(including dangling links) are reported as `symlink`. These errors and unauthorized
references are recorded in `auditErrors` with `auditComplete=false` and exit code 1,
while the completed storage and attachment evidence is kept. One audit accepts one
base mapping and reads no preferences.

### Absolute linked paths of a migrated library

When the snapshot still references `/original/papers/nested/a.pdf` from the old
machine but the copy lives at `/backup/papers/nested/a.pdf`, name both roots:

```sh
notewrench zotero-attachments --snapshot /backup/zotero.sqlite --storage /backup/storage \
  --map-linked-root /original/papers /backup/papers --allow-linked-root /backup/papers
```

Python API: `audit(snapshot, storage, linked_root_mappings=(('/original/papers',
'/backup/papers'),), allow_linked_roots=('/backup/papers',))`.
`--map-linked-root SOURCE DESTINATION` is repeatable; the API accepts a sequence of
two-item tuples or lists. Matching is by path component and only for descendants of
SOURCE: `/original/papers-other/a.pdf` does not match. A mapping is applied **once**;
when one destination root is also another source root the result is not mapped
again. Zotero settings are not read and the original linked path is never checked
or read; whether it is missing or a symlink does not matter. A missing destination
file is `missing`, with no fallback even if the original file exists.

The mapping **does not authorize**: `--allow-linked-root` is checked only against the
resolved destination, so authorizing the source root cannot read the destination
(you may authorize a subdirectory of the destination root). Unmatched absolute paths
follow the normal allowlist rules. `attachments:` references use only
`--linked-base-directory` and never this mapping; storage and URL references are
unaffected. Destination files take part in duplicate detection by their actual
bytes, and the original absolute reference, the backup absolute reference and an
`attachments:` reference to the same destination are inventoried once.

Source roots, destination roots and absolute linked references must be normalized
POSIX absolute paths: repeated `/`, trailing `/` (except the root `/`), `.`, `..`,
backslashes, Windows/UNC syntax and NUL are rejected. These checks run before path
normalization; there is no URL decoding or `$VAR`/`~` expansion. When you pass a
Python `Path`, information the caller already normalized away cannot be recovered;
pass strings to have the original spelling validated. Source roots must be mutually
distinct and non-nested, as must destination roots, so there is no order-dependent
choice or many-to-one merge. Invalid or ambiguous mappings are rejected before
scanning with `invalid_linked_mapping` even if no reference uses them (the API
raises `InputError`; the CLI prints a structured report and exits 2). A reference
equal to a source root, or an ambiguous / traversing path, is `invalid_path` (exit 1);
a symlink at the destination root, an ancestor, a subdirectory or the file (including
dangling links and replacement before hashing) is `symlink` (exit 1). Unauthorized
destinations are `blocked_linked` (exit 1) and are not inspected. Completed storage
and duplicate evidence is kept; an ordinary `missing` does not on its own make
coverage incomplete.

`policy.linkedRootMappings` records each `source`/`destination` pair (`[]` if none);
attachments keep their original `path` and, when resolvable, the actual
`resolvedPath`. This option relocates **scans**; it does not rewrite identities when
comparing reports. To align roots in a comparison, pass the comparison mapping
described below. Original reports are never modified.

## Compare with an earlier report

First create comparable JSON reports from two controlled snapshots of the **same
source database**, then compare them offline:

```sh
notewrench zotero-attachments --snapshot /backup/day1/zotero.sqlite --storage /backup/day1/storage \
  --comparison-scope my-zotero-profile > /reports/before.json
notewrench zotero-attachments --snapshot /backup/day2/zotero.sqlite --storage /backup/day2/storage \
  --comparison-scope my-zotero-profile > /reports/after.json
notewrench zotero-attachments compare /reports/before.json /reports/after.json > /reports/delta.json
notewrench zotero-attachments compare /reports/before.json /reports/after.json --format markdown
```

The Python API is `audit(..., comparison_scope='my-zotero-profile')` and
`compare_reports(before_json_path, after_json_path)`. The comparison reads only the
two reports; it never opens the snapshots or attachment paths recorded in them, and
it never upgrades, completes or overwrites the original reports. Output again goes
to stdout only; redirect it to a new file that is not one of the inputs.

`--comparison-scope` is a caller-declared **source-profile namespace**, not a backup
directory, date or SQLite file hash. Backups of the same source use the same value;
unrelated sources must use different values. Zotero's local `libraryID` and `itemID`
cannot tell different people's databases apart, and the tool does not guess account
identity. The report stores the declaration, the identities of all items and the set
of observed libraryIDs. A different scope or libraryID set refuses the comparison;
adding or removing an entire library needs a new baseline. Misusing the same scope,
hand-forging a report or pairing the wrong storage with a snapshot cannot be detected
from report hashes: the hashes identify input versions, they are not signatures. An
empty library with no items does not appear in the observed libraryID set.

A comparable scan **re-reads the complete bytes of every inventoried file and computes
SHA-256**, including files with a unique size; it uses no hash cache and never
assumes unchanged bytes from mtime/size. The default size-prefiltered scan keeps its
behavior; an old report made with only `--hash-all` and no scope does not establish
comparison identity. Old reports cannot be completed in place: rescan the retained
snapshot and storage.

The full delta contains:

- `attachmentsAdded/Removed/Changed`: aligned by `(libraryID, key)` within the scope;
  records content, original path, status, parent identity and trash changes; ignores
  local itemID renumbering and relocation of the storage backup root.
- `restoredAttachments`: the same attachment went from `missing` to `present`;
  `disappearedAttachments`: from `present` to `missing`, separate from database
  deletions.
- `filesAdded/Removed/Changed`: storage files by `KEY/relative path`, linked files by
  absolute path; `Changed` means size/SHA-256 changed. Each entry keeps the
  referencing attachment identities and known directory-owner identities; companion
  and orphan files without attachment references are explicitly empty, never
  invented. Renames are shown as remove + add.
- `newOrphans/resolvedOrphans`: directory keys that became or stopped being orphans,
  with previous/current item identities; a vanished directory and a directory that is
  retained again by an item have different `reason`s.
- `duplicateGroupsAdded/Removed/Changed`: groups and members compared by
  `(size, SHA-256)`, listing added/removed members, members' attachment/owner identity
  changes and before/after member evidence. A group that shrinks from two members to
  one is `Removed`; a group of the same bytes whose members were replaced is `Changed`.

Example: between two snapshots `STUVWXYZ/missing.pdf` is restored with the same bytes
as `ABCDEFGH/a.pdf`, `JKLMNPQR/b.pdf` disappears, and the database item of `BCDEFGHJ`
is removed while its directory stays. The expected result is
`restoredAttachments=[…STUVWXYZ…]`, `disappearedAttachments=[…JKLMNPQR…]`,
`newOrphans=[…BCDEFGHJ…]`, and the original duplicate group's `removedMembers` points
to `JKLMNPQR` and `addedMembers` to `STUVWXYZ`. Identities stay the same even if every
itemID is renumbered or the backup root moves. A rewrite of attachment bytes with the
same size and mtime still appears in `attachmentsChanged/filesChanged`.

`inputs.before/after` each store the **SHA-256 of the original report bytes as read**,
the path, the byte count and `coverage` (audit completeness, errors, file/hash counts,
attachment count, scope, library set and reasons for gaps). With unauthorized linked
files, read/change errors, unhashed files or missing comparison evidence the exit code
is **1** with `comparisonComplete=false` and `changes=null`; evidence gaps are never
treated as file deletions or "no change". Corrupt JSON/schema, duplicate identities,
dangling references, a different source or library set, and a missing / symlinked /
changing report exit **2**. Readable invalid JSON still records the input hash; the
hash of an unread input is `null`. With sufficient evidence on both sides and a
matching scope/library set the exit code is **0** with `comparisonComplete=true`.
Markdown contains the change counts plus the complete JSON, so no evidence is lost.
An empty delta only means these input reports show no change; it cannot prove that
snapshot and storage were captured at the same moment, nor defend against a malicious
in-scan rewrite that preserves metadata.

### Report consistency checks

Before mapping comparison identities, `compare` checks that each report is internally
consistent. A contradiction returns `fatal=true, comparisonComplete=false,
changes=null, linkedFileAlignments=[]` with
`{"code":"invalid_report","input":"before|after","detail":...}` and exit **2** for JSON
and Markdown alike, while the exact SHA-256, byte count and existing coverage of both
readable reports are kept (the invalid side gets `coverage.complete=false` with
`invalid_report` among its `reasons`). The checks apply to BEFORE and AFTER, with or
without scan or comparison mappings, and to partial reports. They only check the
report's own statements: they do not read scan sources, query Zotero, contact the
network or authenticate a report.

| `detail` | What is contradictory |
| --- | --- |
| `Duplicate member lacks matching file inventory` | a `duplicateGroups[].files[]` member (matched by its original `path` and `source`) has no matching entry in the main `files` inventory |
| `Duplicate member digest disagrees with file inventory` | a member's declared SHA-256 differs from its inventory entry (e.g. only the member `sha256` was changed) |
| `Duplicate member references disagree with file inventory` | a member's referencing itemID set differs from the inventory (e.g. member `references` set to `[]`); reference order is irrelevant, but repeated or non-integer itemIDs cannot form a consistent set |
| `Duplicate member status disagrees with file inventory` | a member's non-null `status` differs from the inventory entry's usable `status` (e.g. member `present` → `missing`); a non-null member status must be a nonempty string, and a missing / null status on either side is simply no observation |
| `Duplicate group digest disagrees with file evidence`, `Duplicate group size disagrees with file evidence` | a group header's declared `sha256` / `size` disagrees with a member or its inventory entry; declared digests must be 64 lowercase hex characters and sizes nonnegative integers (not booleans) |
| `Repeated duplicate group member identity` | the same original `(path, source)` member appears twice in one declared group; equal bytes at different paths and hardlinks remain distinct members |
| `Attachment trash flag disagrees with all-item inventory`, `Attachment parent trash flag disagrees with all-item inventory` | an attachment's `inTrash` differs from the boolean for the same itemID in `comparisonEvidence.items`, or `parentInTrash` differs from the declared parent's flag (it must be `false` without a parent); missing or non-boolean all-item trash evidence cannot establish a consistent observation |
| `File/reference path mismatch`, `File/reference status mismatch` | an attachment listed in a file's `references` does not match that file's original `path` and final `status` (e.g. AFTER marks `QQQQQQQQ` `missing` while a `present` file still lists it), for storage and linked files alike |
| `Missing attachment target is inventoried present` | a `missing` attachment's `resolvedPath` is still inventoried `present`, even if the itemID was removed from the file's `references` and duplicate projections; applies to absolute / `attachments:` aliases and stored modes 0/1/4 |
| `Remote attachment cannot have a resolved local target` | a remote `linkMode=3, status=remote_url` observation declares a non-null local `resolvedPath`; the scanner never resolves local targets in mode 3, so a missing or `null` `resolvedPath` is valid and the raw `path` may be null, a URL or any retained string |
| `Stored attachment target mismatch` | a stored attachment (`linkMode=0/1/4`) does not follow `storage:<single file name>` → `<report.storage>/<the attachment's own key>/<original file name>` (e.g. `ABCDEFGH`'s `resolvedPath` pointed at `<storage>/JKLMNPQR/b.pdf`); `present`/`missing` need a valid original file name and the matching `resolvedPath` |
| `Local attachment observation requires linkMode 0/1/2/4` | a local `present`/`missing` observation uses another link mode (e.g. `99`, `null`, or the remote-only `3`) |
| `Local linked observation requires a supported raw path` | a `linkMode=2` `present`/`missing` observation's raw `path` is not a normalized POSIX absolute path or a safe `attachments:relative/path` (e.g. `storage:a.pdf`); empty/null, relative, Windows/backslash, NUL, traversal and ambiguous components are rejected |
| `Linked attachment target mismatch with scan policy` | a linked attachment's `resolvedPath` cannot be derived from the report's own scan policy: absolute raw paths apply `policy.linkedRootMappings` once, `attachments:` uses only `policy.linkedBaseDirectory`; early version-1 reports without `linkedRootMappings` are treated as `[]`, but resolved observations with malformed or ambiguous declared mappings are rejected; a comparison mapping cannot repair this |
| `Local linked observation outside declared allowed roots` | a `linkMode=2` `present`/`missing` target is not a strict descendant (by path component) of a directory in the report's `policy.allowedLinkedRoots`; `/papers` does not contain `/papers-other/a.pdf`, a directory equal to the file does not count, the root `/` contains its descendants; no mapping, base, storage location or alias substitutes for a declared allowed root |

These checks keep the original semantics for real evidence: genuinely missing targets
that were never inventoried, valid present aliases, stored companion/orphan files,
same keys across libraries, explicitly authorized linked attachments that point into
storage (linked attachments do not use the stored key/file-name rule), remote
observations, local itemID renumbering, and real partial scans. Missing stable
SHA-256 in the main inventory, a read failure consistent with the file, a real
`blocked_linked`, an unresolvable `invalid_path`/`unsupported_path` without a target,
or `unsupported_link_mode` without a local observation remain coverage gaps (exit
**1**), not corrupt reports. An empty `duplicateGroups` collection can still be
compared; the checks do not require every group or member to be listed.

### Compare after the linked backup root moved

When the two reports scanned linked files from `/backup/day1/papers` and
`/backup/day2/papers`, align those files' identities explicitly:

```sh
notewrench zotero-attachments compare /reports/before.json /reports/after.json \
  --map-linked-root /backup/day1/papers /backup/day2/papers > /reports/relocated-delta.json
notewrench zotero-attachments compare /reports/before.json /reports/after.json \
  --map-linked-root /backup/day1/papers /backup/day2/papers --format markdown
```

Python API: `compare_reports(before_path, after_path,
linked_root_mappings=(('/backup/day1/papers', '/backup/day2/papers'),))`. Here
`--map-linked-root BEFORE_ROOT AFTER_ROOT` is an identity mapping **at comparison
time**; it is not inherited from the reports' scan `policy`, and no snapshot,
attachment or root directory is read. Comparison works even when the original files
are offline; only the two JSON reports are read and they stay unchanged.

Only BEFORE linked file identities are mapped, **once**, on component boundaries and
keeping the suffix; AFTER and storage file identities are unchanged. There is no
recursive mapping and no rename inference; unmatched linked files keep their absolute
path identity. For example `day1/papers/nested/a.pdf` aligns with
`day2/papers/nested/a.pdf`, but `day1/papers-other/a.pdf` does not match. Several
`attachments:`/absolute references to the same file still correspond to one file
member. With the same size/SHA-256 a root move alone never produces file or duplicate
member additions/removals; a rewrite with the same size and mtime still produces
`filesChanged` and the real duplicate group changes. Attachments themselves are still
aligned by `(libraryID, key)`, and a changed original absolute reference remains an
`attachmentsChanged` entry; the mapping does not hide path edits in the database.

The output's `policy.linkedRootMappings` records every validated
`source`/`destination`. A file view's `identity` uses the AFTER namespace, and `path`
is always the original path in that report; mapped BEFORE views additionally store
`originalIdentity`. `linkedFileAlignments` lists every successfully aligned relocated
file (including files with unchanged bytes) with its canonical identity, complete
`before`/`after` file views, original paths, referencing identities and the SHA-256
from each independent scan. The original input reports' SHA-256 and coverage are kept;
attachment content evidence is not rewritten or recomputed. Mapped files that were
not aligned are shown under the normal add/remove rules.

Mappings are repeatable and roots follow the normalized POSIX rules of scan mappings
above (strings are validated before normalization; a caller-built `Path` may have lost
the original spelling). Source roots must be mutually distinct and non-nested, as must
destination roots; overlap between sources and destinations is allowed, but each
replacement happens once. Invalid or ambiguous configurations are reported as
`invalid_linked_mapping` (exit **2**) before any report is read, even if unused; so is
a BEFORE file equal to a source root. If two BEFORE files map to the same identity
(including an unmapped file already under the destination root) the result is
`ambiguous_linked_identity` (exit **2**) even with equal SHA-256; the two observations
are never merged. These failures keep `changes=null` and an empty
`linkedFileAlignments`; hashes of reports already read are kept. The scope/library
checks and the complete-SHA-256 evidence requirement still apply: incomplete evidence
exits **1**, corrupt reports or mismatched sources exit **2**, complete and unambiguous
evidence exits **0**. A mapping is only the caller's identity statement; it cannot
prove that backups are correctly paired.

## Snapshot contract and supported scope

Quit Zotero completely, then copy `zotero.sqlite` and the matching storage; or use an
external SQLite backup tool to produce a consistent standalone snapshot plus a
matching storage copy. **This tool does not make snapshots, never opens the live
database for a write lock, and never checkpoints.** Snapshots with `-wal`, `-shm` or
`-journal` sidecars (including empty files or dangling links) are refused. An old main
file whose WAL was lost may still pass `quick_check`; the tool cannot prove that it
was captured together with storage or that it contains the latest transactions. Never
copy only the main database of a running Zotero.

The open mode is `mode=ro&immutable=1`, through a read-only pinned file descriptor that
does not follow symlinks (`/proc/self/fd` on Linux). The tool checks SQLite
`quick_check`, the required real tables (not views) and columns, item identity and
attachment/`deletedItems` relationships, and the snapshot file metadata before and
after reading. Both reduced and full schemas with the required columns are supported;
extra columns are allowed, missing columns are refused. Every `items.key` protects its
directory, including non-attachment items, all libraries and trashed items.

Based on the [Zotero schema](https://github.com/zotero/zotero/blob/main/resource/schema/userdata.sql),
the [linkMode definitions](https://github.com/zotero/zotero/blob/main/chrome/content/zotero/xpcom/attachments.js)
and the [attachmentPath definition](https://github.com/zotero/zotero/blob/main/chrome/content/zotero/xpcom/data/item.js),
the following is supported:

- linkMode 0/1/4: `storage:<single file name>` → `storage/ITEMKEY/<file name>`;
  traversal and separators are rejected.
- linkMode 2: POSIX absolute linked file paths below explicitly authorized directories.
- linkMode 2: absolute paths relocated with `--map-linked-root SOURCE DESTINATION`;
  only the backup destination's authorization is checked, with no fallback to the
  original location.
- linkMode 2: `attachments:relative/path`, with an explicit base mapping and root
  authorization (see above).
- linkMode 3: marked `remote_url`, never treated as a local missing file, never fetched.
- Storage directories whose names are eight-character uppercase letter/digit keys are
  inventoried recursively, including HTML companion resources and files inside orphan
  candidates. Root entries that do not match the key rule are reported as errors, not
  silently marked as orphans.

The Zotero base-directory preference is never read; ancient unprefixed relative paths,
Windows drive/UNC paths and unknown modes/paths are explicit errors. The current Zotero
stored-path format allows a single file name, so multi-level `storage:` references are
not guessed.

### Format notes

These are format observations from public Zotero `main` definitions, not copied
implementation code or a compatibility promise for every Zotero version.

- [userdata.sql](https://github.com/zotero/zotero/blob/main/resource/schema/userdata.sql)
  defines `items(itemID, libraryID, key)`, attachment identity/parent/linkMode/path,
  and `deletedItems(itemID)`. Item keys are unique per library. The audit validates
  these columns and relationships, requires actual tables rather than views and
  accepts additional columns. All item keys take part in retention, across libraries
  and including deleted items. Duplicate keys across libraries conservatively retain
  the directory and list all identities.
- [attachments.js](https://github.com/zotero/zotero/blob/main/chrome/content/zotero/xpcom/attachments.js)
  link modes: 0 imported file, 1 imported URL, 2 linked file, 3 linked URL, 4 embedded
  image. Modes 0/1/4 are treated as stored local files; mode 3 is reported as remote
  URL metadata and never fetched.
- [item.js](https://github.com/zotero/zotero/blob/main/chrome/content/zotero/xpcom/data/item.js)
  `attachmentPath` uses `storage:filename`, an absolute linked path, or
  `attachments:relative/path`. The current stored-path setter disallows directory paths
  in the file name, so nested stored reference paths and ancient unprefixed relative
  paths are rejected; nested companion files on disk are still inventoried and hashed.
  Absolute POSIX linked paths require explicit directory authorization; Windows
  drive/UNC paths are never guessed.
- Relative linked support follows Zotero commit
  [`c634bba5b65c0f9984af8fc3b1926b633139fc3f`](https://github.com/zotero/zotero/tree/c634bba5b65c0f9984af8fc3b1926b633139fc3f):
  [`attachments.js` lines 2863–2916](https://github.com/zotero/zotero/blob/c634bba5b65c0f9984af8fc3b1926b633139fc3f/chrome/content/zotero/xpcom/attachments.js#L2863-L2916)
  defines the `attachments:` prefix and stores base-relative paths with forward slashes
  for cross-platform use. Upstream resolves them against the `baseAttachmentPath`
  preference; this offline audit instead requires the caller's
  `--linked-base-directory` and never reads Zotero preferences. Resolved files use the
  same component-by-component, descriptor-relative `O_NOFOLLOW` reads, hashing and
  final metadata checks as absolute linked files. Only linkMode 2 uses this mapping.
- Relocation of absolute linked paths (`--map-linked-root`) adds no new Zotero format
  assumption. Different resolved linked paths remain different file identities; scan
  relocation is not report-comparison identity remapping.

## Reading the report

JSON and Markdown both keep the complete evidence; Markdown embeds the full JSON with
no field dropped. `attachments` contains itemID/libraryID/key, parent, original path,
resolved path, trash flags and status; `files` records each file's source, referencing
itemIDs, device/inode/size/mtime/ctime, hash status and SHA-256 (when computed).
`duplicateGroups` groups by size + SHA-256.

`missing` means not found; `inaccessible` is a permission failure; `symlink`,
`not_regular`, `blocked_linked`, `unsupported_path` and so on are reported separately.
A change detected during inventory, hashing or the final check is recorded as
`unstable` or `unstable_directory`; changed files do not enter duplicate groups. These
are metadata checks: they cannot defend against a malicious rewrite that preserves
metadata, nor guarantee that nothing changes after the final check.

`orphanCandidates` only means that a directory key is not among **all items**; it lists
the inventoried file count and bytes. It is not a deletion list. `retainedDirectories`
shows why each directory is retained; the same key across libraries lists every
identity. When the audit has errors, inventoried bytes may be a lower bound, not the
complete directory content.

`statistics` contains the actual `bytesHashed`, the number of files hashed and
attempted, unique-size skips, inventoried bytes and duplicate bytes. `bytesHashed`
includes bytes already read in failed hash attempts and excludes SQLite metadata
reads. Equal bytes under different file names are grouped; different bytes of the
same size are not. `extraPathBytes` counts the logical bytes of duplicate paths;
`extraDistinctInodeBytes` excludes known hardlink aliases. Neither is an estimate of
reclaimable disk space (sparse files, reflinks, compression and external hardlinks
are not modeled). A path referenced by several items is inventoried once. Unreferenced
companion files are included in duplicate detection.

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | audit complete (or comparison complete); duplicates, missing files and orphan candidates may still need human review |
| 1 | partial audit or comparison: `auditErrors` is nonempty (including unauthorized linked files) or comparison evidence is incomplete; the evidence gathered so far is printed |
| 2 | fatal snapshot/schema error, invalid or mismatched reports, invalid mappings, an unsupported platform, or a CLI usage error; snapshot errors print a structured JSON/Markdown report |

`auditComplete` describes whether the audit process was complete, not whether the
library is free of problems. Reading may still update access times (atime); the tool
opens no writable descriptor on any input. It runs no scripts and has no automatic
repair, deletion, WebDAV, decryption or hash cache, and it never expands network
addresses. If an authorized directory is itself a network mount, file reads behave as
that mount does. File and report metadata are held in memory; there is no streaming
report for very large libraries.

## Hashing strategy and benchmark

By default files are first bucketed by size, and SHA-256 is computed only for buckets
with at least two files; unique-size files are not read, but their accessibility and
stability are still checked. Every directory and file still has to be listed, stat'ed
and opened, so no I/O-free claim is made. `--hash-all` is the explicit baseline: the
same SHA-256 reader over every file of the same inventory. Scans are sequential, with
no resident server, background process or parallel scan.

[`benchmarks/zotero-attachments/benchmark.py`](../benchmarks/zotero-attachments/benchmark.py)
reproduces a seeded experiment (seed 100: 256 unique-size files, 12 duplicate pairs,
8 same-size/different-content controls plus the basic synthetic sample, 295 files and
1,154,637 bytes in total). In one local run the full-hash baseline read 295 files /
1,154,637 bytes, while size prefiltering read 37 files / 73,375 bytes and skipped 258
files, with **identical complete evidence for all 13 duplicate groups**. Counts,
environment and wall time are in
[`benchmarks/zotero-attachments/results.json`](../benchmarks/zotero-attachments/results.json).
The timing describes that single sequential warm-cache run and is no speed guarantee;
when all sizes are equal, prefiltering does not reduce content reads.

## Related tools

[zotero-storage-cleaner](https://github.com/GrainRainAndy/zotero-storage-cleaner)
already handles storage orphans by `items.key`, and
[zotero-storage-scanner](https://github.com/retorquere/zotero-storage-scanner) is
another related tool. There is overlap; this command focuses on standalone read-only
snapshots, size-prefiltered SHA-256, distinguishing broken links from inaccessible
files, complete evidence and real counts, without claiming to be first or faster. The
historical orphan request [zotero/zotero#1107](https://github.com/zotero/zotero/issues/1107)
is only a motivation and does not imply a current Zotero defect. No code or article
text from these projects is copied.

Compatibility note: reports keep `"tool": "zotero-attach-audit"` and `reportVersion`/`comparisonVersion` 1, so reports written by
the standalone tool and by `notewrench zotero-attachments` can be compared with each other.
