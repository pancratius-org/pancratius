"""Standardized linear scorer for the current verse-register artifact."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from pancratius.intent_inference.decisions import (
    ArtifactId,
    ArtifactSchemaId,
    DisplayRegisterLabel,
    FeatureSetId,
    IntentTask,
    LabelScore,
    LabelSpaceId,
    ObservationSchemaId,
    Prediction,
    PredictorRef,
    ScoreKind,
    ScorerFamily,
)
from pancratius.intent_inference.errors import RegisterArtifactError
from pancratius.intent_inference.observations import RegisterObservation
from pancratius.locales import Locale

_TERM_RE = re.compile(r"[.!?…]\s*$")
_DASH_LINE_RE = re.compile(r"^[—–-]\s")
_Q2P_RE = re.compile(r"\b(ты|тебя|тебе|тобой|твой|твоя|твоё|твои)\b", re.IGNORECASE)
_DIVINE_RE = re.compile(r"\b(Я|Меня|Мне|Мной|Мой|Моя|Моё|Мои)\b")
_QUOTE_OPEN_RE = re.compile(r"^[«\"„]")
_NUM_LEAD_RE = re.compile(r"^\d{1,4}[.:)\s]")

FEATURE_NAMES = (
    "n_lines", "mean_len", "max_len", "cv_len", "term_rate", "dash_rate",
    "q2p_rate", "divine_rate", "quote_open_rate", "num_lead_rate",
    "question_rate", "comma_end_rate", "lower_start_rate", "n_stanzas",
    "multi_line_stanzas", "ev_hard_break", "ev_inferred", "ev_stanza_break",
    "ev_compact_callout", "ctx_heading", "ctx_named", "ctx_separator",
    "len_vs_book", "book_lineated_frac",
)
_WEIGHT_FIELDS = frozenset({
    "schema",
    "version",
    "features",
    "mean",
    "std",
    "coef",
    "intercept",
    "threshold",
})


class StandardizedLinearWeightsSchema(StrEnum):
    V1 = "pancratius.standardized_linear.weights.v1"


@dataclass(frozen=True, slots=True)
class StandardizedLinearWeights:
    version: int
    features: tuple[str, ...]
    mean: tuple[float, ...]
    std: tuple[float, ...]
    coef: tuple[float, ...]
    intercept: float
    threshold: float


@dataclass(frozen=True, slots=True)
class StandardizedLinearRegisterScorer:
    version: int
    langs: tuple[Locale, ...]
    features: tuple[str, ...]
    mean: tuple[float, ...]
    std: tuple[float, ...]
    coef: tuple[float, ...]
    intercept: float
    threshold: float
    predictor_ref: PredictorRef

    def predict(self, observation: RegisterObservation) -> Prediction:
        probability = self.probability(observation)
        primary = LabelScore(
            label=DisplayRegisterLabel.VERSE,
            score=probability,
            score_kind=ScoreKind.POSTERIOR,
            predictor=self.predictor_ref,
        )
        alternative = LabelScore(
            label=DisplayRegisterLabel.ORDINARY,
            score=1.0 - probability,
            score_kind=ScoreKind.POSTERIOR,
            predictor=self.predictor_ref,
        )
        return Prediction(primary=primary, alternatives=(alternative,))

    def probability(self, observation: RegisterObservation) -> float:
        feats = verse_register_features(observation)
        z = self.intercept
        for name, mu, sd, weight in zip(
            self.features, self.mean, self.std, self.coef, strict=True
        ):
            z += weight * ((feats[name] - mu) / sd)
        return _sigmoid(z)


def verse_register_features(observation: RegisterObservation) -> dict[str, float]:
    lines = observation.lines
    n = len(lines)
    lens = [len(x) for x in lines] or [0]
    mean_len = sum(lens) / len(lens)
    rate = (lambda pred: sum(1 for x in lines if pred(x)) / n) if n else (lambda _pred: 0.0)
    return {
        "n_lines": float(n),
        "mean_len": mean_len,
        "max_len": float(max(lens)),
        "cv_len": (
            (sum((x - mean_len) ** 2 for x in lens) / len(lens)) ** 0.5 / mean_len
            if mean_len else 0.0
        ),
        "term_rate": rate(lambda x: bool(_TERM_RE.search(x))),
        "dash_rate": rate(lambda x: bool(_DASH_LINE_RE.match(x))),
        "q2p_rate": rate(lambda x: bool(_Q2P_RE.search(x))),
        "divine_rate": rate(lambda x: bool(_DIVINE_RE.search(x))),
        "quote_open_rate": rate(lambda x: bool(_QUOTE_OPEN_RE.match(x))),
        "num_lead_rate": rate(lambda x: bool(_NUM_LEAD_RE.match(x))),
        "question_rate": rate(lambda x: "?" in x),
        "comma_end_rate": rate(lambda x: x.rstrip().endswith((",", "—", "–"))),
        "lower_start_rate": rate(lambda x: x[:1].islower()),
        "n_stanzas": float(len(observation.stanza_line_counts)),
        "multi_line_stanzas": float(sum(1 for s in observation.stanza_line_counts if s > 1)),
        "ev_hard_break": float(observation.evidence.hard_break),
        "ev_inferred": float(observation.evidence.inferred_source_rows),
        "ev_stanza_break": float(observation.evidence.stanza_break),
        "ev_compact_callout": float(observation.evidence.compact_callout),
        "ctx_heading": float(observation.model_context.heading),
        "ctx_named": float(observation.model_context.named),
        "ctx_separator": float(observation.model_context.separator),
        "len_vs_book": (
            mean_len / observation.book.mean_para_len
            if observation.book.mean_para_len else 0.0
        ),
        "book_lineated_frac": observation.book.lineated_frac,
    }


def _string_tuple(raw: object, field: str) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)) or not all(isinstance(item, str) for item in raw):
        raise RegisterArtifactError(f"field {field!r} must be a list of strings")
    return tuple(str(item) for item in raw)


def _finite_float(raw: object, field: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise RegisterArtifactError(f"field {field!r} must be a number")
    try:
        value = float(raw)
    except OverflowError as exc:
        raise RegisterArtifactError(f"field {field!r} must be finite") from exc
    if not math.isfinite(value):
        raise RegisterArtifactError(f"field {field!r} must be finite")
    return value


def _finite_tuple(raw: object, field: str) -> tuple[float, ...]:
    if not isinstance(raw, (list, tuple)):
        raise RegisterArtifactError(f"field {field!r} must be a number list")
    return tuple(_finite_float(item, field) for item in raw)


def _weights_schema(raw: object) -> StandardizedLinearWeightsSchema:
    if not isinstance(raw, str):
        raise RegisterArtifactError("field 'schema' must be a string")
    try:
        return StandardizedLinearWeightsSchema(raw)
    except ValueError as exc:
        raise RegisterArtifactError(f"unknown standardized linear weights schema {raw!r}") from exc


def _version(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise RegisterArtifactError("field 'version' must be an integer")
    if raw <= 0:
        raise RegisterArtifactError("field 'version' must be positive")
    return raw


def _sigmoid(z: float) -> float:
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


def _parse_weights(weights: Mapping[str, object]) -> StandardizedLinearWeights:
    unknown = sorted(set(weights) - _WEIGHT_FIELDS)
    if unknown:
        raise RegisterArtifactError(f"unknown standardized linear weights field {unknown[0]!r}")
    schema = _weights_schema(weights["schema"])
    if schema is not StandardizedLinearWeightsSchema.V1:
        raise RegisterArtifactError(
            f"unsupported standardized linear weights schema {schema.value!r}"
        )
    return StandardizedLinearWeights(
        version=_version(weights["version"]),
        features=_string_tuple(weights["features"], "features"),
        mean=_finite_tuple(weights["mean"], "mean"),
        std=_finite_tuple(weights["std"], "std"),
        coef=_finite_tuple(weights["coef"], "coef"),
        intercept=_finite_float(weights["intercept"], "intercept"),
        threshold=_finite_float(weights["threshold"], "threshold"),
    )


def load_standardized_linear_register_scorer(
    weights: Mapping[str, object],
    *,
    artifact_id: ArtifactId,
    artifact_schema: ArtifactSchemaId,
    observation_schema: ObservationSchemaId,
    label_space: LabelSpaceId,
    scorer_family: ScorerFamily,
    feature_set: FeatureSetId,
    langs: tuple[Locale, ...],
) -> StandardizedLinearRegisterScorer:
    if scorer_family is not ScorerFamily.STANDARDIZED_LINEAR_V1:
        raise RegisterArtifactError(
            f"standardized linear loader received scorer family {scorer_family.value!r}"
        )
    if feature_set is not FeatureSetId.VERSE_REGISTER_FEATURES_V1:
        raise RegisterArtifactError(
            f"standardized linear loader received feature set {feature_set.value!r}"
        )
    try:
        parsed = _parse_weights(weights)
        if not 0.0 <= parsed.threshold <= 1.0:
            raise RegisterArtifactError("field 'threshold' must be between 0 and 1")
        scorer = StandardizedLinearRegisterScorer(
            version=parsed.version,
            langs=langs,
            features=parsed.features,
            mean=parsed.mean,
            std=parsed.std,
            coef=parsed.coef,
            intercept=parsed.intercept,
            threshold=parsed.threshold,
            predictor_ref=PredictorRef(
                task=IntentTask.DISPLAY_REGISTER,
                artifact_id=artifact_id,
                artifact_schema=artifact_schema,
                observation_schema=observation_schema,
                label_space=label_space,
                scorer_family=scorer_family,
                feature_set=feature_set,
            ),
        )
    except RegisterArtifactError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise RegisterArtifactError(f"malformed standardized linear weights: {exc}") from exc
    if scorer.features != FEATURE_NAMES:
        raise RegisterArtifactError(
            "standardized linear weights feature schema drifted from the producer"
        )
    if not (
        len(scorer.mean)
        == len(scorer.std)
        == len(scorer.coef)
        == len(scorer.features)
    ):
        raise RegisterArtifactError("standardized linear weights vector lengths disagree")
    if any(sd <= 0 for sd in scorer.std):
        raise RegisterArtifactError("standardized linear weights contain non-positive feature std")
    return scorer
