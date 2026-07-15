# research-pure: proves the student dataset/CV is leakage-free and book-grouped (real labels).
"""One build_dataset() + one train_cv() (module-scoped — the slow part) back every assertion."""
from __future__ import annotations

from typing import cast

import pytest
from intent_ai import identity, producer, student, truth
from intent_ai.annotations import LabelSet, LabelSource, LineLabel
from intent_ai.identity import BookKey, Label, LineId
from intent_ai.records import (
    Align,
    EndPunct,
    IndentVsBook,
    LineFeatures,
    LineRecord,
    Role,
    SpacingVsBook,
)


def _record(
    ordinal: int, *, book: str = "01", role: Role = Role.BODY, fill: float = 0.5,
    run_len: int = 1, run_pos: int = 0,
) -> LineRecord:
    text = f"line {ordinal}"
    features = LineFeatures(
        fill=fill, wraps=False, char_len=len(text), word_count=2,
        end_punct=EndPunct.NONE, starts_lower=True, next_line_lower=False,
        enjambs=False, colon_opens=False, align=Align.LEFT,
        indent_vs_book=IndentVsBook.DEFAULT,
        spacing_after_vs_book=SpacingVsBook.TYPICAL,
        align_is_book_default=True, sub=0, n_subs=1,
        run_len=run_len, run_pos=run_pos,
        fill_pctile_in_book=0.5,
    )
    return LineRecord(
        id=LineId.mapped("ru", book, ordinal, 0), text=text, role=role,
        features=features, line_text_hash=identity.text_hash(text),
    )


def _label(
    ordinal: int, text_hash: str, *, book: str = "01", label: Label = "prose",
    holdout: bool = False,
) -> LineLabel:
    return LineLabel(
        id=LineId.mapped("ru", book, ordinal, 0), label=label,
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


@pytest.mark.corpus_cache
def test_dataset_joins_every_trainable_label(ds, corpus):
    """The dataset is exactly the trainable truth — no silent shrinkage. A label whose line is
    missing from the records map must surface (a stale artifact or a broken join)."""
    _, labelset = corpus
    assert ds.n_joined == len(labelset.trainable)
    assert len(ds.X) == len(ds.y) == len(ds.groups) == len(ds.ids) == ds.n_joined


@pytest.mark.corpus_cache
def test_dataset_is_bilingual_and_groups_split_by_lang(ds):
    """The (lang, book) re-key: en labels JOIN (the bare-book_id join silently dropped them) and
    ru:NN / en:NN are DISTINCT CV groups — one shared folder number never folds two books."""
    assert {lid.lang for lid in ds.ids} == {"ru", "en"}
    assert set(ds.groups) == {lid.book_key for lid in ds.ids}
    both = ({g.book_id for g in ds.groups if g.lang == "ru"}
            & {g.book_id for g in ds.groups if g.lang == "en"})
    assert both, "expected at least one folder number labeled in both languages"


@pytest.mark.corpus_cache
def test_holdout_labels_are_never_training_rows(ds, corpus):
    _, labelset = corpus
    holdout = {g.id for g in labelset.labels if g.holdout}
    assert holdout and not holdout & set(ds.ids)


@pytest.mark.corpus_cache
def test_every_row_spans_the_fixed_columns_no_nan(ds):
    import math
    cols = set(ds.columns)
    for row in ds.X:
        assert set(row.keys()) == cols
        assert all(not math.isnan(v) and not math.isinf(v) for v in row.values())


@pytest.mark.corpus_cache
def test_labels_are_two_class(ds):
    assert set(ds.y) <= {"prose", "lineated"}


def test_no_feature_column_is_the_label():
    cols = producer.vector_columns()
    assert not any("label" in c or "gold" in c or "predict" in c for c in cols)


def test_constant_fold_model_is_a_total_batch_scorer():
    features = [_record(1).features, _record(2).features]
    assert student.ConstantModel("prose").posteriors(features) == [0.0, 0.0]
    assert student.ConstantModel("lineated").posteriors(features) == [1.0, 1.0]
    assert student.ConstantModel("lineated").posteriors([]) == []
    with pytest.raises(ValueError, match=r"prose\|lineated"):
        student.ConstantModel(cast(Label, "other"))


def test_linear_model_transforms_and_predicts_once_per_batch():
    import numpy as np

    class SpyScaler:
        mean_ = scale_ = ()
        calls = 0

        def transform(self, matrix):
            self.calls += 1
            return matrix

    class SpyClassifier:
        coef_ = intercept_ = ()
        calls = 0

        def predict_proba(self, matrix):
            self.calls += 1
            return np.array([[0.25, 0.75]] * len(matrix))

    scaler, classifier = SpyScaler(), SpyClassifier()
    columns = tuple(producer.vector_columns())
    model = student.LinearModel(scaler, classifier, columns)
    features = [_record(1).features, _record(2).features, _record(3).features]

    feature_scores = model.posteriors(features)
    assert feature_scores == [0.75, 0.75, 0.75]
    assert scaler.calls == classifier.calls == 1
    matrix = np.array([
        [producer.vectorize_fixed(feature)[column] for column in columns]
        for feature in features
    ])
    assert model._posteriors_from_matrix(matrix) == feature_scores
    assert scaler.calls == classifier.calls == 2


def test_alpha_grid_cross_fits_and_scores_each_document_once(monkeypatch):
    records = {}
    labels: list[LineLabel] = []
    for book in ("01", "02", "03"):
        recs = [
            _record(1, book=book, fill=0.1, run_len=3, run_pos=0),
            _record(2, book=book, fill=0.9, run_len=3, run_pos=1),
            _record(3, book=book, fill=0.2, run_len=3, run_pos=2),  # unlabeled run neighbor
            _record(4, book=book, role=Role.HEADING, fill=0.7),
        ]
        records[BookKey("ru", book)] = recs
        labels.extend([
            _label(1, recs[0].line_text_hash, book=book, label="prose"),
            _label(2, recs[1].line_text_hash, book=book, label="lineated"),
        ])
    labelset = LabelSet(tuple(labels))
    dataset = student.build_dataset(records, labelset)
    fit_calls = 0
    score_calls: list[tuple[LineFeatures, ...]] = []

    class SpyScorer:
        def posteriors(self, features):
            score_calls.append(tuple(features))
            return [feature.fill for feature in features]

    def fake_fit(*args, **kwargs):
        nonlocal fit_calls
        fit_calls += 1
        return SpyScorer()

    monkeypatch.setattr(student, "_fit", fake_fit)
    assert student.tune_alpha(dataset, labelset, records, grid=()) == []
    assert fit_calls == 0
    grid = (0.0, 0.5, 1.0)
    result = student.tune_alpha(dataset, labelset, records, grid=grid)

    books = sorted(records)
    assert fit_calls == len(books)
    assert score_calls == [
        tuple(record.features for record in records[book] if record.votable)
        for book in books
    ]
    assert [row.alpha for row in result] == list(grid)


@pytest.mark.corpus_cache
def test_cv_is_book_grouped_no_leakage(ds, res):
    assert set(res.oof_pred.keys()) == set(ds.ids)
    assert set(res.oof_pred.values()) <= {"prose", "lineated"}


@pytest.mark.corpus_cache
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


@pytest.mark.corpus_cache
def test_zero_support_columns_reported_not_dropped(res, ds):
    assert "align=center" in res.zero_support_columns
    for c in res.zero_support_columns:
        assert c in ds.columns


@pytest.mark.corpus_cache
def test_model_explains_itself_with_signed_weights(ds):
    """The interpretability readout is the fitted model's own (`LinearModel.explain`), not the CV
    harness's. The top features carry the domain-sane sign: wraps→prose (negative toward lineated),
    starts_lower→lineated (positive), fill→prose. If these flip, the model learned something
    suspicious."""
    w = dict(student.fit_full(ds).explain())
    assert w["wraps"] < 0
    assert w["starts_lower"] > 0
    assert w["fill"] < 0


@pytest.mark.corpus_cache
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
