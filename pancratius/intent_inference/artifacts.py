"""Local production artifacts for import-time intent inference."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

from pancratius.intent_inference.policies import (
    ModelBackedRegisterPolicy,
    RegisterPolicy,
    RulesOnlyRegisterPolicy,
)
from pancratius.intent_inference.scorers.standardized_linear import (
    StandardizedLinearRegisterScorer,
    load_standardized_linear_register_scorer,
)
from pancratius.locales import Locale
from pancratius.paths import REPO_ROOT

REGISTER_MODEL_PATH = REPO_ROOT / "data" / "models" / "verse_register_v1.json"


@dataclass(frozen=True, slots=True)
class RegisterPolicyLoad:
    policy: RegisterPolicy
    missing_artifact: Path | None = None


@cache
def load_register_scorer(path: Path) -> StandardizedLinearRegisterScorer | None:
    return load_standardized_linear_register_scorer(path)


def load_register_policy_for(
    lang: Locale,
    *,
    path: Path | None = None,
) -> RegisterPolicyLoad:
    artifact_path = path if path is not None else REGISTER_MODEL_PATH
    scorer = load_register_scorer(artifact_path)
    if scorer is None:
        return RegisterPolicyLoad(
            policy=RulesOnlyRegisterPolicy(),
            missing_artifact=artifact_path if not artifact_path.exists() else None,
        )
    if lang not in scorer.langs:
        return RegisterPolicyLoad(policy=RulesOnlyRegisterPolicy())
    return RegisterPolicyLoad(policy=ModelBackedRegisterPolicy(scorer))
