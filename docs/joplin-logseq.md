# notewrench joplin-logseq

Move a Joplin notebook collection into a **Logseq Markdown file graph** without
losing note identities, links or attachments. Use it when you have a Joplin
**JEX** export and want a fresh Logseq graph plus an audit/loss report that shows
exactly what was rewritten, what could not be mapped and which links are broken.

`notewrench joplin-logseq` reads original item identities before converting links,
namespaces notebooks, copies attachment bytes unchanged, maps tags and tasks, and
writes `report.json`. It works entirely offline: it does not access a running
Joplin installation or a Logseq database, and needs no API key or network.

Formerly the standalone `jex2logseq` tool.

**Platforms:** Linux and macOS; Windows is not supported. Only ordinary path
operations are used, so symlinked parent directories (such as macOS `/tmp`) are
fine; the output path itself must not exist, not even as a dangling symlink.

**Reads / writes:** reads one uncompressed `.jex` tar (never extracted to disk and
never modified) and creates one new graph directory (`pages/`, `assets/`,
`report.json`), staged in a temporary sibling first. `--dry-run` creates no files.

## Quick start

```sh
pip install notewrench
notewrench joplin-logseq --help
```

From a clone of the repository, convert the bundled synthetic archive:

```sh
notewrench joplin-logseq examples/joplin-logseq/synthetic.jex my-new-logseq-graph
```

The output path **must not exist**, even as an empty directory or dangling symlink.
Its parent must already exist. Choose a fresh path each time; there is no overwrite
or in-place merge option. Open the resulting directory as a Markdown file graph
in Logseq's file-based edition (now maintained as Logseq OG). This is not a DB
version importer, and app-level rendering in Logseq has not been tested.

For an actual Joplin migration, export selected notes or all notes using Joplin's
**JEX – Joplin Export File** option. Keep the original export. Then run:

```sh
notewrench joplin-logseq backup.jex new-graph --dry-run > migration-preview.json
notewrench joplin-logseq backup.jex new-graph
```

`--dry-run` performs the same input, mapping and output-conflict checks and prints
the complete report as JSON, without creating any files or executing scripts.
Normal success prints a JSON count summary. Exit codes: **0** = conversion or
validation succeeded (review any issues); **1** = an opted-in dry-run rejected
broken links (see below); **2** = invalid input, conflict or I/O error, with a
diagnostic on stderr. Fatal validation errors produce no output
directory; an error diagnostic identifies the rejected item/type where available.

The same conversion is available from Python:

```python
from notewrench.joplinlogseq import convert

report = convert("backup.jex", "new-graph", dry_run=True)
```

## Gate a migration preview on broken links

To make a complete preview fail when its report contains any `broken_link` issue:

```sh
notewrench joplin-logseq backup.jex new-graph --dry-run --fail-on-broken-links > migration-preview.json
```

`--fail-on-broken-links` requires `--dry-run`; using it without `--dry-run` exits
**2** before reading the archive or creating files. A valid preview prints the
same complete JSON bytes as ordinary `--dry-run`, including all source contents,
issues, source line numbers and counters. It then exits **1** for one or more
existing `broken_link` issues, or **0** for none. Both outcomes create no graph
directory or staging files. Invalid archives and destination conflicts still
exit **2** with no report.

The gate uses the report's existing classifications. Missing note or resource
targets fail; a declared resource whose payload is missing is invalid input
(exit 2). References to notebook/tag pages fail when their page type is omitted;
`--include-notebook-pages`, `--include-tag-pages` or the combined shortcut can
resolve them. Protected code and recognized link labels/titles are literal data
and add no broken links. HTML references retain their existing diagnostics:
an unknown target has `broken_link` and fails, while a known target's
`unsupported_link_context` alone passes. Other warnings such as
`note_link_uses_file_path`, `missing_note_folder`, unclosed code fences and
unapplied metadata do not fail this gate. Omitting the flag keeps ordinary
dry-run/conversion behavior and its **0/2** exits.

## Keep notebook and tag pages

By default, only notes get page files. Select notebooks and tags independently,
including empty notebooks and unused tags, or use the existing shortcut for both:

```sh
notewrench joplin-logseq backup.jex notebooks-graph --include-notebook-pages
notewrench joplin-logseq backup.jex tags-graph --include-tag-pages
notewrench joplin-logseq backup.jex both-graph --include-notebook-pages --include-tag-pages
notewrench joplin-logseq backup.jex organized-graph --include-organization-pages
notewrench joplin-logseq backup.jex preview-graph --include-organization-pages --dry-run
```

Flags combine by union: `--include-organization-pages` always selects both types,
even when combined with an independent flag. The Python `convert` API accepts
the corresponding keyword arguments `include_notebook_pages=True`,
`include_tag_pages=True` and `include_organization_pages=True` with the same rules.

These pages have `joplin-id` and `joplin-type` (`notebook` or `tag`) properties.
Notebook pages link to their parent, direct child notebooks and direct notes;
tag pages link to their assigned notes. Notes link to their notebook through
`joplin-notebook` when notebook pages are selected. The `tags` property uses
allocated tag names in every mode, including when tag page files are omitted.
Note-body internal URIs resolve only to emitted pages; targets of an unselected
type stay unchanged and are reported as broken links. For example, with notebook
`Archive` (ID `aaa…aaa`) and tag `Used` (`555…555`), `[book](:/aaa…aaa)` becomes
`[book]([[Archive]])` only when notebooks are selected, and `[tag](:/555…555)`
becomes `[tag]([[Used]])` only when tags are selected. Reference definitions use
relative file paths under the same selection rule. The legacy
`rewrites.note_links` counter counts these page-link rewrites too. Source link
counts exclude generated membership links.

Notes, notebooks and tags share one name allocator in **every mode**. It reserves
names even for notebook/tag pages that are not written, preventing a tag from
silently referring to a same-named note and keeping names stable when toggling
the flags. For example, synthetic JEX records with note `Café` (ID `111…111`),
notebook `CAFÉ` (`aaa…aaa`) and tag `Café` (`555…555`, decomposed Unicode) become
`Café--<full-note-id>`, `CAFÉ--<full-folder-id>` and `Café--<full-tag-id>`.
The note's `tags` property uses the allocated tag name. All colliding entities
receive suffixes, regardless of tar order. Windows-reserved and encoded filename
collisions use the same allocator. The integration fixtures in
`tests/joplinlogseq/test_organization.py` cover these cases and suffix-looking source titles.

Notebook `namespace` prefixes remain as originally normalized, separately from
their allocated page names. If a notebook page gets a collision suffix, its
descendants retain their namespace prefixes; the explicit parent/notebook links
and report IDs preserve the original hierarchy. Original titles are retained in
`source_items` and the organization maps. No extra page files are written in
default mode. The report uses `path: null` for notebook/tag files not emitted;
their names, hierarchy and memberships remain in the maps. `output_pages` counts
actual page files by entity type. In `options`, `include_organization_pages`
records the requested shortcut; `include_notebook_pages` and `include_tag_pages`
record the effective selection, including selection by the shortcut. Overlong
**emitted** filenames, invalid hierarchy, dangling tag relations and output
conflicts fail with exit 2 before output is created, in all modes. An overlong
unemitted notebook filename does not prevent tag-only output; malformed source
hierarchy is always rejected even if notebook pages are omitted.

## Bundled example

`examples/joplin-logseq/synthetic.jex` is a real, deterministic tar, generated
from fictitious notes. It has three notes, two nested notebooks, a tag relation,
and a valid original 1×1 PNG. To regenerate it (the test suite checks that the
generator and the bundled bytes agree):

```sh
python3 examples/joplin-logseq/make_synthetic.py regenerated.jex
cmp regenerated.jex examples/joplin-logseq/synthetic.jex
```

The bundled sample produces:

```text
pages/Notebook___Plan--11111111111111111111111111111111.md
pages/Notebook___Plan--22222222222222222222222222222222.md
pages/Notebook___Projects___Release.md
assets/44444444444444444444444444444444.png
report.json
```

Its note link `[other plan](:/22222222222222222222222222222222)` becomes
`[other plan]([[Notebook/Plan--22222222222222222222222222222222]])`.
The image points to `../assets/44444444444444444444444444444444.png`.
The deliberate missing note link stays unchanged and is listed in `issues`.

## Conversion rules and report

1. Read the tar without extracting archive paths. Parse every terminal metadata
   record and index notes (1), folders (2), resources (4), tags (5), note_tags (6).
2. Validate parent relationships, detect folder cycles, resolve resource bytes,
   and allocate all page names before rewriting any note. Ordering in the tar
   does not affect names or report content.
3. Write page properties (`title`, `joplin-id`, `tags`) and a multiline outline
   block headed by the title. Notebook hierarchy is a namespace: `Notebook/Title`
   has filename `Notebook___Title.md`. Name components retain Unicode letters,
   numbers, spaces, hyphens, underscores and dots; other characters become `_`,
   triple underscores are reduced, and components are capped at 60 characters.
   All collisions, including case differences and encoded filename collisions,
   receive stable full item-ID suffixes. Original titles are retained in the
   report. Overlong final filenames (>240 UTF-8 bytes) are rejected explicitly.
4. Rewrite ordinary inline Markdown links/images and standalone `:/<32-hex-id>`
   references. Note links use labeled Logseq page references; resource links use
   relative asset paths. Note images, fragments and links with a supported tooltip
   use a file link instead and are reported (`note_link_uses_file_path`).
   Reference definitions such as `[target]: :/<id>` also accept the destination
   on the immediately next line and a one-line title on the destination line or
   immediately next line. They map to relative page/asset paths, preserving
   whitespace, line breaks, labels, optional angle brackets, fragments and
   titles. Their full, collapsed, shortcut and image reference uses stay
   unchanged. Note definitions use the reported file-link fallback above; each
   converted definition counts once, regardless of how many uses it has. Issues
   identify the URI's 1-based body line, including next-line destinations.
   HTML tags/attributes and comments remain unchanged and their internal URIs
   get `unsupported_link_context` issues, including Markdown-looking links.
   Missing targets remain unchanged and get `broken_link` issues with note ID
   and 1-based body line number (also in unsupported contexts).
5. `is_todo: 1` makes a `TODO` title block, or `DONE` if `todo_completed > 0`.
   Unordered Markdown checkboxes become `TODO` / `DONE` blocks. Fenced code using
   backticks or tildes is left unchanged, including task syntax and internal
   links (including simple quoted/list fence containers); only the two-space indentation of the containing block is added.
   Indented code lines and paired inline backtick spans, including spans across
   lines within one paragraph, are also protected. Escaped opening brackets in
   literal link examples (`\[example](:/<id>)`) are left unchanged. Code/literal
   examples do not count as link rewrites or broken links.

**Task completion:** Joplin stores completion in `todo_completed`, a
millisecond timestamp, together with `is_todo`; there is no `is_completed` field.
Those real fields are mapped; a nonstandard `is_completed` field, if present, is
retained as unapplied metadata, never guessed into a new format.
See the [Joplin property reference](https://joplinapp.org/help/api/references/rest_api/#properties).

`report.json` includes:

- `counts`, `rewrites`: actual item and conversion counters.
- `pages`, `folders`, `tags`: original ID → final destination/name mappings.
  Folder/tag maps include `page`, `path` (null unless emitted), `source_title`
  and `note_ids`; folders also include `namespace`, `parent_id` and `child_ids`.
- `options`, `output_pages`: selected output mode and actual page-file counts.
- `assets`: paths, byte counts and SHA-256 checksums, including unreferenced assets.
- `issues`: broken links, unsupported link contexts, missing note folders,
  file-link fallbacks and unclosed code fences.
- `source_items`: all serialized metadata values, source titles and complete
  normalized source bodies. **The report therefore contains the note contents.**
- `metadata_not_applied`: fields not mapped into Logseq, including unknown fields,
  timestamps, geolocation, ordering, application data and due/alarm data.
- `limitations`: conversion boundaries, present on every successful report.

Metadata is retained in its serialized string form, including Joplin's escaped
newline sequences. Input CRLF framing and bodies normalize to LF. Resource bytes
are never normalized. Missing parent folders for notes are valid in selected-note
exports: those notes use `Unfiled/Title` and get an issue. A missing parent of a
folder is fatal. Empty notebooks and unused tags remain in the report in every
mode; selecting their type also gives them explicit page files.

## Safety and limits

Only uncompressed JEX tar with `<id>.md` records and
`resources/<id>[.<extension>]` payloads is accepted; IDs are 32 hexadecimal
characters and must match their item filenames. An optional `./` prefix and a
`resources/` directory entry are supported. Resource filename extension must
agree with explicit metadata. If the extension metadata is absent, the sole
matching archive resource supplies its extension (including extensionless
resources); there is no platform-dependent MIME guessing.

Absolute paths, traversal, backslashes, duplicate members/identities, symlinks,
hardlinks, devices, FIFOs, sparse members, missing tar EOF padding or trailing nonzero data, malformed metadata, unknown member
layouts/types, encryption/locked notes, missing/ambiguous resources, orphan
resource files and dangling tag relations are rejected. Archive mode bits are
not copied, and attachment content is never executed. Input must be a regular
file. Default safety bounds can be lowered or raised explicitly:

| Flag | Default |
| --- | ---: |
| `--max-archive-bytes` | 268435456 (256 MiB on disk) |
| `--max-member-bytes` | 33554432 (32 MiB per logical file) |
| `--max-total-bytes` | 134217728 (128 MiB total logical file bytes) |
| `--max-members` | 10000 (logical members including directories) |

Notebook depth is capped at 32. The converter retains payloads and generated
output/report in memory; peak memory can exceed the input limits by several
times. These are input bounds, not an OS memory sandbox. No large-scale
performance or speed comparison is claimed.

All content is staged in a temporary sibling directory before claiming the new
output directory using exclusive `mkdir`. Normal write failures clean up staged
and newly created output. Publication moves three top-level entries; it is not a
single atomic directory swap, so process termination/power loss during this short
phase may leave partial output. Do not concurrently modify the input archive,
output path or its parent. A second writer creating the output before it is
claimed is refused without touching that writer's files.

## Boundaries

This is a file-format migration tool, not a full Markdown/CommonMark parser.
No SQLite access, E2EE decryption, DB graph writes, sync or graph merging. HTML
notes (`markup_language: 2`), revision items, modern note-lock key files and
unrecognized future formats are rejected. Common Markdown body formatting is
retained inside outline blocks, but layout, HTML, plugins, tables and Joplin-only
semantics may render differently. Link syntax boundaries are explicit:

- Inline links/images support a flat label, internal ID, optional fragment and
  optional one-line double-quoted, single-quoted or parenthesized title. An odd backslash run before `!` leaves that marker
  literal; the following unescaped `[label](destination)` is a normal link.
  For example, `\![target]( 	:/22222222222222222222222222222222 	)` becomes
  `\![target]( 	[[Target]] 	)` with its prefix and padding preserved and no
  file-path issue. Even backslash runs retain image behavior and a note's
  reported file-path fallback. Escaped opening brackets remain literal examples.
  A destination may be plain or wrapped
  in paired `<…>` brackets. An unmatched opener or stray closer is malformed:
  `[target](<:/22222222222222222222222222222222)` and
  `[target](:/22222222222222222222222222222222>)` stay unchanged, with
  `unsupported_link_context` at the URI line, plus `broken_link` for a missing
  destination. Malformed links use ordinary source-context rules; code remains
  protected and HTML keeps its context diagnostics. An odd run of backslashes escapes a
  title quote; an even run leaves it structural. Recognized labels and titles
  are data, including internal URIs, backticks and HTML-looking text. Their bytes
  and whitespace stay unchanged; only the destination is rewritten once.
  In inline URI fragments, an odd backslash run escapes `)`; an even run leaves
  it structural. Fragment escapes are retained verbatim, through the actual link
  closer and any supported title. For example,
  `[target]( 	:/22222222222222222222222222222222#part\)tail "tooltip :/ffffffffffffffffffffffffffffffff" 	)`
  becomes `[target]( 	../pages/Target.md#part\)tail "tooltip :/ffffffffffffffffffffffffffffffff" 	)`.
  Only the destination gets a file-path issue; the title's missing URI stays data.
  This also applies to images, resources and paired angle-wrapped destinations.
  Single-quoted titles follow the same rules and require a space/tab separator
  after the destination. For example,
  `[target]( \t:/22222222222222222222222222222222#part\)tail 'tooltip :/22222222222222222222222222222222 :/ffffffffffffffffffffffffffffffff ' \t)`
  becomes `[target]( \t../pages/Target.md#part\)tail 'tooltip :/22222222222222222222222222222222 :/ffffffffffffffffffffffffffffffff ' \t)`.
  Here `\t` denotes a literal tab. The title's URIs stay literal, so
  `--dry-run --fail-on-broken-links` exits **0**; a missing destination exits **1**.
  Odd backslash runs escape a matching single quote; even runs leave it
  structural. Empty titles, all escape bytes and surrounding spaces/tabs are
  preserved. Double quotes inside single-quoted titles are literal data too.
  Parenthesized titles use the same separator, one-line and byte-preservation
  rules. For example,
  `[target]( \t:/22222222222222222222222222222222#part\)tail (tooltip :/22222222222222222222222222222222 :/ffffffffffffffffffffffffffffffff ) \t)`
  becomes `[target]( \t../pages/Target.md#part\)tail (tooltip :/22222222222222222222222222222222 :/ffffffffffffffffffffffffffffffff ) \t)`.
  Only the destination counts and gets a file-path issue, so this preview passes
  the broken-link gate. Odd backslash runs escape either title parenthesis;
  even runs leave it structural. Empty titles and escaped nested parentheses
  such as `(a \(nested\) tooltip)` are supported; unescaped nested opening
  parentheses are rejected. Quotes, backticks, HTML-looking text and internal
  URIs within a recognized title are literal data. Attached titles (after a
  destination ID or closing angle), unterminated/multiline titles and trailing
  junk are malformed; following valid links still use ordinary source-context
  rules. Existing fragment recognition is unchanged: an attached `(` within a
  fragment is URI content, not the start of a title.
  Bare URIs retain their existing fragment rules. Unescaped nested parentheses
  and additional inline title forms remain outside this grammar.
  Spaces/tabs after the opening `(` are accepted before either a plain or
  angle-wrapped destination and preserved byte-for-byte. For example,
  `[target](  :/22222222222222222222222222222222 "tooltip"  )` becomes
  `[target](  ../pages/Target.md "tooltip"  )`; tabs are retained too.
  Invalid destinations/titles cannot be rescued as bare URIs by this padding:
  `[bad](  :/22222222222222222222222222222222 "ok" junk)` stays unchanged
  with `unsupported_link_context` (plus `broken_link` for an unknown target).
  Spaces/tabs before the closing `)` are accepted after either the destination
  or its supported title and preserved byte-for-byte. For example,
  `[target](:/22222222222222222222222222222222 "tooltip"  )` becomes
  `[target](../pages/Target.md "tooltip"  )` (the two closing spaces remain).
  Padding alone does not require a file path: `[target](:/22222222222222222222222222222222  )`
  becomes `[target]([[Target]]  )` when the note's page name is `Target`.
  For example, `[target](:/22222222222222222222222222222222 "a \"quoted\" :/ffffffffffffffffffffffffffffffff")`
  becomes `[target](../pages/Target.md "a \"quoted\" :/ffffffffffffffffffffffffffffffff")`
  when that note maps to `pages/Target.md`. Only the destination gets
  `note_link_uses_file_path`, on its original body line; the literal missing URI
  in the title gets no issues. A missing destination remains unchanged with one
  `broken_link`. Attached or unterminated titles, trailing junk before the closing link
  parenthesis and multiline titles remain
  outside this inline grammar and use ordinary source-context rules.
  Bare internal URIs must stand alone.
- Reference definitions start with up to three spaces and a nonempty
  flat/escaped label. The internal URI (optionally in angle brackets) may be on
  that line or the immediately next line. An optional double-quoted,
  single-quoted or parenthesized title must fit on one physical line, on the
  destination line or immediately next line. A same-line title requires at least
  one space or tab after the destination (after `>` for an angle-wrapped URI).
  For example, `[target]: :/22222222222222222222222222222222"tooltip"`
  stays unchanged with `unsupported_link_context` at the URI line; a missing
  target also gets `broken_link`. Adding a space before `"tooltip"` permits
  conversion. Reference URI fragments accept `)` after an odd backslash run,
  preserving every escape byte; an even run leaves it a delimiter and cannot
  make that destination valid. For example,
  `[target]: 	:/22222222222222222222222222222222#part\)tail "tooltip :/ffffffffffffffffffffffffffffffff"`
  becomes `[target]: 	../pages/Target.md#part\)tail "tooltip :/ffffffffffffffffffffffffffffffff"`.
  This applies to plain and paired angle-wrapped destinations, on the label line
  or immediately next line. The complete definition is recognized before its
  label/title contents are scanned: only its destination counts once and gets
  diagnostics at its original URI line. The title's missing URI stays literal
  data, so `--dry-run --fail-on-broken-links` exits **0** for this example;
  a genuinely missing destination still exits **1**.
  A next-line title needs no indentation. Continuation indentation can use
  spaces or tabs, including four or more spaces; a blank line never joins a
  definition. Within titles, an odd run of backslashes escapes the matching
  quote or parenthesis; an even run leaves the delimiter structural. Escaped
  delimiters and backslashes are retained verbatim. Labels and recognized titles are data, so internal URIs within
  them stay unchanged without link issues. Uses are retained for the Markdown
  renderer; undefined labels and duplicate-definition precedence are not
  resolved by this tool. Malformed destination lines remain unchanged with
  unsupported URI issues (and broken-link issues for missing targets).
  Unrecognized following lines use ordinary source-context rules. Unterminated
  titles and trailing junk on the destination line leave that definition
  unchanged with URI issues; on the next line they are ordinary source content.
  Titles whose contents span lines, unescaped nested parentheses and container
  definition forms are not parsed.
- HTML tag spans honor single/double quotes (including `>` in attribute values)
  and may cross lines. Comments extend through `-->`; unterminated tags/comments
  conservatively protect the remainder of the body. Element text is still prose;
  raw HTML block semantics, scripts and styles are not parsed.
- Fences support backticks/tildes and simple quoted/list containers. All lines
  with four leading spaces, or a tab reaching column four, are conservatively
  protected, except recognized reference-definition continuations, even where
  full container parsing might treat them as prose.
  Inline code requires equal-length backtick runs and stops at blank lines or
  recognized block boundaries. Unmatched/escaped opening backticks remain prose.
  Nested containers are not a complete CommonMark implementation.

For example, with internal ID `22222222222222222222222222222222` mapped to
`pages/Target.md`:

```markdown
[see][target]
[target]:
    :/22222222222222222222222222222222
  "a \"quoted\" :/ffffffffffffffffffffffffffffffff"
<a title="[literal](:/22222222222222222222222222222222)">HTML</a>
```

becomes (before the containing Logseq block's two-space indentation):

```markdown
[see][target]
[target]:
    ../pages/Target.md
  "a \"quoted\" :/ffffffffffffffffffffffffffffffff"
<a title="[literal](:/22222222222222222222222222222222)">HTML</a>
```

The escaped title (including its literal missing URI) is preserved without link
issues. The definition gets `note_link_uses_file_path` at destination line 3; the HTML
reference gets `unsupported_link_context`. An unknown ID stays verbatim with
`broken_link`; conversion still exits 0 so the rest of the graph can be reviewed. Review
`source_items` for an unmodified logical body when assessing conversion loss.
There is no Logseq UI compatibility claim.

## Related projects and format sources

[dasrecht/joplin-to-logseq-importer](https://github.com/dasrecht/joplin-to-logseq-importer)
and [htinaunglu/Joplin-to-Logseq-Integrater](https://github.com/htinaunglu/Joplin-to-Logseq-Integrater)
are existing related projects. The former documents a Markdown + Front Matter
workflow. This implementation instead reads JEX identity records, establishes
complete mappings before rendering, preserves resource hashes and produces an
explicit audit/loss report. Overlap is acknowledged; there is no claim of novelty
or measured superiority. No source from those projects was reused.

Format sources (checked 2026-11-11):
[JEX exporter](https://github.com/laurent22/joplin/blob/dev/packages/lib/services/interop/InteropService_Exporter_Jex.ts),
[raw exporter](https://github.com/laurent22/joplin/blob/dev/packages/lib/services/interop/InteropService_Exporter_Raw.ts),
[item serialization](https://github.com/laurent22/joplin/blob/dev/packages/lib/models/BaseItem.ts),
[resource naming](https://github.com/laurent22/joplin/blob/dev/packages/lib/models/utils/resourceUtils.ts),
[Joplin property reference](https://joplinapp.org/help/api/references/rest_api/#properties),
[Logseq properties](https://github.com/logseq/docs/blob/master/pages/Properties.md),
[Logseq built-in properties](https://github.com/logseq/docs/blob/master/pages/Built-in%20Properties.md),
[Logseq tasks](https://github.com/logseq/docs/blob/master/pages/Tasks.md).

Joplin and Logseq are independent upstream projects; notewrench is not affiliated
with either. Attribution details are in [THIRD_PARTY.md](../THIRD_PARTY.md).

Reports keep the original identifier `"tool": "jex2logseq"` so existing report
consumers continue to work.
