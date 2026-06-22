"""Prompt builders for the image text translation engine."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from enum import StrEnum

from pancratius.image_translation.models import (
    LANGUAGE_NAME,
    SCRIPT_HINT,
    ImageTranslationJob,
    QaDiscrepancy,
    ResolvedText,
    TextRole,
)


def recon_prompt(job: ImageTranslationJob) -> str:
    """Prompt for the load-bearing recon vision call."""
    source_name = LANGUAGE_NAME[job.source_lang]
    target_name = LANGUAGE_NAME[job.target_lang]
    return (
        f"You are reading a {source_name} text-bearing {job.context}. "
        "Find EVERY piece of text visibly rendered in the image: the dominant "
        "title/name, secondary text, creator/credit names, taglines, "
        "labels, and any text baked into the artwork (emblems, coins, banners, "
        "decorative lettering). For each element return: its generic role; its "
        f"verbatim {source_name}; a faithful {target_name} translation that YOU "
        "produce; and whether it is painted into the artwork (embedded=true) or "
        "overlaid as a caption (embedded=false). "
        "Use role 'primary' for the most prominent text element, 'secondary' for "
        "subtitle-like text, 'credit' only for creator/credit names, 'tagline' for "
        "slogans, 'label' for small labels, 'art_text' for text embedded in artwork, "
        "and 'other' when none fits. Also give primary_text: the dominant text as it "
        "actually appears in the source image. Do not omit any element. If unsure "
        "of a character, include it with your best read."
    )


def _role_name(role: TextRole) -> str:
    return role.value.replace("_", " ")


def _replacement_line(element: ResolvedText) -> str:
    """One enumerated replacement instruction for the generation map."""
    if not element.has_source:
        return (
            f"- Set the {_role_name(element.role)} text element to «{element.target}». "
            "Do not add a new text element if no corresponding source element exists."
        )
    if "\n" in element.source:
        source = " / ".join(line.strip() for line in element.source.splitlines() if line.strip())
        embedded = " embedded artwork" if element.embedded else ""
        return (
            f"- Replace the multi-line{embedded} text block «{source}» with the "
            f"single target string «{element.target}». Treat all listed source "
            "lines as ONE visual text element; do not translate the lines "
            "separately, and do not leave any source-line fragment behind."
        )
    if element.embedded:
        return (
            f"- IMPORTANT: The embedded artwork text «{element.source}» is painted "
            f"INTO the image. You MUST replace it with «{element.target}» in the same "
            "position, style, and size. Do not leave the source-language text or "
            "source-script characters in the artwork."
        )
    return f"- Replace «{element.source}» with «{element.target}»."


def generation_prompt(
    *,
    job: ImageTranslationJob,
    elements: Sequence[ResolvedText],
    steering: str,
) -> str:
    """Prompt for the image-edit generation call."""
    replacement_lines = [_replacement_line(e) for e in elements]
    replacement_block = (
        "Apply these exact text instructions. Render target strings verbatim; do "
        "not re-translate or paraphrase them:\n"
        + "\n".join(replacement_lines)
        + "\n"
        if replacement_lines
        else ""
    )

    steering_block = f"\n{steering.strip()}\n" if steering.strip() else ""
    source_script = SCRIPT_HINT[job.source_lang]

    return (
        f"Edit this {LANGUAGE_NAME[job.source_lang]} {job.context} into its "
        f"{LANGUAGE_NAME[job.target_lang]} version. Keep the artwork, composition, "
        "colors, fonts, sizes, and layout pixel-identical; only the visible text "
        "changes, with each target string rendered in the same style and position "
        "as its source.\n"
        + replacement_block
        + "Match the source letter-case of each element where possible. Do NOT add "
        "text that is not requested, and do NOT leave any "
        f"{source_script} text anywhere in the image. Output only the edited image, "
        "same dimensions and framing as the input."
        + steering_block
    )


def qa_prompt(*, job: ImageTranslationJob, elements: Sequence[ResolvedText]) -> str:
    """Prompt for the QA vision call (first image = source, second = target)."""
    source_script = SCRIPT_HINT[job.source_lang]
    expected = "; ".join(f"«{e.target}»" for e in elements if e.target.strip())
    expected_block = (
        f"The translated image should show these target strings where their source "
        f"elements exist: {expected}. "
        if expected
        else ""
    )
    return (
        f"You are doing quality assurance on an image text translation. The FIRST "
        f"image is the {LANGUAGE_NAME[job.source_lang]} source; the SECOND image is "
        f"the {LANGUAGE_NAME[job.target_lang]} result. {expected_block}"
        "Check for ALL of the following defects and only these:\n"
        f"(a) Any {source_script} text left untranslated, including embedded artwork text.\n"
        "(b) Background or artwork visually altered: visual elements added, removed, "
        "or structurally changed.\n"
        "(c) A source text element clearly visible in the first image is completely "
        "absent from the second image.\n"
        "(d) A constrained target string is missing or rendered as a different wording.\n\n"
        "Do NOT flag minor font differences, small text color differences, or small "
        "layout adjustments when the image remains recognizably the same.\n\n"
        "For EACH defect, set embedded=true when the offending text is painted into "
        "the artwork (an emblem, coin, banner, decorative lettering) and false when "
        "it is an overlay caption.\n\n"
        "If none of the four defects are present, set verdict to 'pass' and "
        "discrepancies to []. If any defect is present, set verdict to 'fail' and "
        "list each defect concretely."
    )


class SteeringLevel(StrEnum):
    FIRM = "firm"
    URGENT = "urgent"


_STEERING_LABELS: dict[str, str] = {
    "source_text_left": "Some source-language text is still showing. Render the listed target strings in place of it; leave no source-script text anywhere.",
    "artwork_changed": "The artwork or background was altered. Preserve it exactly; only replace text.",
    "text_dropped": "A source text element is missing. Make sure every listed target string appears in the corresponding source position.",
    "wrong_text": "A constrained target string was rendered incorrectly. Use the listed target strings exactly.",
}

_STEERING_LABELS_ESCALATED: dict[str, str] = {
    "source_text_left": "CRITICAL: source-language text is STILL visible. Each listed target string must appear; do NOT leave any source-script text, including inside artwork or emblems.",
    "artwork_changed": "CRITICAL: the artwork was altered again. Do not add, remove, or change any non-text visual element.",
    "text_dropped": "CRITICAL: a listed target string is still missing. Reproduce EVERY listed string in the same position as its source.",
    "wrong_text": "CRITICAL: a constrained target string is still wrong. Use the listed target strings exactly, with no paraphrase.",
}

_ELEMENT_NAMED_KINDS = frozenset({"source_text_left", "text_dropped", "wrong_text"})


def _named_targets(elements: Sequence[ResolvedText]) -> str:
    if not elements:
        return ""
    ordered = sorted(elements, key=lambda e: not e.embedded)
    listed = "; ".join(f"«{e.target}»" for e in ordered if e.target.strip())
    return f" The image must show exactly these target strings: {listed}." if listed else ""


def build_steering(
    discrepancies: Iterable[QaDiscrepancy],
    *,
    elements: Sequence[ResolvedText] = (),
    level: SteeringLevel = SteeringLevel.FIRM,
) -> str:
    """Build a safe steering addendum for a generation retry."""
    labels = _STEERING_LABELS_ESCALATED if level is SteeringLevel.URGENT else _STEERING_LABELS
    named = _named_targets(elements)
    seen_kinds: set[str] = set()
    lines: list[str] = []
    for disc in discrepancies:
        kind = "source_text_left" if disc.kind == "cyrillic_left" else disc.kind
        if kind in labels and kind not in seen_kinds:
            seen_kinds.add(kind)
            suffix = named if kind in _ELEMENT_NAMED_KINDS else ""
            lines.append(f"- {labels[kind]}{suffix}")
    if not lines:
        return ""
    return "Correction needed:\n" + "\n".join(lines)
