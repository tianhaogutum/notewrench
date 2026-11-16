# Third-party sources and attribution

notewrench is licensed under the GNU Affero General Public License, version 3 only
(AGPL-3.0-only; see [LICENSE](LICENSE)). The commands below were previously separate
AGPL-3.0-only projects by the same authors; their notices are preserved below, one section
per command, with paths updated to this repository's layout. `notewrench links` and
`notewrench citekeys` were first written for Node.js and ported to Python by those same
authors; the ports contain no third-party code. The LICENSE file is the unmodified GNU AGPL
version 3 text, Copyright (C) 2007 Free Software Foundation, Inc., reproduced from
<https://www.gnu.org/licenses/agpl-3.0.txt> under its verbatim-copying notice. The original
work is offered under version 3 **only**, not "or later".

Product names identify interoperable formats only; notewrench is not affiliated with or
endorsed by Zotero, Joplin, Logseq, Wallabag, Karakeep, Calibre, Pandoc or any other project
named here.

## At a glance

- **No runtime dependencies.** Every command uses only the Python standard library
  (Python 3.11+, installed separately under the Python Software Foundation License).
  The wheel contains only the `notewrench` package and these project notices.
- **No third-party source code, documentation prose, articles or models are vendored.**
  Upstream projects were consulted as format and behavior references only; the readers and
  writers are original implementations.
- **One bundled data table:** `src/notewrench/linkdedupe/_idna_data.py` (UTS #46 IDNA
  mapping and Joining_Type data, Unicode 17.0.0), used by `notewrench links` to map
  Unicode host names like the WHATWG URL Standard does. It is derived from the Unicode
  Character Database (Unicode License v3, [text](licenses/Unicode-3.0.txt)) as tabulated
  by the [idna](https://github.com/kjd/idna) package (BSD-3-Clause, Copyright (c)
  2013-2026 Kim Davies and contributors, [text](licenses/idna-BSD-3-Clause.txt)). See
  [`notewrench links`](#notewrench-links-formerly-linkdedupe) below. Both license texts
  ship in the wheel and sdist.
- **Examples and test fixtures are original synthetic data** authored for these tools
  (reserved example domains, invented IDs, dates and text), under the root license.
- Test-only dependency (`test` extra): [pytest](https://github.com/pytest-dev/pytest) (MIT)
  and its dependencies (iniconfig, pluggy: MIT; packaging: Apache-2.0 OR BSD-2-Clause;
  Pygments: BSD-2-Clause). Build-only tools: [hatchling](https://github.com/pypa/hatch) (MIT)
  and [hatch-fancy-pypi-readme](https://github.com/hynek/hatch-fancy-pypi-readme) (MIT).

## Contents

- [`notewrench zotero-logseq` (formerly zotlog-sync)](#notewrench-zotero-logseq-formerly-zotlog-sync)
- [`notewrench joplin-logseq` (formerly jex2logseq)](#notewrench-joplin-logseq-formerly-jex2logseq)
- [`notewrench wallabag-notes` (formerly bagnotes)](#notewrench-wallabag-notes-formerly-bagnotes)
- [`notewrench archive` (formerly bagkeep)](#notewrench-archive-formerly-bagkeep)
- [`notewrench epub` (formerly readbundle)](#notewrench-epub-formerly-readbundle)
- [`notewrench links` (formerly linkdedupe)](#notewrench-links-formerly-linkdedupe)
- [`notewrench joplin-dupes` (formerly joplin-dedupe)](#notewrench-joplin-dupes-formerly-joplin-dedupe)
- [`notewrench calibre-dupes` (formerly the standalone calibre-dupes)](#notewrench-calibre-dupes-formerly-the-standalone-calibre-dupes)
- [`notewrench citekeys` (formerly citekey-rekey)](#notewrench-citekeys-formerly-citekey-rekey)
- [`notewrench zotero-attachments` (formerly zotero-attach-audit)](#notewrench-zotero-attachments-formerly-zotero-attach-audit)

## `notewrench zotero-logseq` (formerly zotlog-sync)

Original implementation (`src/notewrench/zoterologseq/`), tests (`tests/zoterologseq/`)
and synthetic examples (`examples/zotero-logseq/`): Copyright (C) 2026 zotlog-sync
contributors; SPDX-License-Identifier: AGPL-3.0-only (version 3 only, no "or later"
option).

No upstream or competitor source code, third-party Python packages, article text,
real bibliographic records or application assets are incorporated into the
implementation, tests or fixtures. Runtime uses the Python 3.11+ standard library
only. The synthetic examples were authored for this tool and are not copied
upstream test data.

The following projects are referenced **only for documented-format
interoperability** and motivation. Their code is not linked, copied, translated,
vendored, or required at runtime:

| Project | Upstream license | Role |
| --- | --- | --- |
| [Zotero](https://github.com/zotero/zotero) | AGPL-3.0-only | CSL export workflow |
| [Better BibTeX for Zotero](https://github.com/retorquere/zotero-better-bibtex) | MIT | citekey/export workflow |
| [Logseq](https://github.com/logseq/logseq) | AGPL-3.0-only | Markdown file-graph format |

Format references (no schema or documentation is bundled), with the decisions they
inform in [docs/zotero-logseq.md](docs/zotero-logseq.md#format-decisions-and-sources):

- [CSL input schema](https://github.com/citation-style-language/schema/blob/master/schemas/input/csl-data.json)
- [citeproc-js CSL JSON documentation](https://citeproc-js.readthedocs.io/en/latest/csl-json/markup.html)
- [Logseq properties documentation](https://github.com/logseq/docs/blob/master/pages/Properties.md),
  pinned at [docs commit 628cdde](https://github.com/logseq/docs/blob/628cdde0b6f1d90c4722e2bfebb2fd00a5186825/pages/Properties.md)
- [Better BibTeX exporting](https://retorque.re/zotero-better-bibtex/exporting/) and
  [automatic export](https://retorque.re/zotero-better-bibtex/exporting/auto/)
- [Logseq 0.10.9 page extraction](https://github.com/logseq/logseq/blob/c67b8b5fa47f8fe1e1954226c9bdfabd46ebb968/deps/graph-parser/src/logseq/graph_parser/extract.cljc)
  and [filename/namespace utilities](https://github.com/logseq/logseq/blob/c67b8b5fa47f8fe1e1954226c9bdfabd46ebb968/deps/graph-parser/src/logseq/graph_parser/util.cljs) at commit c67b8b5
- [Logseq issue 13902](https://github.com/logseq/logseq/issues/13902) (motivation)

Related tools acknowledged in the documentation, with no material included:
[logseq-zoterolocal-plugin](https://github.com/benjypng/logseq-zoterolocal-plugin) and
[logseq-logtero-plugin](https://github.com/vyleung/logseq-logtero-plugin).

## `notewrench joplin-logseq` (formerly jex2logseq)

Source: `src/notewrench/joplinlogseq/`, tests in `tests/joplinlogseq/`, example in
`examples/joplin-logseq/`. The implementation, tests, example generator and the
generated synthetic fixture are original work, licensed **AGPL-3.0-only**
(Copyright (c) 2026 jex2logseq contributors). No third-party runtime, test or build
packages are used; only the Python 3.11+ standard library.

Interoperability references (consulted only; nothing bundled):

- **Joplin**, <https://github.com/laurent22/joplin>, **AGPL-3.0-or-later**. The raw/JEX
  exporter, item serializer, model types, resource filename rules and public Data API
  property documentation were read only to establish file-format interoperability:
  [JEX exporter](https://github.com/laurent22/joplin/blob/dev/packages/lib/services/interop/InteropService_Exporter_Jex.ts),
  [raw exporter](https://github.com/laurent22/joplin/blob/dev/packages/lib/services/interop/InteropService_Exporter_Raw.ts),
  [item serialization](https://github.com/laurent22/joplin/blob/dev/packages/lib/models/BaseItem.ts),
  [resource naming](https://github.com/laurent22/joplin/blob/dev/packages/lib/models/utils/resourceUtils.ts),
  [property reference](https://joplinapp.org/help/api/references/rest_api/#properties).
  No upstream implementation, article text, export fixture, logo or Joplin Server code
  was copied into the implementation or its fixtures.
- **Logseq**, <https://github.com/logseq/logseq>, **AGPL-3.0**. File graph work is now
  maintained separately in <https://github.com/logseq/og>. Public Logseq docs
  ([properties](https://github.com/logseq/docs/blob/master/pages/Properties.md),
  [built-in properties](https://github.com/logseq/docs/blob/master/pages/Built-in%20Properties.md),
  [tasks](https://github.com/logseq/docs/blob/master/pages/Tasks.md)) and file-graph
  conventions informed Markdown properties, page references and task markers. No Logseq
  implementation code or documentation prose is bundled.
- **Related tools**, <https://github.com/dasrecht/joplin-to-logseq-importer> and
  <https://github.com/htinaunglu/Joplin-to-Logseq-Integrater>: acknowledged related work.
  No source reuse and no vendored code; their license status is not used as permission
  to copy anything.

The synthetic PNG inside `examples/joplin-logseq/synthetic.jex` is generated from
original pixel data with Python's standard library (`struct` and `zlib`) by
`examples/joplin-logseq/make_synthetic.py`. No image, note text or attachment from
upstream or a real user is used.

## `notewrench wallabag-notes` (formerly bagnotes)

Original code under **AGPL-3.0-only**, copyright (C) 2026 the bagnotes contributors. No upstream
or competitor implementation is copied or bundled. No runtime packages, vendored libraries, paid
APIs, model files or network services are used; Python 3.11+ and its standard library are supplied
by the user under Python's own license.

Interoperability references only (no code or fixtures copied):

- [Wallabag](https://github.com/wallabag/wallabag), MIT: JSON export fields and serialization groups
  (pinned commit `2c15fef98033c43d86cf3dcdd97bfe53f3b4f22d`; import fixture, `Entry` and
  `Annotation` entities, `EntriesExport` helper).
- [Logseq](https://github.com/logseq/logseq), AGPL-3.0, and the
  [Logseq docs](https://github.com/logseq/docs) (pinned commit
  `628cdde0b6f1d90c4722e2bfebb2fd00a5186825`, `pages/Properties.md`): Markdown file-graph
  block/property syntax.
- [bagcarry](https://github.com/Rezarys/bagcarry), MIT: acknowledged related project; no code reused.

[docs/wallabag-notes/format.md](docs/wallabag-notes/format.md) records the source URLs and
observations. All examples under `examples/wallabag-notes/` and tests under `tests/wallabagnotes/`
are synthetic and were authored for this tool, not copied from upstream fixtures or articles.

## `notewrench archive` (formerly bagkeep)

Original Python implementation, tests, documentation and synthetic examples:
Copyright (C) 2026 bagkeep contributors, **AGPL-3.0-only**. No third-party Python
packages, competitor code, fonts, assets, upstream article prose or models are bundled.
Publication calls libc `renameat2` through the Python standard library's `ctypes`; no
extra package or executable is used.

Only format interoperability was studied:

| Project | Upstream license | Use |
| --- | --- | --- |
| [Wallabag](https://github.com/wallabag/wallabag) | MIT | Export fixture structure ([v2 fixture](https://github.com/wallabag/wallabag/blob/master/tests/fixtures/Import/wallabag-v2.json)), importer field expectations ([v2 importer](https://github.com/wallabag/wallabag/blob/master/src/Import/WallabagV2Import.php), [importer](https://github.com/wallabag/wallabag/blob/master/src/Import/WallabagImport.php)), [annotation fields](https://github.com/wallabag/wallabag/blob/master/src/Entity/Annotation.php), and the API pagination contract ([EntryRestController at 2c15fef](https://github.com/wallabag/wallabag/blob/2c15fef98033c43d86cf3dcdd97bfe53f3b4f22d/src/Controller/Api/EntryRestController.php), [tests](https://github.com/wallabag/wallabag/blob/2c15fef98033c43d86cf3dcdd97bfe53f3b4f22d/tests/functional/Controller/Api/EntryRestControllerTest.php)); no source or article copied into implementation/examples. |
| [Karakeep](https://github.com/karakeep-app/karakeep) | AGPL-3.0-only | [Native exporter schema](https://github.com/karakeep-app/karakeep/blob/main/packages/shared/import-export/exporters.ts) and seconds convention, [OpenAPI spec at 88a9ff9](https://github.com/karakeep-app/karakeep/blob/88a9ff9cfdfa2b99da4d6a700ea2d0055bd3639d/packages/open-api/karakeep-openapi-spec.json); no source copied into implementation/examples. |
| [ITU-T T.81](https://www.w3.org/Graphics/JPEG/itu-t81.pdf) | Specification | Baseline/progressive JPEG scan and entropy rules for the local image validator; no specification text or code copied. |
| [bagcarry](https://github.com/Rezarys/bagcarry), [wb2kk](https://github.com/fikovnik/wb2kk) | Not bundled | Related tools acknowledged only. |

All files in `examples/archive/` are newly authored synthetic records. Domains use
`example.org`; no production exports or personal data were used.

## `notewrench epub` (formerly readbundle)

Original implementation, tests, synthetic examples and documentation: Copyright (C)
2026 readbundle contributors, **AGPL-3.0-only**. There are zero third-party runtime or
build dependencies. No upstream implementation code, article text, font, image, model
or competitor code is bundled. The archive-bundle interoperability fixture includes
generated archive wrappers/diagnostics as described below; its article and image inputs
are original.

The following are format interoperability references only; their code was not
incorporated:

- [Wallabag](https://github.com/wallabag/wallabag), MIT. The official
  [v2 JSON fixture](https://github.com/wallabag/wallabag/blob/master/tests/fixtures/Import/wallabag-v2.json)
  establishes the array and HTML `content` structure. Article text is original.
- [Karakeep](https://github.com/karakeep-app/karakeep), AGPL-3.0-only. Its
  [native export schema](https://github.com/karakeep-app/karakeep/blob/main/packages/shared/import-export/exporters.ts)
  and [API bookmark types](https://github.com/karakeep-app/karakeep/blob/main/packages/shared/types/bookmarks.ts)
  establish field names and types. The adapters are original code.
- [EPUB 3.3](https://www.w3.org/TR/epub-33/) is a format reference. No specification
  text or example implementation was copied.
- [CommonMark 0.31.2](https://spec.commonmark.org/0.31.2/) is the pinned syntax
  reference for the optional restricted Markdown renderer. No parser code,
  specification prose or specification test fixtures are bundled; all Markdown
  fixtures are original synthetic notes. This is not a full CommonMark parser.
- [PNG Second Edition (2003-11-10)](https://www.w3.org/TR/2003/REC-PNG-20031110/) is
  the pinned binary-format reference for optional local images. No spec prose, code or
  third-party image is bundled. PNG fixtures, including the 2×2 sample, are original
  synthetic byte streams.
- [ITU-T T.81 (09/1992)](https://www.w3.org/Graphics/JPEG/itu-t81.pdf), Annexes B/C/F,
  is the JPEG structural reference. No specification prose, code, tables or
  third-party photos are copied. The 8×8 gray JPEG, the 16×8 DRI/RST example and the
  malformed regression streams are original; `tests/epub/test_jpeg.py` and
  `tests/epub/test_jpeg_restart.py` record their byte construction. No encoder is
  bundled.
- The `notewrench archive` api-import bundle format (manifest `tool: "bagkeep"`,
  version 0.1.0, schema 1) is the local format read by `--archive-bundle`.
  `examples/epub/archive-api/` contains original synthetic API data and the PNG above;
  `tests/epub/fixtures/archive-bundle/` is the bundle generated from those inputs,
  including generated HTML wrappers and JSON diagnostics (AGPL-3.0-only).
- [EPUBCheck](https://github.com/w3c/epubcheck) is an optional external tool mentioned
  in the documentation, never a dependency or bundled artifact.

## `notewrench links` (formerly linkdedupe)

Original implementation (first written for Node.js, ported to Python for notewrench),
tests and synthetic examples: Copyright (C) 2026 linkdedupe contributors, AGPL-3.0-only.
No third-party runtime or development dependencies, competitor source, article content,
logos or server implementation are incorporated.

Formats were inspected for interoperability only; the adapters are original code:

| Project | Upstream license | Use here |
| --- | --- | --- |
| [Karakeep](https://github.com/karakeep-app/karakeep) | AGPL-3.0 | Native export structural facts ([export schema](https://github.com/karakeep-app/karakeep/blob/main/packages/shared/import-export/exporters.ts)), not implementation reuse |
| [Wallabag](https://github.com/wallabag/wallabag) | MIT | V2 export structure and annotation fields ([v2 fixture](https://github.com/wallabag/wallabag/blob/master/tests/fixtures/Import/wallabag-v2.json), [exporter](https://github.com/wallabag/wallabag/blob/master/src/Helper/EntriesExport.php), [Entry](https://github.com/wallabag/wallabag/blob/master/src/Entity/Entry.php), [Annotation](https://github.com/wallabag/wallabag/blob/master/src/Entity/Annotation.php)); no code or fixture articles reused |

Standards and data:

- [WHATWG URL Standard](https://url.spec.whatwg.org/) (CC BY 4.0) and
  [UTS #46: Unicode IDNA Compatibility Processing](https://www.unicode.org/reports/tr46/):
  `src/notewrench/linkdedupe/whatwg_url.py` is an original implementation of the host
  parsing steps the original Node.js tool observed through `new URL()` and
  `url.domainToASCII()`. No specification prose or code is copied.
- `src/notewrench/linkdedupe/_idna_data.py` is generated data, not hand-written code:
  the UTS #46 mapping table (`IdnaMappingTable.txt`) and Joining_Type values
  (`DerivedJoiningType.txt`) of the Unicode Character Database, version 17.0.0, as
  tabulated by the [idna](https://github.com/kjd/idna) package and re-encoded in a
  compact row format. Unicode data: Copyright © 1991-2025 Unicode, Inc., Unicode
  License v3 ([licenses/Unicode-3.0.txt](licenses/Unicode-3.0.txt)). idna tables:
  Copyright (c) 2013-2026, Kim Davies and contributors, BSD-3-Clause
  ([licenses/idna-BSD-3-Clause.txt](licenses/idna-BSD-3-Clause.txt)). The idna package
  itself is not a dependency and none of its code is included.

Pinned format-evidence URLs and hashes are documented with the command
([docs/links.md](docs/links.md)). Examples contain only original synthetic
writing, reserved example domains, invented IDs and dates. linkding and bagcarry are
acknowledged related tools; none of their code is used.

## `notewrench joplin-dupes` (formerly joplin-dedupe)

Source: `src/notewrench/joplindupes/`, tests and synthetic fixture generators in
`tests/joplindupes/`, examples in `examples/joplin-dupes/`, benchmark in
`benchmarks/joplin-dupes/`. Original implementation, tests and synthetic examples:
Copyright (c) 2026 joplin-dedupe contributors, licensed **AGPL-3.0-only**. No Joplin
source, server code, logos, competitor code, upstream fixtures or articles are
included in the implementation or examples. Only the Python 3.11+ standard library
is used; there are no runtime or build dependencies.

Interoperability references (consulted only; nothing bundled):

- **Joplin**, <https://github.com/laurent22/joplin>, **AGPL-3.0-or-later**. Its public
  raw/JEX serialization was consulted solely to implement original interoperability
  code, checked against dev commit `c8efb344252b5f84580c839491158c097f77f212`:
  [import/export documentation](https://joplinapp.org/help/apps/import_export/),
  [raw exporter](https://github.com/laurent22/joplin/blob/c8efb344252b5f84580c839491158c097f77f212/packages/lib/services/interop/InteropService_Exporter_Raw.ts),
  [JEX exporter](https://github.com/laurent22/joplin/blob/c8efb344252b5f84580c839491158c097f77f212/packages/lib/services/interop/InteropService_Exporter_Jex.ts),
  [BaseItem](https://github.com/laurent22/joplin/blob/c8efb344252b5f84580c839491158c097f77f212/packages/lib/models/BaseItem.ts),
  [model types](https://github.com/laurent22/joplin/blob/c8efb344252b5f84580c839491158c097f77f212/packages/lib/BaseModel.ts),
  [resource path utilities](https://github.com/laurent22/joplin/blob/c8efb344252b5f84580c839491158c097f77f212/packages/lib/models/utils/resourceUtils.ts),
  [raw importer](https://github.com/laurent22/joplin/blob/c8efb344252b5f84580c839491158c097f77f212/packages/lib/services/interop/InteropService_Importer_Raw.ts).
  Observations are recorded in [docs/joplin-dupes.md](docs/joplin-dupes.md#format-sources-and-scope).
  This tool is not affiliated with Joplin.
- **Related projects** acknowledged without code reuse:
  <https://github.com/Abhishek-Amogha/Joplin-Notebook-Manager> and
  <https://github.com/LuredbyLogic/python-duplicate-file-finder>. Their licenses were
  not established for reuse; no files from them are bundled.

## `notewrench calibre-dupes` (formerly the standalone calibre-dupes)

The implementation, tests, synthetic metadata, example scripts and documentation are copyright
2026 the calibre-dupes contributors and licensed **AGPL-3.0-only**. No third-party runtime
packages, Calibre installation, ebook contents, copied competitor implementations or external
services are included or required. Python and its `sqlite3` module are provided by the user's
Python installation under their respective licenses; they are not bundled.

Interoperability was independently implemented after inspecting the public Calibre database
contract:

- [Calibre](https://github.com/kovidgoyal/calibre), GPL-3.0-only. Only relational field meanings and
  storage conventions were used (pinned commit `be4a47bd5ba1da3dc10a46f7e4eeea3598d3295b`;
  `resources/metadata_sqlite.sql` and `src/calibre/db/backend.py`). No upstream source, SQL schema
  file, triggers or implementation fixtures are bundled. See
  [docs/calibre-dupes/format.md](docs/calibre-dupes/format.md) for the exact observations and file hashes.
- [Find Duplicates](https://github.com/komali2/calibre-find-duplicates) and
  [CalibreQuarry](https://github.com/VirInvictus/CalibreQuarry) are related existing tools. Their
  implementations were not copied, bundled or benchmarked.

The synthetic libraries written by `examples/calibre-dupes/synthetic.py` and
`examples/calibre-dupes/libraries.json` contain entirely fictional metadata; checksum-valid ISBNs are
mathematical examples, not claims of registration.

## `notewrench citekeys` (formerly citekey-rekey)

Original CLI, scanner, identity matcher, tests and synthetic examples (first written for
Node.js, ported to Python for notewrench): Copyright (C) 2026 citekey-rekey contributors,
AGPL-3.0-only. No runtime or development dependencies, bundled binaries or models.

Interoperability references (no code, documentation prose or fixtures copied):

| Project | Upstream license | Use here |
| --- | --- | --- |
| [retorquere/zotero-better-bibtex](https://github.com/retorquere/zotero-better-bibtex) | MIT | Public CSL/Pandoc workflow and format evidence only |
| [zotero/zotero](https://github.com/zotero/zotero) | AGPL-3.0-only | Ecosystem/format interoperability only; no live app or database access |
| [citation-style-language/schema](https://github.com/citation-style-language/schema) | See upstream notices | Read-only schema reference; not bundled |
| [Pandoc manual](https://pandoc.org/MANUAL.html#citation-syntax) | See upstream documentation notices | Read-only citation syntax reference; no parser or manual text copied |
| [WHATWG URL Standard](https://url.spec.whatwg.org/) | CC BY 4.0 | Host validation rules mirrored from the original tool's `new URL()` check; no specification prose or code copied |
| [andrewheiss/pandoc-citekeys](https://github.com/andrewheiss/pandoc-citekeys) | See upstream notices | Related-tool acknowledgment; no implementation reused |

All DOI/URL/key examples and benchmark documents were authored synthetically for this tool.

## `notewrench zotero-attachments` (formerly zotero-attach-audit)

Original implementation (`src/notewrench/zoteroattachments/`), tests
(`tests/zoteroattachments/`), the fixture generator and synthetic examples
(`examples/zotero-attachments/`) and the benchmark (`benchmarks/zotero-attachments/`):
Copyright (c) 2026 zotero-attach-audit contributors. SPDX-License-Identifier:
**AGPL-3.0-only**. No Zotero code is incorporated.

Zero third-party Python package dependencies. Python 3.11+ and its bundled standard
library (including `sqlite3` and `hashlib`) are installed by the user; Python, SQLite
and OpenSSL are supplied by the interpreter distributor, not vendored here. The tool
never invokes Zotero, network services, package managers, shell scripts or provider CLIs.

Interoperability research only:

- [Zotero](https://github.com/zotero/zotero): the public schema
  ([userdata.sql](https://github.com/zotero/zotero/blob/main/resource/schema/userdata.sql)),
  link modes ([attachments.js](https://github.com/zotero/zotero/blob/main/chrome/content/zotero/xpcom/attachments.js),
  relative-path handling pinned at commit
  [c634bba](https://github.com/zotero/zotero/blob/c634bba5b65c0f9984af8fc3b1926b633139fc3f/chrome/content/zotero/xpcom/attachments.js#L2863-L2916))
  and path semantics ([item.js](https://github.com/zotero/zotero/blob/main/chrome/content/zotero/xpcom/data/item.js))
  were researched and the implementation written independently. The upstream COPYING
  says AGPLv3; individual inspected source headers allow v3 or later. This original code
  deliberately uses AGPL-3.0-only.
- [zotero-storage-cleaner](https://github.com/GrainRainAndy/zotero-storage-cleaner)
  and [zotero-storage-scanner](https://github.com/retorquere/zotero-storage-scanner):
  related projects acknowledged. No code, fixtures, icons or article content copied.
- `examples/zotero-attachments/synthetic/` and the fixture/benchmark generators contain
  only newly authored test bytes and a reduced SQLite schema using the public format's
  table/column names. Fake `.pdf`/`.png` payloads are byte-audit samples, not renderable
  documents or copied publications.

Zotero's name remains its owner's trademark; this is an independent, unofficial
interoperability tool.
