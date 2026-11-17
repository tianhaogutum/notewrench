# notewrench bookmark-migration

After you move bookmarks from a browser to Karakeep, from one Karakeep server to
another, or back out to a browser, you rarely know whether everything arrived: an
importer may drop a duplicate, flatten a folder, lose a note or rewrite a URL.
`notewrench bookmark-migration` compares the export from **before** the migration
with the export from **after** it, offline and read-only, and reports which URLs
moved, which entries are missing or new, whether duplicate counts changed, and where
titles, tags, folders, notes, export times or archive state conflict. It writes a
Markdown, a JSON and a self-contained HTML report that point back to the original
line or JSON position of every entry.

**Which command?** Use `bookmark-migration` **after** a migration, to check the result
against the export you started from. Before an import, use
[`notewrench browser-bookmarks`](browser-bookmarks.md) to review duplicate browser and
linkding bookmarks; inside read-later apps, use [`notewrench links`](links.md) to find
duplicates across Karakeep and Wallabag exports.

It supports **Netscape bookmarks HTML** (Firefox, Chrome, Safari, Karakeep's HTML
export and other exporters) and **Karakeep JSON**, in any combination. A field that a
format cannot express is marked *not comparable* instead of being counted as lost. It
never opens a bookmark URL, logs in to an account or changes an export.

Formerly the standalone `bookmark-migration-audit` tool (`bookmark-audit` command). The
report format is unchanged: `report.json` keeps `"schema_version": 1` and its field
names, and the attachment manifest keeps `"schema_version": 1`. Report text is now in
English.

**Reads / writes:** reads only the two exports and the optional attachment manifests
you name (and checks whether the files those manifests list exist). Writes
`report.md`, `report.json` and `report.html` into `--output-dir` (default
`audit-report`, created if needed). Existing reports are refused unless you pass
`--force`; even then a report never overwrites an input, a manifest or a listed
attachment, and a report path that is a symlink is refused.

**Platforms:** Linux and macOS, Python 3.11+, standard library only (no extra needed);
Windows is not supported.

## Quick start

```sh
pip install notewrench
notewrench bookmark-migration --help
```

From a clone of the repository, compare the bundled synthetic browser export with a
Karakeep export of the same bookmarks after a migration (fictional data on reserved
example domains):

```sh
notewrench bookmark-migration \
  examples/bookmark-migration/cross-format/source.html \
  examples/bookmark-migration/cross-format/target.json \
  --output-dir migration-check
```

The sample contains deliberate differences, so the command exits **1** after writing
`migration-check/report.md`, `report.json` and `report.html`. It matches 4 of the 7
source URLs, reports 3 missing and 2 added entries and 2 field conflicts (an export
time and a folder). Open `report.html` in a browser: it needs no server, script or
external resource.

On your own data, put the export from before the migration first:

```sh
notewrench bookmark-migration old-bookmarks.html karakeep-export.json --output-dir migration-check
notewrench bookmark-migration before.json after.html \
  --source-format karakeep --target-format netscape \
  --formats md json --output-dir migration-check-2
```

Export Netscape HTML from your browser's bookmark manager and **JSON** from Karakeep's
import/export settings page. Database backups, REST API responses, ZIP backups and plain
URL lists are not accepted. Formats are detected from the content (a file starting with
`{` is Karakeep JSON, anything else must be Netscape HTML); `--source-format` and
`--target-format` set them explicitly.

Reports contain the raw entries, folder paths and notes of both exports: sharing a
report shares those exports.

## Options

| Option | Effect |
| --- | --- |
| `source`, `target` | The export from before and the export from after the migration |
| `--source-format`, `--target-format` | `auto` (default), `netscape` or `karakeep` |
| `--source-attachments`, `--target-attachments` | Optional attachment manifest per side (see [Attachments](#optional-attachment-check)) |
| `--url-mode` | `conservative` (default) or `raw` (see [URL policy](#url-policy)) |
| `--output-dir` | Report directory, default `audit-report` |
| `--formats` | Any of `md`, `json`, `html` (default: all three) |
| `--force` | Overwrite existing reports in `--output-dir` |
| `--version` | Print `notewrench bookmark-migration <version>` |

## Reading the report

| Section | Meaning |
| --- | --- |
| Matched | Entries paired one to one under the selected URL policy; each lists every field result |
| Missing | Source entries without a target entry, including fewer copies of a duplicate URL |
| Added | Target entries without a source entry |
| Duplicate URL groups | Match key, count, raw URLs and positions on each side; nothing is silently deduplicated |
| Field conflicts | Both sides can be compared, but title, tags, folder path, notes, saved time, archive state or attachment references differ |
| Not comparable | The format cannot express the field, or there is not enough evidence; never counted as a conflict |
| Not audited | Text bookmarks, `content: null` entries, historical Livemarks and similar, with their position and reason |
| Local attachments missing | A manifest lists a path that is not an existing regular file; counted apart from reference conflicts |

In `report.json`, `summary` holds the counts and `coverage` the per-field results;
`matched` has the pairs with their field results, `missing` and `added` the full
entries, and `source` / `target` every parsed entry with its `raw` data. `A[1]` is the
first `A` element of an HTML file; `$.bookmarks[0]` is a zero-based JSON position. For
HTML, `raw.start_tag` keeps the undecoded start tag, so `&amp;` and similar can be
checked against the original.

**Duplicate pairing is ambiguous.** Within one URL group the command first pairs
entries whose raw URL and all comparable fields are equal, then entries with the same
raw URL, then the rest in file order. Each entry is used at most once. Duplicate groups
are always flagged for review, and their conflicts come from a suggested pairing: the
command does not claim to know which copy became which. Tags compare as an unordered
set, folders as the full name path; titles and notes compare exactly, including Unicode
and case.

## URL policy

`--url-mode conservative` (the default) only touches HTTP(S) URLs: it lower-cases the
scheme and host, drops the scheme's default port and adds `/` to an empty path. IPv6
zone IDs keep their case. Other schemes compare as the original string.

- `HTTPS://Example.org:443?q=书签&page=2` matches `https://example.org/?q=书签&page=2`;
  the report still shows that the raw URLs differ.
- Query parameters are all kept: **tracking parameters such as `utm_*` are not
  removed**, parameters are not reordered, percent-encoding is not decoded, fragments
  are kept, HTTP is not upgraded and trailing slashes are not merged.
- `?q=书签` and `?q=收藏`, or `?a=1&b=2` and `?b=2&a=1`, are different URLs.
- HTML entities are decoded first, so `&amp;` in the HTML means `&` in the URL.

`--url-mode raw` compares the decoded URL strings exactly. It still checks that each
URL is a valid absolute URL and never opens it. The conservative policy may leave
URLs that mean the same but are encoded differently as a missing/added pair for you to
check; that is deliberate.

## Fields and time

- **Tags (HTML):** if no entry in the file has a `TAGS` attribute, tags are not
  comparable for the whole file; once any entry has `TAGS`, an entry without it has no
  tags.
- **Notes (HTML):** only entries followed by a `DD` have a comparable note; an entry
  without one is unknown, not empty. Historical folder `DD` descriptions are kept as
  metadata and not audited.
- **Time:** an HTML entry without `ADD_DATE` has an unknown time, not zero. Karakeep's
  `createdAt` (Unix seconds) is the **last saved** time, not `firstCreatedAt`; a time
  conflict means the exported values differ, not that the original save date was lost.
- **Folders (Karakeep):** list IDs are only used inside one file to rebuild the
  hierarchy; across systems the name path is compared. An older export without list
  fields has unknown folders; one without `archived` has an unknown archive state.
- **Out of scope:** empty lists, list descriptions, icons and queries, the dynamic
  members of smart lists, and lists with the same name and path (their identity cannot
  be told apart). Folders are never mapped to tags and import root folders are never
  removed.
- **Attachments:** native exports carry no attachment list; Karakeep asset bookmarks may
  be missing from a plain JSON export altogether.

The format evidence these rules follow (Karakeep exporter and parser at a pinned
commit, Mozilla's bookmarks HTML reader and writer) is in
[bookmark-migration/formats.md](bookmark-migration/formats.md).

## Optional attachment check

Add a **complete reference manifest** to either side. This is this command's own
format, not a Karakeep or Netscape field:

```json
{
  "schema_version": 1,
  "entries": {
    "$.bookmarks[0]": [
      {"reference": "manual", "path": "files/manual.pdf"},
      {"reference": "remote-only-reference"}
    ]
  }
}
```

Keys of `entries` are report locations (`$.bookmarks[n]` or `A[n]`). `reference` is a
stable name you choose for an attachment on both sides and must be unique within an
entry. The optional `path` is resolved relative to **the manifest's directory**; it
must not be absolute or escape that directory, symlinks included. Nothing is
downloaded. The reference sets of the two sides are compared; local paths may differ.

**A manifest declares every attachment of every URL entry in that export.** An entry it
does not list has no attachments; without a manifest, attachments are not comparable. A
reference without a `path` is `not_checked`. `present` means only that the local file
exists; content, size, hashes and online availability are not checked.

```sh
notewrench bookmark-migration \
  examples/bookmark-migration/karakeep/source.json \
  examples/bookmark-migration/karakeep/target.json \
  --source-attachments examples/bookmark-migration/karakeep/source-attachments.json \
  --target-attachments examples/bookmark-migration/karakeep/target-attachments.json \
  --output-dir attachment-check
```

This sample keeps the `diagram` reference but its target file is missing, so the
command exits 1 and reports 6 matched, 2 missing and 1 added entries, 6 field conflicts
and 1 missing local attachment.

## Exit status

| Exit | Meaning |
| --- | --- |
| `0` | No differences and nothing left unchecked within the declared URL, field and local-reference scope; still not a pass for a whole account |
| `1` | Reports written; missing, added or conflicting entries, a duplicate group on either side, or a missing local attachment |
| `2` | Input, output or usage error; see stderr (`notewrench bookmark-migration: error: ...`). No new report is written, so do not read an older report in the same directory as this run's result |
| `3` | Reports written; no differences in the comparable scope, but some fields are unknown, some entries were not audited or the input raised a scope note |
| `130` | Interrupted |

Differences take precedence over an incomplete scope. Native exports carry no
attachment list, so two identical exports usually exit `3` unless both sides have a
manifest. Any duplicate group needs review, so it never exits `0`.

Input is strict: truncated JSON, duplicate keys, non-finite numbers, unknown `content`
types, dangling or cyclic list references, incomplete HTML, invalid URLs or times and
escaping manifest paths are errors, not silently repaired. Inputs must be UTF-8
(a BOM is allowed), at most 50 MiB per export or manifest. Large collections were not
benchmarked; the HTML report embeds the full JSON and can be larger than the inputs.

## Bundled examples

All files under [`examples/bookmark-migration/`](../examples/bookmark-migration/) are
fictional data written for this command on the reserved `example.org` / `example.net`
domains, shaped like the verified real formats.

- **`cross-format/`** (browser HTML → Karakeep JSON). The browser side has no `TAGS` or
  `DD`, so tags and notes are not comparable. `A[1]` (`HTTPS://Example.org:443/...`)
  matches `$.bookmarks[0]` although the raw URLs differ, and its export time differs by
  an hour. `A[2]`/`A[3]` are two copies of one URL and only one arrived. `A[4]` moved
  from `资料 / 研究` to `资料 / 归档`. `A[5]` was not migrated. `A[6]`
  (`search?q=书签`) and the target's `search?q=收藏` are a missing/added pair. Expected
  counts: conservative 4 matched, 3 missing, 2 added; raw 3 matched, 4 missing, 3 added
  ([`expected.json`](../examples/bookmark-migration/cross-format/expected.json)).
- **`karakeep/`** (Karakeep JSON → Karakeep JSON). The source has 8 URL bookmarks and one
  text bookmark, the target 7 and one. Different list IDs with the same names and
  hierarchy are not a folder conflict; `changed?edition=2` conflicts in all six fields;
  a URL saved three times arrived twice; the text bookmarks are listed as not audited.
  The two attachment manifests map `$.bookmarks[6]` to `$.bookmarks[4]`; `manual`
  exists on both sides and `diagram` is missing locally on the target
  ([`expected.json`](../examples/bookmark-migration/karakeep/expected.json)).
- **`bad-input/`**: a truncated JSON file, valid JSON that is not a Karakeep export and
  an ordinary web page. Each exits 2 with a clear error and writes no report.

## Library use

```python
from pathlib import Path
from notewrench.bookmarkmigration.parsers import load_export
from notewrench.bookmarkmigration.compare import compare_exports

report = compare_exports(load_export(Path("before.html")), load_export(Path("after.json")))
print(report["verdict"], report["summary"]["missing"])
```

`python -m notewrench bookmark-migration` and `python -m notewrench.bookmarkmigration`
are equivalent.
