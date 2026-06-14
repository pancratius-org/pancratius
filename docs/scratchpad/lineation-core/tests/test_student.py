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


def test_dataset_joins_every_trainable_label(ds, corpus):
    """The dataset is exactly the trainable truth — no silent shrinkage. A label whose line is
    missing from the records map must surface (a stale artifact or a broken join), and unmapped
    rejections stay visible end-to-end."""
    _, labelset = corpus
    assert ds.n_joined == len(labelset.trainable)
    assert ds.n_skipped_unmapped == labelset.n_rejected_unmapped == 2
    assert len(ds.X) == len(ds.y) == len(ds.groups) == len(ds.ids) == ds.n_joined


def test_dataset_is_bilingual_and_groups_split_by_lang(ds):
    """The (lang, book) re-key: en labels JOIN (the bare-book_id join silently dropped them) and
    ru:NN / en:NN are DISTINCT CV groups — one shared folder number never folds two books."""
    assert {lid.lang for lid in ds.ids} == {"ru", "en"}
    assert set(ds.groups) == {lid.book_key for lid in ds.ids}
    both = ({g.book_id for g in ds.groups if g.lang == "ru"}
            & {g.book_id for g in ds.groups if g.lang == "en"})
    assert both, "expected at least one folder number labeled in both languages"


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


def test_locked_cv_number(res, corpus):
    """The bilingual gate-era cohort (migration 620 + E1 working-half gate 726 + live
    adjudications): balanced 0.919 / macro-F1 0.902 / prose-F1 0.828 under (lang, book)-grouped
    LOO CV. Slightly below the human-cohort-only 0.929 — the E1 random instrument adds
    representative (not curated) lines, including the gate-routed hard tail; the dip is the
    honest price of an unbiased substrate, not a regression. Re-derive (run
    `python -m lineation_core.student`) when truth grows; investigate any UNEXPLAINED drop."""
    _, labelset = corpus
    assert res.n == len(labelset.trainable)
    assert res.n_books == len({g.id.book_key for g in labelset.trainable})
    assert res.balanced_accuracy == pytest.approx(0.919, abs=0.01)
    assert res.macro_f1 == pytest.approx(0.902, abs=0.01)
    assert res.prose_f1 == pytest.approx(0.828, abs=0.02)
    assert res.balanced_accuracy > 0.5
    assert res.balanced_accuracy > res.majority_baseline_acc


def test_zero_support_columns_reported_not_dropped(res, ds):
    assert "numbered" in res.zero_support_columns or "align=center" in res.zero_support_columns
    for c in res.zero_support_columns:
        assert c in ds.columns


def test_model_explains_itself_with_signed_weights(ds):
    """The interpretability readout is the fitted model's own (`FittedModel.explain`), not the CV
    harness's. The top features carry the domain-sane sign: wraps→prose (negative toward lineated),
    starts_lower→lineated (positive), fill→prose. If these flip, the model learned something
    suspicious."""
    w = dict(student.fit_full(ds).explain())
    assert w["wraps"] < 0
    assert w["starts_lower"] > 0
    assert w["fill"] < 0


def test_reproducible(ds):
    a = student.train_cv(ds, seed=0)
    b = student.train_cv(ds, seed=0)
    assert a.balanced_accuracy == b.balanced_accuracy
    assert a.confusion == b.confusion
