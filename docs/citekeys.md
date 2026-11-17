# `notewrench citekeys`

When a reference manager regenerates citation keys (a new Better BibTeX key pattern,
a re-imported library), every Markdown manuscript that cites the old keys breaks, and
a global search-and-replace also rewrites code, e-mail addresses, prose and keys that
merely share a prefix. `notewrench citekeys` matches two CSL JSON exports **by DOI or
exact URL**, then edits only supported Pandoc bracket citation key spans. The default
is a read-only JSON plan with the mapping, conflicts, unresolved keys and unified diffs;
output copies always go to a new directory, never over your sources.

**Which command?** Use `citekeys` when your bibliography is a CSL-JSON export and you
want rewritten copies of the Markdown files. If you keep BibTeX/BibLaTeX exports or
Better BibTeX JSON snapshots with stable item IDs and want a read-only review bundle and
a unified patch to apply yourself, use [`notewrench bibtex-keys`](bibtex-keys.md) instead.

Formerly the standalone `citekey-rekey` tool (Node.js); this is a byte-compatible
Python port (see [Compatibility with citekey-rekey](#compatibility-with-citekey-rekey)).
Python 3.11+, standard library only. No Zotero installation, account, network, Pandoc
executable or external program is used.

**Reads / writes:** reads the two CSL JSON exports and the Markdown files you name;
never modifies them. By default it only prints a JSON plan on stdout. With `--out-dir`
it creates one new directory holding edited copies (an existing path is refused).

**Platforms:** Linux and macOS; Windows is not supported. Every component of the
export, `--root`, input and `--out-dir` parent paths is checked, and a path with a
symlinked ancestor is refused (the default `--root` is the current directory). On
macOS `/tmp` and `/var` are symlinks, so work under `/private/tmp/...` or your home
folder. See [Output safety](#output-safety).

## Quick start

```sh
pip install notewrench
```

The examples below use the synthetic inputs under
[`examples/citekeys/`](../examples/citekeys/), so run them from a clone of the repository:

```sh
notewrench citekeys --help
notewrench citekeys --old examples/citekeys/old.json --new examples/citekeys/new.json examples/citekeys/paper.md
```

For the bundled paper, the plan contains these edits:

```text
See [@doe2020; -@doe2020-extra]. Exact keys: [@doe2020-extra].
→ See [@Doe20; -@Field-Notes]. Exact keys: [@Field-Notes].

Simultaneous migration: [@chainA; @chainB].
→ Simultaneous migration: [@chainB; @chainC].
```

The code, e-mail and prose occurrences of the same strings remain unchanged.
To create copies, choose a directory that **does not exist**, whose parent exists:

```sh
notewrench citekeys --old examples/citekeys/old.json --new examples/citekeys/new.json \
  --root examples/citekeys --out-dir migrated-examples paper.md
```

This creates `migrated-examples/paper.md`. Input paths are relative to `--root`
(default: current directory); absolute inputs inside that root also work. Export
paths and `--out-dir` resolve relative to the current directory, independently of
`--root`. Pass explicit filenames; directory recursion and glob expansion inside
the command are not supported. Use `--` before filenames starting with `-`.

For a deliberate exit-1 review example (unknown key, braced key, author-in-text):

```sh
notewrench citekeys --old examples/citekeys/old.json --new examples/citekeys/new.json examples/citekeys/review.md
```

`python examples/citekeys/demo.py` runs both modes on copies in a temporary directory
and checks the exact output bytes. `python -m notewrench.citekeys` is equivalent to
`notewrench citekeys`.

## Options

| Option | Meaning |
| --- | --- |
| `--old FILE` | Old CSL JSON array; `id` is the bibliography key |
| `--new FILE` | New CSL JSON array; match DOI first, then exact URL |
| `--root DIR` | Markdown input root (default: current directory) |
| `--out-dir DIR` | Write copies into a NEW directory; never overwrite sources |
| `--dry-run` | Explicit read-only mode (the default; incompatible with `--out-dir`) |
| `--help`, `-h` / `--version` | Show help / version |
| `--` | End options (for filenames beginning with `-`) |

Each option may be given once. Usage mistakes are reported like every other error,
as stderr JSON with code `USAGE` and exit status 2.

## Identity contract

The inputs are UTF-8 CSL JSON arrays of objects. `id` must be the actual bibliography
key used in your documents, not a Zotero internal item key. The command uses `id`, not
`citation-key`, `key`, item URLs inferred from titles, or application database IDs.

1. `id` is a nonempty string in the supported key grammar below. Duplicate ids on
   either side are errors. CSL permits numeric ids too; this command deliberately
   rejects them rather than guessing which external citekey they denote.
2. A `DOI` string is normalized by removing one leading HTTP(S) `doi.org/` or
   `dx.doi.org/` resolver prefix (case insensitive), then lowercasing the DOI. No
   whitespace trimming, `doi:` handling, percent decoding or suffix removal occurs.
   The accepted spelling is `10.` + digits + `/` + non-whitespace suffix; this is a
   shape check, not an assertion that a DOI is registered.
3. Otherwise match an explicitly equal `URL` string. URLs must be absolute HTTP(S)
   strings without whitespace. Their case, trailing slash, query and fragment are
   significant. There is no URL normalization, redirect lookup or network access.
   If a URL match has two different nonempty DOIs, it is a conflict.
4. Repeated normalized DOI or exact URL on **either** side, DOI/URL disagreement,
   many-to-one matching, or capture of an unmatched old key by a different migrated
   item refuses **the whole mapping**. This conservative policy also rejects shared
   landing URLs even if DOI could disambiguate them.
5. Missing identity or no match is reported in `unmatched`; names, titles, years and
   identical ids do not supply identity evidence. New-only items are not mappings.

Mappings include identity-confirmed unchanged keys. Key chains and swaps are valid:
all replacements use spans from the original document, never repeatedly rekeying a
replacement. A new key already present but absent from the old export is unresolved;
it is not silently assumed to identify the same item.

## Exact Markdown subset

This is a conservative scanner for prose in a bounded Markdown subset, including
explicit list/quote containers and one frontmatter field. It is not a complete
Pandoc parser. The prose group grammar is:

```text
KEY   = [A-Za-z0-9_]+(?:[.:-][A-Za-z0-9_]+)*
ITEM  = optional PREFIX, optional '-', '@', KEY, optional SUFFIX
GROUP = '[' ITEM (';' ITEM)* ']'
```

Groups occupy one physical line; spaces/tabs around items are retained. Every item
must contain exactly one unescaped `@` marker. A prefix must end in a space/tab
before `@` or `-@`. A suffix must begin with a space/tab or comma after the complete
key. Prefixes and suffixes are plain text, including Unicode and punctuation;
backslash escapes of ASCII punctuation are accepted and preserved without decoding.
Unescaped brackets, braces, backticks, emphasis markers (`*`/`_`), HTML delimiters
and dollar signs in affixes are outside this subset. Paired TeX math delimiters
remain opaque; groups containing them stay unsupported. Semicolons separate items;
use `\;` for a literal semicolon. An extra unescaped `@`, even in affix e-mail text,
is ambiguous. There is no locator interpretation or normalization.

With identity-confirmed mappings `A → B` and `B → Longer`:

```text
[see @A, pp. 4–6; -@B, chap. 2]
→ [see @B, pp. 4–6; -@Longer, chap. 2]
```

Only original key spans change; prefixes, locators, suffixes, separators and escapes
remain byte-identical. Unknown keys in otherwise supported groups stay unchanged
and are reported. The whole group is validated before any edit: `[@A @B]`,
`[@A, @B]`, `[@A; prose]`, `[@A; @B--part]`, `[@A; [@B]]`, and multiline or unclosed
groups remain whole, with one unsupported diagnostic covering their original range.

Examples without affixes remain supported: `[@doe2020]`, `[-@Doe-2020]`,
`[@a.b; -@x:y_2]`. Matching is case sensitive and uses complete keys. Repeated
punctuation, Unicode keys and braced keys are not accepted in exports. A key ending
in punctuation, such as `[@A.]`, remains ambiguous; use `[@A, ...]` or whitespace
before a suffix. This key grammar is intentionally narrower than Pandoc's.

Protected content:

- Fences with at least three backticks or tildes and up to three leading spaces
  relative to their list/quote container. The closing fence must use the same
  character, at least the opening length, and have only trailing spaces/tabs. An
  unclosed fence protects to the container end (EOF at top level). Backtick fence info
  strings cannot contain a backtick.
- Matching backtick code spans within a paragraph, including differing run lengths
  and multiline spans crossing tab/four-space-indented continuations. Closers may
  occur on those indented lines too. Blank lines (including whitespace-only lines),
  fences and reference definitions stop the search for a closer; unmatched backticks
  are literal. Lines beginning with a tab or four spaces relative to their container
  are conservatively always protected, including text after a closing backtick on
  that line.
- Ordinary e-mails, plain key text, escaped bracket literals, and reference
  definitions without citation candidates. Code contents are not inventoried as
  unresolved citations.

For example, with the identity-confirmed mapping `A → B`, this input:

```text
Opening `literal [@A]
    continuation
closing` then prose [@A].
```

becomes exactly:

```text
Opening `literal [@A]
    continuation
closing` then prose [@B].
```

Only the final key is edited. Citation-like text inside the code span does not
produce unresolved/unsupported diagnostics. If no matching closer exists in the
paragraph, the unmatched opener stays literal and prose citations remain eligible
for migration; a backtick in a later block cannot hide them.

Citation-like groups with ambiguous affixes, braced keys, nested brackets, newlines,
items with only escaped markers, link/image/reference/attribute contexts or inline
footnote contexts are left **whole and unchanged**. Recognizable citation markers in
these contexts are listed in `unsupported`. Author-in-text `@key` is also reported
unchanged. Adjacent groups separated only by spaces/tabs or one newline, such as
`[@a] [@b]`, are conservatively treated as a reference-link context; use
prose/punctuation between separate groups. Unknown keys in a supported group remain
unchanged and appear in `unresolved`.

Supported containers use `-`, `+`, `*`, or 1–9 digit ordered markers (`1.` or `1)`)
with 1–4 spaces of padding, and explicit `>` quote prefixes. They may nest; list
continuation lines must retain their content indentation. Blank list lines need not be
indented, including inside a fence. Code indentation is measured after those prefixes.
Five or more spaces after a list marker leave four spaces of protected code
indentation. Each new list item is a code-span boundary. Tabs in list markers and lazy
continuations without explicit quote markers/list indentation fail closed with exit 2.
Use a blank line before leaving a container for ordinary prose.

HTML elements, comments, declarations and HTTP(S)/mailto/e-mail autolinks are opaque.
Element matching respects quoted attributes, nested same-name elements and raw
`script`/`style`/`pre`/`textarea` contents. A line-start HTML region conservatively
protects through the first physical or quote-only blank line after its closing tag;
text on the closing line and following nonblank lines may therefore stay protected.
Inline HTML protects the entire element. Malformed/unclosed recognized HTML fails with
exit 2. Markdown-in-HTML extensions are not interpreted.

Matched `$...$`, `$$...$$`, `\(...\)` and `\[...\]` regions are opaque, including raw
TeX inside them. Dollar runs longer than two and unmatched/cross-block inline math
delimiters fail with exit 2; literal dollars must be escaped or in code. The existing
escaped bracket literal `\[@key]` remains protected. Line-start display math may span
blank lines and explicit container prefixes; it must close.

## Frontmatter and `nocite`

Initial frontmatter must start at offset zero (or immediately after one optional UTF-8
BOM) with `---` followed only by optional ASCII spaces/tabs on its physical LF/CRLF
line. It must close with `---` or `...` followed only by optional ASCII spaces/tabs, on
an LF/CRLF line or at EOF. All delimiter, padding, BOM and newline bytes remain
verbatim. Indented or malformed markers, non-ASCII padding, comments/content after a
marker and missing closers refuse the batch before stdout or copies. Later metadata
delimiters remain unsupported. Only a **flat mapping** of scalars and the bounded
`nocite` flow or block sequences below is supported.

For example, `--- \t\nnocite:\t' \t[@A; -@B]\t ' # opaque\n... \t\nBody [@A].`
becomes `--- \t\nnocite:\t' \t[@B; -@Longer]\t ' # opaque\n... \t\nBody [@B].`
with `A → B` and `B → Longer`; the escapes here denote literal tabs/newlines.
The same contract applies to a closing `---`.

Fields use ASCII names beginning with a letter, no duplicate names, one field per
line, and blank/comment lines. A field colon must be followed by one or more ASCII
spaces/tabs and a nonempty value; empty or comment-only values are unsupported. The
separator bytes are preserved exactly. Unknown scalar fields are opaque, including
citation-like text. Only the case-sensitive `nocite` field is scanned, and only when
its value is a single- or double-quoted scalar in either form below, or the flow or
block sequence defined afterward:

```text
WS      = zero or more spaces/tabs
ITEM    = optional '-', '@', KEY
BRACKET = WS '[' WS ITEM (WS ';' WS ITEM)* WS ']' WS
LIST    = WS ITEM (WS ',' WS ITEM)* WS
```

A `nocite` flow sequence has the grammar `'[' WS QUOTED (WS ',' WS QUOTED)* WS ']'`,
where `QUOTED` is exactly `'ITEM'` or `"ITEM"`. Quote styles may mix; each item
contains one literal bare marker without interior padding. ASCII spaces/tabs may
surround items, commas and the closing bracket, then precede an optional opaque `#`
comment or line end. One item, suppression and repeats work. For `A → B` and
`B → Longer`, `nocite: ['@A', "-@B", '@A'] # opaque` becomes exactly
`nocite: ['@B', "-@Longer", '@B'] # opaque`. Only original key spans change
simultaneously; all quotes/brackets/commas/tabs/comments/BOM/LF/CRLF/EOF bytes remain
verbatim. Unknown keys retain their original locations and exit 1.
Empty/trailing/missing items, unquoted/escaped/decoded/affixed/padded items, wildcards
(alone or mixed), nested/multiline sequences, non-ASCII separators and noncomment
suffixes refuse the whole batch with exit 2 before stdout or copies. Collections in
unknown fields remain unsupported.

A block `nocite` sequence is the other supported list form. The field line is
`nocite:` with no inline value, optionally followed by ASCII spaces/tabs and a `#`
comment. Each item starts on its own line with the same positive number of ASCII
spaces, then `-`, one or more ASCII spaces (no tabs), and exactly one single- or
double-quoted literal `@KEY` or `-@KEY`. ASCII spaces/tabs may follow the closing
quote, then an optional opaque `#` comment. The first item fixes the indentation. A
later item with different indentation, a tab in the indentation or after the dash, or
any other content refuses the batch. Blank lines and whole-line `#` comments at any
indentation may appear before, between or after items without changing that
indentation. The sequence ends at the first other nonblank, noncomment line, which
must be an unindented field or the initial `---`/`...` closer. A value on the
`nocite:` line, an empty sequence, an indented field, nested content or a missing
closer refuses the batch. For `A → B` and `B → Longer`:

```yaml
---
nocite:
  - '@A'
  - "-@B"
  - '@A' # opaque
title: "Study [@unknown]"
---
Body [@A].
```

becomes exactly the same document with `'@B'`, `"-@Longer"` and `'@B'`, and
`Body [@B].`. Only the key spans change; indentation, quotes, dashes, blank and
comment lines and line endings remain byte-identical. The `title` line stays opaque.

A complete bare-key bracket group inside either scalar quote style also accepts
leading/trailing ASCII spaces/tabs: `nocite: '  [@A; -@B]  ' # opaque` becomes exactly
`nocite: '  [@B; -@Longer]  ' # opaque`. Non-ASCII outer padding, any prefix/suffix
outside the group, affixes inside the group, malformed groups, and
bracketed/mixed/escaped/decoded wildcards refuse the whole batch with exit 2.

No affixes, escapes or decoded YAML spellings are accepted in `nocite`. A list can
contain one key or repeated keys; only complete original key spans change,
simultaneously. A standalone wildcard `@*` inside either quote style may have
leading/trailing ASCII spaces/tabs, for example `'  @*  '`; it stays byte-identical
and produces no key diagnostic. Non-ASCII whitespace, including U+2028/U+2029, is
never wildcard padding. Mixed wildcard lists (`'@*, @A'`, `'@A, @*'`),
trailing/empty/missing separators (`'@A,'`, `'@A,, @B'`, `'@A @B'`), escaped markers
and malformed keys refuse the whole document with exit 2 before any batch output.
Spaces/tabs may follow a closing scalar quote before an optional comment or line end;
any noncomment token after the closing quote refuses the batch. With `A → B` and
`B → Longer`:

```yaml
---
nocite: '@A, @B'
title: "Study [@A]"
custom: "[@unknown]"
---
```

Only `nocite` becomes `'@B, @Longer'`; `title` and `custom` stay byte-identical and do
not produce citation diagnostics. Nested mappings, collections other than the bounded
`nocite` sequences above, block or multiline scalars, aliases, anchors, tags, complex
keys, and unsupported `nocite` values refuse the whole document. Plain unknown scalars
may not begin with YAML structural indicators or contain brackets/braces or `: `;
double-quoted unknown scalars allow literal ASCII tabs and otherwise must use
JSON-compatible escaping. Validation never decodes or rewrites the scalar. A backslash
followed by a literal tab is still an invalid escape; raw C0 controls other than tab
and physical multiline values are rejected. After a nonempty plain unknown scalar, an
ASCII space/tab followed by `#` starts an opaque comment; hashes inside a scalar token
or quoted scalar do not start such a comment. U+2028/U+2029 stay literal content inside
scalars and comments on the same physical LF/CRLF line and never act as separators.
This is an explicit subset, not a YAML parser. TOML frontmatter, initial Pandoc `%`
titles, later metadata delimiters (including `---` horizontal rules), fenced divs, raw
TeX outside math, and citations in reference/footnote definitions cause exit 2 before
output creation.

## Reports and exit status

Stdout contains JSON with `tool`, `version`, `mode`, `mapping`, `conflicts`,
`unmatched`, `documents` and `written`. The report identity is kept from the original
tool (`"tool": "citekey-rekey"`, `"version": "1.0.0"`) so existing consumers keep
working. Each document contains `path`, `changed`, `edits`, `unresolved`,
`unsupported` and a `diff` string. Locations refer to the original text: line/column
are 1-based, offsets are 0-based **UTF-16 code units** (JavaScript string offsets, as
in the original tool; an emoji counts as 2). Unsupported diagnostics include `length`
in UTF-16 units: `[offset, offset + length)` covers the whole original bracket group,
including attached link/reference/attribute context and image/footnote markers when
present; unclosed groups extend to EOF. Each edit additionally has `byteOffset` and
`byteLength`, a 0-based UTF-8 byte range in the original input, including any BOM.
JSON escaping preserves CRLF in the diff string. Diffs use three context lines and
mark missing EOF newlines; they apply with `git apply`. They are review artifacts; the
command never invokes a patch program.

| Exit | Meaning | Output copies |
| --- | --- | --- |
| 0 | All identities/active citations resolved within this subset | Only with `--out-dir` |
| 1 | Unmatched old records, unknown keys, or unsupported citation candidates | Supported edits written if `--out-dir`; unresolved/unsupported spans stay intact |
| 2 | Invalid JSON/encoding/id, identity conflicts, unsupported document structure, argument/path/I/O error | None for validation failures |

Conflicts are in stdout JSON with an empty mapping; other fatal errors use stderr JSON
`{ "error": { "code", "message", "details" } }`. File-system errors use the errno name
as the code (for example `ENOENT`). Always check the exit code, even when JSON was
printed. An unused unmatched old item still produces exit 1.

## Output safety

Dry-run writes nothing, including no reports or temp files; redirect stdout yourself
if desired. Explicit `--dry-run` and `--out-dir` are mutually exclusive. Nothing is
ever rewritten in place. Output copies preserve valid UTF-8 bytes except the key spans,
including BOM, Unicode, LF/CRLF and absence of final newline; NUL, invalid UTF-8 and
bare CR are errors. Mixed LF/CRLF is accepted. File modes/timestamps are not copied.

Every input is validated before writes. `--out-dir` must be a new directory; files are
created exclusively under their root-relative paths, even for unchanged inputs.
Traversal components, backslashes/control characters in input paths, duplicate paths,
symlinks in input/output ancestor paths (on macOS, use `/private/tmp` rather than the
`/tmp` symlink), and nonregular input files are refused. Only `.md`/`.markdown` inputs
are supported. Each file is limited to 32 MiB; all documents/reports are held in
memory, so total batch memory is unbounded. Output is private (`0700` directories,
`0600` files). A failed write attempts to remove the newly created tree. Batch writes
are not crash-atomic; interruption may leave a partial new directory. This is not a
defense against hostile concurrent filesystem mutation between validation and access.
Keep inputs stable during a run.

## Python API

```python
from notewrench.citekeys import build_mapping, rekey_markdown

plan = build_mapping([{'id': 'a', 'DOI': '10.123/demo'}], [{'id': 'b', 'DOI': '10.123/DEMO'}])
if plan['conflicts']:
    raise SystemExit('Review identity conflicts first')
result = rekey_markdown('See [@a].', plan['mapping'])
# result['text'] == 'See [@b].'; inspect result['unresolved'] / result['unsupported']
```

The pure API performs no filesystem I/O. `rekey_markdown` accepts the mapping list or
a `{old: new}` dict; duplicate or unsupported keys raise `RekeyError` with code
`INVALID_MAPPING`. Callers must check `build_mapping()['conflicts']` themselves and
decide how to handle unresolved data. `unified_diff(before, after, name)` builds the
report diff.

## Benchmark

`python examples/citekeys/benchmark.py` compares the scanner with an explicit
baseline: sequential global literal replacement of each old key in export order. It
labels citation lines and protected lines independently, counts correct/missed
citation edits and unintended changed lines, and asserts the scanner's counts before
printing. On the 500 synthetic documents (18 labeled lines each):

| Measured on these labels | Global literal baseline | Syntax-aware scanner |
| --- | ---: | ---: |
| Correct citation edits | 1,000 | 2,000 |
| Missed/incorrect citation edits | 1,000 | 0 |
| Unintentionally changed protected lines | 4,000 | 0 |
| Documents exactly matching the line oracle | 0 / 500 | 500 / 500 |

The baseline fails on prefix overlap and the chainA→chainB→chainC cascade and corrupts
e-mail, prose, inline and fenced code, indented code and an ambiguous group. Timings
(one warmup, one in-memory pass) are printed but are not a speed claim; these short
synthetic documents do not establish real-corpus accuracy.

## Format notes and related tools

- [CSL input schema](https://github.com/citation-style-language/schema/blob/master/schemas/input/csl-data.json):
  an array of items with `id` (string or number), `DOI`, `URL` and a distinct
  `citation-key`. Only string ids already used as document citekeys are accepted;
  other fields are retained in exports but unused for matching. This is not a CSL
  schema validator.
- [Pandoc citation syntax](https://pandoc.org/MANUAL.html#citation-syntax) defines
  bracket citations, suppression, locators and a broader key spelling than accepted
  here. Consecutive punctuation and Unicode keys are deliberately excluded.
- [Better BibTeX](https://github.com/retorquere/zotero-better-bibtex) manages Zotero
  citation keys and exports ([Pandoc guide](https://retorque.re/zotero-better-bibtex/exporting/pandoc/));
  this command complements it by matching two local exports and updating external
  prose files. It does not manipulate live Zotero keys.
  [andrewheiss/pandoc-citekeys](https://github.com/andrewheiss/pandoc-citekeys)
  provides related Python helpers. No code from these projects is used.

URL identity equality is a deliberate application rule, not a claim that equal URLs
prove scholarly identity universally. Shared URLs, contradictory DOIs and all
one-to-many/many-to-one ambiguity fail closed. Curate exports first.

## Compatibility with citekey-rekey

The report, rewritten files, diffs and error codes are byte-identical to
`citekey-rekey` 1.0.0, with these exceptions:

- The program name in help text is `notewrench citekeys`.
- `BAD_JSON` messages carry Python's JSON parser wording instead of V8's. JSON nested
  deeper than Python's recursion limit is reported as `BAD_JSON`.
- `URL` validity reproduces the WHATWG URL rules that matter (host, port, IPv4/IPv6,
  percent-encoding); internationalized (non-ASCII) host names are checked with IDNA
  2003 instead of UTS #46, so rare exotic hosts may be judged differently.
- Unicode letter/digit classes (used to tell e-mail addresses from citations) follow
  the Python build's Unicode database rather than Node's ICU version.
