# research-pure: compare.score scores every panel reader vs the student on shared labeled lines.
"""Locks the head-to-head coverage + the OOF student metric — the byte-identical guard for
`compare.score`."""
from __future__ import annotations

import pytest
from lineation_core.annotations import by_reader
from lineation_core.evaluation import compare


def test_compare_locked(corpus):
    records, labelset = corpus
    cmp = compare.score(records, labelset, by_reader())
    # 529 = the historical 515 + 14 voted lines whose human labels were homed from the contested
    # eval set into labels.jsonl (holdout) — a deliberate re-lock of the shared population.
    assert cmp.n_labels_shared == 529
    grok = next(r for r in cmp.rows if r.reader == "grok")
    # 0.979 under the FIXED-render re-adjudicated truth — the recency-era 0.811 was the
    # render-bug contamination dragging prose recall down (see test_contested, same effect).
    assert grok.student_metrics.balanced_acc == pytest.approx(0.979, abs=0.01)
