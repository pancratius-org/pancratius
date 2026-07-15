# research-pure: document posterior attribution + run-aware decoding.
"""Sequence-shaped decisions — score a document once, then interpret it under a policy.

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

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Protocol

from .identity import Label, LineId
from .records import LineFeatures, LineRecord, runs


@dataclass(frozen=True, slots=True)
class LineDecision:
    id: LineId
    label: Label          # prose | lineated  (votable lines only)
    posterior: float      # smoothed P(lineated)
    base_posterior: float # the per-line P(lineated) before smoothing


class PosteriorScorer(Protocol):
    """One operational posterior surface: an ordered feature batch → ordered probabilities.

    Batch cardinality and line attribution are checked by `score_document`; implementations own
    only model inference and never receive source identities or structural records.
    """

    def posteriors(self, features: Sequence[LineFeatures]) -> Sequence[float]: ...


@dataclass(frozen=True, slots=True)
class BasePosterior:
    """A model probability attributed to the source line it describes."""

    id: LineId
    value: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.value) or not 0.0 <= self.value <= 1.0:
            raise ValueError("posterior must be finite and between zero and one")


@dataclass(frozen=True, slots=True)
class ScoredDocument:
    """One record document with exactly one base posterior per votable line, in source order."""

    records: tuple[LineRecord, ...]
    scores: tuple[BasePosterior, ...]

    def __post_init__(self) -> None:
        identities = tuple(record.id for record in self.records)
        if len(identities) != len(set(identities)):
            raise ValueError("a scored document requires unique record identities")
        if len({identity.book_key for identity in identities}) > 1:
            raise ValueError("a scored document cannot mix books or languages")
        if any(current < previous for previous, current in pairwise(identities)):
            raise ValueError("a scored document requires source-ordered records")
        expected = tuple(record.id for record in self.records if record.votable)
        actual = tuple(score.id for score in self.scores)
        if actual != expected:
            raise ValueError("posterior identities must match votable records in document order")

    @property
    def by_id(self) -> dict[LineId, float]:
        return {score.id: score.value for score in self.scores}


def score_document(
    records: Sequence[LineRecord], scorer: PosteriorScorer,
) -> ScoredDocument:
    """Attribute one batched model call to every votable line in a record document."""
    document = tuple(records)
    votable = tuple(record for record in document if record.votable)
    values = (
        tuple(scorer.posteriors(tuple(record.features for record in votable)))
        if votable else ()
    )
    if len(values) != len(votable):
        raise ValueError(
            f"posterior scorer returned {len(values)} values for {len(votable)} votable lines"
        )
    scores = tuple(
        BasePosterior(record.id, value)
        for record, value in zip(votable, values, strict=True)
    )
    return ScoredDocument(document, scores)


def decide_document(
    document: ScoredDocument, *, alpha: float = 0.0, threshold: float = 0.5,
) -> list[LineDecision]:
    """Decode attributed base posteriors with run-level soft smoothing."""
    if not 0.0 <= alpha <= 1.0:
        raise ValueError(f"alpha must be in [0,1], got {alpha}")
    if not 0.0 <= threshold <= 1.0:
        raise ValueError(f"threshold must be in [0,1], got {threshold}")
    base = document.by_id
    out: list[LineDecision] = []
    for run in runs(document.records):
        mean = sum(base[document.records[i].id] for i in run) / len(run)
        for i in run:
            record = document.records[i]
            posterior = base[record.id]
            smoothed = (1.0 - alpha) * posterior + alpha * mean
            out.append(LineDecision(
                id=record.id, label="lineated" if smoothed >= threshold else "prose",
                posterior=smoothed, base_posterior=posterior,
            ))
    return out


def predict_document(
    records: Sequence[LineRecord], scorer: PosteriorScorer, *, alpha: float = 0.0,
    threshold: float = 0.5,
) -> list[LineDecision]:
    """Sequence-shaped per-line decisions with run-level soft smoothing.

    alpha=0 reproduces the i.i.d. student exactly (a strict superset — proven by test). With
    alpha>0 each votable line's posterior is blended toward its run's mean; a line's own
    evidence still dominates unless the whole block disagrees with it."""
    return decide_document(
        score_document(records, scorer), alpha=alpha, threshold=threshold,
    )
