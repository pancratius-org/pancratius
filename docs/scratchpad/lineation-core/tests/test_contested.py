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
    # 0.867 under the recency-resolved truth (latest-mtime human pass wins). The same truth scored
    # against the stale-trained student gives 0.941 — retraining on the corrected labels COSTS
    # contested prose recall (the flipped lines are φ-prose-shaped but human-lineated).
    assert r.n_contested == 424
    assert r.n_with_student == 382
    assert r.student.balanced_acc == pytest.approx(0.867, abs=0.01)
    assert r.student.prose_recall == pytest.approx(0.816, abs=0.01)
    assert r.rows                                       # per-reader head-to-head rows present
