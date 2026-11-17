# `notewrench zotero-dupes`: artifact contract

The files `notewrench zotero-dupes` reads and writes, and how their IDs are built. Schema ids (`urn:library-bridge:schema:...`), file names and JSON fields keep the names of the former standalone `library-bridge` tool, so existing reviews and decision files stay valid.

All files are local UTF-8 JSON unless noted. Current protocol version is `schema_version: 1`. Print self-contained [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12/schema) definitions without fetching anything:

```sh
notewrench zotero-dupes schema review
notewrench zotero-dupes schema decisions
notewrench zotero-dupes schema correspondences
notewrench zotero-dupes schema suggestions
notewrench zotero-dupes schema carryover-audit
```

Schemas validate shape and types. Finalization also checks content fingerprints, source references, pair identity, cross-library membership and contradictory decisions. A valid JSON Schema instance alone is insufficient to confirm a correspondence.

## IDs and snapshots

An item ID is `item-` followed by the first 24 hexadecimal digits of SHA-256 over the canonical JSON array `[library_label, source_id]`. Canonical JSON sorts object keys, uses UTF-8 and compact separators. The source ID is a library-scoped Zotero key when available, otherwise the export identity identified by `identity_quality`.

Pair and correspondence IDs use the same construction over the **sorted** two item IDs, with `pair-` and `correspondence-` prefixes. They are independent of input iteration order and matching score. DOI/title values are evidence, not item identities.

Every record also has a complete SHA-256 `fingerprint` over its serialized data, excluding that fingerprint field. A review ID is `review-` plus 32 hexadecimal SHA-256 digits over the complete canonical review, excluding its own ID. It includes library export file hashes and matching configuration/versions. These are local content hashes, not signatures.

## Review and decisions

`review.json` contains a `records` object keyed by item ID, a list of candidate `pairs`, navigation `clusters`, source-library diagnostics and matching provenance. A record retains:

- `metadata`: normalized display/matching fields plus unmapped bibliographic fields.
- `raw`: the exact original JSON item/wrapper, or retained RDF statements/context.
- `tags`, `notes`, `annotations`, `attachments`: original values/objects with their metadata. Attachments represent metadata, not file bytes.

`decisions.json` references one exact `review_id`. Its `decisions` object is keyed by pair ID:

```json
{
  "status": "same",
  "preferred": null,
  "comment": "Confirmed after comparing the authors and publication details"
}
```

Statuses are `unreviewed`, `same`, `different`, `defer`. `preferred` is null or one of that pair's item IDs. Missing pair entries remain unreviewed. Only `same` produces a correspondence. Multiple confirmed edges do not cause additional pairs to be exported.

## Confirmed output

`correspondences.json` preserves both scoped source identities and full record fingerprints, a stable correspondence ID, the pair ID, preference and rationale. Its CSV companion is for spreadsheet inspection; spreadsheet formula prefixes are escaped in CSV, while JSON retains exact strings.

`suggestions.json` retains both complete source records for each confirmed pair, all differing normalized field values with provenance, optional missing-field proposals, and exact raw tag/note/annotation/attachment variants with their source IDs. It does not create a merged bibliography record or select a winner for conflicting fields.

The UI compares note/annotation **content** without treating different item keys as a content difference. Exports still preserve each distinct original object; that UI convenience never drops source objects or note provenance. Highlight text and annotation comment/page are displayed separately. Notes have readable text and an expandable exact export view.

## Refreshing a review

`notewrench zotero-dupes carry-decisions` is an explicit migration between two snapshots. The same stable pair ID **and** unchanged full fingerprints for both source records are required. Any changed note, tag, annotation, raw field or metadata value resets that pair to unreviewed. The audit records previous decisions for changed/absent pairs. This intentionally treats reordered source arrays as changes, even if the content looks similar.

## Demonstration files

[`examples/zotero-dupes/review/`](../../examples/zotero-dupes/review/) is a generated unreviewed example. [`examples/zotero-dupes/synthetic-decisions.json`](../../examples/zotero-dupes/synthetic-decisions.json) and [`examples/zotero-dupes/confirmed/`](../../examples/zotero-dupes/confirmed/) show the finalization format using one clearly labelled **synthetic** confirmation. No real researcher reviewed that example. Regenerate them from a clone of the repository with `python examples/zotero-dupes/make_examples.py --review` (needs the `zotero-dupes` extra).

