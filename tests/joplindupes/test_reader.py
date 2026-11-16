# SPDX-License-Identifier: AGPL-3.0-only
import io
from pathlib import Path
import tarfile
import tempfile
import unittest

from notewrench.joplindupes import ExportError, Limits, load_export
from notewrench.joplindupes.reader import parse_item
from .synthetic import example_files, record, uid, write_jex, write_raw


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def archive(self, members):
        path = self.root / "input.jex"
        with tarfile.open(path, "w") as archive:
            for info, data in members:
                archive.addfile(info, io.BytesIO(data) if data is not None else None)
        return path

    def test_terminal_metadata_preserves_body_colons_and_blank_lines(self):
        data = ("A title\n\nbody: value\n\nid: body text\ntype_: not metadata\n\n"
                "ending\n\n\nid: " + uid(1) + "\ncustom: a:b\ntype_: 1").encode()
        item = parse_item(data, uid(1) + ".md")
        self.assertEqual(item.body, "body: value\n\nid: body text\ntype_: not metadata\n\nending\n")
        self.assertEqual(item.metadata["custom"], "a:b")

    def test_real_shapes_empty_note_folder_and_note_tag(self):
        for data, title, body in [
            (record(uid(1), 1, "Empty", ""), "Empty", ""),
            (record(uid(1), 1, "", "body"), "", "body"),
            (record(uid(1), 2, "Notebook"), "Notebook", ""),
            (record(uid(1), 6, note_id=uid(2), tag_id=uid(3)), "", ""),
        ]:
            with self.subTest(data=data):
                item = parse_item(data, uid(1) + ".md")
                self.assertEqual((item.title, item.body), (title, body))

    def test_raw_and_tar_equivalence_source_unchanged(self):
        files = example_files()
        raw = write_raw(self.root / "raw", files)
        jex = write_jex(self.root / "a.jex", files)
        before = jex.read_bytes()
        a, b = load_export(raw), load_export(jex)
        self.assertEqual(a.items, b.items)
        self.assertEqual(a.blobs, b.blobs)
        self.assertEqual(a.stats["resource_hashes"], 2)
        self.assertEqual(jex.read_bytes(), before)
        self.assertEqual({name: (raw / name).read_bytes() for name in files}, files)
        self.assertEqual(set(p.relative_to(raw).as_posix() for p in raw.rglob("*") if p.is_file()), set(files))

    def test_tar_traversal_absolute_and_unexpected_paths(self):
        for name in ("../escape", "/tmp/escape", "resources/../../escape", "a\\b", "nested/" + uid(1) + ".md", "resources//" + uid(1), "./../evil", "ordinary.md"):
            with self.subTest(name=name):
                info = tarfile.TarInfo(name)
                info.size = 1
                with self.assertRaises(ExportError):
                    load_export(self.archive([(info, b"x")]))
        self.assertFalse((self.root / "escape").exists())

    def test_links_and_special_tar_types(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE):
            with self.subTest(kind=kind):
                info = tarfile.TarInfo(uid(1) + ".md")
                info.type, info.linkname = kind, "/etc/passwd"
                with self.assertRaisesRegex(ExportError, "Links/special"):
                    load_export(self.archive([(info, None)]))

    def test_duplicate_tar_names_rejected(self):
        data = record(uid(1), 1, "Title", "Body")
        info = tarfile.TarInfo(uid(1) + ".md")
        info.size = len(data)
        with self.assertRaisesRegex(ExportError, "Duplicate archive path"):
            load_export(self.archive([(info, data), (info, data)]))

    def test_tar_directories_and_dot_prefix(self):
        folder = tarfile.TarInfo("resources/")
        folder.type = tarfile.DIRTYPE
        data = record(uid(1), 1, "Title", "Body")
        info = tarfile.TarInfo("./" + uid(1) + ".md")
        info.size = len(data)
        export = load_export(self.archive([(folder, None), (info, data)]))
        self.assertEqual(len(export.items), 1)

    def test_size_total_metadata_and_entry_limits_for_both_readers(self):
        files = example_files()
        raw = write_raw(self.root / "raw", files)
        jex = write_jex(self.root / "a.jex", files)
        for path in (raw, jex):
            for limits in (Limits(max_file_bytes=4), Limits(max_total_bytes=4),
                           Limits(max_entries=1), Limits(max_metadata_bytes=4)):
                with self.subTest(path=path, limits=limits), self.assertRaisesRegex(ExportError, "limit"):
                    load_export(path, limits)

    def test_invalid_limits(self):
        for n in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                Limits(max_entries=n)

    def test_raw_symlinks_fifos_and_nested_layout(self):
        for mode in ("filelink", "dirlink", "fifo", "nested"):
            with self.subTest(mode=mode):
                raw = write_raw(self.root / mode, {uid(1) + ".md": record(uid(1), 1, "t", "b")})
                if mode == "filelink":
                    (raw / "resources").mkdir()
                    (raw / "resources" / uid(2)).symlink_to("/etc/passwd")
                elif mode == "dirlink":
                    (raw / "resources").symlink_to(self.root, target_is_directory=True)
                elif mode == "fifo":
                    import os
                    os.mkfifo(raw / "resources")
                else:
                    (raw / "resources" / "subdir").mkdir(parents=True)
                with self.assertRaises(ExportError):
                    load_export(raw)

    def test_symlink_input_or_ancestor(self):
        raw = write_raw(self.root / "raw", example_files())
        linked = self.root / "linked"
        linked.symlink_to(raw, target_is_directory=True)
        for path in (linked, linked / (uid(1) + ".md")):
            with self.assertRaisesRegex(ExportError, "Symlink"):
                load_export(path)

    def test_ambiguous_blobs(self):
        files = example_files()
        files[f"resources/{uid(301)}.other"] = b"other"
        with self.assertRaisesRegex(ExportError, "Ambiguous"):
            load_export(write_raw(self.root / "raw", files))

    def test_malformed_records(self):
        good = record(uid(1), 1, "Title", "Body")
        bad_cases = [b"not an export", b"\xff", good.replace(b"type_: 1", b"type_: 0"),
                     good + b"\nid: " + uid(1).encode(), good.replace(uid(1).encode(), uid(2).encode()),
                     good.replace(b"Body", b"a\0b"), good.replace(b"type_: 1", b"file_extension: ../x\ntype_: 1")]
        for data in bad_cases:
            with self.subTest(data=data), self.assertRaises(ExportError):
                parse_item(data, uid(1) + ".md")

    def test_unknown_type_and_orphan_blob_reported(self):
        files = {uid(1) + ".md": record(uid(1), 99, "Unknown", "uninterpreted", extra="preserve"),
                 "resources/" + uid(2): b"orphan"}
        export = load_export(write_raw(self.root / "raw", files))
        self.assertEqual({w["code"] for w in export.warnings}, {"unknown_item_type", "orphan_resource_file"})
        self.assertEqual(export.items[uid(1)].body, "uninterpreted")

    def test_unsupported_missing_empty_and_compressed(self):
        for index, files in enumerate(({}, {"notes/readme.md": b"text"}, {"note-lock-key.json": b"{}"})):
            with self.assertRaises(ExportError):
                load_export(write_raw(self.root / str(index), files))
        with self.assertRaises(ExportError):
            load_export(self.root / "missing")
        zipped = self.root / "compressed.jex"
        with tarfile.open(zipped, "w:gz") as archive:
            data = record(uid(1), 1, "t", "b")
            info = tarfile.TarInfo(uid(1) + ".md")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
        with self.assertRaises(ExportError):
            load_export(zipped)

    def test_truncated_and_hidden_trailing_tar(self):
        path = write_jex(self.root / "input.jex", {uid(1) + ".md": record(uid(1), 1, "Title", "Body")})
        full = path.read_bytes()
        for bad in (full[:520], full[:1024], full + b"hidden", full + full):
            path.write_bytes(bad)
            with self.subTest(length=len(bad)), self.assertRaises(ExportError):
                load_export(path)
