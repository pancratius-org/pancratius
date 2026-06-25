"""Registry for approved production intent scorers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from pancratius.intent_inference.decisions import ScorerFamily
from pancratius.intent_inference.errors import RegisterArtifactError
from pancratius.intent_inference.policies import RegisterScorer
from pancratius.intent_inference.scorers.standardized_linear import (
    load_standardized_linear_register_scorer,
)

if TYPE_CHECKING:
    from pancratius.intent_inference.artifacts import ArtifactBundle


class RegisterScorerLoader(Protocol):
    def __call__(self, bundle: ArtifactBundle) -> RegisterScorer:
        """Load a scorer from a validated artifact bundle."""


@dataclass(frozen=True, slots=True)
class ScorerRegistry:
    loaders: Mapping[ScorerFamily, RegisterScorerLoader]

    def __post_init__(self) -> None:
        object.__setattr__(self, "loaders", MappingProxyType(dict(self.loaders)))

    def load(self, bundle: ArtifactBundle) -> RegisterScorer:
        try:
            loader = self.loaders[bundle.manifest.scorer_family]
        except KeyError as exc:
            family = bundle.manifest.scorer_family.value
            raise RegisterArtifactError(f"no scorer registered for family {family!r}") from exc
        return loader(bundle)


def _load_standardized_linear(bundle: ArtifactBundle) -> RegisterScorer:
    manifest = bundle.manifest
    return load_standardized_linear_register_scorer(
        bundle.weights,
        artifact_id=manifest.artifact_id,
        artifact_schema=manifest.artifact_schema,
        observation_schema=manifest.observation_schema,
        label_space=manifest.label_space,
        scorer_family=manifest.scorer_family,
        feature_set=manifest.feature_set,
        langs=manifest.language_support.locales,
    )


DEFAULT_SCORER_REGISTRY = ScorerRegistry(
    {
        ScorerFamily.STANDARDIZED_LINEAR_V1: _load_standardized_linear,
    }
)
