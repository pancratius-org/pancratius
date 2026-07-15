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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol, Self, cast

from . import producer
from .records import (
    FEATURE_SCHEMA_VERSION,
    PRODUCER_VERSION,
    FeatureName,
    LineFeatures,
)


class _Scaler(Protocol):
    mean_: Iterable[float]
    scale_: Iterable[float]


class _Classifier(Protocol):
    coef_: tuple[tuple[float, ...], ...]
    intercept_: tuple[float, ...]


class ExportableModel(Protocol):
    scaler: _Scaler
    clf: _Classifier
    columns: list[FeatureName]
    single_class: int | None

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
    feature_schema_version: str = FEATURE_SCHEMA_VERSION
    producer_version: str = PRODUCER_VERSION

    def __post_init__(self) -> None:
        n = len(self.columns)
        if not n:
            raise ValueError("a posterior needs at least one feature column")
        if not (len(self.mean) == len(self.std) == len(self.coef) == n):
            raise ValueError("columns, mean, std, coef must be the same length")
        expected = tuple(producer.vector_columns())
        if self.columns != expected:
            raise ValueError("posterior feature columns do not exactly match the live contract")
        values = (*self.mean, *self.std, *self.coef, self.intercept)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("posterior parameters must be finite")
        if any(s <= 0.0 for s in self.std):
            raise ValueError("posterior std entries must be positive")
        if self.feature_schema_version != FEATURE_SCHEMA_VERSION:
            raise ValueError(
                f"posterior feature contract {self.feature_schema_version!r} "
                f"!= live {FEATURE_SCHEMA_VERSION!r}"
            )
        if self.producer_version != PRODUCER_VERSION:
            raise ValueError(
                f"posterior producer contract {self.producer_version!r} "
                f"!= live {PRODUCER_VERSION!r}"
            )

    def __call__(self, features: LineFeatures) -> float:
        vec = producer.vectorize_fixed(features)
        z = self.intercept
        for c, m, s, w in zip(self.columns, self.mean, self.std, self.coef, strict=True):
            z += w * ((vec[c] - m) / s)
        return _sigmoid(z)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": WEIGHTS_SCHEMA,
            "feature_schema_version": self.feature_schema_version,
            "producer_version": self.producer_version,
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
            columns=tuple(str(x) for x in cast(Iterable[object], d["features"])),
            mean=tuple(float(str(x)) for x in cast(Iterable[object], d["mean"])),
            std=tuple(float(str(x)) for x in cast(Iterable[object], d["std"])),
            coef=tuple(float(str(x)) for x in cast(Iterable[object], d["coef"])),
            intercept=float(str(d["intercept"])),
            feature_schema_version=str(d.get("feature_schema_version", "")),
            producer_version=str(d.get("producer_version", "")),
        )


def export_posterior(model: ExportableModel) -> StandardizedLinearPosterior:
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
