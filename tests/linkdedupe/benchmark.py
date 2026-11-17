# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 tianhaogutum
"""Port of linkdedupe scripts/corpus.mjs and scripts/benchmark.mjs (used by test_report)."""

from __future__ import annotations

import time

from notewrench.linkdedupe import normalize_url


def make_corpus(families: int = 500) -> list[dict[str, str]]:
    """Original deterministic labeled URLs, not downloaded bookmarks.

    A label denotes intended equivalence under this tool's default policy, not fetched content.
    """
    corpus = []
    for i in range(families):
        base = f'https://example.org/article/{i}'

        def add(url: str, label: str) -> None:
            corpus.append({'url': url, 'label': f'{i}:{label}'})

        add(base, 'article')
        add(base, 'article')  # exact-URL baseline has a true positive too
        add(f'https://EXAMPLE.org:443/article/{i}?utm_source=synthetic&utm_medium=email', 'article')
        add(f'{base}?fbclid=example', 'article')
        add(f'http://example.org/article/{i}', 'http')
        add(f'{base}/', 'slash')
        add(f'{base}#one', 'fragment-one')
        add(f'{base}#two', 'fragment-two')
        add(f'{base}?a=1&b=2', 'order-ab')
        add(f'{base}?b=2&a=1', 'order-ba')
        add(f'{base}?a=1&a=2', 'repeat-12')
        add(f'{base}?a=2&a=1', 'repeat-21')
        add(f'{base}?q=a+b', 'plus')
        add(f'{base}?q=a%20b', 'encoded-space')
        add(f'{base}?q=%2F', 'encoded-slash')
        add(f'{base}?q=/', 'literal-slash')
        add(f'{base}?sig=x&utm_source=one', 'signed-one')
        add(f'{base}?sig=x&utm_source=two', 'signed-two')
        add(f'{base}?X-Amz-Signature=x&utm_source=one', 'aws-one')
        add(f'{base}?X-Amz-Signature=x&utm_source=two', 'aws-two')
        add(f'{base}?ref=one', 'meaningful-ref-one')
        add(f'{base}?ref=two', 'meaningful-ref-two')
        add(f'https://bücher.example/{i}', 'idna')
        add(f'https://xn--bcher-kva.example/{i}', 'idna')
    return corpus


def _pairs(n: int) -> int:
    return n * (n - 1) // 2


def evaluate(corpus, key):
    start = time.perf_counter()
    groups: dict[str, dict[str, int]] = {}
    labels: dict[str, int] = {}
    for row in corpus:
        labels[row['label']] = labels.get(row['label'], 0) + 1
        group = groups.setdefault(key(row['url']), {})
        group[row['label']] = group.get(row['label'], 0) + 1
    predicted = true_positives = 0
    for group in groups.values():
        predicted += _pairs(sum(group.values()))
        true_positives += sum(_pairs(count) for count in group.values())
    positives = sum(_pairs(count) for count in labels.values())
    return {'truePositives': true_positives, 'falseMerges': predicted - true_positives,
            'falseNegatives': positives - true_positives,
            'precision': true_positives / predicted if predicted else 1,
            'recall': true_positives / positives if positives else 1,
            'elapsedMs': round((time.perf_counter() - start) * 1000, 3)}


def run_benchmark():
    corpus = make_corpus()
    baseline = evaluate(corpus, lambda url: url)
    linkdedupe = evaluate(corpus, lambda url: normalize_url(url)['url'])
    assert linkdedupe['falseMerges'] == 0, 'Unexpected false merge on labeled corpus'
    assert linkdedupe['recall'] > baseline['recall'], 'Default rules must improve intended positive recall'
    assert linkdedupe['recall'] == 1
    return {'corpus': 'synthetic-v1', 'families': 500, 'records': len(corpus), 'baseline': baseline, 'linkdedupe': linkdedupe}
