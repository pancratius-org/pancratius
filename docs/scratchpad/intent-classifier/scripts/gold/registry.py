# research-pure: the frozen panel — which models the gold run uses, owned here, no API import.
"""The gold pipeline owns its panel definition (the readers + their OpenRouter ids + modality), so
the manifest records exact model ids without importing the network reader. `vision=False` readers
see the per-line structure listing only; `vision=True` also see the docx-page composite.

Validated panel (`[[lineated_prose_not_brief_separable]]`): grok leads; gemini-pro + ds-flash-text
complete the core triad; glm is an extra signal (never solo for prose — it over-lineates).
"""
from __future__ import annotations

from dataclasses import dataclass

from .types import ReaderId


@dataclass(frozen=True, slots=True)
class Model:
    model_id: str
    vision: bool


PANEL: dict[ReaderId, Model] = {
    "grok": Model("x-ai/grok-4.3", vision=True),
    "gemini-pro": Model("google/gemini-3.1-pro-preview", vision=True),
    "ds-flash-text": Model("deepseek/deepseek-v4-flash", vision=False),
    "glm": Model("z-ai/glm-4.7-flash", vision=False),
}


def model_ids(readers: tuple[ReaderId, ...]) -> dict[ReaderId, str]:
    return {r: PANEL[r].model_id for r in readers if r in PANEL}
