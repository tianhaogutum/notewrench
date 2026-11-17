# notewrench doctor

You have a Zotero library, a Joplin export, a Calibre library, a Karakeep or Wallabag
dump, browser bookmark exports and a folder of Markdown notes with Pandoc citations. Which of them has
problems, and what do you run about it? `notewrench doctor` answers that in one
read-only pass. It works out what each path you give it is, runs the read-only analysis
of the notewrench command that owns that kind of data, prints one verdict line per
source, and then lists the top fixes with the exact `notewrench ...` command to run
next.

It **never writes anything**: no report files, no database or export changes, no network
access. The rules are not copied into doctor. It calls the library functions of the other
commands, so a doctor verdict and the full report of the command it points to always
agree.

**Platforms:** Linux and macOS, Python 3.11+, standard library only. Windows is not
supported. The Zotero attachment audit needs Linux (`/proc/self/fd`). Elsewhere the
Zotero line reads `SKIPPED` with the reason, and the other checks still run. The
bookmark HTML check uses the parsers of the optional `browser-bookmarks` extra
(`pip install 'notewrench[browser-bookmarks]'`); without it the Bookmarks line reads
`SKIPPED` with that install hint.

## Quick start

From a clone of the repository, check the bundled synthetic sample (all fictional
data, under 90 KB):

```sh
pip install notewrench        # or: uvx notewrench doctor examples/doctor
notewrench doctor examples/doctor
```

Output on Linux (exit status 1, because the sample has a missing attachment and an
unknown citekey):

```text
notewrench doctor  5 checks · read-only, nothing is written

  ✗ Zotero      7 attachments: 1 missing file, 1 duplicate set (11 B extra), 1 orphan folder (22 B)
                examples/doctor/zotero
  ! Joplin      3 notes: 1 exact-duplicate group (1 extra copy)
                examples/doctor/joplin-export.jex
  ! Calibre     7 books: 1 strong duplicate cluster, 1 possible duplicate, 1 invalid ISBN, 1 book without authors
                examples/doctor/calibre
  ! Read-later  4 bookmarks in Karakeep + Wallabag: 1 duplicate cluster (2 bookmarks, 1 across exports)
                examples/doctor/read-later/karakeep.json, examples/doctor/read-later/wallabag.json
  ✗ Citations   2 Markdown files: 1 unknown-key citation, 7 renamed-key citations, 2 unsupported citations
                examples/doctor/thesis/references-old.json, examples/doctor/thesis/references.json + 2 Markdown files

Top fixes
  1. Restore 1 missing Zotero attachment and review 2 cleanup candidates
     Missing: STUVWXYZ/missing.pdf. Restore the file or re-link it in Zotero. Duplicate sets and orphan folders are candidates only; keep trashed items.
     notewrench zotero-attachments --snapshot examples/doctor/zotero/zotero.sqlite --storage examples/doctor/zotero/storage --format markdown > zotero-audit.md
  2. Fix 1 citation whose key is in no bibliography
     examples/doctor/thesis/reading-notes.md:3 @notInExport. Add the item to your library and export again, or correct the key.
  3. Rekey 7 citations in 2 files from references-old.json to references.json
     ...
```

On your own data, point it at the folders and files you have. Close Zotero and Calibre
first, or better, point it at copies. Both commands refuse a database that has
`-wal`/`-journal` sidecar files next to it.

```sh
notewrench doctor ~/Zotero "$HOME/Calibre Library" ~/exports/joplin.jex
notewrench doctor ~/exports                       # a folder that contains several exports
notewrench doctor thesis/ --fail-on warning       # as a pre-submission gate
notewrench doctor ~/exports --format json > health.json
notewrench doctor examples/browser-bookmarks      # browser + linkding bookmark HTML (extra needed)
```

## What it detects

| You pass | Detected as | Check that runs |
| --- | --- | --- |
| a folder with `zotero.sqlite` (and `storage/`), or a `*.sqlite` file (its sibling `storage/` is used) | Zotero | `zotero-attachments` audit: missing files, duplicate bytes, orphan storage folders, unreadable paths (Linux only) |
| a `.jex` file (or any tar file passed explicitly), or a folder of Joplin `<32 hex>.md` items | Joplin | `joplin-dupes` analysis: exact-duplicate groups, ID conflicts across exports, notes that cannot be compared, near candidates |
| a folder with `metadata.db`, or `metadata.db` itself | Calibre | `calibre-dupes` within-library plan (metadata only, files not read): strong and possible duplicate clusters, invalid ISBNs, missing authors/titles |
| a JSON object with a `bookmarks` array (Karakeep) or a Wallabag v2 array | Read-later | `links` report over **all** such exports together: duplicate clusters, including across exports; invalid records |
| an `.html`/`.htm` file that starts with `<!DOCTYPE NETSCAPE-Bookmark-file-1>` (Firefox, Chrome, Safari, linkding exports; linkding is recognized by its `PRIVATE`/`TOREAD` attributes or notes markers) | Bookmarks | `browser-bookmarks` parsing and grouping over **all** such exports together, without writing a review bundle: duplicate groups, including across exports, and how many of them have differing notes, descriptions or tags (needs the `browser-bookmarks` extra) |
| a JSON array of CSL items (`id` and `type`), plus `.md`/`.markdown` files | Citations | `citekeys` dry run: citekeys found in no bibliography, keys another export renamed for the same item, unsupported citation syntax |

Any other folder is scanned, up to 6 levels deep and 50,000 entries, for the sources
above. Hidden entries and symlinks are skipped, and other files are ignored. A path
that does not exist, or a file or folder in which nothing is recognized, is a usage
error (exit 2) and nothing runs. Several Joplin exports are analyzed together, like
`notewrench joplin-dupes a.jex b.jex`, so ID conflicts across them are found. The same
goes for read-later exports and `notewrench links`, and for bookmark HTML exports and
`notewrench browser-bookmarks scan`.

### Citations: unknown and renamed keys

All CSL-JSON files found form the bibliography. A citation whose key is in none of them
is an **unknown-key citation** (error). When there are two or more bibliographies,
doctor picks each document's *home* bibliography, the one that resolves the most of
its keys. It then asks `citekeys`' own DOI/URL matching whether another export holds
the same items under different keys. Those citations are **renamed-key citations**
(warning), and the fix is the matching `notewrench citekeys --old HOME --new OTHER ...
--dry-run`. Doctor cannot know which export is current, so the fix says "if OTHER is
your current export". If no single home exists, no renamed-key check is made.
Identical copies of one export count as one.

## Severities and exit status

| Severity | Findings |
| --- | --- |
| error | missing Zotero attachment files, unreadable Zotero paths, unknown-key citations |
| warning | Zotero duplicate sets and orphan folders, Joplin exact duplicates / ID conflicts / notes that cannot be compared, strong Calibre duplicates, duplicate bookmark clusters, invalid read-later records, duplicate groups in bookmark HTML exports, renamed-key citations |
| note | Zotero linked files that were not read, Joplin near candidates / unknown items / export warnings, possible Calibre duplicates and metadata gaps, bookmark duplicate groups with differing notes, descriptions or tags, unsupported citation syntax, unreadable Markdown files |

Counts are occurrences: 7 renamed-key citations are 7 citation spans, not 7 keys.

| Exit | Meaning |
| --- | --- |
| `0` | nothing at or above `--fail-on` (default `error`) |
| `1` | at least one finding at or above `--fail-on` |
| `2` | usage error, unrecognized or missing path, or a recognized source that could not be read (for example a Calibre library with a `-wal` file). The report for the other sources is still printed |

`--fail-on none` always exits 0 or 2. A `SKIPPED` check never affects the exit status.

## Options

| Option | Meaning |
| --- | --- |
| `PATH ...` | exports, libraries, or folders that contain them; at least one |
| `--format text\|json` | text (default; colored on a terminal) or a `notewrench.doctor/v1` JSON document |
| `--fail-on error\|warning\|note\|none` | lowest severity that makes the exit status 1 (default `error`) |
| `--fixes N` | how many top fixes the text output lists (default 5; JSON lists all) |
| `--no-color` | no ANSI colors (also `NO_COLOR`, or when stdout is not a terminal) |

## JSON output

```text
{"schema": "notewrench.doctor/v1", "notewrench_version": ..., "read_only": true,
 "summary": {"sources", "with_findings", "skipped", "unreadable", "errors", "warnings", "notes",
             "fail_on", "failed", "exit_status", "ignored_entries", "scan_truncated"},
 "sources": [{"check": "zotero|joplin|calibre|links|bookmarks|citations", "label", "paths", "where",
              "status": "ok|findings|skipped|error", "reason", "verdict", "counts": {...},
              "findings": [{"severity", "code", "count", "message"}], "fixes": [...]}],
 "top_fixes": [{"check", "severity", "title", "detail", "commands": ["notewrench ..."]}]}
```

`counts` holds the raw numbers behind each verdict (for example `missing`,
`duplicate_extra_bytes` and `orphan_bytes` for Zotero, `stale_citations` for
citations). `top_fixes` is ordered by severity, then by how many findings a fix covers.

## Limits

- Doctor is a summary. Duplicates and orphan folders are *candidates*. Read the full
  report of the command each fix names before you delete or merge anything.
- The Calibre check reads metadata only (`calibre-dupes` without `--verify-files`) and
  looks within each library. For duplicates across two libraries, run `notewrench
  calibre-dupes A B`.
- The Zotero check uses the audit defaults: no linked-file roots are allowed, so linked
  files outside `storage/` are reported as not checked. Pass `--allow-linked-root` to
  `notewrench zotero-attachments` for those.
- Size limits are those of the underlying commands (for example 32 MiB per Markdown
  file for `citekeys`, 8 MiB per bookmark HTML file for `browser-bookmarks`, and the
  `joplin-dupes` archive limits). JSON files over 64 MiB are
  not inspected during detection.

The Python API is `from notewrench.doctor import detect, run_checks`.
