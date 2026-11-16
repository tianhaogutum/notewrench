# SPDX-License-Identifier: AGPL-3.0-only
# Port of linkdedupe tests/cli.test.js.
import json
import os
import subprocess
import sys

from .helpers import ROOT, SRC, fixture, invoke, js, listdir

ROW = {'id': 1, 'title': 'synthetic', 'url': 'https://example.org/a', 'tags': [], 'created_at': '2026-11-11', 'is_archived': False}


def write(path, data):
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding='utf-8')


def test_cli_exports_all_four_reports_and_refuses_to_overwrite(cwd):
    args = ['--out', 'output', fixture('karakeep.json'), fixture('wallabag-v2.json')]
    result = invoke(args, cwd)
    assert result.status == 0, result.stderr
    assert json.loads(result.stdout)['candidateClusters'] == 1
    assert listdir(cwd / 'output') == ['clusters.json', 'merge-plan.json', 'provenance.json', 'records.json']
    original = (cwd / 'output' / 'records.json').read_text(encoding='utf-8')
    retry = invoke(args, cwd)
    assert retry.status == 1
    assert 'already exists' in retry.stderr
    assert (cwd / 'output' / 'records.json').read_text(encoding='utf-8') == original


def test_dry_run_validates_real_inputs_without_writing_including_script_looking_content(cwd):
    write(cwd / 'input.json', js([{**ROW, 'content': '<script>require("fs").writeFileSync("EXECUTED", "yes")</script>'}]))
    before = listdir(cwd)
    result = invoke(['--dry-run', '--out', 'would-write', 'input.json'], cwd)
    assert result.status == 0, result.stderr
    assert json.loads(result.stdout)['dryRun'] is True
    assert listdir(cwd) == before


def test_strict_failure_creates_no_output_skip_preserves_raw_rejected_records_and_warns(cwd):
    write(cwd / 'input.json', js([ROW, {**ROW, 'url': 'javascript:alert(1)'}]))
    failure = invoke(['--out', 'strict', 'input.json'], cwd)
    assert failure.status == 1
    assert 'strict mode' in failure.stderr
    assert listdir(cwd) == ['input.json']
    skipped = invoke(['--skip-invalid', '--out', 'skipped', 'input.json'], cwd)
    assert skipped.status == 0, skipped.stderr
    assert json.loads(skipped.stdout)['skippedRecords'] == 1
    assert 'Quarantined 1' in skipped.stderr
    provenance = json.loads((cwd / 'skipped' / 'provenance.json').read_text(encoding='utf-8'))
    assert provenance['records'][1]['raw']['url'] == 'javascript:alert(1)'
    assert provenance['records'][1]['status'] == 'rejected'


def test_mixed_explicit_formats_work_ambiguous_detection_requests_format(cwd):
    write(cwd / 'minimal.json', js([{'title': 'minimal', 'url': 'https://example.org/a', 'tags': []}]))
    ambiguous = invoke(['--dry-run', 'minimal.json'], cwd)
    assert ambiguous.status == 1
    assert 'ambiguous' in ambiguous.stderr
    result = invoke(['--dry-run', '--format', 'karakeep', '--format', 'wallabag-v2', fixture('karakeep.json'), 'minimal.json'], cwd)
    assert result.status == 0, result.stderr
    assert json.loads(result.stdout)['candidateClusters'] == 1
    assert invoke(['--dry-run', '--format', 'wallabag-v2', 'minimal.json'], cwd).status == 0


def test_invalid_cli_options_unsafe_rules_missing_files_malformed_utf8_json_fail(cwd):
    valid = fixture('wallabag-v2.json')
    for args in [[], [valid], ['--dry-run', '--bogus', valid], ['--dry-run', '--rules', '', valid],
                 ['--dry-run', '--drop-param', '*', valid], ['--dry-run', '--format', 'invented', valid],
                 ['--dry-run', '--format', 'auto', '--format', 'auto', valid], ['--dry-run', 'missing.json'],
                 ['--out', 'absent/child', valid]]:
        assert invoke(args, cwd).status == 1, ' '.join(args)
    for name, data in [('utf8.json', b'\xff'), ('bad.json', '{broken'), ('duplicates.json', '{"bookmarks":[],"bookmarks":[]}')]:
        write(cwd / name, data)
        result = invoke(['--skip-invalid', '--out', f'{name}-output', name], cwd)
        assert result.status == 1, result.stderr
        assert f'{name}-output' not in listdir(cwd)


def test_output_symlinks_are_refused(cwd):
    (cwd / 'real').mkdir()
    write(cwd / 'real' / 'keep', 'unchanged')
    os.symlink('real', cwd / 'output')
    result = invoke(['--out', 'output', fixture('wallabag-v2.json')], cwd)
    assert result.status == 1
    assert (cwd / 'real' / 'keep').read_text() == 'unchanged'


def test_help_version_and_module_entry_points_work_without_installation():
    # Equivalent of the package-root import check: run from the repository root
    # with only src/ on the path, through the dispatcher and the subpackage.
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [str(SRC), os.environ.get('PYTHONPATH')])))
    for module in (['-m', 'notewrench', 'links'], ['-m', 'notewrench.linkdedupe']):
        help_run = subprocess.run([sys.executable, *module, '--help'], cwd=ROOT, env=env, capture_output=True,
                                  text=True, encoding='utf-8', timeout=120)
        assert help_run.returncode == 0, help_run.stderr
        assert 'usage: notewrench links' in help_run.stdout
        version = subprocess.run([sys.executable, *module, '--version'], cwd=ROOT, env=env, capture_output=True,
                                 text=True, encoding='utf-8', timeout=120)
        assert version.stdout == 'notewrench links 1.0.0\n'
    code = ('from notewrench.linkdedupe import normalize_url\n'
            'raise SystemExit(normalize_url("https://EXAMPLE.org/a")["url"] != "https://example.org/a")')
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr


def test_in_process_help_and_version(cwd):
    assert 'usage: notewrench links' in invoke(['--help'], cwd).stdout
    assert invoke(['--version'], cwd).stdout == 'notewrench links 1.0.0\n'


def test_rules_dry_run_exposes_per_rule_comparisons_without_writes_normal_reports_preserve_them(cwd):
    args = ['--rules', fixture('host-rules.json'), fixture('host-rules-wallabag-v2.json')]
    before = listdir(cwd)
    dry = invoke(['--dry-run', '--out', 'unused', *args], cwd)
    assert dry.status == 0, dry.stderr
    summary = json.loads(dry.stdout)
    assert summary['canonicalRecords'] == 6
    assert summary['candidateClusters'] == 1
    assert len(summary['ruleComparisons']) == 5
    assert summary['ruleSummary'][0]['changedUrls'] == 2
    assert summary['ruleSummary'][1]['protectedUrls'] == 1
    catalog = next(item for item in summary['ruleComparisons'] if item['sourceRef'] == 's1:r1')
    assert catalog['ruleId'] == 'catalog-business-source'
    assert catalog['beforeUrl'] == 'https://catalog.example/a'
    assert catalog['afterUrl'] == 'https://catalog.example/a?utm_source=edition-one'
    assert catalog['source']['pointer'] == '/rules/0'
    assert next(item for item in summary['ruleComparisons'] if item['sourceRef'] == 's1:r7')['status'] == 'protected'
    assert listdir(cwd) == before
    written = invoke(['--out', 'reports', *args], cwd)
    assert written.status == 0, written.stderr
    records = json.loads((cwd / 'reports' / 'records.json').read_text(encoding='utf-8'))
    assert records['records'][0]['members'][0]['ruleComparisons'][0]['afterUrl'] == catalog['afterUrl']
    assert records['normalization']['rules']['source']['sha256'] == catalog['source']['sha256']


def test_invalid_rules_abort_before_publication_even_with_skip_invalid_and_dry_run(cwd):
    documents = [
        '{"schemaVersion":1,"rules":[{"id":"bad","host":"news.example","dropParams":["ref"],"allowSigned":true}]}',
        '{"schemaVersion":1,"rules":[{"id":"a","host":"news.example","keepParams":["ref"]},{"id":"b","host":"NEWS.example","dropParams":["ref"]}]}',
        '{"schemaVersion":1,"rules":[],"rules":[]}',
        '{"schemaVersion":1,"rules":[{"id":"a","host":"news.example","dropParams":["token"]}]}',
        b'\xff',
    ]
    for data in documents:
        write(cwd / 'rules.json', data)
        for extra in [[], ['--dry-run']]:
            result = invoke(['--skip-invalid', '--out', 'never-created', *extra, '--rules', 'rules.json',
                             fixture('host-rules-wallabag-v2.json')], cwd)
            assert result.status == 1, result.stderr
            assert result.stdout == ''
            assert 'rules.json' in result.stderr
            assert listdir(cwd) == ['rules.json']


def test_dry_run_accounts_for_unused_host_rules_and_matching_hosts_with_no_target_parameters(cwd):
    rules = {'schemaVersion': 1, 'rules': [
        {'id': 'unused-host', 'host': 'absent.example', 'dropParams': ['ref']},
        {'id': 'unused-key', 'host': 'example.org', 'keepParams': ['utm_source']},
    ]}
    write(cwd / 'rules.json', js(rules))
    write(cwd / 'input.json', js([ROW]))
    result = invoke(['--dry-run', '--rules', 'rules.json', 'input.json'], cwd)
    assert result.status == 0, result.stderr
    data = json.loads(result.stdout)
    assert len(data['ruleSummary']) == 2
    assert data['ruleSummary'][0]['hostMatchedUrls'] == 0
    assert data['ruleSummary'][0]['source']['pointer'] == '/rules/0'
    assert data['ruleSummary'][1]['hostMatchedUrls'] == 1
    assert data['ruleSummary'][1]['parameterMatchedUrls'] == 0
    assert data['ruleComparisons'][0]['status'] == 'no-match'
    assert listdir(cwd) == ['input.json', 'rules.json']


def test_native_wallabag_two_pass_decisions_workflow_round_trips_and_stale_decisions_write_nothing(cwd):
    source = fixture('wallabag-merge-v2.json')
    original = open(source, encoding='utf-8').read()
    args = ['--wallabag-export', source]
    preview = invoke(['--out', 'review', *args], cwd)
    assert preview.status == 0, preview.stderr
    assert json.loads(preview.stdout)['wallabag']['outputRecords'] == 3
    decisions = json.loads((cwd / 'review' / 'wallabag-decisions.json').read_text(encoding='utf-8'))
    decisions['decisions'][0]['approved'] = True
    write(cwd / 'approved.json', js(decisions))
    before_dry = listdir(cwd)
    dry = invoke(['--dry-run', '--out', 'unused', '--decisions', 'approved.json', *args], cwd)
    assert dry.status == 0, dry.stderr
    assert json.loads(dry.stdout)['wallabag']['mergedRecords'] == 1
    assert listdir(cwd) == before_dry
    merged = invoke(['--out', 'merged', '--decisions', 'approved.json', *args], cwd)
    assert merged.status == 0, merged.stderr
    exported = json.loads((cwd / 'merged' / 'wallabag-export.json').read_text(encoding='utf-8'))
    assert len(exported) == 2
    assert len(exported[0]['annotations']) == 2
    reread = invoke(['--dry-run', '--format', 'wallabag-v2', 'merged/wallabag-export.json'], cwd)
    assert reread.status == 0, reread.stderr
    assert json.loads(reread.stdout)['acceptedRecords'] == 2
    audit = json.loads((cwd / 'merged' / 'wallabag-merge-report.json').read_text(encoding='utf-8'))
    assert len(audit['mapping']) == 3
    assert audit['roundTrip']['verified'] is True
    assert any(field['pointer'] == '/note' for field in audit['unrepresentedFields'])
    write(cwd / 'changed.json', original + '\n')
    for extra in [[], ['--dry-run'], ['--skip-invalid']]:
        failed = invoke(['--wallabag-export', '--decisions', 'approved.json', '--out', 'failed', *extra, 'changed.json'], cwd)
        assert failed.status == 1
        assert 'basis' in failed.stderr and 'decision' in failed.stderr.lower()
        assert failed.stdout == ''
        assert 'failed' not in listdir(cwd)
    assert invoke(['--dry-run', '--decisions', 'approved.json', source], cwd).status == 1
    assert open(source, encoding='utf-8').read() == original


def test_optional_cluster_comparison_is_identical_in_dry_run_stdout_and_written_clusters_report(cwd):
    args = ['--compare-clusters', '--rules', fixture('host-rules.json'), fixture('host-rules-wallabag-v2.json')]
    dry = invoke(['--dry-run', '--out', 'unused', *args], cwd)
    assert dry.status == 0, dry.stderr
    comparison = json.loads(dry.stdout)['clusterComparison']
    assert comparison['enteredSourceRefs'] == ['s1:r3', 's1:r4']
    assert comparison['leftSourceRefs'] == ['s1:r1', 's1:r2']
    assert len(comparison['changes']) == 4
    assert listdir(cwd) == []
    written = invoke(['--out', 'review', *args], cwd)
    assert written.status == 0, written.stderr
    assert json.loads(written.stdout)['clusterComparison'] == comparison
    clusters = json.loads((cwd / 'review' / 'clusters.json').read_text(encoding='utf-8'))
    assert clusters['clusterComparison'] == comparison
    assert listdir(cwd / 'review') == ['clusters.json', 'merge-plan.json', 'provenance.json', 'records.json']
    ordinary = invoke(['--dry-run', '--rules', fixture('host-rules.json'), fixture('host-rules-wallabag-v2.json')], cwd)
    assert ordinary.status == 0, ordinary.stderr
    assert 'clusterComparison' not in json.loads(ordinary.stdout)


def test_cluster_comparison_without_rules_or_with_invalid_inputs_fails_before_writing(cwd):
    for extra in [[], ['--dry-run'], ['--skip-invalid']]:
        result = invoke(['--compare-clusters', '--out', 'failed', *extra, fixture('wallabag-v2.json')], cwd)
        assert result.status == 1
        assert result.stdout == ''
        assert '--compare-clusters requires --rules' in result.stderr
        assert listdir(cwd) == []
    write(cwd / 'invalid.json', js([{**ROW, 'url': 'broken'}]))
    failed = invoke(['--compare-clusters', '--rules', fixture('host-rules.json'), '--out', 'failed', 'invalid.json'], cwd)
    assert failed.status == 1
    assert failed.stdout == ''
    assert 'strict mode' in failed.stderr
    assert listdir(cwd) == ['invalid.json']
    write(cwd / 'invalid-rules.json', '{"schemaVersion":1,"rules":[]}')
    for extra in [[], ['--dry-run']]:
        bad_rules = invoke(['--compare-clusters', '--skip-invalid', '--rules', 'invalid-rules.json', '--out', 'failed',
                            *extra, fixture('wallabag-v2.json')], cwd)
        assert bad_rules.status == 1
        assert bad_rules.stdout == ''
        assert 'rules' in bad_rules.stderr
        assert listdir(cwd) == ['invalid-rules.json', 'invalid.json']
