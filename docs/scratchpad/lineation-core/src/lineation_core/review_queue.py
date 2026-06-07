# research-pure: the active-learning human-review queue over the artifact.
"""Which lines should a human look at? The ones where a judgment buys the most. Two queries for
the two goals (grow the training set; find questionable places):

  AUDIT   — LABELED lines where the student's book-held-out prediction disagrees with the human
            label. A disagreement is either a label error worth re-checking or the hardest kind
            of case. Sorted most-confident-first (a confident disagreement is the strongest
            signal). Uses the OUT-OF-FOLD prediction, so the student never trained on the line
            it is second-guessing — no leakage.
  ACQUIRE — UNLABELED votable lines the student is LEAST sure about (margin |P(lineated)-0.5| → 0,
            the classic least-confident query). A new label here is the most informative; the
            prose-leaning ones are counted, since the prose corner is the thin one to widen.
            Uses the full-data model — an unlabeled line is not a training row, so its posterior
            is the deployed model's honest uncertainty.

Confidence is the student's own posterior margin; logistic probabilities are only approximately
calibrated, so the queue RANKS by margin rather than thresholding an absolute number. Each item
carries its run-neighbours so a reviewer judges the unit, not a line in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from . import labels, sequence, store, student
from .identity import BookId, Label, LineId
from .records import LineRecord, RecordsByBook


@dataclass(frozen=True)
class QueueItem:
    id: LineId
    text: str
    posterior: float                       # P(lineated)
    margin: float                          # |posterior - 0.5| — lower = less confident
    kind: Literal["disagreement", "acquire"]  # audit disagreement vs active-learning acquire
    existing_label: Label | None           # the human label, for audit items
    student_label: Label                   # what the student says (prose | lineated)
    context: tuple[str, ...]               # rendered run-neighbours, this line marked "->"


@dataclass
class ReviewQueue:
    audit: list[QueueItem]           # labeled disagreements, most-confident first
    acquire: list[QueueItem]         # uncertain unlabeled, least-confident first
    n_votable: int                   # votable body lines scanned
    n_labeled_scored: int            # of those, labeled + out-of-fold scored
    prose_leaning_acquire: int       # acquire items the student calls prose (the corner to grow)
    books: list[BookId]


def _label(p: float) -> Label:
    return "lineated" if p >= 0.5 else "prose"


def _context(body: list[LineRecord], k: int, *, radius: int = 2) -> tuple[str, ...]:
    lo, hi = max(0, k - radius), min(len(body), k + radius + 1)
    return tuple(("-> " if j == k else "   ")
                 + f"{body[j].id.src_ordinal}.{body[j].id.sub}  {body[j].text}"
                 for j in range(lo, hi))


def build_queue(records: RecordsByBook, labelset: labels.LabelSet, *,
                top_acquire: int = 300, seed: int = 0, alpha: float = 0.75,
                books: list[BookId] | None = None) -> ReviewQueue:
    """Build the review queue with RUN-AWARE (smoothed) confidence. `books` defaults to the labeled
    books; it must be a subset of the loaded `records` map (load a wider map at the edge to acquire
    over more books). Audit uses the book-held-out smoothed OOF; acquire uses the full model + the
    same run smoothing — so a line judged against its block surfaces as low-confidence, not as a
    confident error (the i.i.d. failure mode). alpha=0 reproduces the per-line queue."""
    ds = student.build_dataset(records, labelset)
    oof = student.oof_smoothed(ds, records, alpha=alpha, seed=seed)  # smoothed, book-held-out (no leakage)
    full = student.fit_full(ds, seed=seed)
    label_by_id = {g.id: g for g in labelset.labels}
    book_ids = books if books is not None else sorted(set(ds.groups))
    missing = [b for b in book_ids if b not in records]
    if missing:
        raise ValueError(f"build_queue: no records loaded for books {missing}; "
                         f"load a records map covering `books` at the edge")

    audit: list[QueueItem] = []
    acquire: list[QueueItem] = []
    n_votable = n_scored = 0
    for book in book_ids:
        recs = records[book]
        vidx = [i for i, r in enumerate(recs) if r.votable]
        base = [0.0] * len(recs)
        for i, p in zip(vidx, full.posteriors([recs[i].features for i in vidx]), strict=True):
            base[i] = p
        smoothed = {d.id: d for d in sequence.smooth_runs(recs, base, alpha=alpha)}  # full-model
        body = [recs[i] for i in vidx]
        for k, r in enumerate(body):
            n_votable += 1
            lbl = label_by_id.get(r.id)
            if lbl is None:                       # unlabeled → acquire, full-model smoothed score
                p = smoothed[r.id].posterior
                acquire.append(QueueItem(r.id, r.text, p, abs(p - 0.5), "acquire", None,
                                         _label(p), _context(body, k)))
                continue
            d = oof.get(r.id)                     # labeled → audit on the book-held-out smoothed call
            if d is None:                         # labeled but unmapped/unscored — nothing to audit
                continue
            n_scored += 1
            if d.label != lbl.label:
                audit.append(QueueItem(r.id, r.text, d.posterior, abs(d.posterior - 0.5),
                                       "disagreement", lbl.label, d.label, _context(body, k)))

    audit.sort(key=lambda q: -q.margin)          # most-confident disagreement first
    acquire.sort(key=lambda q: q.margin)         # least-confident first
    acquire = acquire[:top_acquire]
    prose_leaning = sum(1 for q in acquire if q.posterior < 0.5)
    return ReviewQueue(audit, acquire, n_votable, n_scored, prose_leaning, book_ids)


def _print_item(q: QueueItem) -> None:
    tag = (f"label={q.existing_label} student={q.student_label}"
           if q.kind == "disagreement" else f"student={q.student_label}")
    print(f"  [{q.id.book_id}] {q.id.src_ordinal}.{q.id.sub}  p(lineated)={q.posterior:.2f} "
          f"margin={q.margin:.2f}  {tag}")
    for c in q.context:
        print(f"      {c}")


if __name__ == "__main__":
    labelset = labels.load()
    records = store.load_records_many(sorted({g.id.book_id for g in labelset.labels}))
    q = build_queue(records, labelset)
    print(f"scanned {q.n_votable} votable body lines over {len(q.books)} books "
          f"({q.n_labeled_scored} labeled+scored)\n")
    print(f"=== AUDIT: {len(q.audit)} labeled disagreements (student book-held-out vs human) — "
          f"most confident first (likely label errors / hardest cases) ===")
    for item in q.audit[:15]:
        _print_item(item)
    print(f"\n=== ACQUIRE: top {len(q.acquire)} least-confident UNLABELED lines "
          f"({q.prose_leaning_acquire} lean prose — the corner to widen) ===")
    for item in q.acquire[:15]:
        _print_item(item)
