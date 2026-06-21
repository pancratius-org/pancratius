"""Prompt construction and the unit I/O protocol.

The model never sees Markdown structure — it sees a JSON map ``{unit_id: text}``
and must return ``{unit_id: translation}`` for exactly the requested ids. That
keeps the model's job purely lexical (translate spans) while ``document.py`` owns
every structural byte. Each unit's source is a single line, so the JSON strings
never carry newlines and parsing stays trivial.

Message layout is built for prompt caching: a constant system style guide, then a
per-book read-only reference (brief + full source map) marked as a cache
breakpoint, then the small varying per-chunk instruction. For a book's 2nd..Nth
chunk the whole prefix is served from cache.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from pancratius.translate.client import ChatMessage
from pancratius.translate.document import TextUnit, Translations

# The constant contract. Cached across the entire run (every book, every chunk).
STYLE_GUIDE = """\
You are an expert literary translator rendering Russian sacred, visionary and \
philosophical prose into English of the same stature. The corpus speaks in an \
elevated, scriptural, intimate voice; many passages are first-person divine \
speech. Translate so an English reader meets the same text — not a paraphrase.

Absolute rules:
1. FAITHFULNESS over fluency. Never omit, add, soften, or amplify. Preserve the \
claim, the agency (who acts on whom), the modality (must/will/may), and the \
theological force exactly. If the source is strange, keep it strange.
2. VOICE. Keep persona and register distinctions consistent across the book \
(the Creator, the Son, the narrator, the questioner). Follow the brief's \
terminology and persona notes for names and recurring terms.
3. MARKDOWN IS STRUCTURE — preserve it byte-exact. Keep every ``**bold**``, \
``*italic*``, link ``[text](url)`` and inline marker as-is; translate only the \
human-readable words. NEVER alter a URL, a path, or a unit id.
4. TYPOGRAPHY. Render Russian guillemets «…» and any quotation as English curly \
quotes “…” / ‘…’. Use English em-dashes and sentence punctuation naturally.
5. ONE LINE PER UNIT. Each translation is a single line; never introduce line \
breaks inside a unit.

CALIBRATION — recurring traps; get these exactly right:
- A bare copula IS the meaning: «ЕСТЬ» → "IS"; «Я ЕСМЬ» → "I AM". Never pad to \
"there is", "it exists", "I exist". If one word stands alone (often bold), keep it \
one bare word and keep the bold.
- «от Моего/Его имени» is the idiom "in My/His name" (on My/His behalf), NOT \
"from My/His name".
- Grammatical gender is not personhood: «Она»/«Он» standing in for a noun (книга, \
Евангелие, истина, душа) is "it" in English. Use "She"/"He" only where the text \
clearly personifies a living presence.
- Do NOT soften, explain, or hedge. Keep absolute, strange, first-person divine \
claims exactly as stated — no "it is as if", no smoothed agency or modality.
- Capitalized sacred terms stay capitalized and consistent: Свет→Light, \
Истина→Truth, Слово→Word, Лик→Face, Присутствие→Presence, Творец→Creator, \
Агнец→Lamb, Царствие→Kingdom.
- CROSS-SCRIPT WORDPLAY / NAME-ETYMOLOGY. When meaning depends on a Russian \
word's spelling, sound, or letters (a name explained by its roots, a pun on a \
letter), render the MEANING in English, PRESERVE the Russian in guillemets, and \
add a Latin transliteration where it is not obvious — format: English-meaning \
(Russian «source», "translit"). e.g. «Глагол» → Word (Russian «Глагол», \
"Glagol"); «имеЙ» → Have (Russian «имеЙ», "imeY"). NEVER weld two scripts into \
one token (never "HaveЙ"). A bare, obvious transliteration of a plain name needs \
no gloss: «Се» → Se.

You are given source units as {id: source}; translate each requested unit and
return it under its exact id. The reply shape is enforced by a schema."""


@dataclass(frozen=True, slots=True)
class TermEntry:
    """One terminology decision carried into the brief: a Russian ``source`` and
    its fixed English ``target``, with an optional ``note`` and a ``locked`` flag.
    Locked entries are editorial decisions the operator owns (a ``--glossary``);
    unlocked ones are the model's own per-book proposals. Both flow into the brief
    the same way — ``locked`` only governs precedence when the two sources merge."""

    source: str
    target: str
    note: str = ""
    locked: bool = False


@dataclass(frozen=True, slots=True)
class TitlePrecedent:
    """A RU→EN book-title pair already rendered elsewhere in the library, offered
    to the model as house-style precedent (a weak reference, not ground truth)."""

    source_ru: str
    target_en: str


def _units_json(units: Sequence[TextUnit]) -> str:
    return json.dumps({unit.id: unit.source for unit in units}, ensure_ascii=False, indent=0)


def _units_text(units: Sequence[TextUnit]) -> str:
    """The units as plain newline-joined source — for the read-only reference, which
    is context the model only reads (no ids, no JSON). A `{id: source}` map would add
    ~24 framing tokens per unit, tripling a unit-dense book's reference for nothing."""
    return "\n".join(unit.source for unit in units)


def build_brief(
    *,
    title_ru: str,
    title_en: str,
    summary: str,
    register: str,
    personas: Sequence[str],
    terms: Sequence[TermEntry],
    title_precedents: Sequence[TitlePrecedent],
) -> str:
    lines = [
        "TRANSLATION BRIEF (read-only context for the whole book):",
        f"- Russian title: {title_ru}",
        f"- English title: {title_en}" if title_en else "",
        f"- Summary: {summary}" if summary else "",
        f"- Register / voice: {register}" if register else "",
    ]
    if personas:
        lines.append("- Personas: " + "; ".join(personas))
    if terms:
        lines.append("- Locked terminology (source → English):")
        lines += [f"    {t.source} → {t.target}" + (f"  ({t.note})" if t.note else "") for t in terms]
    if title_precedents:
        lines.append("- Related book titles already rendered in this library (stay consistent):")
        lines += [f"    {p.source_ru} → {p.target_en}" for p in title_precedents]
    return "\n".join(line for line in lines if line)


def translate_messages(
    *,
    brief: str,
    full_source_units: Sequence[TextUnit],
    chunk_units: Sequence[TextUnit],
) -> list[ChatMessage]:
    """Messages for one draft chunk. The brief + full-book plain-text source is the
    cached reference; the trailing instruction (the id-keyed chunk) is the only
    varying part."""
    reference = (
        f"{brief}\n\nFULL SOURCE (read-only; for global consistency only — "
        f"do NOT translate these now):\n{_units_text(full_source_units)}"
    )
    instruction = (
        "Translate ONLY the units below, using the full source and brief above for "
        "context:\n"
        f"{_units_json(chunk_units)}"
    )
    return [
        ChatMessage("system", STYLE_GUIDE, cache=True),
        ChatMessage("user", reference, cache=True),
        ChatMessage("user", instruction),
    ]


def revise_messages(
    *,
    brief: str,
    units: Sequence[TextUnit],
    draft: Translations,
) -> list[ChatMessage]:
    """Messages for the source-aware revise pass over one chunk. The critic sees
    source and draft side by side and returns improved text ONLY where needed."""
    pairs = {
        unit.id: {"source": unit.source, "draft": draft.get(unit.id, "")} for unit in units
    }
    # No "smooth seams" here: revise sees one chunk in isolation, so cross-boundary
    # consistency is owned by the dedicated reconcile pass below.
    instruction = (
        "You are revising an existing draft translation. For each unit you are given "
        "the Russian source and the current English draft. Improve faithfulness to "
        "the source (restore anything omitted/softened, fix agency/modality), keep "
        "terminology and persona voice consistent with the brief, and polish the "
        "English. Return ONLY the units whose text you changed under their exact "
        "ids; omit units already correct.\n"
        f"{json.dumps(pairs, ensure_ascii=False, indent=0)}"
    )
    return [
        ChatMessage("system", STYLE_GUIDE, cache=True),
        ChatMessage("user", brief, cache=True),
        ChatMessage("user", instruction),
    ]


def reconcile_messages(
    *,
    brief: str,
    units: Sequence[TextUnit],
    draft: Translations,
) -> list[ChatMessage]:
    """Messages for the seam-reconcile pass: a window straddling one chunk boundary,
    drafted in two separate passes. The critic sees both sides' source and current
    English and makes terminology, voice, and any clause split CONSISTENT across the
    boundary, returning only the units it changed."""
    pairs = {
        unit.id: {"source": unit.source, "draft": draft.get(unit.id, "")} for unit in units
    }
    instruction = (
        "These units span a chunk boundary translated in separate passes, so a "
        "recurring term, a voice, or a clause split may differ between the two "
        "sides. Make terminology, persona voice, and any clause split CONSISTENT "
        "across the boundary, staying faithful to each source. Return ONLY the "
        "units whose text you changed under their exact ids; omit the rest.\n"
        f"{json.dumps(pairs, ensure_ascii=False, indent=0)}"
    )
    return [
        ChatMessage("system", STYLE_GUIDE, cache=True),
        ChatMessage("user", brief, cache=True),
        ChatMessage("user", instruction),
    ]


PROFILE_INSTRUCTION = """\
You are preparing a translation brief for the book below. Read it as a whole, then
produce: a faithful English title and description, the tags in English, a short
summary (argument, arc, stakes), one line on the voice/register and who speaks, the
recurring personas, a termbase of names and recurring terms whose English must stay
consistent (mark the fixed ones locked), and the recurring symbolic phrases. The
reply fields are enforced by a schema."""


def profile_messages(
    *,
    title_ru: str,
    description_ru: str,
    tags_ru: Sequence[str],
    source_text: str,
    title_precedents: Sequence[TitlePrecedent],
) -> list[ChatMessage]:
    precedent = ""
    if title_precedents:
        listing = "\n".join(f"    {p.source_ru} → {p.target_en}" for p in title_precedents)
        precedent = (
            "\n\nRelated book titles already rendered in this library "
            f"(reference for house style, not ground truth):\n{listing}"
        )
    tags_line = f"\nRussian tags: {', '.join(tags_ru)}" if tags_ru else ""
    user = (
        f"Russian title: {title_ru}\nRussian description: {description_ru}{tags_line}{precedent}"
        f"\n\nSOURCE:\n{source_text}"
    )
    return [
        ChatMessage("system", PROFILE_INSTRUCTION, cache=True),
        ChatMessage("user", user),
    ]


