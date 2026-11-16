# `notewrench epub`

You have a pile of saved articles in Wallabag or Karakeep and want to read them on an
e-reader, offline, without trusting a web export to fetch anything. Use
`notewrench epub` to turn **locally cached article bodies** into one deterministic
**EPUB 3** plus a JSON provenance/loss report that says exactly which entries were
included, skipped or changed. It can also preview a selection (`plan`) and
structurally check a book (`validate`).

Formerly the standalone `readbundle` tool. Python 3.11+, standard library only.

**Reads / writes:** it reads only the JSON exports, image manifest, chapter-order file
or archive bundle you name, and never modifies them. `build` creates one **new** EPUB
and one **new** JSON report (existing files are never overwritten); `plan`, `validate`
and `build --dry-run` write nothing. No network requests, server, API keys or running
application.

**Platforms:** Linux and macOS (POSIX descriptor-relative, no-follow reads and hard-link
publication); Windows is not supported. Creating an archive bundle to feed
`--archive-bundle` needs Linux (see [`notewrench archive`](archive.md)). An
`--archive-bundle` path is opened without following symlinks in any component,
including its ancestors; on macOS `/tmp` and `/var` are symlinks, so pass a real path
such as `/private/tmp/...` or a directory under your home folder.

## Quick start

```sh
pip install notewrench
```

The examples use the synthetic inputs under [`examples/epub/`](../examples/epub/), so
run them from a clone of the repository:

```sh
notewrench epub --help
notewrench epub build examples/epub/wallabag.json \
  --title "Weekend reading" --language zh-Hans \
  -o reading.epub --report report.json
notewrench epub validate reading.epub
```

The example has two original synthetic articles. These commands create six ZIP
members: mimetype, container.xml, package.opf, nav.xhtml and two XHTML chapters.
`report.json` contains both included IDs, their source URLs and the omitted-image
record. It contains an EPUB SHA-256 for checking the book.

## Commands

| Command | Purpose |
| --- | --- |
| `build INPUT... -o BOOK.epub` | Convert cached bodies to an EPUB and a JSON report (`--report`, default `BOOK.report.json`) |
| `plan INPUT...` | Print usable keys and provenance (source or proposed order) as JSON; writes no files |
| `validate BOOK.epub` | Check ZIP/XML/manifest/spine/nav/images against this tool's static XHTML/PNG/JPEG profile |

Options shared by `build` and `plan`:

| Option | Meaning |
| --- | --- |
| `--format {auto,wallabag,karakeep-export,karakeep-api}` | Input adapter (default `auto`) |
| `--text-mode {literal,markdown}` | Karakeep text bodies: literal (default) or restricted static Markdown |
| `--archive-bundle DIR` | Read an explicit [`notewrench archive api-import`](archive.md) bundle instead of positional inputs (`--bagkeep-bundle` is accepted as the former spelling) |
| `--image-manifest FILE` | Explicit local PNG/JPEG declarations |
| `--tag TAG` | Include entries with any matching tag (repeatable, case-sensitive) |
| `--id INPUT_NUMBER:ID` | Include an exact key (repeatable); native Karakeep IDs are `entry-N` |
| `--exclude-id INPUT_NUMBER:ID` | Exclude an exact known key (repeatable; overrides tag/ID inclusion) |
| `--skip-missing` | Explicitly omit missing/empty/unsupported bodies and record each in the report |
| `--chapter-order FILE` | Strict UTF-8 JSON array of all usable selected keys in chapter order |

`build` also accepts `-o/--output` (required), `--report`, `--title` (default
`Reading bundle`), `--author` (default `notewrench`), `--language` (simple BCP 47 tag,
default `en`), `--modified` (fixed UTC timestamp) and `--dry-run`.

## Inputs and honest format boundaries

| Adapter | Accepted local JSON | Body and IDs |
| --- | --- | --- |
| `wallabag` | Wallabag v2 export array | HTML string `content`; original string/integer `id`, or `entry-N` if absent; string-array `tags` |
| `karakeep-export` | Native `{ "bookmarks": [...], "lists": [...] }` | `content.type="text"` and `content.text`; no native IDs, so `entry-N` (1-based original position) |
| `karakeep-api` | One saved API bookmark object, `{ "bookmarks": [...] }` page, or an array with explicit `--format karakeep-api` | String `id`; `content.type="link"` with actual `content.htmlContent`, or text; tags are objects with `name` |

`--format auto` recognizes Wallabag arrays, native Karakeep envelopes, single API
bookmarks and API envelopes containing IDs. An empty array or API array needs an
explicit format. A build can mix auto-detected files. `--format` applies to every
input when specified. JSON must be UTF-8 (optional BOM); duplicate properties,
non-finite numbers, duplicate IDs within a file, wrong relevant field types and unknown
shapes/content types fail explicitly. Unsupported API `asset`/`unknown` bookmarks are
missing-body entries, never invented text.

**Native Karakeep link exports have no cached HTML.** Even when the website is
reachable, the tool never fetches it or substitutes the note, summary, title or
description as article body. A non-native `htmlContent` field in a native export is
rejected: use an actual saved API snapshot and the API adapter instead. Karakeep text
is preserved literally in a preformatted block by default, including Markdown and
HTML-looking text. Use `--text-mode markdown` to opt into the restricted static
rendering described below; this affects native and API text bookmarks, while Wallabag
and cached API HTML retain their existing HTML handling.

API snapshots are local files you already obtained. The tool does not log in, fetch
pages, follow API cursors or load application asset IDs. Each supplied page is
processed independently; a non-null `nextCursor` is reported under `warnings` as
`api_more_pages`. The tool cannot establish that an entire account was exported.
Wallabag tags are strings, following its official v2 fixture; alternate API tag
objects or arbitrary database dumps are not supported. The tool selects article bodies
and a small metadata subset (title, URL, tags, ID); annotations, notes, lists, dates,
archive/star state and other export metadata are outside this book format (see
[`notewrench wallabag-notes`](wallabag-notes.md) for annotations). Keep the original
JSON as the authoritative full archive.

These boundaries follow the
[Wallabag v2 fixture](https://github.com/wallabag/wallabag/blob/master/tests/fixtures/Import/wallabag-v2.json),
[Karakeep native schema](https://github.com/karakeep-app/karakeep/blob/main/packages/shared/import-export/exporters.ts)
and [API types](https://github.com/karakeep-app/karakeep/blob/main/packages/shared/types/bookmarks.ts),
checked on 2026-11-11. Native-export HTML is deliberately not invented.

## Build directly from an archive bundle

```sh
notewrench epub build --archive-bundle api-archive \
  --title "Rescued reading" -o rescued.epub --report rescued.json
notewrench epub validate rescued.epub
```

This explicit selector accepts bundles written by `notewrench archive api-import`
(manifest `tool: "bagkeep"`, version **0.1.0**, `schema_version` **1**, mode
`api-import`; bundles from the former standalone `bagkeep` 0.1.0 are identical) with
declared `wallabag.json`, `provenance.json`, `index.html`, article HTML and optional
local PNG/JPEG payloads. It never guesses from a directory name or suffix.
Rescue-only directories and other tool/schema versions fail. Supply one bundle,
without positional inputs or `--image-manifest`; `--format` may be `auto` or
`wallabag`. Existing tag/ID selection, dry-run, no-overwrite and missing-body policy
still apply. Article keys use input number 1 and the Wallabag ID (or `entry-N` when
absent).

To try it, use the generated two-article bundle shipped with the test suite, or create
one from the matching synthetic API pages (creating a bundle requires Linux, see
[`notewrench archive`](archive.md)):

```sh
notewrench epub build --archive-bundle tests/epub/fixtures/archive-bundle -o rescued.epub
notewrench archive api-import examples/epub/archive-api/pages.json \
  --assets examples/epub/archive-api/assets.json -o api-archive
```

Before conversion, every declared payload, including unused articles/assets, is read as
a bounded regular file and checked against its exact byte count and SHA-256. Relative
paths cannot contain empty, `.`/`..` or backslash components; absolute paths,
controls, symlinks in any component (including the chosen root and its ancestors),
FIFOs and missing files fail before either output is published. On macOS, `/tmp` and `/var` are
symlinks, so pass bundles by their real path (for example under `/private/tmp`). Conversion uses that
verified in-memory snapshot. Limits are 4 MiB for the archive manifest, 20,000
payloads, 32 MiB per payload and 64 MiB total including the manifest. The image
limits/profile below also apply (8 MiB/image, 32 MiB distinct image bytes, at most
1,000 article/source declarations). Extra undeclared files are ignored. Hashes
establish consistency with the supplied manifest, not authenticity.

The adapter uses the original HTML in `wallabag.json`, maps each article's archived
image references into the image manifest model, and reuses the HTML cleaner, resource
deduplication and EPUB validator. Shared bytes produce one OPF image item. All archived
image mappings, even unused ones, must pass **this command's PNG/JPEG structural
subset** (below); this does not cover every JPEG accepted by `notewrench archive`
(progressive JPEGs, for example). Unsafe/unrepresentable source labels and broader
image formats fail rather than being downloaded or implicitly converted. Undeclared
image URLs stay reported omissions; no external link or image triggers a network
request.

The usual report additionally contains `bagkeep_archive`: exact manifest and Wallabag
digests, all verified payload hashes/sizes, API source/pagination data, image source
mappings, and per-article source identity, upstream article file/hash, Wallabag index,
image references, body-source metadata and EPUB `output_index`. Indices are
zero-based; `output_index` is null for excluded/skipped articles or when the build
fails. `cached_body: false` is treated as a missing body even if the Wallabag payload
contains a placeholder or note (skip reason `bagkeep_missing_cached_body`). Omitting it
requires explicit `--skip-missing`; the report retains that reason.

Upstream diagnostics remain verbatim under `bagkeep_archive.upstream_issues`, with a
loss count and code counts; upstream skipped count and content-asset summary also
survive. The usual top-level `changes` records this EPUB conversion only. Upstream
losses are historical observations, not a guarantee that the original Wallabag HTML
had the same sanitization losses. Neither a complete account snapshot nor recovery of
already missing content is claimed. Keep the original archive and report; source
mappings and diagnostics can contain private data.

### Optional EPUBCheck conformance check

Built-in `validate` is structural validation, not EPUBCheck; no EPUBCheck pass or
reader compatibility is claimed. For a separate optional conformance check, use the
official [EPUBCheck 5.3.0 release](https://github.com/w3c/epubcheck/releases/tag/v5.3.0)
artifact (needs a Java runtime):

```sh
curl -fL -o epubcheck-5.3.0.zip \
  https://github.com/w3c/epubcheck/releases/download/v5.3.0/epubcheck-5.3.0.zip
printf '%s  %s\n' \
  6c07e68584b2e2ce2f89fe06e1246dfead3eb36b46b340e7d93524f29dcff6c5 \
  epubcheck-5.3.0.zip | sha256sum -c -
unzip epubcheck-5.3.0.zip
java -jar epubcheck-5.3.0/epubcheck.jar /absolute/path/to/rescued.epub
```

The ZIP digest above is from the official release asset metadata. The result is
independent of this tool's validator and of device/reader testing.

## Optional Markdown text mode

```sh
notewrench epub build examples/epub/karakeep-markdown.json \
  --text-mode markdown -o notes.epub --report notes.json
notewrench epub validate notes.epub
```

This zero-dependency renderer recognizes a deliberately small subset of
[CommonMark 0.31.2](https://spec.commonmark.org/0.31.2/), not general Markdown.
Separate blocks with blank lines. Supported blocks are ATX headings (`#` through
`######`, with up to three leading spaces), paragraphs, flat one-line-item lists with
one consistent bullet (`-`, `+`, `*`) or numeric delimiter (`.` or `)`),
single-paragraph quotes with `> ` on every line, and column-zero backtick/tilde fenced
code. List markers need exactly one space; ordered starts support up to six digits.
Closing code fences may be longer than the opener; unclosed fences consume the
remaining text as code. Code info strings such as `python` are retained in
`markdown_code_info` report events; no syntax highlighting is loaded.

Inline support is plain text/soft line breaks, ASCII-punctuation backslash escapes,
non-nested emphasis/strong, single-backtick code spans, semicolon-terminated character
references, and simple `[label](destination)` links without nested markup, titles,
whitespace or parentheses in bare destinations. Angle-bracket destinations permit
parentheses. For example, `## Weekend`, a blank line and `- Read this` produce an `h2`
and a `ul/li` in the chapter; a fenced `<script>` stays escaped code.

Matching `*text*` or `_text_` renders as `em`; `**text**` or `__text__` renders as
`strong`. Delimiters use CommonMark Unicode whitespace/punctuation flanking rules: no
whitespace just inside either delimiter; intraword stars such as `read*note*book` work.
In ordinary text, complete underscore runs of any width are plain text when both
immediate source neighbors are Unicode letters or numbers (`str.isalnum()`):
`read_note_book`, `中文__笔记`, `1___2`. This includes Unicode numeric characters such
as superscripts, Roman numerals and fractions. Combining marks, joiners, punctuation
and whitespace do not qualify; source is not Unicode-normalized, and entity/escape
decoding cannot supply flanking characters. For example, `read_notes and **saved**.`
produces `<p>read_notes and <strong>saved</strong>.</p>` without a fallback event.
Emphasis interiors keep their conservative subset: `*read_notes*` still falls back
atomically. Interiors support plain text, soft line breaks, character references and
backslash escapes. Code spans and link destinations outside emphasis are opaque: their
`*`/`_` characters and backslashes stay data. Backslashes in destinations remain
subject to the unsafe-URI filter; they are not decoded into active links. For example,
`Read *one* and __two__.` produces `<p>Read <em>one</em> and <strong>two</strong>.</p>`.

In ordinary inline text, flat emphasis interiors and simple flat link labels, a
backslash before any of the 32 ASCII punctuation characters emits that character as
text. Escaped `*`/`_` never become delimiters, and escaped backslashes never escape the
next character. For example, `Read \*literal\* and **bold**.` produces
`<p>Read *literal* and <strong>bold</strong>.</p>` without a literal-fallback event.
Escaped angle brackets stay safe text; `\&amp;` displays `&amp;` without entity
decoding. Backslashes before other characters (including Unicode punctuation), or at
the end of a block, stay visible. Fenced code and code spans retain their literal
behavior.

For example, `[Read \*literal\*](https://example.invalid/notes) and **saved**.`
produces `<p><a href="https://example.invalid/notes">Read *literal*</a> and <strong>saved</strong>.</p>`
without a literal-fallback event. In a label, `\]` is text rather than the closing
bracket; `\\` produces one backslash, so an immediately following `]` closes the
label. Non-punctuation backslashes stay visible there too. Escaped delimiters and
entity-decoded characters remain text; escaped angle brackets cannot create HTML.
Labels may contain soft line breaks and remain flat: unescaped brackets, backticks,
`*`, `<` and `>` are unsupported. Flat labels use the same source-flanked underscore
rule as ordinary text, so `[read_notes](https://example.invalid/notes) and **saved**.`
renders without fallback. Other raw underscores, nested labels, code/emphasis in
labels, malformed links and hard breaks preserve the entire inline run and its
source-line event. Destinations retain their recognition, character-reference decoding
and URI filtering; label escape decoding never applies to destinations.

Soft breaks also work beside label escapes and entities. Odd backslash runs before a
newline still make unsupported hard breaks; paired backslashes are text. Two or more
spaces before a newline also retain whole-run fallback. Blank lines still separate
blocks, and multiline list items remain unsupported. Original CR/CRLF endings stay
exact in the report and render as LF.

Unmatched delimiters, mismatched markers/widths, runs of three or more markers (except
accepted intraword underscores outside emphasis), nested emphasis
(`**outer *inner* outer**`), or code/links inside emphasis preserve the **entire inline
run** literally with a `markdown_literal_inline` event. This conservative fallback also
covers intraword underscores inside emphasis, invalid flanking, and backslash-newline
hard breaks; no partial emphasis survives. Escapes and character references decode only
in recognized runs, never in literal fallback.

Nested/multiline lists, nested quotes, setext headings, indented code, reference links,
images, tables, extensions and mixed blocks without blank-line separation are outside
this subset. Unrecognized block syntax stays visible in a preformatted block;
unrecognized inline syntax keeps the entire inline run literal. Both produce
`markdown_literal_block`/`markdown_literal_inline` events with 1-based source line
ranges, rather than silently deleting text. Raw HTML also stays literal, including
scripts, declarations and event attributes.

Rendered markup always passes through the HTML cleaner and EPUB validator. Unsafe links
become inactive and are reported; no images or URLs are fetched. Invalid XML characters
still reject the build with exit 2 and no outputs. The mode applies to all supplied
Karakeep text bodies; it is never inferred from their contents.

The report records `text_mode`; each rendered text entry in `included` (or `not_built`
on a missing-body failure) additionally retains exact `original_text` and
`original_text_sha256` over its UTF-8 bytes, including original line endings. Keep
that report with the EPUB for source traceability. It contains the original text,
including inactive content and private data. Repeated builds with the same mode follow
the EPUB determinism boundary; different modes may produce different content and
identifiers.

## Selection and missing bodies

```sh
# Two source files; skip the native link explicitly and retain its report entry.
notewrench epub build examples/epub/karakeep-export.json examples/epub/karakeep-api.json \
  --skip-missing --tag weekend --title "Saved notes" \
  -o notes.epub --report notes-report.json

# Qualified ID = input number : source ID; retain original reading order.
notewrench epub build examples/epub/wallabag.json \
  --id 1:101 --tag 中文 -o selected.epub

# Omit a few known entries from a broader selection; no renumbering.
notewrench epub plan examples/epub/wallabag.json --exclude-id 1:101
notewrench epub build examples/epub/wallabag.json examples/epub/karakeep-export.json \
  --tag weekend --exclude-id 1:101 --exclude-id 2:entry-2 -o remaining.epub

# Full in-memory build and validation; no output files created.
notewrench epub build examples/epub/wallabag.json -o preview.epub --dry-run
```

Repeated `--tag` values match **any** exact, case-sensitive tag. Repeated `--id` values
match any qualified key. Tag and ID filters combine with AND. Unknown ID keys fail to
catch typos; an unmatched tag produces a no-chapters failure. The report records
filtered entries under `excluded`; input/entry order is preserved unless an explicit
chapter order is supplied.

Repeatable `--exclude-id INPUT_NUMBER:ID` removes any of those exact known keys from
either `plan` or `build`, also with `--archive-bundle DIR`. Exclusion wins over
matching tags and inclusion IDs. Repeats are harmless. Keys are not trimmed,
case-folded or Unicode-normalized; native `entry-N` positions and input numbers stay
fixed. Unknown exclusion keys fail with exit 2 even when another filter would omit
them. All inputs and declared resources/archive payloads are validated before
filtering; exclusions cannot hide schema errors or corrupt unused assets. Excluded
bodies are not cleaned or counted as missing. Their original provenance appears under
`excluded` with reason `explicit_id_exclusion`, even if another filter would also
reject them; other filtered entries keep `selection_filter`. Excluding every usable
chapter still fails with `No usable chapters selected.`

The library accepts `notewrench.epub.core.plan(inputs, exclude_ids=["1:101"])` and
`notewrench.epub.core.prepare(inputs, exclude_ids=("1:101",))`. `exclude_ids` must be a
sequence of exact qualified key strings, not a bare string, path, set, mapping or
iterator. Malformed/unknown entries raise `BundleError`. Empty or omitted exclusions
preserve existing report and EPUB bytes. The archive-bundle selector is the
`bagkeep_bundle=` keyword of both functions.

A selected missing, blank or empty-after-cleaning body rejects the build by default
(exit **2**), writes a **failed report**, and creates **no EPUB**. The report lists
`skipped` reasons and `not_built` candidates. `--skip-missing` explicitly permits those
omissions and lists them in `skipped`. Zero usable chapters always fails. On success
exit is **0**, report status is `completed`, and `included` names chapters actually
present. Fatal JSON/schema/unsafe-XML/output errors exit 2 with a clear stderr message
before publication; they do not promise a report. Argument syntax errors also exit 2.

`--report` defaults to the EPUB path with suffix `.report.json`. Both output paths must
be new, distinct from inputs, and have existing parent directories. Existing files and
symlinks are never overwritten; there is no force option. Outputs are staged completely
and published with exclusive local hard links; normal publication errors roll back
newly published files. A crash between the two publications can leave one complete
output. Use a filesystem supporting hard links. Dry-run checks destination conflicts
too, but creates no output or temp files.

## Plan selection without an EPUB

```sh
notewrench epub plan examples/epub/wallabag.json examples/epub/karakeep-export.json \
  --skip-missing --text-mode markdown > selection.json
python3 -c 'import json; p=json.load(open("selection.json")); print(json.dumps(p["chapter_order"], ensure_ascii=False, indent=2))' > order.json
# Edit order.json to rearrange every key, then use the same inputs and selection options.
notewrench epub plan examples/epub/wallabag.json examples/epub/karakeep-export.json \
  --skip-missing --text-mode markdown --chapter-order order.json
notewrench epub build examples/epub/wallabag.json examples/epub/karakeep-export.json \
  --skip-missing --text-mode markdown --chapter-order order.json -o planned.epub
```

`plan` prints JSON to stdout without requiring an EPUB or report destination. It
shares build's adapters, full declared image/archive validation, tag/ID selection, body
cleaning, Markdown mode and missing-body policy, and accepts the same selection
options listed above. It never constructs, serializes or validates an EPUB, stages
files, publishes files or accesses the network. Shell redirection in the example saves
stdout; `plan` itself writes no files.

On success, exit **0** and `status: "planned"` mean selection is usable. The
schema-version-1 object contains `chapter_order`, a complete array of usable qualified
keys, and `included` with their provenance in the same order. Without
`--chapter-order` this is source order. Extract that array unchanged for
`build --chapter-order`, or rearrange it without adding or dropping keys. Keep inputs
and selection options unchanged. Native `entry-N` positions, input numbers and exact
Unicode IDs are retained. Templates obey the 10,000-key and 1 MiB serialized-array
bounds; larger templates fail. This checks selection and resources; book metadata and
EPUB-specific size/profile limits are checked by `build`.

`plan --chapter-order order.json` accepts the same bounded, once-read strict UTF-8 JSON
file and complete-permutation rules as `build` below. For example,
`plan --archive-bundle DIR --chapter-order order.json` previews an archive order. After
full input/resource validation and successful missing-body policy, it reorders only
`included`, `chapter_order` and archive `plan_index`. Source IDs, input numbers, native
positions, keyed changes and all other evidence stay fixed. Duplicate, unknown,
excluded, skipped or omitted keys are fatal: exit 2, stderr, no stdout JSON and no
files. The library accepts a key sequence with
`notewrench.epub.core.plan(inputs, chapter_order=["1:102", "1:101"])`; it never reads an
order path. `chapter_order=None` preserves source-order planning.

The plan also retains `inputs`, `text_mode`, `skipped`, `excluded`, keyed `changes`,
`warnings`, `errors`, and any image/resource or archive evidence. Markdown entries
retain exact original text and its SHA-256. Archive article `output_index` stays null
because no EPUB exists; `plan_index` is the zero-based position in `chapter_order`, or
null for excluded/skipped articles. Hashes, original source mappings and upstream
issues are retained.

Missing bodies without `--skip-missing`, or zero usable chapters, produce exit **2**,
stderr diagnostics and `status: "failed"` JSON. In a plan, `included` and
`chapter_order` still describe usable candidates even on that failure; they do not
claim a completed book. Fatal input/schema/resource/unknown-ID/XML or template bound
errors produce exit 2 with stderr and no stdout JSON. When missing-body/no-chapter
policy fails, a proposed sequence is not applied or inspected: the failure JSON retains
usable candidates and `plan_index` in source order. This policy takes precedence over
key/count/permutation errors. Order-file I/O, byte bound, UTF-8, JSON and
top-level-array errors remain fatal before loading inputs. Plans contain source text
and potentially private provenance, just like build reports. Unlike build's
`--dry-run`, planning needs no destination and performs no EPUB construction.

## Explicit chapter order

To arrange a selected book, supply **all** its usable qualified keys in the requested
order. This changes chapter serialization, EPUB spine/table of contents and
`report.included`, while input numbers, native `entry-N` positions and source IDs
remain fixed. The order is applied after tag/ID filters, explicit exclusions and
missing-body policy; it never adds chapters or changes selection.

```sh
# This example deliberately omits the native link that has no cached body.
printf '%s\n' '["2:entry-1", "1:102", "1:101"]' > order.json
notewrench epub build examples/epub/wallabag.json examples/epub/karakeep-export.json \
  --skip-missing --chapter-order order.json -o ordered.epub
notewrench epub validate ordered.epub
```

The order file is read once, with a **1 MiB (1,048,576 byte)** bound. It must be strict
UTF-8 (no BOM) and exactly one JSON array with at most **10,000** entries; nonstandard
`NaN`/`Infinity`, trailing text and malformed JSON are rejected. Each entry must be an
exact, nonempty qualified string `INPUT_NUMBER:ID`: a positive ASCII decimal input
number without leading zeroes and a nonempty source ID. Unicode IDs, colons and spaces
in source IDs are preserved exactly. There is no trimming, case folding or Unicode
normalization. Duplicate keys are forbidden.

The array must be a complete permutation of usable selected articles. Unknown,
filtered, missing-body, empty-after-cleaning and omitted keys fail with exit 2; there
is no partial-order or append-the-rest behavior. Order errors publish neither EPUB nor
report. File I/O/encoding/JSON/top-level-array errors are checked before loading
articles. Key/count/permutation checks follow full input/resource validation and
selection. Missing-body/no-chapter failures take precedence over those checks and
retain their failed report/no EPUB behavior; `--skip-missing` permits ordering just the
remaining usable articles.

On success `report.chapter_order` records the supplied key array. It is also recorded
in a missing-body failure report when its key sequence is valid, even if it is not a
permutation. Keyed changes, resource declarations, original text/hashes and archive
evidence retain source order/identities. Archive `articles` stay in archive order and
their zero-based `output_index` values reflect the new book order (or `null` for
omitted/unbuilt articles). The built-in validator checks the resulting spine/nav and
local resource links. The order file is protected as an input when checking
EPUB/report output aliases, including in `--dry-run`, which prints the ordered report
and writes nothing.

Library callers can use
`notewrench.epub.core.prepare(inputs, chapter_order=["1:102", "1:101"])`.
`chapter_order=None` preserves the report shape and source-order EPUB bytes. A supplied
list, tuple or other `collections.abc.Sequence` uses the same 10,000-entry/key/
permutation validation; strings, bytes, paths, sets, mappings and generators are
rejected. The library never reads an order file. Order errors raise `BundleError`;
missing-body failures return `(None, failed_report)`.

## XHTML, resources and validation

The cleaner retains headings, paragraphs, lists, code/pre, emphasis, blockquotes,
ordinary tables and safe links. It escapes text as XML and preserves Unicode. It
normalizes common missing end tags using the standard library `HTMLParser`, not a full
HTML5 DOM algorithm; original layout and every malformed-HTML interpretation are not
promised (and can differ slightly between Python patch releases). Unknown wrappers are
unwrapped and reported. Only a small attribute allowlist survives. Removed
attributes/elements/unsafe links and broken fragments appear in `changes`;
browser-style entity and end-tag normalization is implicit.

Scripts, event handlers, CSS, frames, forms, embedded documents, SVG/MathML and
audio/video content are removed and reported. By default, image occurrences become a
visible `[Image omitted: ...]` placeholder and a `missing_resource` event with the
original src/srcset/alt, including relative, local and data URLs. No stylesheet is
loaded. Images are embedded only when declared in the optional local manifest below.
Relative article links resolve against a safe HTTP(S) source URL; HTTP(S), mailto and
valid local fragments survive. Other URIs become inactive. Invalid XML characters,
excessive nesting, processing instructions and declarations other than simple
`<!DOCTYPE html>` are rejected. Sources are retained as text, with clickable links
only for safe HTTP(S) URLs.

The EPUB has uncompressed first-entry `mimetype`, OCF container.xml, OPF 3.0
metadata/manifest/spine, EPUB toc nav, and individually serialized XHTML chapters.
Every build runs the independent validator before writing output. `validate` checks
CRCs, safe ZIP names, duplicates, size bounds, mimetype/local header, container
rootfile, metadata, manifest membership, spine and nav order, XML, local
links/fragment targets and forbidden active constructs. It accepts this command's
**static XHTML/PNG/JPEG profile** (reported as `profile: "readbundle-static-xhtml"`),
not every third-party EPUB (CSS, other image types, font, multiple-rootfile,
nonlinear and other EPUB features are outside that validator profile). DTD/entity
declarations are refused and archives are never extracted. This is structural
validation, **not EPUBCheck**, a complete EPUB conformance certification, or a tested
reader/device compatibility claim.

### Explicit local PNG/JPEG manifest

```sh
notewrench epub build examples/epub/wallabag-images.json \
  --image-manifest examples/epub/image-manifest.json -o illustrated.epub
notewrench epub validate illustrated.epub
```

This example has two synthetic articles sharing one original 2×2 PNG. The EPUB
contains seven members: mimetype, container, OPF, nav, two XHTML chapters and **one**
PNG. Both chapters reference the same `images/<sha256>.png`, and OPF declares that
image exactly once. A minimal manifest for one article is:

```json
{
  "schema_version": 1,
  "images": [{
    "key": "1:1",
    "src": "/shared.png",
    "path": "image-resources/shared.png",
    "media_type": "image/png",
    "sha256": "fb5e4dc04449231dc2ef8be56ab26c7c1fd46a8476bded538d5d67c806a4ff23"
  }]
}
```

`key` is the qualified article key used by `--id`. `src` matches that article's HTML
`img src` **exactly after HTML character-reference decoding**; it is not resolved
against the article URL. Relative or HTTP(S) source references are labels only and are
never fetched. Declare each article/reference separately; the same bytes, even at
different file paths, are archived once by SHA-256. Only declared occurrences in
selected HTML bodies are embedded. Literal and Markdown text keep their behavior,
including literal Markdown images. `srcset` is never loaded; a declared `src` can be
embedded while its discarded `srcset` is recorded as a `missing_resource` event. Other
image attributes are removed and reported; `alt` is retained, or supplied as an empty
string.

`path` is a relative local file below the manifest's resolved directory. Absolute
paths, `.`/`..` components, backslashes, symlinks (including directory symlinks), FIFOs
and other nonregular files are rejected. Asset reads use anchored POSIX file
descriptors with no symlink following (POSIX). The tool checks SHA-256 over
exact file bytes and requires `image/png` or `image/jpeg` plus matching image
structure, independent of filename suffix. Cached/repeated declarations must also
agree on media type. All declarations are checked, including ones for excluded or
unused images; unknown article keys, duplicate key/src pairs, extra/missing fields,
duplicate JSON properties, bad hashes and unsupported media types fail explicitly.

The supported PNG subset follows the pinned
[PNG Second Edition (2003-11-10)](https://www.w3.org/TR/2003/REC-PNG-20031110/):
non-interlaced grayscale, truecolor, indexed and alpha images at legal depths. Checks
include signature, chunk bounds/order, CRCs, palette presence, exact bounded zlib
scanline size, filter types and IEND with no trailing bytes. Animation and unknown
critical chunks are rejected. Ancillary chunks are CRC-checked and retained, without
interpretation; this is not full PNG conformance certification or a pixel-rendering
check.

JPEG uses the same schema and path/hash rules:

```sh
notewrench epub build examples/epub/wallabag-jpeg.json \
  --image-manifest examples/epub/jpeg-manifest.json -o photos.epub
notewrench epub build examples/epub/wallabag-jpeg-restart.json \
  --image-manifest examples/epub/jpeg-restart-manifest.json -o restart.epub
notewrench epub validate photos.epub
```

The first example has two articles sharing one original synthetic 8×8 gray JPEG
(declare `media_type: "image/jpeg"`; both chapters reference one
`images/<sha256>.jpg` and one OPF `image/jpeg` item). The second is a 16×8 gray JPEG
with two one-block entropy intervals, DRI interval 1 and one RST0 separator. PNG and
JPEG declarations can coexist.

The JPEG structural profile uses
[ITU-T T.81 (09/1992), Annexes B/C/F](https://www.w3.org/Graphics/JPEG/itu-t81.pdf):
8-bit baseline Huffman (SOF0), one or three components, one sequential scan containing
every component in SOF0 frame insertion order (T.81 B.2.3). Identifiers need not be
numerically sorted: frame IDs `9,2,7` require SOS IDs `9,2,7`; reversed or other
permutations reject, with or without DRI/RST. Checks cover marker order, exact segment
bounds, nonzero 8-bit quantizers, Huffman table shape/symbols, component/table
references, legal sampling factors (at most ten blocks per MCU), nonzero dimensions,
entropy-byte stuffing and final EOI without trailing bytes. One optional DRI is allowed
after SOI and before SOS, among the header tables/metadata, before or after SOF0. Its
segment length must be exactly 4 and its two-byte interval is 0–65535; zero disables
restarts. RST markers require a positive active interval, start at RST0 and cycle
through RST7 back to RST0. Every entropy interval, including the last, must contain
data. Orphan, repeated, out-of-order or truncated restart markers, empty intervals,
duplicate/redefined DRI, DRI inside/after the scan, extra scans and trailing bytes are
rejected. Marker fill is accepted; stuffed FF bytes remain entropy data. Progressive,
extended, arithmetic, lossless, four-component, multi-scan and deferred-height JPEG are
rejected explicitly. APPn/COM metadata is bounded but retained verbatim without
interpretation or stripping; it may contain private EXIF/location data. Entropy
coefficients and pixels are **not decoded**: actual MCU counts and spacing between
restart markers cannot be certified. Structurally accepted bytes can still have
corrupt coded content. Filename extensions never establish media type.

Limits: manifest ≤1 MiB/1,000 declarations; each image ≤8 MiB; distinct image bytes
≤32 MiB, including unused declarations; decoded scanlines ≤16 MiB/image. PNG
dimensions cannot bypass the decoded-size bound. JPEG dimensions must satisfy
`width × height × component_count ≤16 MiB` (8-bit nominal samples); the tool does not
allocate decoded JPEG pixels. Whole-book ZIP limits still apply. Invalid
declarations/files exit 2 before either output is published (no failure report is
promised for these fatal errors). Undeclared image occurrences instead retain the
visible omission placeholder and `missing_resource` record; `--skip-missing` does not
permit invalid assets.

With a manifest, the report adds `image_manifest`, deduplicated `resources` (EPUB
href, hash, media type, byte count and used declarations), `unused_images`, and
per-occurrence `embedded_resource` changes. On a missing-body failure these describe
the planned resources; no EPUB is created. The validator checks actual PNG/JPEG media
bytes, local `img src` targets, OPF/ZIP membership, use of every image, and separation
of images from the chapter spine. Builds without a manifest retain the image-omission
behavior, report shape and EPUB bytes.

## Determinism and limits

Defaults fix `dcterms:modified` at `2000-01-01T00:00:00Z`. Set `--modified` to a valid
UTC `YYYY-MM-DDTHH:MM:SSZ` for meaningful metadata. Same ordered content and metadata
on the same Python/zlib toolchain produce identical EPUB bytes: stable content-derived
identifier, member order, ZIP dates/permissions and compression. Cross-zlib byte
identity is not promised. Source paths in the external report do not affect EPUB
identity. Reports and books contain article/source data; store and share them
accordingly.

Limits: each JSON input ≤32 MiB/10,000 entries; HTML nesting <128; generated and
validated ZIP members ≤16 MiB each and ≤64 MiB total uncompressed. The whole book is
held in memory. No DRM, page downloading, cover, automatic image discovery,
GIF/WebP/SVG embedding, CSS fidelity, PDF support or running application integration
is provided.

## Related tools

EPUB generation already exists in this area. Karakeep's
[multi-bookmark EPUB request](https://github.com/karakeep-app/karakeep/issues/295)
itself mentions Readeck; Wallabag has EPUB export and an
[export improvement PR](https://github.com/wallabag/wallabag/pull/7958).
[linkpub_dev](https://github.com/RussPalms/linkpub_dev) is a related existing tool.
This command's particular scope is **offline local cached-body inputs, deterministic
packaging and explicit provenance/omission reports**. The implementation is original
code; examples are synthetic. See [THIRD_PARTY.md](../THIRD_PARTY.md).
