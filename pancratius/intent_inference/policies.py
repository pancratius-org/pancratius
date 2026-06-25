"""Register policies: rules choose defaults, scorers provide optional evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pancratius.intent_inference.decisions import (
    DecisionOutcome,
    DiagnosticSeverity,
    DisplayRegisterLabel,
    IntentDiagnostic,
    IntentDiagnosticCode,
    Prediction,
    RegisterDecision,
    RegisterDecisionReason,
    ScoreKind,
)
from pancratius.intent_inference.observations import (
    RegisterCandidate,
    RegisterDocumentContext,
    RegisterObservation,
)
from pancratius.locales import Locale


class PolicyMode(StrEnum):
    RULES_ONLY = "rules_only"
    ARTIFACT_OPTIONAL_DIAGNOSTIC_TEMP = "artifact_optional_diagnostic_temp"


class RegisterScorer(Protocol):
    version: int
    threshold: float
    langs: tuple[Locale, ...]

    def predict(self, observation: RegisterObservation) -> Prediction:
        """Score one candidate observation."""


class RegisterPolicy(Protocol):
    name: str
    mode: PolicyMode
    reports_model_delta: bool

    @property
    def model_version(self) -> int | None:
        """Model artifact version when this policy can emit model diagnostics."""

    def decide_document(
        self,
        candidates: tuple[RegisterCandidate, ...],
        context: RegisterDocumentContext,
    ) -> tuple[RegisterDecision, ...]:
        """Return one materializable decision per candidate."""


@dataclass(frozen=True, slots=True)
class RulesOnlyRegisterPolicy:
    name: str = "rules-only-register"
    mode: PolicyMode = PolicyMode.RULES_ONLY
    reports_model_delta: bool = False
    model_version: int | None = None

    def decide_document(
        self,
        candidates: tuple[RegisterCandidate, ...],
        context: RegisterDocumentContext,
    ) -> tuple[RegisterDecision, ...]:
        _ = context
        return tuple(
            RegisterDecision(
                subject=candidate.candidate_id,
                outcome=DecisionOutcome.ACCEPT_RULES,
                label=candidate.rules.label,
                reason=candidate.rules.reason,
            )
            for candidate in candidates
        )


@dataclass(frozen=True, slots=True)
class ModelBackedRegisterPolicy:
    scorer: RegisterScorer
    name: str = "model-backed-register"
    mode: PolicyMode = PolicyMode.ARTIFACT_OPTIONAL_DIAGNOSTIC_TEMP
    reports_model_delta: bool = True

    @property
    def model_version(self) -> int:
        return self.scorer.version

    def decide_document(
        self,
        candidates: tuple[RegisterCandidate, ...],
        context: RegisterDocumentContext,
    ) -> tuple[RegisterDecision, ...]:
        if context.lang not in self.scorer.langs:
            return tuple(
                RegisterDecision(
                    subject=candidate.candidate_id,
                    outcome=DecisionOutcome.FALLBACK_TO_RULES,
                    label=candidate.rules.label,
                    reason=RegisterDecisionReason.UNSUPPORTED_LANGUAGE,
                    fallback_label=candidate.rules.label,
                )
                for candidate in candidates
            )
        return tuple(self._decide_candidate(candidate) for candidate in candidates)

    def _decide_candidate(self, candidate: RegisterCandidate) -> RegisterDecision:
        if not candidate.rules.model_allowed:
            return RegisterDecision(
                subject=candidate.candidate_id,
                outcome=DecisionOutcome.ACCEPT_RULES,
                label=candidate.rules.label,
                reason=candidate.rules.reason,
            )

        prediction = self.scorer.predict(candidate.observation)
        primary = prediction.primary
        if primary.score_kind is not ScoreKind.POSTERIOR:
            raise ValueError(
                f"register policy cannot threshold {primary.score_kind.value} scores"
            )
        score = primary.score
        if score is None:
            raise ValueError("register policy cannot threshold a missing score")
        model_label = primary.label if score >= self.scorer.threshold else DisplayRegisterLabel.ORDINARY
        diagnostics: tuple[IntentDiagnostic, ...] = ()
        reason = RegisterDecisionReason.MODEL_THRESHOLD
        if model_label is not candidate.rules.label:
            diagnostics = (
                IntentDiagnostic(
                    code=IntentDiagnosticCode.MODEL_RULES_DISAGREE,
                    severity=DiagnosticSeverity.INFO,
                    subject=candidate.candidate_id,
                    message=(
                        f"model chose {model_label.value}; "
                        f"rules chose {candidate.rules.label.value}"
                    ),
                    evidence={
                        "score": score,
                        "threshold": self.scorer.threshold,
                    },
                ),
            )
            reason = RegisterDecisionReason.MODEL_RULES_DISAGREE
        return RegisterDecision(
            subject=candidate.candidate_id,
            outcome=DecisionOutcome.ACCEPT_MODEL,
            label=model_label,
            reason=reason,
            prediction=prediction,
            diagnostics=diagnostics,
        )
