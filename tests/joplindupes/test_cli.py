# SPDX-License-Identifier: AGPL-3.0-only
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import notewrench
from .synthetic import COMMAND, example_files, linked_files, record, uid, write_jex, write_raw

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples" / "joplin-dupes"

_BENCHMARK = Path(__file__).resolve().parents[2] / "benchmarks" / "joplin-dupes" / "benchmark.py"


def run_benchmark(*args):
    """Load the benchmark script by path; ``benchmarks/`` is not a package."""
    spec = importlib.util.spec_from_file_location("_notewrench_joplindupes_benchmark", _BENCHMARK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.run_benchmark(*args)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw = write_raw(self.root / "raw", example_files())
        self.jex = write_jex(self.root / "input.jex", example_files())

    def cli(self, *args, expected=0):
        run = subprocess.run([*COMMAND, *map(str, args)],
                             cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, expected, run.stderr)
        return run

    def test_shipped_examples_match_documented_generated_bytes(self):
        files = example_files()
        for name, data in files.items():
            self.assertEqual((EXAMPLES / "raw" / name).read_bytes(), data)
        report = json.loads(self.cli(EXAMPLES / "synthetic.jex").stdout)
        self.assertEqual(report["summary"]["exact_groups"], 1)
        self.assertEqual(report["summary"]["near_groups"], 0)
        self.assertEqual(report["summary"]["notes"], 3)

    def test_help_and_version(self):
        self.assertIn("Read-only", self.cli("--help").stdout)
        self.assertEqual(self.cli("--version").stdout, f"notewrench joplin-dupes {notewrench.__version__}\n")

    def test_json_stdout_and_markdown_output_from_real_jex(self):
        before = self.jex.read_bytes()
        run = self.cli(self.jex)
        report = json.loads(run.stdout)
        self.assertEqual(report["summary"]["exact_groups"], 1)
        self.assertEqual(run.stderr, "")
        out = self.root / "report.md"
        self.cli(self.jex, "--format", "markdown", "--output", out)
        self.assertIn("## Exact duplicates", out.read_text())
        self.assertEqual(out.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.jex.read_bytes(), before)

    def test_exact_markdown_names_keep_values_in_complete_json(self):
        files = {uid(n) + ".md": record(uid(n), 1, "Same", "same body",
                                      **({"__private__": "VALUE_ONLY_IN_JSON", "absent__": ""}
                                         if n == 1 else {"__private__": ""}))
                 for n in (1, 2)}
        path = write_jex(self.root / "exact.jex", files)
        before = path.read_bytes()
        json_text = self.cli(path).stdout
        output = self.root / "exact.md"
        self.cli(path, "--format", "markdown", "-o", output)
        markdown = output.read_text()
        overview = markdown.split("## Exact duplicates\n\n", 1)[1].split(
            "\n\n## Near candidates", 1)[0]
        self.assertEqual(overview,
                         f"- `s1:{uid(1)}`, `s1:{uid(2)}`; conflicting fields: 2. "
                         "Review all conflicts below. Conflicting fields (manual review): "
                         "`metadata.__private__`, `metadata.absent__`.")
        self.assertNotIn("VALUE_ONLY_IN_JSON", overview)
        self.assertIn("```json\n" + json_text + "```", markdown)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.read_bytes(), before)
        self.cli(path, "--format", "markdown", "-o", output, expected=2)
        self.assertEqual(output.read_text(), markdown)

    def test_near_markdown_names_keep_values_in_complete_json(self):
        files = {uid(n) + ".md": record(uid(n), 1, "Same", "same body", is_todo=n-1,
                                      __private__="VALUE_ONLY_IN_JSON" if n == 1 else "")
                 for n in (1, 2)}
        path = write_jex(self.root / "near.jex", files)
        before = path.read_bytes()
        json_text = self.cli(path).stdout
        output = self.root / "near.md"
        self.cli(path, "--format", "markdown", "-o", output)
        markdown = output.read_text()
        overview = markdown.split("## Near candidates\n\n", 1)[1].split(
            "\n\n## Complete evidence", 1)[0]
        self.assertEqual(overview,
                         f"- `s1:{uid(1)}`, `s1:{uid(2)}`; todo_conflict_or_trash_state_differs; "
                         "conflicting fields (manual comparison): `metadata.__private__`, "
                         "`metadata.is_todo`, `state`.")
        self.assertNotIn("VALUE_ONLY_IN_JSON", overview)
        self.assertIn("```json\n" + json_text + "```", markdown)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.read_bytes(), before)
        self.cli(path, "--format", "markdown", "-o", output, expected=2)
        self.assertEqual(output.read_text(), markdown)

    def test_identity_markdown_separates_same_id_conflicts_from_duplicates(self):
        paths = [write_jex(self.root / f'identity-{n}.jex', {
            uid(1) + '.md': record(uid(1), 1, 'Same', body,
                                   __private__='VALUE_ONLY_IN_JSON' if n == 1 else '')})
            for n, body in ((1, 'first body'), (2, 'changed body'))]
        before = [p.read_bytes() for p in paths]
        json_text = self.cli(*paths).stdout
        report = json.loads(json_text)
        self.assertEqual(report['exact_duplicates'], [])
        self.assertEqual(report['near_candidates'], [])
        self.assertEqual(report['summary']['identity_conflicts'], 1)
        self.assertEqual(sorted(report['identity_conflicts'][0]['conflicts']),
                         ['content_signature', 'metadata.__private__', 'normalized_body_sha256'])
        output = self.root / 'identity.md'
        self.cli(*paths, '--format', 'markdown', '-o', output)
        markdown = output.read_text()
        overview = markdown.split('## Identity conflicts\n\n', 1)[1].split(
            '\n\n## Exact duplicates', 1)[0]
        self.assertEqual(overview,
                         'Same note ID across snapshots does not imply equal content. '
                         'Manual review required; no keeper or merge approval is selected.\n\n'
                         f'- Original note ID `{uid(1)}`; occurrences: `s1:{uid(1)}`, `s2:{uid(1)}`; '
                         'conflicting fields (manual review): `content_signature`, '
                         '`metadata.__private__`, `normalized_body_sha256`.')
        self.assertNotIn('VALUE_ONLY_IN_JSON', overview)
        self.assertNotIn('changed body', overview)
        self.assertIn('```json\n' + json_text + '```', markdown)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual([p.read_bytes() for p in paths], before)
        self.cli(*paths, '--format', 'markdown', '-o', output, expected=2)
        self.assertEqual(output.read_text(), markdown)

    def test_incomplete_and_unknown_inputs_exit_three_with_report(self):
        (self.raw / "resources" / (uid(302) + ".txt")).unlink()
        (self.raw / (uid(999) + ".md")).write_bytes(record(uid(999), 99, "Unknown", future_field="kept"))
        report = json.loads(self.cli(self.raw, expected=3).stdout)
        self.assertEqual(report["summary"]["blocked_notes"], 1)
        self.assertEqual(report["summary"]["unknown_items"], 1)
        self.assertEqual(report["unknown_items"][0]["metadata"]["future_field"], "kept")
        self.assertFalse(report["exact_duplicates"])

    def test_blocked_markdown_copies_only_blocked_keys_reasons_and_preserves_exit(self):
        from test_rewrite_retained import preservation_files
        blocked = write_jex(self.root / 'blocked.jex', preservation_files())
        for path, code, count in [(blocked, 3, 6), (self.jex, 0, 0)]:
            with self.subTest(count=count):
                before = path.read_bytes()
                json_text = self.cli(path, expected=code).stdout
                report = json.loads(json_text)
                notes = [n for n in report['notes'] if n['blocked_reasons']]
                self.assertEqual(len(notes), count)
                output = self.root / f'blocked-{count}.md'
                run = self.cli(path, '--format', 'markdown', '-o', output, expected=code)
                self.assertEqual(run.stdout, '')
                markdown = output.read_text()
                self.assertIn('## Blocked notes\n\n', markdown)
                overview = markdown.split('## Blocked notes\n\n', 1)[1].split(
                    '\n\n## Identity conflicts', 1)[0]
                expected = ['Manual review required; blocked notes do not establish duplicate status '
                            'or target correspondence. No keeper or merge approval is selected.', '']
                expected += [f"- `{n['key']}`; blocked reasons: "
                             + ', '.join(f'`{r}`' for r in n['blocked_reasons']) + '.' for n in notes]
                if not notes:
                    expected.append('No blocked notes.')
                self.assertEqual(overview, '\n'.join(expected))
                self.assertIn('```json\n' + json_text + '```', markdown)
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                self.cli(path, '--format', 'markdown', '-o', output, expected=2)
                self.assertEqual(output.read_text(), markdown)
                self.assertEqual(path.read_bytes(), before)

    def test_errors_do_not_create_report_or_modify_source(self):
        invalid = self.root / "invalid.jex"
        invalid.write_bytes(b"not tar")
        out = self.root / "report.json"
        run = self.cli(invalid, "-o", out, expected=2)
        self.assertIn("notewrench joplin-dupes:", run.stderr)
        self.assertEqual(run.stdout, "")
        self.assertFalse(out.exists())
        self.assertEqual(invalid.read_bytes(), b"not tar")

    def test_protect_inputs_existing_reports_symlinks_and_hardlinks(self):
        before = self.jex.read_bytes()
        self.cli(self.jex, "-o", self.jex, expected=2)
        self.cli(self.raw, "-o", self.raw / "report.json", expected=2)
        self.assertFalse((self.raw / "report.json").exists())
        out = self.root / "existing"
        out.write_text("keep")
        self.cli(self.jex, "-o", out, expected=2)
        self.assertEqual(out.read_text(), "keep")
        symlink = self.root / "symlink"
        symlink.symlink_to(self.jex)
        self.cli(self.jex, "-o", symlink, expected=2)
        hardlink = self.root / "hardlink"
        hardlink.hardlink_to(self.jex)
        self.cli(self.jex, "-o", hardlink, expected=2)
        self.assertEqual(self.jex.read_bytes(), before)
        real_parent = self.root / "real-parent"
        real_parent.mkdir()
        linked_parent = self.root / "linked-parent"
        linked_parent.symlink_to(real_parent, target_is_directory=True)
        self.cli(self.jex, "-o", linked_parent / "report.json", expected=2)
        self.assertFalse((real_parent / "report.json").exists())

    def test_limits_and_unknown_arguments(self):
        for args in (("--max-entries", "0"), ("--max-file-bytes", "-1"),
                     ("--max-total-bytes", "text"), ("--delete",), ("--dry-run",)):
            self.cli(self.jex, *args, expected=2)
        run = self.cli(self.jex, "--max-file-bytes", "4", expected=2)
        self.assertIn("limit", run.stderr)

    def test_multiple_sources_and_duplicate_path_detection(self):
        report = json.loads(self.cli(self.raw, self.jex).stdout)
        self.assertEqual(len(report["sources"]), 2)
        self.assertEqual(report["summary"]["notes"], 6)
        self.assertEqual(sorted(len(g["members"]) for g in report["exact_duplicates"]), [2, 4])
        self.cli(self.raw, self.raw, expected=2)

    def test_installed_command_from_unrelated_cwd(self):
        run = subprocess.run([*COMMAND, str(self.jex)], cwd=self.root,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)["summary"]["exact_groups"], 1)
        (self.raw / "resources" / (uid(302) + ".txt")).unlink()
        run = subprocess.run([*COMMAND, str(self.raw)], cwd=self.root,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 3, run.stderr)
        self.assertEqual(json.loads(run.stdout)["summary"]["blocked_notes"], 1)
        run = subprocess.run([*COMMAND, "missing.jex"], cwd=self.root,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(run.returncode, 2)

    def test_labeled_baseline_accuracy_and_counts_are_replayable(self):
        a, b = run_benchmark(3, 2), run_benchmark(3, 1)
        for key in ("title_only", "body_resource_state"):
            for metric in ("counts", "precision", "recall", "true_positives", "false_positives", "false_negatives"):
                self.assertEqual(a[key][metric], b[key][metric])
            self.assertTrue(all(t > 0 for t in a[key]["elapsed_seconds"]))
        self.assertEqual(a["title_only"]["true_positives"], 3)
        self.assertEqual(a["title_only"]["false_positives"], 27)
        self.assertEqual(a["title_only"]["false_negatives"], 6)
        self.assertEqual(a["body_resource_state"]["true_positives"], 9)
        self.assertEqual(a["body_resource_state"]["false_positives"], 0)
        self.assertEqual(a["body_resource_state"]["false_negatives"], 0)
        self.assertEqual(a["body_resource_state"]["counts"]["resource_hashes"], 18)
        self.assertEqual(a["title_only"]["counts"]["resource_hashes"], 0)
        with self.assertRaises(ValueError):
            run_benchmark(0, 1)

    def test_packaged_remapped_note_links_and_review_failures(self):
        left_files = linked_files()
        left = write_raw(self.root / "linked-left", left_files)
        for scenario, expected_exit, exact_groups, blocked in (
            ("equal", 0, 3, 0), ("changed", 0, 0, 0), ("missing", 3, 0, 2),
            ("ambiguous", 3, 1, 4), ("cycle", 3, 0, 3),
        ):
            with self.subTest(scenario=scenario):
                right = linked_files(1000)
                if scenario == "changed":
                    right[uid(1003) + ".md"] = right[uid(1003) + ".md"].replace(b"detail\n", b"changed detail\n")
                elif scenario == "missing":
                    del right[uid(1003) + ".md"]
                elif scenario == "ambiguous":
                    right[uid(1004) + ".md"] = right[uid(1003) + ".md"].replace(uid(1003).encode(), uid(1004).encode())
                elif scenario == "cycle":
                    right[uid(1003) + ".md"] = record(uid(1003), 1, "Detail", f"[cycle](:/{uid(1001)})")
                jex = write_jex(self.root / f"linked-{scenario}.jex", right)
                before = jex.read_bytes()
                run = subprocess.run([*COMMAND, str(left), str(jex)], cwd=self.root,
                                     capture_output=True, text=True, timeout=60)
                self.assertEqual(run.returncode, expected_exit, run.stderr)
                report = json.loads(run.stdout)
                self.assertEqual(report["summary"]["exact_groups"], exact_groups)
                self.assertEqual(report["summary"]["blocked_notes"], blocked)
                self.assertEqual(jex.read_bytes(), before)
                self.assertTrue(all((left / name).read_bytes() == data for name, data in left_files.items()))
                if scenario != "equal":
                    self.assertTrue(any(f"s1:{uid(1)}" in group["members"]
                                        and f"s2:{uid(1001)}" in group["members"]
                                        for group in report["near_candidates"]))
