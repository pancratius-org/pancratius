"""Typed decision vocabulary for production intent inference."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import NewType

ArtifactId = NewType("ArtifactId", str)
CandidateId = NewType("CandidateId", str)
DataFingerprint = NewType("DataFingerprint", str)
FeatureSchemaHash = NewType("FeatureSchemaHash", str)

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]


class IntentTask(StrEnum):
    DISPLAY_REGISTER = "display_register"


class SchemaId(StrEnum):
    REGISTER_ARTIFACT_V1 = "pancratius.register_artifact.v1"
    REGISTER_OBSERVATION_V1 = "pancratius.register_observation.v1"
    DISPLAY_REGISTER_LABELS_V1 = "pancratius.display_register.labels.v1"


class FeatureSetId(StrEnum):
    VERSE_REGISTER_FEATURES_V1 = "pancratius.verse_register_features.v1"


class ScorerFamily(StrEnum):
    STANDARDIZED_LINEAR_V1 = "standardized_linear.v1"


class DisplayRegisterLabel(StrEnum):
    ORDINARY = "ordinary"
    VERSE = "verse"
    SCRIPTURE = "scripture"
    INSET = "inset"
    VOICE = "voice"


class ScoreKind(StrEnum):
    POSTERIOR = "posterior"


class DecisionOutcome(StrEnum):
    ACCEPT_RULES = "accept_rules"
    ACCEPT_MODEL = "accept_model"
    FALLBACK_TO_RULES = "fallback_to_rules"
    REFUSE_UNSUPPORTED_LANGUAGE = "refuse_unsupported_language"
    REFUSE_CONTRACT = "refuse_contract"


class RegisterDecisionReason(StrEnum):
    HARD_GUARD = "hard_guard"
    RULES = "rules"
    MODEL_THRESHOLD = "model_threshold"
    MODEL_RULES_DISAGREE = "model_rules_disagree"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    ARTIFACT_CONTRACT = "artifact_contract"


class DiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    FATAL = "fatal"


class IntentDiagnosticCode(StrEnum):
    MODEL_RULES_DISAGREE = "model_rules_disagree"
    UNSUPPORTED_LANGUAGE = "unsupported_language"
    ARTIFACT_CONTRACT = "artifact_contract"
    FALLBACK_USED = "fallback_used"


_EMPTY_EVIDENCE: Mapping[str, JsonValue] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class IntentDiagnostic:
    code: IntentDiagnosticCode
    severity: DiagnosticSeverity
    subject: CandidateId
    message: str
    evidence: Mapping[str, JsonValue] = _EMPTY_EVIDENCE


@dataclass(frozen=True, slots=True)
class PredictorRef:
    task: IntentTask
    artifact_id: ArtifactId
    artifact_schema: SchemaId
    observation_schema: SchemaId
    label_space: SchemaId
    scorer_family: ScorerFamily
    feature_set: FeatureSetId


@dataclass(frozen=True, slots=True)
class LabelScore:
    label: DisplayRegisterLabel
    score: float | None
    score_kind: ScoreKind
    predictor: PredictorRef


@dataclass(frozen=True, slots=True)
class Prediction:
    primary: LabelScore
    alternatives: tuple[LabelScore, ...] = ()


@dataclass(frozen=True, slots=True)
class RegisterDecision:
    subject: CandidateId
    outcome: DecisionOutcome
    label: DisplayRegisterLabel | None
    reason: RegisterDecisionReason
    prediction: Prediction | None = None
    fallback_label: DisplayRegisterLabel | None = None
    diagnostics: tuple[IntentDiagnostic, ...] = ()
