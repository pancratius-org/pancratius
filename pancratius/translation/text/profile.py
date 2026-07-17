"""The per-book translation brief (the "pre-pass").

Before translating a chunk the pipeline reads the whole book once and asks the
model for a structured brief: an English title/description, a summary, the voice,
the recurring personas, and a termbase of names/terms whose English rendering
must stay fixed. That brief rides along (cached) with every chunk so terminology,
persona voice and motifs stay consistent across a long book — the failure mode of
naive chunk-by-chunk MT.

Term precedence, strongest first: a human-authored ``--glossary`` (locked), then
the model-proposed per-book terms, then mined title precedents from
already-translated books.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pancratius.content_catalog import CatalogEntry
from pancratius.translation.text.client import TranslatorClient, Usage
from pancratius.translation.text.config import (
    MAX_OUTPUT_TOKENS,
    TranslateConfig,
    reasoning_budget,
)
from pancratius.translation.text.prompts import TermEntry, TitlePrecedent, profile_messages
from pancratius.translation.text.schema import profile_format

logger = logging.getLogger(__name__)

# The brief is one call per book and its termbase grows with the book: the corpus's
# second-largest needs ~7k tokens for 98 terms. Take the house ceiling — a bigger
# book must not silently lose terms off the end of a tighter budget.
_PROFILE_MAX_TOKENS = MAX_OUTPUT_TOKENS


class ProfileError(RuntimeError):
    """The brief could not be built, so this book has no English title,
    description, or termbase to translate against."""


# A model's structured brief reply, parsed from JSON: untrusted, read defensively
# field by field (the readers below tolerate any missing or mistyped field).
type ProfileReply = Mapping[str, Any]

# The corpus tag glossary's `en` block: a canonical RU tag key mapped to the one
# EN display label that concept keeps across every book.
type TagKey = str
type TagLabel = str
type TagLabels = Mapping[TagKey, TagLabel]


@dataclass(frozen=True, slots=True)
class PersonaEntry:
    name: str
    voice: str


@dataclass(frozen=True, slots=True)
class BookProfile:
    title_en: str
    description_en: str
    summary: str
    register: str
    personas: tuple[PersonaEntry, ...]
    terms: tuple[TermEntry, ...]
    recurring: tuple[str, ...]

    def persona_lines(self) -> tuple[str, ...]:
        return tuple(f"{p.name}: {p.voice}" for p in self.personas)


@dataclass(frozen=True, slots=True)
class ProfileResult:
    profile: BookProfile
    usage: Usage


# The brief schema is strict, so a well-formed reply is the norm; these readers
# only have to survive a stray malformed field (e.g. when `_lenient_object` had to
# salvage JSON from a prose-wrapped reply). Each narrows structurally and drops
# anything that doesn't fit, never raising.
def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def str_tuple(raw: object) -> tuple[str, ...]:
    """Coerce an untrusted JSON value to its string elements, dropping the rest
    (shared with the pipeline's frontmatter reads)."""
    if not isinstance(raw, list):
        return ()
    return tuple(item for item in raw if isinstance(item, str))


def _personas(raw: object) -> tuple[PersonaEntry, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[PersonaEntry] = []
    for item in raw:
        match item:
            case {"name": name, "voice": voice}:
                out.append(PersonaEntry(name=_str(name), voice=_str(voice)))
    return tuple(out)


def _terms(raw: object) -> tuple[TermEntry, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[TermEntry] = []
    for item in raw:
        match item:
            case {"source": str(source), "target": str(target), **rest} if source and target:
                out.append(
                    TermEntry(
                        source=source,
                        target=target,
                        note=_str(rest.get("note")),
                        locked=bool(rest.get("locked")),
                    )
                )
    return tuple(out)


def _profile_from_json(
    data: ProfileReply, *, fallback_title: str, fallback_desc: str
) -> BookProfile:
    return BookProfile(
        title_en=_str(data.get("title_en")) or fallback_title,
        description_en=_str(data.get("description_en")) or fallback_desc,
        summary=_str(data.get("summary")),
        register=_str(data.get("register")),
        personas=_personas(data.get("personas")),
        terms=_terms(data.get("terms")),
        recurring=str_tuple(data.get("recurring")),
    )


def build_profile(
    client: TranslatorClient,
    config: TranslateConfig,
    *,
    title_ru: str,
    description_ru: str,
    tags_ru: Sequence[str],
    source_text: str,
    title_precedents: Sequence[TitlePrecedent],
) -> ProfileResult:
    messages = profile_messages(
        title_ru=title_ru,
        description_ru=description_ru,
        tags_ru=tags_ru,
        source_text=source_text,
        title_precedents=title_precedents,
    )
    usage = Usage.empty()
    for attempt in range(config.profile_attempts):
        completion = client.complete(
            model=config.models.profile,
            messages=messages,
            temperature=config.draft_temperature,
            max_tokens=_PROFILE_MAX_TOKENS,
            response_format=profile_format(),
            reasoning_max_tokens=reasoning_budget(
                config.profile_reasoning_tokens, _PROFILE_MAX_TOKENS
            ),
        )
        usage += completion.usage
        data = _reply_object(completion.text)
        if data is not None:
            profile = _profile_from_json(
                data, fallback_title=title_ru, fallback_desc=description_ru
            )
            return ProfileResult(profile=profile, usage=usage)
        logger.warning(
            "profile brief %s for %r (attempt %d/%d, %s); retrying",
            "hit the token ceiling" if completion.truncated else "reply was unparseable",
            title_ru[:40], attempt + 1, config.profile_attempts, config.models.profile,
        )
    raise ProfileError(
        f"could not build a brief for {title_ru!r} after {config.profile_attempts} attempts: "
        f"{config.models.profile} never returned a parseable reply"
    )


def _reply_object(text: str) -> ProfileReply | None:
    """The reply as an object, or None when even a lenient read cannot find one.
    The schema makes a well-formed object the norm; a stray prose wrapper is still
    salvageable, anything else is a failed attempt."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return _lenient_object(text)
        except (json.JSONDecodeError, ValueError):
            return None


def _lenient_object(text: str) -> ProfileReply:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("profile reply was not JSON")
    parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ValueError("profile reply was not a JSON object")
    return parsed


# --- term sources -------------------------------------------------------------
def load_glossary(path: Path) -> tuple[TermEntry, ...]:
    """A human-authored glossary (YAML list of ``{source,target,note?}``). These
    are locked editorial decisions the operator owns; the tool only consumes them."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"glossary {path} must be a YAML list of source/target entries")
    out: list[TermEntry] = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("source") or not item.get("target"):
            raise ValueError(f"glossary {path} entry needs 'source' and 'target': {item!r}")
        out.append(
            TermEntry(
                source=_str(item.get("source")),
                target=_str(item.get("target")),
                note=_str(item.get("note")),
                locked=True,
            )
        )
    return tuple(out)


def load_tag_labels(path: Path) -> TagLabels:
    """Canonical RU-tag → EN-label map from the glossary's `en` block (shape
    ``{ru: {...}, en: {ru_key: en_label}}``). The pipeline maps each book's RU
    tags through this so one concept keeps one EN label across the corpus."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    en = raw.get("en") if isinstance(raw, dict) else None
    if not isinstance(en, dict):
        raise ValueError(f"tag glossary {path} must have an 'en' object of RU-key → EN-label")
    return {str(k): str(v) for k, v in en.items() if isinstance(v, str)}


def title_precedents(entries: Iterable[CatalogEntry]) -> tuple[TitlePrecedent, ...]:
    """RU→EN title pairs from works already translated in the corpus — house-style
    precedent (weak reference, not ground truth)."""
    titles_by_work: dict[tuple[str, int], dict[str, str]] = {}
    for entry in entries:
        titles_by_work.setdefault((entry.kind, entry.number), {})[entry.lang] = entry.title
    precedents = [
        TitlePrecedent(source_ru=ru, target_en=en)
        for langs in titles_by_work.values()
        if (ru := langs.get("ru")) and (en := langs.get("en"))
    ]
    return tuple(sorted(precedents, key=lambda p: (p.source_ru, p.target_en)))


def effective_terms(profile: BookProfile, glossary: Sequence[TermEntry]) -> tuple[TermEntry, ...]:
    """Merge glossary over model-proposed terms, deduped by source. The glossary
    comes last so its locked entries win the key collision."""
    merged: dict[str, TermEntry] = {}
    for term in (*profile.terms, *glossary):
        merged[term.source] = term
    return tuple(merged.values())
