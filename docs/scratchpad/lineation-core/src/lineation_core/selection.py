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

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import sequence, store, student
from .annotations import LabelSet, load
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


def _cap_per_book(items: list[QueueItem], top: int, per_book_cap: int | None) -> list[QueueItem]:
    """Take up to `top` items in the given (least-confident-first) order, but no more than
    `per_book_cap` from any one book — so a few ambiguous books cannot dominate a labeling batch.
    Without a cap, the plain least-confidence head."""
    if per_book_cap is None:
        return items[:top]
    out: list[QueueItem] = []
    per: Counter[BookId] = Counter()
    for it in items:
        if per[it.id.book_id] >= per_book_cap:
            continue
        out.append(it)
        per[it.id.book_id] += 1
        if len(out) >= top:
            break
    return out


def build_queue(records: RecordsByBook, labelset: LabelSet, *,
                top_acquire: int = 300, per_book_cap: int | None = None, seed: int = 0,
                alpha: float = 0.75, books: list[BookId] | None = None) -> ReviewQueue:
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
    acquire = _cap_per_book(acquire, top_acquire, per_book_cap)
    prose_leaning = sum(1 for q in acquire if q.posterior < 0.5)
    return ReviewQueue(audit, acquire, n_votable, n_scored, prose_leaning, book_ids)


def commit_acquire(name: str = "acquire", *, top_acquire: int = 300, per_book_cap: int | None = 40,
                   alpha: float = 0.75, annotations: Path | None = None) -> ReviewQueue:
    """Build the acquire queue over the labeled books' UNLABELED votable lines and COMMIT its
    LineIds as a selection the teacher consumes (`store.save_selection` → the eval→file→teacher
    handoff, no teacher import). `per_book_cap` (default 40) bounds how many lines any one book
    contributes, so the first paid batch is not dominated by a couple of ambiguous books — the
    margin cost is tiny and the coverage gain large; pass None for the plain least-confidence head.
    Returns the queue so the caller can report the distribution BEFORE spending on a paid panel."""
    labelset = load(annotations=annotations)
    records = store.load_records_many(sorted({g.id.book_id for g in labelset.labels}))
    q = build_queue(records, labelset, top_acquire=top_acquire, per_book_cap=per_book_cap, alpha=alpha)
    store.save_selection(name, [item.id.as_key() for item in q.acquire], annotations=annotations)
    return q


def acquire_distribution(q: ReviewQueue) -> str:
    """A pre-spend summary of the acquire set: size, by-book counts, the student's predicted-class
    split, and the margin spread (least-confident first, so the head buys the most). The numbers a
    reviewer checks before authorizing a paid panel."""
    by_book = Counter(item.id.book_id for item in q.acquire)
    by_class = Counter(item.student_label for item in q.acquire)
    margins = [item.margin for item in q.acquire]
    lines = [
        f"acquire: {len(q.acquire)} lines over {len(by_book)} books "
        f"(scanned {q.n_votable} votable, {q.n_labeled_scored} labeled+scored)",
        f"  predicted class: {by_class['prose']} prose / {by_class['lineated']} lineated "
        f"({q.prose_leaning_acquire} lean prose — the corner to widen)",
    ]
    if margins:
        lines.append(f"  margin |p-0.5|: min {min(margins):.3f}  "
                     f"median {margins[len(margins) // 2]:.3f}  max {max(margins):.3f}")
    lines.append("  by book: " + ", ".join(f"{b}:{n}" for b, n in sorted(by_book.items())))
    return "\n".join(lines)


def _print_item(q: QueueItem) -> None:
    tag = (f"label={q.existing_label} student={q.student_label}"
           if q.kind == "disagreement" else f"student={q.student_label}")
    print(f"  [{q.id.book_id}] {q.id.src_ordinal}.{q.id.sub}  p(lineated)={q.posterior:.2f} "
          f"margin={q.margin:.2f}  {tag}")
    for c in q.context:
        print(f"      {c}")


if __name__ == "__main__":
    q = commit_acquire()
    print("committed acquire selection → annotations/selections/acquire.json\n")
    print(acquire_distribution(q) + "\n")
    print(f"=== AUDIT: {len(q.audit)} labeled disagreements (book-held-out vs human), "
          f"most confident first (likely label errors / hardest cases) ===")
    for item in q.audit[:15]:
        _print_item(item)
    print(f"\n=== ACQUIRE head: {min(15, len(q.acquire))} least-confident UNLABELED lines ===")
    for item in q.acquire[:15]:
        _print_item(item)
