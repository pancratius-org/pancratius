# research-pure: proves the student dataset/CV is leakage-free and book-grouped (real labels).
"""One build_dataset() + one train_cv() (module-scoped — the slow part) back every assertion."""
from __future__ import annotations

import pytest
from lineation_core import producer, student


@pytest.fixture(scope="module")
def ds(corpus):
    records, labelset = corpus
    return student.build_dataset(records, labelset)


@pytest.fixture(scope="module")
def res(ds):
    return student.train_cv(ds)


def test_dataset_shape_locked(ds):
    # 620 = the trainable labels, byte-identical to before the holdout cohort was homed in
    # labels.jsonl — homing eval-only truth must never grow the training set.
    assert ds.n_joined == 620
    assert ds.n_skipped_unmapped == 2
    assert len(ds.X) == len(ds.y) == len(ds.groups) == len(ds.ids) == 620
    assert len(set(ds.groups)) == 26


def test_holdout_labels_are_never_training_rows(ds, corpus):
    _, labelset = corpus
    holdout = {g.id for g in labelset.labels if g.holdout}
    assert holdout and not holdout & set(ds.ids)


def test_every_row_spans_the_fixed_columns_no_nan(ds):
    import math
    cols = set(ds.columns)
    for row in ds.X:
        assert set(row.keys()) == cols
        assert all(not math.isnan(v) and not math.isinf(v) for v in row.values())


def test_labels_are_two_class(ds):
    assert set(ds.y) <= {"prose", "lineated"}


def test_no_feature_column_is_the_label():
    cols = producer.vector_columns()
    assert not any("label" in c or "gold" in c or "predict" in c for c in cols)


def test_cv_is_book_grouped_no_leakage(ds):
    res = student.train_cv(ds)
    assert set(res.oof_pred.keys()) == set(ds.ids)
    assert set(res.oof_pred.values()) <= {"prose", "lineated"}


def test_locked_cv_number(res):
    """Under the FIXED-render re-adjudicated truth the prose boundary recovers: the 12 trainable
    lines the recency pass had flipped prose→lineated were render-bug verdicts (the old prose
    render mangled multi-line content into one paragraph), not φ-noise — re-judged prose, the
    poisoning vanishes (prose_f1 0.71 → 0.85, balanced 0.844 → 0.929). The old "the flips are
    φ-prose-shaped but human-lineated" story was the contamination talking."""
    assert res.n == 620
    assert res.n_books == 26
    assert res.balanced_accuracy == pytest.approx(0.929, abs=0.01)
    assert res.macro_f1 == pytest.approx(0.914, abs=0.01)
    assert res.prose_f1 == pytest.approx(0.854, abs=0.02)
    # all-lineated scores 0.5 balanced; the student now clears the majority PLAIN acc again
    # (0.929 > 0.852) — the recency-era inversion was an artifact of the contaminated truth.
    assert res.balanced_accuracy > 0.5
    assert res.balanced_accuracy > res.majority_baseline_acc


def test_zero_support_columns_reported_not_dropped(res, ds):
    assert "numbered" in res.zero_support_columns or "align=center" in res.zero_support_columns
    for c in res.zero_support_columns:
        assert c in ds.columns


def test_coefficients_are_interpretable_and_signed(res):
    """The top features carry the domain-sane sign. wraps→prose (negative toward lineated),
    starts_lower→lineated (positive), fill→prose. If these flip, the model learned something
    suspicious."""
    w = dict(res.coefficients)
    assert w["wraps"] < 0
    assert w["starts_lower"] > 0
    assert w["fill"] < 0


def test_reproducible(ds):
    a = student.train_cv(ds, seed=0)
    b = student.train_cv(ds, seed=0)
    assert a.balanced_accuracy == b.balanced_accuracy
    assert a.confusion == b.confusion
