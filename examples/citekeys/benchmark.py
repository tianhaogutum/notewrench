#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
"""Labeled correctness benchmark: syntax-aware rekeying versus naive literal replacement.

Run from the repository root: ``python examples/citekeys/benchmark.py``.
The baseline replaces every literal old key, sequentially in export order; it
corrupts prose, code and e-mail addresses, mangles keys sharing a prefix and
re-replaces its own output in a key chain. Counts describe these synthetic labels
only; timings are a single in-memory pass, not a speed claim.
"""
from __future__ import annotations

import json
import platform
import sys
import time
from pathlib import Path

try:
    from notewrench.citekeys import build_mapping, rekey_markdown
except ImportError:  # running from a source checkout without installation
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
    from notewrench.citekeys import build_mapping, rekey_markdown


def dataset(count: int = 500) -> dict:
    old = [
        {'id': 'doe2020', 'DOI': '10.123/demo'},
        {'id': 'doe2020-extra', 'URL': 'https://example.org/notebook'},
        {'id': 'chainA', 'DOI': '10.5555/a'},
        {'id': 'chainB', 'DOI': '10.5555/b'},
    ]
    updated = [
        {'id': 'Doe20', 'DOI': 'https://doi.org/10.123/DEMO'},
        {'id': 'Field-Notes', 'URL': 'https://example.org/notebook'},
        {'id': 'chainB', 'DOI': '10.5555/a'},
        {'id': 'chainC', 'DOI': '10.5555/b'},
    ]
    plan = build_mapping(old, updated)
    assert plan['conflicts'] == []
    documents = []
    for doc in range(count):
        labels: list[dict] = []
        lines: list[str] = []

        def line(original: str, expected: str | None = None, kind: str = 'protected') -> None:
            labels.append({'line': len(lines), 'original': original,
                           'expected': original if expected is None else expected, 'kind': kind})
            lines.append(original)

        line(f'# Synthetic document {doc}')
        line('See [@doe2020].', 'See [@Doe20].', 'citation')
        line('Prefix [@doe2020-extra].', 'Prefix [@Field-Notes].', 'citation')
        line('Chain [@chainA].', 'Chain [@chainB].', 'citation')
        line('Chain [@chainB].', 'Chain [@chainC].', 'citation')
        line('reader@doe2020 is a synthetic email.')
        line('Ordinary prose doe2020 and chainA.')
        line('`[@doe2020]`')
        line('``code ` [@chainA]``')
        line('```markdown')
        line('[@doe2020-extra]')
        line('```')
        line('~~~text')
        line('[@chainA]')
        line('~~~')
        line('    [@doe2020]')
        line('Ambiguous citation [@doe2020, p. 4; prose].')
        line('Unknown [@unlisted].')
        documents.append({'text': '\n'.join(lines) + '\n', 'labels': labels})
    return {'documents': documents, 'mapping': plan['mapping']}


def literal_baseline(text: str, mapping: list[dict]) -> str:
    """For each old/new pair in export order, replace every literal old key anywhere."""
    for entry in mapping:
        text = text.replace(entry['old'], entry['new'])
    return text


def score(documents: list[dict], outputs: list[str]) -> dict:
    correct = missed = unintended = exact_documents = 0
    for document, output in zip(documents, outputs):
        lines = output.split('\n')
        exact = True
        for label in document['labels']:
            actual = lines[label['line']] if label['line'] < len(lines) else None
            if label['kind'] == 'citation':
                if actual == label['expected']:
                    correct += 1
                else:
                    missed += 1
            elif actual != label['original']:
                unintended += 1
            if actual != label['expected']:
                exact = False
        if exact:
            exact_documents += 1
    return {'correctCitationEdits': correct, 'missedOrIncorrectCitationEdits': missed,
            'unintendedChangedLines': unintended, 'exactDocuments': exact_documents}


def run_benchmark(count: int = 500) -> dict:
    data = dataset(count)
    documents, mapping = data['documents'], data['mapping']
    strategies = [
        ('literal-sequential', lambda text: literal_baseline(text, mapping)),
        ('syntax-aware', lambda text: rekey_markdown(text, mapping)['text']),
    ]
    # Warm both once; the timed part is the in-memory transformation (including
    # diagnostics for syntax-aware), excluding startup, disk I/O, mapping and scoring.
    for _, transform in strategies:
        transform(documents[0]['text'])
    results = []
    for name, transform in strategies:
        started = time.perf_counter()
        outputs = [transform(document['text']) for document in documents]
        elapsed_ms = (time.perf_counter() - started) * 1000
        results.append({'name': name, **score(documents, outputs), 'elapsedMs': round(elapsed_ms, 3)})
    assert results[1]['correctCitationEdits'] == count * 4
    assert results[1]['missedOrIncorrectCitationEdits'] == 0
    assert results[1]['unintendedChangedLines'] == 0
    assert results[1]['exactDocuments'] == count
    assert results[0]['unintendedChangedLines'] > 0
    return {
        'python': platform.python_version(), 'platform': sys.platform, 'arch': platform.machine(),
        'documents': count, 'utf8Bytes': sum(len(document['text'].encode('utf-8')) for document in documents),
        'labeledCitationEdits': count * 4, 'protectedLines': count * 14,
        'results': results,
        'limits': 'Deterministic synthetic English/ASCII documents, 18 labeled lines each; one citation slot per citation '
                  'line. Counts describe these labels only. Single timed pass after one warmup per strategy; no speed '
                  'promise, real-corpus coverage claim, disk-I/O or startup measurement.',
    }


# camelCase names of the original script.
literalBaseline = literal_baseline
runBenchmark = run_benchmark


if __name__ == '__main__':
    print(json.dumps(run_benchmark(), indent=2, ensure_ascii=False))
