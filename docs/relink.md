# `notewrench relink`

Links in old notes die, but you often already have a private copy of the page: a
SingleFile or browser-saved HTML file, an ArchiveBox snapshot, an nb bookmark, a WARC
or an MHTML file. Use `notewrench relink` when you want to point Markdown citations at
those **existing local copies**: it indexes your archives, lists the candidate copies
for each citation with the evidence behind them, and writes a patch only for the copies
you explicitly select.

Formerly the standalone `archive-relink` tool. Everything runs locally: it never fetches
URLs, never decides whether a live page is broken, and never changes notes by itself.

**Install:** `relink` needs the optional `relink` dependencies (Beautiful Soup,
idna, markdown-it-py, mdit-py-plugins, warcio) and SQLite with the FTS5 `trigram`
tokenizer (SQLite 3.34+, included in current Python builds):

```sh
pip install 'notewrench[relink]'
notewrench relink doctor
```

`notewrench relink doctor` is the environment check for this command only: it prints the
Python and SQLite versions, confirms the FTS5 trigram tokenizer and lists the installed
parser packages as JSON. It is not [`notewrench doctor`](doctor.md), the read-only health
check across your libraries.

**Reads / writes:** `index` reads an archive folder and writes a disposable SQLite index
(plus `INDEX.sqlite.assets/` for HTML extracted from WARC files); `scan` reads notes and
the index and writes a JSON plan and an optional Markdown review; `patch` writes a unified
diff. Archive files and notes are never modified, and outputs may not overwrite any input.

## Quick start

From a clone of the repository, using the synthetic samples in
[`examples/relink/`](../examples/relink/):

```sh
mkdir -p relink-demo
cp examples/relink/note.md relink-demo/note.md
notewrench relink index examples/relink/archive --db relink-demo/archive.sqlite
notewrench relink scan relink-demo/note.md --db relink-demo/archive.sqlite \
  --out relink-demo/plan.json --review relink-demo/review.md
```

The note has three citations. The scan finds one copy by its recorded original URL (a
SingleFile page saved under an unrelated file name), one copy by a quotation in the note
(the page whose title merely resembles the citation is not offered), and nothing for the
third citation. The code sample at the end of the note is never treated as a citation.

Read `relink-demo/review.md` and the full evidence in `plan.json`. Choose a link ID and
one candidate ID from that plan, then write and apply the patch:

```sh
notewrench relink patch relink-demo/plan.json \
  --select LINK_ID=CANDIDATE_ID --base . --out relink-demo/repair.patch
git apply --check relink-demo/repair.patch
git apply relink-demo/repair.patch
```

Repeat `--select` for each accepted repair, or pass `--selections choices.json` with a
JSON object mapping link IDs to chosen candidate IDs. A `null` value leaves that
citation unselected. No candidate is preselected, not even a unique URL match.

`patch` checks that the note, the local target and the recorded evidence have not
changed since indexing and scanning. Patches change only destination spans and keep the
rest of the Markdown formatting. Autolinks become ordinary Markdown links so relative
local paths stay clickable. `--absolute` writes `file://` destinations; otherwise paths
are relative to each note. Patch paths are relative to `--base` (default: the current
directory), and every note must be inside it.

The original URL fragment is kept unless a recorded redirect explicitly overrides or
clears it, following [HTTP Location semantics](https://www.rfc-editor.org/rfc/rfc9110.html#name-location).
Whether that anchor exists in the saved page is not checked.

Exit status: 0 on success, 2 on invalid input or output (message on stderr, prefixed
`notewrench relink:`); argparse usage errors also exit 2.

## Commands

| Command | What it does |
| --- | --- |
| `index ARCHIVE --db INDEX` | Build (or atomically rebuild) a disposable index of an archive folder. Choose an index path outside the archive. |
| `scan NOTE... --db INDEX --out PLAN [--review REVIEW.md] [--quotes QUOTES.json] [--only-urls URLS.txt] [--limit N]` | Write candidates and evidence for every citation; never edits notes. `--limit` is the number of candidates per link (1-100, default 20). |
| `patch PLAN --out PATCH [--select LINK=CANDIDATE]... [--selections CHOICES.json] [--base DIR] [--absolute]` | Write a unified diff for the explicitly selected candidates only. |
| `doctor` | Check Python, SQLite FTS5 trigram support and the parser packages for `relink`. |

## Matching and review

1. Match a recorded original URL, using conservative URL normalization.
2. Follow recorded WARC redirect chains, at most eight hops. A page's canonical or
   `og:url` declarations are shown as **declared aliases**, not as recorded redirects.
3. Search exact normalized **body quotations** when there is no recorded URL match,
   only page-declared aliases exist, or a supplied quotation contradicts every copy with
   a recorded URL. Conflicting URL evidence stays visible for review. A quotation can be
   straight or curly quoted text in the citation paragraph or an adjacent Markdown
   blockquote. Headings, titles, navigation and link labels alone do not count. Phrase
   evidence is a candidate, not proof of the original identity.

URL keys keep HTTP/HTTPS, query order, query parameters (including an empty `?`), path
case and trailing-slash distinctions. Only unreserved percent escapes, host casing,
default ports, Unicode URL encoding and fragments are normalized. International host
names use non-transitional IDNA 2008/UTS 46. Several captures of one URL stay several
choices; the review flags ambiguity and truncated results. When a quotation accompanies
a URL match, the review shows whether it is present in that copy: a matching address can
still belong to a login page or a later revision.

For a phrase that is not quoted in the note, pass `--quotes quotes.json`:

```json
{"https://lost.example/post": "a sufficiently distinctive phrase copied from the original page"}
```

A quotation needs at least 24 alphanumeric characters or 12 CJK characters. Matching
collapses whitespace and casefolds after NFC normalization; it uses no title similarity,
embeddings, fuzzy matching, stemming or punctuation removal.

To restrict a scan to URLs another tool found broken, pass `--only-urls failed-urls.txt`
(one HTTP(S) URL per line; `#` starts a comment line). Link checking stays in tools such
as lychee or buku.

## Recipes for existing archivers

Index an ArchiveBox collection directly, or point `index` at an nb notebook that contains
`.bookmark.md` files. The same index works for ordinary Markdown notes outside that
collection; it stores paths, not another copy of your notes.

For a buku catalog, export its Markdown format and scan it as a note:

```sh
buku --nostdin --export bookmarks.md
notewrench relink scan bookmarks.md --db relink-demo/archive.sqlite \
  --out relink-demo/bookmarks-plan.json --review relink-demo/bookmarks-review.md
```

A buku bookmark on its own is not treated as a saved page body. Rebuild the index after
your captures change.

## Formats

- **HTML / HTM / XHTML:** SingleFile provenance comments, Internet Explorer's
  saved-from comment, canonical links and `og:url`; any saved HTML is searchable by body
  quotation.
- **ArchiveBox:** legacy per-snapshot `index.json`, collection exports with `links` /
  timestamp directories (with or without headers), known HTML outputs and successful
  extractor history paths; typed JSONL snapshot/result records and HTTP-resource
  `url`/`path` manifests. Generated navigation pages are excluded; a resource manifest's
  captured `index.html` stays searchable.
- **nb:** `.bookmark.md` with the original first autolink, `## Content`, `## Quote` and
  captured HTML in `## Source`. Encrypted bookmarks are skipped. An article heading
  identical to a reserved nb section name can truncate the extracted text; the full
  bookmark stays available for manual review.
- **WARC / WARC.GZ:** successful HTML response records are streamed with warcio and
  written beside the index in `INDEX.sqlite.assets/` so backlinks point at a usable
  file. Redirect responses provide alias evidence. Revisit-only records are not
  reconstructed, and subresources are not rewritten for full-fidelity replay.
- **MHTML / MHT:** Python's MIME parser selects the main HTML part, honors related-part
  IDs and MIME charsets, and reads recorded original addresses. Iframe and attachment
  bodies are not article evidence. The backlink points at the original MHTML file; a
  capture whose declared main part is missing is skipped.

The index and the WARC-derived files contain private page content and belong with your
private data. Deleting the index is safe; keep the assets folder if accepted notes link
to its WARC-derived HTML.

## Limits and recovery

- Individual HTML, Markdown bookmark, MHTML and JSON files over 4 MiB are skipped with
  a warning. WARC files and JSONL streams are read incrementally, with a 4 MiB limit per
  HTML payload or JSON record; extracted body text is limited to 1 MiB.
- A scan is bounded to 2,000 notes, 20,000 citations, 20,000 candidate entries and a
  32 MiB review-content budget; split larger jobs or filter them with `--only-urls`.
  Phrase queries examine up to 20 quotations of at most 2,000 characters. Redirect
  exploration stops at eight hops, 256 distinct addresses and 100 edges per address.
  Truncated candidate sets stay marked as ambiguous. JSON plans are limited to 64 MiB.
- The ArchiveBox metadata-to-file map is held in memory; split very large collections
  into smaller archive roots.
- Directory traversal skips symlinks, hidden directories, `.git`, `node_modules`, named
  pipes and the index's own assets. Metadata paths that point outside the archive root
  are rejected. Invalid files produce warnings (up to 1,000 are kept) without hiding
  usable files. Fatal indexing errors, and files that change between parsing and
  fingerprinting, leave the previous index intact.
- Supported Markdown: CommonMark inline links, reference links and angle autolinks;
  YAML front matter is skipped. Code blocks, inline code, images, raw HTML anchors and
  GFM bare-URL autolinks are not repaired. A reference definition shared with an image
  is left unchanged with a warning. Invalid HTTP destinations, URLs with credentials and
  unmappable source spans are reported and left unchanged.
- Notes must be UTF-8 (with or without BOM); patches keep the BOM, line endings and
  final-newline state. Paths containing newlines, tabs, quotes or backslashes cannot be
  patch file names. PDFs, Safari WebArchive, Obsidian wikilinks and WARC revisit
  resolution are not supported.
- Saved HTML may contain JavaScript and external references from the archiver. `relink`
  only parses bytes; it does not execute or sanitize the page.

## Benchmark

[`benchmarks/relink/benchmark.py`](../benchmarks/relink/benchmark.py) generates a
synthetic archive and note and times one `index` + `scan` round (files stay under
`.cache/relink-benchmark/`):

```sh
python benchmarks/relink/benchmark.py --pages 2000 --links 2000
```

On the original development machine, 10,000 pages of 4 KiB and 10,001 citations took
about 8.6 s to index and 2.3 s to scan, with a peak of about 94 MiB; treat these as one
local measurement, not a promise for other machines.

## Related commands and sources

- [`notewrench archive`](archive.md) and [`notewrench epub`](epub.md) *create* offline
  copies of read-later articles; `relink` points existing notes at copies you already
  have.
- Built on [markdown-it-py](https://github.com/executablebooks/markdown-it-py) and
  [mdit-py-plugins](https://github.com/executablebooks/mdit-py-plugins) (source spans
  and front matter), [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/),
  [warcio](https://github.com/webrecorder/warcio), [idna](https://github.com/kjd/idna)
  and SQLite FTS5. Formats follow [SingleFile](https://github.com/gildas-lormeau/SingleFile),
  [ArchiveBox](https://github.com/ArchiveBox/ArchiveBox), [nb](https://github.com/xwmx/nb)
  and [buku](https://github.com/jarun/buku). Test fixture origins are listed in
  [`tests/relink/fixtures/PROVENANCE.md`](../tests/relink/fixtures/PROVENANCE.md);
  licenses and credits are in [THIRD_PARTY.md](../THIRD_PARTY.md).
