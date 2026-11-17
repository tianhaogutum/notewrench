# bookmark-migration: export formats, evidence and comparison limits

Reference notes for [`notewrench bookmark-migration`](../bookmark-migration.md). The
formats were checked against the upstream sources linked below on 2026-11-11. All
bundled examples are fictional data written for this command, following the verified
file structures; no private collection and no third-party test file is shipped as an
example.

## Karakeep JSON

Supported is the JSON export object downloaded from Karakeep's settings page, **not** a
REST API response, a database backup or an arbitrary URL array. The structure follows
upstream commit `88a9ff9cfdfa2b99da4d6a700ea2d0055bd3639d`:

- [Export schema and converter](https://github.com/karakeep-app/karakeep/blob/88a9ff9cfdfa2b99da4d6a700ea2d0055bd3639d/packages/shared/import-export/exporters.ts):
  `zExportSchema`, `zExportBookmarkSchema`, `zExportListSchema`, `toExportFormat`.
- [The web download route](https://github.com/karakeep-app/karakeep/blob/88a9ff9cfdfa2b99da4d6a700ea2d0055bd3639d/apps/web/app/api/bookmarks/export/route.tsx):
  the JSON branch assembles bookmarks, lists and manual list memberships.
- [The official parser](https://github.com/karakeep-app/karakeep/blob/88a9ff9cfdfa2b99da4d6a700ea2d0055bd3639d/packages/shared/import-export/parsers.ts)
  and [its tests](https://github.com/karakeep-app/karakeep/blob/88a9ff9cfdfa2b99da4d6a700ea2d0055bd3639d/packages/shared/import-export/parsers.test.ts):
  list IDs, lists with the same name, hierarchy and smart lists.

The top level needs a `bookmarks` array and may have a `lists` array (default `[]`).
Bookmark fields:

| Field | Upstream shape | Meaning for the audit |
| --- | --- | --- |
| `content` | `{type:"link",url:string}`, `{type:"text",text:string}` or `null` | Links are matched by URL; text and null content are listed as not audited |
| `title` | string or null, required | Title; null means the export has no title value |
| `tags` | array of strings, required | Tag names; tag IDs are never compared across systems |
| `note` | string or null, required | Note; null is an explicit empty value |
| `createdAt` | number, required, Unix seconds | Exported save time; not milliseconds or an ISO string |
| `lists` | array of strings, optional, default `[]` | Manual list IDs; the name path is rebuilt from the top-level lists |
| `archived` | boolean, optional, default false | Archive state; not a save time |

A minimal, fictional, valid example:

```json
{
  "bookmarks": [{
    "createdAt": 1735689600,
    "title": "Migration check · example",
    "tags": ["research"],
    "content": {"type": "link", "url": "https://example.org/find?q=bookmarks&page=2"},
    "note": "Keep this note.",
    "lists": ["reading"],
    "archived": false
  }],
  "lists": [{
    "id": "reading", "name": "Reading", "description": null,
    "icon": "📁", "type": "manual", "query": null, "parentId": null
  }]
}
```

List definitions: `id`, `name` and `icon` are strings; `description`, `query` and
`parentId` are strings or null; `type` is `manual` or `smart`. The local IDs in a file
rebuild the hierarchy; IDs usually change in a migration, so the name path is compared
across systems. Two lists with the same name and path cannot be told apart, so the
report cannot prove their identity survived. Empty lists, list descriptions, icons and
queries, and the dynamic members of smart lists are not audited.

Limits that follow from the format:

1. The current exporter writes the last saved date as `createdAt`, deliberately not
   `firstCreatedAt`. A comparison shows whether the exported values agree, not whether
   the original save date was kept.
2. A plain JSON export has no attachment fields. The converter excludes asset bookmarks
   and the download route also drops `content === null`, so asset bookmarks may be
   missing from the file altogether. The export cannot prove that assets or attachments
   migrated.
3. The download route exports only lists the user owns, and memberships only of manual
   lists; smart lists export their definition, not their query results. Older exports
   may lack list fields, in which case folders cannot be verified.
4. API fields such as `assets` or `firstCreatedAt` are not part of this format, and an
   invented `attachments` property does not make them one.

## Netscape bookmarks HTML

The common bookmark exchange format of browsers (not a browser's internal JSON). Checked
against Mozilla's reader and writer and real format tests:

- [Mozilla BookmarkHTMLUtils.sys.mjs, pinned revision](https://github.com/mozilla/gecko-dev/blob/6891d8609824fc63b03af460d0b5a4911749b56b/toolkit/components/places/BookmarkHTMLUtils.sys.mjs):
  the format description at the top of the file, link attribute reading,
  `_writeHeader`, `_writeContainer`, `_writeItem` and `_writeDateAttributes`.
- [Mozilla's historical HTML test file](https://github.com/mozilla/gecko-dev/blob/master/toolkit/components/places/tests/unit/bookmarks.preplaces.html):
  the non-XML `<DT>` / `<DD>` syntax without closing tags and nested folders (this link
  follows upstream and is supporting evidence only).
- [Mozilla's tag export/re-import test](https://github.com/mozilla/gecko-dev/blob/master/toolkit/components/places/tests/unit/test_bookmarks_html_import_tags.js):
  real use of the `TAGS` extension.
- [Karakeep's Netscape generator and tests](https://github.com/karakeep-app/karakeep/blob/88a9ff9cfdfa2b99da4d6a700ea2d0055bd3639d/packages/shared/import-export/exporters.test.ts):
  `&` in URLs and text such as tags must be escaped.

Structure: the file has the `NETSCAPE-Bookmark-file-1` doctype; `A HREF` is the URL and
the text of `A` the title; an `H3` followed by a `DL` is a folder, and nested `DL`s
express the hierarchy; `ADD_DATE` is Unix seconds; `TAGS` is a comma-separated tag
extension. HTML entities are decoded before comparing; Unicode is preserved. A
fictional example:

```html
<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
  <DT><H3>Reading</H3>
  <DL><p>
    <DT><A HREF="https://example.org/find?q=bookmarks&amp;page=2" ADD_DATE="1735689600" TAGS="research">Migration check · example</A>
    <DD>A note in the historical DD format.
  </DL><p>
</DL><p>
```

HTML is a family of formats and exporters differ in what they write. These rules keep
fields an exporter did not write from being reported as lost:

| Field | Rule |
| --- | --- |
| URL / title / folders | Compared as found in `A`, `H3` and `DL`; the root has no folder path |
| Tags | Not comparable when no entry in the file has `TAGS`; once one does, a missing attribute means no tags (Karakeep's HTML exporter also omits empty tags) |
| Notes | Only entries followed by a historical `DD` have a comparable note; others are unknown. Mozilla's current writer does not write notes and its reader no longer imports descriptions |
| Time | Comparable only with a valid `ADD_DATE`; a missing one is unknown, never zero. `LAST_MODIFIED` is not a save time and does not replace `ADD_DATE` |
| Attachments | Not supported; `ICON` / `ICON_URI` are site icons, not attachments |

Exporters may rename the toolbar or add an import root folder. The command never drops
root paths silently, turns folder names into tags or merges folders by case, and does
not claim that an equal path proves an identical folder. Not every private extension of
every browser is covered.

Historical folder `DD` descriptions are kept as out-of-scope metadata. Livemarks with
`FEEDURL` (which may have no `HREF`) are listed as not audited rather than counted as
migrated links. An explicit `BR` in a note becomes a line break. The Mozilla historical
sample above was parsed during development; it is not shipped.

## Attachments and the sidecar manifest

Neither native export is enough for an attachment audit. The command reads an optional
**sidecar manifest of its own** (schema in
[the command page](../bookmark-migration.md#optional-attachment-check)) that adds
attachment references and relative local paths per entry. It is not a Karakeep or
Netscape field. A present file proves only that the path can be found, not that
content, size, hash or online assets match; without a manifest, attachments are not
comparable.

## Compared with Karakeep's own tools

Karakeep's [server migration guide](https://docs.karakeep.com/administration/server-migration/)
describes a CLI that copies the list hierarchy, tags, bookmarks and some assets, and may
deduplicate links. `bookmark-migration` does not claim those as its own features; it
adds a read-only, cross-format, entry-by-entry comparison of two local exports with
field capabilities and original positions. It never connects to an account or changes
an online collection. A JSON export can prove less than a full server migration covers.
