"""Unit tests for `pancratius.poem_chrome` — stihi.ru chrome stripping.

Cases are drawn verbatim from the committed corpus: every real sign-off, timestamp,
style note, and byline must be recognised, and every kept line (bold subtitles 04/39,
the Frost companion stanza in 06, ordinary verse) must be left alone. The fused
sign-offs (05 multi-line stanza, 08 single block) are the ones a block-level stripper
would have eaten; here they are just the last physical line.
"""

from __future__ import annotations

import pytest

from pancratius.poem_chrome import (
    PoemChrome,
    PoemPersona,
    PoemSourceDate,
    PoemStyleNote,
    clean_poem_chrome,
    parse_signoff_date,
    persona_of,
)
from pancratius.poem_chrome import _is_signoff_line as is_signoff_line
from pancratius.poem_chrome import _leading_style_note as leading_style_note

# Every trailing sign-off / publication line in the corpus → its parsed ISO date.
SIGNOFFS: list[tuple[str, str]] = [
    ("09.02.2025 Сергей Панкратиус", "2025-02-09"),
    ("Светозар 14.03.2025", "2025-03-14"),
    ("10.08.2026", "2026-08-10"),
    ("15.12.2024 г.", "2024-12-15"),
    ("22.10.2025", "2025-10-22"),
    ("14.03.25", "2025-03-14"),
    ("Панкратиус. 25.09.2024", "2024-09-25"),
    ("22.10.25", "2025-10-22"),
    ("10 марта 2026г.", "2026-03-10"),
    ("25.12.2024", "2024-12-25"),
    ("24.10.2024", "2024-10-24"),
    ("March 14, 2025 11:10 PM", "2025-03-14"),
    ("March 15, 2025 1:29 AM", "2025-03-15"),
    ("March 15, 2026", "2026-03-15"),
]

# Lines that must survive — bold subtitles (04/39), Frost (06), ordinary verse.
KEEP_LINES = [
    "Сон Бога",
    "**Сон Бога**",
    "Фиолетовая пудра: в ней Ты и я",
    "Я помню чудное мгновение —",
    "Пора… пора… да, вы все здесь,",
    "And that has made all the difference.",
    "I took the one less traveled by,",
    "Боже, Боже милый, сердце мне открой,",
]


@pytest.mark.parametrize(("line", "iso"), SIGNOFFS)
def test_signoff_recognised_and_dated(line: str, iso: str) -> None:
    assert is_signoff_line(line)
    assert parse_signoff_date(line) == iso


@pytest.mark.parametrize("line", KEEP_LINES)
def test_verse_is_not_a_signoff(line: str) -> None:
    assert not is_signoff_line(line)
    assert leading_style_note(line) is None


def test_persona_canonicalised() -> None:
    assert persona_of("Светозар 14.03.2025") == "Светозар"
    assert persona_of("09.02.2025 Сергей Панкратиус") == "Панкратиус"
    assert persona_of("Панкратиус. 25.09.2024") == "Панкратиус"
    assert persona_of("22.10.2025") is None


@pytest.mark.parametrize(
    ("line", "note"),
    [
        ("**Весна (в духе Есенина)**", "в духе Есенина"),
        (r"Грачи прилетели\*\* \*(в стиле Есенина)", "в стиле Есенина"),
        (r"Русь святая\*\* \*(в духе Есенина)", "в духе Есенина"),
    ],
)
def test_style_note_extracted(line: str, note: str) -> None:
    assert leading_style_note(line) == note


# ─── whole-body integration, from real poem shapes ──────────────────────────

def test_fused_multiline_signoff_keeps_verse() -> None:
    """05: sign-off is the last hard-break line of the final stanza (no blank before
    it). A block-level drop would delete four verse lines; the line stripper keeps them."""
    body = (
        "Боже, Боже милый, я Тебе вверяюсь,  \n"
        "Свет Твой в сердце вечен, не угаснет он.  \n"
        "И с Тобой иду я, больше не теряюсь,  \n"
        "В вечности Твоей в Свете я рождён!!!  \n"
        "09.02.2025 Сергей Панкратиус\n"
    )
    cleaned, chrome = clean_poem_chrome(body)
    assert cleaned == (
        "Боже, Боже милый, я Тебе вверяюсь,  \n"
        "Свет Твой в сердце вечен, не угаснет он.  \n"
        "И с Тобой иду я, больше не теряюсь,  \n"
        "В вечности Твоей в Свете я рождён!!!\n"
    )
    assert chrome == PoemChrome((
        PoemPersona("Панкратиус"),
        PoemSourceDate("2025-02-09"),
    ))


def test_single_block_signoff() -> None:
    """08: the whole poem is one block ending in the sign-off line."""
    body = "Не угасает свет звезды,  \nОн за пределами теченья.\nСветозар 14.03.2025\n"
    cleaned, chrome = clean_poem_chrome(body)
    assert cleaned == "Не угасает свет звезды,  \nОн за пределами теченья.\n"
    assert chrome.persona_fact == PoemPersona("Светозар")
    assert chrome.source_date_fact == PoemSourceDate("2025-03-14")


def test_leading_timestamp_keeps_bold_subtitle() -> None:
    """04: strip the leading timestamp; keep the bold subtitle `**Сон Бога**`."""
    body = "March 14, 2025 11:10 PM  \n**Сон Бога**\n\nБог видит сон сквозь этот мир,\n"
    cleaned, chrome = clean_poem_chrome(body)
    assert cleaned == "**Сон Бога**\n\nБог видит сон сквозь этот мир,\n"
    assert chrome.source_date_fact == PoemSourceDate("2025-03-14")


def test_leading_style_note_and_trailing_timestamp() -> None:
    """07: lift the style note from the leading title husk; drop the trailing stamp."""
    body = "**Весна (в духе Есенина)**\nСквозь сон берёзовых аллей,\nMarch 15, 2025 1:29 AM\n"
    cleaned, chrome = clean_poem_chrome(body)
    assert cleaned == "Сквозь сон берёзовых аллей,\n"
    assert chrome == PoemChrome((
        PoemStyleNote("в духе Есенина"),
        PoemSourceDate("2025-03-15"),
    ))


def test_poem_chrome_rejects_duplicate_fact_kind() -> None:
    with pytest.raises(ValueError, match="duplicate poem chrome fact"):
        PoemChrome((
            PoemPersona("Панкратиус"),
            PoemPersona("Светозар"),
        ))


def test_poem_source_date_is_validated() -> None:
    with pytest.raises(ValueError, match="source date must be ISO"):
        PoemSourceDate("15.03.2025")


def test_conflicting_signoff_dates_are_not_silently_collapsed() -> None:
    body = "March 14, 2025 11:10 PM\nСвет идёт.\nMarch 15, 2025 1:29 AM\n"
    with pytest.raises(ValueError, match="conflicting poem chrome fact PoemSourceDate"):
        clean_poem_chrome(body)


def test_leading_byline_link_stripped_any_host() -> None:
    body = "[Сергей Панкратиус](https://example.org/avtor)\nстих о свете,\n"
    cleaned, chrome = clean_poem_chrome(body)
    assert cleaned == "стих о свете,\n"
    assert chrome.persona_fact == PoemPersona("Панкратиус")


def test_inline_link_in_verse_kept() -> None:
    body = "свет [ведёт](https://x.org) меня,\nи тьма ушла.\n"
    cleaned, _ = clean_poem_chrome(body)
    assert cleaned == body


def test_clean_poem_untouched() -> None:
    body = "Я свет, что в тишине парит,\nЕго никто не замечает.\n"
    cleaned, chrome = clean_poem_chrome(body)
    assert cleaned == body
    assert chrome == PoemChrome()


def test_idempotent() -> None:
    body = "March 14, 2025 11:10 PM  \nстих,\nСветозар 14.03.2025\n"
    once, _ = clean_poem_chrome(body)
    twice, chrome = clean_poem_chrome(once)
    assert twice == once == "стих,\n"
    assert chrome == PoemChrome()
