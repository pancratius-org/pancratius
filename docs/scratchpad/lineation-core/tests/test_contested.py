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
    # Scorable rose 382 → 424 (all of them now): the E1 gate added ru labels in books the ru-only
    # student didn't cover, so every contested line's book is now in the dataset and gets a
    # book-held-out OOF prediction. The contested lines are human/holdout truth — never gate-labeled
    # (route skips protected lines), so no training leak.
    # 0.915 for the bilingual gate-era student (was 0.954): the wider, representative training half
    # (gate + en) trades a little hard-line lineated-recall for coverage — the SAME shift as the
    # student-CV lock. prose_recall HOLDS at 0.989: the asymmetric direction the DoD binds on (the
    # importer's weak side) did not regress. Re-derive on truth growth; chase an unexplained drop.
    assert r.n_contested == 424
    assert r.n_with_student == 424
    assert r.student.balanced_acc == pytest.approx(0.915, abs=0.01)
    assert r.student.prose_recall == pytest.approx(0.989, abs=0.01)
    assert r.rows                                       # per-reader head-to-head rows present
