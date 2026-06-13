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
    # 2019 shared (reader ⋂ truth): the E1 panel added ~1,490 voted lines whose truth (gate or
    # human) is now committed. NOT the policy-replay population (that one is panel-INDEPENDENT
    # truth only) — `compare.score` measures the student against EVERY reader on every line with
    # both a vote and a label, so the gate-labeled lines belong here.
    assert cmp.n_labels_shared == 2019
    grok = next(r for r in cmp.rows if r.reader == "grok")
    # 0.893 for the bilingual gate-era student (was 0.979 ru-human-only): the student now trains on
    # the representative E1 working half (gate + adjudications, en + ru), so its OOF score on the
    # full shared population is honestly lower than on the curated human cohort — the same shift as
    # the student-CV lock. Re-derive on truth growth; investigate an UNEXPLAINED drop.
    assert grok.student_metrics.balanced_acc == pytest.approx(0.893, abs=0.01)
