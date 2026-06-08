# research-pure: contested.evaluate scores the student vs the panel on the human-adjudicated lines.
"""Locks the contested head-to-head — the byte-identical guard for `contested.evaluate` (the
module __main__ prints the same numbers)."""
from __future__ import annotations

import pytest
from lineation_core.annotations import by_reader
from lineation_core.evaluation import contested


def test_contested_locked(corpus):
    records, labelset = corpus
    r = contested.evaluate(records, labelset, by_reader(),
                           contested.load_contested(), alpha=0.75)
    assert r.n_contested == 425
    assert r.n_with_student == 342
    assert r.student.balanced_acc == pytest.approx(0.966, abs=0.01)
    assert r.student.prose_recall == pytest.approx(1.0, abs=0.01)
    assert r.rows                                       # per-reader head-to-head rows present
