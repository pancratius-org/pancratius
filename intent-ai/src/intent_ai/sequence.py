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
  2. group consecutive BODY lines (`role == BODY`) into RUNS (a block bounded by any
     structural slot; the SAME predicate the producer's run features use);
  3. SOFT-smooth each VOTABLE line's posterior toward its block's votable-member mean by weight
     `alpha` (0 = pure per-line, 1 = pure block consensus). Soft, not hard, so confident
     within-run splits survive.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .identity import Label, LineId
from .records import LineFeatures, LineRecord, runs


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
        if not votable:                       # a structural-only run — nothing to decide
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


class RunModel(Protocol):
    """A whole-run decision: `records -> [LineDecision]`. `SmoothedPosterior` decodes a per-line
    `Posterior` with run smoothing; a structured/sequence model is a peer that scores the run
    directly (no per-line decomposition). The decision step talks to THIS, so swapping the model
    is not a change to it."""

    def __call__(self, records: Sequence[LineRecord]) -> list[LineDecision]: ...


@dataclass(frozen=True, slots=True)
class SmoothedPosterior:
    """The baseline `RunModel`: a per-line `Posterior` decoded with run-level soft smoothing.
    alpha=0 is the pure i.i.d. student; alpha>0 pulls each line toward its run consensus."""

    posterior: Posterior
    alpha: float = 0.0
    threshold: float = 0.5

    def __call__(self, records: Sequence[LineRecord]) -> list[LineDecision]:
        return predict_document(records, self.posterior, alpha=self.alpha, threshold=self.threshold)
