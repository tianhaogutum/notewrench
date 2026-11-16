# notewrench documentation

One page per command. Each page starts with the problem the command solves and a quick
start, followed by the full reference. Run `notewrench <command> --help` for the flags.
Quick starts use the synthetic sample inputs under [`examples/`](../examples/), so run them
from a clone of the repository (`git clone https://github.com/tianhaogutum/notewrench`).

All commands work offline with the Python standard library only. They need Python 3.11+
on Linux or macOS; Windows is not supported. Pages note where a command needs Linux.

## Check

| Command | Use it to |
| --- | --- |
| [`notewrench doctor`](doctor.md) | Run one read-only health check over Zotero, Joplin, Calibre, Karakeep/Wallabag and citation files, and get the command that fixes each problem |

## Migrate and sync notes

| Command | Use it to |
| --- | --- |
| [`notewrench zotero-logseq`](zotero-logseq.md) | Keep one Logseq page per citekey in sync with a Zotero CSL-JSON export, without touching your notes |
| [`notewrench joplin-logseq`](joplin-logseq.md) | Convert a Joplin JEX export into a fresh Logseq Markdown file graph, with links, attachments, tags and a loss report |
| [`notewrench wallabag-notes`](wallabag-notes.md) | Export Wallabag highlights and annotations to Markdown, Logseq blocks and CSV, and update them later without losing your edits ([format notes](wallabag-notes/format.md)) |

## Read-later archives

| Command | Use it to |
| --- | --- |
| [`notewrench archive`](archive.md) | Rescue cached Wallabag articles into a static HTML archive, convert Karakeep exports to Wallabag JSON, and import saved API pages (Linux) |
| [`notewrench epub`](epub.md) | Bundle cached Wallabag/Karakeep articles, or an `archive` bundle, into one validated EPUB 3 |

## Duplicates

| Command | Use it to |
| --- | --- |
| [`notewrench links`](links.md) | Find duplicate bookmarks across Karakeep and Wallabag exports and plan a reviewed merge |
| [`notewrench joplin-dupes`](joplin-dupes.md) | Review duplicate notes across Joplin JEX/raw exports and write an approved deduplicated JEX copy |
| [`notewrench calibre-dupes`](calibre-dupes.md) | Find duplicate books within one Calibre library or across two, and plan which formats to keep ([format notes](calibre-dupes/format.md)) |

## Citations and attachments

| Command | Use it to |
| --- | --- |
| [`notewrench citekeys`](citekeys.md) | Rewrite Pandoc citekeys in Markdown documents after your CSL-JSON keys change |
| [`notewrench zotero-attachments`](zotero-attachments.md) | Audit a Zotero database snapshot and `storage/` for duplicate, missing and orphaned attachments (Linux), and compare audits |
