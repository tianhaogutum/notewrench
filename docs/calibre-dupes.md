# `notewrench calibre-dupes`

Calibre libraries accumulate duplicate books: the same title imported twice, an EPUB in one
library and the PDF in another, or two editions that only look alike. `notewrench calibre-dupes`
reads one library (within-library duplicates) or two local libraries (cross-library duplicates)
read-only and writes an auditable JSON review plan: pair-by-pair title/author/ISBN evidence, a
format-complement matrix and conflicts. Use it before merging libraries or cleaning one up; after
you pick the books to keep, it validates your decision file against the current snapshot and
produces a read-only plan of the formats to add.

It **never merges, adds or deletes books, and never emits SQL or shell commands**. It needs no
Calibre installation, network access, account or API key.

Formerly the standalone `calibre-dupes` tool.

**Platforms:** Linux and macOS; Windows is not supported. The default metadata mode accepts
library paths through symlinks. `--verify-files` opens every path level with POSIX
directory descriptors and `O_NOFOLLOW` and refuses a library root with a symlinked ancestor; on
macOS, where `/tmp` and `/var` are symlinks, pass the real path (for example
`/private/tmp/library`) or use a directory under your home folder.

**Reads / writes:** reads each library's `metadata.db` read-only (and, only with
`--verify-files`, the declared format files) plus an optional decision file. It writes the JSON
plan to stdout or to a new `-o` file outside every library; libraries are never modified.

- [Quick start](#quick-start)
- [Inputs, output and exit codes](#inputs-output-and-exit-codes)
- [A reproducible real SQLite example](#a-reproducible-real-sqlite-example)
- [Matching and evidence levels](#matching-and-evidence-levels)
- [Optional read-only format-file verification](#optional-read-only-format-file-verification-posix)
- [Human decision files and the read-only format-addition plan](#human-decision-files-and-the-read-only-format-addition-plan)
- [Format contract and limitations](#format-contract-and-limitations)
- [Benchmark](#benchmark)
- [Python API details](#python-api-details)
- [Checked interoperability contract](calibre-dupes/format.md)

## Quick start

```sh
pip install notewrench
notewrench calibre-dupes --help
notewrench calibre-dupes library-a
notewrench calibre-dupes library-a/metadata.db library-b -o reviews/clusters.json
notewrench calibre-dupes library-a library-b --verify-files
```

`library-a` and `library-b` stand for your Calibre library directories (close Calibre first). To
try it on fictional sample libraries from a clone of the repository, see
[A reproducible real SQLite example](#a-reproducible-real-sqlite-example).

Requires Python 3.11+ with the standard `sqlite3` module; there are no third-party dependencies.

## Inputs, output and exit codes

One input compares records within that library; two inputs compare **only cross-library
records**. Each input must be an explicit library directory or a file named `metadata.db`. Close
Calibre and every other writer first, and use a clean rollback-journal database. WAL mode (even
without sidecar files), `-wal`, `-shm` and `-journal` states are reported as errors; the command
never repairs, checkpoints or changes the journal mode. If needed, make a consistent rollback-mode
copy yourself beforehand.

By default the JSON plan goes to stdout. `-o` only creates a **new file outside every input
library** and never overwrites an existing file; a sibling directory of the inputs is fine. Do not
redirect stdout into a production library. `-o` / `--output` accepts relative paths and expandable
`~` / `~user`. If the home directory of the output cannot be expanded, only the `RuntimeError` of
the output's `expanduser()` is turned into `cannot expand output path <path>: <reason>`: the error
goes to stderr, the exit code is 2, stdout is empty, and no report or traceback is produced. CLI
argument parsing, library count, budget opt-ins, verification input checks and locating every
input library (including the duplicate-database check) still come first; output expansion happens
before resolution, existence checks, database reads and plan generation. Existing files or
directories, symlinks (including dangling ones) and outputs inside a library are still refused, and
the final creation still uses exclusive mode.

Exit codes: `0` success; `2` for argument, lock, input or output errors, with the error on stderr
and no partial plan. If the stdout reader fails, it may already have received partial bytes; always
check the exit code.

`--timeout 1` is the default SQLite lock wait in seconds (0–30 allowed). `--max-pairs 100000`
limits the number of candidate pairs produced by the index. Exceeding it fails explicitly; candidates
are never truncated. Raising the limit increases memory and report size. The timeout limits lock
waiting only, not the total time of reading, hashing or matching.

## A reproducible real SQLite example

Run from a clone of the repository (`git clone https://github.com/tianhaogutum/notewrench`). The
first run uses `.demo/library-a`, `.demo/library-b` and `.demo/clusters.json`, which must not exist
yet. All records are fictional; mathematically valid ISBNs do not claim real registrations. The
sample only creates format records inside real SQLite metadata databases, with no ebook content.

```sh
python3 examples/calibre-dupes/synthetic.py sample .demo
notewrench calibre-dupes .demo/library-a/metadata.db .demo/library-b -o .demo/clusters.json
python3 -c 'import json; print(json.load(open(".demo/clusters.json"))["summary"])'
```

The result has 12 records and 5 matching pairs/review groups (1 strong, 4 candidate); the 2 records
with the same title but different authors do not form a group. The first review group contains these
fields; the others additionally hold the original records, normalized values, library paths and
database SHA-256 values:

```json
{
  "action": "review",
  "level": "strong",
  "formats": {"A": ["EPUB"], "B": ["PDF"]},
  "formatUnion": ["EPUB", "PDF"],
  "missingByLibrary": {"A": ["PDF"], "B": ["EPUB"]},
  "missingByBook": {"A:1": ["PDF"], "B:1": ["EPUB"]},
  "pairs": [{
    "a": "A:1", "b": "B:1", "level": "strong",
    "matchEvidence": ["isbn-equivalent", "title-normalized", "author-normalized"],
    "sharedISBN13": ["9780306406157"], "conflicts": [], "action": "review"
  }]
}
```

`examples/calibre-dupes/synthetic.py` takes one of `sample`, `verification`, `decisions` or
`donors` and a new directory, and prints the created `metadata.db` paths. It needs only the Python
standard library.

## Matching and evidence levels

- ISBN-10/13 must pass their checksums; ISBN-10 is converted to the 978 ISBN-13 for comparison. A
  978 ISBN-13 can be converted back to ISBN-10; 979 has no such equivalent. Only identifiers of type
  `isbn` are recognized; whitespace and hyphen separators are allowed, and nothing is guessed from
  URLs, prefixed strings or strings holding several ISBNs. Invalid values are kept and diagnosed.
- Titles and authors are normalized with NFKC, casefold, punctuation to spaces and whitespace
  folding; accents are kept, nothing is transliterated, names are not reordered, and there is no
  fuzzy edit-distance matching. Authors are compared as the complete normalized set, ignoring order;
  empty authors, whitespace and Calibre's `Unknown` placeholder never count as complete author
  evidence.
- **strong**: a valid shared ISBN, equal known titles, equal complete author sets and no identity
  conflict. This is the strength of the metadata evidence; it still only recommends review and does
  not guarantee the same edition or content.
- **candidate**: equal title and complete authors but no shared ISBN; equal title with missing
  authors; or a shared ISBN with conflicting title/authors. Invalid/conflicting ISBNs, missing
  authors or a missing title can never be strong. Equal titles with different known authors and no
  shared ISBN produce no match.
- Clusters are connected review groups of matching edges; **only the listed pairs have direct
  evidence**, and transitive connections are never treated as identity. `hasUnevidencedPairs`
  marks member pairs in the current comparison scope without evidence. ISBN, author and title
  disagreements inside a group are flagged separately; by default, identical formats are marked as
  content-unverified.

The output also contains `schemaVersion: 1`, the scope, library identities, a snapshot hash per
library, counts and library-wide diagnostics; anomalous ISBNs are never dropped silently, even when
their book has no match. Ordinary unmatched books are only counted, not exported one by one. The
format union/missing values are always **database declarations**; files are not checked by default.
Every book and library has a missing matrix; a library without a record in the group has an empty
format set.

## Optional read-only format-file verification (POSIX)

Only `--verify-files` reads ebook files. Each explicit input library directory (or the parent of a
`metadata.db`) is the only allowed root; no other libraries are discovered. Following the fixed
upstream contract, only `books.path / (data.name + "." + data.format.lower())` is verified;
alternative files are never searched for, renamed or repaired. Keep Calibre and other writers closed.
The option requires POSIX descriptor-relative, no-follow file opens (available on Linux and macOS);
unsupported platforms fail explicitly, and the default metadata mode has no such requirement.

The output keeps `schemaVersion: 1` and adds `fileVerification` and per-pair `contentEvidence`:

- Disabled: `fileVerification.enabled: false`, `evidenceLevel: "metadata-only"`, empty records; no
  format file is accessed, and the metadata matching and format matrix are unchanged.
- Enabled: every format declaration in every library (including unmatched books) gets a
  verification record. `verified` records give the actual `bytes`, `sha256`, `declaredBytes` and
  `declaredSizeMatches`. The declared size is never used to infer content; a differing actual size
  still keeps the measured hash. Missing files are `missing`, unreadable ones `unreadable`,
  non-regular files `not-regular`, other read errors `read-error`.
- For every shared format of a matched pair: `same-bytes-sha256` (equal actual size and SHA-256),
  `different-bytes` (size or hash differs) or `unverified` (at least one side could not be
  verified). Records are linked by `ref` / `format`; each pair also reports `aStatus` / `bStatus`.
- `contentEvidence.level`: `content-match` only if every shared format is equal; `content-different`
  if at least one is confirmed different; otherwise `content-incomplete`. Without shared formats or
  with verification disabled it is `metadata-only`. It is independent of the existing
  `level: strong/candidate` **metadata level**, adds no matching pairs and never treats indirect
  cluster connections as content evidence. Identical content does not prove edition identity or
  that the ebook is valid.

Absolute paths, `..` / `.` / empty components, backslashes, colons, NUL or a `data.name` containing a
directory are reported as `unsafe-path`; symlinked directories and files (including links inside the
library and dangling links) are `blocked-path`. Directory descriptors and `O_NOFOLLOW` are used at
every level and only regular files are read; a FIFO never waits for a writer. If any level of an
explicit input root, or `metadata.db` itself, is a symlink, verification fails immediately (exit 2,
no JSON, no output file). `metadata.db`, book content and directories are never modified; ordinary
reads may update atime. Per-file problems such as missing files or refused paths are reviewable
results with exit 0, never fake hashes. If size/mtime/ctime/identity changes during a read the
status is `changed-during-read` and no hash is emitted; this is not an atomic snapshot across files,
and concurrent directory replacement is unsupported. Verification streams every declared file and
adds disk I/O.

On macOS, `/tmp` and `/var` are symlinks to `/private/...`. Because the root is opened without
following symlinks, pass the real path (for example `/private/tmp/library`) when a library lives
there.

`--max-total-file-bytes N` caps the format-file bytes read across **all input libraries** in one
verification. N is a non-negative integer (bytes) and requires `--verify-files`; without it, reads
are unlimited. For every safely opened regular file, the actual size is checked against the
remaining budget first; if it would exceed it, the command exits `2` before reading that file's
content, naming the budget and the ref/format on stderr, with empty stdout and no `-o` report. The
verification is never truncated and no partial plan is printed; using the budget exactly succeeds.
`0` allows empty files and missing/refused results that need no content read, and fails at the first
non-empty readable file.

With verification enabled, the full result has `fileVerification.maxTotalFileBytes` (null without a
budget) and `totalBytesRead`. The count is actual bytes read, not database-declared sizes; bytes read
from unmatched books, files declared more than once, and files found changed or failing after a read
all count. SQLite reads and database snapshot hashing do not count, and the budget limits neither
physical read-ahead, run time nor memory; it only limits content bytes returned from format-file reads.

`--max-file-records N` additionally caps the number of format declarations verified across **all
input libraries**. N is a non-negative integer and requires `--verify-files`; without it the number
is unlimited. Declarations are counted once each, in input-library order, book id order and format
name order, including unmatched books, empty files, missing/unsafe/blocked paths and other
verification failures; multiple formats count separately and repeated references to the same file
are not deduplicated. **Before** the path check or open of declaration N+1 the command exits `2`,
naming the budget and the next ref/format on stderr, with empty stdout and no report. Exactly N
succeeds; `0` only succeeds when there are no format declarations. The full result with verification
gains `maxFileRecords` (null when omitted) and `fileRecordsInspected` (the number of declarations
verified, equal to the length of the records); the default metadata-mode output is unchanged. The
record and byte budgets can be used independently or together: a byte budget of `0` still allows
empty/missing/refused files, and the record budget limits that work. Neither limits root checks,
database reads/hashing, metadata loading/matching, run time or memory.

The following entirely fictional SQLite + byte-file sample contains three pairs of books. Use sample
library directories that do not exist yet:

```sh
python3 examples/calibre-dupes/synthetic.py verification .demo/verify
notewrench calibre-dupes .demo/verify/library-a .demo/verify/library-b --verify-files --max-total-file-bytes 39 --max-file-records 6 -o .demo/verify/plan.json
python3 -c 'import json; p=json.load(open(".demo/verify/plan.json")); print([c["pairs"][0]["contentEvidence"] for c in p["clusters"]])'
```

| Title | EPUB in A | EPUB in B | Shared-format result | Content level |
| --- | --- | --- | --- | --- |
| Missing | declared, file missing | `present` | `unverified`, aStatus=`missing` | content-incomplete |
| Different | `AAAA` (4 bytes) | `BBBB` (4 bytes) | different-bytes | content-different |
| Same | `same content` (12 bytes) | same | same-bytes-sha256 | content-match |

The verification records also include `A:1 / EPUB / missing`; the other five files are verified. The
sample contains no real ebooks. It reads exactly 39 bytes, so a budget of 39 succeeds; with
`--max-total-file-bytes 38` and a new report path the command exits 2 with no JSON and no report.
The six declarations include one missing file, giving `maxFileRecords: 6` and
`fileRecordsInspected: 6`; changing only to `--max-file-records 5` exits 2 before the path check of
B:3/EPUB, again with no JSON or report. Without `--verify-files`, `--max-total-file-bytes 39` and
`--max-file-records 6`, all three pairs report only metadata-only, and the metadata review completes
even though files are missing.

## Human decision files and the read-only format-addition plan

The default review result includes a `decisionTemplate`. Save that object as a separate JSON file
and choose the `keep` (for example `"A:1"`) of each group; **delete the decisions you do not want to
handle** rather than leaving null. You may choose only some groups; an empty decisions list plans no
groups. Do not modify libraries, clusterId or members. Then run `--decisions decisions.json` with
**the same libraries in the same order**. A library directory and its `metadata.db` are
interchangeable; the resolved absolute database path is compared.

The example below uses `.demo/decisions/library-a`, `library-b` and report files that do not exist
yet; all are local fictional SQLite projections without ebook content. The first group has
complementary EPUB/PDF formats, the second an explicit conflict that needs manual handling:

```sh
python3 examples/calibre-dupes/synthetic.py decisions .demo/decisions
notewrench calibre-dupes .demo/decisions/library-a .demo/decisions/library-b -o .demo/decisions/review.json
python3 - <<'PYDECISIONS'
import json
from pathlib import Path
base = Path(".demo/decisions")
template = json.loads((base / "review.json").read_text())["decisionTemplate"]
for decision in template["decisions"]:
    decision["keep"] = next(m["ref"] for m in decision["members"] if m["ref"].startswith("A:"))
with (base / "decisions.json").open("x", encoding="utf-8") as stream:
    json.dump(template, stream, ensure_ascii=False, indent=2)
PYDECISIONS
notewrench calibre-dupes .demo/decisions/library-a .demo/decisions/library-b --decisions .demo/decisions/decisions.json -o .demo/decisions/merge-plan.json
```

`mergePlan.action` is `"plan-only"`. The first item keeps the EPUB of `A:1` and its
`formatAdditions` gives `{"format":"PDF","from":"B:1","status":"proposed",...}` with empty
`metadataConflicts`. The second item keeps `A:2` with the PDF from `B:2`, but its
`metadataConflicts` lists title, authors, languages (eng / deu) and publicationDate
(2020-01-01 / 2024-01-01), each with `action: "manual-review"`. `manualReview` contains
`edition-evidence-conflict`: different editions or languages must not be merged blindly.

- The decision template pins schemaVersion 1, the library labels, resolved database paths and the
  SHA-256 of the original database bytes, plus the **complete** member refs/memberSHA256 of each
  candidate group. Every run re-reads the libraries and recomputes candidates rather than trusting an
  old report. Any database byte change (including records outside the group), a different library
  order/path, or mismatching candidate group members or member hashes exits 2 with empty stdout and
  no output file. Review and choose again; do not refresh hashes by hand to bypass the check. This
  detects stale or misused decisions; it **is not a signature or tamper-proof authentication**.
- `memberSHA256` is the SHA-256 of the member record without this field, as key-sorted UTF-8 JSON
  with compact separators, `ensure_ascii=False` and `allow_nan=False`; it covers ref, original and
  normalized metadata, format declarations, languages and publicationDate. It does not include ebook
  bytes, and by default files are not checked for existence or changes. For current format-file
  evidence, add `--verify-files`; a missing file marks a single source as `source-unavailable` and
  explicitly requires manual handling; fake hashes are never produced.
- Every planned item records the zero-based `decisionIndex` and the complete `inputDecision`; the top
  level records the resolved path and **original-bytes** SHA-256 of the decision file. ref/memberSHA256
  trace each item back to the input records in the same output's `clusters.members`. Every format
  source also carries the original formatRecord and its current verification status.
- Only database-declared formats missing from the keeper are proposed. When several members offer the
  same missing format, `from` is null, the status is `manual-source-selection` and every source is
  listed; choose one explicitly with `formatSources` (below); nothing is picked automatically. Existing
  formats are always kept and never replaced; overlaps, content differences and missing direct match
  evidence all become manual review items.
- For the complete candidate group, disagreements in known titles, author sets, language code sets,
  valid ISBN sets and publication dates are listed with their original values and refs. Missing
  languages/publication dates are marked unknown. Languages are only stripped and casefolded, with no
  code alias mapping; publication dates compare the stripped raw strings, without inferring the same
  moment or edition. There is no standard edition field and no content parsing, so **every** item
  requires an `edition-identity-unverified` manual check; ISBN, title and publication-date
  differences are edition clues, not proof. Other unprojected metadata (custom edition fields,
  publisher, tags, comments) is neither merged nor claimed as reviewed.
- The JSON must have the template structure; unknown fields, duplicate keys, non-finite numbers, wrong
  types, invalid keepers, duplicate decisions and missing/duplicate members all fail without partial
  results. The report is created only after every check has passed. Decision files are read-only;
  nothing is executed, written back, deleted, copied across libraries or turned into commands.

### Explicitly choosing the source of a missing format

Each decision may have an optional `formatSources` object; new templates default to `{}`, and older
files without the field still work. Keys must be format names **exactly** as reported; values are
member refs of the same group that declare that format, for example `{"PDF":"B:2","MOBI":"B:1"}`. You
may choose some formats only; unchosen multi-source formats still need manual selection. A single
source may also be chosen explicitly. Formats the keeper already has, wrong formats and members
outside the group are rejected. Chosen sources are bound by the same database snapshot and complete
member ref/memberSHA256 checks; wrong or stale choices exit 2 with empty stdout and no report.

The following creates a fictional SQLite sample in `.demo/donors` (which must not exist) without
ebook content: B:1 and B:2 both declare PDF/MOBI, B:3 only EPUB, and B:4 is an unrelated book outside
the group:

```sh
python3 examples/calibre-dupes/synthetic.py donors .demo/donors
notewrench calibre-dupes .demo/donors/library-a .demo/donors/library-b -o .demo/donors/review.json
python3 - <<'PYDONORS'
import json
from pathlib import Path
base = Path(".demo/donors")
template = json.loads((base / "review.json").read_text())["decisionTemplate"]
template["decisions"][0].update(keep="A:1", formatSources={"PDF":"B:2", "MOBI":"B:1"})
with (base / "decisions.json").open("x", encoding="utf-8") as stream:
    json.dump(template, stream, ensure_ascii=False, indent=2)
PYDONORS
notewrench calibre-dupes .demo/donors/library-a .demo/donors/library-b --decisions .demo/donors/decisions.json -o .demo/donors/merge-plan.json
```

`mergePlan.action` is still `plan-only`; the two additions are MOBI / from B:1 / proposed and
PDF / from B:2 / proposed. Each `sources` list keeps the records, identities and current verification
evidence of B:1 and B:2; `inputDecision.formatSources` keeps your original choice, and `manualReview`
no longer contains `format-source-selection-required` (other warnings such as edition or indirect
matches remain). Choosing B:3 (which lacks the format) or B:4 (outside the group) as the PDF source is
rejected. With `--verify-files` the files of this sample are missing, so the chosen sources are
`source-unavailable`; another readable source is never substituted. A choice is not an executable
operation and proves neither the same edition nor valid content.

## Format contract and limitations

The real upstream `backend.py` and `resources/metadata_sqlite.sql` were checked; the pinned commit,
file hashes and field notes are in [the interoperability contract](calibre-dupes/format.md). Calibre
is not imported and none of its source or SQL is copied. The required field types, primary keys and
actual data integrity of the five core relational tables are validated; real Calibre
`languages` / `books_languages_link` and `books.pubdate` are read optionally (see the contract).
Other extra columns and tables may exist but are not interpreted; incompatible schemas fail clearly.
The whole library is not claimed to be validated. The samples are not complete libraries that
Calibre could edit or read.

SQLite is opened with `mode=ro`, `query_only` and one read transaction per library; the lock-bypassing
`immutable` mode is not used. Templates, plugins, pickles and scripts are never parsed or executed; by
default `books.path` and `data.name` are only kept as raw evidence, and with verification enabled only
declared files inside the root are read under the safety rules above. A `metadata.db` symlink escaping
its explicit library directory is refused. Each library takes its own snapshot; there is no global
atomic snapshot across two libraries, and replacing files or switching journal mode during a run is
unsupported.

Everything is computed in memory; space grows with the number of books, indexes, candidate pairs and
the report. Huge buckets of a common shared title can trigger the budget failure. Duplicates with
different titles and no shared ISBN, author spelling/order differences, volume/translation/edition
identity, and files in the directory that are not registered in the database are never resolved
automatically. The manual plan lists projected language/edition clue disagreements. There is no
merge/add/delete function.

[Find Duplicates](https://github.com/komali2/calibre-find-duplicates) and
[CalibreQuarry](https://github.com/VirInvictus/CalibreQuarry) already provide related duplicate
detection. This command's distinct scope is a read-only comparison of two local libraries, a
format-complement matrix, explicit evidence levels and a review plan without executable instructions.
No performance comparison with these tools was run, and no claim is made that duplicate detection
lacks tools.

## Benchmark

```sh
python3 examples/calibre-dupes/benchmark.py --groups 100 --repeats 3
```

[`examples/calibre-dupes/benchmark-results.json`](../examples/calibre-dupes/benchmark-results.json)
is a measured result: 100 fixed groups, 900 synthetic records per library and 500 labelled positive
pairs; every other cross-library pair is negative. ISBN/normalized matching uses indexes to avoid the
full Cartesian product. The baseline also uses an index to produce candidates by **raw title
equality**, not a deliberately slow double loop. Timing covers matching only, and both algorithms
share one read; baseline then matcher run sequentially 3 times without warm-up, and the shared read
time is reported separately. The fixture SHA-256, every timing, comparison counts and the run
environment are stored in the JSON; replays should give the same counts/fixture hash while timings
vary.

| Method | Output pairs | TP / FP / FN | precision | recall | median matching time |
| --- | ---: | --- | ---: | ---: | ---: |
| raw title equality baseline | 600 | 400 / 200 / 100 | 0.6667 | 0.8 | 0.536 ms |
| ISBN + normalized title/author, incl. candidates | 700 | 500 / 200 / 0 | 0.7143 | 1.0 | 25.111 ms |
| strong subset of the above | 100 | 100 / 0 / 400 | 1.0 | 0.2 | not timed separately |

The matcher examines 800 pairs out of a theoretical 810,000 cross-library pairs; the baseline outputs
and examines 600. The matcher is **slower than this simple baseline**, in exchange for recall and
conflict evidence on this sample. Labels are predefined by fictional work identities and include
Unicode differences, same title with different authors, wrong/conflicting ISBNs, missing authors and
ISBN misuse; the two conflict scenarios kept among candidates produce 2 false positives per group.
This is a small synthetic experiment and says nothing about accuracy on real libraries, general speed
or advantages over plugins.

## Python API details

The subpackage is importable as `notewrench.calibredupes`; the functions below live in its
`reader`, `files` and `compare` modules.

`locate_library(value)` / `read_library(value, ...)` and `validate_verification_input(value)` accept a
`str`, a `pathlib.Path` or an `os.PathLike` whose `__fspath__()` returns text (a library directory or
`metadata.db`). `None`, booleans, numbers, bytes, containers, plain objects and `__fspath__`
implementations returning bytes or non-text raise
`ReviewError("library path must be a string or a path-like object returning a string")` before any
filesystem, journal/header, SQLite or metadata access. Unsupported objects are never converted to
strings; only the `TypeError` of path construction is converted, and valid text paths keep their
original not-found, permission and database-state diagnostics. `OSError` / `RuntimeError` while
locating in `locate_library` / `read_library` become
`ReviewError("cannot locate library <path>: <reason>")`, keeping the original exception as the cause;
the diagnostic uses the text obtained by the path pre-check (before expansion/absolute conversion),
without calling the original PathLike's `__str__` or `__repr__` or calling `__fspath__` again.

If any part of a text path (including parent directories and the file name) contains NUL (`\0`),
`ReviewError("library path must not contain NUL characters")` is raised before expanding `~`,
converting to an absolute path, resolve/stat, journal/header, SQLite or metadata access, so the
filesystem's `ValueError` never leaks. `read_library` validates the timeout first, then the path type
and NUL, then locates and reads the library. `validate_verification_input`, which `--verify-files`
calls before locating libraries, uses the same pre-check and rejects invalid types/NUL before
expansion, absolute conversion, `is_dir`, the POSIX capability check, opening the root descriptor and
the `metadata.db` check. If the home directory of `~` / `~user` then cannot be expanded, the
`RuntimeError` of `expanduser()` becomes `ReviewError("cannot expand library path <path>: <reason>")`
(keeping the cause), failing before absolute conversion, filesystem or descriptor access; the
diagnostic uses the validated path text. `--verify-files` therefore exits 2 with empty stdout, no
report and no traceback. Valid paths are still checked level by level without following symlinks, and
symlinks at any level of the root or for `metadata.db` are refused. CLI argument parsing, input library
count and budget requirements still come before the path pre-check. OS command-line arguments cannot
contain NUL; that boundary is covered through the Python API and in-process `cli.main([...])` tests.

`read_library(path, label="A", timeout=1.0)` limits `timeout` to 0–30 seconds inclusive and accepts only
exact built-in `int` / `float` values, excluding `bool` and numeric subclasses. The default `1.0`, integer
or float zero, fractions and `30` are valid; negatives, values above 30, NaN/infinity, `None`, strings,
containers and other types raise
`ReviewError("timeout must be a finite int or float between 0 and 30 seconds (excluding bool)")`. There
is no type conversion or truthiness evaluation; the timeout is validated before locating the input,
stat, journal/header checks, connecting to SQLite or traversing metadata, so an invalid timeout takes
precedence over a missing library, journal, corruption or schema errors.

`find_pairs(..., max_pairs=N)` and `make_plan(..., max_pairs=N)` accept only a positive `int` (not
`bool`); `None`, floats (including NaN/infinity), strings, containers, zero and negatives raise
`ReviewError` with `--max-pairs must be a positive integer` before candidate indexing/enumeration,
without conversion. The default is still `100000`. The budget counts unique candidate pairs produced by
the index, even when an author conflict means they do not match in the end; a pair sharing both title
and ISBN counts once. `make_plan` checks every budget argument first, then library labels, duplicate
databases and book expansion, so an invalid budget never triggers a database stat or book traversal.

`find_pairs(books, cross_library=value)` requires an exact `bool` for `cross_library`: `False` keeps
within-input candidate pairs, `True` compares only candidates between libraries A and B. Strings
(including `"false"`), the integers 0/1, `None`, containers and other non-booleans raise
`ReviewError("cross_library must be a boolean")` without conversion or truthiness evaluation.
`max_pairs` is checked first, then the scope flag, both before book traversal, index construction,
candidate enumeration and evidence computation. `make_plan` and the CLI still choose the scope from the
number of input libraries.

`make_plan(..., verify_files=value)` and `verify_libraries(..., enabled=value)` accept only an exact
`bool` verification switch: `False` disables, `True` enables; `make_plan` still defaults to `False`.
Strings (including `"false"`), the integers `0`/`1`, `None`, containers and other non-booleans raise
`ReviewError("--verify-files must be a boolean")` without conversion or truthiness evaluation, whether
or not budgets are given, before library checks, database stat, book traversal, root/format opens and
content reads. The CLI's `--verify-files` remains an argument-less switch.

In `verify_libraries` / `make_plan`, `max_total_file_bytes` and `max_file_records` accept only `None`
(unlimited) or a non-negative `int` (not `bool`). Floats (even `1.0`), NaN/infinity and strings are not
converted; invalid values raise `ReviewError` ("must be a nonnegative integer") before the verification
root is opened and before format path checks. Explicit integer budgets still require verification to be
enabled. `make_plan` performs these checks before library checks, database stat and book traversal; the
diagnostic order is the candidate-pair budget, the verification switch type, the record budget
(type/range, switch), then the byte budget (type/range, switch). Valid budgets keep the existing library
checks and results.
