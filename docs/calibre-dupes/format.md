# `notewrench calibre-dupes`: checked interoperability contract

This page records the Calibre sources the command was checked against and the exact
projection of `metadata.db` it reads. `tool: "calibre-dupes"` and other identifiers in the
JSON plans keep their original names so existing plans and decision files stay valid.

Checked on 2026-11-11 against Calibre master commit
`be4a47bd5ba1da3dc10a46f7e4eeea3598d3295b`:

- [resources/metadata_sqlite.sql](https://github.com/kovidgoyal/calibre/blob/be4a47bd5ba1da3dc10a46f7e4eeea3598d3295b/resources/metadata_sqlite.sql)
  SHA256 `3503d771a142fcfb2c4461b4346ee0b53b95b117209240d40f264c1c1218225f`.
- [src/calibre/db/backend.py](https://github.com/kovidgoyal/calibre/blob/be4a47bd5ba1da3dc10a46f7e4eeea3598d3295b/src/calibre/db/backend.py)
  SHA256 `07cc97d1d1b7c082ad045b8d5b088093997139e6d1676457986616845adc61f6`.

Only observations are recorded here; upstream files are not vendored. The
schema lives in resources/metadata_sqlite.sql (there is no db/schema.sql). Backend
initialization loads that resource. Backend library paths select metadata.db.
Author names use a vertical bar to represent an embedded comma; the reader
decodes it before matching. Authors are taken from relational links, not the
books.author_sort display/sort field. ISBN is the identifiers type `isbn`, with
its string in `val`; neither a legacy books.isbn column nor separate isbn10/13
columns are required or read. Other identifier types are retained as evidence
but are not matched.

This tool validates this **projection**, not the whole Calibre schema:

| Table | Required integer-affinity fields | Required text-affinity fields |
| --- | --- | --- |
| books | id | title, path |
| authors | id | name |
| books_authors_link | id, book, author | — |
| identifiers | id, book | type, val |
| data | id, book, uncompressed_size | format, name |

Each id must be the sole primary key; all accessed values must have the expected
SQLite storage types and be non-NULL. Tables must be ordinary tables, not views
or virtual tables. Other Calibre columns, triggers, indexes and tables are
allowed but never executed or interpreted, except the optional metadata projection below. No user-defined SQLite extension,
collation, metadata template, script or Calibre import is needed. Relevant
dangling references, duplicate book/author, book/identifier-type and book/format
rows, unsupported format tokens, and negative sizes are errors. Format tokens
are uppercased and restricted to ASCII letters, digits and underscore (including
ORIGINAL_EPUB); no supported ebook-format catalog is claimed.

Schema compatibility is structural, not inferred from user_version (which is
reported). Older incompatible schemas fail clearly and are never migrated.
The synthetic writer in `examples/calibre-dupes/synthetic.py` creates independently written
minimal real SQLite tables for this projection, without copying upstream SQL,
triggers or full-library behavior. These fixtures are not usable Calibre libraries
for editing, conversion or ebook reading.

Input is one or two explicit library directories or metadata.db files. A
metadata.db symlink escaping its explicit library directory is refused. The
reader opens a percent-encoded file URI with mode=ro and query_only enabled,
disables trusted schema, uses a read transaction and closes its connection.
No SQLite writes, ATTACH or extension loading is performed. By default no
filesystem book-path traversal occurs. The database hash is calculated while the read snapshot holds a
shared lock. Two libraries are read independently, not in one global snapshot.

Close Calibre and other writers first and use clean rollback-journal databases.
WAL mode (including a WAL header with no sidecar), -wal, -shm and -journal states
are explicitly refused. mode=ro alone can create WAL shared memory; immutable=1
could ignore live journal content and bypass locks. Neither behavior is an
acceptable shortcut here. The tool does not checkpoint, recover, change journal
mode or prepare copies of production databases. A user-managed consistent
rollback-mode copy can be supplied. Concurrent filesystem replacements or journal
mode switches are outside the closed-library contract.

The format matrix is derived **only** from data.format, including when optional
file verification is enabled. It does not itself assert file availability.
By default data.name, data.uncompressed_size and books.path are uninterpreted
evidence and same-format overlaps are content-unverified.

## Candidate-pair budget API

`find_pairs(..., max_pairs=N)` and `make_plan(..., max_pairs=N)` accept only
exact positive Python `int` values, excluding `bool`; the default is 100000.
None, floats (including integral values, NaN and infinity), strings, containers,
zero and negatives raise `ReviewError("--max-pairs must be a positive integer")`
before candidate indexing/enumeration, pair evidence or format verification.
`make_plan` validates all budget arguments before checking library shape,
database identity/stat, or traversing books. Pair-budget validation runs first,
then verification-flag type, declaration-count type/range and opt-in, then byte-budget type/range and
opt-in (the same verification-budget order as direct `verify_libraries`).
Valid budgets retain the existing library-shape and duplicate-database errors.

The budget counts unique blocked candidates, including candidates rejected by
the later evidence check; a shared title and ISBN do not count the same pair
twice. A single input compares within its library; two inputs compare only
across them. Exact fit succeeds; overflow aborts without truncation or a partial
plan. CLI integer parsing and exit 2 / empty stdout / no report on failure are
unchanged.

Direct `find_pairs(books, cross_library=value)` requires an exact `bool` scope
flag. `False` retains within-input candidate enumeration; `True` compares only
A/B library candidates. Strings (including `"false"`), integers 0/1, None,
containers and other nonboolean values raise
`ReviewError("cross_library must be a boolean")` without coercion or truthiness
evaluation, even with empty books or omitted/explicit valid pair budgets.
The existing `max_pairs` type/range guard runs first, then the scope guard,
before book traversal, index allocation, candidate enumeration or evidence.
The scope keyword remains required; `make_plan` and CLI still derive it from
the number of input libraries.

## Optional declared-file verification

Python `make_plan(..., verify_files=value)` and
`verify_libraries(..., enabled=value)` accept only exact `bool` flags. `False`
returns metadata-only verification without format I/O; `True` opts in.
`make_plan` defaults to `False`; direct `verify_libraries` still requires its
`enabled` keyword. Strings (including `"false"`), integers 0/1, None, containers
and other nonboolean values raise `ReviewError("--verify-files must be a boolean")`
without coercion or truthiness evaluation. This guard precedes verification
budget validation, library checks, database stat, book traversal, root/format
opens and payload reads, with omitted or explicit budgets. Valid bool flags
retain existing budget type/range/opt-in errors. CLI `--verify-files` remains a
no-value opt-in switch; it does not accept a boolean string argument.

Rechecked at the same pinned backend commit above: `format_abspath` (lines
1859–1887) first constructs library root / books.path / (data.name + '.' +
format.lower()). This is the only filename this tool checks with `--verify-files`.
It deliberately omits upstream's fallback directory scan and possible rename.
`format_hash` streams file bytes, while `format_metadata` observes filesystem
size; this tool independently computes observed size and SHA256, and separately
reports whether data.uncompressed_size agrees. No upstream code is copied.

The CLI refuses symlink aliases in the explicit input root and metadata.db in
verification mode. Each root component and each untrusted books.path component
is opened descriptor-relative with O_DIRECTORY | O_NOFOLLOW. Metadata path
components cannot be empty, '.', '..', or contain slash, backslash, NUL or colon;
data.name must be a single such component. No absolute paths, symlinks, filename
search or path repair is allowed. Final files are opened read-only with
O_NOFOLLOW | O_NONBLOCK, then fstat-checked for regular-file status before reads.
Reads are bounded to initial size, and identity/size/mtime/ctime
are compared before/after hashing. Observed changes invalidate evidence. This
does not establish an atomic filesystem snapshot or support concurrent renames.

`fileVerification.records` covers every declaration, including unmatched books.
Only `verified` records contain observed bytes and hash. Per-pair
`contentEvidence.formats` compares shared declared formats; a size or hash
difference is `different-bytes`, equal size and SHA256 is `same-bytes-sha256`, and
an unavailable/rejected side is `unverified`. `content-match` requires at least
one shared format and all shared formats to match. Complementary formats remain
declarations, and original strong/candidate levels remain metadata evidence.
Additive fields preserve schemaVersion 1. The synthetic verification fixture
in `examples/calibre-dupes/synthetic.py` supplies real local byte payloads; they are not ebooks.

`--max-total-file-bytes N` requires `--verify-files` and a nonnegative integer
byte count (zero allowed). Omission is unlimited. One counter spans every
declaration in every input library, including unmatched records and repeated
file references. After safe opening and signature checks, a file's observed
size must fit the remaining budget before any of its payload is read. Reads
never probe beyond that initial size; the final signature check detects growth.
Count actual returned bytes, including bytes of subsequently failed/changed
files, rather than declared sizes. Database reads/hashing do not count; this is
not a physical disk I/O, time, or memory limit. Exhaustion is a fatal review
error (exit 2, stderr diagnostic, empty stdout, no report), not a per-file status
or truncated result. Enabled `fileVerification` includes `maxTotalFileBytes`
(integer or null for unlimited) and `totalBytesRead`; disabled output is unchanged.
The Python `verify_libraries` and `make_plan` APIs accept `max_total_file_bytes`
only as None or an exact nonnegative int, excluding bool. Floats (including
integral floats, NaN and infinities), strings and other types raise ReviewError
with the nonnegative-integer diagnostic before verification root opens or format
inspection; no coercion. Explicit integer budgets still require verification.
This matches `max_file_records` validation and leaves CLI parsing unchanged.
`make_plan` performs the same checks before library validation, database stat,
book traversal or pair matching, so invalid options cannot trigger that work.

`--max-file-records N` independently caps declaration inspections across
all input libraries. It accepts a nonnegative integer, requires `--verify-files`,
and is unlimited when omitted. Traverse libraries in input order, then ascending
book id and format name. Each declaration counts, including unmatched books,
multiple formats per book, repeated paths, empty/missing files, unsafe/blocked
paths and other failed results. Before declaration N+1 enters `_inspect` (even
path validation, open or stat), fail with exit 2 and a diagnostic naming the
limit, next ref/format and prior count. No partial stdout or new report file.
Exact fit succeeds; zero succeeds only with no declarations. Enabled results
add `maxFileRecords` (integer or null) and `fileRecordsInspected` (equal to the
length of records); metadata-only output is unchanged. The Python API accepts
only an int excluding bool, or None. Both budgets can be combined; the byte
budget alone does not bound empty/missing/rejected inspections. Neither count
nor byte budgets bound root validation/opens, database reads/hashes, metadata
loading/matching, elapsed time or memory. Closed stable inputs remain required.


## Optional decision metadata and identity

Rechecked the same pinned metadata_sqlite.sql (SHA256 above): languages at lines
120–124 uses id INTEGER PRIMARY KEY / lang_code TEXT; books_languages_link at
23–28 uses id INTEGER PRIMARY KEY / book INTEGER / lang_code INTEGER (a foreign
key to languages.id, not a text language code). books.pubdate at line 11 is a
nullable TIMESTAMP. Read both language tables when either appears, validating
ordinary-table structure, affinity, sole id primary keys, storage types, links
and duplicate book/language links. Missing both tables means unknown languages;
a partial relation is refused. Read pubdate when present as TIMESTAMP or TEXT,
requiring text or NULL storage; absent/empty means unknown. Sort raw language
values for deterministic records. No date parsing, locale alias mapping, edition
field, custom-column adapter or version inference is invented. Other metadata
remains outside the projection. Matching strong/candidate levels still use the
original title/author/ISBN rules; language/date conflicts are assessed in the
keeper-specific manual plan.

Members include languages, publicationDate and memberSHA256, computed from the
entire emitted member without memberSHA256, using sorted-key UTF-8 JSON with
ensure_ascii=False, compact comma/colon separators and allow_nan=False. This is
metadata identity, not file content or authentication. decisionTemplate binds
all labels, resolved database paths and raw database byte SHA256 values plus
exact candidate ids/full member refs/hashes. --decisions always recomputes them
from the current libraries before producing mergePlan. File verification is an
independent opt-in and its current evidence is attached to each donor; file byte
changes alone do not invalidate metadata-only decisions.

Decision JSON schemaVersion 1 has exactly libraries, decisions and schemaVersion.
Libraries are ordered objects with label/database/databaseSHA256. Each decision
requires clusterId/members/keep and optionally accepts formatSources.
New templates include formatSources: {}; existing decisions without it remain
valid. Each member has exactly ref/memberSHA256.
Use a subset of whole candidate clusters, with one keeper belonging to each;
remove unused decisions. Member order is immaterial. Duplicate candidate entries
or members, missing members, unknown keys, malformed JSON/duplicate keys/nonfinite
values and wrong types are rejected. An empty decisions list is an explicit
no-op plan. Every result retains the input decision, zero-based index, input
file path/byte hash and links to original cluster records. The result never
contains copy/update/delete commands or executable library operations.

formatSources maps exact emitted format names to donor ref strings, e.g.
{"PDF":"B:2","MOBI":"B:1"}. It can select any subset of keeper-missing
declared formats, including formats with only one source. Each ref must belong
to the same candidate and declare that format; keeper-retained or unknown
formats, unknown/out-of-candidate/wrong-format refs and wrong types are errors.
The same full member hashes and database identities bind chosen donors; there
is no separate donor hash to refresh. Duplicate keys remain errors. All choices
are validated before any output, including choices in later decisions.

For an explicit choice, formatAdditions.from is that ref; sources still lists
all available declarations and their identities/current verification evidence.
status is proposed for a not-inspected or verified selected donor, otherwise
source-unavailable; never silently choose a different donor. Unselected formats
keep the default single-source or manual-source-selection behavior. The
format-source-selection-required manual review code remains only while at
least one format is unresolved. Other metadata, edition, overlapping-format
and indirect-pair warnings are retained. Selection is an auditable plan only;
it does not attest file availability in metadata mode or edition identity.
