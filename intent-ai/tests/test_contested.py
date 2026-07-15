# research-pure: contested.evaluate scores the student vs the panel on the human-adjudicated lines.
"""Locks the contested head-to-head — the byte-identical guard for `contested.evaluate` (the
module __main__ prints the same numbers)."""
from __future__ import annotations

import pytest
from intent_ai.annotations import by_reader, load_labels
from intent_ai.evaluation import contested


def test_contested_truth_comes_from_the_one_store():
    """The contested slice is membership only; its truth is the `labels.jsonl` label for every
    member — by construction of the one join (`eval_slice`), so a second store cannot disagree."""
    human = {g.id: g.label for g in load_labels().labels}
    assert all(human[lid] == lab for lid, lab in contested.load_contested().items())


def test_contested_locked(corpus, student_predictions):
    _, labelset = corpus
    r = contested.evaluate_predictions(
        labelset, by_reader(), contested.load_contested(), student_predictions
    )
    # Source-v3 has 97 prose and 327 lineated items. The book-held-out model gets 93 and 272;
    # pin the class counts so a metric change cannot hide denominator drift.
    assert r.n_contested == 424
    assert r.n_with_student == 424
    assert r.label_dist == {"prose": 97, "lineated": 327}
    assert r.student.prose_recall == pytest.approx(93 / 97)
    assert r.student.lineated_recall == pytest.approx(272 / 327)
    assert r.student.balanced_acc == pytest.approx((93 / 97 + 272 / 327) / 2)
    assert r.rows                                       # per-reader head-to-head rows present
