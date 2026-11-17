# `notewrench bibtex-keys`

When you regenerate citation keys in Better BibTeX, every Markdown note that cites,
links or names the old keys silently goes stale, and a search-and-replace cannot tell
which `@key` belongs to which library item. `notewrench bibtex-keys` compares two
BibTeX/BibLaTeX exports (or Better BibTeX JSON snapshots) taken before and after the
change, matches entries **only by an explicit stable item ID** (by default the Zotero
item URI), and writes a review bundle: key mappings, every affected location in your
notes directory, conflicts, and a unified patch you apply yourself after review.

**Which command?** Use `bibtex-keys` when your bibliography is BibTeX/BibLaTeX or Better
BibTeX JSON that carries a stable item ID, and you want a read-only review of a whole
notes directory plus a patch to apply with Git. Use
[`notewrench citekeys`](citekeys.md) when you have two CSL-JSON exports, want items
matched by DOI or exact URL, and want rewritten copies of named Markdown files written
to a new directory.

Formerly the standalone `citekey-audit` tool. Python 3.11+ with the optional
`bibtex-keys` dependencies (Pybtex, markdown-it-py, mdit-py-plugins, pylatexenc,
PyYAML). No Zotero installation, database access, Pandoc executable or network is used.

**Reads / writes:** reads the two snapshots and the notes directory; never modifies
them. `plan` writes one new output directory, which must not exist yet and must be
outside the notes directory. `verify` writes nothing.

## Quick start

```sh
pip install 'notewrench[bibtex-keys]'
```

The examples below use the inputs under [`examples/bibtex-keys/`](../examples/bibtex-keys/),
so run them from a clone of the repository:

```sh
notewrench bibtex-keys --help
notewrench bibtex-keys plan examples/bibtex-keys/old.bib examples/bibtex-keys/new.bib \
  examples/bibtex-keys/notes --out review
cat review/REVIEW.md
cat review/changes.patch
```

This produces four replacements: a metadata field, two citations, and a saved Zotero
citekey link. Inline and fenced code stay literal. The bibliography is derived from a
real Better BibTeX export fixture; the added identity and changed key are explicitly
synthetic (see [fixture provenance](../tests/bibtexkeys/fixtures/PROVENANCE.md)).

Outputs in the `--out` directory:

- `review.json`: machine-readable mappings, conflicts, source locations, and SHA-256
  hashes of affected files before and after the proposed patch.
- `changes.patch`: a standard unified diff containing only unambiguous replacements.
- `REVIEW.md`: mappings, issues, and the first 200 affected locations; every location
  remains available in `review.json`.

Exit status: **0** a complete plan with no outstanding issues, **2** a bundle was
written but needs manual review, **1** a fatal input/output problem (nothing is
published). Argument errors use argparse's exit status 2. A successful plan still
needs your review before you apply it.

To try the patch without changing the example inputs:

```sh
mkdir -p .tmp
cp -R examples/bibtex-keys/notes .tmp/notes
git -C .tmp/notes init -q
notewrench bibtex-keys verify review --notes-root .tmp/notes
(cd .tmp/notes && git apply --check ../../review/changes.patch)
(cd .tmp/notes && git apply ../../review/changes.patch)
notewrench bibtex-keys verify review --notes-root .tmp/notes --after
```

Patch paths are relative to the notes root. If your notes live inside a larger Git
repository, use `git apply --directory=path/to/notes` from that repository's root.
Review or commit your notes first. A patch is a proposal against the captured
snapshot; later edits may make it stale. Initializing Git in the example copy matters:
`git apply` run in a subdirectory of an outer repository can silently skip paths
outside that subdirectory.

`python -m notewrench.bibtexkeys` is equivalent to `notewrench bibtex-keys`.

## Commands and options

`notewrench bibtex-keys plan OLD NEW NOTES --out DIR [options]`

| Option | Meaning |
| --- | --- |
| `OLD`, `NEW` | Old and new `.bib` export, or `.json` Better BibTeX JSON snapshot |
| `NOTES` | Notes directory to scan (read-only) |
| `--out DIR` | New output directory (must not exist; must be outside `NOTES`) |
| `--id-field FIELD` | Stable ID field; repeat to accept aliases. Default: `zotero-uri`, `zotero_uri` |
| `--extension EXT` | Note extension; repeat to override the defaults `md`, `markdown`, `qmd`, `rmd` |
| `--max-note-bytes N` | Per-note size limit (default 2 MiB) |
| `--max-files N` | Files to scan (default 10,000) |
| `--max-occurrences N` | Affected references to record (default 100,000) |

`notewrench bibtex-keys verify BUNDLE [--notes-root DIR] [--after] [--notes-only]`

`verify` checks both bibliography snapshot hashes, the patch hash, and **every scanned
note**, including previously unaffected notes. It reports bibliography drift, edits,
new/deleted notes, and incomplete coverage without writing anything. `--after` expects
the proposed replacement bytes, and `--notes-root` checks a relocated copy. Exit 0
means the snapshot matches; exit 2 means stale/incomplete; exit 1 means invalid input.
Matching hashes do not resolve the original plan's manual-review issues. Use
`--notes-only` to check just notes and patch when the original bibliography files are
unavailable; the result records that narrower scope.

## Preserve stable identity before changing keys

Ordinary BibTeX does not require a stable item identifier. A DOI identifies a
publication, not necessarily a unique Zotero library item. Title/author matching can
silently connect the wrong notes, so the command never guesses identity.

In Better BibTeX's **Export → Postscript**, add this to your existing postscript:

```javascript
if (Translator.BetterTeX && zotero.uri) {
  tex.add({ name: "zotero-uri", value: zotero.uri });
}
```

1. Export the relevant library/collection with this field **before** changing keys.
2. Change/regenerate your citation keys using Better BibTeX.
3. Export the same scope with the same identity field again.
4. Run `notewrench bibtex-keys plan old.bib new.bib notes --out review-new`.

The recipe follows the Better BibTeX v9.0.77 serializer and documented postscript API;
it has not been executed inside Zotero. Check that your exports actually contain
`zotero-uri`. Stable Zotero item links such as `zotero://select/library/items/ABCD1234`
already survive citation-key changes.

If an old export lacks identity, the report says `missing_identity`. Recover a matching
identity-bearing historical export or supply a field that already contains a genuinely
stable ID; do not fabricate IDs from citation keys. For a different exporter:

```sh
notewrench bibtex-keys plan old.bib new.bib notes --id-field my-stable-id --out review-custom
```

`--id-field` may repeat to accept aliases. Values across aliases on one entry must
agree. The defaults `zotero-uri` and `zotero_uri` accept full Zotero user or group item
URIs, normalize http/https and trailing slash, and preserve library scope. Custom
identity fields use exact, case-sensitive parsed values, with no additional whitespace
trimming. BibTeX's standard string/whitespace semantics are still applied by Pybtex.
Numeric local `itemID` values should only be used if their stability across both
snapshots is independently guaranteed.

Source locations use one-based lines and Unicode-code-point columns; `start` and `end`
are zero-based Unicode-code-point offsets with an exclusive end. Filename-only findings
have no source span. Hashes and patches use exact UTF-8 bytes, including original BOM
and line endings.

### Alternative: Better BibTeX JSON snapshots

Better BibTeX's existing **BetterBibTeX JSON** exporter preserves `uri` and
`citationKey` directly. Export it before and after changing keys, with **Items**
enabled, and use `.json` filenames:

```sh
notewrench bibtex-keys plan examples/bibtex-keys/old.json examples/bibtex-keys/new.json \
  examples/bibtex-keys/notes --out review-json
```

This avoids a custom BibTeX field. Format selection follows the filename: `.json` means
BetterBibTeX JSON, everything else means BibTeX/BibLaTeX. Mixed BibTeX/JSON snapshots
work when both carry the same canonical Zotero item URI. The JSON adapter requires the
exporter's actual `config.id`, uses only `uri` and `citationKey`, skips
note/attachment/annotation items, and never derives identity from numeric `itemID` or
citation keys. `--id-field` applies to BibTeX; JSON always uses `uri`. Plain CSL-JSON is
rejected because its `id` usually represents the citation key rather than stable
library-item identity (for CSL-JSON, use [`notewrench citekeys`](citekeys.md)).

BetterBibTeX JSON is an upstream debug/interchange format, not a standardized
bibliography format. Missing IDs/keys and duplicate JSON members are explicit
issues/errors.

## Supported references

- Pandoc `@key`, `[@key; -@other]`, locators, and `@{complex-key}`. A new key requiring
  braces is braced automatically.
- Top-level YAML `citekey`, `citekeys`, `citation-key`, and `citation_key` scalar values
  or lists. Only those explicit metadata fields are changed.
- Better BibTeX links `zotero://select/items/@key`, including percent-encoded keys,
  Markdown links/autolinks, and reference-style link definitions. Only link
  destinations change; labels and titles remain untouched. Stable item-key links need
  no replacement.
- Wiki/file links and note filenames containing an old key are **reported for manual
  review**. They are not changed because their destination must be renamed along with
  every backlink. Explicit `@key Title.md` filenames are recognized too. Use your note
  application's rename operation. Custom filename templates that hide or transform the
  key require a separate manual search.

Markdown structure protects fenced/indented code, inline code, dollar math, parsed
attributes, footnote labels, escaped `@`, HTML tags/comments, raw HTML code-element
bodies, ordinary URLs, and ordinary Markdown link labels/destinations. Footnote body
citations are scanned. Code elements include `script`, `style`, `code`, `pre`,
`textarea`, and `math`; ordinary inline HTML prose stays scannable. Raw TeX macros and
environments are protected using pylatexenc, including environments spanning
paragraphs. Unclosed TeX requires manual review. Email-shaped citation text and
references inside HTML code elements receive manual-review entries because Markdown
dialects can interpret them differently. Changes inside a heading with no explicit ID
warn that automatic anchors and section backlinks may change.

All replacements refer to original source offsets, so key swaps and chains do not
cascade. Source formatting, LF/CRLF, UTF-8 BOM, and final-newline state are preserved
outside the replacement spans.

The notes must still use the **old snapshot's key meanings**. A directory that mixes
already-migrated citations with old citations cannot be disambiguated from the text
alone, especially when keys are swapped or reused. Scope such a review to a known
pre-migration snapshot.

## Conflicts, limits, and coverage

Duplicate IDs, contradictory ID aliases, missing identities, and removed items are
explicit report items. Ambiguous mappings never produce replacements. Malformed BibTeX,
repeated bibliography keys/fields, and undefined string macros fail parsing rather than
silently dropping data. Pybtex treats bibliography keys case-insensitively when checking
duplicates; `Key` and `key` in one export are therefore rejected conservatively.

Scans include `.md`, `.markdown`, `.qmd`, and `.rmd`. Override with repeated
`--extension`. Hidden files/directories and `.git`, `.venv`, `.cache`, `node_modules`,
and `vendor-tmp` are excluded. Symlinks are not followed and matching skipped
files/directories are reported. There is a 16 MiB limit per bibliography, 2 MiB per
note, 10,000 files, and 100,000 affected references; raise the note/scan limits with
the options above. Unreadable, non-UTF-8, oversized notes and unsupported source spans
are reported; they never imply a complete scan.

Review JSON is streamed with a 64 MiB output limit, shared by the verifier. If that
limit is exceeded, staging files are removed and no output bundle is published; reduce
`--max-occurrences` or review smaller directory subsets. Special files such as FIFOs
are skipped rather than opened, and scans matching no notes are reported instead of
claiming complete coverage.

This is not a complete Pandoc/Obsidian/Quarto interpreter. Custom plugins, embedded
languages, arbitrary YAML metadata text, Org-mode, LaTeX citation commands, PDF/docx
files, attachments, and renaming notes are out of scope. YAML anchors/aliases and
block-scalar citekeys require manual review. Metadata errors can leave body references
patchable, but the bundle remains `review_required`.

## Benchmark

`python benchmarks/bibtex-keys/benchmark.py` runs a bounded, serial scanner benchmark on
synthetic text. It creates no files and prints timing plus platform-native peak RSS as
JSON; the numbers are observations for your machine, not portable guarantees.

## Related tools

[Better BibTeX](https://github.com/retorquere/zotero-better-bibtex) generates and manages
citation keys; this command only adds an offline review step around that workflow.
[ZotLit](https://github.com/aidenlx/zotlit) keeps literature-note identity in a stable
`zotero-key` frontmatter field and is the better choice for ongoing note management.
[Obsidian Citations](https://github.com/hans/obsidian-citation-plugin) and
[Zotero Integration](https://github.com/community-archive/obsidian-zotero-integration)
create and refresh literature notes, and [bibtex-tidy](https://github.com/FlamingTempura/bibtex-tidy)
formats bibliographies and generates keys; none of them compares two exports against a
notes directory. Attribution and licenses are in [THIRD_PARTY.md](../THIRD_PARTY.md);
this command's code is MIT-licensed.

Review bundles keep the original identifier `"tool": "citekey-audit"` and
`"schema_version": 1`, so bundles written by the former standalone tool still verify.
