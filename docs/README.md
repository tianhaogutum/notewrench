# notewrench documentation

One page per command. Each page starts with the problem the command solves and a quick
start, followed by the full reference. Run `notewrench <command> --help` for the flags.
Quick starts use the synthetic sample inputs under [`examples/`](../examples/), so run them
from a clone of the repository (`git clone https://github.com/tianhaogutum/notewrench`).

All commands work offline. They need Python 3.11+ on Linux or macOS; Windows is not
supported. Pages note where a command needs Linux. Most commands use only the Python
standard library; the five marked "needs an extra" wrap third-party libraries and are
installed with `pip install 'notewrench[<command>]'` (without it, the command prints that
hint).

## Check

| Command | Use it to |
| --- | --- |
| [`notewrench doctor`](doctor.md) | Run one read-only health check over Zotero, Joplin, Calibre, Karakeep/Wallabag, browser bookmark exports and citation files, and get the command that fixes each problem |

## Migrate and sync notes

| Command | Use it to |
| --- | --- |
| [`notewrench zotero-logseq`](zotero-logseq.md) | Keep one Logseq page per citekey in sync with a Zotero CSL-JSON export, without touching your notes |
| [`notewrench joplin-logseq`](joplin-logseq.md) | Convert a Joplin JEX export into a fresh Logseq Markdown file graph, with links, attachments, tags and a loss report |
| [`notewrench wallabag-notes`](wallabag-notes.md) | Export Wallabag highlights and annotations to Markdown, Logseq blocks and CSV, and update them later without losing your edits ([format notes](wallabag-notes/format.md)) |
| [`notewrench anki-merge`](anki-merge.md) | Merge old-teacher, new-teacher and student Anki note text exports by stable course ID before importing a course update, keeping personal fields and holding conflicts for review (needs an extra) |
| [`notewrench bookmark-migration`](bookmark-migration.md) | Audit a bookmark migration: compare the browser HTML or Karakeep JSON export from before with the one from after, and report moved, missing, added and duplicate URLs and conflicting tags, folders, notes and times ([format notes](bookmark-migration/formats.md)) |

## Read-later archives

| Command | Use it to |
| --- | --- |
| [`notewrench archive`](archive.md) | Rescue cached Wallabag articles into a static HTML archive, convert Karakeep exports to Wallabag JSON, and import saved API pages (Linux) |
| [`notewrench epub`](epub.md) | Bundle cached Wallabag/Karakeep articles, or an `archive` bundle, into one validated EPUB 3 |
| [`notewrench relink`](relink.md) | Point Markdown citations at copies you already archived locally (SingleFile, ArchiveBox, nb, WARC, MHTML) and write a patch only for the candidates you select (needs an extra) |

## Duplicates

| Command | Use it to |
| --- | --- |
| [`notewrench links`](links.md) | Find duplicate bookmarks across Karakeep and Wallabag exports and plan a reviewed merge |
| [`notewrench browser-bookmarks`](browser-bookmarks.md) | Review duplicate bookmarks across browser (Firefox, Chrome, Safari) and linkding HTML exports with an explicit merge or keep decision per group, then write one importable file with receipts of every original note and tag (needs an extra) |
| [`notewrench joplin-dupes`](joplin-dupes.md) | Review duplicate notes across Joplin JEX/raw exports and write an approved deduplicated JEX copy |
| [`notewrench calibre-dupes`](calibre-dupes.md) | Find duplicate books within one Calibre library or across two, and plan which formats to keep ([format notes](calibre-dupes/format.md)) |
| [`notewrench zotero-dupes`](zotero-dupes.md) | Compare exports of a personal Zotero library and group libraries, review duplicate candidates with their notes and annotations in an offline HTML page, and export only confirmed matches ([artifact contract](zotero-dupes/artifacts.md)) (needs an extra) |

**Which bookmark command?** Before an import, review duplicate browser and linkding
bookmarks with `browser-bookmarks`; inside read-later apps, find duplicates across
Karakeep and Wallabag exports with `links`; after a migration, check the result against
the export you started from with `bookmark-migration`.

## Citations and attachments

| Command | Use it to |
| --- | --- |
| [`notewrench citekeys`](citekeys.md) | Rewrite Pandoc citekeys in Markdown documents after your CSL-JSON keys change |
| [`notewrench bibtex-keys`](bibtex-keys.md) | Preview a citation-key change from two BibTeX/BibLaTeX or Better BibTeX JSON snapshots (matched by stable item ID) across a Markdown notes folder, as a review bundle and a patch you apply yourself (needs an extra) |
| [`notewrench zotero-attachments`](zotero-attachments.md) | Audit a Zotero database snapshot and `storage/` for duplicate, missing and orphaned attachments (Linux), and compare audits |
