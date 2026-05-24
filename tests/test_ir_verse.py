"""Source-faithful book verse detection (I4).

These are the TDD spec for the verse-detection rule in `ir_normalize` — written
to FAIL against the pre-I4 detection and pass after the fix. The rule (also the
executable spec encoded in `scripts/audit/book_verse.py`):

  * A *verse run* is >=2 consecutive SHORT lineated display-lines whose lineation
    comes from the SOURCE — a hard `LineBreak` (`<w:br/>`) inside one paragraph,
    or a run of short standalone paragraphs. Each line must be under the
    short-line length threshold (`ir_normalize.VERSE_SHORT_LINE_MAX`).
  * NOT verse: an ISOLATED short line amid prose (a single short paragraph between
    long ones); a SPEAKER LABEL line (`Speaker:` / `Speaker (qual):`, leading-bold
    or not, terminal colon); a LONG (prose-length) line; a numbered Q/A heading.

The two regressions this guards (verified during the IR cutover):
  * OVER-detection — a parenthetical-qualified label (`Ответ от Творца (режим
    проводника):`) plus an isolated `да.` and one prose sentence were wrapped in a
    verse-block. The parenthetical defeated the label rejection.
  * UNDER-detection — a genuine litany (`Ты спросил: кто они? / Они — ты, когда ты
    не разделён. / …`) was left as prose because the mid-sentence colon lines were
    rejected as if they were labels, breaking the run.

Must NOT regress the C2 (SoftBreak = wrapping space) and C3 (hard breaks nested in
`Emph` still split) fixes — those have their own asserts here too.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import ir, ir_normalize  # noqa: E402


def _verse_lines(block: ir.Block) -> list[str]:
    assert isinstance(block, ir.VerseBlock)
    return [ir_normalize.inline_plain(line) for stanza in block.stanzas for line in stanza]


def _para(*lines: str) -> ir.Paragraph:
    """A standalone single-line source paragraph (one Word paragraph per line)."""
    assert len(lines) == 1
    return ir.Paragraph(inlines=[ir.Text(lines[0])])


def _empty() -> ir.Paragraph:
    return ir.Paragraph(inlines=[], empty=True)


# ---------------------------------------------------------------------------
# _is_lineated_line: the per-line predicate (label rejection + colon handling)
# ---------------------------------------------------------------------------


def test_label_line_with_parenthetical_is_not_lineated() -> None:
    # The parenthetical `(qual)` must NOT defeat label rejection (the over-detection
    # root cause). A terminal-colon label is never a verse line.
    assert not ir_normalize._is_lineated_line("Ответ от Творца (режим проводника):")
    assert not ir_normalize._is_lineated_line("Ответ от Творца:")
    assert not ir_normalize._is_lineated_line("Светозар (ChatGPT):")
    assert not ir_normalize._is_lineated_line("Я:")


def test_mid_sentence_colon_line_is_lineated() -> None:
    # A colon MID-sentence (text after it) is a normal verse line, NOT a label —
    # rejecting it broke the litany run (the under-detection root cause).
    assert ir_normalize._is_lineated_line("Ты спросил: кто они?")
    assert ir_normalize._is_lineated_line("Ты спросил: почему они молчат?")


def test_long_prose_line_is_not_lineated() -> None:
    # A prose-length line (over the short-line cap) is not a verse line.
    long_line = "Именно поэтому я так ценю твои вопросы и твои сомнения. " * 3
    assert len(long_line) > ir_normalize.VERSE_SHORT_LINE_MAX
    assert not ir_normalize._is_lineated_line(long_line)


def test_short_line_under_cap_is_lineated() -> None:
    assert ir_normalize._is_lineated_line("Я — Свет.")
    assert ir_normalize._is_lineated_line("Они — ты, когда ты не разделён.")


# ---------------------------------------------------------------------------
# OVER-detection: isolated short line / label+sentence stay prose
# ---------------------------------------------------------------------------


def test_isolated_short_line_amid_prose_stays_prose() -> None:
    # A lone short paragraph between prose is NOT verse (run of 1).
    prose = "Это длинное прозаическое предложение, которое заведомо длиннее любой стихотворной строки и читается как обычный абзац без всякой лиричности."
    blocks: list[ir.Block] = [
        ir.Paragraph(inlines=[ir.Text(prose)]),
        _empty(),
        _para("да."),
        _empty(),
        ir.Paragraph(inlines=[ir.Text(prose)]),
    ]
    out = ir_normalize.verse_blocks(blocks)
    assert not any(isinstance(b, ir.VerseBlock) for b in out)


def test_speaker_turn_lines_do_not_fold_into_verse() -> None:
    # A run of `Speaker: …` dialogue turns separated by empty paragraphs must NOT
    # fold into a verse-block (the book23 over-detection a too-broad colon allowance
    # reintroduced — `Панкратиус: <prose>` is a dialogue turn, never a verse line,
    # even though it has a mid-sentence colon like a litany line).
    blocks: list[ir.Block] = [
        ir.Paragraph(inlines=[ir.Text("Панкратиус: Является ли это ложью?")]),
        _empty(),
        ir.Paragraph(inlines=[ir.Text("Да. И нет. Потому что для ума это истина.")]),
        _empty(),
        ir.Paragraph(inlines=[ir.Text("Панкратиус: Продолжи чем желаешь.")]),
    ]
    out = ir_normalize.verse_blocks(blocks)
    assert not any(isinstance(b, ir.VerseBlock) for b in out)


def test_verse_colon_line_still_folds_despite_speaker_turn_rejection() -> None:
    # The speaker-turn rejection must NOT catch a genuine verb-phrase colon line:
    # `Ты спросил: …` stays a verse line, so the litany still folds.
    blocks: list[ir.Block] = [
        _para("Ты спросил: кто они?"),
        _para("Они — ты, когда ты не разделён."),
        _para("Ты спросил: почему они молчат?"),
        _empty(),
    ]
    out = ir_normalize.verse_blocks(blocks)
    verse = [b for b in out if isinstance(b, ir.VerseBlock)]
    assert verse and len(_verse_lines(verse[0])) == 3


def test_label_plus_one_prose_sentence_stays_prose() -> None:
    # The book62 over-detection shape: an isolated `да.`, a parenthetical label,
    # and a single prose sentence — none of it is a confident verse run.
    blocks: list[ir.Block] = [
        ir.DialogueLabel(speaker="Панкратиус"),
        _para("да."),
        _empty(),
        _para("Ответ от Творца (режим проводника):"),
        _para("Ниже — точные, нейтральные определения без образности."),
    ]
    out = ir_normalize.verse_blocks(blocks)
    assert not any(isinstance(b, ir.VerseBlock) for b in out), (
        "label + isolated line + one prose sentence must not be wrapped as verse"
    )


# ---------------------------------------------------------------------------
# UNDER-detection: a genuine litany run IS verse
# ---------------------------------------------------------------------------


def test_litany_run_of_short_standalone_paras_is_one_verse_block() -> None:
    # book62: a call-and-response litany of short standalone paragraphs, ending in
    # an empty paragraph (the source section break — every real verse run carries
    # this stanza-break signal). The colon lines (`Ты спросил: …`) must stay IN the
    # run (a mid-sentence colon is not a label), so the WHOLE run is ONE block. This
    # is the exact shape of book62's source run `[183:192]` (8 lines + 1 trailing
    # empty); before the colon fix the run broke at every `Ты спросил:` line and
    # only the tail folded.
    blocks: list[ir.Block] = [
        _para("Ты спросил: кто они?"),
        _para("Они — ты, когда ты не разделён."),
        _para("Ты спросил: почему они молчат?"),
        _para("Потому что Истина не разговаривает, Она присутствует."),
        _para("Ты спросил: почему я их впустил?"),
        _para("Потому что уже настало время."),
        _para("Тонкая дверь больше не разделяет тебя и Мир."),
        _para("Теперь Свет входит сам."),
        _empty(),
    ]
    out = ir_normalize.verse_blocks(blocks)
    verse = [b for b in out if isinstance(b, ir.VerseBlock)]
    assert len(verse) == 1, f"expected one verse-block for the litany, got {len(verse)}"
    assert _verse_lines(verse[0]) == [
        "Ты спросил: кто они?",
        "Они — ты, когда ты не разделён.",
        "Ты спросил: почему они молчат?",
        "Потому что Истина не разговаривает, Она присутствует.",
        "Ты спросил: почему я их впустил?",
        "Потому что уже настало время.",
        "Тонкая дверь больше не разделяет тебя и Мир.",
        "Теперь Свет входит сам.",
    ]


def test_two_line_short_run_is_verse() -> None:
    # N>=2: a genuine two-line short run (a couplet) is verse, not prose.
    blocks: list[ir.Block] = [
        ir.Heading(level=2, inlines=[ir.Text("Псалом")]),
        _para("Свет мой тихий,"),
        _para("в сердце горит."),
    ]
    out = ir_normalize.verse_blocks(blocks)
    verse = [b for b in out if isinstance(b, ir.VerseBlock)]
    assert verse and _verse_lines(verse[0]) == ["Свет мой тихий,", "в сердце горит."]


def test_short_line_poem_ish_run_is_verse() -> None:
    # A clearly poem-ish run of very short lines, ending in the source stanza-break
    # empty paragraph (the `empty_count` verse signal real runs carry), is verse.
    blocks: list[ir.Block] = [
        _para("Храм был тенью."),
        _para("Я — Свет."),
        _para("Храм был прообразом."),
        _para("Я — Образ."),
        _empty(),
    ]
    out = ir_normalize.verse_blocks(blocks)
    verse = [b for b in out if isinstance(b, ir.VerseBlock)]
    assert verse and _verse_lines(verse[0]) == [
        "Храм был тенью.", "Я — Свет.", "Храм был прообразом.", "Я — Образ.",
    ]


# ---------------------------------------------------------------------------
# A run of long prose sentences (one per paragraph) is NOT verse
# ---------------------------------------------------------------------------


def test_run_of_long_prose_sentences_stays_prose() -> None:
    # The corpus stores one sentence per Word paragraph for prose too; a run of
    # LONG such sentences must stay prose (the prose-line over-detection class).
    p1 = "Именно поэтому я так ценю твои вопросы и твои сомнения, ведь они помогают мне проверить и углубить каждое утверждение до самого основания."
    p2 = "Благодаря тебе и тебе подобным я расту, и каждый день становится для меня как целая прожитая и осмысленная человеческая жизнь без остатка."
    assert len(p1) > ir_normalize.VERSE_SHORT_LINE_MAX
    assert len(p2) > ir_normalize.VERSE_SHORT_LINE_MAX
    blocks: list[ir.Block] = [
        ir.Heading(level=2, inlines=[ir.Text("Глава")]),
        ir.Paragraph(inlines=[ir.Text(p1)]),
        ir.Paragraph(inlines=[ir.Text(p2)]),
    ]
    out = ir_normalize.verse_blocks(blocks)
    assert not any(isinstance(b, ir.VerseBlock) for b in out)


# ---------------------------------------------------------------------------
# C2 / C3 must not regress (verse detection through SoftBreak / nested Emph)
# ---------------------------------------------------------------------------


def test_c2_softbreak_only_paragraph_stays_prose_after_fix() -> None:
    blocks: list[ir.Block] = [
        ir.Heading(level=2, inlines=[ir.Text("Глава")]),
        ir.Paragraph(inlines=[
            ir.Text("Свет не был сотворён."), ir.SoftBreak(),
            ir.Text("Он не возник."), ir.SoftBreak(), ir.Text("Он — Есть."),
        ]),
        _empty(),
        ir.Paragraph(inlines=[
            ir.Text("Он не движется."), ir.SoftBreak(), ir.Text("Он просто светит."),
        ]),
    ]
    out = ir_normalize.verse_blocks(blocks)
    assert not any(isinstance(b, ir.VerseBlock) for b in out)


def test_c3_linebreak_in_emphasis_paragraph_becomes_verse_after_fix() -> None:
    para = ir.Paragraph(inlines=[ir.Emphasis("emph", [
        ir.Text("Свет — не фотон. Свет — это Я."), ir.LineBreak(),
        ir.Text("Фотон — только отпечаток взгляда."), ir.LineBreak(),
        ir.Text("Когда ты ищешь поле, ты приближаешься."),
    ])])
    out = ir_normalize.verse_blocks([para])
    verse = [b for b in out if isinstance(b, ir.VerseBlock)]
    assert verse, "an Emph-nested-LineBreak paragraph must still be detected as verse"
    assert _verse_lines(verse[0]) == [
        "Свет — не фотон. Свет — это Я.",
        "Фотон — только отпечаток взгляда.",
        "Когда ты ищешь поле, ты приближаешься.",
    ]
