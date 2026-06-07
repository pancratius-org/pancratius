# research-pure: the sequence-shaped prediction API (predict_document) + run smoothing.
"""Sequence-shaped decisions — `predict_document(records) -> [LineDecision]`.

The student is per-line, but the decision is hierarchical: a set of similar BLOCKS (sometimes
the whole book) → a block → a line. Region coherence is REAL — on the labeled corpus 85.7% of
label-bearing runs are homogeneous, and the few mixed runs are lopsided. So an isolated line
whose physics misfires inside an otherwise-uniform block is probably wrong; pulling it toward
the block consensus should help — WITHOUT erasing genuine splits (a prose lead-in before a
lineated stanza), which a hard majority vote would destroy.

Steps:
  1. per-line posterior P(lineated) from the fitted model (the i.i.d. base);
  2. group consecutive BODY lines (`role == BODY`) into RUNS (a block — bounded by any
     structural slot; the SAME predicate the producer's run features use, so an interior
     unmapped body line continues the stanza rather than splitting it);
  3. SOFT-smooth each VOTABLE line's posterior toward its block's votable-member mean by weight
     `alpha` (0 = pure per-line, 1 = pure block consensus). Soft, not hard, so confident
     within-run splits survive.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .identity import Label, LineId
from .records import LineFeatures, LineRecord, Role

type RecordIndex = int           # a 0-based position into a records sequence — NOT a src_ordinal
type Run = list[RecordIndex]     # one run's record indices: a maximal BODY block (an authorial unit)


@dataclass(frozen=True, slots=True)
class LineDecision:
    id: LineId
    label: Label          # prose | lineated  (votable lines only)
    posterior: float      # smoothed P(lineated)
    base_posterior: float # the per-line P(lineated) before smoothing
    run_id: int           # which run/block this line belongs to (-1 = not votable)


class Posterior(Protocol):
    """Anything that maps features → P(lineated). A fitted model satisfies it structurally
    (`student.FittedModel`), so this module keeps NO sklearn/numpy dependency and stays
    unit-testable with a stub — no implementer imports or subclasses this."""

    def __call__(self, features: LineFeatures) -> float: ...


def runs(records: Sequence[LineRecord]) -> list[Run]:
    """Indices grouped into runs: maximal spans of consecutive BODY lines (`role == BODY`),
    bounded by any structural record — the block level of the hierarchy, and the SAME predicate
    the producer's `run_len`/`run_pos` features use, so the two notions of "run" agree. An
    interior unmapped body line (`role == BODY` but `votable == False`) CONTINUES its stanza
    rather than splitting it; `smooth_runs` averages over the votable members only, and the teacher
    tiler keeps a whole run together as one authorial unit."""
    runs: list[list[int]] = []
    cur: list[int] = []
    for i, r in enumerate(records):
        if r.role is Role.BODY:
            cur.append(i)
        elif cur:
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    return runs


def smooth_runs(
    records: Sequence[LineRecord], base: Sequence[float], *, alpha: float = 0.0,
    threshold: float = 0.5,
) -> list[LineDecision]:
    """Run-level soft smoothing of PRE-COMPUTED per-line base posteriors (one entry per record;
    non-votable entries are ignored). Blends each votable line's posterior toward its run mean by
    `alpha`. Split out so a caller that already scored a whole book in one batch (`oof_smoothed`,
    the review queue) reuses the SAME smoothing definition as `predict_document` instead of a
    second copy — there is one run-smoother."""
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(f"alpha must be in [0,1], got {alpha}")
    out: list[LineDecision] = []
    for rid, run in enumerate(runs(records)):
        votable = [i for i in run if records[i].votable]
        if not votable:                       # an all-unmapped block — nothing to decide
            continue
        mean = sum(base[i] for i in votable) / len(votable)   # votable members only
        for i in votable:
            smoothed = (1.0 - alpha) * base[i] + alpha * mean
            out.append(LineDecision(
                id=records[i].id, label="lineated" if smoothed >= threshold else "prose",
                posterior=smoothed, base_posterior=base[i], run_id=rid,
            ))
    return out


def predict_document(
    records: Sequence[LineRecord], posterior: Posterior, *, alpha: float = 0.0,
    threshold: float = 0.5,
) -> list[LineDecision]:
    """Sequence-shaped per-line decisions with run-level soft smoothing.

    alpha=0 reproduces the i.i.d. student exactly (a strict superset — proven by test). With
    alpha>0 each votable line's posterior is blended toward its run's mean; a line's own
    evidence still dominates unless the whole block disagrees with it."""
    base = [posterior(r.features) if r.votable else 0.0 for r in records]
    return smooth_runs(records, base, alpha=alpha, threshold=threshold)
