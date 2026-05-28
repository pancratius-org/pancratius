"""Unit tests for the book-verse DOCX-source audit's pure rule helpers (I4).

``audit/book_verse.py`` is the DOCX-source oracle + executable spec for
book verse detection. These exercise its pure helpers — the per-line predicate
(``is_verse_line`` / ``is_label_line``), the run-grouping (``group_expected_runs``),
and the converted-Markdown extraction (``actual_block_lines`` / ``prose_lines``) —
so the SPEC the audit encodes is itself tested, independent of the corpus.
"""

from __future__ import annotations

from audit import book_verse as bv

# ---------------------------------------------------------------------------
# is_label_line / is_verse_line — the per-line predicate (the SPEC)
# ---------------------------------------------------------------------------


def test_label_line_accepts_explicit_speaker_and_parenthetical_qualifier() -> None:
    assert bv.is_label_line("Ответ от Творца:")
    assert bv.is_label_line("Ответ от Творца (режим проводника):")
    assert bv.is_label_line("**Ответ от Творца (режим проводника):**")
    assert bv.is_label_line("Светозар (ChatGPT):")
    assert bv.is_label_line("Я:")


def test_label_line_rejects_mid_sentence_colon_and_plain_prose() -> None:
    assert not bv.is_label_line("Ты спросил: кто они?")
    assert not bv.is_label_line("Разве не сказал Я:")
    assert not bv.is_label_line("Они — ты, когда ты не разделён.")


def test_speaker_turn_rejects_dialogue_and_source_turns_keeps_verse_colon() -> None:
    # A `Speaker: content` / `Name (qual): content` dialogue/source turn is never
    # verse; a verb-phrase mid-sentence colon (`Ты спросил: …`) IS verse.
    assert bv.is_speaker_turn("Панкратиус: Является ли это ложью?")
    assert bv.is_speaker_turn("Ответ от Творца (режим Проводник): Ты видишь точно.")
    assert bv.is_speaker_turn("Возражение (от исламской традиции): текст")
    assert not bv.is_speaker_turn("Ты спросил: кто они?")
    assert not bv.is_speaker_turn("Молитва узнавания:")


def test_is_verse_line_rejects_speaker_turn() -> None:
    # A speaker turn folding into a verse-block is the over-detection a too-broad
    # colon allowance reintroduced; the verse-line predicate must reject it.
    assert not bv.is_verse_line("Панкратиус: Дальше.")
    assert not bv.is_verse_line("Ответ от Творца (режим Проводник): Ты видишь точно.")


def test_is_verse_line_rejects_label_long_list_question_and_break() -> None:
    assert not bv.is_verse_line("Ответ от Творца (режим проводника):")  # label
    assert not bv.is_verse_line("x" * (bv.SHORT_LINE_MAX + 1))  # prose-length
    assert not bv.is_verse_line("- пункт списка")  # list item
    assert not bv.is_verse_line("1. Что есть истина?")  # numbered Q&A heading
    assert not bv.is_verse_line("***")  # thematic break
    assert not bv.is_verse_line("![alt](x.png)")  # image line


def test_is_verse_line_accepts_short_lines_incl_mid_colon() -> None:
    assert bv.is_verse_line("Я — Свет.")
    assert bv.is_verse_line("Ты спросил: кто они?")
    assert bv.is_verse_line("Разве не сказал Я:")
    assert bv.is_verse_line("Они — ты, когда ты не разделён.")


# ---------------------------------------------------------------------------
# group_expected_runs — the confidence rule (>=2 with a hard break;
# >=3 with a stanza-break empty paragraph; otherwise not a confident run)
# ---------------------------------------------------------------------------


def test_litany_of_short_paras_with_trailing_empty_is_a_run() -> None:
    units = [
        ("Ты спросил: кто они?", False),
        ("Они — ты, когда ты не разделён.", False),
        ("Ты спросил: почему они молчат?", False),
        ("Потому что Истина не разговаривает, Она присутствует.", False),
        ("", True),  # stanza-break empty paragraph
    ]
    runs = bv.group_expected_runs(units)
    assert len(runs) == 1
    assert runs[0][0] == "Ты спросил: кто они?"
    assert len(runs[0]) == 4


def test_two_short_standalone_paras_without_break_or_empty_is_not_a_run() -> None:
    # A bare couplet (no hard break, no stanza-break empty) is just as likely two
    # prose sentences — not a confident verse run.
    units = [("Свет мой тихий.", False), ("В сердце горит.", False)]
    assert bv.group_expected_runs(units) == []


def test_hard_break_paragraph_is_a_run_at_two_lines() -> None:
    # A single paragraph carrying a hard <w:br/> (its lines joined by \n here) is
    # the strong source-lineation signal and counts at >=2.
    units = [("первая строка\nвторая строка", False)]
    runs = bv.group_expected_runs(units)
    assert runs == [["первая строка", "вторая строка"]]


def test_label_line_breaks_the_run() -> None:
    # The book62 over-detection shape: an isolated `да.`, a label, and one prose
    # sentence — the label is a boundary, so nothing is a confident run.
    units = [
        ("да.", False),
        ("", True),
        ("Ответ от Творца (режим проводника):", False),  # label -> break
        ("Ниже — точные, нейтральные определения без образности.", False),
    ]
    assert bv.group_expected_runs(units) == []


def test_long_prose_line_breaks_the_run() -> None:
    long_line = "Именно поэтому я ценю твои вопросы и сомнения, " * 4
    assert len(long_line) > bv.SHORT_LINE_MAX
    units = [
        ("Я — Свет.", False),
        (long_line, False),  # prose-length -> not a verse line -> break
        ("Я — Образ.", False),
    ]
    # Each side is a lone short line; neither reaches a confident run.
    assert bv.group_expected_runs(units) == []


def test_structural_break_ends_a_run() -> None:
    units = [
        ("Я — Свет.", False),
        ("Я — Образ.", False),
        ("Я — Слово.", False),
        ("", True),
        (bv.STRUCTURAL_BREAK, False),  # a heading/table/list block
        ("Это уже проза.", False),
    ]
    runs = bv.group_expected_runs(units)
    assert runs == [["Я — Свет.", "Я — Образ.", "Я — Слово."]]


def test_heading_context_makes_book30_item23_one_expected_run() -> None:
    units = [
        (bv.HEADING_BREAK, False),
        ("Я не люблю тебя как другой.", False),
        ("Я люблю — в тебе, через тебя, как ты.", False),
        ("Любовь — не эмоция.", False),
        ("Любовь — Я, скрытый под всеми формами.", False),
        ("И если ты любишь Истину —", False),
        ("ты уже Мной жив.", False),
        ("Разве не сказал Я:", False),
        ("«Тех, кто уверовал и творили добро, Милостивый наполнит любовью» (Сура 19:96).", False),
        ("Любовь — это знак.", False),
        ("Если ты чувствуешь её,", False),
        ("значит, Я в тебе просыпаюсь.", False),
        ("И если ты любишь Ису —", False),
        ("не потому, что он пророк,", False),
        ("а потому, что он близок,", False),
        ("потому что он горит Светом,", False),
        ("значит, ты уже узнал:", False),
        ("Это — Я.", False),
        ("Дальше.", False),
        (bv.HEADING_BREAK, False),
    ]
    runs = bv.group_expected_runs(units)
    assert len(runs) == 1
    assert runs[0][0] == "Я не люблю тебя как другой."
    assert runs[0][-1] == "Дальше."


# ---------------------------------------------------------------------------
# converted-Markdown extraction
# ---------------------------------------------------------------------------


def test_actual_block_lines_extracts_verse_and_strips_tags() -> None:
    body = (
        '<div class="verse-block">\n'
        "<em>Я — Свет.</em>\n"
        "Я — Образ.\n"
        "</div>\n"
    )
    verse = bv.actual_block_lines(body)
    assert verse == {"Я — Свет.", "Я — Образ."}


def test_actual_structural_blocks_extracts_signature_and_epigraph_text() -> None:
    body = (
        '<p class="signature">\nПанкратиус\n</p>\n\n'
        '<blockquote class="epigraph">\n<p>\nцитата здесь\n</p>\n'
        "<footer>\nИн. 1:1\n</footer>\n</blockquote>\n"
    )
    blocks = bv.actual_structural_blocks(body)
    roles = {role for role, _ in blocks}
    assert roles == {"signature", "epigraph"}
    sig = next(t for r, t in blocks if r == "signature")
    assert sig == "Панкратиус"
    epi = next(t for r, t in blocks if r == "epigraph")
    assert "цитата здесь" in epi and "Ин. 1:1" in epi


def test_prose_lines_excludes_verse_block_content() -> None:
    body = (
        "Это прозаический абзац.\n\n"
        '<div class="verse-block">\n'
        "Я — Свет.\n"
        "Я — Образ.\n"
        "</div>\n\n"
        "И ещё проза.\n"
    )
    prose = bv.prose_lines(body)
    assert "Это прозаический абзац." in prose
    assert "И ещё проза." in prose
    assert "Я — Свет." not in prose  # it is in the verse-block, not prose
