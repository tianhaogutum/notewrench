# Fixture provenance

Downloaded from public upstream repositories on 2026-11-11. `upstream/manifest.json` records the exact source URL and SHA-256 of each unmodified fixture. GitHub's API rate limit prevented pinning the branch commit; content hashes make the downloaded bytes auditable.

- `zotero_rdf.xml`: real Zotero test export containing a book, two child notes and a related webpage.
- `book_and_snapshot.rdf`: real Zotero RDF test export with child snapshot attachment metadata.
- `translatorExport.js`: a pure JSON object despite its suffix; generated/exported Zotero test records covering bibliographic item types and fields. Tests wrap its values in an array without changing their contents.
- `bbt-001.json`: real Better BibTeX JSON export of Adams et al.'s 2001 Huaynaputina article, with ordered creators, Unicode page range and tags.
- `bbt-note.json`: real Better BibTeX regression export containing HTML note strings and URL fragment/backslash escaping.
- `bbt.csl.json`: real Better BibTeX CSL JSON regression export of Olson's 2006 article, with title markup and CSL date-parts.

Zotero fixture data retains the upstream license (`ZOTERO-LICENSE.txt`, GNU AGPL v3); Better BibTeX fixture data retains `BBT-LICENSE.txt` (MIT).

## Derived/adversarial examples

`examples/zotero-dupes/make_examples.py` (repository root) reproducibly derives `examples/zotero-dupes/personal.json` and `group.json` from the unmodified BBT inputs above. Synthetic changes add library URIs/keys, differing tag/note/annotation metadata, a DOI URL variant, and a deliberately conflicting DOI/year/preprint candidate. These two files are demonstration data, not authentic personal/group library snapshots. The example DOI is a synthetic matching identifier and is not asserted to be the article's real DOI.

The `tests/zoterodupes/` suite constructs invalid input, identifier collisions, API child items, sparse records, stale/contradictory decisions and script-like note contents. These are targeted adversarial fixtures, explicitly distinct from the real upstream files.

No user library data was used and no credentials or files were uploaded.

## Additional real format variants

The manifest also records nine unmodified BBT regression exports: all-fields native JSON and matching CSL JSON, collected notes, complex CSL dates, automatic tag types, Extra DOI fields, Extra creators, non-file attachments and Chinese titles.

## Executing the official RDF exporter

`upstream/zotero-rdf-translator.js` is the unmodified official Zotero export translator, used only to generate a test fixture. When the fixture was made, a small Node.js probe supplied a minimal in-memory Zotero.RDF host (triple storage, ordered containers, export options) and ran the translator's actual `doExport()`; RDFLib then serialized the emitted triples to `derived-official-translator.rdf`. The probe scripts are not part of notewrench; the derived fixture is kept as data. The `.source.json` alongside it records the exact derived item supplied to the exporter.

This is **not** a Zotero desktop execution or a PDF/file export test. It specifically validates the real translator's field/container/creator/tag/child-note mappings. The fixture has a nested section followed by a journal with a URI subject, an editor, a publisher/address, tags and attachment notes. The first parser version mistakenly treated the section title as the journal; that defect was reproduced and fixed with this fixture.
