# research-pure: compare.score scores every panel reader vs the student on shared labeled lines.
"""Locks the head-to-head coverage + the OOF student metric — the byte-identical guard for
`compare.score`."""
from __future__ import annotations

import pytest
from intent_ai.annotations import by_reader
from intent_ai.evaluation import compare

pytestmark = pytest.mark.corpus_cache


def test_compare_locked(corpus, student_predictions):
    _, labelset = corpus
    cmp = compare.score_predictions(labelset, by_reader(), student_predictions)
    # Unresolved source-v2 identities live in history, so they cannot silently enter this join.
    assert cmp.n_labels_shared == 1983
    grok = next(r for r in cmp.rows if r.reader == "grok")
    # Source-v3 predictions cover every labeled book, including holdout-only groups.
    assert grok.student_metrics.balanced_acc == pytest.approx(0.913, abs=0.01)
