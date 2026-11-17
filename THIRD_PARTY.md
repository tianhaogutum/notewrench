# Third-party sources and attribution

notewrench is licensed under the GNU Affero General Public License, version 3 only
(AGPL-3.0-only; see [LICENSE](LICENSE)). The first ten commands below were previously separate
AGPL-3.0-only projects by the same authors; their notices are preserved below, one section
per command, with paths updated to this repository's layout. `notewrench links` and
`notewrench citekeys` were first written for Node.js and ported to Python by those same
authors; the ports contain no third-party code. The LICENSE file is the unmodified GNU AGPL
version 3 text, Copyright (C) 2007 Free Software Foundation, Inc., reproduced from
<https://www.gnu.org/licenses/agpl-3.0.txt> under its verbatim-copying notice. The original
work is offered under version 3 **only**, not "or later".

Five later commands (`anki-merge`, `relink`, `browser-bookmarks`, `zotero-dupes` and
`bibtex-keys`) were separate projects under the **MIT License**. Their files keep that
license and notice (each source file carries `SPDX-License-Identifier: MIT` and the
original copyright line; full texts in [`licenses/`](licenses/)). MIT code may be
distributed as part of this AGPL-3.0-only work; those files on their own remain
available under MIT.

Product names identify interoperable formats only; notewrench is not affiliated with or
endorsed by Zotero, Joplin, Logseq, Wallabag, Karakeep, Calibre, Pandoc or any other project
named here.

## At a glance

- **No required runtime dependencies.** The core commands use only the Python standard
  library (Python 3.11+, installed separately under the Python Software Foundation
  License). `anki-merge`, `relink`, `browser-bookmarks`, `zotero-dupes` and `bibtex-keys`
  are thin adapters over third-party libraries that are installed from PyPI with the
  optional extra of the same name (`pip install 'notewrench[<command>]'`); they are not
  bundled. Each package and its license is listed in that command's section below. The
  wheel contains only the `notewrench` package and these project notices.
- **One piece of vendored third-party code:** `notewrench browser-bookmarks` includes an
  adapted copy of linkding's Netscape-HTML exporter and a few unmodified linkding helper
  functions (MIT, Copyright (c) 2019 Sascha Ißbrücker,
  [text](licenses/linkding-MIT.txt)); see
  [its section](#notewrench-browser-bookmarks-formerly-bookmark-receipts). Otherwise no
  third-party source code, documentation prose, articles or models are vendored:
  upstream projects were consulted as format and behavior references only, and the
  readers and writers are original implementations.
- **One bundled data table:** `src/notewrench/linkdedupe/_idna_data.py` (UTS #46 IDNA
  mapping and Joining_Type data, Unicode 17.0.0), used by `notewrench links` to map
  Unicode host names like the WHATWG URL Standard does. It is derived from the Unicode
  Character Database (Unicode License v3, [text](licenses/Unicode-3.0.txt)) as tabulated
  by the [idna](https://github.com/kjd/idna) package (BSD-3-Clause, Copyright (c)
  2013-2026 Kim Davies and contributors, [text](licenses/idna-BSD-3-Clause.txt)). See
  [`notewrench links`](#notewrench-links-formerly-linkdedupe) below. Both license texts
  ship in the wheel and sdist.
- **Examples and test fixtures are original synthetic data** authored for these tools
  (reserved example domains, invented IDs, dates and text), under the root license,
  except where a command's section lists upstream fixtures: `relink`, `browser-bookmarks`,
  `zotero-dupes` and `bibtex-keys` keep small unmodified upstream test files (Better
  BibTeX, Zotero, linkding, Chromium, warcio, ArchiveBox, CommonMark, browser exports)
  with their licenses beside them under `tests/`, and `anki-merge` ships genuine Anki
  exports of invented course content. Tests and their fixtures are not in the wheel.
- Test-only dependencies (`test` extra): [pytest](https://github.com/pytest-dev/pytest) (MIT)
  and its dependencies (iniconfig, pluggy: MIT; packaging: Apache-2.0 OR BSD-2-Clause;
  Pygments: BSD-2-Clause), [Hypothesis](https://github.com/HypothesisWorks/hypothesis)
  (MPL-2.0), and the packages of all five optional extras. Build-only tools: [hatchling](https://github.com/pypa/hatch) (MIT)
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
- [`notewrench anki-merge` (formerly anki-course-merge)](#notewrench-anki-merge-formerly-anki-course-merge)
- [`notewrench relink` (formerly archive-relink)](#notewrench-relink-formerly-archive-relink)
- [`notewrench browser-bookmarks` (formerly bookmark-receipts)](#notewrench-browser-bookmarks-formerly-bookmark-receipts)
- [`notewrench zotero-dupes` (formerly library-bridge)](#notewrench-zotero-dupes-formerly-library-bridge)
- [`notewrench bibtex-keys` (formerly citekey-audit)](#notewrench-bibtex-keys-formerly-citekey-audit)

## `notewrench zotero-logseq` (formerly zotlog-sync)

Original implementation (`src/notewrench/zoterologseq/`), tests (`tests/zoterologseq/`)
and synthetic examples (`examples/zotero-logseq/`): Copyright (C) 2026 tianhaogutum; SPDX-License-Identifier: AGPL-3.0-only (version 3 only, no "or later"
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
(Copyright (c) 2026 tianhaogutum). No third-party runtime, test or build
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

Original code under **AGPL-3.0-only**, copyright (C) 2026 tianhaogutum. No upstream
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
Copyright (C) 2026 tianhaogutum, **AGPL-3.0-only**. No third-party Python
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
tests and synthetic examples: Copyright (C) 2026 tianhaogutum, AGPL-3.0-only.
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
Copyright (c) 2026 tianhaogutum, licensed **AGPL-3.0-only**. No Joplin
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
Node.js, ported to Python for notewrench): Copyright (C) 2026 tianhaogutum,
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
Copyright (c) 2026 tianhaogutum. SPDX-License-Identifier:
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

## `notewrench anki-merge` (formerly anki-course-merge)

Original implementation (`src/notewrench/ankimerge/`), tests (`tests/ankimerge/`), sample
exports and their generator (`examples/anki-merge/`): Copyright (c) 2026 tianhaogutum, under the **MIT License** ([text](licenses/anki-merge-MIT.txt)), not the
AGPL-3.0-only license of the rest of notewrench. Every ported source file keeps an
`SPDX-License-Identifier: MIT` header with that copyright line. The MIT terms permit
inclusion in this AGPL-3.0-only distribution; the combined work is distributed under
AGPL-3.0-only, and these files can still be reused under MIT on their own.

Runtime dependency (optional `anki-merge` extra, installed from PyPI, not bundled):
[csvdiff](https://github.com/larsyencken/csvdiff) (`>=0.3.3,<0.4`, see its package metadata
for license terms), used unmodified as the keyed semantic field-diff engine via its public
`diff_records` API. Test-only: [Hypothesis](https://github.com/HypothesisWorks/hypothesis)
(MPL-2.0) for property tests, and optionally the headless [Anki](https://github.com/ankitects/anki)
core (`anki` on PyPI, AGPL-3.0-or-later; not a dependency of any extra) for integration tests
that export and import notes in disposable collections; those tests are skipped when it is
not installed. No Anki source code is copied.

Sample data: `examples/anki-merge/*.txt` and the `anki-2.1.54/`, `plain-text/`, `no-guid/` and
`fields-only/` variants are genuine Anki note-text exports (Anki 26.9.4, and 2.1.54 for that
variant) of invented, synthetic course content, generated with Anki's public
`Collection.export_note_csv` API by `examples/anki-merge/make_examples.py`; no real profile or
user data was read ([provenance](examples/anki-merge/PROVENANCE.md)). They are MIT-licensed
like the code.

Format and behavior references (consulted, not copied):

| Project | Upstream license | Use here |
| --- | --- | --- |
| [Anki](https://github.com/ankitects/anki) and its [text import](https://docs.ankiweb.net/importing/text-files.html) / [export](https://docs.ankiweb.net/exporting.html) manuals; [CSV metadata source](https://github.com/ankitects/anki/blob/main/rslib/src/import_export/text/csv/metadata.rs) | AGPL-3.0-or-later | Text export headers (`#separator`, `#html`, `#guid column`, `#if matches`, `#match scope`, ...), GUID matching and import behavior; verified against the 2.1.66 and 26.9.4 backends |
| [csvdiff](https://github.com/larsyencken/csvdiff) 0.3.3 | see upstream | Reused as the diff engine (above) |
| [daff](https://github.com/paulfitz/daff) 1.4.2 | MIT | Evaluated for three-way CSV merging; not used (its deletion and conflict-marker policy does not fit unattended imports) |
| [CrowdAnki](https://github.com/Stvad/CrowdAnki) | MIT | Personal-field protection approach reviewed; related workflow |
| [Brain Brew](https://github.com/jeprecated/brain-brew) | see upstream | Related CSV ↔ CrowdAnki authoring pipeline; inputs can be fed to this command |
| [Ki](https://github.com/langfield/ki), [ankidmpy](https://github.com/gitonthescene/ankidmpy), [csvdb](https://github.com/jeff-gorelick/csvdb) | see upstream | Related version-control/format tools; acknowledged only |
| [Special Fields](https://ankiweb.net/shared/info/1102281552), [AnkiCollab](https://github.com/CravingCrates/AnkiCollab-Plugin), [AnkiHub](https://www.ankihub.net/faq) | see upstream | Related add-ons/services for protected fields and shared decks; acknowledged only |

Anki, AnkiWeb, AnkiHub and the other names identify interoperable software only; notewrench is
not affiliated with or endorsed by them.


## `notewrench relink` (formerly archive-relink)

Original implementation (`src/notewrench/relink/`), tests (`tests/relink/`), synthetic
examples (`examples/relink/`), benchmark (`benchmarks/relink/`) and documentation
(`docs/relink.md`): Copyright (c) 2026 tianhaogutum, **MIT License**
(full text: [licenses/relink-MIT.txt](licenses/relink-MIT.txt)). This differs from
notewrench's AGPL-3.0-only license: the MIT notice is kept, and each source file carries
`SPDX-License-Identifier: MIT` with the original copyright line. MIT code may be
distributed as part of the AGPL-3.0-only whole; the files themselves remain available
under MIT.

Unlike the standard-library commands, `relink` is a thin adapter over third-party
packages, installed with the optional extra `pip install 'notewrench[relink]'` (not
bundled or vendored):

| Package | License | Use |
| --- | --- | --- |
| [markdown-it-py](https://github.com/executablebooks/markdown-it-py) | MIT | CommonMark tokens and source maps; the upstream link, image, autolink and reference rules decide what is a citation, and a small span plugin records destination offsets |
| [mdit-py-plugins](https://github.com/executablebooks/mdit-py-plugins) | MIT | Front-matter tokenization, so note metadata is never treated as a citation |
| [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) (`beautifulsoup4`) | MIT | HTML parsing and text/metadata extraction from saved pages |
| [warcio](https://github.com/webrecorder/warcio) | Apache-2.0 | Streaming WARC/WARC.GZ reading |
| [idna](https://github.com/kjd/idna) | BSD-3-Clause | Non-transitional IDNA 2008/UTS 46 host names in URL keys |

Their own dependencies (for example soupsieve, mdurl) come with their own licenses.
SQLite FTS5 (public domain) and Python's `email` MIME parser are used from the standard
library. No code from these packages is copied.

Formats followed, as interoperability references only (no code copied):
[SingleFile](https://github.com/gildas-lormeau/SingleFile) provenance comments
(single-file-core 1.12.0, AGPL-3.0), [ArchiveBox](https://github.com/ArchiveBox/ArchiveBox)
snapshot indexes, JSONL and resource manifests (v0.7.2 and dev
`7ee253e8205012e838f9f89e8b81dd97dbe2b182`, MIT), [nb](https://github.com/xwmx/nb)
`.bookmark.md` files (7.25.6, AGPL-3.0), [buku](https://github.com/jarun/buku) Markdown
exports (5.1, GPL-3.0), MHTML as written by UnMHT/Chromium, and
[RFC 9110](https://www.rfc-editor.org/rfc/rfc9110.html#name-location) Location
semantics for redirect fragments. Related tools surveyed and acknowledged, not used:
[archive-md-urls](https://github.com/sbaack/archive-md-urls) (remote Wayback rewriting;
an inline-code replacement issue observed in 0.0.8 was documented locally, never
submitted) and [lychee](https://github.com/lycheeverse/lychee) (link checking; its
broken-URL list can feed `--only-urls`).

Test fixtures in `tests/relink/fixtures/upstream/` are small upstream files kept for
format compatibility, each with origin, commit and SHA-256 in
[`tests/relink/fixtures/PROVENANCE.md`](tests/relink/fixtures/PROVENANCE.md). Their
licenses are kept beside them and ship only in the sdist (tests are not in the wheel):

| Fixture | Origin | License (file) |
| --- | --- | --- |
| `warcio-example.warc.gz` | warcio test data | Apache-2.0 (`WARCIO-LICENSE`) |
| `archivebox-resources/` | ArchiveBox test fixtures | MIT, Copyright (c) 2020 Nick Sweeting (`ARCHIVEBOX-LICENSE`) |
| `chromium-hello.mhtml`, `chromium-test.mht` | Chromium test data (serialized by UnMHT) | BSD-3-Clause, Copyright 2015 The Chromium Authors (`CHROMIUM-LICENSE`) |
| `commonmark-0.31.2.json` | official CommonMark 0.31.2 spec examples | CC-BY-SA 4.0, Copyright (C) 2014-16 John MacFarlane (`COMMONMARK-LICENSE`) |
| `nb-saved.bookmark.md`, `nb-example-com.bookmark.md` | generated with nb 7.25.6 (synthetic content / public example.com) | upstream serialization of synthetic or example-domain content |
| `buku-export.md` | generated with buku 5.1 from synthetic bookmarks | upstream serialization of synthetic content |
| `wget-example.warc.gz` | recorded with GNU Wget 1.25.0 from public example.com | example-domain page |

`examples/relink/` (a note and three saved pages) is original synthetic data; the
SingleFile sample reproduces only the provenance-comment layout read from
single-file-core.


## `notewrench browser-bookmarks` (formerly bookmark-receipts)

Original implementation (`src/notewrench/browserbookmarks/`), tests
(`tests/browserbookmarks/`) and benchmark (`benchmarks/browser-bookmarks/`): Copyright (c)
2026 bookmark-receipts contributors, **MIT License**
([licenses/browser-bookmarks-MIT.txt](licenses/browser-bookmarks-MIT.txt)). This command
keeps its MIT notice (every source file carries `SPDX-License-Identifier: MIT`); MIT is
compatible with, and distributed here under the terms of, notewrench's AGPL-3.0-only
license for the combined work.

**Vendored linkding code (MIT).** Copyright (c) 2019 Sascha Ißbrücker, MIT License
([licenses/linkding-MIT.txt](licenses/linkding-MIT.txt)):

- `src/notewrench/browserbookmarks/_linkding_exporter.py` is adapted from linkding's
  `bookmarks/services/exporter.py` at commit `27b7303baf41bb28babc610ac8eaa486e1ddfab5`.
  Changes: the Django model dependency is removed, the `HREF` attribute is HTML-escaped,
  and raw date/state attributes plus a `DATA-RECEIPT` ID may be overridden for browser
  exports that lack linkding's model fields.
- `src/notewrench/browserbookmarks/_linkding_rules.py` contains unmodified pure function
  bodies (URL normalization, tag parsing, timestamp parsing) extracted from linkding
  v1.47.0 (`24b5ad6`, `bookmarks/utils.py` and `bookmarks/models.py`), used only for the
  destination compatibility check and timestamp comparison.
- `tests/browserbookmarks/upstream/linkding_v1_{15,31,47}_{exporter,parser}.py` are the
  unmodified upstream exporter and parser modules from linkding tags v1.15.0
  (`2c7848aa46cffbd2cfe98628842bf06004d17dd3`), v1.31.0
  (`695b0dc300772add2069d7857b99b989da2305b7`) and v1.47.0
  (`24b5ad6cc9bde497b5d1b1e86aed5a2fb7d25c2f`). The format integration tests strip only
  their Django imports; they are test-only and not part of the wheel.

**Runtime dependencies (optional `browser-bookmarks` extra, not vendored):**

| Package | License | Use |
| --- | --- | --- |
| [Beautiful Soup](https://www.crummy.com/software/BeautifulSoup/) (`beautifulsoup4`) | MIT | Netscape-HTML bookmark parsing |
| [html5lib](https://github.com/html5lib/html5lib-python) | MIT | HTML5 tree builder for Beautiful Soup |
| [webencodings](https://github.com/gsnedders/python-webencodings) | BSD-3-Clause | strict re-decoding with the detected encoding |
| [filelock](https://github.com/tox-dev/filelock) | MIT (older releases: Unlicense; verify for the installed version) | lock for concurrent review edits |
| [ijson](https://github.com/ICRAR/ijson) | BSD-3-Clause | streaming JSON complexity preflight (pure-Python backend) |

Test-only: [Hypothesis](https://github.com/HypothesisWorks/hypothesis) (MPL-2.0).

**Test fixtures** (`tests/browserbookmarks/fixtures/`, provenance in its `README.md`;
copies of three of them are the quick-start samples in `examples/browser-bookmarks/`):

- `firefox.html`, `chrome.html` (also `examples/browser-bookmarks/`): unmodified real
  browser exports from the test suite of
  [bookmarks-parser](https://github.com/andriyor/bookmarks-parser) at
  `c5a7a94bb20b10f2123c9f6fbc7633cb05332c07` (declared MIT on PyPI; no license file was
  found in the repository).
- `linkding-upstream.html`: unmodified linkding import test resource
  (`bookmarks/tests/resources/simple_valid_import_file.html` at `27b7303`), MIT as above.
- `linkding-conflicts.html` (also `examples/browser-bookmarks/linkding.html`) and
  `linkding-v1.15.html`, `linkding-v1.31.html`, `linkding-v1.47.html`: generated by
  running linkding's own exporter functions on synthetic bookmark values
  (`tests/browserbookmarks/upstream/generate_fixtures.py` reproduces the versioned ones).
- `safari-no-root.html`, `safari-folders.html`, `safari-reading-list.html`: raw HTML
  literals from Chromium's Safari importer tests
  (`components/user_data_importer/utility/safari_data_importer_unittest.cc` at
  `f82317413d635d3dfc1abf79597fce56eb9eceff`), Copyright 2015 The Chromium Authors,
  BSD-3-Clause ([licenses/chromium-BSD-3-Clause.txt](licenses/chromium-BSD-3-Clause.txt)).

**Prior art and related tools** (surveyed for the gap this command fills; no code from them
is used except linkding's as stated above): [linkding](https://github.com/sissbruecker/linkding)
(MIT; exporter/importer read, duplicate handling of `_import_batch`/`_copy_bookmark_data`,
[issue 751](https://github.com/sissbruecker/linkding/issues/751)),
[buku](https://github.com/jarun/buku) 5.1 (GPL-3.0; import behavior checked with the merged
output), [BrowserBookmarkChecker](https://github.com/VoxHash/BrowserBookmarkChecker) 1.0.0
(GPL-3.0 per its LICENSE file; not reused or redistributed),
[bookmarks-parser](https://github.com/andriyor/bookmarks-parser) 0.2.0,
[bkmrk](https://github.com/jtabke/bkmrk) 0.2.2 (already preserves notes when deduplicating
its own store), [LinkMarks](https://docs.rs/linkmarks-core/2.2.0/) 2.2 and
[PyBookmark](https://github.com/Crumbs350/pybookmark) 1.1.1.1 (read, not run).


## `notewrench zotero-dupes` (formerly library-bridge)

Source: `src/notewrench/zoterodupes/` (including the review page `review.html`, `review.css`
and `review.js`), tests in `tests/zoterodupes/`, examples and their generator in
`examples/zotero-dupes/`. Original implementation, review page, tests and examples:
Copyright (c) 2026 tianhaogutum, licensed under the **MIT License**
([licenses/zotero-dupes-MIT.txt](licenses/zotero-dupes-MIT.txt)), unlike the rest of notewrench
(AGPL-3.0-only). Each source file keeps its `SPDX-License-Identifier: MIT` header; the MIT notice
applies to these files when they are distributed as part of notewrench. The review page is
browser JavaScript generated into one self-contained HTML file; it loads nothing from a network.

The command is a thin offline adapter. These packages are runtime dependencies of the optional
`zotero-dupes` extra (`pip install 'notewrench[zotero-dupes]'`); they are installed from PyPI under
their own licenses and none of their source is copied into notewrench:

| Project | License | Use here |
| --- | --- | --- |
| [BibDedupe](https://github.com/CoLRev-Environment/bib-dedupe) (`bib-dedupe` 0.11) | MIT | Main bibliographic matcher: `prep`, `block` and `match` with one CPU, unchanged; the adapter preselects cross-library records and adds candidates |
| [RDFLib](https://github.com/RDFLib/rdflib) | BSD-3-Clause | Parses native Zotero RDF/XML exports |
| [defusedxml](https://github.com/tiran/defusedxml) | PSF-2.0 | Rejects DTDs/entities before RDF parsing |
| [RapidFuzz](https://github.com/rapidfuzz/RapidFuzz) | MIT | Title-similarity candidates for sparse exports (already a BibDedupe dependency) |
| [pandas](https://github.com/pandas-dev/pandas) | BSD-3-Clause | Data frames passed to BibDedupe (held below 3: BibDedupe 0.11 fails on sparse records under pandas 3) |
| [isbnlib](https://github.com/xlcnd/isbnlib) | LGPL-3.0 | Offline ISBN extraction, checksum validation and ISBN-10 to ISBN-13 conversion; no metadata lookups are called |
| [jsonschema](https://github.com/python-jsonschema/jsonschema) | MIT | Validates decision and artifact files against the bundled JSON Schemas |

Interoperability references and related tools (consulted; no code copied):

- [Zotero](https://github.com/zotero/zotero) (AGPL-3.0) duplicate detection
  ([documentation](https://www.zotero.org/support/duplicate_detection)), its
  [Zotero RDF translator](https://github.com/zotero/translators/blob/master/Zotero%20RDF.js) and the
  [Extra field conventions](https://www.zotero.org/support/kb/item_types_and_fields#citing_fields_from_extra).
- [Better BibTeX](https://github.com/retorquere/zotero-better-bibtex) (MIT) JSON and CSL JSON exports.
- Related tools acknowledged without code reuse: [Zoplicate](https://github.com/ChenglongMa/zoplicate),
  [Citation.js](https://github.com/citation-js/citation-js),
  [Zotero MCP](https://github.com/54yyyu/zotero-mcp) and
  [bibliography-io](https://github.com/CoLRev-Environment/bibliography-io).

Test fixtures in `tests/zoterodupes/fixtures/upstream/` are **unmodified upstream files**
(source URL and SHA-256 of each in `manifest.json`, provenance in
[tests/zoterodupes/fixtures/UPSTREAM.md](tests/zoterodupes/fixtures/UPSTREAM.md)) and keep their
licenses beside them: Zotero test exports and the official Zotero RDF export translator under the
GNU AGPL v3 (`ZOTERO-LICENSE.txt`), Better BibTeX regression exports under MIT (`BBT-LICENSE.txt`).
`derived-official-translator.rdf` was produced by running that unmodified translator on the item in
`derived-official-translator.source.json`. The fixtures are test data only and are not in the wheel.
`examples/zotero-dupes/personal.json` and `group.json` are derived from the Better BibTeX fixtures
by `examples/zotero-dupes/make_examples.py` with clearly synthetic library-specific edits (keys,
URIs, tags, notes, annotations, a conflicting DOI/year); the example DOI is a synthetic matching
identifier, and the confirmed example decision is synthetic, not a human review.


## `notewrench bibtex-keys` (formerly citekey-audit)

Original implementation (`src/notewrench/bibtexkeys/`), tests (`tests/bibtexkeys/`),
benchmark (`benchmarks/bibtex-keys/`) and examples (`examples/bibtex-keys/`): Copyright (c)
2026 citekey-audit contributors, **MIT License** ([text](licenses/bibtex-keys-MIT.txt)).
This command keeps its MIT license and notice; it differs from the AGPL-3.0-only license
of the rest of notewrench. Each source file carries `SPDX-License-Identifier: MIT`. The
MIT terms permit distribution as part of this AGPL-3.0-only work; the combined
distribution is offered under AGPL-3.0-only, and the MIT notice must be retained with
these files.

Runtime dependencies (optional `bibtex-keys` extra, `pip install 'notewrench[bibtex-keys]'`),
used as libraries and not vendored:

- [Pybtex](https://pybtex.org/) (MIT) — BibTeX/BibLaTeX parsing, including macro and
  concatenation handling; it pulls in [latexcodec](https://github.com/mcmtroffaes/latexcodec) (MIT).
- [markdown-it-py](https://github.com/executablebooks/markdown-it-py) (MIT) — CommonMark
  structure; pulls in [mdurl](https://github.com/executablebooks/mdurl) (MIT).
- [mdit-py-plugins](https://github.com/executablebooks/mdit-py-plugins) (MIT) — dollar
  math, attributes and footnotes, used to protect those regions while scanning citations.
- [pylatexenc](https://github.com/phfaist/pylatexenc) (MIT) — source-positioned TeX
  macros/environments, used to protect raw TeX in Markdown.
- [PyYAML](https://pyyaml.org/) (MIT) — YAML front-matter source marks.
- Python's standard-library `difflib` (PSF License) — unified diffs; `html.parser` for
  raw HTML code-element bodies; `urllib.parse` for URI handling.

Formats and behavior followed (consulted as references; no code copied):
[Better BibTeX for Zotero](https://github.com/retorquere/zotero-better-bibtex) v9.0.77
(MIT) — `content/item-export-format.ts` (`serialized.uri`), the postscript API
(`site/content/exporting/scripting.md`, `tex.add`), `translators/lib/bbtjson.ts` and the
BetterBibTeX JSON translator ID `36a3b0b5-bad0-4a04-b79b-441c7cef77db`, and the Quick Copy
`zotero://select/items/@key` link format; the
[Pandoc citation syntax](https://pandoc.org/MANUAL.html#extension-citations) (Pandoc 3.13
was used as a development oracle only, never at runtime). Prior art surveyed before
implementation, none reused: [Obsidian Citations](https://github.com/hans/obsidian-citation-plugin)
0.4.5, [Zotero Integration](https://github.com/community-archive/obsidian-zotero-integration)
3.2.1, [ZotLit](https://github.com/aidenlx/zotlit), [bibtex-tidy](https://github.com/FlamingTempura/bibtex-tidy)
1.15.1 and [bibtexparser](https://github.com/sciunto-org/python-bibtexparser) 2.1.0.

Test fixtures under `tests/bibtexkeys/fixtures/upstream/` are **unmodified files from
Better BibTeX** (v9.0.77 and v8.0.25 `test/fixtures/export/`), MIT License, Copyright (c)
2016 Emiliano Heyns; the upstream license text ships beside them
(`tests/bibtexkeys/fixtures/upstream/bbt-license.txt`), and exact source URLs and sizes are
recorded in `variants/manifest.json` and [PROVENANCE.md](tests/bibtexkeys/fixtures/PROVENANCE.md).
`examples/bibtex-keys/old.bib`/`old.json` preserve that real export metadata (a published
SysML paper's bibliographic record) and add a **synthetic** Zotero item URI;
`new.bib`/`new.json` change only the citation key; `notes/systems.md` is original
demonstration text. Other inline test inputs are synthetic.

Review bundles keep the original identifier `"tool": "citekey-audit"` and
`"schema_version": 1`; the staging directory prefix `.citekey-audit-` is also unchanged.
