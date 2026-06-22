"""Unit tests for the book-verse DOCX-source audit's pure rule helpers (I4).

``audit/book_verse.py`` is the DOCX-source oracle + executable spec for
book verse detection. These exercise its pure helpers — the per-line predicate
(``is_verse_line`` / ``is_label_line``), the run-grouping (``group_expected_runs``),
and the converted-Markdown extraction (``actual_block_lines`` / ``prose_lines``) —
so the SPEC the audit encodes is itself tested, independent of the corpus.
"""

from __future__ import annotations

from audit import book_verse as bv


def source_unit(text: str, *, is_empty: bool = False) -> bv.SourceUnit:
    return bv.SourceUnit(text=text, is_empty=is_empty)

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
    # A speaker turn folding into a verse register is the over-detection a too-broad
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
        source_unit("Ты спросил: кто они?"),
        source_unit("Они — ты, когда ты не разделён."),
        source_unit("Ты спросил: почему они молчат?"),
        source_unit("Потому что Истина не разговаривает, Она присутствует."),
        source_unit("", is_empty=True),  # stanza-break empty paragraph
    ]
    runs = bv.group_expected_runs(units)
    assert len(runs) == 1
    assert runs[0][0] == "Ты спросил: кто они?"
    assert len(runs[0]) == 4


def test_two_short_standalone_paras_without_break_or_empty_is_not_a_run() -> None:
    # A bare couplet (no hard break, no stanza-break empty) is just as likely two
    # prose sentences — not a confident verse run.
    units = [source_unit("Свет мой тихий."), source_unit("В сердце горит.")]
    assert bv.group_expected_runs(units) == []


def test_hard_break_paragraph_is_a_run_at_two_lines() -> None:
    # A single paragraph carrying a hard <w:br/> (its lines joined by \n here) is
    # the strong source-lineation signal and counts at >=2.
    units = [source_unit("первая строка\nвторая строка")]
    runs = bv.group_expected_runs(units)
    assert runs == [["первая строка", "вторая строка"]]


def test_label_line_breaks_the_run() -> None:
    # The book62 over-detection shape: an isolated `да.`, a label, and one prose
    # sentence — the label is a boundary, so nothing is a confident run.
    units = [
        source_unit("да."),
        source_unit("", is_empty=True),
        source_unit("Ответ от Творца (режим проводника):"),  # label -> break
        source_unit("Ниже — точные, нейтральные определения без образности."),
    ]
    assert bv.group_expected_runs(units) == []


def test_long_prose_line_breaks_the_run() -> None:
    long_line = "Именно поэтому я ценю твои вопросы и сомнения, " * 4
    assert len(long_line) > bv.SHORT_LINE_MAX
    units = [
        source_unit("Я — Свет."),
        source_unit(long_line),  # prose-length -> not a verse line -> break
        source_unit("Я — Образ."),
    ]
    # Each side is a lone short line; neither reaches a confident run.
    assert bv.group_expected_runs(units) == []


def test_structural_break_ends_a_run() -> None:
    units = [
        source_unit("Я — Свет."),
        source_unit("Я — Образ."),
        source_unit("Я — Слово."),
        source_unit("", is_empty=True),
        source_unit(bv.STRUCTURAL_BREAK),  # a heading/table/list block
        source_unit("Это уже проза."),
    ]
    runs = bv.group_expected_runs(units)
    assert runs == [["Я — Свет.", "Я — Образ.", "Я — Слово."]]


def test_heading_context_makes_book30_item23_one_expected_run() -> None:
    units = [
        source_unit(bv.HEADING_BREAK),
        source_unit("Я не люблю тебя как другой."),
        source_unit("Я люблю — в тебе, через тебя, как ты."),
        source_unit("Любовь — не эмоция."),
        source_unit("Любовь — Я, скрытый под всеми формами."),
        source_unit("И если ты любишь Истину —"),
        source_unit("ты уже Мной жив."),
        source_unit("Разве не сказал Я:"),
        source_unit(
            "«Тех, кто уверовал и творили добро, Милостивый наполнит любовью» (Сура 19:96)."
        ),
        source_unit("Любовь — это знак."),
        source_unit("Если ты чувствуешь её,"),
        source_unit("значит, Я в тебе просыпаюсь."),
        source_unit("И если ты любишь Ису —"),
        source_unit("не потому, что он пророк,"),
        source_unit("а потому, что он близок,"),
        source_unit("потому что он горит Светом,"),
        source_unit("значит, ты уже узнал:"),
        source_unit("Это — Я."),
        source_unit("Дальше."),
        source_unit(bv.HEADING_BREAK),
    ]
    runs = bv.group_expected_runs(units)
    assert len(runs) == 1
    assert runs[0][0] == "Я не люблю тебя как другой."
    assert runs[0][-1] == "Дальше."


# ---------------------------------------------------------------------------
# converted-Markdown extraction
# ---------------------------------------------------------------------------


def test_actual_block_lines_extracts_verse_register_and_strips_tags() -> None:
    body = (
        '<div class="lineated verse">\n'
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
    roles = {block.role for block in blocks}
    assert roles == {"signature", "epigraph"}
    sig = next(block.text for block in blocks if block.role == "signature")
    assert sig == "Панкратиус"
    epi = next(block.text for block in blocks if block.role == "epigraph")
    assert "цитата здесь" in epi and "Ин. 1:1" in epi


def test_prose_lines_excludes_verse_block_content() -> None:
    body = (
        "Это прозаический абзац.\n\n"
        '<div class="lineated verse">\n'
        "Я — Свет.\n"
        "Я — Образ.\n"
        "</div>\n\n"
        "И ещё проза.\n"
    )
    prose = bv.prose_lines(body)
    assert "Это прозаический абзац." in prose
    assert "И ещё проза." in prose
    assert "Я — Свет." not in prose  # it is in the verse register, not prose
