"""Book-cover translation: Russian covers to English by image editing.

Public surface for ``pancratius work cover``.
"""

from __future__ import annotations

from pancratius.cover.models import (
    CoverElement,
    CoverResult,
    CoverStatus,
    ElementRole,
    QaVerdict,
    ResolvedElement,
    ResolvedTitle,
    TitlePin,
    TitleSource,
)
from pancratius.cover.pipeline import (
    CoverTranslateConfig,
    translate_cover,
    translate_covers,
)
from pancratius.cover.seed import (
    SeedMap,
    author_only_elements,
    init_seed,
    load_seed,
    plan_title,
    resolve_elements,
    resolve_pin,
    resolve_title,
)

__all__ = [
    "CoverElement",
    "CoverResult",
    "CoverStatus",
    "CoverTranslateConfig",
    "ElementRole",
    "QaVerdict",
    "ResolvedElement",
    "ResolvedTitle",
    "SeedMap",
    "TitlePin",
    "TitleSource",
    "author_only_elements",
    "init_seed",
    "load_seed",
    "plan_title",
    "resolve_elements",
    "resolve_pin",
    "resolve_title",
    "translate_cover",
    "translate_covers",
]
