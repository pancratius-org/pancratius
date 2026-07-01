"""Configuration value types for the book-translation pipeline.

The pipeline is *book-aware chunked translation*: the whole source book travels as
a read-only reference so the model keeps terminology, personas and motifs
consistent, while output is produced one bounded chunk at a time and stitched
back into the exact source structure.

Nothing here hardcodes prices — model *prices* drift and are fetched live from
OpenRouter (`client.fetch_pricing`). This module only fixes the knobs the
operator chooses: which model runs each stage, how big a chunk is, and the
char/token ratios used for the offline cost *estimate* (real billing comes back
in each API response's usage).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from pancratius.openrouter import ModelId

# Default model for every stage: DeepSeek V4 Flash — 1M context (fits whole
# books as cached reference), strong RU→EN, and the cheapest capable tier on
# OpenRouter at time of writing ($0.09/$0.18 per Mtok, $0.02 cached input).
DEFAULT_MODEL: ModelId = "deepseek/deepseek-v4-flash"


@dataclass(frozen=True, slots=True)
class StageModels:
    """Per-stage model selection. The profile and revise stages may use a
    stronger (or reasoning) model than the bulk draft without changing the rest
    of the pipeline."""

    profile: ModelId = DEFAULT_MODEL
    draft: ModelId = DEFAULT_MODEL
    revise: ModelId = DEFAULT_MODEL
    # Fallback drafter for chunks the primary model cannot complete. The primary
    # (deepseek-v4-flash) intermittently returns null content under the strict
    # per-unit JSON schema on some dense passages; a different model clears them.
    backup_draft: ModelId | None = "google/gemini-2.5-flash"

    @classmethod
    def uniform(cls, model: ModelId) -> StageModels:
        return cls(profile=model, draft=model, revise=model)


@dataclass(frozen=True, slots=True)
class TranslateConfig:
    """The full knob-set for one translation run.

    `source_chars_per_token` / `target_chars_per_token` are empirical ratios for
    this corpus (measured on the 29 existing RU↔EN pairs with the o200k
    tokenizer: RU ≈ 3.1, EN ≈ 4.15). They drive the *estimate* only. `output_ratio`
    is the measured EN/RU token expansion (0.824) used to size `max_tokens` and
    predict output cost."""

    source_lang: str = "ru"
    target_lang: str = "en"
    models: StageModels = StageModels()

    # Chunking: target source tokens per generated chunk. ~3k keeps each request
    # well inside the quality band (SOTA: larger-than-sentence, smaller-than-book)
    # while the full book rides along as cached reference.
    chunk_source_tokens: int = 3000
    # Also cap units per chunk: a verse-dense run of short lines stays under the
    # token budget yet asks the model for one huge JSON array, and the draft model
    # stops emitting a coherent array well before the token ceiling — a 228-unit
    # chunk truncated mid-array on every attempt (unparseable → all units blank).
    # ~80 units keeps each reply short enough to return in full (the broken chunk
    # stopped near ~134 units, so 80 leaves margin); only verse sections re-split,
    # prose chunks still flush on the token budget first.
    chunk_max_units: int = 80
    # Cap on how much source we attach as read-only reference. Below the model's
    # context so the chunk + instructions + output still fit; books past this fall
    # back to a windowed reference (preceding + following neighbourhood).
    reference_token_budget: int = 600_000

    # Stage toggles.
    build_profile: bool = True
    revise: bool = True
    # After revise, a cheap pass that reconciles ONLY flagged chunk boundaries
    # (seams with an at_seam audit finding or a term rendered two ways across them).
    reconcile: bool = True

    # Sampling. Draft translation wants faithful, low-temperature output; the
    # revise critique benefits from reasoning (set per-call in the client).
    draft_temperature: float = 0.2
    revise_temperature: float = 0.1
    # Cap the revise critic's hidden reasoning so it can't spend the whole reply
    # budget thinking and return empty content.
    revise_reasoning_tokens: int = 3000

    # Re-draft a chunk while any unit is still blank, up to this many attempts.
    # ds-flash occasionally returns malformed JSON for a dense chunk; each attempt
    # only fills units earlier ones left blank, so more attempts raise a long
    # book's completion odds at no cost to healthy chunks (which finish on the
    # first). A still-incomplete chunk after this surfaces as a critical check.
    draft_attempts: int = 4

    # Estimation ratios (corpus-measured, o200k proxy).
    source_chars_per_token: float = 3.09
    target_chars_per_token: float = 4.15
    output_ratio: float = 0.824

    def with_models(self, models: StageModels) -> TranslateConfig:
        return replace(self, models=models)

    def estimate_source_tokens(self, chars: int) -> int:
        return round(chars / self.source_chars_per_token)

    def estimate_output_tokens(self, source_tokens: int) -> int:
        return round(source_tokens * self.output_ratio)
