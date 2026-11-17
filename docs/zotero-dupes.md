# `notewrench zotero-dupes`

Zotero's own duplicate detection works inside one library only, so the same paper saved in your
personal library and in one or more group libraries stays duplicated, often with different tags,
notes and PDF annotations on each copy. `notewrench zotero-dupes` compares read-only exports of
those libraries, shows each duplicate candidate side by side in an offline HTML review (fields,
tags, notes, annotations), and exports **only the pairs you explicitly confirm**, together with
suggestions for what to carry over. Use it before you consolidate a personal library with group
libraries, or to keep a record of which group item corresponds to which personal item.

It never opens a Zotero database, calls an API, edits a library, deletes items or changes group
permissions; you merge in Zotero yourself. It looks at bibliographic records and their notes, not
at files: to audit the attachment files in `storage/` (duplicate bytes, missing or orphaned
files), use [`notewrench zotero-attachments`](zotero-attachments.md) instead.

Formerly the standalone `library-bridge` tool. It is a thin offline adapter around
[BibDedupe](https://github.com/CoLRev-Environment/bib-dedupe), with RDFLib for native Zotero RDF,
RapidFuzz for title-only candidates and isbnlib for offline ISBN handling (see
[THIRD_PARTY.md](../THIRD_PARTY.md#notewrench-zotero-dupes-formerly-library-bridge)).

**Install:** this command needs optional dependencies:

```sh
pip install 'notewrench[zotero-dupes]'
```

Without them, `notewrench zotero-dupes --help` still works and every other invocation prints that
install hint. pandas is held below 3: BibDedupe 0.11's preprocessing fails on sparse records
(no authors or year) under pandas 3.

**Reads / writes:** reads the exports you name. `compare`, `finalize` and `carry-decisions` write
only into a **new** output directory (never overwritten; a failed run removes its staging
directory). `inspect` and `schema` write nothing. No network access, no worker processes.

- [Quick start](#quick-start)
- [Inputs](#inputs)
- [Reviewing and finalizing](#reviewing-and-finalizing)
- [How matching works](#how-matching-works)
- [Refreshing a review](#refreshing-a-review)
- [Resource limits, errors and exit codes](#resource-limits-errors-and-exit-codes)
- [Artifact contract (schemas and IDs)](zotero-dupes/artifacts.md)

## Quick start

From a clone of the repository, compare the two sample libraries (derived from real Better
BibTeX exports, with clearly documented synthetic library-specific changes: different notes and
tags, PDF annotation metadata, a matching DOI, a conflicting DOI/year and an ambiguous
three-item cluster):

```sh
notewrench zotero-dupes compare \
  --library personal=examples/zotero-dupes/personal.json \
  --library group=examples/zotero-dupes/group.json \
  --output my-review
```

Open `my-review/review.html` in any modern browser. It is one local file with no CDN, server,
network requests or external assets. Review each pair and choose **Same work**, **Different
works** or **Defer**; optionally choose a preferred metadata source and record a rationale. Use
**Download decisions** to save `decisions.json` (browser local storage is only a convenience).
Then export the confirmed pairs:

```sh
notewrench zotero-dupes finalize \
  --review my-review/review.json \
  --decisions ~/Downloads/decisions.json \
  --output confirmed-review
```

The generated `my-review/decisions.json` is an unreviewed template: passing it to `finalize`
correctly exports zero confirmed pairs. A generated review and a clearly labelled synthetic
finalized output are in [`examples/zotero-dupes/review/`](../examples/zotero-dupes/review/) and
[`examples/zotero-dupes/confirmed/`](../examples/zotero-dupes/confirmed/).

Subcommands:

| Subcommand | What it does |
| --- | --- |
| `compare --library LABEL=PATH --library LABEL=PATH ... --output DIR` | Build `review.json`, `review.html` and a `decisions.json` template from two or more exports. Options: `--title-threshold` (RapidFuzz cutoff, 70–100, default 92), `--max-block-pairs` (default 50000, at most 100000), `--max-title-comparisons` (default 5000000, at most 25000000) |
| `finalize --review FILE --decisions FILE --output DIR` | Export only explicitly confirmed correspondences and preservation suggestions |
| `inspect EXPORT [--library LABEL]` | Print counts, identity quality and tag/note/annotation/attachment totals for one export; writes nothing |
| `carry-decisions --old-review FILE --decisions FILE --new-review FILE --output DIR` | Carry decisions to a regenerated review only for unchanged records |
| `schema {review,decisions,correspondences,suggestions,carryover-audit}` | Print a self-contained JSON Schema for an artifact |

## Inputs

Pass a stable, distinct label for each library with repeatable `--library LABEL=PATH` arguments.
Keep labels unchanged between runs; labels are part of item identity. Two or more libraries are
supported, and matches within the same library are excluded. Inputs are detected by content:

| Input | Preserved information | Identity source |
|---|---|---|
| Better BibTeX JSON (`{"items": [...]}`) | Full raw item, metadata, tags including type, string/object notes, attachment metadata and annotation objects if present | `uri`, `key`/`itemKey`, then export ID/citation key |
| Zotero API JSON (array of `{"key": ..., "data": {...}}`) | Full wrapper plus child notes, attachments and annotations associated via `parentItem`/`sourceItemID` | Item URI or library-scoped item key |
| CSL JSON (array) | Original CSL fields, keyword arrays and note strings, if supplied | Export `id` |
| Native Zotero RDF/XML | Actual Zotero exporter vocabulary, ordered authors, DOI/ISBN, containers, manual/automatic tags, child notes, attachment descriptions and raw RDF statements | `z:uri`, otherwise export RDF subject |

In Zotero, export each library separately. For native **Zotero RDF**, enable **Export Notes**.
Files do not need to be exported. Embedded PDF highlights may not appear as annotation objects in
these formats: create/export annotation notes in Zotero if you need their text in the review.
Check an export before relying on it:

```sh
notewrench zotero-dupes inspect examples/zotero-dupes/personal.json --library personal
```

JSON with durable Zotero URIs or keys gives the best identity continuity. Recognized Zotero URI
schemes/hosts are normalized to the same library-scoped item key; conflicting keys are rejected.
Numeric `itemID`, CSL IDs and citation keys are only as stable as the exporter; RDF `#item_N`
subjects can be renumbered; content-only identities change when the item is edited. These cases
are marked explicitly. Duplicate source/export IDs are rejected with examples in the error (real
CSL citation keys can collide between different papers) and are never silently coalesced. A
stable ID is never fabricated from only a DOI or title.

JSON-LD, RDF web fetching, PDFs, ZIPs, RIS and BibTeX are not supported inputs.

## Reviewing and finalizing

`finalize` writes three files into a new directory:

- `correspondences.json`: stable pair correspondence IDs, original library/source IDs, identity
  quality, source fingerprints, preference and rationale.
- `correspondences.csv`: the same pair mapping for a spreadsheet; potentially executable cells
  are prefixed with an apostrophe. The JSON keeps the exact values.
- `suggestions.json`: all differing metadata values with provenance, optional missing-field
  proposals relative to the preferred record, all distinct tags/notes/annotations/attachment
  metadata, and both complete source records. This is a review artifact, not an importable merge
  or delete plan.

Large clusters are paged at 25 pairs to bound the page size. Decisions persist across pages and
downloads include all pairs. Candidate clusters are connected components **for navigation only**
and can contain several items from one library, exposing ambiguity. Every correspondence needs
an explicit pair decision; nothing is confirmed by transitivity. Conflicting explicit decisions
(A same as B, B same as C, A different from C) are rejected at finalization.

## How matching works

BibDedupe 0.11 preprocesses, blocks and labels cross-library record pairs with `cpu=1`; its rules
remain the main bibliographic matcher. The adapter supplies its native book/proceedings container
fields and maps equivalent Zotero/CSL types before matching. Exact normalized DOI/PMID and
checksum-valid ISBN candidates are added, alongside RapidFuzz title candidates (default cutoff
92/100). isbnlib extracts multiple ISBNs from one field, checks them and converts ISBN-10 to
ISBN-13. Documented Zotero `DOI:`/`PMID:` Extra lines and Better BibTeX `tex.doi=` values supply
identifier evidence when a native field is absent; native values take precedence. Invalid native
identifiers are kept and diagnosed, and extracted evidence is labelled in the review. Title
similarity is a string similarity, **not a probability**.

Sparse records without authors or year can still be reviewed by title. Conflicting DOIs, dates
more than one year apart, different item types, shared ISBNs and short or missing titles are
flagged, never automatically accepted or discarded. Conference papers, preprints, corrections,
translations and book chapters need human judgment.

## Refreshing a review

Decisions are bound to a fingerprint of the complete review snapshot; loading an old decision file
into a different snapshot is rejected. Item and correspondence IDs stay the same while source
identities stay the same. To carry decisions into a regenerated review explicitly:

```sh
notewrench zotero-dupes carry-decisions \
  --old-review previous-review/review.json \
  --decisions previous-decisions.json \
  --new-review refreshed-review/review.json \
  --output carried-review
```

Load `carried-review/decisions.json` into the new review. A decision carries over only when **both
complete source fingerprints are unchanged**, including all notes, tags, annotations and raw
fields; changed and new pairs start unreviewed. `carryover-audit.json` records carried, reset and
absent pairs, with the previous decisions for changed or absent pairs.

## Resource limits, errors and exit codes

Everything runs locally with one BibDedupe worker. Defaults: 16 MiB per input, 32 MiB per
generated JSON file or embedded HTML payload, 10,000 parent records in total, 150,000 RDF triples,
RDF structural depth 12, 50,000 intermediate BibDedupe block pairs, 5,000,000 title comparisons and
10,000 candidates. JSON source nesting is limited to 48 levels and artifact nesting to 64. The
block allocation is estimated **before** combinations are materialized, so large identical
DOI/author/year blocks can hit the limit even with few records; export narrower collections first,
or raise `--max-block-pairs` / `--max-title-comparisons` within their bounds.

Exit codes: `0` success; `2` for argument errors, malformed JSON/XML, DTDs/entities, duplicate
identities, missing files, stale decisions, existing output directories or missing optional
dependencies (with an actionable message on stderr prefixed `notewrench zotero-dupes:`); `130` when
interrupted. Input exports are never changed.

No fuzzy-match precision/recall benchmark is claimed. The example data and test fixtures and their
provenance are described in
[`tests/zoterodupes/fixtures/UPSTREAM.md`](../tests/zoterodupes/fixtures/UPSTREAM.md).
