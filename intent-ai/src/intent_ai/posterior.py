# research-pure: the deployable, sklearn-free form of the student's per-line score.
"""Serialize the fitted student as weights a consumer applies with stdlib math alone.

`student.FittedModel` is a sklearn scaler + logistic regression; its score is, exactly,
`sigmoid(coef · ((x - mean) / std) + intercept)` over the fixed feature columns. So it serializes
to a few float arrays and applies with no sklearn on the path — which is what lets the importer
run the model as DATA, never importing a model family.

This is the standardized-linear form — THIS student's family. A tree, a CRF, or a distilled head
would carry its own serialized shape behind the same `sequence.Posterior` callable; the dict below
is not a universal contract.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Self

from . import producer
from .records import FeatureName, LineFeatures

if TYPE_CHECKING:
    from .student import FittedModel

WEIGHTS_SCHEMA = "intent_ai.lineation.standardized_linear.weights.v1"


def _sigmoid(z: float) -> float:
    # split on the sign so a large positive z can't overflow exp().
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


@dataclass(frozen=True, slots=True)
class StandardizedLinearPosterior:
    """A sklearn-free `P(lineated)` over the fixed feature columns. Satisfies `sequence.Posterior`
    (`features -> float`), so it drops into `predict_document` exactly where a fitted model does,
    with no sklearn/numpy on the path."""

    columns: tuple[FeatureName, ...]
    mean: tuple[float, ...]
    std: tuple[float, ...]
    coef: tuple[float, ...]
    intercept: float

    def __post_init__(self) -> None:
        n = len(self.columns)
        if not n:
            raise ValueError("a posterior needs at least one feature column")
        if not (len(self.mean) == len(self.std) == len(self.coef) == n):
            raise ValueError("columns, mean, std, coef must be the same length")
        if any(s == 0.0 for s in self.std):
            raise ValueError("std has a zero entry (a constant column standardizes with std 1.0)")

    def __call__(self, features: LineFeatures) -> float:
        vec = producer.vectorize_fixed(features)
        z = self.intercept
        for c, m, s, w in zip(self.columns, self.mean, self.std, self.coef, strict=True):
            z += w * ((vec[c] - m) / s)
        return _sigmoid(z)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": WEIGHTS_SCHEMA,
            "features": list(self.columns),
            "mean": list(self.mean),
            "std": list(self.std),
            "coef": list(self.coef),
            "intercept": self.intercept,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, object]) -> Self:
        if d.get("schema") != WEIGHTS_SCHEMA:
            raise ValueError(f"unexpected posterior schema {d.get('schema')!r}")
        return cls(
            columns=tuple(d["features"]),
            mean=tuple(float(x) for x in d["mean"]),
            std=tuple(float(x) for x in d["std"]),
            coef=tuple(float(x) for x in d["coef"]),
            intercept=float(d["intercept"]),
        )


def export_posterior(model: FittedModel) -> StandardizedLinearPosterior:
    """Lift a fitted scaler+LR student into its sklearn-free posterior. A degenerate single-class
    fit has no linear form; the deployable model is fit on the full labeled set, which has both."""
    if model.single_class is not None:
        raise ValueError("cannot export a single-class degenerate model as a linear posterior")
    return StandardizedLinearPosterior(
        columns=tuple(model.columns),
        mean=tuple(float(m) for m in model.scaler.mean_),
        std=tuple(float(s) for s in model.scaler.scale_),
        coef=tuple(float(w) for w in model.clf.coef_[0]),
        intercept=float(model.clf.intercept_[0]),
    )
