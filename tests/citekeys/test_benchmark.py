# SPDX-License-Identifier: AGPL-3.0-only
"""Port of test/benchmark.test.js; the benchmark itself lives in examples/citekeys/benchmark.py."""
from __future__ import annotations

import importlib.util
import re

import pytest

from .conftest import EXAMPLES


@pytest.fixture(scope='module')
def benchmark():
    spec = importlib.util.spec_from_file_location('citekeys_benchmark', EXAMPLES / 'benchmark.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_labeled_correctness_benchmark_reproduces_baseline_corruption_and_syntax_aware_accuracy(benchmark):
    result = benchmark.run_benchmark(3)
    assert [{k: v for k, v in counts.items() if k != 'elapsedMs'} for counts in result['results']] == [
        {'name': 'literal-sequential', 'correctCitationEdits': 6, 'missedOrIncorrectCitationEdits': 6,
         'unintendedChangedLines': 24, 'exactDocuments': 0},
        {'name': 'syntax-aware', 'correctCitationEdits': 12, 'missedOrIncorrectCitationEdits': 0,
         'unintendedChangedLines': 0, 'exactDocuments': 3},
    ]
    assert result['protectedLines'] == 42


def test_benchmark_scoring_detects_both_missed_edits_and_protected_data_corruption(benchmark):
    data = benchmark.dataset(1)
    documents, mapping = data['documents'], data['mapping']
    assert benchmark.score(documents, [documents[0]['text']])['missedOrIncorrectCitationEdits'] == 4
    assert re.search('reader@Doe20', benchmark.literal_baseline(documents[0]['text'], mapping))
    assert re.search(r'Chain \[@chainC\]', benchmark.literal_baseline(documents[0]['text'], mapping))
