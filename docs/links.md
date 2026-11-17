# notewrench links

Bookmarks saved in both Karakeep and Wallabag — or saved twice in one of them —
rarely have byte-identical URLs: tracking parameters, host case and default ports
differ. `notewrench links` compares Karakeep native and Wallabag v2 JSON exports
**offline**, groups duplicate *candidates*, unions tags, keeps every note and
annotation with its source, and writes an explainable, data-only merge plan. On
explicit approval it can also write a new Wallabag v2 native-field export.

It never goes online, deletes, calls an app API or imports anything, and it does
**not** treat an identical URL as proof of identical content.

**Which command?** Use `links` for Karakeep and Wallabag read-later JSON exports. For
browser bookmark HTML exports (Firefox, Chrome, Safari) and linkding HTML exports that
you want to merge with an explicit decision per group before importing them, use
[`notewrench browser-bookmarks`](browser-bookmarks.md).

Formerly the standalone `linkdedupe` tool (Node.js); see
[Compatibility with linkdedupe](#compatibility-with-linkdedupe).

**Reads / writes:** reads only the export, rules and decision files you name; never
modifies them. With `--out` it creates one new directory of JSON reports (an existing
path is refused); `--dry-run` writes nothing and prints the summary only.

**Platforms:** Linux and macOS (Python 3.11+, standard library only); Windows is not
supported. The `--out` directory is never created through a symlink: its immediate
parent must be a real, existing directory. On macOS `/tmp` and `/var` are symlinks,
so use `/private/tmp/...` or a directory under your home folder for `--out`.

## Quick start

```sh
pip install notewrench
notewrench links --help
```

From a clone of the repository, run it on the bundled synthetic examples (invented
data on reserved example domains):

```sh
notewrench links --out review examples/links/karakeep.json examples/links/wallabag-v2.json
notewrench links --dry-run --rules examples/links/host-rules.json examples/links/host-rules-wallabag-v2.json
notewrench links --wallabag-export --out native-review examples/links/wallabag-merge-v2.json
```

`review` must not exist yet and its parent must. The first command reports 4 source
records, 3 canonical records and 1 candidate cluster: `https://example.org/a` with
tags `研究`, `offline`, `阅读`, the Karakeep note and the Wallabag annotation. The
HTTP entry and the text-only bookmark stay separate.

stdout is one line of JSON (the summary); diagnostics go to stderr as
`notewrench links: <message>`. Exit status is **0** on success and **1** for every
failure (bad option, input, rules, decisions or a failed policy); on failure
stdout is empty and no output directory is created.

`python -m notewrench links` and `python -m notewrench.linkdedupe` are equivalent.
The library API lives in `notewrench.linkdedupe`: `build_report`,
`build_wallabag_export`, `normalize_url`, `validate_options`, `parse_rule_file`,
`parse_json`, `detect_format`, `TRACKING_PARAMS` and `WALLABAG_EXPORT_ADAPTER`.

## Input formats

- **Karakeep native**: an object with a `bookmarks` array. Each bookmark has a
  numeric `createdAt` (Unix seconds), nullable `title`, string-array `tags`,
  nullable `note` and `content` that is `{"type":"link","url":...}`,
  `{"type":"text","text":...}` or `null`; optional `lists` and `archived`. Root
  `lists` and all other root metadata are preserved. Text/null bookmarks are valid
  but have no URL, so they are never clustered.
- **Wallabag v2**: a JSON array of entries with string `url`, nullable `title` and
  string-array `tags`; `id`, if present, must be a string or safe integer.
  `created_at` and `is_archived` (present in native dumps) drive auto-detection.
  `is_archived`/`is_starred` accept booleans or integer 0/1. Every `annotations`
  object (quote, ranges, extension fields) becomes a note. A non-native `note`
  extension is kept explicitly as a note too.
- **Not supported**: Karakeep REST responses, Wallabag `_embedded` API responses,
  ZIP, HTML or arbitrary JSON. Unknown shapes fail. Empty arrays and URL-only arrays
  are ambiguous and need `--format`.

```sh
notewrench links --dry-run --format wallabag-v2 my-export.json
notewrench links --out review2 --format karakeep --format wallabag-v2 k.json w.json
notewrench links --out review3 --skip-invalid k.json w.json
```

One `--format` applies to all inputs; repeated, it must be given once per input in
positional order. The default is strict: any invalid record exits 1 and writes
nothing. `--skip-invalid` quarantines record-level errors only, exits 0, reports the
count on stdout/stderr and in the reports, and keeps the raw rejected record in
provenance. JSON syntax errors, unknown shapes and bad rules always fail.

Inputs must be UTF-8 JSON. Duplicate keys, non-finite or unsafe integers and nesting
deeper than 256 levels are errors, so fields and large IDs are never silently
overwritten or rounded (export large IDs as strings). The exact original text
(number spelling, BOM, whitespace) and its SHA-256 are kept in provenance. Inputs are
read into memory; this is not a streaming tool.

The adapters follow the public
[Karakeep export schema](https://github.com/karakeep-app/karakeep/blob/88a9ff9cfdfa2b99da4d6a700ea2d0055bd3639d/packages/shared/import-export/exporters.ts)
and Wallabag's
[v2 import fixture](https://github.com/wallabag/wallabag/blob/2c15fef98033c43d86cf3dcdd97bfe53f3b4f22d/tests/fixtures/Import/wallabag-v2.json),
[exporter](https://github.com/wallabag/wallabag/blob/2c15fef98033c43d86cf3dcdd97bfe53f3b4f22d/src/Helper/EntriesExport.php),
[Entry](https://github.com/wallabag/wallabag/blob/2c15fef98033c43d86cf3dcdd97bfe53f3b4f22d/src/Entity/Entry.php) and
[Annotation](https://github.com/wallabag/wallabag/blob/2c15fef98033c43d86cf3dcdd97bfe53f3b4f22d/src/Entity/Annotation.php)
entities (pinned commits; structural facts only, no upstream code or content). Karakeep
native exports have no bookmark ID, so `nativeId` is `null` and records are referenced
by source position (`s1:r1`, …). The canonical reports are a review format, **not an
importable file for either app**. Not every historical export version was verified.

## URL policy

By default only: scheme/host case folding, IDNA mapping of Unicode hosts, removal of
the scheme's default port, and removal of these **case-sensitive exact** parameters:

```text
utm_source utm_medium utm_campaign utm_term utm_content utm_id
utm_source_platform utm_creative_format utm_marketing_tactic
gclid dclid fbclid msclkid mc_cid mc_eid
```

Query keys are decoded only to recognise names; values are never decoded or
re-encoded. Duplicate keys, key order, empty values and tokens, percent-encoding,
fragments, dot segments, non-default ports and trailing-dot hosts are preserved. An
empty path differs from `/`, HTTP differs from HTTPS. Titles never cluster.
`https://EXAMPLE.org:443/a?utm_source=x` and `https://example.org/a` cluster;
`http://example.org/a`, `https://example.org/a/` and `https://example.org/a#s` do not.

A URL carrying a known signed/authenticated parameter is kept **byte for byte** and no
opt-in rule runs (a protection reason is recorded). Names match case-insensitively
after percent-decoding: `sig`, `signature`, `token`, `access_token`, `auth`,
`authorization`, `jwt`, `expires`, `expiry`, `policy`, `key-pair-id`, `awsaccesskeyid`,
`oauth_signature`, `hmac`, `hash`, `api_key`, `apikey`, `hdnts`, `hdnea`, and the
prefixes `x-amz-`, `x-goog-`, `oauth_`. Private signing schemes cannot all be
recognised; when in doubt use `--keep-tracking` or a host keep rule.

| Option | Effect |
| --- | --- |
| `--keep-tracking` | Keep all tracking parameters; conflicts with `--drop-param` and host `dropParams` |
| `--strip-fragment` | Drop the whole fragment (may be an SPA route) |
| `--sort-query` | Sort by decoded key; values of one key keep their order; encoding unchanged |
| `--https` | Treat http as https; removes the original default port, and 443 afterwards; no network check |
| `--strip-trailing-slash` | Remove all trailing `/` from the path, including a root `/` |
| `--drop-param NAME` | Repeatable, at most 32; only exact builtin, `utm_`, `pk_` or `mtm_` names; no wildcards, regexes, business keys or signature-like names |

Only absolute HTTP(S) URLs are accepted. Credentials, invalid ports, empty hosts, raw
whitespace/control characters/backslashes, malformed percent escapes and non-UTF-8
query keys are rejected, never repaired. WHATWG URL rules are used only for
validation, never to re-serialize paths or queries. Unusual IPv4 spellings and IPv6
compression are not folded further.

## Host rules and per-rule dry run

`--rules FILE` reads strict UTF-8 JSON (`schemaVersion: 1`); no scripts or regexes.
[`examples/links/host-rules.json`](../examples/links/host-rules.json):

```json
{
  "schemaVersion": 1,
  "rules": [
    { "id": "catalog-business-source", "host": "catalog.example", "keepParams": ["utm_source"] },
    { "id": "news-tracking", "host": "news.example", "dropParams": ["utm_source", "ref"] }
  ]
}
```

`keepParams` override default tracking removal and `--drop-param`; `dropParams` may
remove ordinary business keys for that host. Keys match exactly and case-sensitively
after decoding; every occurrence of a repeated key gets the same decision. Signature
protection overrides all rules; `allowSigned` and other unknown fields are errors, and
known or signature-like parameters cannot be dropped.

`host` is an exact host name (case-insensitive, Unicode mapped to IDNA) covering HTTP,
HTTPS and every port. There is no subdomain inheritance, wildcard, path or port
matching; schemes, ports, trailing dots and percent escapes are rejected. IPv6 hosts
are not supported in rules. Unknown hosts keep the default policy.

Rules need a unique `id` (1–64 ASCII letters/digits/`_.-`, starting alphanumeric),
1–128 rules, at most 32 non-empty exact names (1–128 of `A-Za-z0-9_.~-`) per
`keepParams`/`dropParams`, and at least one action. Each host/parameter pair may be
declared only once. Unknown fields, empty actions, wrong types, duplicate JSON keys,
duplicate IDs, conflicting targets and invalid UTF-8/JSON exit 1 with empty stdout,
even with `--skip-invalid`.

The bundled 7-record input yields 6 canonical records and one `news.example`
cluster: catalog's `?utm_source=edition-one` / `edition-two` stay apart, news'
`?utm_source=mail&ref=front` joins the query-less URL, the unknown host only loses
`utm_source`, and the `sig=synthetic` URL is untouched.

With rules, the dry-run summary adds `ruleSummary` (every rule: host matches,
parameter matches, changed and protected URLs) and `ruleComparisons` (per matching
URL, in rule order: `beforeUrl`/`afterUrl`, parameter actions, status `applied` /
`no-match` / `protected`). Comparisons start from the same input and global options
**without** host rules and add rules cumulatively, so a keep rule can restore a
parameter the baseline removed. Every decision carries the rule ID, file name, the
SHA-256 of the exact rule file text and a `/rules/N` JSON Pointer; normal reports
keep the same provenance in `ruleComparisons`, `normalizationReasons` and
`normalization.rules`.

## Optional cluster membership comparison

```sh
notewrench links --dry-run --compare-clusters --rules examples/links/host-rules.json examples/links/host-rules-wallabag-v2.json
notewrench links --dry-run --compare-rule-clusters --rules examples/links/rule-clusters.json examples/links/rule-clusters-wallabag-v2.json
notewrench links --dry-run --compact-rule-clusters --rules examples/links/rule-clusters.json examples/links/rule-clusters-wallabag-v2.json
```

All three require `--rules` (API: `compare_clusters`, `compare_rule_clusters`,
`compact_rule_clusters`, booleans only). They add one `clusterComparison` object to
stdout and `clusters.json`; candidates, merge plan and approval hashes are unchanged.

| Field | Contract |
| --- | --- |
| `baseline` / `ruleEnabled` | Exact `normalization` and `clusters`; baseline uses the same accepted records and global options with `rules: null` |
| `clusters[]` | Candidates with two or more members: `{canonicalUrl, sourceRefs}` in first-member order |
| `enteredSourceRefs` / `leftSourceRefs` | Records going from standalone to candidate and back, in input order |
| `changes[]` | One row per record whose peers changed: `sourceRef`, `before`/`after` (cluster or `null`), `addedPeerSourceRefs`, `removedPeerSourceRefs` |

On the 7-record example the baseline cluster is catalog `[s1:r1,s1:r2]`, the final
one news `[s1:r3,s1:r4]`; entered `["s1:r3","s1:r4"]`, left `["s1:r1","s1:r2"]`, four
changes. URL-only changes are not membership changes.

`--compare-rule-clusters` adds `ruleSteps`, one per declared rule in file order:
`ruleId`, `host`, `source {name, sha256, pointer}`, `beforeClusters`,
`afterClusters`, `enteredSourceRefs`, `leftSourceRefs`, `changes`, each relative to
the previous step (first `before` = baseline, last `after` = `ruleEnabled`). For the
4-record example: baseline `?ref=one` → [r1,r2], `?ref=two` → [r3,r4]; `edition`
splits all four (left [r1..r4]); `ref` regroups them as `?utm_source=x` → [r1,r3] and
`?utm_source=y` → [r2,r4]; `unused` changes nothing.

`--compact-rule-clusters` (`mode: "compact"`) replaces inventories with counts:
`baseline`/`ruleEnabled` `{normalization, candidateClusters, clusteredRecords}`,
`enteredRecords`, `leftRecords`, `changedPeerRecords`, and `ruleSteps` with
`before`/`after` counts. The example gives 2/4 → 2/4 overall with 0/0/4 changes;
`edition` 2/4 → 0/0 (0/4/4), `ref` 0/0 → 2/4 (4/0/4), `unused` 0/0/0.
[`compact-merge-wallabag-v2.json`](../examples/links/compact-merge-wallabag-v2.json)
merges two two-member candidates (baseline 3/6, final 2/6, `ref` step 0/0/4).

## Outputs

| File | Contents |
| --- | --- |
| `records.json` | `schemaVersion: 1`, sources, configuration, counts and every canonical record (including singletons and no-URL records) |
| `clusters.json` | Candidate clusters (two or more records) and rejected records with position and reason |
| `merge-plan.json` | `mode: "candidate-only"`, `executable: false`: source refs, tags, notes and fields needing manual review |
| `provenance.json` | Each source's exact `originalText`, SHA-256 and root metadata; every accepted/rejected raw record with its JSON Pointer |

Each canonical record lists its `members` (source, original URL, native ID,
`normalizationReasons`, `protections`). Tags are unioned in first-seen order with no
case or Unicode folding. Notes keep **every** occurrence in source order. All titles
are in `titles`; `title` is the first one and `titleConflict` flags disagreement.
Body, dates, archive/star state, list IDs and unknown fields are never merged; they
appear in `unmergedFields` per source value, with `conflict: true` when values differ
or a member lacks the field. `sN:rN` and `cN` are positions in this run, not app IDs.
Reports are deterministic for fixed inputs, order and options (the report `tool`
field stays `"linkdedupe"`).

The output directory is created fresh (mode 0700, files 0600 subject to umask);
existing paths, including symlinks, are refused. Everything is computed before
writing and an ordinary write error removes the new directory. A killed process can
leave a partial directory: delete it and rerun. Reports contain all input content;
review them before sharing.

## Approved Wallabag native-field export

`--wallabag-export` takes exactly **one** Wallabag v2 file. The adapter is pinned as
`wallabag-v2@2c15fef98033c43d86cf3dcdd97bfe53f3b4f22d`: the `entries_for_user`
projection of that commit's exporter, with these entry fields

```text
id uid title url origin_url given_url is_archived archived_at is_starred
content created_at updated_at published_at published_by starred_at annotations
mimetype language reading_time domain_name preview_picture http_status headers
is_public tags
```

and annotation fields `text`, `quote`, `ranges`. It adds three files:

| File | Contents |
| --- | --- |
| `wallabag-export.json` | A new native-field JSON array; candidates stay separate unless approved |
| `wallabag-decisions.json` | A decision template bound to the exact input bytes and URL policy, all `approved: false` |
| `wallabag-merge-report.json` | Per-source mapping, applied decisions and outcomes, blockers, scalar conflicts, unrepresentable fields and the read-back check |

```sh
notewrench links --wallabag-export --out native-review examples/links/wallabag-merge-v2.json
cp native-review/wallabag-decisions.json approved.json
# Edit approved.json: set "approved": true only for groups you confirmed, choose keepSourceRef.
notewrench links --wallabag-export --decisions approved.json --out native-merged examples/links/wallabag-merge-v2.json
notewrench links --dry-run --format wallabag-v2 native-merged/wallabag-export.json
```

Approving the single candidate of the 3-entry example with keeper `s1:r1` produces
[`wallabag-merge-expected-v2.json`](../examples/links/wallabag-merge-expected-v2.json):
2 entries; id 10 keeps its URL, title and state, tags `研究`, `offline` and both
identical annotations; id 12 stays separate.

Decision schema 1 has exactly `schemaVersion`, `adapter`, `basisSha256` and
`decisions`; each decision exactly `sourceRefs`, `keepSourceRef`, `approved`. The
basis hashes the adapter, the input text SHA-256 and the validated normalization
(including rule provenance), so changed bytes, order or policy invalidate approvals.
A decision selects **two or more distinct members of one current candidate** and a
keeper among them. Replace a whole-candidate row by disjoint subgroups to approve
parts of a candidate; all rows, including `approved: false`, must be disjoint.
Empty/singleton/repeated refs, overlaps, unknown or cross-candidate refs, keepers
outside the selection, unknown fields, wrong types and basis mismatches exit 1 even
with `--dry-run` or `--skip-invalid`. Missing or false approvals never merge.

An approved group is **blocked** (with reason) when any member has a repeated non-empty
`id`/`uid` anywhere in the source (`10` and `"10"` count as equal), when a member's
annotations lack string `text`/`quote` or array `ranges`, or when a non-keeper with
annotations has `content` different from the keeper's string `content`. Precedence is
identity, annotation shape, annotation content. Merges union tags and concatenate
annotations in **source order**; the keeper's URL and scalars, including absence,
stay. Every differing field appears in `conflicts` (`keeper-value-or-absence` for
native fields, `audit-only` otherwise). Unknown entry/annotation fields, including the
non-native `note`, go to `unrepresentedFields` with JSON Pointers (`~` → `~0`,
`/` → `~1`). Records with malformed annotations are `unprocessed`, as are
`--skip-invalid` quarantined rows. The output is serialized, re-read through the same
v2 adapter and compared (`roundTrip.verified`). **The native JSON alone is not
lossless**: keep the audit and provenance. Live Wallabag import and annotation
re-attachment are not tested.

`decisionOutcomes` has one row per submitted decision, in input order:
`decisionIndex`, `sourceRefs` (source order), `keepSourceRef`, `status`, `reason`,
`outputPointer`. Status/reason pairs: `applied`/`explicitly-approved` (with pointer),
`blocked`/`ambiguous-native-identity|unrepresentable-annotation|annotation-content-mismatch`,
`not-approved`/`not-approved` (pointer `null`). For
[`wallabag-subgroups-v2.json`](../examples/links/wallabag-subgroups-v2.json)
(rows 1–5 one candidate, row 6 standalone), approving `[r5,r1]` keeper r1 and `[r4,r2]`
keeper r2 gives:

```json
[
  {"decisionIndex":0,"sourceRefs":["s1:r1","s1:r5"],"keepSourceRef":"s1:r1","status":"blocked","reason":"annotation-content-mismatch","outputPointer":null},
  {"decisionIndex":1,"sourceRefs":["s1:r2","s1:r4"],"keepSourceRef":"s1:r2","status":"applied","reason":"explicitly-approved","outputPointer":"/1"}
]
```

Approving `[r3,r1]` keeper r3 and `[r4,r2]` keeper r2 instead outputs IDs
`[103,102,105,106]` with mapping pointers `[/0,/1,/0,/1,/2,/3]`.

### stdout audit flags

Each requires `--wallabag-export`, works with `--dry-run`, is off by default and adds
one key (in this order) to the summary without changing any file or hash:

| Flag | stdout key | Equals |
| --- | --- | --- |
| `--decision-outcomes` | `wallabagDecisionOutcomes` | `wallabag-merge-report.json` `decisionOutcomes` |
| `--wallabag-mapping` | `wallabagMapping` | `mapping` (every source row in source order) |
| `--wallabag-conflicts` | `wallabagConflicts` | `conflicts` |
| `--wallabag-unrepresented` | `wallabagUnrepresentedFields` | `unrepresentedFields` |
| `--wallabag-decisions` | `wallabagDecisions` | the fresh template (`wallabag-decisions.json`) |

`notewrench links --wallabag-export --wallabag-decisions --dry-run examples/links/wallabag-subgroups-v2.json`
returns the hash-bound template (basis
`b898bd2458d8dc273eb86c7335144c2d5dc6c9a41a9b02a5d191b33ef42074c4`) without writing
anything, ready to edit and pass to `--decisions`.

### Policies

Opt-in checks that run after full validation and before any directory is created or
stdout is written; they always run in this fixed order regardless of flag order and
fail with exit 1 and one exact stderr line:

| Flag | Fails when | stderr |
| --- | --- | --- |
| `--require-all-applied` | a submitted decision is not `applied` (false rows too; omitted/empty decisions pass) | `notewrench links: --require-all-applied: decision <i> <status>/<reason>` |
| `--require-all-processed` | a source row is `unprocessed` (quarantined rows too; empty dumps pass) | `notewrench links: --require-all-processed: <sourceRef> unprocessed: <reason>` |
| `--require-no-unrepresented` | `unrepresentedFields` is non-empty (scalar conflicts pass) | `notewrench links: --require-no-unrepresented: <sourceRef> <pointer>: <reason>` |
| `--require-no-conflicts` | `conflicts` is non-empty | `notewrench links: --require-no-conflicts: <keptSourceRef> /<escaped-field>: <resolution>` |

Even all four together are not a lossless-import guarantee.

## Compatibility with linkdedupe

`notewrench links` is a Python port of `linkdedupe` 1.0.0. Report formats, IDs, schema names, the
adapter identifier, decision hashes and error messages are unchanged, apart from the
program name (`notewrench links: …` instead of `linkdedupe: …`) and `--version`
printing `notewrench links 1.0.0`. Options are parsed with the same rules as Node's
`util.parseArgs` (so `--flag=value` on a boolean, unknown options and `--no-…` forms
fail with the original messages). JSON is serialized like `JSON.stringify` (number
formatting, key order, escapes).

Unicode hosts are mapped with an embedded UTS #46 table (Unicode 17.0) and the URL
Standard's host rules. Character properties used by the Bidi, joiner and leading-mark
checks and by NFC come from Python's `unicodedata`; Node's URL parser carries its own
tables. For characters assigned in Unicode 14 or later (new scripts and symbols) the
two can disagree on whether a host is valid, so such hosts may be accepted or rejected
differently. All ASCII and established-script hosts behave identically.

## Benchmark

A deterministic synthetic corpus of 500 URL families (12,000 URLs, 3,500 labelled
positive pairs, with HTTP/HTTPS, order, duplicate-key, encoding, trailing-slash,
fragment and signature hard negatives) is grouped by exact URL and by the default
policy. Exact strings find 500 true positive pairs (recall 0.143); the default policy
finds all 3,500 (recall 1.0) with 0 false merges. This measures the policy on invented
URLs only — not real bookmark quality and not speed.

## Related tools and limits

[Karakeep](https://github.com/karakeep-app/karakeep) and
[Wallabag](https://github.com/wallabag/wallabag) have their own duplicate handling
(see the [tracking parameter request](https://github.com/karakeep-app/karakeep/issues/633),
[trailing slash PR](https://github.com/karakeep-app/karakeep/pull/2841) and
[Wallabag duplicate entries issue](https://github.com/wallabag/wallabag/issues/8478));
[linkding](https://github.com/sissbruecker/linkding) and
[bagcarry](https://github.com/Rezarys/bagcarry) overlap in scope. This tool focuses on
offline, explained candidates across the two export formats with full provenance and
a manual merge plan. There is no online canonicalisation, content similarity, app
API, automatic deletion, live import verification or streaming. IDs, bodies or notes
missing from an export cannot be recovered.
