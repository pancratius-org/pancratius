# import-pure: no filesystem mutation
"""Rights notices removed from published works and downloadable sources."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from pancratius import docx_source


class RightsBoilerplateKind(StrEnum):
    COPYRIGHT = "copyright"
    RESERVED_RIGHTS = "reserved_rights"
    REPRODUCTION_RESTRICTION = "reproduction_restriction"
    FICTION_DISCLAIMER = "fiction_disclaimer"
    FREE_GIFT_NOTICE = "free_gift_notice"
    PURITY_NOTICE = "purity_notice"


@dataclass(frozen=True, slots=True)
class RightsRemoval:
    ordinal: docx_source.ParagraphOrdinal
    text: str
    kind: RightsBoilerplateKind

    def matches(self, text: str) -> bool:
        """Whether a live projection still denotes this exact notice."""
        return (
            text == self.text
            and classify_rights_boilerplate_notice(text) is self.kind
        )


@dataclass(frozen=True, slots=True)
class RightsRemovalPlan:
    removals: tuple[RightsRemoval, ...] = ()

    def __post_init__(self) -> None:
        ordinals = tuple(removal.ordinal for removal in self.removals)
        if len(set(ordinals)) != len(ordinals):
            raise ValueError("rights-removal ordinals must be unique")


class RightsRemovalMismatch(RuntimeError):
    """A source-approved removal no longer has one exact target projection."""


_PERSON_NAME = (
    r"(?!The\b|This\b|That\b|A\b|An\b)"
    r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё.'’\-]+"
    r"(?:\s+[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё.'’\-]+){0,3}"
)


_NOTICE_PATTERNS = {
    RightsBoilerplateKind.COPYRIGHT: re.compile(
        r"(?:(?i:Copyright)\s+)?©\s*(?:"
        r"\d{4}(?:\s*[,–-]\s*\d{2,4})*"
        rf"(?:\s+{_PERSON_NAME})?"
        r"|Сергей\s+Орехов(?:\s*\([^\n)]*\))?(?:\s*,\s*\d{4})+"
        r")\.?"
    ),
    RightsBoilerplateKind.RESERVED_RIGHTS: re.compile(
        r"(?:All\s+rights\s+reserved|Все\s+права\s+защищены)\.?",
        re.I,
    ),
    RightsBoilerplateKind.REPRODUCTION_RESTRICTION: re.compile(
        r"(?:No\s+part\s+of\s+this\s+book\s+may\s+be\s+reproduced"
        r"(?:\s+or\s+(?:used|utilized|distributed))?"
        r"(?:[.,;:]?\s+(?:stored|transmitted|distributed|in\s+any|by\s+any|without|except)"
        r"[^\n]*)?"
        r"|Никакая\s+часть\s+(?:этой|данной)\s+книги\s+"
        r"(?:не\s+может\s+быть\s+воспроизведена|не\s+подлежит\s+воспроизведению)"
        r"[^\n]*"
        r"|Воспроизведение\s+(?:или\s+)?распространение[^\n]*запрещено)\.?",
        re.I,
    ),
    RightsBoilerplateKind.FICTION_DISCLAIMER: re.compile(
        r"The\s+characters\s+and\s+events\s+portrayed"
        r"(?:\s+in\s+this\s+(?:book|work))?\s+are\s+fictitious[.;]\s+"
        r"Any\s+resemblance(?:\s+to\s+actual\s+(?:persons?|events?))?\s+"
        r"is\s+(?:purely\s+)?coincidental\.?",
        re.I,
    ),
    RightsBoilerplateKind.FREE_GIFT_NOTICE: re.compile(
        r"Эта\s+книга\s+даруется\s+миру\s+свободно\.?",
        re.I,
    ),
    RightsBoilerplateKind.PURITY_NOTICE: re.compile(
        r"Пусть\s+е[ёе]\s+чистота\s+будет\s+сохранена\.?",
        re.I,
    ),
}


def classify_rights_boilerplate_notice(text: str) -> RightsBoilerplateKind | None:
    """Classify one complete legal/chrome paragraph; prose mentions are not notices."""
    stripped = text.strip()
    return next(
        (kind for kind, pattern in _NOTICE_PATTERNS.items() if pattern.fullmatch(stripped)),
        None,
    )


def plan_rights_removal(source: docx_source.DocxSourceDocument) -> RightsRemovalPlan:
    """Select removable top-level notices once, by stable source identity."""
    paragraphs = source.paragraphs
    first_heading = next(
        (index for index, paragraph in enumerate(paragraphs) if paragraph.heading),
        len(paragraphs),
    )
    window_end = min(first_heading, max(20, int(len(paragraphs) * 0.03)))
    return RightsRemovalPlan(
        tuple(
            RightsRemoval(paragraph.ordinal, paragraph.text, kind)
            for paragraph in paragraphs[:window_end]
            if paragraph.atomic_deletion_safe
            and (kind := classify_rights_boilerplate_notice(paragraph.text)) is not None
        )
    )
