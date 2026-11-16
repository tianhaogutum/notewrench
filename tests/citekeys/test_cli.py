# SPDX-License-Identifier: AGPL-3.0-only
"""Port of test/cli.test.js (in-process ``cli.main`` plus a few real subprocess runs)."""
from __future__ import annotations

import json
import os

import pytest

from notewrench.citekeys import build_mapping, rekey_markdown

from .conftest import (assert_edit_location, error, git_apply, index_of, js_slice, load_fixture, requires_git,
                       run_subprocess, snapshot, unknown_location)

KINDS = ['closers', 'openers', 'tabs', 'flows', 'sequences']


@requires_git
@pytest.mark.parametrize('kind', KINDS)
def test_cli_padded_frontmatter_preserve_bytes_locations_dry_run_and_applicable_diffs(project, kind):
    fixture = load_fixture(f'frontmatter-{kind}')
    project.put('old.json', json.dumps(fixture['old']))
    project.put('new.json', json.dumps(fixture['new']))
    for example in fixture['examples']:
        name, text, expected = example['name'], example['input'], example['expected']
        project.put(f'input/{name}.md', text)
        before = snapshot(project.dir)
        preview = project.run(f'{name}.md')
        assert preview.status == example['exitCode'], preview.stderr
        assert snapshot(project.dir) == before
        result = project.run('--out-dir', f'{name}-output', f'{name}.md')
        assert result.status == example['exitCode'], result.stderr
        document = json.loads(result.stdout)['documents'][0]
        assert document == json.loads(preview.stdout)['documents'][0]
        assert len(document['edits']) == example['edits']
        assert document['unsupported'] == []
        assert document['unresolved'] == ([unknown_location(text)] if example['exitCode'] else [])
        for edit in document['edits']:
            assert_edit_location(text, edit)
        assert project.read(f'{name}-output/{name}.md') == expected.encode()
        assert project.read(f'input/{name}.md') == text.encode()
        (project.dir / f'{name}-patch').mkdir()
        project.put(f'{name}-patch/{name}.md', text)
        patch = git_apply(project.dir / f'{name}-patch', document['diff'], '--allow-empty', '--whitespace=nowarn', '-')
        assert patch.returncode == 0, patch.stderr
        assert project.read(f'{name}-patch/{name}.md') == expected.encode()


@pytest.mark.parametrize('kind', KINDS)
def test_cli_padded_frontmatter_retain_fatal_batch_preflight_before_stdout_or_copies(project, kind):
    fixture = load_fixture(f'frontmatter-{kind}')
    project.put('old.json', json.dumps(fixture['old']))
    project.put('new.json', json.dumps(fixture['new']))
    project.put('input/paper.md', fixture['examples'][0]['input'])
    for invalid in fixture['invalid']:
        project.put('input/bad.md', invalid['input'])
        before = snapshot(project.dir)
        result = project.run('--out-dir', 'refused-output', 'paper.md', 'bad.md')
        error(result, invalid['code'])
        assert result.stdout == ''
        assert snapshot(project.dir) == before
        assert not (project.dir / 'refused-output').exists()


def test_cli_default_and_explicit_dry_run_make_zero_filesystem_writes(project):
    before = snapshot(project.dir)
    for flags in [[], ['--dry-run']]:
        result = project.run(*flags, 'paper.md')
        assert result.status == 0, result.stderr
        report = json.loads(result.stdout)
        assert report['mode'] == 'dry-run'
        assert len(report['documents'][0]['edits']) == 1
        assert '+See [@New].' in report['documents'][0]['diff']
        assert report['written'] == []
        assert snapshot(project.dir) == before


def test_real_file_e2e_writes_nested_copies_preserves_sources_and_exact_crlf_bom_bytes(project):
    (project.dir / 'input/notes').mkdir()
    before = '\ufeff中文 🐝\r\n[@old]\r\n`[@old]`'
    project.put('input/notes/unicode.md', before)
    result = project.run('--out-dir', 'output', 'paper.md', 'notes/unicode.md')
    assert result.status == 0, result.stderr
    assert len(json.loads(result.stdout)['written']) == 2
    assert project.read('output/notes/unicode.md') == before.replace('\r\n[@old]', '\r\n[@New]', 1).encode()
    assert project.read('input/notes/unicode.md') == before.encode()


def test_unknown_key_preserved_and_reported_with_exit_1_even_when_writing_copies(project):
    project.put('input/paper.md', '[@old; @unknown]')
    result = project.run('--out-dir', 'output', 'paper.md')
    assert result.status == 1
    assert json.loads(result.stdout)['documents'][0]['unresolved'][0]['key'] == 'unknown'
    assert project.read('output/paper.md') == b'[@New; @unknown]'


def test_cli_protects_multiline_code_across_indented_lines_and_edits_only_real_prose(project):
    code = '\ufeffOpening `literal [@old]\r\n\tcontinuation [@unknown]\r\nclosing`'
    before = f'{code} then prose [@old].'
    project.put('input/paper.md', before)
    preview = project.run('paper.md')
    assert preview.status == 0, preview.stderr
    result = project.run('--out-dir', 'output', 'paper.md')
    assert result.status == 0, result.stderr
    document = json.loads(result.stdout)['documents'][0]
    assert len(document['edits']) == 1
    assert document['unresolved'] == []
    assert document['unsupported'] == []
    assert document == json.loads(preview.stdout)['documents'][0]
    assert project.read('output/paper.md') == f'{code} then prose [@New].'.encode()
    assert project.read('input/paper.md') == before.encode()


def test_unsupported_citation_syntax_stays_intact_with_explicit_diagnostic_and_exit_1(project):
    project.put('input/paper.md', 'See [@old, p. 2; prose]. Then [@old].')
    result = project.run('--out-dir', 'output', 'paper.md')
    assert result.status == 1
    assert len(json.loads(result.stdout)['documents'][0]['unsupported']) == 1
    assert project.read('output/paper.md') == b'See [@old, p. 2; prose]. Then [@New].'


@requires_git
def test_cli_same_line_citation_affixes_preserve_exact_bytes_spans_and_whole_ambiguous_groups(project):
    citation = load_fixture('citations')
    project.put('old.json', json.dumps(citation['old']))
    project.put('new.json', json.dumps(citation['new']))
    for name, text, expected, status, count in [
        ('positive', citation['input'], citation['expected'], 0, 4),
        ('review', citation['reviewInput'], citation['reviewExpected'], 1, 2),
    ]:
        project.put(f'input/{name}.md', text)
        before = snapshot(project.dir)
        preview = project.run(f'{name}.md')
        assert preview.status == status, preview.stderr
        assert snapshot(project.dir) == before
        result = project.run('--out-dir', f'{name}-output', f'{name}.md')
        assert result.status == status, result.stderr
        document = json.loads(result.stdout)['documents'][0]
        assert document == json.loads(preview.stdout)['documents'][0]
        assert len(document['edits']) == count
        assert project.read(f'{name}-output/{name}.md') == expected.encode()
        assert project.read(f'input/{name}.md') == text.encode()
        for edit in document['edits']:
            assert_edit_location(text, edit)
        if status == 0:
            assert document['unsupported'] == []
            assert document['unresolved'] == []
        else:
            assert len(document['unsupported']) == 1
            diagnostic = document['unsupported'][0]
            assert js_slice(text, diagnostic['offset'], diagnostic['offset'] + diagnostic['length']) == citation['unsupported']
            assert [item['key'] for item in document['unresolved']] == ['unknown']
            assert document['unresolved'][0]['offset'] == index_of(text, 'unknown')
        # The report remains an applicable patch of the original source bytes.
        project.put(f'{name}.patch', document['diff'])
        patch = git_apply(project.dir, '', '--unsafe-paths', '--directory=input', str(project.dir / f'{name}.patch'))
        assert patch.returncode == 0, patch.stderr
        assert project.read(f'input/{name}.md') == expected.encode()


def test_identity_conflict_refuses_every_output_and_reports_conflicts(project):
    project.put('new.json', json.dumps([{'id': 'b', 'DOI': '10.123/demo'}, {'id': 'c', 'DOI': '10.123/DEMO'}]))
    result = project.run('--out-dir', 'output', 'paper.md')
    assert result.status == 2
    report = json.loads(result.stdout)
    assert report['conflicts'][0]['code'] == 'DUPLICATE_IDENTITY'
    assert report['mapping'] == []
    assert report['written'] == []
    assert not (project.dir / 'output').exists()


def test_missing_identity_is_not_guessed_and_exit_1_lists_unmatched_export_record(project):
    project.put('old.json', '[{"id":"old","title":"same"}]')
    result = project.run('paper.md')
    assert result.status == 1
    assert json.loads(result.stdout)['unmatched'][0]['reason'] == 'missing_identity'
    assert json.loads(result.stdout)['documents'][0]['unresolved'][0]['key'] == 'old'


@pytest.mark.parametrize('text,code', [('{broken', 'BAD_JSON'), ('[{"id":"old"},{"id":"old"}]', 'DUPLICATE_ID'),
                                       ('{"items":[]}', 'INVALID_CSL')], ids=['bad JSON', 'duplicate id', 'wrong JSON shape'])
def test_bad_exports_fail_explicitly(project, text, code):
    project.put('old.json', text)
    error(project.run('paper.md'), code)


def test_all_inputs_validated_before_writing_including_unsupported_frontmatter(project):
    project.put('input/bad.md', '---\nnocite: |\n  [@old]\n---\n[@old]')
    error(project.run('--out-dir', 'output', 'paper.md', 'bad.md'), 'UNSUPPORTED_FRONTMATTER')
    assert not (project.dir / 'output').exists()


def test_existing_output_file_directory_and_symlink_all_refused(project):
    (project.dir / 'directory').mkdir()
    project.put('file', 'keep')
    os.symlink('directory', project.dir / 'link')
    for destination in ['directory', 'file', 'link']:
        error(project.run('--out-dir', destination, 'paper.md'), 'OUTPUT_EXISTS')
    assert project.read('file') == b'keep'
    assert os.listdir(project.dir / 'directory') == []


def test_input_path_traversal_and_absolute_path_escape_refused(project):
    project.put('outside.md', '[@old]')
    error(project.run('../outside.md'), 'UNSAFE_PATH')
    error(project.run(str(project.dir / 'outside.md')), 'PATH_ESCAPE')
    error(project.run('sub/../paper.md'), 'UNSAFE_PATH')
    error(project.run('bad\\paper.md'), 'UNSAFE_PATH')


def test_symlinked_input_input_parent_and_output_parent_refused(project):
    os.symlink('paper.md', project.dir / 'input/link.md')
    os.symlink('input', project.dir / 'alias')
    error(project.run('link.md'), 'SYMLINK')
    error(project.run('--root', 'alias', 'paper.md'), 'USAGE')  # duplicate --root, no ambiguity
    error(project.run_raw('--old', 'old.json', '--new', 'new.json', '--root', 'alias', 'paper.md'), 'SYMLINK')
    error(project.run('--out-dir', 'alias/output', 'paper.md'), 'SYMLINK')


def test_duplicate_paths_unsupported_extensions_and_directories_refused(project):
    error(project.run('paper.md', './paper.md'), 'DUPLICATE_INPUT')
    project.put('input/other.txt', '[@old]')
    error(project.run('other.txt'), 'UNSUPPORTED_FORMAT')


def test_invalid_utf8_and_non_text_content_refused_without_replacement_decoding(project):
    project.put('input/paper.md', bytes([0xff, 0x40]))
    error(project.run('paper.md'), 'INVALID_UTF8')
    project.put('input/paper.md', '[@old]\0')
    error(project.run('paper.md'), 'UNSUPPORTED_TEXT')


def test_file_size_limit_is_explicit_and_sparse_fixture_stays_small(project):
    with open(project.dir / 'input/paper.md', 'wb') as handle:
        handle.truncate(32 * 1024 * 1024 + 1)
    error(project.run('paper.md'), 'FILE_TOO_LARGE')


def test_missing_input_output_parent_produces_an_error_not_a_partial_output(project):
    error(project.run('--out-dir', 'output', 'paper.md', 'missing.md'), 'ENOENT')
    assert not (project.dir / 'output').exists()
    error(project.run('--out-dir', 'missing/output', 'paper.md'), 'ENOENT')


def test_cli_usage_help_version_and_literal_filenames_after_double_dash(project):
    for args in [['--unknown'], ['--out-dir'], ['--dry-run', '--out-dir', 'output', 'paper.md']]:
        error(project.run(*args), 'USAGE')
    error(project.run_raw(), 'USAGE')
    help_text = project.run('--help').stdout
    assert 'Usage:' in help_text and 'notewrench citekeys' in help_text
    assert project.run('--version').stdout == '1.0.0\n'
    project.put('input/-note.md', '[@old]')
    assert project.run('--', '-note.md').status == 0


def test_python_api_is_importable_from_the_subpackage():
    """Replaces the ESM package-exports check: the API is exposed by ``notewrench.citekeys``."""
    result = build_mapping([{'id': 'a', 'DOI': '10.1/a'}], [{'id': 'b', 'DOI': '10.1/a'}])
    assert rekey_markdown('[@a]', result['mapping'])['text'] == '[@b]'


def test_directories_with_markdown_extension_are_refused_as_input(project):
    (project.dir / 'input/folder.md').mkdir()
    error(project.run('folder.md'), 'NOT_FILE')


@requires_git
def test_cli_mixed_containers_frontmatter_exact_bytes_ranges_and_applicable_diff(project):
    fixture = load_fixture('containers')
    text, expected = fixture['input'], fixture['expected']
    project.put('old.json', '[{"id":"A","DOI":"10.123/demo"}]')
    project.put('new.json', '[{"id":"Longer","DOI":"10.123/demo"}]')
    project.put('input/paper.md', text)
    before = snapshot(project.dir)
    preview = project.run('paper.md')
    assert preview.status == 0, preview.stderr
    assert snapshot(project.dir) == before
    result = project.run('--out-dir', 'output', 'paper.md')
    assert result.status == 0, result.stderr
    document = json.loads(result.stdout)['documents'][0]
    assert document == json.loads(preview.stdout)['documents'][0]
    assert len(document['edits']) == 7
    assert project.read('output/paper.md') == expected.encode()
    assert project.read('input/paper.md') == text.encode()
    for edit in document['edits']:
        assert text.encode()[edit['byteOffset']:edit['byteOffset'] + edit['byteLength']] == b'A'
    (project.dir / 'patch').mkdir()
    project.put('patch/paper.md', text)
    patched = git_apply(project.dir / 'patch', document['diff'], '--whitespace=nowarn', '-')
    assert patched.returncode == 0, patched.stderr
    assert project.read('patch/paper.md') == expected.encode()


def test_mixed_multi_file_batch_refuses_all_writes_for_unsafe_metadata_or_container_math_syntax(project):
    project.put('input/paper.md', '- Safe [@old].\n\n> Safe [@old].')
    for text, code in [('---\ncustom:\n  nested: "[@old]"\n---', 'UNSUPPORTED_FRONTMATTER'),
                       ('> $[@old]', 'UNSUPPORTED_MARKDOWN'), ('-     \t[@old]', 'UNSUPPORTED_MARKDOWN')]:
        project.put('input/bad.md', text)
        error(project.run('--out-dir', 'output', 'paper.md', 'bad.md'), code)
        assert not (project.dir / 'output').exists()
        assert project.read('input/paper.md') == b'- Safe [@old].\n\n> Safe [@old].'


NOCITE_CASES = [
    ('nocite', 'input', 'expected', 0, 4), ('nocite-review', 'reviewInput', 'reviewExpected', 1, 2),
    ('nocite-whitespace', 'whitespaceInput', 'whitespaceExpected', 0, 4),
    ('nocite-whitespace-review', 'whitespaceReviewInput', 'whitespaceReviewExpected', 1, 2),
    ('nocite-separator', 'separatorInput', 'separatorExpected', 0, 4),
    ('nocite-separator-review', 'separatorReviewInput', 'separatorReviewExpected', 1, 2),
    ('nocite-comment', 'commentInput', 'commentExpected', 0, 4),
    ('nocite-comment-review', 'commentReviewInput', 'commentReviewExpected', 1, 2),
    ('nocite-unicode-comment', 'unicodeCommentInput', 'unicodeCommentExpected', 0, 4),
    ('nocite-unicode-comment-review', 'unicodeCommentReviewInput', 'unicodeCommentReviewExpected', 1, 2),
    ('nocite-unicode-quoted', 'unicodeQuotedInput', 'unicodeQuotedExpected', 0, 4),
    ('nocite-unicode-quoted-review', 'unicodeQuotedReviewInput', 'unicodeQuotedReviewExpected', 1, 2),
    ('nocite-unicode-plain', 'unicodePlainInput', 'unicodePlainExpected', 0, 4),
    ('nocite-unicode-plain-review', 'unicodePlainReviewInput', 'unicodePlainReviewExpected', 1, 2),
    ('nocite-padded-bracket-single', 'bracketPaddingSingleInput', 'bracketPaddingSingleExpected', 0, 4),
    ('nocite-padded-bracket-double', 'bracketPaddingDoubleInput', 'bracketPaddingDoubleExpected', 0, 4),
    ('nocite-padded-bracket-single-review', 'bracketPaddingSingleReviewInput', 'bracketPaddingSingleReviewExpected', 1, 3),
    ('nocite-padded-bracket-double-review', 'bracketPaddingDoubleReviewInput', 'bracketPaddingDoubleReviewExpected', 1, 3),
    ('nocite-padded-wildcard', 'wildcardInput', 'wildcardExpected', 0, 2),
    ('nocite-padded-wildcard-double', 'wildcardDoubleInput', 'wildcardDoubleExpected', 0, 2),
    ('nocite-padded-wildcard-review', 'wildcardReviewInput', 'wildcardReviewExpected', 1, 2),
]


def _nocite_project(project):
    nocite = load_fixture('nocite')
    project.put('old.json', json.dumps(nocite['old']))
    project.put('new.json', json.dumps(nocite['new']))
    return nocite


@requires_git
def test_cli_quoted_nocite_comma_lists_preserve_bytes_dry_run_parity_unknown_locations_and_applicable_diffs(project):
    nocite = _nocite_project(project)
    for name, input_key, expected_key, status, count in NOCITE_CASES:
        text, expected = nocite[input_key], nocite[expected_key]
        project.put(f'input/{name}.md', text)
        before = snapshot(project.dir)
        preview = project.run(f'{name}.md')
        assert preview.status == status, preview.stderr
        assert snapshot(project.dir) == before
        result = project.run('--out-dir', f'{name}-output', f'{name}.md')
        assert result.status == status, result.stderr
        document = json.loads(result.stdout)['documents'][0]
        assert document == json.loads(preview.stdout)['documents'][0]
        assert len(document['edits']) == count
        assert document['unsupported'] == []
        assert [item['key'] for item in document['unresolved']] == (['unknown'] if status else [])
        if status:
            assert document['unresolved'][0] == unknown_location(text)
        for edit in document['edits']:
            assert_edit_location(text, edit)
        assert project.read(f'{name}-output/{name}.md') == expected.encode()
        assert project.read(f'input/{name}.md') == text.encode()
        (project.dir / f'{name}-patch').mkdir()
        project.put(f'{name}-patch/{name}.md', text)
        patch = git_apply(project.dir / f'{name}-patch', document['diff'], '--whitespace=nowarn', '-')
        assert patch.returncode == 0, patch.stderr
        assert project.read(f'{name}-patch/{name}.md') == expected.encode()


def _refuses_batch(project, fields, body='Body [@old].'):
    project.put('input/bad.md', f'---\n{fields}\n---\n{body}')
    before = snapshot(project.dir)
    result = project.run('--out-dir', 'refused-output', 'paper.md', 'bad.md')
    error(result, 'UNSUPPORTED_FRONTMATTER')
    assert result.stdout == ''
    assert snapshot(project.dir) == before
    assert not (project.dir / 'refused-output').exists()


def test_cli_quoted_nocite_wildcard_and_malformed_lists_retain_fail_closed_batch_preflight(project):
    wildcard = '---\nnocite: "@*" # @old\ncustom: "@old, @unknown"\n---\nBody [@old].'
    project.put('input/paper.md', wildcard)
    valid = project.run('--out-dir', 'wildcard-output', 'paper.md')
    assert valid.status == 0, valid.stderr
    document = json.loads(valid.stdout)['documents'][0]
    assert len(document['edits']) == 1
    assert document['unresolved'] == []
    assert document['unsupported'] == []
    assert project.read('wildcard-output/paper.md') == wildcard.replace('Body [@old]', 'Body [@New]', 1).encode()
    for fields in ["nocite: '@old, @*'", "nocite: '@*, @old'", "nocite: '@old,'", "nocite: '@old,, @unknown'",
                   "nocite: '@old @unknown'", "nocite: '@old, @unknown/part'", r'nocite: "@old, \u0040unknown"',
                   "nocite: '@old, @unknown'\ncustom:\n  child: value"]:
        _refuses_batch(project, fields)


def test_cli_quoted_scalar_suffix_whitespace_preserves_wildcard_opacity_and_fail_closed_batch_preflight(project):
    wildcard = '---\nnocite: "@*"\t# @unknown\ncustom: \'[@unknown]\' \t\n---\nBody [@old].'
    project.put('input/paper.md', wildcard)
    valid = project.run('--out-dir', 'wildcard-output', 'paper.md')
    assert valid.status == 0, valid.stderr
    document = json.loads(valid.stdout)['documents'][0]
    assert len(document['edits']) == 1
    assert document['unresolved'] == []
    assert document['unsupported'] == []
    assert project.read('wildcard-output/paper.md') == wildcard.replace('Body [@old]', 'Body [@New]', 1).encode()
    for fields in ["nocite: '@old'\ttrailing", 'nocite: "@old" \t, # comment',
                   "nocite: '@old'\t# opaque\ncustom: '[@unknown]'\ttrailing",
                   'nocite: "@old"\t# opaque\ncustom: "unterminated\t# comment',
                   "nocite: '@old'\t# opaque\ncustom: 'unterminated\t# comment",
                   'nocite: "@old"\t# opaque\ncustom: "\\q"\t# invalid escape',
                   "nocite: '@old, @*'\t# comment", "nocite: '@old,'\t# comment",
                   r'nocite: "@old, \@unknown"' + '\t# comment',
                   "nocite: '@old'\t# comment\ncustom:\n  child: value"]:
        _refuses_batch(project, fields)


def test_cli_field_colon_separators_preserve_wildcard_opacity_and_refuse_malformed_batches(project):
    nocite = _nocite_project(project)
    wildcard = '---\nnocite:\t"@*"\t# @unknown\ncustom: \t\'[@unknown]\' \t\n---\nBody [@A].'
    project.put('input/paper.md', wildcard)
    valid = project.run('--out-dir', 'wildcard-output', 'paper.md')
    assert valid.status == 0, valid.stderr
    document = json.loads(valid.stdout)['documents'][0]
    assert len(document['edits']) == 1
    assert document['unresolved'] == []
    assert document['unsupported'] == []
    assert project.read('wildcard-output/paper.md') == wildcard.replace('Body [@A]', 'Body [@B]', 1).encode()
    for fields in nocite['separatorInvalidFields']:
        _refuses_batch(project, fields, 'Body [@A].')


@pytest.mark.parametrize('paper_key,field_keys', [
    ('commentInput', ['commentInvalidFields']),
    ('unicodePlainInput', ['unicodeCommentInvalidFields', 'unicodeQuotedInvalidFields', 'unicodePlainInvalidFields']),
    ('wildcardInput', ['wildcardInvalidFields']),
    ('bracketPaddingSingleInput', ['bracketPaddingInvalidFields']),
], ids=['plain scalar comments', 'unicode metadata', 'padded wildcard', 'padded bracket'])
def test_cli_metadata_refusals_retain_fatal_batch_preflight_before_stdout_or_copies(project, paper_key, field_keys):
    nocite = _nocite_project(project)
    project.put('input/paper.md', nocite[paper_key])
    for key in field_keys:
        for fields in nocite[key]:
            _refuses_batch(project, fields, 'Body [@A].')


# --- real processes: `python -m notewrench.citekeys` -------------------------------

def test_module_entry_point_runs_the_bundled_example(tmp_path):
    from .conftest import EXAMPLES
    work = tmp_path.resolve() / 'example'
    work.mkdir()
    for name in ['old.json', 'new.json', 'paper.md', 'review.md']:
        (work / name).write_bytes((EXAMPLES / name).read_bytes())
    result = run_subprocess('--old', 'old.json', '--new', 'new.json', 'paper.md', cwd=work)
    assert result.status == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['tool'] == 'citekey-rekey' and report['mode'] == 'dry-run'
    assert len(report['documents'][0]['edits']) == 5
    review = run_subprocess('--old', 'old.json', '--new', 'new.json', 'review.md', cwd=work)
    assert review.status == 1, review.stderr
    usage = run_subprocess(cwd=work)
    assert usage.status == 2 and json.loads(usage.stderr)['error']['code'] == 'USAGE'
    written = run_subprocess('--old', 'old.json', '--new', 'new.json', '--out-dir', 'migrated', 'paper.md', cwd=work)
    assert written.status == 0, written.stderr
    assert json.loads(written.stdout)['written'] == [str(work / 'migrated' / 'paper.md')]
    assert b'See [@Doe20; -@Field-Notes]. Exact keys: [@Field-Notes].' in (work / 'migrated/paper.md').read_bytes()
