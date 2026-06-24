from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from pancratius import ir
from pancratius.intent_inference.decisions import (
    ArtifactId,
    DecisionOutcome,
    DiagnosticSeverity,
    DisplayRegisterLabel,
    FeatureSetId,
    IntentDiagnosticCode,
    IntentTask,
    LabelScore,
    Prediction,
    PredictorRef,
    RegisterDecisionReason,
    SchemaId,
    ScoreKind,
    ScorerFamily,
)
from pancratius.intent_inference.errors import RegisterArtifactError
from pancratius.intent_inference.observations import (
    RegisterBookStats,
    RegisterCandidate,
    RegisterDocumentContext,
    RegisterModelContext,
    RegisterObservation,
    RegisterRuleEvaluation,
    stable_register_candidate_id,
    stanza_line_counts,
)
from pancratius.intent_inference.policies import ModelBackedRegisterPolicy
from pancratius.intent_inference.scorers.standardized_linear import (
    FEATURE_NAMES,
    StandardizedLinearRegisterScorer,
    load_standardized_linear_register_scorer,
)
from pancratius.locales import Locale

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_SPAN = ir.SourceSpan(10, 12)


def _lineated(source_span: ir.SourceSpan | None = DEFAULT_SOURCE_SPAN) -> ir.LineatedBlock:
    return ir.LineatedBlock(
        stanzas=[[
            ir.Line([ir.Text("Свет мой тихий,")]),
            ir.Line([ir.Text("в сердце горит.")]),
        ]],
        source_span=source_span,
    )


def test_register_candidate_id_is_replay_stable_for_equivalent_ir() -> None:
    left = _lineated()
    right = _lineated()

    assert stable_register_candidate_id(
        left,
        source_block_index=3,
        candidate_ordinal=1,
    ) == stable_register_candidate_id(
        right,
        source_block_index=3,
        candidate_ordinal=1,
    )


def test_register_candidate_id_prefers_source_span_over_traversal_position() -> None:
    block = _lineated()

    assert stable_register_candidate_id(
        block,
        source_block_index=3,
        candidate_ordinal=1,
    ) == stable_register_candidate_id(
        block,
        source_block_index=99,
        candidate_ordinal=42,
    )


def test_register_candidate_id_uses_spanless_content_fingerprint() -> None:
    left = _lineated(source_span=None)
    right = _lineated(source_span=None)

    assert stable_register_candidate_id(
        left,
        source_block_index=3,
        candidate_ordinal=1,
    ) == stable_register_candidate_id(
        right,
        source_block_index=3,
        candidate_ordinal=1,
    )
    assert stable_register_candidate_id(
        left,
        source_block_index=4,
        candidate_ordinal=1,
    ) != stable_register_candidate_id(
        right,
        source_block_index=3,
        candidate_ordinal=1,
    )


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def _defined_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def test_register_pass_does_not_own_model_family_code() -> None:
    path = ROOT / "pancratius" / "passes" / "register.py"
    imported = _imported_modules(path)
    defined = _defined_names(path)

    assert "json" not in imported
    assert "math" not in imported
    assert "pancratius.intent_inference.scorers" not in imported
    assert "FEATURE_NAMES" not in defined
    assert "RegisterModel" not in defined
    assert "load_register_model" not in defined


def test_production_intent_inference_has_no_research_or_network_imports() -> None:
    forbidden_roots = {"intent_ai", "openrouter", "requests", "httpx", "urllib", "google"}
    paths = [
        *(ROOT / "pancratius" / "intent_inference").rglob("*.py"),
        ROOT / "pancratius" / "passes" / "register.py",
        ROOT / "pancratius" / "passes" / "context.py",
        ROOT / "pancratius" / "docx_conversion.py",
    ]
    for path in sorted(paths):
        imported_roots = {module.split(".", maxsplit=1)[0] for module in _imported_modules(path)}
        assert imported_roots.isdisjoint(forbidden_roots), path


def _artifact(features: tuple[str, ...] = FEATURE_NAMES) -> dict[str, object]:
    n = len(features)
    return {
        "version": 1,
        "langs": ["ru"],
        "features": list(features),
        "mean": [0.0] * n,
        "std": [1.0] * n,
        "coef": [0.0] * n,
        "intercept": 0.0,
        "threshold": 0.6,
    }


def test_standardized_linear_loader_rejects_feature_drift(tmp_path: Path) -> None:
    path = tmp_path / "model.json"
    bad_features = ("drifted", *FEATURE_NAMES[1:])
    path.write_text(json.dumps(_artifact(bad_features)), encoding="utf-8")

    with pytest.raises(RegisterArtifactError, match="feature schema drifted"):
        load_standardized_linear_register_scorer(path)


def test_standardized_linear_loader_rejects_malformed_language_support(tmp_path: Path) -> None:
    path = tmp_path / "model.json"
    artifact = _artifact()
    artifact["langs"] = "ru"
    path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(RegisterArtifactError, match="langs"):
        load_standardized_linear_register_scorer(path)


def test_standardized_linear_loader_rejects_non_finite_numbers(tmp_path: Path) -> None:
    path = tmp_path / "model.json"
    artifact = _artifact()
    artifact["intercept"] = float("inf")
    path.write_text(json.dumps(artifact), encoding="utf-8")

    with pytest.raises(RegisterArtifactError, match="finite"):
        load_standardized_linear_register_scorer(path)


def test_standardized_linear_loader_missing_artifact_is_optional(tmp_path: Path) -> None:
    assert load_standardized_linear_register_scorer(tmp_path / "missing.json") is None


_TEST_PREDICTOR = PredictorRef(
    task=IntentTask.DISPLAY_REGISTER,
    artifact_id=ArtifactId("test-register-model"),
    artifact_schema=SchemaId.REGISTER_ARTIFACT_V1,
    observation_schema=SchemaId.REGISTER_OBSERVATION_V1,
    label_space=SchemaId.DISPLAY_REGISTER_LABELS_V1,
    scorer_family=ScorerFamily.STANDARDIZED_LINEAR_V1,
    feature_set=FeatureSetId.VERSE_REGISTER_FEATURES_V1,
)


def _scorer(*, bias: float, langs: tuple[str, ...] = ("ru",)) -> StandardizedLinearRegisterScorer:
    n = len(FEATURE_NAMES)
    return StandardizedLinearRegisterScorer(
        version=7,
        langs=langs,
        features=FEATURE_NAMES,
        mean=(0.0,) * n,
        std=(1.0,) * n,
        coef=(0.0,) * n,
        intercept=bias,
        threshold=0.6,
        predictor_ref=_TEST_PREDICTOR,
    )


def _candidate(
    *,
    lang: Locale = "ru",
    rules_label: DisplayRegisterLabel = DisplayRegisterLabel.ORDINARY,
    model_allowed: bool = True,
) -> RegisterCandidate:
    block = _lineated()
    return RegisterCandidate(
        candidate_id=stable_register_candidate_id(
            block,
            source_block_index=1,
            candidate_ordinal=0,
        ),
        source_block_index=1,
        source_span=block.source_span,
        observation=RegisterObservation(
            candidate_id=stable_register_candidate_id(
                block,
                source_block_index=1,
                candidate_ordinal=0,
            ),
            lang=lang,
            lines=("Свет мой тихий,", "в сердце горит."),
            stanza_line_counts=stanza_line_counts(block.stanzas),
            evidence=ir.LineationEvidence(),
            model_context=RegisterModelContext(),
            book=RegisterBookStats(mean_para_len=80.0, lineated_frac=0.2),
        ),
        rules=RegisterRuleEvaluation(
            label=rules_label,
            reason=RegisterDecisionReason.RULES,
            model_allowed=model_allowed,
        ),
    )


def test_model_policy_records_candidate_level_disagreement_diagnostic() -> None:
    policy = ModelBackedRegisterPolicy(_scorer(bias=3.0))

    (decision,) = policy.decide_document(
        (_candidate(rules_label=DisplayRegisterLabel.ORDINARY),),
        RegisterDocumentContext(lang="ru"),
    )

    assert decision.outcome is DecisionOutcome.ACCEPT_MODEL
    assert decision.label is DisplayRegisterLabel.VERSE
    assert decision.reason is RegisterDecisionReason.MODEL_RULES_DISAGREE
    assert len(decision.diagnostics) == 1
    diagnostic = decision.diagnostics[0]
    assert diagnostic.code is IntentDiagnosticCode.MODEL_RULES_DISAGREE
    assert diagnostic.severity is DiagnosticSeverity.INFO
    assert diagnostic.evidence["threshold"] == 0.6


def test_model_policy_unsupported_language_falls_back_to_rules() -> None:
    policy = ModelBackedRegisterPolicy(_scorer(bias=3.0, langs=("ru",)))

    (decision,) = policy.decide_document(
        (_candidate(lang="en", rules_label=DisplayRegisterLabel.ORDINARY),),
        RegisterDocumentContext(lang="en"),
    )

    assert decision.outcome is DecisionOutcome.FALLBACK_TO_RULES
    assert decision.label is DisplayRegisterLabel.ORDINARY
    assert decision.reason is RegisterDecisionReason.UNSUPPORTED_LANGUAGE
    assert decision.fallback_label is DisplayRegisterLabel.ORDINARY


def test_model_policy_thresholds_the_prediction_label_not_an_implicit_verse_score() -> None:
    class OrdinaryScorer:
        version: int = 3
        threshold: float = 0.6
        langs: tuple[str, ...] = ("ru",)

        def predict(self, observation: RegisterObservation) -> Prediction:
            _ = observation
            return Prediction(
                primary=LabelScore(
                    label=DisplayRegisterLabel.ORDINARY,
                    score=0.99,
                    score_kind=ScoreKind.POSTERIOR,
                    predictor=_TEST_PREDICTOR,
                )
            )

    policy = ModelBackedRegisterPolicy(OrdinaryScorer())

    (decision,) = policy.decide_document(
        (_candidate(rules_label=DisplayRegisterLabel.VERSE),),
        RegisterDocumentContext(lang="ru"),
    )

    assert decision.label is DisplayRegisterLabel.ORDINARY
