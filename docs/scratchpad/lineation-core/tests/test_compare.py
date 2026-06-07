# research-pure: compare.score scores every panel reader vs the student on shared labeled lines.
"""Locks the head-to-head coverage + the OOF student metric — the byte-identical guard for
`compare.score`. Locked from a verified run on 2026-06."""
from __future__ import annotations

import pytest
from lineation_core import compare, panel_votes


def test_compare_locked(corpus):
    records, labelset = corpus
    cmp = compare.score(records, labelset, panel_votes.by_reader())
    assert cmp.n_labels_shared == 515
    grok = next(r for r in cmp.rows if r.reader == "grok")
    assert grok.student_metrics.balanced_acc == pytest.approx(0.963, abs=0.01)
