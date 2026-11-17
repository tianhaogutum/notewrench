# Fixture provenance

Fetched from [Better BibTeX v9.0.77](https://github.com/retorquere/zotero-better-bibtex/tree/v9.0.77)
on 2026-11-11, without starting Zotero:

- `upstream/bbt-sample.bib` is the unmodified
  `test/fixtures/export/Auto-Export citekey edits #citekey-edit.before.bibtex`.
  This is a real upstream expected export for a machine-learning/SysML paper.
- `upstream/bbt-stable.bib` is the unmodified
  `test/fixtures/export/Better BibLaTeX.stable-keys.biblatex`.
  It includes Unicode, LaTeX markup, and multiple entries sharing one DOI,
  demonstrating why DOI matching is insufficient for library-item identity.
- `upstream/bbt-license.txt` is the upstream MIT license.
- `upstream/bbt-sample.json` is the unmodified companion
  `test/fixtures/export/Auto-Export citekey edits #citekey-edit.json`.
  This sanitized test fixture has `citationKey` and numeric `itemID`, but no
  `uri`; the test proves that the latter is never invented from the former.

`examples/bibtex-keys/old.bib` preserves the real sample export and adds the **synthetic**
stable identity `https://zotero.org/users/12345/items/ABCD1234` through the field
the postscript recipe would add. `examples/bibtex-keys/new.bib` changes only its citation
key to `Radler2022SysML`. This pair is a controlled migration, not a claim to
have run Zotero or regenerated its keys. `examples/bibtex-keys/notes/systems.md` is an
original demonstration using the real paper and citation formats documented
by Better BibTeX and Pandoc.

`examples/bibtex-keys/old.json` and `examples/bibtex-keys/new.json` preserve the real companion JSON
fixture's metadata and add the same synthetic URI and key change as the BibTeX
pair. They are controlled migrations in the source-verified BBT JSON shape,
not newly generated Zotero exports. BBT v9.0.77's
`translators/lib/bbtjson.ts` retains `uri` in real exports; testing/sanitization
accounts for its absence in the unmodified upstream fixture.

Other small inline fixtures in tests are synthetic adversarial cases, labeled
by the test's name. They supplement the upstream data rather than replacing it.

## Additional upstream variants

`upstream/variants/manifest.json` records exact versioned download URLs and sizes
for six additional unmodified files: BBT **v8.0.25** auto-export and stable-key
fixtures, and BBT **v9.0.77** BibTeX/BibLaTeX Extra-field regressions, a custom
field regression, and an Arabic-text/transliterated-key fixture. Each is covered
by the upstream MIT license alongside the first two fixtures.

`tests/bibtexkeys/test_upstream_variants.py` first parses each unmodified fixture. Its separate
end-to-end tests add synthetic identity fields and renames through Pybtex's own
writer. These controlled migrations verify the adapter around real exported
metadata, not an unperformed claim about running Zotero. The six-entry fixture
shares one DOI; distinct synthetic item IDs still yield six correct mappings.
