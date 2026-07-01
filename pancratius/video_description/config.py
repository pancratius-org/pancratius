"""Knobs for the description splitter.

Model prices are never hardcoded — they are fetched live from OpenRouter when a
cost estimate is wanted. This only fixes the operator's choices: which model
runs, how faithful the body must be to survive QA, and the hook length window.
"""

from __future__ import annotations

from dataclasses import dataclass

from pancratius.openrouter import ModelId

# gemini-2.5-flash-lite won the bake-off on the real incoming videos: it reliably
# skips the SEO keyword line, lifts the author's own opening as the hook, keeps
# the intimate "ты" register, and never hallucinated a body. It is a Google
# first-party model on OpenRouter (steady availability for the weekly CI sync).
DEFAULT_MODEL: ModelId = "google/gemini-2.5-flash-lite"


@dataclass(frozen=True, slots=True)
class DescriptionConfig:
    model: ModelId = DEFAULT_MODEL

    # Faithful extraction wants low, near-deterministic sampling.
    temperature: float = 0.2
    # The longest real body runs ~3.5k chars of Russian; 6k tokens leaves room
    # for the body + hook + JSON envelope so a reply never truncates mid-string.
    max_tokens: int = 6000

    # The stored hook is the lede; the SEO/OG meta later clamps it to ~220 chars
    # at a sentence boundary. Aim a complete thought under `target`; `max` is the
    # hard QA ceiling so a hook can never become a wall of text.
    hook_target_chars: int = 240
    hook_max_chars: int = 340

    # Total model attempts (each re-prompted with the prior QA violations) before
    # falling back to the deterministic split.
    attempts: int = 3

    # Hallucination guards. A body survives only if its LEAST-grounded sentence
    # clears `faithfulness_floor` (verbatim source spans score 1.0; the floor
    # leaves room for light reformatting yet fails an invented sentence). The hook
    # is condensed, so it only has to reuse `hook_grounding_floor` of the source's
    # own vocabulary — loose enough for paraphrase, tight enough to reject a
    # wholesale fabrication.
    faithfulness_floor: float = 0.6
    hook_grounding_floor: float = 0.3
