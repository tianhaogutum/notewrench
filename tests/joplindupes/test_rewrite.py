# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic JEX/raw tests for explicitly reviewed archive creation."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from notewrench.joplindupes import ExportError, Limits, analyze, load_export
from notewrench.joplindupes.analyze import LINK
from notewrench.joplindupes.rewrite import decision_template, rewrite_archive
from .synthetic import COMMAND, example_files, linked_files, record, uid, write_jex, write_raw

ROOT = Path(__file__).resolve().parents[2]


class RewriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.app = None
        self.inputs = [write_jex(self.root / "source.jex", example_files())]

    def cli(self, *args, expected=0):
        run = subprocess.run([*COMMAND] + list(map(str, (*self.inputs, *args))),
                             cwd=self.root if self.app else ROOT,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, expected, run.stderr)
        return run

    def template(self, expected=0):
        path = self.root / "decisions.json"
        self.cli("--decision-template", path, expected=expected)
        return path, json.loads(path.read_text())

    def approve(self, decision):
        for group in decision["groups"]:
            group["approved"] = True
            group["keeper"] = group["members"][0]
        return decision

    def apply(self, path, decision, name="result", expected=0):
        path.write_text(json.dumps(decision))
        out = self.root / name
        run = self.cli("--apply-decisions", path, "--output-dir", out, expected=expected)
        if expected:
            self.assertFalse(out.exists())
            return run
        return out, json.loads((out / "rewrite-report.json").read_text())

    def test_approved_keeper_preserves_unselected_notes_all_resources_context_and_source(self):
        before = self.inputs[0].read_bytes()
        path, decision = self.template()
        self.assertFalse(decision["groups"][0]["approved"])
        self.assertIsNone(decision["groups"][0]["keeper"])
        out, report = self.apply(path, self.approve(decision))
        export = load_export(out / "deduplicated.jex")
        self.assertEqual(len([i for i in export.items.values() if i.type == 1]), 2)
        self.assertEqual(len(export.blobs), 2)
        self.assertEqual(len(export.items), 9)  # 10 originals minus one approved duplicate.
        self.assertEqual(self.inputs[0].read_bytes(), before)
        self.assertEqual(len(report["id_map"]), 10)
        mapping = {m["old_key"]: m for m in report["id_map"]}
        keeper = mapping[f"s1:{uid(1)}"]["new_id"]
        self.assertEqual(mapping[f"s1:{uid(2)}"]["new_id"], keeper)
        self.assertEqual(mapping[f"s1:{uid(2)}"]["action"], "merged_into_keeper")
        self.assertEqual(report["omitted_notes"][0]["original"]["body"], load_export(self.inputs[0]).items[uid(2)].body)
        self.assertEqual(export.items[keeper].metadata["synthetic_extra"], "retained: yes")
        relations = [i for i in export.items.values() if i.type == 6]
        self.assertEqual(len(relations), 2)
        self.assertTrue(all(i.metadata["note_id"] == keeper for i in relations))
        self.assertTrue(report["link_rewrites"])
        self.assertTrue(report["validation"]["all_internal_references_reachable"])
        self.assertEqual((out.stat().st_mode & 0o777), 0o700)
        for name in ("deduplicated.jex", "rewrite-report.json"):
            self.assertEqual((out / name).stat().st_mode & 0o777, 0o600)
        self.assertEqual(analyze([export])["warnings"], [])

    def test_packaged_multi_export_links_same_ids_and_partial_group_approval(self):
        self.app = True  # run the installed command from an unrelated working directory
        files = linked_files()
        # Same IDs in different exports must remain distinct unless explicitly merged.
        left = write_raw(self.root / "left", files)
        right = write_jex(self.root / "right.jex", files)
        self.inputs = [left, right]
        before = right.read_bytes()
        path, decision = self.template()
        decision["groups"] = [g for g in decision["groups"] if f"s1:{uid(3)}" in g["members"]]
        self.approve(decision)
        out, report = self.apply(path, decision)
        export = load_export(out / "deduplicated.jex")
        self.assertEqual(len([i for i in export.items.values() if i.type == 1]), 5)
        ids = {m["old_key"]: m["new_id"] for m in report["id_map"]}
        self.assertNotEqual(ids[f"s1:{uid(1)}"], ids[f"s2:{uid(1)}"])
        self.assertEqual(ids[f"s1:{uid(3)}"], ids[f"s2:{uid(3)}"])
        for item in export.items.values():
            for match in LINK.finditer(item.body):
                self.assertIn(match[1].lower(), export.items)
        self.assertEqual(len(export.blobs), 2)
        self.assertEqual(right.read_bytes(), before)
        self.assertTrue(all((left / n).read_bytes() == b for n, b in files.items()))
        # Same decisions and source bytes produce byte-identical archives.
        other, _ = self.apply(path, decision, "second")
        self.assertEqual((other / "deduplicated.jex").read_bytes(), (out / "deduplicated.jex").read_bytes())

    def test_rejects_unapproved_near_subset_wrong_keeper_duplicates_and_bad_schema(self):
        path, template = self.template()
        base = self.approve(copy.deepcopy(template))
        cases = []
        for key, value in (("approved", False), ("approved", "true"), ("keeper", f"s1:{uid(3)}"),
                           ("signature", "0" * 64), ("members", [f"s1:{uid(1)}", f"s1:{uid(3)}"]),
                           ("members", [f"s1:{uid(1)}"])):
            decision = copy.deepcopy(base)
            decision["groups"][0][key] = value
            cases.append(decision)
        cases += [template, {**base, "schema_version": True}, {**base, "groups": []},
                  {**base, "groups": base["groups"] * 2}, {**base, "unexpected": True}]
        for n, decision in enumerate(cases):
            with self.subTest(n=n):
                self.apply(path, decision, f"invalid-{n}", expected=2)

    def test_all_remapped_groups_merge_with_explicit_mixed_source_keepers(self):
        self.inputs = [write_raw(self.root / "left", linked_files()),
                       write_jex(self.root / "right.jex", linked_files(1000))]
        path, decision = self.template()
        self.approve(decision)
        for group in decision["groups"]:
            if f"s1:{uid(2)}" in group["members"]:
                group["keeper"] = f"s2:{uid(1002)}"
        out, report = self.apply(path, decision)
        result = load_export(out / "deduplicated.jex")
        self.assertEqual(sum(i.type == 1 for i in result.items.values()), 3)
        ids = {m["old_key"]: m["new_id"] for m in report["id_map"]}
        self.assertIn(":/" + ids[f"s2:{uid(1002)}"], result.items[ids[f"s1:{uid(1)}"]].body)
        self.assertIn(":/" + ids[f"s1:{uid(3)}"] + "#section", result.items[ids[f"s2:{uid(1002)}"]].body)
        self.assertEqual(analyze([result])["summary"]["blocked_notes"], 0)

    def test_resource_copy_detects_mid_operation_source_changes_and_rolls_back_io_errors(self):
        exports = [load_export(self.inputs[0])]
        assessment = analyze(exports)
        decision = self.approve(decision_template(exports, assessment))
        changed = example_files()
        changed[f"resources/{uid(301)}.txt"] = b"changed after approval"
        write_jex(self.inputs[0], changed)
        out = self.root / "changed"
        with self.assertRaisesRegex(ExportError, "Stale source"):
            rewrite_archive(exports, assessment, decision, out, Limits())
        self.assertFalse(out.exists())
        write_jex(self.inputs[0], example_files())
        out = self.root / "failed-write"
        with patch("notewrench.joplindupes.rewrite.shutil.copyfileobj", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                rewrite_archive(exports, assessment, decision, out, Limits())
        self.assertFalse(out.exists())

    def test_metadata_and_title_urls_are_rewritten_and_output_limits_checked(self):
        files = example_files()
        files[uid(3) + ".md"] = record(uid(3), 1, f"See :/{uid(2).upper()}", "other body",
            conflict_original_id=uid(2), custom=f"[kept](:/{uid(301)}#fragment)")
        self.inputs = [write_jex(self.root / "metadata.jex", files)]
        path, decision = self.template()
        self.approve(decision)
        out, report = self.apply(path, decision)
        ids = {m["old_key"]: m["new_id"] for m in report["id_map"]}
        item = load_export(out / "deduplicated.jex").items[ids[f"s1:{uid(3)}"]]
        self.assertEqual(item.title, "See :/" + ids[f"s1:{uid(1)}"])
        self.assertEqual(item.metadata["conflict_original_id"], ids[f"s1:{uid(1)}"])
        self.assertEqual(item.metadata["custom"], f"[kept](:/{ids[f's1:{uid(301)}']}#fragment)")
        # Two valid small inputs may exceed the combined output budget.
        self.inputs = [write_raw(self.root / "left", linked_files()),
                       write_jex(self.root / "right.jex", linked_files(1000))]
        path.unlink()
        path, decision = self.template()
        decision["groups"] = [g for g in decision["groups"] if f"s1:{uid(3)}" in g["members"]]
        self.approve(decision)
        path.write_text(json.dumps(decision))
        self.cli("--apply-decisions", path, "--output-dir", self.root / "limited",
                 "--max-entries", "6", expected=2)
        self.assertFalse((self.root / "limited").exists())

    def test_malformed_decisions_and_incompatible_cli_modes_fail_without_output(self):
        path, decision = self.template()
        for value in ('{"schema_version": 1, "schema_version": 1}', '[]', '{', 'null'):
            path.write_text(value)
            self.cli("--apply-decisions", path, "--output-dir", self.root / "bad", expected=2)
            self.assertFalse((self.root / "bad").exists())
        for args in (("--apply-decisions", path), ("--output-dir", self.root / "bad"),
                     ("--apply-decisions", path, "--output-dir", self.root / "bad", "-o", self.root / "report"),
                     ("--decision-template", path, "--format", "markdown")):
            self.cli(*args, expected=2)

    def test_any_source_byte_change_and_source_order_are_stale(self):
        path, decision = self.template()
        self.approve(decision)
        files = example_files()
        # Even a serialization-only change invalidates approval.
        files[uid(3) + ".md"] += b"\n"
        write_jex(self.inputs[0], files)
        self.assertIn("stale", self.apply(path, decision, expected=2).stderr.lower())
        self.inputs = [write_raw(self.root / "left", linked_files()),
                       write_jex(self.root / "right.jex", linked_files(1000))]
        path.unlink()
        path, decision = self.template()
        self.approve(decision)
        self.inputs.reverse()
        self.apply(path, decision, "reordered", expected=2)

    def test_empty_and_multichunk_binary_resources_are_preserved(self):
        for n, payload in enumerate((b"", bytes(range(256)) * 1200)):
            with self.subTest(size=len(payload)):
                files = example_files()
                for resource in (301, 302):
                    name = f"resources/{uid(resource)}.txt"
                    previous_size = len(files[name])
                    files[name] = payload
                    metadata = uid(resource) + ".md"
                    files[metadata] = files[metadata].replace(f"size: {previous_size}".encode(),
                                                           f"size: {len(payload)}".encode())
                self.inputs = [write_jex(self.root / f"binary-{n}.jex", files)]
                path = self.root / "decisions.json"
                path.unlink(missing_ok=True)
                path, decision = self.template()
                out, report = self.apply(path, self.approve(decision), f"binary-result-{n}")
                result = load_export(out / "deduplicated.jex")
                self.assertEqual(len(result.blobs), 2)
                self.assertTrue(all(b.size == len(payload) for b in result.blobs.values()))
                self.assertTrue(report["validation"]["all_internal_references_reachable"])

    def test_missing_resource_dangling_context_unknown_and_unreferenced_resource_fail(self):
        for scenario in ("missing", "folder", "unknown", "unreferenced", "metadata_link"):
            with self.subTest(scenario=scenario):
                files = example_files()
                if scenario == "missing":
                    del files[f"resources/{uid(301)}.txt"]
                elif scenario == "folder":
                    del files[uid(100) + ".md"]
                elif scenario == "unknown":
                    files[uid(999) + ".md"] = record(uid(999), 99, "unknown")
                elif scenario == "unreferenced":
                    files[uid(999) + ".md"] = record(uid(999), 4, "missing.txt", file_extension="txt")
                else:
                    files[uid(3) + ".md"] += f"\ncustom: :/{uid(999)}".encode()
                # Approval cannot bypass revalidation, even when a template is freshly produced.
                self.inputs = [write_jex(self.root / f"{scenario}.jex", files)]
                path = self.root / "decisions.json"
                path.unlink(missing_ok=True)
                path, decision = self.template(expected=3 if scenario in ("missing", "folder", "unknown") else 0)
                if not decision["groups"]:
                    decision["groups"] = [{"approved": True, "keeper": f"s1:{uid(1)}",
                        "members": [f"s1:{uid(1)}", f"s1:{uid(2)}"], "signature": "0" * 64}]
                self.apply(path, self.approve(decision), scenario, expected=2)

    def test_refuses_existing_outputs_symlink_ancestors_and_writes_inside_raw(self):
        self.inputs = [write_raw(self.root / "raw", example_files())]
        path, decision = self.template()
        self.approve(decision)
        path.write_text(json.dumps(decision))
        existing = self.root / "existing"
        existing.mkdir()
        marker = existing / "keep"
        marker.write_text("keep")
        self.cli("--apply-decisions", path, "--output-dir", existing, expected=2)
        self.assertEqual(marker.read_text(), "keep")
        symlink = self.root / "linked"
        symlink.symlink_to(self.root, target_is_directory=True)
        self.cli("--apply-decisions", path, "--output-dir", symlink / "result", expected=2)
        self.assertFalse((self.root / "result").exists())
        self.cli("--apply-decisions", path, "--output-dir", self.inputs[0] / "out", expected=2)
        self.assertFalse((self.inputs[0] / "out").exists())
        self.cli("--decision-template", self.inputs[0] / "decisions.json", expected=2)
        self.assertFalse((self.inputs[0] / "decisions.json").exists())


if __name__ == "__main__":
    unittest.main()
