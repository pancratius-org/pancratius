"""Prompt builders for the cover pipeline.

Three call types:
  1. Recon: vision call on the RU cover that EXTRACTS and TRANSLATES every text
     element (the load-bearing step — see the module docstring of pipeline.py).
  2. Generation: image-edit call handed an explicit per-element replacement map.
  3. QA: vision call on both RU and EN covers to check correctness.

Each function returns a plain string (or structured payload) — no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum

from pancratius.cover.models import (
    AUTHOR_EN,
    QaDiscrepancy,
    ResolvedElement,
    ResolvedTitle,
)


def recon_prompt() -> str:
    """Prompt for the load-bearing recon vision call.

    Asks the model to enumerate AND translate every visible text element on the
    cover. Each element carries its verbatim Russian, the model's English, its role,
    and whether it is baked into the artwork. The schema (``recon_format``) pins the
    shape; this prose tells the model what to find and how to translate it.
    """
    return (
        "You are reading a Russian book cover image. "
        "Find EVERY piece of text visibly rendered on this cover — the title, "
        "subtitle, author name, any taglines, and any text baked into the artwork "
        "(emblems, coins, banners, decorative lettering). For each element return: "
        "its role; its verbatim Russian; a faithful English translation that YOU "
        "produce; and whether it is painted into the artwork (art_baked=true) or "
        "overlaid as a caption (art_baked=false). "
        "Translate meaning, not letters: «Система дефицита» is 'System of Scarcity' "
        "(a deficit/shortage system), NOT 'System of avoidance'. For the author name, "
        "transliterate. "
        "Also give 'displayed_title': the title as it ACTUALLY APPEARS on the cover "
        "(which may be shorter than the full catalogue title — e.g. just 'Мамона' "
        "rather than 'Книга 50. Мамона. Почему ты в его власти…'). "
        "Do not omit any element. If unsure of a character, include it with your best read."
    )


def _replacement_line(element: ResolvedElement) -> str:
    """One enumerated replacement instruction for the generation map.

    Art-baked elements (seal/emblem/coin/banner text painted into the artwork)
    receive a more emphatic instruction that names the exact source text and its
    English equivalent — the generic line under-performs for embedded artwork.
    """
    if element.art_baked:
        return (
            f'- IMPORTANT: The seal/emblem/coin/banner text «{element.russian}» '
            f'is painted INTO the artwork. You MUST replace it with «{element.english}» '
            f'— render «{element.english}» in the same position, style, and size as '
            f'«{element.russian}». Do not leave «{element.russian}» or any Cyrillic '
            f'characters in the artwork.'
        )
    return f'- Replace «{element.russian}» with «{element.english}».'


def generation_prompt(
    elements: Sequence[ResolvedElement],
    steering: str,
) -> str:
    """Prompt for the image-edit generation call.

    The model is handed an EXPLICIT, ENUMERATED replacement map — one
    "Replace «russian» with «english»" line per resolved element — and told to
    render exactly those strings while keeping the artwork, fonts and layout
    identical. It does NOT find-or-translate on its own, so it cannot miss an
    element or invent a wrong translation. The title's resolved English is already
    one of the ``elements`` (see ``resolve_elements``).

    ``steering`` is the addendum from a previous failed QA attempt (empty on the
    first attempt). It references specific elements by English, never raw Cyrillic.
    """
    replacement_lines = [_replacement_line(e) for e in elements]
    replacement_block = (
        "Replace each Russian string below with the exact English given — render "
        "the English verbatim, do not re-translate or paraphrase it:\n"
        + "\n".join(replacement_lines)
        + "\n"
        if replacement_lines
        else ""
    )

    steering_block = f"\n{steering.strip()}\n" if steering.strip() else ""

    return (
        "Edit this Russian book cover into its English edition. "
        "Keep the artwork, composition, colors, fonts, sizes, and layout pixel-identical; "
        "ONLY the text changes, each English string rendered in the same style and "
        "position as the Russian it replaces.\n"
        + replacement_block
        + "Match the source letter-case of each element: if the cover shows a string in "
        "all-capitals, render its English in all-capitals too "
        f"(e.g. the author as 'SERGEI PANCRATIUS' rather than '{AUTHOR_EN}'). "
        "Do NOT add any text that is not in this list, and do NOT leave any Russian "
        "or Cyrillic characters anywhere on the cover. "
        "Output only the edited cover image, same dimensions and framing as the input."
        + steering_block
    )


def qa_prompt(title: ResolvedTitle) -> str:
    """Prompt for the QA vision call (first image = RU source, second = EN output).

    Checks for: (a) untranslated Cyrillic, (b) artwork changes including phantom
    text added, (c) dropped text, (d) author name correctness.

    Title correctness is anchored on ``title.to_render`` — the exact short string
    the cover should bear. With no pin (``to_render`` empty) we do not assert a
    title wording: the model translated the displayed title faithfully, which is
    correct, and we must not flag a mere phrasing difference.
    """
    cover_title_hint = (
        f"The title should read exactly '{title.to_render}'. " if title.is_pinned else ""
    )

    return (
        "You are doing quality assurance on a book cover translation. "
        "The FIRST image is the RUSSIAN original; the SECOND image is the ENGLISH translation. "
        f"{cover_title_hint}"
        f"The author name must appear EXACTLY as '{AUTHOR_EN}' (case as on the cover). "
        "Check for ALL of the following defects and only these:\n"
        "(a) Any Russian/Cyrillic text left untranslated (including text in artwork/emblems).\n"
        "(b) Background or artwork visually altered — elements ADDED that were not in the "
        "source (e.g. a phantom word appears in the artwork or emblem), or structural "
        "elements REMOVED.\n"
        "(c) A text element clearly visible in the Russian cover that is completely absent "
        "from the English (not just translated differently, but entirely missing).\n"
        "(d) The author name is not rendered as required.\n\n"
        "Do NOT flag: minor font differences, a title that translates correctly "
        "but uses a different phrasing from the catalogue title, colour differences "
        "in text, or small layout adjustments. These are acceptable.\n\n"
        "For EACH defect, set 'in_artwork' to true when the offending text is painted "
        "into the artwork (an emblem, coin, banner, or decorative lettering) and false "
        "when it is an overlay caption (title, subtitle, author, or tagline).\n\n"
        "If none of the four defects are present, set verdict to 'pass' and "
        "discrepancies to []. If any defect is present, set verdict to 'fail' and "
        "list each defect concretely (quote the exact offending text)."
    )


class SteeringLevel(StrEnum):
    """How forcefully a retry's correction is phrased.

    FIRM is the first correction. URGENT escalates to a negative, capitalised
    instruction once a defect has already survived one retry — the gentle wording
    did not land, so we sharpen it.
    """

    FIRM = "firm"
    URGENT = "urgent"


# Per-kind correction text. The "cyrillic_left"/"text_dropped" entries are
# suffixed at call time with the named English strings that must be present, so the
# correction points at the actual elements rather than saying "translate all Russian".
_STEERING_LABELS: dict[str, str] = {
    "cyrillic_left": "Some Russian/Cyrillic text is still showing. Render the listed English strings in place of it; leave no Cyrillic anywhere.",
    "artwork_changed": "The artwork or background was altered. Preserve it exactly — do not add or remove any visual elements.",
    "text_dropped": "A text element from the source is missing. Make sure every listed English string appears, in the same position as its Russian.",
    "author_wrong": f"The author name was not rendered correctly. It must be exactly '{AUTHOR_EN}' (matching source letter-case).",
}

# URGENT phrasing once a defect has already survived one correction.
_STEERING_LABELS_ESCALATED: dict[str, str] = {
    "cyrillic_left": (
        "CRITICAL: Russian/Cyrillic text is STILL visible. Each listed English string "
        "must appear; do NOT leave any Cyrillic characters, including inside artwork or emblems."
    ),
    "artwork_changed": (
        "CRITICAL: The artwork was altered again. You must NOT add, remove, or change any "
        "visual element in the background or artwork. Only replace text; leave everything "
        "else pixel-identical."
    ),
    "text_dropped": (
        "CRITICAL: A listed English string is still missing. Reproduce EVERY listed string "
        "in the same position as its Russian source."
    ),
    "author_wrong": (
        f"CRITICAL: The author name is still wrong. Use ONLY '{AUTHOR_EN}'. "
        "Do not use any other spelling, transliteration, or variant. "
        "Match the letter-case of the source (ALL-CAPS if the source is ALL-CAPS)."
    ),
}

# Defect kinds whose correction is sharpened by naming the per-element English.
_ELEMENT_NAMED_KINDS = frozenset({"cyrillic_left", "text_dropped"})


def _named_english(elements: Sequence[ResolvedElement]) -> str:
    """The English strings to assert present, Cyrillic-free, for a leftover/drop.

    Art-baked elements are listed first — they are the usual culprits for a
    surviving leftover — but the whole authoritative set is named so the model
    targets specific strings, not a generic 'all Russian'.
    """
    if not elements:
        return ""
    ordered = sorted(elements, key=lambda e: not e.art_baked)
    listed = "; ".join(f"«{e.english}»" for e in ordered)
    return f" The cover must show exactly these English strings: {listed}."


def build_steering(
    discrepancies: Iterable[QaDiscrepancy],
    *,
    elements: Sequence[ResolvedElement] = (),
    level: SteeringLevel = SteeringLevel.FIRM,
) -> str:
    """Build a safe steering addendum for a generation retry.

    QA descriptions are NOT forwarded verbatim — they may carry Cyrillic or error
    text that trips the generation model's safety filters. Each defect kind maps to
    a pre-written English instruction; leftover/drop kinds are sharpened with the
    named English strings (``elements``) so the correction points at specific
    elements rather than "translate all Russian". The English is the resolved
    authoritative form, so naming it leaks no raw source Cyrillic.

    ``level`` selects FIRM (first retry) or URGENT (a defect that survived a
    correction) phrasing.
    """
    labels = _STEERING_LABELS_ESCALATED if level is SteeringLevel.URGENT else _STEERING_LABELS
    named = _named_english(elements)
    seen_kinds: set[str] = set()
    lines: list[str] = []
    for disc in discrepancies:
        if disc.kind in labels and disc.kind not in seen_kinds:
            seen_kinds.add(disc.kind)
            suffix = named if disc.kind in _ELEMENT_NAMED_KINDS else ""
            lines.append(f"- {labels[disc.kind]}{suffix}")
    if not lines:
        return ""
    return "Correction needed:\n" + "\n".join(lines)
