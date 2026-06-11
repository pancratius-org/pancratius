# research-pure: contested.evaluate scores the student vs the panel on the human-adjudicated lines.
"""Locks the contested head-to-head — the byte-identical guard for `contested.evaluate` (the
module __main__ prints the same numbers)."""
from __future__ import annotations

import pytest
from lineation_core.annotations import by_reader, load_labels
from lineation_core.evaluation import contested


def test_contested_truth_comes_from_the_one_store():
    """The contested slice is membership only; its truth is the `labels.jsonl` label for every
    member — by construction of the one join (`eval_slice`), so a second store cannot disagree."""
    human = {g.id: g.label for g in load_labels().labels}
    assert all(human[lid] == lab for lid, lab in contested.load_contested().items())


def test_contested_locked(corpus):
    records, labelset = corpus
    r = contested.evaluate(records, labelset, by_reader(),
                           contested.load_contested(), alpha=0.75)
    # 424 = the historical 425 minus one corrupt-id row (its line is unmapped in the corpus).
    # Scorable rose 342 → 382: the homed holdout labels (never trainable) are scored too now —
    # the old 0.975 was computed on a population that silently excluded the hardest lines.
    # 0.954 under the FIXED-render re-adjudicated truth: the recency-era 0.867 (prose_recall
    # 0.816) was the render-bug contamination — the flipped lines were re-judged prose on the
    # fixed render, and both the truth and the retrained student moved together.
    assert r.n_contested == 424
    assert r.n_with_student == 382
    assert r.student.balanced_acc == pytest.approx(0.954, abs=0.01)
    assert r.student.prose_recall == pytest.approx(0.989, abs=0.01)
    assert r.rows                                       # per-reader head-to-head rows present
