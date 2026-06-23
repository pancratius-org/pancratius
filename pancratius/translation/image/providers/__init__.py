"""Content adapters for the generic image translation engine."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pancratius.translation.image.models import (
    ImageTranslationJob,
    ImageTranslationResult,
    ImageTranslationStatus,
)


@dataclass(frozen=True, slots=True)
class FrontmatterUpdate:
    """Provider-declared post-success frontmatter mutation."""

    path: Path
    field: str
    value: str


@dataclass(frozen=True, slots=True)
class ProviderJob:
    """A provider-built translation job plus optional successful-write finalizer."""

    job: ImageTranslationJob
    label: str
    finalize_success: Callable[[ImageTranslationResult], None] | None = None
    finalize_caveats: bool = False
    frontmatter_updates: tuple[FrontmatterUpdate, ...] = ()

    def finalize(self, result: ImageTranslationResult) -> None:
        finalizable = result.status is ImageTranslationStatus.OK or (
            self.finalize_caveats and result.status is ImageTranslationStatus.OK_WITH_CAVEAT
        )
        if finalizable and self.finalize_success is not None:
            self.finalize_success(result)


__all__ = ["FrontmatterUpdate", "ProviderJob"]
