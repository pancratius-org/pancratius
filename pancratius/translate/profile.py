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
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from pancratius.content_catalog import CatalogEntry
from pancratius.translate.client import TranslatorClient, Usage
from pancratius.translate.config import TranslateConfig
from pancratius.translate.prompts import TermEntry, TitlePrecedent, profile_messages
from pancratius.translate.schema import profile_format

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PersonaEntry:
    name: str
    voice: str


@dataclass(frozen=True, slots=True)
class BookProfile:
    title_en: str
    description_en: str
    tags_en: tuple[str, ...]
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
    data: dict[str, Any], *, fallback_title: str, fallback_desc: str, fallback_tags: Sequence[str]
) -> BookProfile:
    return BookProfile(
        title_en=_str(data.get("title_en")) or fallback_title,
        description_en=_str(data.get("description_en")) or fallback_desc,
        tags_en=str_tuple(data.get("tags_en")) or tuple(fallback_tags),
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
    max_tokens = 4096
    completion = client.complete(
        model=config.models.profile,
        messages=messages,
        temperature=config.draft_temperature,
        max_tokens=max_tokens,
        response_format=profile_format(),
    )
    try:
        data = json.loads(completion.text)
    except json.JSONDecodeError:
        # The schema makes a valid object the norm; tolerate a stray prose wrapper,
        # and if even that fails, degrade to a minimal brief rather than failing the
        # whole book — the brief is an aid, not a hard requirement.
        try:
            data = _lenient_object(completion.text)
        except (json.JSONDecodeError, ValueError):
            logger.warning("profile JSON unparseable for %r; using a minimal brief", title_ru[:40])
            data = {}
    profile = _profile_from_json(
        data, fallback_title=title_ru, fallback_desc=description_ru, fallback_tags=tags_ru
    )
    return ProfileResult(profile=profile, usage=completion.usage)


def _lenient_object(text: str) -> dict[str, Any]:
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
