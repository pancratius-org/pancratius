# research-pure: proves the student dataset/CV is leakage-free and book-grouped (real labels).
"""One build_dataset() + one train_cv() (module-scoped — the slow part) back every assertion."""
from __future__ import annotations

import pytest
from intent_ai import identity, producer, student, truth
from intent_ai.annotations import LabelSet, LabelSource, LineLabel
from intent_ai.identity import BookKey, LineId
from intent_ai.records import (
    Align,
    EndPunct,
    IndentVsBook,
    LineFeatures,
    LineRecord,
    Role,
    SpacingVsBook,
)


def _record(ordinal: int, *, role: Role = Role.BODY) -> LineRecord:
    text = f"line {ordinal}"
    features = LineFeatures(
        fill=0.5, wraps=False, char_len=len(text), word_count=2,
        end_punct=EndPunct.NONE, starts_lower=True, next_line_lower=False,
        enjambs=False, colon_opens=False, align=Align.LEFT,
        indent_vs_book=IndentVsBook.DEFAULT,
        spacing_after_vs_book=SpacingVsBook.TYPICAL,
        align_is_book_default=True, sub=0, n_subs=1,
        run_len=1, run_pos=0,
        fill_pctile_in_book=0.5,
    )
    return LineRecord(
        id=LineId.mapped("ru", "01", ordinal, 0), text=text, role=role,
        features=features, line_text_hash=identity.text_hash(text),
    )


def _label(ordinal: int, text_hash: str, *, holdout: bool = False) -> LineLabel:
    return LineLabel(
        id=LineId.mapped("ru", "01", ordinal, 0), label="prose",
        source=LabelSource.HUMAN, confidence=None, audit_status="", notes="",
        provenance={}, line_text_hash=text_hash, holdout=holdout,
    )


def test_truth_join_reports_every_broken_reference():
    records = [_record(1), _record(2, role=Role.HEADING), _record(3), _record(4)]
    labels = LabelSet(labels=[
        _label(1, identity.text_hash("line 1")),
        _label(2, identity.text_hash("line 2")),
        _label(3, identity.text_hash("line 3")),
        _label(4, "old-hash"),
        _label(5, identity.text_hash("line 5"), holdout=True),
    ])
    with pytest.raises(truth.TruthJoinError) as caught:
        student.build_dataset({BookKey("ru", "01"): records}, labels)
    assert {issue.fault for issue in caught.value.issues} == {
        truth.TruthJoinFault.NON_VOTABLE_RECORD,
        truth.TruthJoinFault.TEXT_HASH_DRIFT,
        truth.TruthJoinFault.MISSING_RECORD,
    }


@pytest.fixture(scope="module")
def ds(corpus):
    records, labelset = corpus
    return student.build_dataset(records, labelset)


@pytest.fixture(scope="module")
def res(ds):
    return student.train_cv(ds)


def test_dataset_joins_every_trainable_label(ds, corpus):
    """The dataset is exactly the trainable truth — no silent shrinkage. A label whose line is
    missing from the records map must surface (a stale artifact or a broken join)."""
    _, labelset = corpus
    assert ds.n_joined == len(labelset.trainable)
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


def test_cv_is_book_grouped_no_leakage(ds, res):
    assert set(res.oof_pred.keys()) == set(ds.ids)
    assert set(res.oof_pred.values()) <= {"prose", "lineated"}


def test_locked_cv_number(res, corpus):
    """Source-v3 truth under (lang, book)-grouped leave-one-book-out CV."""
    _, labelset = corpus
    assert res.n == len(labelset.trainable)
    assert res.n_books == len({g.id.book_key for g in labelset.trainable})
    assert res.balanced_accuracy == pytest.approx(0.919, abs=0.01)
    assert res.macro_f1 == pytest.approx(0.902, abs=0.01)
    assert res.prose_f1 == pytest.approx(0.828, abs=0.02)
    assert res.balanced_accuracy > 0.5
    assert res.balanced_accuracy > res.majority_baseline_acc


def test_zero_support_columns_reported_not_dropped(res, ds):
    assert "align=center" in res.zero_support_columns
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
    prose_groups = {group for group, label in zip(ds.groups, ds.y, strict=True) if label == "prose"}
    lineated_groups = {
        group for group, label in zip(ds.groups, ds.y, strict=True) if label == "lineated"
    }
    groups = set(sorted(prose_groups)[:3]) | set(sorted(lineated_groups)[:3])
    keep = [index for index, group in enumerate(ds.groups) if group in groups]
    small = student.Dataset(
        X=[ds.X[index] for index in keep], y=[ds.y[index] for index in keep],
        groups=[ds.groups[index] for index in keep], columns=ds.columns,
        feature_support=ds.feature_support, ids=[ds.ids[index] for index in keep],
        n_joined=len(keep),
    )
    a = student.train_cv(small, seed=0)
    b = student.train_cv(small, seed=0)
    assert a.balanced_accuracy == b.balanced_accuracy
    assert a.confusion == b.confusion
