"""Local production artifacts for import-time intent inference."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from pathlib import Path, PurePosixPath
from typing import NoReturn, cast

from pancratius.intent_inference.decisions import (
    ArtifactBundleHash,
    ArtifactBundleId,
    ArtifactId,
    ArtifactManifestSchema,
    ArtifactSchemaId,
    FeatureSetId,
    IntentTask,
    LabelSpaceId,
    ObservationSchemaId,
    RelativeBundlePath,
    ScorerFamily,
    Sha256Digest,
)
from pancratius.intent_inference.errors import RegisterArtifactError
from pancratius.intent_inference.policies import (
    ModelBackedRegisterPolicy,
    RegisterPolicy,
    RegisterScorer,
    RulesOnlyRegisterPolicy,
)
from pancratius.intent_inference.scorers.registry import DEFAULT_SCORER_REGISTRY, ScorerRegistry
from pancratius.locales import LOCALES, Locale, is_locale
from pancratius.paths import REPO_ROOT

ARTIFACT_ROOT = REPO_ROOT / "data" / "models"
DEFAULT_REGISTER_BUNDLE_ID = ArtifactBundleId("register/verse_register_v1")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_FIELDS = frozenset({
    "schema",
    "artifact_id",
    "artifact_schema",
    "bundle_id",
    "bundle_sha256",
    "task",
    "scorer_family",
    "observation_schema",
    "label_space",
    "feature_set",
    "language_support",
    "files",
})
_LANGUAGE_SUPPORT_FIELDS = frozenset({"locales"})
_FILES_FIELDS = frozenset({"weights", "model_card"})
_FILE_REF_FIELDS = frozenset({"path", "sha256"})


@dataclass(frozen=True, slots=True)
class ArtifactFileRef:
    path: RelativeBundlePath
    sha256: Sha256Digest


@dataclass(frozen=True, slots=True)
class LanguageSupport:
    locales: tuple[Locale, ...]

    def supports(self, lang: Locale) -> bool:
        return lang in self.locales


@dataclass(frozen=True, slots=True)
class RegisterArtifactManifest:
    schema: ArtifactManifestSchema
    artifact_id: ArtifactId
    artifact_schema: ArtifactSchemaId
    bundle_id: ArtifactBundleId
    bundle_sha256: ArtifactBundleHash
    task: IntentTask
    scorer_family: ScorerFamily
    observation_schema: ObservationSchemaId
    label_space: LabelSpaceId
    feature_set: FeatureSetId
    language_support: LanguageSupport
    weights: ArtifactFileRef
    model_card: ArtifactFileRef


@dataclass(frozen=True, slots=True)
class ArtifactBundle:
    root: Path
    manifest_path: Path
    manifest: RegisterArtifactManifest
    weights: Mapping[str, object]
    model_card: str


@dataclass(frozen=True, slots=True)
class ValidatedArtifactBundle:
    bundle: ArtifactBundle
    scorer: RegisterScorer


@dataclass(frozen=True, slots=True)
class RegisterPolicyLoad:
    policy: RegisterPolicy
    missing_artifact: Path | None = None


@dataclass(frozen=True, slots=True)
class LocalArtifactRepository:
    root: Path = ARTIFACT_ROOT

    def bundle_root(self, bundle_id: ArtifactBundleId) -> Path:
        rel = _parse_relative_bundle_path(str(bundle_id), "bundle_id")
        if "latest" in PurePosixPath(str(rel)).parts:
            raise RegisterArtifactError("artifact bundle id must be pinned, not latest")
        return _resolve_relative_path(self.root, rel, "bundle_id")

    def load_manifest(self, bundle_id: ArtifactBundleId) -> RegisterArtifactManifest | None:
        bundle_root = self.bundle_root(bundle_id)
        if not bundle_root.exists():
            return None
        if not bundle_root.is_dir():
            raise RegisterArtifactError(f"artifact bundle {bundle_root} is not a directory")
        manifest_path = bundle_root / "manifest.json"
        if not manifest_path.is_file():
            raise RegisterArtifactError(f"artifact bundle {bundle_root} has no manifest.json")
        manifest = _parse_manifest(_load_json_object(manifest_path, "artifact manifest"))
        if manifest.bundle_id != bundle_id:
            raise RegisterArtifactError(
                f"artifact manifest bundle_id {manifest.bundle_id!r} does not match {bundle_id!r}"
            )
        return manifest

    def load_bundle(self, bundle_id: ArtifactBundleId) -> ArtifactBundle | None:
        bundle_root = self.bundle_root(bundle_id)
        manifest_path = bundle_root / "manifest.json"
        manifest = self.load_manifest(bundle_id)
        if manifest is None:
            return None

        weights_path = _resolve_relative_path(bundle_root, manifest.weights.path, "weights.path")
        model_card_path = _resolve_relative_path(
            bundle_root, manifest.model_card.path, "model_card.path"
        )
        _validate_file_hash(weights_path, manifest.weights.sha256, "weights")
        _validate_file_hash(model_card_path, manifest.model_card.sha256, "model_card")

        weights = _load_json_object(weights_path, "weights")
        try:
            model_card = model_card_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RegisterArtifactError(f"cannot read model_card {model_card_path}: {exc}") from exc
        if not model_card.strip():
            raise RegisterArtifactError(f"model_card {model_card_path} must not be empty")

        return ArtifactBundle(
            root=bundle_root,
            manifest_path=manifest_path,
            manifest=manifest,
            weights=weights,
            model_card=model_card,
        )

    def load_validated_bundle(
        self,
        bundle_id: ArtifactBundleId,
        scorer_registry: ScorerRegistry = DEFAULT_SCORER_REGISTRY,
    ) -> ValidatedArtifactBundle | None:
        bundle = self.load_bundle(bundle_id)
        if bundle is None:
            return None
        return ValidatedArtifactBundle(
            bundle=bundle,
            scorer=scorer_registry.load(bundle),
        )


def compute_bundle_sha256(manifest: RegisterArtifactManifest) -> ArtifactBundleHash:
    payload = {
        "schema": manifest.schema.value,
        "artifact_id": str(manifest.artifact_id),
        "artifact_schema": manifest.artifact_schema.value,
        "bundle_id": str(manifest.bundle_id),
        "task": manifest.task.value,
        "scorer_family": manifest.scorer_family.value,
        "observation_schema": manifest.observation_schema.value,
        "label_space": manifest.label_space.value,
        "feature_set": manifest.feature_set.value,
        "language_support": {
            "locales": list(manifest.language_support.locales),
        },
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
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return ArtifactBundleHash(hashlib.sha256(encoded).hexdigest())


@cache
def load_register_scorer(
    root: Path,
    bundle_id: ArtifactBundleId,
    bundle_sha256: ArtifactBundleHash,
) -> RegisterScorer | None:
    repository = LocalArtifactRepository(root)
    bundle = repository.load_bundle(bundle_id)
    if bundle is None:
        return None
    if bundle.manifest.bundle_sha256 != bundle_sha256:
        raise RegisterArtifactError(
            f"artifact bundle hash changed while loading {bundle_id!r}"
        )
    return DEFAULT_SCORER_REGISTRY.load(bundle)


def load_register_policy_for(
    lang: Locale,
    *,
    repository: LocalArtifactRepository | None = None,
    bundle_id: ArtifactBundleId | None = None,
    scorer_registry: ScorerRegistry = DEFAULT_SCORER_REGISTRY,
) -> RegisterPolicyLoad:
    artifact_repository = repository or LocalArtifactRepository(ARTIFACT_ROOT)
    resolved_bundle_id = bundle_id or DEFAULT_REGISTER_BUNDLE_ID
    manifest = artifact_repository.load_manifest(resolved_bundle_id)
    if manifest is None:
        missing = artifact_repository.bundle_root(resolved_bundle_id)
        return RegisterPolicyLoad(policy=RulesOnlyRegisterPolicy(), missing_artifact=missing)
    if not manifest.language_support.supports(lang):
        return RegisterPolicyLoad(policy=RulesOnlyRegisterPolicy())

    if scorer_registry is DEFAULT_SCORER_REGISTRY:
        scorer = load_register_scorer(
            artifact_repository.root,
            resolved_bundle_id,
            manifest.bundle_sha256,
        )
    else:
        validated = artifact_repository.load_validated_bundle(resolved_bundle_id, scorer_registry)
        scorer = None if validated is None else validated.scorer

    if scorer is None:
        return RegisterPolicyLoad(policy=RulesOnlyRegisterPolicy())
    if lang not in scorer.langs:
        return RegisterPolicyLoad(policy=RulesOnlyRegisterPolicy())
    return RegisterPolicyLoad(policy=ModelBackedRegisterPolicy(scorer))


def _parse_manifest(raw: Mapping[str, object]) -> RegisterArtifactManifest:
    _reject_unknown_fields(raw, _MANIFEST_FIELDS, "artifact manifest")
    manifest = RegisterArtifactManifest(
        schema=_enum_field(raw, "schema", ArtifactManifestSchema),
        artifact_id=ArtifactId(_non_empty_string(raw, "artifact_id")),
        artifact_schema=_enum_field(raw, "artifact_schema", ArtifactSchemaId),
        bundle_id=ArtifactBundleId(_non_empty_string(raw, "bundle_id")),
        bundle_sha256=ArtifactBundleHash(_sha256_field(raw, "bundle_sha256")),
        task=_enum_field(raw, "task", IntentTask),
        scorer_family=_enum_field(raw, "scorer_family", ScorerFamily),
        observation_schema=_enum_field(raw, "observation_schema", ObservationSchemaId),
        label_space=_enum_field(raw, "label_space", LabelSpaceId),
        feature_set=_enum_field(raw, "feature_set", FeatureSetId),
        language_support=_parse_language_support(raw.get("language_support")),
        weights=_parse_file_ref(raw.get("files"), "weights"),
        model_card=_parse_file_ref(raw.get("files"), "model_card"),
    )
    _validate_manifest_semantics(manifest)
    expected = compute_bundle_sha256(manifest)
    if manifest.bundle_sha256 != expected:
        raise RegisterArtifactError(
            f"artifact bundle hash mismatch: expected {expected}, got {manifest.bundle_sha256}"
        )
    return manifest


def _validate_manifest_semantics(manifest: RegisterArtifactManifest) -> None:
    if manifest.schema is not ArtifactManifestSchema.REGISTER_BUNDLE_V1:
        raise RegisterArtifactError(f"unknown artifact manifest schema {manifest.schema.value!r}")
    if manifest.artifact_schema is not ArtifactSchemaId.REGISTER_ARTIFACT_V1:
        raise RegisterArtifactError(f"unsupported register artifact schema {manifest.artifact_schema}")
    if manifest.task is not IntentTask.DISPLAY_REGISTER:
        raise RegisterArtifactError(f"unsupported intent task {manifest.task.value!r}")
    if manifest.observation_schema is not ObservationSchemaId.REGISTER_OBSERVATION_V1:
        raise RegisterArtifactError(
            f"unsupported observation schema {manifest.observation_schema.value!r}"
        )
    if manifest.label_space is not LabelSpaceId.DISPLAY_REGISTER_LABELS_V1:
        raise RegisterArtifactError(f"unsupported label space {manifest.label_space.value!r}")
    if manifest.feature_set is not FeatureSetId.VERSE_REGISTER_FEATURES_V1:
        raise RegisterArtifactError(f"unsupported feature set {manifest.feature_set.value!r}")


def _parse_file_ref(raw_files: object, role: str) -> ArtifactFileRef:
    files = _mapping(raw_files, "files")
    _reject_unknown_fields(files, _FILES_FIELDS, "files")
    raw_ref = _mapping(files.get(role), f"files.{role}")
    _reject_unknown_fields(raw_ref, _FILE_REF_FIELDS, f"files.{role}")
    return ArtifactFileRef(
        path=_parse_relative_bundle_path(_non_empty_string(raw_ref, "path"), f"{role}.path"),
        sha256=Sha256Digest(_sha256_field(raw_ref, "sha256")),
    )


def _parse_language_support(raw: object) -> LanguageSupport:
    support = _mapping(raw, "language_support")
    _reject_unknown_fields(support, _LANGUAGE_SUPPORT_FIELDS, "language_support")
    raw_locales = support.get("locales")
    if not isinstance(raw_locales, list) or not raw_locales:
        raise RegisterArtifactError("language_support.locales must be a non-empty list")
    locales: list[Locale] = []
    for item in raw_locales:
        if not isinstance(item, str) or not is_locale(item):
            raise RegisterArtifactError(
                f"language_support.locales must use known locales {LOCALES}; got {item!r}"
            )
        if item in locales:
            raise RegisterArtifactError(f"duplicate language support locale {item!r}")
        locales.append(item)
    return LanguageSupport(tuple(locales))


def _parse_relative_bundle_path(raw: str, field: str) -> RelativeBundlePath:
    if "\\" in raw:
        raise RegisterArtifactError(f"{field} must be a POSIX relative path")
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.parts:
        raise RegisterArtifactError(f"{field} must be a relative bundle path")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise RegisterArtifactError(f"{field} must not traverse outside the bundle")
    return RelativeBundlePath(path.as_posix())


def _resolve_relative_path(root: Path, rel: RelativeBundlePath, field: str) -> Path:
    base = root.resolve()
    target = (base / str(rel)).resolve()
    if target != base and base not in target.parents:
        raise RegisterArtifactError(f"{field} resolves outside the artifact bundle")
    return target


def _validate_file_hash(path: Path, expected: Sha256Digest, role: str) -> None:
    if not path.is_file():
        raise RegisterArtifactError(f"artifact {role} file missing: {path}")
    actual = Sha256Digest(_sha256_file(path))
    if actual != expected:
        raise RegisterArtifactError(
            f"artifact {role} sha256 mismatch: expected {expected}, got {actual}"
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise RegisterArtifactError(f"cannot read artifact file {path}: {exc}") from exc
    return digest.hexdigest()


def _load_json_object(path: Path, role: str) -> Mapping[str, object]:
    try:
        raw = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_json_object_pairs,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
            parse_int=_finite_json_int,
        )
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        raise RegisterArtifactError(f"malformed {role} JSON {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RegisterArtifactError(f"{role} JSON root must be an object: {path}")
    return raw


def _finite_json_float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"non-finite JSON number {raw!r}")
    return value


def _finite_json_int(raw: str) -> int:
    value = int(raw)
    try:
        finite_as_float = math.isfinite(float(value))
    except OverflowError:
        finite_as_float = False
    if not finite_as_float:
        raise ValueError(f"non-finite JSON integer {raw[:32]!r}")
    return value


def _reject_json_constant(raw: str) -> NoReturn:
    raise ValueError(f"non-finite JSON number {raw!r}")


def _json_object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate JSON key {key!r}")
        out[key] = value
    return out


def _mapping(raw: object, field: str) -> Mapping[str, object]:
    if not isinstance(raw, dict):
        raise RegisterArtifactError(f"field {field!r} must be an object")
    return cast(Mapping[str, object], raw)


def _reject_unknown_fields(raw: Mapping[str, object], allowed: frozenset[str], field: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise RegisterArtifactError(f"unknown {field} field {unknown[0]!r}")


def _non_empty_string(raw: Mapping[str, object], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RegisterArtifactError(f"field {field!r} must be a non-empty string")
    return value


def _sha256_field(raw: Mapping[str, object], field: str) -> str:
    value = _non_empty_string(raw, field).lower()
    if not _SHA256_RE.match(value):
        raise RegisterArtifactError(f"field {field!r} must be a sha256 hex digest")
    return value


def _enum_field[T: StrEnum](
    raw: Mapping[str, object],
    field: str,
    enum_type: type[T],
) -> T:
    value = raw.get(field)
    if not isinstance(value, str):
        raise RegisterArtifactError(f"field {field!r} must be a string")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise RegisterArtifactError(f"unknown {field} {value!r}") from exc
