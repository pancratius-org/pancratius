# import-pure: no filesystem mutation
"""Strip pasted-source chrome — a publication timestamp, a byline link, a style
note, or a pen-name/date sign-off — from a lowered poem body, lifting
persona/note/date into a `PoemChrome`.

On the body string, not the block IR: a sign-off fused as a stanza's last
hard-break line is still its own line here, so stripping never eats verse.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

# `Сергей Панкратиус` before the bare surname so the longer match wins.
_PERSONA_RE = re.compile(r"Сергей\s+Панкратиус|Панкратиус|Светозар|Pankratius|Svetozar")

_RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_EN_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

_DOTTED_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})\b")
# No trailing `\b`: `2026г.` runs the year straight into `г` with no boundary.
_RU_LONG_RE = re.compile(r"\b(\d{1,2})\s+([а-яё]+)\s+(\d{4})", re.IGNORECASE)
_EN_LONG_RE = re.compile(r"\b([A-Za-z]+)\.?\s+(\d{1,2}),\s*(\d{4})\b")
# The English timestamp's clock (`11:10 PM`): cruft the sign-off test must remove.
_EN_CLOCK_RE = re.compile(r"\b\d{1,2}:\d{2}\s*(?:AM|PM)\b", re.IGNORECASE)

_STYLE_NOTE_RE = re.compile(r"\(\s*(в\s+(?:духе|стиле)\s+[^)]+?)\s*\)", re.IGNORECASE)
# A standalone byline link (a leading line that is only a link) — host-agnostic.
_BYLINE_RE = re.compile(r"^\[[^\]]*\]\(\s*https?://[^)]+\)\s*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class PoemPersona:
    value: str  # "Светозар" / "Панкратиус"

    def __post_init__(self) -> None:
        if self.value not in {"Светозар", "Панкратиус"}:
            raise ValueError(f"unsupported poem persona {self.value!r}")


@dataclass(frozen=True, slots=True)
class PoemStyleNote:
    value: str  # "в духе Есенина"


@dataclass(frozen=True, slots=True)
class PoemSourceDate:
    value: str  # ISO date parsed from the sign-off

    def __post_init__(self) -> None:
        try:
            date.fromisoformat(self.value)
        except ValueError as exc:
            raise ValueError(f"source date must be ISO YYYY-MM-DD: {self.value!r}") from exc


type PoemChromeFact = PoemPersona | PoemStyleNote | PoemSourceDate

_FACT_ORDER = {
    PoemPersona: 0,
    PoemStyleNote: 1,
    PoemSourceDate: 2,
}


@dataclass(frozen=True)
class PoemChrome:
    """Metadata facts lifted out of a poem body; a clean poem has no facts."""

    facts: tuple[PoemChromeFact, ...] = ()

    def __post_init__(self) -> None:
        seen: set[type[PoemPersona] | type[PoemStyleNote] | type[PoemSourceDate]] = set()
        for fact in self.facts:
            fact_type = type(fact)
            if fact_type in seen:
                raise ValueError(f"duplicate poem chrome fact {fact_type.__name__}")
            seen.add(fact_type)
        object.__setattr__(
            self,
            "facts",
            tuple(sorted(self.facts, key=lambda fact: _FACT_ORDER[type(fact)])),
        )

    @property
    def persona_fact(self) -> PoemPersona | None:
        for fact in self.facts:
            if isinstance(fact, PoemPersona):
                return fact
        return None

    @property
    def style_note_fact(self) -> PoemStyleNote | None:
        for fact in self.facts:
            if isinstance(fact, PoemStyleNote):
                return fact
        return None

    @property
    def source_date_fact(self) -> PoemSourceDate | None:
        for fact in self.facts:
            if isinstance(fact, PoemSourceDate):
                return fact
        return None


def _add_chrome_fact(facts: list[PoemChromeFact], fact: PoemChromeFact | None) -> None:
    if fact is None:
        return
    for existing in facts:
        if type(existing) is not type(fact):
            continue
        if existing == fact:
            return
        raise ValueError(
            f"conflicting poem chrome fact {type(fact).__name__}: "
            f"{existing.value!r} vs {fact.value!r}"
        )
    facts.append(fact)


def _add_signoff_facts(facts: list[PoemChromeFact], line: str) -> None:
    if parsed := parse_signoff_date(line):
        _add_chrome_fact(facts, PoemSourceDate(parsed))
    if persona := persona_of(line):
        _add_chrome_fact(facts, PoemPersona(persona))


def parse_signoff_date(text: str) -> str | None:
    """Parse the first date in a sign-off line to ISO `YYYY-MM-DD`, or `None`."""
    if m := _DOTTED_RE.search(text):
        day, month, year = int(m[1]), int(m[2]), int(m[3])
        if len(m[3]) == 2:
            year += 2000
        return _iso(year, month, day)
    if m := _RU_LONG_RE.search(text):
        month = _RU_MONTHS.get(m[2].lower())
        return _iso(int(m[3]), month, int(m[1])) if month else None
    if m := _EN_LONG_RE.search(text):
        month = _EN_MONTHS.get(m[1].lower())
        return _iso(int(m[3]), month, int(m[2])) if month else None
    return None


def _iso(year: int, month: int, day: int) -> str | None:
    """A real calendar date as ISO, or `None` — `date` rejects e.g. 31.02."""
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def persona_of(text: str) -> str | None:
    """The canonical pen name named in a line, or `None`."""
    if not (m := _PERSONA_RE.search(text)):
        return None
    hit = m.group(0).lower()
    return "Светозар" if hit in {"светозар", "svetozar"} else "Панкратиус"


def _is_signoff_line(line: str) -> bool:
    """A publication / sign-off line: it carries a date, and once the date, clock,
    an optional pen name, emphasis markup, and punctuation are removed, nothing is
    left. The emphasis markers cover the unified `*DD.MM.YYYY, <name>*` sign-off."""
    rest = line.strip()
    if not (_DOTTED_RE.search(rest) or _RU_LONG_RE.search(rest) or _EN_LONG_RE.search(rest)):
        return False
    for pat in (_DOTTED_RE, _RU_LONG_RE, _EN_LONG_RE, _EN_CLOCK_RE, _PERSONA_RE):
        rest = pat.sub("", rest)
    return re.sub(r"[.,\sг—–*_-]", "", rest, flags=re.IGNORECASE) == ""


def _leading_style_note(line: str) -> str | None:
    """The note in a title husk like `**Весна (в духе Есенина)**`. Gated on `*` so a
    bare verse line that parenthesizes is never mistaken for chrome."""
    if "*" not in line or not (m := _STYLE_NOTE_RE.search(line)):
        return None
    return m.group(1).strip()


def clean_poem_chrome(body: str) -> tuple[str, PoemChrome]:
    """Return `(verse-only body, PoemChrome)`. Strips a leading timestamp, byline
    link, or style-note title line, and trailing sign-off / timestamp lines; lifts
    persona, note, and sign-off date. The verse between is untouched."""
    lines = body.split("\n")
    facts: list[PoemChromeFact] = []

    while lines:
        head = lines[0].strip()
        if head == "":
            lines.pop(0)
        elif _is_signoff_line(head):
            _add_signoff_facts(facts, head)
            lines.pop(0)
        elif _BYLINE_RE.match(head):
            if persona := persona_of(head):
                _add_chrome_fact(facts, PoemPersona(persona))
            lines.pop(0)
        elif (found := _leading_style_note(head)) is not None:
            _add_chrome_fact(facts, PoemStyleNote(found))
            lines.pop(0)
        else:
            break

    while lines:
        tail = lines[-1].strip()
        if tail == "":
            lines.pop()
        elif _is_signoff_line(tail):
            _add_signoff_facts(facts, tail)
            lines.pop()
        else:
            break

    # `rstrip` clears the now-dangling hard break left on the final verse line.
    cleaned = "\n".join(lines).rstrip()
    cleaned = f"{cleaned}\n" if cleaned else ""
    return cleaned, PoemChrome(tuple(facts))
