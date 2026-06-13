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

import random
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from . import paths, recon, sequence, store, student
from .annotations import LabelSet, load_labels
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


# --- the random instrument: one sample, three jobs (acceptance set, AL seed, signal substrate) ---


def stratified_sample(strata: Mapping[str, Sequence[LineId]], n: int,
                      *, seed: int) -> dict[str, list[LineId]]:
    """A self-weighting random sample of `n` lines: every line's inclusion probability is exactly
    `n/total`, allocated across strata within ±1 of proportional and summing to exactly `n`, drawn
    without replacement within each stratum. The fractional quotas are resolved by SYSTEMATIC
    sampling over a randomly toured cumulative sum (each cell rounds up with probability equal to
    its fraction) — a deterministic largest-remainder would give a low-remainder cell probability
    ZERO, biasing the sample against small strata. Deterministic for a (strata, n, seed). Pure."""
    total = sum(len(v) for v in strata.values())
    if not 0 < n <= total:
        raise ValueError(f"cannot sample {n} of {total}")
    rng = random.Random(seed)
    quota = {k: len(strata[k]) * n // total for k in strata}
    leftovers = n - sum(quota.values())
    if leftovers:
        tour = sorted(strata)
        rng.shuffle(tour)
        acc, offset, hits = 0.0, rng.random(), 0
        for k in tour:
            acc += (len(strata[k]) * n % total) / total   # the fractions sum to `leftovers`
            if acc > offset + hits:
                quota[k] += 1
                hits += 1
    return {k: rng.sample(sorted(strata[k]), quota[k]) for k in sorted(strata) if quota[k]}


def split_halves(sampled: Mapping[str, Sequence[LineId]],
                 *, seed: int) -> tuple[list[LineId], list[LineId]]:
    """Split a stratified sample into two halves, alternating WITHIN each stratum (after a
    seeded shuffle), so both halves stay proportionally representative (±1 per stratum). An
    odd stratum's extra line goes to whichever half is currently smaller, so the odd-cell
    surplus never accumulates into a lopsided global split. Returns (frozen, working), each
    sorted. Pure."""
    rng = random.Random(seed)
    frozen: list[LineId] = []
    working: list[LineId] = []
    for k in sorted(sampled):
        lines = list(sampled[k])
        rng.shuffle(lines)
        first, second = (frozen, working) if len(frozen) <= len(working) else (working, frozen)
        first.extend(lines[0::2])
        second.extend(lines[1::2])
    return sorted(frozen), sorted(working)


def commit_instrument(name: str = "e1-instrument", *, n: int = 1500, seed: int = 0,
                      annotations: Path | None = None) -> dict[str, int]:
    """Mint the keystone instrument: `n` uniformly random votable lines over the whole corpus,
    stratified proportionally by language × book × tier-0 verdict (read from the recon rows),
    split into a frozen acceptance half and a working half. Commits ONE selection per language
    (a teacher recipe is single-language) and the two split MEMBERSHIPS — the recipe's
    `holdout_eval_set` must name the frozen one so its labels are promoted eval-only. REFUSES to
    overwrite an existing instrument: the frozen membership is single-mint (recon rows are derived
    and re-runnable, so a silent re-mint would quietly swap the acceptance set); delete the files
    deliberately to re-mint. Returns the per-stratum sample sizes for the pre-spend report."""
    for existing in (f"{name}-frozen", f"{name}-working"):
        try:
            store.load_eval_set(existing, annotations=annotations)
        except FileNotFoundError:
            continue
        raise ValueError(f"instrument {name!r} is already minted ({existing} exists) — "
                         f"it is single-mint; delete its files deliberately to re-mint")
    strata: dict[str, list[LineId]] = defaultdict(list)
    for lang in ("ru", "en"):
        for book_id in paths.corpus_books(lang):
            for d in store.load_recon_rows(book_id, lang):
                row = recon.LineRecon.from_dict(d)
                strata[f"{lang}:{book_id}:{row.det.value}"].append(row.id)

    sampled = stratified_sample(strata, n, seed=seed)
    frozen, working = split_halves(sampled, seed=seed)
    all_keys = sorted(lid for lines in sampled.values() for lid in lines)
    for lang in ("ru", "en"):
        store.save_selection(f"{name}-{lang}",
                             [lid.as_key() for lid in all_keys if lid.lang == lang],
                             annotations=annotations)
    store.write_eval_set(f"{name}-frozen", [lid.as_key() for lid in frozen],
                         annotations=annotations)
    store.write_eval_set(f"{name}-working", [lid.as_key() for lid in working],
                         annotations=annotations)
    return {k: len(v) for k, v in sorted(sampled.items())}


def _print_item(q: QueueItem) -> None:
    tag = (f"label={q.existing_label} student={q.student_label}"
           if q.kind == "disagreement" else f"student={q.student_label}")
    print(f"  [{q.id.book_id}] {q.id.src_ordinal}.{q.id.sub}  p(lineated)={q.posterior:.2f} "
          f"margin={q.margin:.2f}  {tag}")
    for c in q.context:
        print(f"      {c}")


if __name__ == "__main__":
    labelset = load_labels()
    records = store.load_records_many(sorted({g.id.book_id for g in labelset.labels}))
    q = build_queue(records, labelset)
    print(f"=== AUDIT: {len(q.audit)} labeled disagreements (book-held-out vs human), "
          f"most confident first (likely label errors / hardest cases) ===")
    for item in q.audit[:15]:
        _print_item(item)
