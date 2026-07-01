"""Deterministic QA over a model draft — the gate that lets an auto-merged sync
be trusted.

The model is capable but not infallible, and nobody reviews the sync PR before it
merges. So every draft is checked against machine-verifiable rules before it is
accepted: no junk (links, promo, raw HTML) in the hook or body, the body is
grounded sentence-by-sentence in the source (not invented), the hook reuses the
source's own vocabulary, the language stayed Russian, and the hook stays within
its length. Blocking violations send the draft back to the model with the
specific complaint, then — if it still fails — to the deterministic fallback.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from pancratius.locales import Locale
from pancratius.video_description.config import DescriptionConfig
from pancratius.video_description.models import (
    BodyMarkdown,
    DescriptionDraft,
    Hook,
    RawDescription,
    VideoContext,
)
from pancratius.video_description.patterns import junk_categories

_WORD = re.compile(r"\w+", re.UNICODE)
_LETTER = re.compile(r"[^\W\d_]", re.UNICODE)     # any script's letter
_RU_CYRILLIC = re.compile(r"[а-яёА-ЯЁ]")
_UKRAINIAN = re.compile(r"[іїєґІЇЄҐ]")            # letters Russian lacks — a defect here
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?…»])\s+|\n+")

# A body longer than this must be broken into paragraphs — one unbroken block of
# this length reads as a wall, not a blog post.
_WALL_THRESHOLD = 500


class QaCode(StrEnum):
    EMPTY_HOOK = "empty_hook"
    HOOK_TOO_LONG = "hook_too_long"
    JUNK_IN_HOOK = "junk_in_hook"
    JUNK_IN_BODY = "junk_in_body"
    HOOK_UNGROUNDED = "hook_ungrounded"      # hook vocabulary not from the source
    BODY_UNGROUNDED = "body_ungrounded"      # a body sentence invented, not in the source
    WRONG_LANGUAGE = "wrong_language"        # drifted off Russian
    BODY_NOT_PARAGRAPHED = "body_not_paragraphed"  # a long body with no paragraph breaks
    HOOK_RESTATES_TITLE = "hook_restates_title"
    BODY_DUPLICATES_HOOK = "body_duplicates_hook"


# Blocking codes force a retry, then the fallback; advisory codes are recorded
# but do not reject a draft that is otherwise clean and faithful.
BLOCKING: frozenset[QaCode] = frozenset({
    QaCode.EMPTY_HOOK,
    QaCode.HOOK_TOO_LONG,
    QaCode.JUNK_IN_HOOK,
    QaCode.JUNK_IN_BODY,
    QaCode.HOOK_UNGROUNDED,
    QaCode.BODY_UNGROUNDED,
    QaCode.WRONG_LANGUAGE,
    QaCode.BODY_NOT_PARAGRAPHED,
})


@dataclass(frozen=True, slots=True)
class QaViolation:
    code: QaCode
    detail: str

    @property
    def blocking(self) -> bool:
        return self.code in BLOCKING


@dataclass(frozen=True, slots=True)
class QaVerdict:
    violations: tuple[QaViolation, ...]

    @property
    def ok(self) -> bool:
        """A draft is acceptable when no *blocking* rule fired."""
        return not any(v.blocking for v in self.violations)

    def feedback(self) -> str:
        """The blocking complaints, phrased for the model on a retry."""
        lines = [f"- {v.code.value}: {v.detail}" for v in self.violations if v.blocking]
        return "Your previous answer had problems. Fix these and return the JSON again:\n" + "\n".join(lines)


def verify(
    draft: DescriptionDraft,
    raw: RawDescription,
    context: VideoContext,
    config: DescriptionConfig,
) -> QaVerdict:
    violations: list[QaViolation] = []
    hook, body = draft.hook.strip(), draft.body.strip()
    source = _normalize(raw)

    if not hook:
        violations.append(QaViolation(QaCode.EMPTY_HOOK, "hook is empty"))
    if len(hook) > config.hook_max_chars:
        violations.append(QaViolation(
            QaCode.HOOK_TOO_LONG, f"hook is {len(hook)} chars (max {config.hook_max_chars})"))

    if hook_junk := junk_categories(hook):
        violations.append(QaViolation(QaCode.JUNK_IN_HOOK, f"hook contains {', '.join(hook_junk)}"))
    if body_junk := junk_categories(body):
        violations.append(QaViolation(QaCode.JUNK_IN_BODY, f"body contains {', '.join(body_junk)}"))

    if _drifted_off_language(hook, context.lang):
        violations.append(QaViolation(QaCode.WRONG_LANGUAGE, f"hook is not {context.lang}"))
    if body and _drifted_off_language(body, context.lang):
        violations.append(QaViolation(QaCode.WRONG_LANGUAGE, f"body is not {context.lang}"))

    # The hook is condensed from the message, so it only has to reuse the source's
    # vocabulary — a loose floor that a wholesale-fabricated hook cannot clear.
    if hook and (hg := _hook_grounding(hook, source)) < config.hook_grounding_floor:
        violations.append(QaViolation(
            QaCode.HOOK_UNGROUNDED,
            f"only {hg:.0%} of the hook's words come from the video's own description; "
            f"write the hook from the message, do not invent it",
        ))

    if body:
        if (bg := _body_grounding(body, source)) < config.faithfulness_floor:
            violations.append(QaViolation(
                QaCode.BODY_UNGROUNDED,
                f"a body sentence is only {bg:.0%} grounded in the source; do not invent — "
                f"copy the author's sentences or leave the body empty",
            ))
        if len(body) > _WALL_THRESHOLD and "\n\n" not in body:
            violations.append(QaViolation(
                QaCode.BODY_NOT_PARAGRAPHED,
                "the body is one unbroken block; break it into paragraphs separated by a blank line",
            ))

    # Advisory: quality nits that do not warrant falling back.
    if hook and _near_identical(hook, context.title):
        violations.append(QaViolation(QaCode.HOOK_RESTATES_TITLE, "hook merely restates the title"))
    if body and _starts_with(body, hook):
        violations.append(QaViolation(
            QaCode.BODY_DUPLICATES_HOOK, "body repeats the hook verbatim at its start"))

    return QaVerdict(tuple(violations))


def _normalize(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


def _shingles(normalized: str, n: int = 3) -> set[tuple[str, ...]]:
    words = normalized.split()
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


def _hook_grounding(hook: Hook, source: str) -> float:
    """Fraction of the hook's content words (≥4 letters) that appear in the
    normalized source. A faithful distilled hook reuses the message's vocabulary;
    a fabrication does not. Returns 1.0 for a hook with too few content words to
    judge (the junk/length/language checks still apply to it)."""
    words = [w for w in _normalize(hook).split() if len(w) >= 4]
    if len(words) < 3:
        return 1.0
    return sum(w in source for w in words) / len(words)


def _body_grounding(body: BodyMarkdown, source: str) -> float:
    """The LEAST-grounded sentence's score (so a single invented sentence bolted
    onto faithful prose cannot hide behind the aggregate). A sentence that is a
    verbatim span of the source scores 1.0; otherwise it is its word-trigram
    overlap with the source; a sentence too short to trigram must be a verbatim
    span or it scores 0 — an invented aphorism cannot pass on brevity."""
    source_shingles = _shingles(source)
    scores = [
        _sentence_grounding(s, source, source_shingles)
        for s in _SENTENCE_SPLIT.split(body)
        if s.strip()
    ]
    return min(scores) if scores else 1.0


def _sentence_grounding(sentence: str, source: str, source_shingles: set[tuple[str, ...]]) -> float:
    normalized = _normalize(sentence)
    if not normalized or normalized in source:
        return 1.0
    shingles = _shingles(normalized)
    if len(shingles) < 2:
        return 0.0
    return len(shingles & source_shingles) / len(shingles)


def _drifted_off_language(text: str, lang: Locale) -> bool:
    """True when a field came back in the wrong language for its locale. A Russian
    field must be mostly Russian Cyrillic and free of Ukrainian-only letters; an
    English field must be mostly Latin. Both tolerate the odd foreign token (a
    quoted term, "ChatGPT") and skip very short strings."""
    letters = len(_LETTER.findall(text))
    if letters < 8:
        return False
    russian = len(_RU_CYRILLIC.findall(text)) / letters
    if lang == "ru":
        return bool(_UKRAINIAN.search(text)) or russian < 0.6
    # English: a stray Cyrillic quoted term is fine; a substantially Russian field
    # is a drift (the whole body came back untranslated).
    return russian > 0.4


def _near_identical(a: str, b: str) -> bool:
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio() > 0.9


def _starts_with(body: BodyMarkdown, hook: Hook) -> bool:
    head = _normalize(body)[: len(_normalize(hook))]
    return bool(hook.strip()) and head == _normalize(hook)
