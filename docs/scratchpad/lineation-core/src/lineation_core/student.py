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
from dataclasses import dataclass

from . import labels, producer, store
from .identity import LineId
from .records import LineFeatures, LineRecord


def records_for(book_id: str, lang: str = "ru") -> list[LineRecord]:
    """A book's records read through the on-disk ARTIFACT (load-only, hash-railed) — so every
    consumer is a view over the artifact, never a live producer call. FAILS LOUD on a missing
    or stale artifact (build the store first); it does not re-emit."""
    return store.load_records(book_id, lang)


@dataclass
class Dataset:
    X: list[dict[str, float]]   # per-line fixed-column feature vectors
    y: list[str]                # prose | lineated
    groups: list[str]           # book_id (for grouped CV)
    columns: list[str]
    feature_support: dict[str, int]
    ids: list[LineId]
    n_joined: int
    n_skipped_unmapped: int


def build_dataset(labelset: labels.LabelSet | None = None) -> Dataset:
    labelset = labelset if labelset is not None else labels.load()
    label_by_id = {g.id: g for g in labelset.labels}
    books = sorted({g.id.book_id for g in labelset.labels})

    X: list[dict[str, float]] = []
    y: list[str] = []
    groups: list[str] = []
    ids: list[LineId] = []
    n_joined = 0
    support: Counter[str] = Counter()
    cols = list(producer.vector_columns())

    for book_id in books:
        recs = {r.id: r for r in records_for(book_id)}
        for lid, g in label_by_id.items():
            if lid.book_id != book_id:
                continue
            rec = recs.get(lid)
            if rec is None:
                continue
            vec = producer.vectorize_fixed(rec.features)
            X.append(vec)
            y.append(g.label)
            groups.append(book_id)
            ids.append(lid)
            n_joined += 1
            for c, v in vec.items():
                if v != 0.0:
                    support[c] += 1

    feature_support = {c: support.get(c, 0) for c in cols}  # zero-support cols kept at 0
    # unmapped labels were rejected at the load boundary (6a); surface that count here, where
    # the dataset shape is reported, so the rejection stays visible end-to-end.
    return Dataset(
        X=X, y=y, groups=groups, columns=cols, feature_support=feature_support, ids=ids,
        n_joined=n_joined, n_skipped_unmapped=labelset.n_rejected_unmapped,
    )


def _matrix(ds: Dataset):
    import numpy as np
    M = np.zeros((len(ds.X), len(ds.columns)), dtype=float)
    for i, row in enumerate(ds.X):
        for j, c in enumerate(ds.columns):
            M[i, j] = row[c]
    yv = np.array([1 if lab == "lineated" else 0 for lab in ds.y])
    return M, yv


@dataclass
class FittedModel:
    """A fitted scaler+LR over the fixed feature columns, exposing the sequence module's
    `Posterior` interface: features → P(lineated). The single source of per-line scores for both
    the i.i.d. CV and `predict_document`, so smoothing layers on TOP of exactly the trained
    model, never a parallel scorer."""

    scaler: object
    clf: object
    columns: list[str]
    single_class: int | None = None  # set iff the train fold had one class (degenerate fold)

    def posterior(self, features: LineFeatures) -> float:
        if self.single_class is not None:
            return float(self.single_class)
        import numpy as np
        vec = producer.vectorize_fixed(features)
        x = np.array([[vec[c] for c in self.columns]], dtype=float)
        return float(self.clf.predict_proba(self.scaler.transform(x))[0, 1])

    def posteriors(self, feats: list[LineFeatures]) -> list[float]:
        """Batched `posterior` — one `predict_proba` over a whole corpus's unlabeled lines, so
        scoring a book does not become a per-line Python loop."""
        if not feats:
            return []
        if self.single_class is not None:
            return [float(self.single_class)] * len(feats)
        import numpy as np
        X = np.array([[producer.vectorize_fixed(f)[c] for c in self.columns] for f in feats],
                     dtype=float)
        return self.clf.predict_proba(self.scaler.transform(X))[:, 1].tolist()

    __call__ = posterior  # a FittedModel IS a sequence.Posterior


def _fit(M, y, *, seed: int, columns: list[str]) -> FittedModel:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler().fit(M)
    if len(set(y.tolist())) < 2:
        return FittedModel(scaler, None, columns, single_class=int(y[0]))
    clf = LogisticRegression(max_iter=2000, class_weight="balanced", random_state=seed, C=1.0)
    clf.fit(scaler.transform(M), y)
    return FittedModel(scaler, clf, columns)


def fit_full(ds: Dataset, *, seed: int = 0) -> FittedModel:
    """The deployable model fit on ALL labeled lines — its `posterior` is the honest current
    score for an UNLABELED line (that line is not a training row, so no leakage). The book-held-
    out CV remains the performance number; this is for scoring/serving, not self-evaluation."""
    M, yv = _matrix(ds)
    return _fit(M, yv, seed=seed, columns=ds.columns)


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
    confusion: dict[str, int]
    majority_baseline_acc: float
    coefficients: list[tuple[str, float]]
    zero_support_columns: list[str]
    oof_pred: dict[LineId, str]      # out-of-fold predicted label per line (book held out)


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
    groups = np.array(ds.groups)
    logo = LeaveOneGroupOut()

    y_true_all: list[int] = []
    y_pred_all: list[int] = []
    oof: dict[LineId, str] = {}
    for tr, te in logo.split(M, yv, groups):
        model = _fit(M[tr], yv[tr], seed=seed, columns=ds.columns)
        proba = np.array([model.clf.predict_proba(model.scaler.transform(M[te]))[:, 1]
                          if model.single_class is None
                          else np.full(len(te), float(model.single_class))]).ravel()
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

    full = _fit(M, yv, seed=seed, columns=ds.columns)  # for interpretable coefficients
    coefs = sorted(zip(ds.columns, full.clf.coef_[0]), key=lambda kv: abs(kv[1]), reverse=True)

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
        coefficients=[(c, round(w, 3)) for c, w in coefs],
        zero_support_columns=[c for c, n in ds.feature_support.items() if n == 0],
        oof_pred=oof,
    )


def oof_smoothed(ds: Dataset, *, alpha: float = 0.75, seed: int = 0):
    """Book-held-out run-SMOOTHED prediction (`sequence.LineDecision`) for every votable line in
    the labeled books. For each held-out book: fit on the others, score the book's lines in ONE
    batch, then `smooth_runs` over the book's record document (runs bound correctly). alpha=0
    reproduces the i.i.d. oof; the held-out book is never in the fit, so there is no leakage."""
    import numpy as np
    from sklearn.model_selection import LeaveOneGroupOut

    from . import sequence

    M, yv = _matrix(ds)
    groups = np.array(ds.groups)
    docs = {b: records_for(b) for b in sorted(set(ds.groups))}
    out: dict[LineId, sequence.LineDecision] = {}
    for tr, te in LeaveOneGroupOut().split(M, yv, groups):
        model = _fit(M[tr], yv[tr], seed=seed, columns=ds.columns)
        recs = docs[groups[te][0]]
        votable = [(i, r) for i, r in enumerate(recs) if r.votable]
        probs = model.posteriors([r.features for _, r in votable])      # batched per book
        base = [0.0] * len(recs)
        for (i, _), p in zip(votable, probs, strict=True):
            base[i] = p
        for d in sequence.smooth_runs(recs, base, alpha=alpha):
            out[d.id] = d
    return out


@dataclass
class SequenceCV:
    alpha: float
    balanced_accuracy: float
    macro_f1: float
    prose_recall: float
    n_changed_vs_iid: int   # labeled lines whose label flipped relative to alpha=0


def evaluate_alpha_cv(
    ds: Dataset, labelset: labels.LabelSet, *, alpha: float, seed: int = 0,
) -> SequenceCV:
    """Book-grouped CV of `predict_document` at a given alpha. For each held-out book: fit on
    the OTHER books, build that book's full record document (so runs bound correctly), run
    `predict_document`, and score the labeled votable lines against truth. alpha applies at
    TEST time only. alpha=0 is identical to the i.i.d. CV (smoothing is a strict superset)."""
    import numpy as np
    from sklearn.metrics import balanced_accuracy_score, f1_score, recall_score
    from sklearn.model_selection import LeaveOneGroupOut

    from . import sequence

    M, yv = _matrix(ds)
    groups = np.array(ds.groups)
    truth = {g.id: g.label for g in labelset.labels}
    docs = {b: records_for(b) for b in sorted(set(ds.groups))}

    y_true: list[int] = []
    y_iid: list[int] = []
    y_seq: list[int] = []
    for tr, te in LeaveOneGroupOut().split(M, yv, groups):
        book = groups[te][0]
        model = _fit(M[tr], yv[tr], seed=seed, columns=ds.columns)
        decisions = sequence.predict_document(docs[book], model, alpha=alpha)
        iid = sequence.predict_document(docs[book], model, alpha=0.0)
        iid_by_id = {d.id: d.label for d in iid}
        for d in decisions:
            if d.id in truth:
                y_true.append(1 if truth[d.id] == "lineated" else 0)
                y_seq.append(1 if d.label == "lineated" else 0)
                y_iid.append(1 if iid_by_id[d.id] == "lineated" else 0)

    yt, ys, yi = np.array(y_true), np.array(y_seq), np.array(y_iid)
    return SequenceCV(
        alpha=alpha,
        balanced_accuracy=float(balanced_accuracy_score(yt, ys)),
        macro_f1=float(f1_score(yt, ys, average="macro")),
        prose_recall=float(recall_score(yt, ys, pos_label=0, zero_division=0)),
        n_changed_vs_iid=int((ys != yi).sum()),
    )


def tune_alpha(ds: Dataset, labelset: labels.LabelSet, *, grid=(0.0, 0.25, 0.5, 0.75, 1.0),
               seed: int = 0) -> list[SequenceCV]:
    """Sweep alpha under book-grouped CV. alpha=0 is the i.i.d. baseline; a higher alpha is
    worth adopting only if it improves the held-out metric WITHOUT collapsing prose recall."""
    return [evaluate_alpha_cv(ds, labelset, alpha=a, seed=seed) for a in grid]


if __name__ == "__main__":
    labelset = labels.load()
    ds = build_dataset(labelset)
    print(f"dataset: {ds.n_joined} labeled lines over {len(set(ds.groups))} books "
          f"(rejected {ds.n_skipped_unmapped} unmapped); {len(ds.columns)} feature columns")
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
    for c, w in res.coefficients[:12]:
        print(f"     {w:+.3f}  {c}")
    print()
    print("=== sequence-shaped: run-level soft smoothing, alpha swept under book-grouped CV ===")
    print(f"  {'alpha':>6} {'balAcc':>8} {'macroF1':>8} {'proseRec':>9} {'flips_vs_iid':>13}")
    for s in tune_alpha(ds, labelset):
        print(f"  {s.alpha:>6.2f} {s.balanced_accuracy:>8.3f} {s.macro_f1:>8.3f} "
              f"{s.prose_recall:>9.3f} {s.n_changed_vs_iid:>13}")
