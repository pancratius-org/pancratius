# research-pure: builds the labeled feature matrix and trains an interpretable per-line student.
"""The real task: an interpretable student on `prose`/`lineated`.

Joins the per-line labels to the producer's records by LineId, vectorizes the features via the
SAME `vectorize_fixed` the teacher listing's tokens come from (one feature contract), and trains
an INTERPRETABLE student (logistic regression — a coefficient per feature) with BOOK-GROUPED
CV so no book leaks across the train/test split. Reports a real number on the real task and
the feature_support (zero-support columns stay visible).

The prediction API is sequence-shaped (`predict_document`), though this first student decides
per line; the shape allows run-level smoothing later.

Confidence vs disagreement (a real distinction): the model emits a per-line posterior
`P(lineated) ∈ [0,1]`; its CONFIDENCE on a line is the margin `|posterior − 0.5|` (a line at
0.97 is confident, one at 0.52 is uncertain). That is NOT the same as label DISAGREEMENT — a
line where readers/humans gave conflicting labels (the `contested` set). The human labels carry
`confidence=None` because a human did not emit a probability; we never pretend otherwise.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, assert_never

from . import producer, sequence, store
from .annotations import LabelSet, load_labels
from .identity import BookKey, Label, LabelByLine, LineId
from .records import FeatureName, FeatureVector, LineFeatures, RecordsByBook
from .truth import join_truth


@dataclass
class Dataset:
    X: list[FeatureVector]
    y: list[Label]
    groups: list[BookKey]
    columns: list[FeatureName]
    feature_support: dict[FeatureName, int]
    ids: list[LineId]
    n_joined: int




def build_dataset(records: RecordsByBook, labelset: LabelSet) -> Dataset:
    """Training rows from the TRAINABLE labels only — a `holdout` (eval-only) label is scoring
    truth elsewhere but never a training target here. FAILS LOUD on a labeled book missing from
    `records` — a silently skipped book would shrink the dataset without a trace."""
    X: list[FeatureVector] = []
    y: list[Label] = []
    groups: list[BookKey] = []
    ids: list[LineId] = []
    n_joined = 0
    support: Counter[FeatureName] = Counter()
    cols = list(producer.vector_columns())
    for binding in join_truth(records, labelset).training:
        rec, truth = binding.record, binding.truth
        vec = producer.vectorize_fixed(rec.features)
        X.append(vec)
        y.append(truth.label)
        groups.append(truth.id.book_key)
        ids.append(truth.id)
        n_joined += 1
        for column, value in vec.items():
            if value != 0.0:
                support[column] += 1

    feature_support = {c: support.get(c, 0) for c in cols}  # zero-support cols kept at 0
    return Dataset(
        X=X, y=y, groups=groups, columns=cols, feature_support=feature_support, ids=ids,
        n_joined=n_joined,
    )


def _matrix(ds: Dataset):
    import numpy as np
    M = np.zeros((len(ds.X), len(ds.columns)), dtype=float)
    for i, row in enumerate(ds.X):
        for j, c in enumerate(ds.columns):
            M[i, j] = row[c]
    yv = np.array([1 if lab == "lineated" else 0 for lab in ds.y])
    return M, yv


class _Scaler(Protocol):
    mean_: Any
    scale_: Any

    def transform(self, X: Any) -> Any: ...


class _Classifier(Protocol):
    coef_: Any
    intercept_: Any

    def predict_proba(self, X: Any) -> Any: ...


@dataclass(frozen=True, slots=True)
class LinearModel:
    """The fitted, interpretable student: one batched posterior surface over fixed features."""

    scaler: _Scaler
    clf: _Classifier
    columns: tuple[FeatureName, ...]

    def _posteriors_from_matrix(self, matrix: Any) -> list[float]:
        return self.clf.predict_proba(self.scaler.transform(matrix))[:, 1].tolist()

    def posteriors(self, feats: Sequence[LineFeatures]) -> list[float]:
        if not feats:
            return []
        import numpy as np
        X = np.array([[producer.vectorize_fixed(f)[c] for c in self.columns] for f in feats],
                     dtype=float)
        return self._posteriors_from_matrix(X)

    def explain(self) -> list[tuple[FeatureName, float]]:
        """This model's signed per-feature weights, largest |weight| first — the INTERPRETABLE
        readout. Model-specific (a logistic regression's coefficients), so it lives on the model,
        not in the CV harness: a different student carries its own `explain`."""
        # sort by the UNROUNDED weight (so rounding-tied features keep their true order), round
        # only on emit.
        ordered = sorted(zip(self.columns, self.clf.coef_[0], strict=True),
                         key=lambda cw: abs(cw[1]), reverse=True)
        return [(c, round(float(w), 3)) for c, w in ordered]


@dataclass(frozen=True, slots=True)
class ConstantModel:
    """A valid degenerate fold model whose training truth contained only one class."""

    label: Label

    def __post_init__(self) -> None:
        if self.label not in ("prose", "lineated"):
            raise ValueError(f"constant model needs a prose|lineated label, got {self.label!r}")

    def _posteriors_from_matrix(self, matrix: Any) -> list[float]:
        value = 1.0 if self.label == "lineated" else 0.0
        return [value] * len(matrix)

    def posteriors(self, feats: Sequence[LineFeatures]) -> list[float]:
        return self._posteriors_from_matrix(feats)


def _fit(M, y, *, seed: int, columns: list[FeatureName]) -> LinearModel | ConstantModel:
    if not len(y):
        raise ValueError("cannot fit a student without training truth")
    if len(set(y.tolist())) < 2:
        return ConstantModel("lineated" if int(y[0]) == 1 else "prose")
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler().fit(M)
    clf = LogisticRegression(max_iter=2000, class_weight="balanced", random_state=seed, C=1.0)
    clf.fit(scaler.transform(M), y)
    return LinearModel(scaler, clf, tuple(columns))


def fit_full(ds: Dataset, *, seed: int = 0) -> LinearModel:
    """The deployable model fit on ALL labeled lines. Its score for an unlabeled line is honest:
    that line is not a training row. Book-held-out CV remains the performance measurement."""
    M, yv = _matrix(ds)
    match model := _fit(M, yv, seed=seed, columns=ds.columns):
        case LinearModel():
            return model
        case ConstantModel():
            raise ValueError("a deployable student requires both prose and lineated truth")
        case unsupported:
            assert_never(unsupported)


@dataclass
class CVResult:
    n: int
    n_books: int
    accuracy: float
    balanced_accuracy: float
    macro_f1: float
    prose_f1: float
    lineated_f1: float
    prose_precision: float
    prose_recall: float
    confusion: dict[str, int]        # confusion-cell name (prose_as_lineated, …) -> count
    majority_baseline_acc: float
    zero_support_columns: list[FeatureName]
    oof_pred: LabelByLine            # out-of-fold predicted label per line (book held out)


def train_cv(ds: Dataset, *, seed: int = 0) -> CVResult:
    """Book-grouped leave-one-book-out CV with an interpretable logistic regression (every
    test book is unseen). Standardize within each fold's TRAIN only (no test leakage). Report
    balanced metrics — the labels are ~6:1 lineated:prose, so raw accuracy misleads."""
    import numpy as np
    from sklearn.metrics import (
        balanced_accuracy_score,
        confusion_matrix,
        f1_score,
        precision_score,
        recall_score,
    )
    from sklearn.model_selection import LeaveOneGroupOut

    M, yv = _matrix(ds)
    groups = np.array([str(g) for g in ds.groups])   # sklearn group labels; "ru:01" ≠ "en:01"
    logo = LeaveOneGroupOut()

    y_true_all: list[int] = []
    y_pred_all: list[int] = []
    oof: LabelByLine = {}
    for tr, te in logo.split(M, yv, groups):
        model = _fit(M[tr], yv[tr], seed=seed, columns=ds.columns)
        proba = np.asarray(model._posteriors_from_matrix(M[te]))
        pred = (proba >= 0.5).astype(int)
        y_true_all.extend(yv[te].tolist())
        y_pred_all.extend(pred.tolist())
        for local_i, global_i in enumerate(te):
            oof[ds.ids[global_i]] = "lineated" if pred[local_i] == 1 else "prose"

    yt = np.array(y_true_all)
    yp = np.array(y_pred_all)
    cm = confusion_matrix(yt, yp, labels=[0, 1])  # rows true [prose,lineated]
    bal = Counter(ds.y)
    maj = max(bal.values()) / sum(bal.values())

    return CVResult(
        n=len(ds.y), n_books=len(set(ds.groups)),
        accuracy=float((yt == yp).mean()),
        balanced_accuracy=float(balanced_accuracy_score(yt, yp)),
        macro_f1=float(f1_score(yt, yp, average="macro")),
        prose_f1=float(f1_score(yt, yp, pos_label=0)),
        lineated_f1=float(f1_score(yt, yp, pos_label=1)),
        prose_precision=float(precision_score(yt, yp, pos_label=0, zero_division=0)),
        prose_recall=float(recall_score(yt, yp, pos_label=0, zero_division=0)),
        confusion={"prose_as_prose": int(cm[0, 0]), "prose_as_lineated": int(cm[0, 1]),
                   "lineated_as_prose": int(cm[1, 0]), "lineated_as_lineated": int(cm[1, 1])},
        majority_baseline_acc=float(maj),
        zero_support_columns=[c for c, n in ds.feature_support.items() if n == 0],
        oof_pred=oof,
    )


def _oof_scored_documents(
    ds: Dataset, records: RecordsByBook, *, seed: int = 0,
) -> dict[BookKey, sequence.ScoredDocument]:
    """Fit and batch-score each labeled document with that document's book held out."""
    import numpy as np

    M, yv = _matrix(ds)
    groups = np.array([str(g) for g in ds.groups])
    missing = sorted(set(ds.groups) - records.keys())
    if missing:
        raise ValueError(f"no record document for labeled books {missing}")
    out: dict[BookKey, sequence.ScoredDocument] = {}
    for book in sorted(records):
        document_records = records[book]
        if any(record.id.book_key != book for record in document_records):
            raise ValueError(f"record document stored under the wrong book key {book}")
        training = np.flatnonzero(groups != str(book))
        if not len(training):
            raise ValueError(f"no training books remain while holding out {book}")
        model = _fit(M[training], yv[training], seed=seed, columns=ds.columns)
        out[book] = sequence.score_document(document_records, model)
    return out


def oof_smoothed(ds: Dataset, records: RecordsByBook, *,
                 alpha: float = 0.75, seed: int = 0) -> dict[LineId, sequence.LineDecision]:
    """Decode one book-held-out base score per votable line under a smoothing policy."""
    out: dict[LineId, sequence.LineDecision] = {}
    for document in _oof_scored_documents(ds, records, seed=seed).values():
        for decision in sequence.decide_document(document, alpha=alpha):
            out[decision.id] = decision
    return out


@dataclass
class SequenceCV:
    alpha: float
    balanced_accuracy: float
    macro_f1: float
    prose_recall: float
    n_changed_vs_iid: int   # labeled lines whose label flipped relative to alpha=0


def _evaluate_alpha(
    documents: dict[BookKey, sequence.ScoredDocument], truth: LabelByLine,
    *, alpha: float,
) -> SequenceCV:
    """Score one decoding policy over an already cross-fitted attributed corpus."""
    import numpy as np
    from sklearn.metrics import balanced_accuracy_score, f1_score, recall_score

    y_true: list[int] = []
    y_iid: list[int] = []
    y_seq: list[int] = []
    for document in documents.values():
        for decision in sequence.decide_document(document, alpha=alpha):
            if decision.id in truth:
                y_true.append(1 if truth[decision.id] == "lineated" else 0)
                y_seq.append(1 if decision.label == "lineated" else 0)
                y_iid.append(1 if decision.base_posterior >= 0.5 else 0)

    yt, ys, yi = np.array(y_true), np.array(y_seq), np.array(y_iid)
    return SequenceCV(
        alpha=alpha,
        balanced_accuracy=float(balanced_accuracy_score(yt, ys)),
        macro_f1=float(f1_score(yt, ys, average="macro")),
        prose_recall=float(recall_score(yt, ys, pos_label=0, zero_division=0)),
        n_changed_vs_iid=int((ys != yi).sum()),
    )


def tune_alpha(ds: Dataset, labelset: LabelSet,
               records: RecordsByBook, *, grid: Sequence[float] = (0.0, 0.25, 0.5, 0.75, 1.0),
               seed: int = 0) -> list[SequenceCV]:
    """Sweep alpha under book-grouped CV. alpha=0 is the i.i.d. baseline; a higher alpha is
    worth adopting only if it improves the held-out metric WITHOUT collapsing prose recall."""
    if not grid:
        return []
    cv_records = {book: records[book] for book in sorted(set(ds.groups))}
    documents = _oof_scored_documents(ds, cv_records, seed=seed)
    truth: LabelByLine = {label.id: label.label for label in labelset.trainable}
    return [_evaluate_alpha(documents, truth, alpha=alpha) for alpha in grid]


if __name__ == "__main__":
    labelset = load_labels()
    records = store.load_records_many(sorted({g.id.book_key for g in labelset.labels}))
    ds = build_dataset(records, labelset)
    print(f"dataset: {ds.n_joined} labeled lines over {len(set(ds.groups))} books; "
          f"{len(ds.columns)} feature columns")
    print("class balance:", dict(Counter(ds.y)))
    res = train_cv(ds)
    print()
    print("=== leave-one-book-out CV (interpretable logistic regression, balanced) ===")
    print(f"  n={res.n}  books={res.n_books}  "
          f"majority-baseline acc={res.majority_baseline_acc:.3f}")
    print(f"  accuracy            = {res.accuracy:.3f}")
    print(f"  balanced_accuracy   = {res.balanced_accuracy:.3f}")
    print(f"  macro_F1            = {res.macro_f1:.3f}")
    print(f"  prose  F1/P/R       = {res.prose_f1:.3f} / {res.prose_precision:.3f} / "
          f"{res.prose_recall:.3f}")
    print(f"  lineated F1         = {res.lineated_f1:.3f}")
    print(f"  confusion           = {res.confusion}")
    print(f"  zero-support cols   = {res.zero_support_columns}")
    print("  top coefficients (|w|):")
    for c, w in fit_full(ds).explain()[:12]:
        print(f"     {w:+.3f}  {c}")
    print()
    print("=== sequence-shaped: run-level soft smoothing, alpha swept under book-grouped CV ===")
    print(f"  {'alpha':>6} {'balAcc':>8} {'macroF1':>8} {'proseRec':>9} {'flips_vs_iid':>13}")
    for s in tune_alpha(ds, labelset, records):
        print(f"  {s.alpha:>6.2f} {s.balanced_accuracy:>8.3f} {s.macro_f1:>8.3f} "
              f"{s.prose_recall:>9.3f} {s.n_changed_vs_iid:>13}")
