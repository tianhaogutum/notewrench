# `notewrench archive`

Read-later services keep the only readable copy of many saved articles, and an
export file full of cached HTML is not something you can open in a browser. Use
`notewrench archive` when you want an **offline, auditable copy** of your Wallabag or
Karakeep library: it rescues cached article bodies into a static HTML archive,
converts Karakeep native exports into Wallabag v2 JSON, and turns already-saved API
response pages into both. Every bundle records what was lost and keeps the complete
original input, and `verify` re-checks a published bundle later without the inputs.

Formerly the standalone `bagkeep` tool. Python 3.11+, standard library only.

**Reads / writes:** it reads only the export, API manifest and local asset manifests you
name, and never modifies them. Each command writes one **new** bundle directory
(`-o`), published all at once; existing paths are never overwritten. `--dry-run` and
`verify` write nothing. Nothing is fetched: no network, account, API key or running
Wallabag/Karakeep instance.

**Platforms:** writing a bundle (`rescue`, `k2w`, `api-import` without `--dry-run`)
and `verify` require **Linux**: publication uses `renameat2(RENAME_NOREPLACE)` through
the standard library's `ctypes`, and verification anchors reads with Linux
directory-descriptor operations. On other platforms publication fails with exit 2 and
a clear message (no output is written), and `verify` reports `unsupported_platform`.
`--dry-run` validation and planning work on Linux and macOS; Windows is not supported.
Explicitly selected inputs and the output parent may sit below symlinked directories
(so macOS `/tmp` is fine), but files declared inside manifests are read without
following symlinks (see [Audit bundle and safety](#audit-bundle-and-safety)).

## Quick start

```sh
pip install notewrench
```

The examples below use the synthetic inputs under
[`examples/archive/`](../examples/archive/), so run them from a clone of the repository:

```sh
notewrench archive --help
notewrench archive rescue examples/archive/wallabag-v2.json -o archive
notewrench archive k2w examples/archive/karakeep-native.json -o converted
notewrench archive api-import examples/archive/api/karakeep-manifest.json -o saved-api
notewrench archive verify archive
```

Open `archive/index.html` directly in a browser. The `k2w` output directory
contains `converted/wallabag.json`, a real Wallabag v2 array with one link entry; the
two non-link records are explicitly reported and retained in provenance. Output
parents must exist; output directories must **not** exist, even if empty. Use a new
directory when repeating these commands.

An `api-import` bundle can be turned into an EPUB with
[`notewrench epub build --archive-bundle`](epub.md#build-directly-from-an-archive-bundle).

## Commands

| Command | Input | Output |
| --- | --- | --- |
| `rescue INPUT -o DIR` | Wallabag v2 export JSON array | Static HTML archive: `index.html`, one `article-*.html` per entry |
| `k2w INPUT -o DIR` | Karakeep native export JSON | `wallabag.json` (Wallabag v2 array) |
| `api-import MANIFEST -o DIR` | Manifest of saved Wallabag/Karakeep API response pages | HTML archive **and** `wallabag.json` |
| `verify DIR` | An existing bundle | JSON size/SHA-256 consistency report; never writes |

Options shared by `rescue`, `k2w` and `api-import`:

| Option | Meaning |
| --- | --- |
| `-o, --output DIR` | New bundle directory (required); its parent must exist; never overwritten |
| `--dry-run` | Validate and print the JSON plan without writing any files |
| `--strict` | Exit 3 without writing if the manifest contains loss issues |
| `--assets MANIFEST` | (`rescue`, `api-import`) explicit local PNG/JPEG manifest; no fetching |
| `--content-assets MANIFEST` | (`api-import`) scoped local Karakeep saved-HTML manifest; no fetching |
| `--require-page-hashes` | (`api-import`) require a SHA-256 pin on every saved API page |

## Verify a published bundle

```sh
notewrench archive verify archive
notewrench archive verify /path/to/published-bundle
```

`verify` needs only an existing flat bundle directory from `rescue`, `k2w` or
`api-import`. It never rebuilds, repairs or rewrites the bundle and uses no network.
Exit **0** means all declared payload sizes and SHA-256 hashes match, with no
unexpected entries; exit **2** means an invalid, unreadable or over-limit bundle. Both
produce deterministic JSON on stdout. CLI usage errors use argparse stderr and exit 2;
interruption uses stderr and exit 130. Ordinary filesystem reads can update access
times.

The JSON has `schema_version: 1`, `status: "valid"` or `"invalid"`, global `issues`
and a sorted `files` array. Each file row has `file`, `status`, `issues`,
`expected_bytes` and `expected_sha256` when declared, and `actual_bytes` and
`actual_sha256` after a complete bounded read. Issue codes include `size_mismatch`,
`hash_mismatch`, `missing`, `unexpected`, `not_regular`, `unsafe_name`,
`invalid_declaration` and explicit limit codes. I/O failures include numeric `errno`;
no timestamps or selected absolute paths enter the result. An undecodable unexpected
filesystem name uses `file_bytes_hex`. Invalid manifest declarations reject before
reading any payloads; undeclared entries are reported without opening them.

The manifest must be UTF-8 JSON (optional BOM), with no duplicate keys, nonfinite
numbers, unpaired surrogates or nesting beyond 128 levels. It must have integer
`schema_version: 1`, `tool: "bagkeep"` (the bundle format keeps its original
generator name), a supported `mode`, and a nonempty `files` object. Other top-level
metadata is not interpreted. Each `files` value must contain exactly `bytes`
(nonnegative integer, excluding booleans) and `sha256` (64 lowercase hexadecimal
characters). Names must be flat UTF-8 basenames of at most 255 bytes: no
empty/dot/dotdot names, slash, backslash, NUL or ASCII controls. `manifest.json`
cannot declare itself.

Verification bounds are **16 MiB manifest**, **256 MiB per payload**, **1 GiB
aggregate payload bytes**, **10,000 declared payloads** and **10,001 directory
entries** including the manifest. Declared and observed sizes are checked; reads use
64 KiB chunks and at most one extra byte to detect growth past a read budget. A
larger valid bundle can exceed these conservative limits and will reject.

Linux directory-descriptor operations anchor every child read to one held bundle
root. The selected root leaf, manifest and payloads must not be symlinks; manifest
and payloads must be regular files. No-follow/nonblocking opens and opened-object
checks reject substitutions and FIFOs without waiting for writers. Unexpected
directories/symlinks/special files invalidate the bundle too. Descriptors close on
success, rejection, I/O errors and interruption. Ancestors of the selected root are
trusted. This is no filesystem snapshot: hard links, concurrent in-place edits and
changes during acquisition are not excluded.

**Manifest hashes detect inconsistency, not authenticity. The manifest is not
self-hashed.** The result explicitly sets `hashes_prove_authenticity: false` and
`manifest_self_hashed: false`. Someone able to replace payloads and their manifest
can create a consistently matching bundle. Verification does not validate article
safety, source authenticity, semantic completeness or metadata outside `files`.

## Preview, strict mode and exits

```sh
notewrench archive rescue examples/archive/wallabag-v2.json -o archive-preview --dry-run
notewrench archive k2w examples/archive/karakeep-links.json -o links-only --strict
notewrench archive k2w examples/archive/karakeep-native.json -o rejected --strict --dry-run
```

The last example exits **3**: text/null records and list structure cannot be mapped
into Wallabag entries. It creates nothing. Dry-run performs the same validation and
conversion in memory, checks output conflicts, and prints a JSON plan, including
filenames and all issues. It executes no input scripts.

| Exit | Meaning |
| --- | --- |
| 0 | Bundle written, or valid dry-run plan. Check `loss_count` and `manifest.issues`: ordinary mode permits reported losses. |
| 2 | Invalid input, unknown format, output conflict, CLI usage or I/O error. |
| 3 | `--strict` found at least one `severity: loss` issue; **no output written**. |
| 130 | Interrupted. |

Warnings and informational issues do not block strict mode. Sanitization removals,
missing readable cached bodies, skipped native records, list losses, `k2w` duplicate
URLs, and sub-microsecond timestamp rounding are loss issues. Rescue duplicates are
warnings because all copies have their own article file. Unknown extra fields
preserved only in provenance are informational, not a claim that Wallabag imports
those fields.

## Rescue behavior

Input is the **Wallabag v2 export array**, with `url` and `title` strings; `content`
is HTML or null/missing, `tags` is an array of strings. Optional archive/star flags
accept booleans or integer 0/1. Time fields, when supplied, must be valid
timezone-qualified ISO 8601 timestamps, including Wallabag's `+0200` syntax (at most
six fractional digits). A bad field rejects the whole input before output is written.
A missing/empty body emits an explicit placeholder and a `missing_cached_body` loss;
strict mode rejects it.

Each entry produces `article-<stable-id>.html`. IDs are SHA-256 of the decoded record
serialized with sorted keys, UTF-8, two-space indentation and final LF; identical
repeated records get `-2`, `-3`, etc. Titles and source IDs never become paths.
Changing a record changes its filename; reordering distinct records does not.
Original order is preserved in the index and manifest. Duplicate detection uses the
**exact URL string**, without tracking-parameter removal or merging (use
[`notewrench links`](links.md) for duplicate review).

Body text, common semantic HTML, source, tags, dates, flags, and annotation
`quote`/`text` are rendered. Comments and quotes are escaped plain text; other
annotation metadata (including opaque `ranges`) is displayed as escaped JSON. No XPath
is executed and no highlight placement is promised.

HTML is rebuilt with a small tag and attribute allowlist. Scripts, CSS, event
handlers, forms, SVG/MathML, frames, embedded media and automatic external resource
loads are excluded. Images are removed unless explicitly mapped through `--assets`
below; omitted images have a `missing_image_asset` loss and alt text may remain. Safe
relative links are resolved against a safe source URL; only HTTP(S) and fragment
links survive. Non-HTTP(S), credential-bearing, malformed or control-containing URLs
are never clickable. Styling and some layout necessarily change. A restrictive meta
CSP and referrer policy provide additional protection. Clicking a source/body link can
still open a network page; creating or opening the archive itself requires no fetch.
This is a conservative archive renderer, not a general browser engine.

## Local static image manifest

`rescue` and `api-import` accept `--assets MANIFEST.json`. This opt-in format maps
**exact decoded `<img src>` strings** to user-saved local static PNG or supported
baseline/progressive JPEG bytes. It never downloads images or interprets source
references as file paths. Relative source strings are matched as written, without URL
resolution, decoding or query removal. One reference has one mapping across the
entire input.

```sh
notewrench archive rescue examples/archive/images/wallabag.json \
  --assets examples/archive/images/manifest.json -o with-images --strict
notewrench archive api-import examples/archive/api/karakeep-manifest.json \
  --assets examples/archive/images/manifest.json -o pages-with-assets --dry-run
```

The first synthetic example contains two image references and produces one article
and **one shared PNG**. Open `with-images/index.html` offline to see two red squares.
Its image manifest is:

```json
{
  "schema_version": 1,
  "assets": [
    {
      "reference": "https://example.org/red.png",
      "file": "red.png",
      "sha256": "becf0cf30d6978325d9ccdea315923e2dbbf7e591c2857b92b5f82e41957bb98"
    },
    {
      "reference": "local-red.png",
      "file": "red.png",
      "sha256": "becf0cf30d6978325d9ccdea315923e2dbbf7e591c2857b92b5f82e41957bb98"
    }
  ]
}
```

`file` is relative to the image manifest directory. Absolute paths, `..`, empty or `.`
components, backslashes, URL/drive syntax, non-files and **all symlink components
below that directory** are errors (`local_asset_path`, exit 2, no output). The
manifest itself cannot be a symlink. Directory-descriptor reads with `O_NOFOLLOW`
also reject a resource replaced by a symlink before opening. No implicit reads of
undeclared HTML references occur, even when the reference looks like an existing
local path or remote URL.

Each descriptor requires the expected lowercase SHA-256 of the file's original bytes.
A mismatch is `asset_sha256`, exit 2. Format is identified from file bytes,
independently of the source filename or URL extension. Supported subsets:

- Static, noninterlaced PNG, including chunk CRCs and bounded scanline data.
- **8-bit baseline JPEG** with one grayscale or three color components and one
  complete Huffman scan. Sampling factors 1..2 cover common 4:4:4, 4:2:2 and 4:2:0
  photos. Marker lengths, nonzero 8-bit quantizers, Huffman tables, component/table
  references, coefficient/run syntax, exact MCU count, byte stuffing, restart
  intervals/sequence, final padding and exact EOI are checked.
- **8-bit progressive JPEG (SOF2)** with the same component/sampling limits. DC must
  precede AC for each component; AC scans carry one component and a valid spectral
  band. First transmissions must not overlap. Refinement must match the previous bit
  position and lower it by one (Ah/Al at most 13). **Every coefficient band must be
  present and fully refined to Al=0** before EOI. This deliberately rejects some legal
  partial-precision JPEGs. DC scans may interleave; single-component scans use actual
  component block grids, including partial edges. Huffman AC EOB runs, ZRL, new
  coefficients and correction bits are validated against per-block nonzero history.
  EOB runs cannot cross scan/restart boundaries. Tables must exist before use;
  quantizers cannot be redefined after use. DHT/DRI may change between scans. The
  scan/entropy rules follow
  [ITU-T T.81 Annex B/G](https://www.w3.org/Graphics/JPEG/itu-t81.pdf).

Malformed/truncated images, trailing bytes (including appended scripts or a second
image), SVG, HTML, scripts, GIF/WebP, APNG, interlaced PNGs and unknown critical PNG
chunks reject (`unsupported_image`, exit 2, no output). Sequential multiscan,
lossless, arithmetic, hierarchical, 12-bit and CMYK JPEGs also reject. This is a
conservative validator, not a full image decoder or conversion service: JPEG
validation does not reconstruct pixels, apply inverse DCT or interpret color
profiles. Length-bounded APP/COM metadata, including EXIF, is opaque and retained
unchanged; metadata contents are not authenticated, sanitized or executed.

Maximum dimensions are **16 Mi pixels**. PNG decoded scanlines and JPEG nominal
samples (including padded blocks) are each capped at **64 MiB** per image. The
manifest and distinct declared files share a separate **64 MiB aggregate** cap, in
addition to the export/API input limit; at most 10,000 descriptors are accepted.
Progressive JPEGs additionally allow at most **128 scans**, **8 MiB of compact nonzero
coefficient masks**, and **64 Mi coefficient visits** summed across all scans (coded
blocks × band width). Large or unusually scan-heavy legal images can exceed these
conservative limits. All declared files are validated even when unused or in dry-run
mode. Images with identical byte hashes are validated once per manifest.

Archive articles replace mapped sources with `asset-<sha256>.png` or
`asset-<sha256>.jpg` and record `image/png` or `image/jpeg` in asset mappings, keeping
the original bytes. Identical bytes share one payload even across different local
files or articles. All valid declared images are included, including unused ones. Only
`src`, `alt`, `title` and `lang` appear on emitted images; `srcset`, CSS, event
handlers, `<source>` alternatives and embedded SVG are excluded and reported as
sanitizer losses. An article containing only mapped images counts as a cached body.
CSP permits same-origin images, with other resource types blocked.

A missing declared file yields `missing_asset_file`; an absent, undeclared or
unavailable image source yields `missing_image_asset`. Ordinary mode removes that
image, retains alt text when supplied, and records its reference/status in
`manifest.records[].image_references`. `--strict` exits 3 without writing on these
losses; `--dry-run` validates hashes and bytes without writing. Unsafe or unsupported
supplied files always reject the entire run, even if unused.

`provenance.image_assets` retains the complete decoded image manifest, its byte hash
and descriptor-to-payload mapping, including missing-file status and expected/actual
hashes. `manifest.image_assets` repeats its hash and mapping; `manifest.files` hashes
every text and binary payload. Original article HTML remains in provenance, and API
`wallabag.json` keeps the original HTML source strings. The rewritten offline article
references are for this archive; no claim is made that a live Wallabag importer
uploads the bundled images.

The synthetic JPEG examples use the same manifest schema:

```sh
notewrench archive rescue examples/archive/jpeg/wallabag.json \
  --assets examples/archive/jpeg/manifest.json -o with-photos --strict
notewrench archive rescue examples/archive/progressive/wallabag.json \
  --assets examples/archive/progressive/manifest.json -o with-progressive --strict
```

The first produces one article with two landscape references sharing one unchanged
48×32 JPEG payload; the second shares one 49×33 progressive `image/jpeg` payload.
Both references and expected/actual hashes remain traceable in provenance; no photo is
fetched. No image library is needed.

## Karakeep mapping and format boundary

The authoritative native schema is a JSON object with `bookmarks` and optional
`lists`. Each bookmark requires numeric `createdAt` (**UNIX seconds**), nullable string
`title`, string-array `tags`, nullable `content`, and nullable string `note`. `lists`
membership and boolean `archived` are optional in current native exports. Defined
list objects are validated, not treated as tags.

| Karakeep native field | Wallabag output / audit |
| --- | --- |
| `content: {type: "link", url}` | HTTP(S) URL becomes `url`; invalid/unsafe URL is reported and skipped. |
| `content: {type: "text", text}` | No native URL; explicitly skipped, complete text kept in provenance. No invented URL, even when the text looks like one. |
| `content: null` | Explicitly skipped as `null_content`; it may represent an excluded asset, but the tool does not guess. |
| Explicit `content.type: "asset"` | Outside the native union; recognized only to report `unsupported_asset` and retain the record. Other unknown types reject the input. |
| `createdAt` | UTC ISO timestamp in `created_at` (microsecond precision, rounding loss reported); strings, milliseconds outside supported date range, and invalid dates reject the input. |
| `title` | String preserved; null falls back to URL and is reported. |
| `tags`, `archived` | `tags`, `is_archived`; absent archived defaults false. |
| `note` | Escaped plain text in a clearly labeled `content` HTML section; not an annotation or cached article. |
| No native star flag | `is_starred: false`. |
| Lists, queries and membership | Loss reported; complete definitions/membership retained in provenance, never silently converted into tags. |
| Unknown fields | Retained in provenance, reported as unmapped. |

**Native link exports contain no cached article HTML.** The tool does not invent an
`includeContent` format, interpret API `htmlContent` as native content, fetch
articles, attach SingleFile, or communicate with a running instance. Generated
`content` contains only a note, or an empty string. Native source data does not supply
updated dates, stars or annotations, so these are not fabricated. Native text is
preserved in the sidecar rather than misrepresented as an importable URL.

A future Wallabag import may fetch URLs, skip duplicates already present, and handle
empty content differently by version. This command only produces files; no
live-instance import has been tested. It makes no lossless round-trip promise.

## Saved API pagination manifests

`api-import` reads **already saved local response JSON files** and creates both
`wallabag.json` and a static HTML archive. It never contacts either service or follows
response URLs. These are API responses, separate from native export formats; `k2w`
continues to handle only native Karakeep exports.

```sh
notewrench archive api-import examples/archive/api/wallabag-manifest.json -o wallabag-pages
notewrench archive api-import examples/archive/api/karakeep-manifest.json -o karakeep-pages
notewrench archive api-import examples/archive/api/karakeep-manifest.json -o plan --dry-run
```

Both two-page synthetic examples produce **2 Wallabag entries and 2 readable article
files**, containing `Wallabag cached body 1/2` or `Karakeep cached body 1/2`. They use
these pinned upstream contracts:

- [Wallabag API controller at 2c15fef](https://github.com/wallabag/wallabag/blob/2c15fef98033c43d86cf3dcdd97bfe53f3b4f22d/src/Controller/Api/EntryRestController.php)
  and [pagination tests at the same revision](https://github.com/wallabag/wallabag/blob/2c15fef98033c43d86cf3dcdd97bfe53f3b4f22d/tests/functional/Controller/Api/EntryRestControllerTest.php):
  `GET /api/entries.json`, with `page`, `pages`, `limit`, `total`, `_embedded.items`,
  and tag objects with `label`.
- [Karakeep OpenAPI at 88a9ff9](https://github.com/karakeep-app/karakeep/blob/88a9ff9cfdfa2b99da4d6a700ea2d0055bd3639d/packages/open-api/karakeep-openapi-spec.json):
  `GET /api/v1/bookmarks`, with `bookmarks`, required nullable `nextCursor`, ISO
  `createdAt`/`modifiedAt`, tag objects with `name`, `userId`, boolean
  `archived`/`favourited`, and link `content.htmlContent`. Saved responses must
  contain inline HTML when saved with upstream's `includeContent` option, or reference
  a saved body via `contentAssetId`. The tool issues no requests; the explicit local
  content manifest below can supply already-saved HTML.

The Karakeep manifest is:

```json
{
  "schema_version": 1,
  "source": {
    "format": "karakeep-api",
    "namespace": "karakeep:offline-example:alice",
    "user_id": "alice"
  },
  "pages": [
    {"file": "karakeep-1.json", "namespace": "karakeep:offline-example:alice", "cursor": null},
    {"file": "karakeep-2.json", "namespace": "karakeep:offline-example:alice", "cursor": "saved-next-1"}
  ]
}
```

For Wallabag, use `format: "wallabag-api"`, omit `user_id`, and replace each `cursor`
with its integer `page` (starting at 1). Response file paths are relative to the
selected manifest's resolved directory. Nested regular files are supported. Absolute
paths, parent traversal, URLs/drive syntax, backslashes, empty or `.` components,
control characters, missing files, non-regular files (including directories, FIFOs
and sockets), and **all symlink components below that directory** reject with
`local_page_path`, exit **2**, empty stdout and no output in ordinary, strict, dry-run
and combined modes. Errors identify `$.pages[N].file` and its filename. Internal
symlinks are intentionally rejected too; copy their regular-file contents into the
resource directory instead. The explicitly selected API manifest may use static leaf
or directory symlinks; its once-resolved parent directory is the trusted response
root.

Before reading the manifest, the tool opens that resolved parent and reads the
resolved manifest leaf relative to the held directory, with `O_NOFOLLOW`, `O_NONBLOCK`
and a regular-file check. A newly substituted leaf symlink rejects with `input_path`
and the selected path (exit 2, empty stdout, no output/plan). After acquisition,
retargeting the selected symlink or renaming/replacing its parent cannot choose a
different response root: manifest and pages use the same held directory. The
selection is never resolved again after the read.

The root stays open while directory-descriptor reads walk each component with
`O_NOFOLLOW`; the final open uses `O_NONBLOCK` and checks `fstat` for a regular file.
Replacing a leaf or not-yet-opened directory with a symlink at open rejects, even if
the outside file matches a supplied pin. Once a directory/file descriptor is open,
reads use that object rather than following later path replacements. This is symlink
confinement, not a filesystem snapshot: hard links and in-place changes to regular
files are not excluded; use pins and keep saved inputs stable. No confinement claim is
made for ancestors of the explicitly selected root. Input size is bounded at **64 MiB
in aggregate**, including the manifest and all page bytes, with at most 10,000 pages.
Each document also uses the existing JSON validation limits.

Each page descriptor may optionally include `"sha256": "<64 lowercase hex characters>"`
to pin the **original saved response bytes**. Both API formats support pins, including
manifests mixing pinned and unpinned pages. The hash includes UTF-8 BOM, whitespace and
line endings; it is not a hash of normalized JSON. A supplied pin must be exactly 64
lowercase ASCII hex characters: null, empty, uppercase, wrong-length and non-string
values reject. A malformed pin or any byte mismatch is `page_sha256`, exit **2**, with
no output directory or JSON plan, including `--strict`, `--dry-run` and their
combination. Mismatches report the descriptor location, filename and expected/actual
hashes before JSON decoding.

Use `api-import --require-page-hashes` to require a pin on **every** page descriptor in
either API format. An omitted `sha256` rejects with
`missing_page_pin: $.pages[N].sha256: required by --require-page-hashes`, exit **2**,
empty stdout and no output directory or JSON plan. Descriptor validation checks later
and unordered descriptors before reading any response page; `N` is the zero-based
input descriptor position. Supplied malformed pins and byte mismatches still report
`page_sha256`. This policy applies equally in ordinary, `--strict`, `--dry-run` and
combined modes. It changes validation only; accepted bundles and their verification
metadata are unchanged.

`provenance.pages[]` and `manifest.pagination.pages[]` retain descriptor order and the
original `page_index`, filename and request page/cursor. Each includes
`expected_sha256` (null if omitted), `actual_sha256`, and `status: "verified"` or
`"unpinned"`. The existing `sha256` field remains the actual byte hash. Only provenance
includes the decoded response. By default, unpinned manifests remain valid; `unpinned`
is metadata, not a loss issue. A matching pin verifies consistency with the user's
declared bytes, not server origin or snapshot completeness.

This pinned example also uses a separately pinned saved HTML body:

```sh
notewrench archive api-import examples/archive/content/pinned-api-manifest.json \
  --content-assets examples/archive/content/content-manifest.json \
  -o pinned-content --require-page-hashes --strict
```

It produces one readable article with zero losses. The page pin is
`2aab21e043b282a7b86027f6b6017350041498c9215912623e14fec08c1a6dfb`; the response's null
inline HTML and `contentAssetId` remain unchanged in provenance, while `body_source`
identifies the verified saved HTML used in the article and `wallabag.json`.

Choose a namespace identifying **one instance, account and query/sort scope**; repeat
it on every descriptor. Mixed declared namespaces are rejected with
`source_namespace`; Karakeep additionally requires every bookmark's `userId` to match
`source.user_id`. A saved Wallabag response does not prove the account it came from;
namespace labels and request cursors are user declarations, not credentials or
authenticated evidence. Do not combine different filtered queries under the same
namespace. File hashes identify saved bytes, not server origin.

Page descriptors may be unordered: Wallabag is assembled in page-number order,
Karakeep by following response `nextCursor` from request `cursor: null` through a
terminal response `nextCursor: null`. Every descriptor must be reachable. Wallabag
requires exactly pages 1..pages, matching response page identities, consistent
pages/limit/total and the expected item count on each page.

| Failure | Diagnostic and behavior |
| --- | --- |
| Repeated request page/cursor or response filename | `duplicate_page`, exit 2, no output. |
| Repeated source item ID, including identical copies | `duplicate_item`, exit 2, no arbitrary body selected. Different IDs with the same URL are retained with the existing `duplicate_url` warning. |
| Missing page, mismatched totals/counts, cursor gap, missing nextCursor, or orphan page | `incomplete_pagination`, exit 2, no output. A missing response file is also an error. |
| Descriptor/response page-number mismatch | `page_identity`, exit 2, no output. |
| Cursor loop | `cursor_cycle`, exit 2, no output. |

`manifest.pagination.complete` means only that **the supplied response sequence passes
these checks**. It does not prove an unfiltered, consistent live account snapshot, or
lossless migration. Sources can change between manual saves. Field losses and skipped
entries remain separately reported, even for a complete page sequence. `--strict`
rejects all loss issues with exit 3 and no output; `--dry-run` validates every page
without writing.

Wallabag HTML `content` and Karakeep `content.htmlContent` are copied verbatim into
`wallabag.json`; article files use the existing sanitizer. Without a resolved local
content asset, missing HTML produces the existing placeholder and
`missing_cached_body` loss. API notes are retained separately in provenance, never
substituted for article bodies. Karakeep non-link content is explicitly skipped with
`api_non_link_not_mapped`; text and assets survive in provenance, without an invented
URL. Dates, flags, titles, annotations (Wallabag) and tag labels/names are mapped;
nullable titles fall back to the link title or URL. Unmapped fields (including tag
identities, notes, asset references, summaries and processing metadata) produce
`api_fields_not_mapped` loss issues. Thus ordinary example imports succeed while strict
mode rejects their metadata losses. No live target import is tested.

The provenance sidecar contains the complete decoded input manifest and every response
page, including original HTML, plus each file's byte SHA-256. Its `mapping` lists
namespace, filename, zero-based page/item positions, source ID, output index (null when
skipped), mapped field paths and provenance-only fields. It also contains all loss
issues, including sanitization losses, with source locations. Manifest article records
carry the same source locator; page indices always refer to the **input manifest's
descriptor order**, even when traversal reorders pages. As with exports, JSON
whitespace/encoding is not preserved. Response metadata such as `_links` is retained
as inert data, never fetched.

## Local Karakeep saved HTML manifest

`api-import --content-assets MANIFEST.json` can supply cached article HTML when a
Karakeep API link has absent or null `content.htmlContent`. The manifest is a local
input format of this tool, not an upstream export or asset API. It maps **exact
`contentAssetId` strings**, never URLs or inferred file paths. It is separate from the
optional `--assets` image manifest; both can be used in the same import.

```sh
notewrench archive api-import examples/archive/content/api-manifest.json \
  --content-assets examples/archive/content/content-manifest.json -o saved-content --strict
```

This synthetic example produces **one Wallabag entry and one readable article** from
`body.html`, with zero losses. Its API response has `htmlContent: null` and
`contentAssetId: "saved-html-1"`. Its content manifest is:

```json
{
  "schema_version": 1,
  "source": {
    "format": "karakeep-api",
    "namespace": "karakeep:offline-example:alice",
    "user_id": "alice"
  },
  "assets": [
    {
      "contentAssetId": "saved-html-1",
      "file": "body.html",
      "sha256": "27a0438d535ca6a960b5365b26951e5d74624dd3c80aa533741652437f3191fb"
    }
  ]
}
```

`source` must match the API manifest's Karakeep format, namespace and user ID exactly,
even when inline HTML exists. Wallabag API imports reject this option's manifest. Scope
labels and asset IDs are user declarations; matching hashes verify saved bytes, not
server origin, ownership or association with a bookmark. Duplicate asset IDs reject
with `duplicate_content_asset`, even for identical copies. Multiple IDs may point to
one file, and multiple bookmarks may use the same ID. No IDs are normalized or
interpreted as paths.

Only absent or null inline HTML enables fallback. A supplied inline string, including
an empty string, takes precedence. Invalid inline field types reject rather than being
hidden by a saved body. Non-link bookmarks stay explicitly skipped. API notes are never
used as fallback bodies. An empty or script-only saved HTML file still has a
`missing_cached_body` loss after sanitization; matching its hash does not prove
readability or safety.

Each descriptor requires exactly `contentAssetId`, `file` and a lowercase `sha256` of
the **original file bytes**. Files must be UTF-8 HTML; an optional UTF-8 BOM is
stripped for decoded content while still participating in the byte hash. Whitespace
and line endings are preserved in decoded HTML. Content is not sniffed or executed. It
is copied to `wallabag.json` and rendered through the same sanitizer as inline HTML.
The saved source is retained as inert JSON, not published as an additional unsanitized
HTML file.

`file` is relative to the selected content manifest directory, with the same
descriptor-confined reader as images: absolute paths, traversal, empty or `.`
components, URL/drive syntax, backslashes, symlinks in any descriptor component,
symlink swaps, and non-regular files reject with `local_asset_path`. The content
manifest itself cannot be a symlink. The manifest plus distinct local files share a
**separate 64 MiB aggregate cap**, with at most **10,000 descriptors**, in addition to
API and image limits. Repeated HTML is also bounded: the sum of original body bytes
across descriptors, and independently across bookmarks selecting saved bodies, must
each stay within **64 MiB** (`expanded_content_limit`, exit 2). All declared files,
including unused ones, are validated and retained in provenance. A hash mismatch
(`content_asset_sha256`), invalid UTF-8 (`content_asset_utf8`), invalid schema, wrong
scope, or unsafe path exits 2 without writing, including in dry-run.

A missing declared file has `missing_content_asset_file` loss and a `missing_file`
mapping status. A fallback ID without an available declaration adds
`missing_content_asset` with `undeclared` or `missing_file` status. Ordinary mode
preserves the entry with the existing missing-body placeholder and
`missing_cached_body` loss. Strict mode rejects all losses with exit 3 and no output.
Missing files are losses even if unused; missing or undeclared images inside selected
saved HTML follow the separate image-manifest rules.

`provenance.content_assets` retains the complete decoded content manifest, its byte
hash, and every descriptor's expected/actual byte hash, source filename, status, byte
count and decoded HTML (null for missing files). API response pages remain unchanged
in provenance. `manifest.content_assets` repeats the manifest hash and mapping without
the HTML. Each Karakeep article record and provenance mapping has `body_source`:
`inline`, `content_asset` (with ID, descriptor index, status and file hash), or
`missing`. A selected asset maps `content.contentAssetId` to Wallabag `content`;
page/item locators still identify the original bookmark. Bundle payload hashes cover
the resulting provenance, Wallabag JSON and sanitized articles. No downloads, live
asset uploads or live Wallabag imports are performed.

## Audit bundle and safety

All commands produce:

- `manifest.json`: version, input byte SHA-256, counts, zero-based source entry
  positions, stable IDs, output mapping, and issues with severity and counts.
- `provenance.json`: **complete decoded original input**, including unknown fields,
  original HTML, text, notes, annotations, list data and input byte hash. JSON
  whitespace/encoding is not preserved; the input itself is never changed.
- Mode-specific files: rescue's HTML articles/index, k2w's `wallabag.json`, or
  api-import's HTML articles/index **and** `wallabag.json`.

The manifest hashes every payload file (including provenance), not itself. Bundles do
not add wall-clock time or absolute input/output paths, so an identical input produces
identical output bytes. The manifest keeps `"tool": "bagkeep"` and its generator
version so that bundles stay compatible with earlier releases. Original unsanitized
HTML in provenance is inert JSON; do not treat its strings as safe HTML. Sidecars can
contain private source data: keep the entire bundle private when needed.

The explicitly selected **output parent follows static symlinks** for compatibility.
Its path is resolved once, then opened with `O_DIRECTORY|O_NOFOLLOW`. The output leaf
is never resolved or followed: even a dangling symlink is a conflict. Dry-run and
strict rejection check the target relative to this held parent without creating
staging or reservation files. A parent changed to a symlink between resolution and
opening rejects with exit 2 and empty stdout.

Outputs are fully staged in an exclusive temporary sibling directory, then published
with one Linux `renameat2(RENAME_NOREPLACE)` operation. The complete directory appears
at once; no destination directory is created and reopened for payload writes. Existing
files, empty/nonempty directories and symlinks are refused, including targets
appearing immediately before publication (exit 2, empty stdout). There is no
overwrite/force switch.

Publication requires **Linux**, a libc exposing `renameat2` (glibc 2.28+ provides it),
and kernel/filesystem support for `RENAME_NOREPLACE`. Python's standard library
`ctypes` calls libc; no extra package or executable is needed. Unsupported platforms,
a missing symbol, or unsupported kernel/filesystem flags fail with exit 2 and empty
stdout, cleaning staging where possible. There is **no fallback to overwriting
rename**. Dry-run and strict rejection need no publication primitive and create no
staging files. See the
[Linux rename manual](https://man7.org/linux/man-pages/man2/rename.2.html).

Staging creation, writes, publication and cleanup use the held output parent and
relative directory-descriptor operations. The private staging entry is checked against
its held descriptor before publication. After acquisition, retargeting the selected
parent symlink or renaming/replacing the parent cannot redirect publication or
cleanup: they continue inside the original held parent. The JSON result keeps the
requested absolute output path; after a retarget, locate the bundle in the original
parent. Bundle directories are created with mode 0700.

A process interruption before publication leaves the destination absent; after
publication it leaves the complete bundle. Cleanup never empties a staging descriptor
after its staging name has disappeared, including an exception immediately after
successful rename. Thus a nonzero exit or missing success JSON can still accompany a
complete bundle; inspect it before retrying. A hard kill before publication may leave a
hidden `.bagkeep-*` staging directory. Ordinary errors clean owned staging where
filesystem operations permit; moved/replaced staging is conservatively left alone.

This is atomic namespace visibility, **not power-loss durability**: no `fsync` is
performed and no machine-crash/filesystem-recovery guarantee is made. These descriptor
guarantees are not a filesystem snapshot. Ancestor changes during acquisition, hard
links, in-place edits, and hostile same-user changes to private staging entries during
acquisition or between the identity check and rename are not excluded. Keep the
staging parent trusted.

The explicitly selected positional input for `rescue`, `k2w` and `api-import` must
open as a **regular file**. A nonblocking descriptor open followed by `fstat` checks
the opened object before reading bytes. FIFOs without writers, directories, sockets
and nonregular replacements at open reject promptly with `input_path`, exit **2**,
empty stdout and no bundle, staging directory or JSON plan, including `--strict`,
`--dry-run` and their combination. Open/read errors also report `input_path` and the
selected path.

For compatibility, explicit input selection **follows symlinks**, including directory
symlinks, when the opened target is regular; missing/broken targets reject. This
policy applies to the positional export/API manifest, not optional image/saved-HTML
manifests or manifest-declared resources. API response paths use the no-symlink
confinement policy beneath the held API manifest parent.

UTF-8 JSON (optional BOM), maximum **64 MiB** per input, is supported. Processing is in
memory, not streaming. Duplicate JSON keys, invalid JSON numbers, unpaired Unicode
surrogates, wrong top-level shapes and incompatible known field types are errors.
Unknown fields survive in provenance; unknown format wrappers do not get guessed. JSON
nesting beyond 128 levels is rejected; large/malformed documents may also be rejected.
No comprehensive browser-engine sanitizer audit was performed.

## Scope, alternatives and sources

[bagcarry](https://github.com/Rezarys/bagcarry) and
[wb2kk](https://github.com/fikovnik/wb2kk) already address related forward Wallabag →
Karakeep migration. This command focuses on independently readable cached-body rescue
and the reverse native-export mapping, with explicit losses. This is an overlap
acknowledgment, not a claim that those tools lack other features.

Formats were checked on 2026-11-11 against the public
[Wallabag v2 fixture](https://github.com/wallabag/wallabag/blob/master/tests/fixtures/Import/wallabag-v2.json),
[Karakeep native schema](https://github.com/karakeep-app/karakeep/blob/main/packages/shared/import-export/exporters.ts),
[Wallabag v2 importer](https://github.com/wallabag/wallabag/blob/master/src/Import/WallabagV2Import.php),
[Wallabag importer](https://github.com/wallabag/wallabag/blob/master/src/Import/WallabagImport.php)
and [annotation entity](https://github.com/wallabag/wallabag/blob/master/src/Entity/Annotation.php).
The importer requires a URL, which is why native text is kept in provenance. These are
branch snapshots, not a guarantee of compatibility with every release. The original
[import request](https://github.com/karakeep-app/karakeep/issues/687) is motivation,
not proof of a current upstream defect. The implementation is original code; examples
are synthetic. See [THIRD_PARTY.md](../THIRD_PARTY.md).
