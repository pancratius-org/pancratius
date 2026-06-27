"""Q1 lineation fold policy: the seam that owns the final fold/no-fold verdict."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pancratius import ir


@dataclass(frozen=True, slots=True)
class LineationCandidate:
    """One Q1 fold decision the pass hands the policy: a model-backed policy
    would read `run` to score it; `RulesOnly` reads only `rules_infer`, the
    compiler's deterministic gate verdict."""

    run: tuple[ir.Paragraph, ...]
    evidence: ir.LineationEvidence
    after_source_boundary: bool
    before_source_boundary: bool
    after_lineated: bool
    rules_infer: bool


class LineationPolicy(Protocol):
    def infer_lineation(self, candidate: LineationCandidate) -> bool:
        """The pass calls this for the final fold verdict; it runs no model.
        `RulesOnly` returns the gate verdict unchanged; a model-backed policy
        may override where the rules are weak."""


@dataclass(frozen=True, slots=True)
class RulesOnlyLineationPolicy:
    def infer_lineation(self, candidate: LineationCandidate) -> bool:
        return candidate.rules_infer
