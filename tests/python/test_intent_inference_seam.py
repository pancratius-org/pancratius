from __future__ import annotations

import ast
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from pancratius import ir
from pancratius.intent_inference.artifacts import (
    ArtifactFileRef,
    LanguageSupport,
    LocalArtifactRepository,
    RegisterArtifactManifest,
    compute_bundle_sha256,
    load_register_policy_for,
)
from pancratius.intent_inference.decisions import (
    ArtifactBundleHash,
    ArtifactBundleId,
    ArtifactId,
    ArtifactManifestSchema,
    ArtifactSchemaId,
    DecisionOutcome,
    DiagnosticSeverity,
    DisplayRegisterLabel,
    FeatureSetId,
    IntentDiagnosticCode,
    IntentTask,
    LabelScore,
    LabelSpaceId,
    ObservationSchemaId,
    Prediction,
    PredictorRef,
    RegisterDecisionReason,
    RelativeBundlePath,
    ScoreKind,
    ScorerFamily,
    Sha256Digest,
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
from pancratius.intent_inference.policies import ModelBackedRegisterPolicy, PolicyMode
from pancratius.intent_inference.scorers.registry import ScorerRegistry
from pancratius.intent_inference.scorers.standardized_linear import (
    FEATURE_NAMES,
    StandardizedLinearRegisterScorer,
    StandardizedLinearWeightsSchema,
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


def test_import_composition_uses_artifact_registry_not_concrete_scorer() -> None:
    import_paths = [
        ROOT / "pancratius" / "docx_conversion.py",
        ROOT / "pancratius" / "intent_inference" / "artifacts.py",
    ]

    for path in import_paths:
        imported = _imported_modules(path)
        assert "pancratius.intent_inference.scorers.standardized_linear" not in imported

    artifact_imports = _imported_modules(ROOT / "pancratius" / "intent_inference" / "artifacts.py")
    assert "pancratius.intent_inference.scorers.registry" in artifact_imports


def test_production_intent_inference_has_no_research_or_network_imports() -> None:
    forbidden_roots = {
        "google",
        "httpx",
        "intent_ai",
        "numpy",
        "openrouter",
        "pandas",
        "requests",
        "sklearn",
        "urllib",
    }
    paths = [
        *(ROOT / "pancratius" / "intent_inference").rglob("*.py"),
        ROOT / "pancratius" / "passes" / "register.py",
        ROOT / "pancratius" / "passes" / "context.py",
        ROOT / "pancratius" / "docx_conversion.py",
    ]
    for path in sorted(paths):
        imported_roots = {module.split(".", maxsplit=1)[0] for module in _imported_modules(path)}
        assert imported_roots.isdisjoint(forbidden_roots), path


_TEST_BUNDLE_ID = ArtifactBundleId("register/verse_register_v1")
_MODEL_CARD = "# Test register model\n\nRuntime test card.\n"


def _weights(features: tuple[str, ...] = FEATURE_NAMES) -> dict[str, object]:
    n = len(features)
    return {
        "schema": StandardizedLinearWeightsSchema.V1.value,
        "version": 1,
        "features": list(features),
        "mean": [0.0] * n,
        "std": [1.0] * n,
        "coef": [0.0] * n,
        "intercept": 0.0,
        "threshold": 0.6,
    }


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _manifest_dict(weights_sha: str, model_card_sha: str) -> dict[str, object]:
    manifest = RegisterArtifactManifest(
        schema=ArtifactManifestSchema.REGISTER_BUNDLE_V1,
        artifact_id=ArtifactId("verse-register-v1"),
        artifact_schema=ArtifactSchemaId.REGISTER_ARTIFACT_V1,
        bundle_id=_TEST_BUNDLE_ID,
        bundle_sha256=ArtifactBundleHash("0" * 64),
        task=IntentTask.DISPLAY_REGISTER,
        scorer_family=ScorerFamily.STANDARDIZED_LINEAR_V1,
        observation_schema=ObservationSchemaId.REGISTER_OBSERVATION_V1,
        label_space=LabelSpaceId.DISPLAY_REGISTER_LABELS_V1,
        feature_set=FeatureSetId.VERSE_REGISTER_FEATURES_V1,
        language_support=LanguageSupport(("ru",)),
        weights=ArtifactFileRef(
            path=RelativeBundlePath("weights.json"),
            sha256=Sha256Digest(weights_sha),
        ),
        model_card=ArtifactFileRef(
            path=RelativeBundlePath("model-card.md"),
            sha256=Sha256Digest(model_card_sha),
        ),
    )
    return {
        "schema": manifest.schema.value,
        "artifact_id": str(manifest.artifact_id),
        "artifact_schema": manifest.artifact_schema.value,
        "bundle_id": str(manifest.bundle_id),
        "bundle_sha256": str(compute_bundle_sha256(manifest)),
        "task": manifest.task.value,
        "scorer_family": manifest.scorer_family.value,
        "observation_schema": manifest.observation_schema.value,
        "label_space": manifest.label_space.value,
        "feature_set": manifest.feature_set.value,
        "language_support": {"locales": list(manifest.language_support.locales)},
        "files": {
            "weights": {
                "path": str(manifest.weights.path),
                "sha256": str(manifest.weights.sha256),
            },
            "model_card": {
                "path": str(manifest.model_card.path),
                "sha256": str(manifest.model_card.sha256),
            },
        },
    }


def _manifest_file_ref(manifest: dict[str, object], role: str) -> dict[str, object]:
    files = manifest["files"]
    assert isinstance(files, dict)
    typed_files = cast(dict[str, object], files)
    ref = typed_files[role]
    assert isinstance(ref, dict)
    return cast(dict[str, object], ref)


def _write_bundle(
    tmp_path: Path,
    *,
    weights: dict[str, object] | None = None,
    weights_text: str | None = None,
    mutate_manifest: Callable[[dict[str, object]], None] | None = None,
) -> LocalArtifactRepository:
    root = tmp_path / "models"
    bundle = root / "register" / "verse_register_v1"
    bundle.mkdir(parents=True, exist_ok=True)
    resolved_weights_text = (
        weights_text
        if weights_text is not None
        else json.dumps(weights or _weights(), ensure_ascii=False, indent=2) + "\n"
    )
    (bundle / "weights.json").write_text(resolved_weights_text, encoding="utf-8")
    (bundle / "model-card.md").write_text(_MODEL_CARD, encoding="utf-8")
    manifest = _manifest_dict(
        _sha256_text(resolved_weights_text),
        _sha256_text(_MODEL_CARD),
    )
    if mutate_manifest is not None:
        mutate_manifest(manifest)
    (bundle / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return LocalArtifactRepository(root)


def test_register_bundle_loader_accepts_valid_bundle(tmp_path: Path) -> None:
    repository = _write_bundle(tmp_path)

    bundle = repository.load_bundle(_TEST_BUNDLE_ID)
    assert bundle is not None
    assert bundle.manifest.artifact_id == ArtifactId("verse-register-v1")
    assert bundle.manifest.scorer_family is ScorerFamily.STANDARDIZED_LINEAR_V1

    loaded = load_register_policy_for("ru", repository=repository, bundle_id=_TEST_BUNDLE_ID)
    assert loaded.missing_artifact is None
    assert loaded.policy.mode is PolicyMode.ARTIFACT_OPTIONAL_DIAGNOSTIC_TEMP


def test_register_validated_bundle_rejects_feature_vector_drift(tmp_path: Path) -> None:
    bad_features = ("drifted", *FEATURE_NAMES[1:])
    repository = _write_bundle(tmp_path, weights=_weights(bad_features))

    with pytest.raises(RegisterArtifactError, match="feature schema drifted"):
        repository.load_validated_bundle(_TEST_BUNDLE_ID)


def test_register_bundle_loader_rejects_bundle_hash_mismatch(tmp_path: Path) -> None:
    def break_hash(manifest: dict[str, object]) -> None:
        _manifest_file_ref(manifest, "weights")["sha256"] = "0" * 64

    repository = _write_bundle(tmp_path, mutate_manifest=break_hash)

    with pytest.raises(RegisterArtifactError, match="bundle hash mismatch"):
        repository.load_bundle(_TEST_BUNDLE_ID)


def test_register_bundle_loader_rejects_file_hash_mismatch(tmp_path: Path) -> None:
    repository = _write_bundle(tmp_path)
    weights = tmp_path / "models" / "register" / "verse_register_v1" / "weights.json"
    weights.write_text(json.dumps(_weights(), ensure_ascii=False), encoding="utf-8")

    with pytest.raises(RegisterArtifactError, match="weights sha256 mismatch"):
        repository.load_bundle(_TEST_BUNDLE_ID)


def test_register_bundle_loader_rejects_missing_weights_file(tmp_path: Path) -> None:
    repository = _write_bundle(tmp_path)
    (tmp_path / "models" / "register" / "verse_register_v1" / "weights.json").unlink()

    with pytest.raises(RegisterArtifactError, match="weights file missing"):
        repository.load_bundle(_TEST_BUNDLE_ID)


def test_register_bundle_loader_rejects_missing_model_card(tmp_path: Path) -> None:
    repository = _write_bundle(tmp_path)
    (tmp_path / "models" / "register" / "verse_register_v1" / "model-card.md").unlink()

    with pytest.raises(RegisterArtifactError, match="model_card file missing"):
        repository.load_bundle(_TEST_BUNDLE_ID)


def test_register_bundle_loader_rejects_path_traversal(tmp_path: Path) -> None:
    def traverse(manifest: dict[str, object]) -> None:
        _manifest_file_ref(manifest, "weights")["path"] = "../weights.json"

    repository = _write_bundle(tmp_path, mutate_manifest=traverse)

    with pytest.raises(RegisterArtifactError, match="traverse outside"):
        repository.load_bundle(_TEST_BUNDLE_ID)


def test_register_bundle_loader_rejects_bundle_id_traversal(tmp_path: Path) -> None:
    repository = _write_bundle(tmp_path)

    with pytest.raises(RegisterArtifactError, match="traverse outside"):
        repository.load_bundle(ArtifactBundleId("../verse_register_v1"))


def test_register_bundle_loader_rejects_absolute_bundle_id(tmp_path: Path) -> None:
    repository = _write_bundle(tmp_path)

    with pytest.raises(RegisterArtifactError, match="relative bundle path"):
        repository.load_bundle(ArtifactBundleId("/tmp/verse_register_v1"))


def test_register_bundle_loader_rejects_unknown_manifest_schema(tmp_path: Path) -> None:
    def unknown_schema(manifest: dict[str, object]) -> None:
        manifest["schema"] = "pancratius.register_bundle_manifest.v9"

    repository = _write_bundle(tmp_path, mutate_manifest=unknown_schema)

    with pytest.raises(RegisterArtifactError, match="unknown schema"):
        repository.load_bundle(_TEST_BUNDLE_ID)


def test_register_bundle_loader_rejects_unknown_manifest_fields(tmp_path: Path) -> None:
    def extra_field(manifest: dict[str, object]) -> None:
        manifest["promoted_at"] = "2026-06-25"

    repository = _write_bundle(tmp_path, mutate_manifest=extra_field)

    with pytest.raises(RegisterArtifactError, match="unknown artifact manifest field"):
        repository.load_bundle(_TEST_BUNDLE_ID)


def test_register_bundle_loader_rejects_duplicate_manifest_keys(tmp_path: Path) -> None:
    repository = _write_bundle(tmp_path)
    manifest_path = tmp_path / "models" / "register" / "verse_register_v1" / "manifest.json"
    manifest_path.write_text(
        '{"schema":"pancratius.register_bundle_manifest.v1","schema":"x"}',
        encoding="utf-8",
    )

    with pytest.raises(RegisterArtifactError, match="duplicate JSON key"):
        repository.load_bundle(_TEST_BUNDLE_ID)


def test_register_bundle_loader_rejects_malformed_manifest_json(tmp_path: Path) -> None:
    repository = _write_bundle(tmp_path)
    manifest_path = tmp_path / "models" / "register" / "verse_register_v1" / "manifest.json"
    manifest_path.write_text('{"schema":', encoding="utf-8")

    with pytest.raises(RegisterArtifactError, match="malformed artifact manifest JSON"):
        repository.load_bundle(_TEST_BUNDLE_ID)


def test_register_bundle_loader_rejects_unknown_scorer_family(tmp_path: Path) -> None:
    def unknown_family(manifest: dict[str, object]) -> None:
        manifest["scorer_family"] = "tree_ensemble.v1"

    repository = _write_bundle(tmp_path, mutate_manifest=unknown_family)

    with pytest.raises(RegisterArtifactError, match="unknown scorer_family"):
        repository.load_bundle(_TEST_BUNDLE_ID)


def test_scorer_registry_rejects_known_family_without_registered_loader(tmp_path: Path) -> None:
    repository = _write_bundle(tmp_path)

    with pytest.raises(RegisterArtifactError, match="no scorer registered"):
        load_register_policy_for(
            "ru",
            repository=repository,
            bundle_id=_TEST_BUNDLE_ID,
            scorer_registry=ScorerRegistry({}),
        )


def test_register_bundle_loader_rejects_malformed_weights_json(tmp_path: Path) -> None:
    bad_json = '{"schema": "pancratius.standardized_linear.weights.v1",'
    repository = _write_bundle(tmp_path, weights_text=bad_json)

    with pytest.raises(RegisterArtifactError, match="malformed weights JSON"):
        repository.load_bundle(_TEST_BUNDLE_ID)


def test_register_bundle_loader_rejects_huge_json_integers(tmp_path: Path) -> None:
    weights_text = json.dumps(_weights(), ensure_ascii=False).replace(
        '"intercept": 0.0',
        '"intercept": ' + ("9" * 400),
    )
    repository = _write_bundle(tmp_path, weights_text=weights_text)

    with pytest.raises(RegisterArtifactError, match="non-finite JSON integer"):
        repository.load_bundle(_TEST_BUNDLE_ID)


def test_register_bundle_loader_rejects_non_finite_numbers(tmp_path: Path) -> None:
    weights = dict(_weights())
    weights_text = json.dumps(weights, ensure_ascii=False).replace(
        '"intercept": 0.0',
        '"intercept": 1e999',
    )
    repository = _write_bundle(tmp_path, weights_text=weights_text)

    with pytest.raises(RegisterArtifactError, match="non-finite JSON number"):
        repository.load_bundle(_TEST_BUNDLE_ID)


def test_register_bundle_loader_rejects_unknown_language_support(tmp_path: Path) -> None:
    def unknown_locale(manifest: dict[str, object]) -> None:
        manifest["language_support"] = {"locales": ["de"]}

    repository = _write_bundle(tmp_path, mutate_manifest=unknown_locale)

    with pytest.raises(RegisterArtifactError, match="known locales"):
        repository.load_bundle(_TEST_BUNDLE_ID)


def test_register_bundle_rejects_unsupported_feature_set(tmp_path: Path) -> None:
    def lineation_features(manifest: dict[str, object]) -> None:
        manifest["feature_set"] = FeatureSetId.LINEATION_FEATURES_V1.value

    repository = _write_bundle(tmp_path, mutate_manifest=lineation_features)

    with pytest.raises(RegisterArtifactError, match="unsupported feature set"):
        repository.load_bundle(_TEST_BUNDLE_ID)


def test_standardized_linear_registry_rejects_feature_vector_drift(tmp_path: Path) -> None:
    bad_features = ("drifted", *FEATURE_NAMES[1:])
    repository = _write_bundle(tmp_path, weights=_weights(bad_features))

    with pytest.raises(RegisterArtifactError, match="feature schema drifted"):
        load_register_policy_for("ru", repository=repository, bundle_id=_TEST_BUNDLE_ID)


def test_register_bundle_missing_artifact_is_optional(tmp_path: Path) -> None:
    loaded = load_register_policy_for(
        "ru",
        repository=LocalArtifactRepository(tmp_path / "models"),
        bundle_id=_TEST_BUNDLE_ID,
    )

    assert loaded.policy.mode is PolicyMode.RULES_ONLY
    assert loaded.missing_artifact == tmp_path / "models" / "register" / "verse_register_v1"


def test_register_bundle_unsupported_language_uses_rules_policy(tmp_path: Path) -> None:
    repository = _write_bundle(tmp_path)

    loaded = load_register_policy_for("en", repository=repository, bundle_id=_TEST_BUNDLE_ID)

    assert loaded.policy.mode is PolicyMode.RULES_ONLY
    assert loaded.missing_artifact is None


def test_unsupported_language_does_not_parse_corrupted_weights(tmp_path: Path) -> None:
    repository = _write_bundle(tmp_path)
    weights = tmp_path / "models" / "register" / "verse_register_v1" / "weights.json"
    weights.write_text("{not json", encoding="utf-8")

    loaded = load_register_policy_for("en", repository=repository, bundle_id=_TEST_BUNDLE_ID)

    assert loaded.policy.mode is PolicyMode.RULES_ONLY
    assert loaded.missing_artifact is None


def test_register_scorer_cache_is_keyed_by_bundle_hash(tmp_path: Path) -> None:
    first_weights = _weights()
    first_weights["version"] = 1
    repository = _write_bundle(tmp_path, weights=first_weights)
    first = load_register_policy_for("ru", repository=repository, bundle_id=_TEST_BUNDLE_ID)

    second_weights = _weights()
    second_weights["version"] = 2
    second_weights["intercept"] = 3.0
    _write_bundle(tmp_path, weights=second_weights)
    second = load_register_policy_for("ru", repository=repository, bundle_id=_TEST_BUNDLE_ID)

    assert first.policy.model_version == 1
    assert second.policy.model_version == 2


_TEST_PREDICTOR = PredictorRef(
    task=IntentTask.DISPLAY_REGISTER,
    artifact_id=ArtifactId("test-register-model"),
    artifact_schema=ArtifactSchemaId.REGISTER_ARTIFACT_V1,
    observation_schema=ObservationSchemaId.REGISTER_OBSERVATION_V1,
    label_space=LabelSpaceId.DISPLAY_REGISTER_LABELS_V1,
    scorer_family=ScorerFamily.STANDARDIZED_LINEAR_V1,
    feature_set=FeatureSetId.VERSE_REGISTER_FEATURES_V1,
)


def _scorer(*, bias: float, langs: tuple[Locale, ...] = ("ru",)) -> StandardizedLinearRegisterScorer:
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


def test_standardized_linear_probability_saturates_for_extreme_finite_scores() -> None:
    low = _scorer(bias=-1_000.0)
    high = _scorer(bias=1_000.0)
    observation = _candidate().observation

    assert low.probability(observation) == 0.0
    assert high.probability(observation) == 1.0


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
        langs: tuple[Locale, ...] = ("ru",)

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
