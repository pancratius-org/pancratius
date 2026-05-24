"""Unit tests for the typed-IR import pipeline (ir / normalize / lower).

These exercise the pure stages directly on hand-built IR fixtures — no pandoc, no
DOCX — so they run everywhere (the corpus A/B in ``test_ir_ab_corpus`` covers the
adapter end-to-end where pandoc is available). They lock in the behaviours ported
from the GFM engine into the IR: dialogue-label canonicalization (including the
mixed-inline split the spike left unfinished), empty-emphasis artifact stripping,
the bare-bibliography-heading strip, AI-alt scrubbing via the shared production
constant, thematic breaks, verse detection, ordered-list start preservation, and
the generated footnote appendix.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import ir, ir_lower, ir_normalize  # noqa: E402
from lib.docx_engine import AI_ALT_FRAGMENTS  # noqa: E402


# Narrowing helpers: the IR Block/Inline unions need an isinstance check before a
# member access type-checks. These assert-and-return so each test reads as one line
# while staying type-sound under `ty`.
def _para(block: ir.Block) -> ir.Paragraph:
    assert isinstance(block, ir.Paragraph)
    return block


def _first_image_alt(block: ir.Block) -> str:
    para = _para(block)
    img = para.inlines[0]
    assert isinstance(img, ir.ImageInline)
    return img.alt


def _blockquote(block: ir.Block) -> ir.BlockQuote:
    assert isinstance(block, ir.BlockQuote)
    return block


# ---------------------------------------------------------------------------
# inline helpers
# ---------------------------------------------------------------------------


def test_inline_plain_flattens_emphasis_and_collapses_whitespace() -> None:
    inlines: list[ir.Inline] = [
        ir.Text("a "), ir.Emphasis("strong", [ir.Text("b")]), ir.SoftBreak(), ir.Text("c"),
    ]
    assert ir_normalize.inline_plain(inlines) == "a b c"


def test_inline_lines_splits_on_breaks() -> None:
    inlines: list[ir.Inline] = [
        ir.Text("one"), ir.LineBreak(), ir.Text("two"), ir.SoftBreak(), ir.Text("three"),
    ]
    lines = ir_normalize.inline_lines(inlines)
    assert [ir_normalize.inline_plain(ln) for ln in lines] == ["one", "two", "three"]


# ---------------------------------------------------------------------------
# dialogue labels
# ---------------------------------------------------------------------------


def test_dialogue_label_whole_strong_with_body_splits() -> None:
    para = ir.Paragraph(inlines=[ir.Emphasis("strong", [ir.Text("Панкратиус: привет")])])
    out = ir_normalize.dialogue_labels([para])
    assert isinstance(out[0], ir.DialogueLabel) and out[0].speaker == "Панкратиус"
    assert ir_normalize.inline_plain(_para(out[1]).inlines) == "привет"


def test_dialogue_label_bare_label_no_body() -> None:
    para = ir.Paragraph(inlines=[ir.Emphasis("strong", [ir.Text("Светозар:")])])
    out = ir_normalize.dialogue_labels([para])
    assert len(out) == 1 and isinstance(out[0], ir.DialogueLabel)
    assert out[0].speaker == "Светозар"


def test_dialogue_label_mixed_leading_strong_then_prose_splits() -> None:
    # The mixed-inline case the spike left unfinished: `**Speaker:**` followed by
    # trailing prose inlines in the SAME paragraph.
    para = ir.Paragraph(inlines=[
        ir.Emphasis("strong", [ir.Text("Творец:")]),
        ir.Text(" body continues "),
        ir.Emphasis("emph", [ir.Text("here")]),
    ])
    out = ir_normalize.dialogue_labels([para])
    assert isinstance(out[0], ir.DialogueLabel) and out[0].speaker == "Творец"
    assert ir_normalize.inline_plain(_para(out[1]).inlines) == "body continues here"


def test_dialogue_label_inside_body_ending_in_opening_quote_no_stray_space() -> None:
    # Nit: when the inside-body text of `**Speaker: «**` ends in an OPENING glyph,
    # joining it to the trailing inlines must NOT insert a space (`« Почему` is wrong;
    # `«Почему` is right).
    para = ir.Paragraph(inlines=[
        ir.Emphasis("strong", [ir.Text("Творец: «")]),
        ir.Text("Почему ты здесь?»"),
    ])
    out = ir_normalize.dialogue_labels([para])
    assert isinstance(out[0], ir.DialogueLabel) and out[0].speaker == "Творец"
    assert ir_normalize.inline_plain(_para(out[1]).inlines) == "«Почему ты здесь?»"


def test_dialogue_label_inside_body_normal_word_keeps_join_space() -> None:
    # The default case: a normal word body still joins with a space to the tail.
    para = ir.Paragraph(inlines=[
        ir.Emphasis("strong", [ir.Text("Творец: Смотри")]),
        ir.Text("внимательно."),
    ])
    out = ir_normalize.dialogue_labels([para])
    assert ir_normalize.inline_plain(_para(out[1]).inlines) == "Смотри внимательно."


def test_dialogue_label_ignores_non_speaker_strong() -> None:
    para = ir.Paragraph(inlines=[ir.Emphasis("strong", [ir.Text("Заголовок главы")])])
    out = ir_normalize.dialogue_labels([para])
    assert out == [para]


def test_dialogue_multi_turn_paragraph_splits_on_hard_breaks() -> None:
    # H1: one Word paragraph packs several `**Speaker:**` turns separated by hard
    # breaks; each turn must become its own label + body, and a leading non-speaker
    # bold segment (a date) stays its own paragraph — not collapsed into a run-on.
    para = ir.Paragraph(inlines=[
        ir.Emphasis("strong", [ir.Text("April 22, 2025")]), ir.LineBreak(),
        ir.Emphasis("strong", [ir.Text("Pankratius:")]), ir.Text(" Explain string theory."), ir.LineBreak(),
        ir.Emphasis("strong", [ir.Text("Svetozar:")]), ir.Text(" I will explain it simply."),
    ])
    out = ir_normalize.dialogue_labels([para])
    kinds = [type(b).__name__ for b in out]
    assert kinds == ["Paragraph", "DialogueLabel", "Paragraph", "DialogueLabel", "Paragraph"]
    assert ir_normalize.inline_plain(_para(out[0]).inlines) == "April 22, 2025"
    assert isinstance(out[1], ir.DialogueLabel) and out[1].speaker == "Pankratius"
    assert ir_normalize.inline_plain(_para(out[2]).inlines) == "Explain string theory."
    assert isinstance(out[3], ir.DialogueLabel) and out[3].speaker == "Svetozar"
    assert ir_normalize.inline_plain(_para(out[4]).inlines) == "I will explain it simply."


def test_dialogue_single_hard_break_segment_not_over_split() -> None:
    # A paragraph with hard breaks but only ONE speaker turn is not multi-turn: it
    # stays the existing single-paragraph mixed-inline behaviour (label + body),
    # never spuriously fragmented.
    para = ir.Paragraph(inlines=[
        ir.Emphasis("strong", [ir.Text("Творец:")]), ir.Text(" первая строка"),
        ir.LineBreak(), ir.Text("вторая строка"),
    ])
    out = ir_normalize.dialogue_labels([para])
    assert isinstance(out[0], ir.DialogueLabel) and out[0].speaker == "Творец"
    # the body keeps both lines (single turn, not split into two turns)
    assert len([b for b in out if isinstance(b, ir.DialogueLabel)]) == 1


# ---------------------------------------------------------------------------
# empty-emphasis artifact strip (the stray `** **`)
# ---------------------------------------------------------------------------


def test_strip_formatting_artifacts_drops_whole_husk_paragraph() -> None:
    husk = ir.Paragraph(inlines=[ir.Emphasis("strong", [ir.LineBreak()])])
    assert ir_normalize.strip_formatting_artifacts([husk]) == []


def test_strip_formatting_artifacts_removes_trailing_empty_emphasis_in_content() -> None:
    para = ir.Paragraph(inlines=[ir.Text("real text "), ir.Emphasis("strong", [ir.Text(" ")])])
    out = ir_normalize.strip_formatting_artifacts([para])
    assert len(out) == 1
    assert ir_lower._inlines_md(_para(out[0]).inlines, "ru").strip() == "real text"


# ---------------------------------------------------------------------------
# AI-alt scrub uses the shared production constant
# ---------------------------------------------------------------------------


def test_scrub_ai_alt_uses_production_constant_and_recurses_containers() -> None:
    frag = AI_ALT_FRAGMENTS[0]
    img = ir.ImageInline(src="x.png", alt=f"{frag} extra")
    para = ir.Paragraph(inlines=[img])
    container = ir.BlockQuote(blocks=[ir.Paragraph(inlines=[ir.ImageInline(src="y.png", alt=frag)])], role="_div")
    out = ir_normalize.scrub_ai_alt([para, container])
    assert _first_image_alt(out[0]) == ""  # scrubbed in a top-level paragraph
    assert _first_image_alt(_blockquote(out[1]).blocks[0]) == ""  # inside an unwrapped Figure/Div


def test_scrub_ai_alt_keeps_real_alt() -> None:
    para = ir.Paragraph(inlines=[ir.ImageInline(src="x.png", alt="A real caption")])
    out = ir_normalize.scrub_ai_alt([para])
    assert _first_image_alt(out[0]) == "A real caption"


# ---------------------------------------------------------------------------
# bare bibliography heading strip
# ---------------------------------------------------------------------------


def test_strip_bare_bibliography_heading_after_lift() -> None:
    blocks: list[ir.Block] = [
        ir.Heading(level=2, inlines=[ir.Text("Библиография")]),
        ir.Paragraph(inlines=[], empty=True),
    ]
    assert ir_normalize.strip_bare_bibliography_heading(blocks) == []


def test_strip_bare_bibliography_heading_keeps_real_section() -> None:
    blocks: list[ir.Block] = [
        ir.Heading(level=2, inlines=[ir.Text("Bibliography")]),
        ir.Paragraph(inlines=[ir.Text("Real prose still here.")]),
    ]
    assert ir_normalize.strip_bare_bibliography_heading(blocks) == blocks


# ---------------------------------------------------------------------------
# thematic breaks + heading demotion
# ---------------------------------------------------------------------------


def test_thematic_break_from_stars_paragraph() -> None:
    out = ir_normalize.thematic_breaks([ir.Paragraph(inlines=[ir.Text("***")])])
    assert len(out) == 1 and isinstance(out[0], ir.ThematicBreak)


def test_demote_headings_h1_to_h2() -> None:
    h = ir.Heading(level=1, inlines=[ir.Text("Title")])
    ir_normalize.demote_headings([h], 1)
    assert h.level == 2


# ---------------------------------------------------------------------------
# signatures + epigraphs from right alignment
# ---------------------------------------------------------------------------


def test_right_aligned_signature_detected() -> None:
    para = ir.Paragraph(inlines=[ir.Text("Панкратиус")], align="right")
    out = ir_normalize.structural_blocks([para])
    assert len(out) == 1 and isinstance(out[0], ir.Signature)
    assert out[0].lines == ["Панкратиус"]


# ---------------------------------------------------------------------------
# verse detection from stanza structure
# ---------------------------------------------------------------------------


def test_verse_block_from_short_lineated_run_after_named_heading() -> None:
    blocks: list[ir.Block] = [
        ir.Heading(level=2, inlines=[ir.Text("Молитва")]),
        ir.Paragraph(inlines=[ir.Text("Свет мой тихий")]),
        ir.Paragraph(inlines=[ir.Text("в сердце горит")]),
        ir.Paragraph(inlines=[ir.Text("и не гаснет")]),
    ]
    out = ir_normalize.verse_blocks(blocks)
    verse = [b for b in out if isinstance(b, ir.VerseBlock)]
    assert verse and verse[0].role == "verse-block"
    lines = [ir_normalize.inline_plain(line) for s in verse[0].stanzas for line in s]
    assert lines == ["Свет мой тихий", "в сердце горит", "и не гаснет"]


# ---------------------------------------------------------------------------
# C2 / C3: the corrected line-splitting primitive
# (SoftBreak = wrapping in verse detection; recurse into containers for hard breaks)
# ---------------------------------------------------------------------------


def test_inline_lines_softbreak_is_space_in_verse_detection() -> None:
    # C2: in verse DETECTION a SoftBreak is wrapping, so the inlines stay ONE line.
    inlines: list[ir.Inline] = [
        ir.Text("one"), ir.SoftBreak(), ir.Text("two"), ir.SoftBreak(), ir.Text("three"),
    ]
    lines = ir_normalize.inline_lines(inlines, soft_break=False)
    assert [ir_normalize.inline_plain(ln) for ln in lines] == ["one two three"]
    # A hard LineBreak STILL splits, even with soft_break=False.
    inlines2: list[ir.Inline] = [ir.Text("a"), ir.LineBreak(), ir.Text("b")]
    lines2 = ir_normalize.inline_lines(inlines2, soft_break=False)
    assert [ir_normalize.inline_plain(ln) for ln in lines2] == ["a", "b"]


def test_inline_lines_recurses_into_emphasis_for_nested_linebreaks() -> None:
    # C3: hard breaks nested inside an Emph still split the display line, and the
    # surviving fragments stay wrapped in the emphasis.
    para_inlines: list[ir.Inline] = [
        ir.Emphasis("emph", [
            ir.Text("Свет — не фотон."), ir.LineBreak(),
            ir.Text("Фотон — отпечаток."), ir.LineBreak(),
            ir.Text("Я — Свет."),
        ]),
    ]
    lines = ir_normalize.inline_lines(para_inlines, soft_break=False)
    assert [ir_normalize.inline_plain(ln) for ln in lines] == [
        "Свет — не фотон.", "Фотон — отпечаток.", "Я — Свет.",
    ]
    # each surviving fragment is still an Emphasis span (emphasis preserved)
    for ln in lines:
        assert len(ln) == 1 and isinstance(ln[0], ir.Emphasis)


def test_softbreak_only_paragraph_stays_prose() -> None:
    # C2: a paragraph whose only breaks are SoftBreaks (prose wrapping, no <w:br/>)
    # is NOT a verse candidate — a run of them stays prose, not a verse-block.
    blocks: list[ir.Block] = [
        ir.Heading(level=2, inlines=[ir.Text("Глава")]),
        ir.Paragraph(inlines=[ir.Text("Свет не был сотворён."), ir.SoftBreak(), ir.Text("Он не возник."), ir.SoftBreak(), ir.Text("Он — Есть.")]),
        ir.Paragraph(inlines=[], empty=True),
        ir.Paragraph(inlines=[ir.Text("Он не движется."), ir.SoftBreak(), ir.Text("Он просто светит.")]),
    ]
    out = ir_normalize.verse_blocks(blocks)
    assert not any(isinstance(b, ir.VerseBlock) for b in out)


def test_linebreak_in_emphasis_paragraph_becomes_verse() -> None:
    # C3: a fully-italic paragraph whose hard breaks live INSIDE the Emph is verse.
    def verse_para() -> ir.Paragraph:
        return ir.Paragraph(inlines=[ir.Emphasis("emph", [
            ir.Text("Свет — не фотон. Свет — это Я."), ir.LineBreak(),
            ir.Text("Фотон — только отпечаток взгляда."), ir.LineBreak(),
            ir.Text("Когда ты ищешь поле, ты приближаешься."),
        ])])
    blocks: list[ir.Block] = [verse_para()]
    out = ir_normalize.verse_blocks(blocks)
    verse = [b for b in out if isinstance(b, ir.VerseBlock)]
    assert verse, "an Emph-nested-LineBreak paragraph must be detected as verse"
    lines = [ir_normalize.inline_plain(line) for s in verse[0].stanzas for line in s]
    assert lines == [
        "Свет — не фотон. Свет — это Я.",
        "Фотон — только отпечаток взгляда.",
        "Когда ты ищешь поле, ты приближаешься.",
    ]


# ---------------------------------------------------------------------------
# C1: poem stanza fidelity (one non-empty paragraph per stanza)
# ---------------------------------------------------------------------------


def test_poem_one_paragraph_per_stanza_yields_n_stanzas() -> None:
    # C1: each non-empty Word paragraph (its lines as internal hard breaks, NO
    # empty paragraph between) is its OWN stanza — not merged into one giant stanza.
    def stanza(*lines: str) -> ir.Paragraph:
        inlines: list[ir.Inline] = []
        for idx, ln in enumerate(lines):
            if idx:
                inlines.append(ir.LineBreak())
            inlines.append(ir.Text(ln))
        return ir.Paragraph(inlines=inlines)

    doc = ir.Document(blocks=[
        stanza("Сквозь сон берёзовых аллей", "Прошёлся ветер лёгкой тенью,"),
        stanza("Сошла метель, растаял лёд,", "И ручейки бегут игриво."),
        stanza("Грачи в колодцах пьют рассвет,", "И солнце в лужах улыбается."),
    ])
    body = ir_lower.lower(doc, "ru", poem=True)
    stanzas = [s for s in body.strip().split("\n\n") if s.strip()]
    assert len(stanzas) == 3, f"expected 3 stanzas, got {len(stanzas)}: {body!r}"
    assert all(len(s.splitlines()) == 2 for s in stanzas)


def test_poem_strong_only_title_is_its_own_stanza() -> None:
    # C1: the first strong-only paragraph (a bold title line) is its own group, so
    # it does not fuse into the first stanza's line count.
    doc = ir.Document(blocks=[
        ir.Paragraph(inlines=[ir.Emphasis("strong", [ir.Text("Заголовок")])]),
        ir.Paragraph(inlines=[ir.Text("первая строка"), ir.LineBreak(), ir.Text("вторая строка")]),
    ])
    body = ir_lower.lower(doc, "ru", poem=True)
    stanzas = [s for s in body.strip().split("\n\n") if s.strip()]
    assert stanzas[0] == "**Заголовок**"
    assert stanzas[1] == "первая строка\nвторая строка"


# ---------------------------------------------------------------------------
# lowering: footnote appendix, list start, verse div, signature
# ---------------------------------------------------------------------------


def test_lower_footnote_appendix_generated_at_tail() -> None:
    doc = ir.Document(
        blocks=[ir.Paragraph(inlines=[ir.Text("See"), ir.FootnoteRef(raw_index=1, id=1)])],
        footnotes=[ir.FootnoteDef(id=1, blocks=[ir.Paragraph(inlines=[ir.Text("the note body")])])],
    )
    body = ir_lower.lower(doc, "ru")
    assert "See[^1]" in body
    assert "[^1]: the note body" in body
    # the def is at the tail, after the reference
    assert body.index("[^1]:") > body.index("See[^1]")


def test_lower_directional_span_keeps_dir_attribute() -> None:
    # I2: a `DirectionalSpan` lowers to `<span dir="rtl">…</span>` in prose, matching
    # the GFM engine which kept the Hebrew/Arabic bidi span. Direction is reading
    # content (it governs visual ordering of mixed RTL/LTR runs).
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[
        ir.Text("The Name "),
        ir.DirectionalSpan(direction="rtl", children=[ir.Text("פקד")]),
        ir.Text(" appears."),
    ])])
    body = ir_lower.lower(doc, "ru")
    assert 'The Name <span dir="rtl">פקד</span> appears.' in body


def test_lower_directional_span_in_verse_line() -> None:
    # The same bidi span survives the verse HTML-line path (verse blocks render
    # inlines to balanced HTML lines, not prose markdown).
    vb = ir.VerseBlock(stanzas=[[[
        ir.DirectionalSpan(direction="rtl", children=[ir.Text("יהוה")]),
    ]]])
    out = ir_lower._verse_md(vb, "ru")
    assert '<span dir="rtl">יהוה</span>' in out


def test_inline_plain_descends_into_directional_span() -> None:
    # The directional span is a container inline: plain-text flattening (used by
    # detection passes) must read through it, not drop its text.
    assert ir_normalize.inline_plain(
        [ir.DirectionalSpan(direction="rtl", children=[ir.Text("שלום")])]
    ) == "שלום"


def test_lower_heading_preserves_footnote_ref() -> None:
    # A footnote anchored to a HEADING must keep its `[^N]` marker on the heading
    # line (the GFM engine kept it). Dropping it (the old `inline_plain` heading
    # path did) orphans the `[^N]:` definition — a footnote-integrity regression.
    doc = ir.Document(
        blocks=[
            ir.Heading(level=3, inlines=[ir.Text("Глава 25"), ir.FootnoteRef(raw_index=1, id=1), ir.Text(". Число")]),
        ],
        footnotes=[ir.FootnoteDef(id=1, blocks=[ir.Paragraph(inlines=[ir.Text("the note")])])],
    )
    body = ir_lower.lower(doc, "ru")
    assert "### Глава 25[^1]. Число" in body
    assert "[^1]: the note" in body


def test_lower_heading_keeps_partial_emphasis_strips_full_bold() -> None:
    # Partial emphasis in a heading survives (matches the GFM engine); a heading
    # wrapped ENTIRELY in bold loses the wrapper (`# **TEXT**` -> `# TEXT`, mirrors
    # `docx_engine.strip_bold_only_headings`).
    partial = ir.Document(blocks=[
        ir.Heading(level=2, inlines=[ir.Text("О "), ir.Emphasis("strong", [ir.Text("Слове")])]),
    ])
    assert "## О **Слове**" in ir_lower.lower(partial, "ru")

    full = ir.Document(blocks=[
        ir.Heading(level=2, inlines=[ir.Emphasis("strong", [ir.Text("Глава 1")])]),
    ])
    assert "## Глава 1" in ir_lower.lower(full, "ru")


def test_lower_preserves_ordered_list_start() -> None:
    lst = ir.ListBlock(ordered=True, start=4, items=[
        [ir.Paragraph(inlines=[ir.Text("four")])],
        [ir.Paragraph(inlines=[ir.Text("five")])],
    ])
    body = ir_lower.lower(ir.Document(blocks=[lst]), "ru")
    assert "4. four" in body and "5. five" in body


# ---------------------------------------------------------------------------
# M1: literal numbered prose stays a paragraph (escape the leading ordinal)
# ---------------------------------------------------------------------------


def test_lower_literal_numbered_prose_escapes_ordinal() -> None:
    # The author typed a literal "1. " in a normal paragraph (no source OrderedList):
    # the leading ordinal is escaped so the downstream parser keeps it a <p>.
    doc = ir.Document(blocks=[
        ir.Paragraph(inlines=[ir.Text("1. Евангелие говорит: Его распяли.")]),
        ir.Paragraph(inlines=[ir.Text("2) Коран говорит иначе.")]),
    ])
    body = ir_lower.lower(doc, "ru")
    assert "1\\. Евангелие говорит" in body
    assert "2\\) Коран говорит" in body


def test_lower_does_not_escape_leading_date() -> None:
    # A date like "25.06.2025" is `25.` followed by a DIGIT (no space) — never a
    # list marker — so it must NOT be escaped.
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[ir.Text("25.06.2025, Сочи")])])
    body = ir_lower.lower(doc, "ru")
    assert body.strip() == "25.06.2025, Сочи"


def test_lower_real_ordered_list_still_renders_as_list() -> None:
    # A genuine source OrderedList is lowered by ListBlock, not the prose path, so
    # it still renders as a real list (its markers are NOT escaped).
    lst = ir.ListBlock(ordered=True, start=1, items=[
        [ir.Paragraph(inlines=[ir.Text("first")])],
        [ir.Paragraph(inlines=[ir.Text("second")])],
    ])
    body = ir_lower.lower(ir.Document(blocks=[lst]), "ru")
    assert "1. first" in body and "2. second" in body
    assert "1\\." not in body


def test_lower_verse_block_emits_div_with_lines() -> None:
    vb = ir.VerseBlock(stanzas=[[[ir.Text("line one")], [ir.Text("line two")]]], role="verse-block")
    body = ir_lower.lower(ir.Document(blocks=[vb]), "ru")
    assert '<div class="verse-block">' in body
    assert "line one\nline two" in body
    assert "</div>" in body


def test_lower_signature_emits_p_signature() -> None:
    body = ir_lower.lower(ir.Document(blocks=[ir.Signature(lines=["Панкратиус"])]), "ru")
    assert body.strip() == '<p class="signature">\nПанкратиус\n</p>'


def test_lower_poem_keeps_lines_and_stanza_breaks() -> None:
    # Two stanzas: lines within a stanza on adjacent lines, blank between stanzas.
    doc = ir.Document(blocks=[
        ir.Paragraph(inlines=[ir.Text("first line")]),
        ir.Paragraph(inlines=[ir.Text("second line")]),
        ir.Paragraph(inlines=[], empty=True),
        ir.Paragraph(inlines=[ir.Text("third line")]),
    ])
    body = ir_lower.lower(doc, "ru", poem=True)
    assert body == "first line\nsecond line\n\nthird line\n"


def test_lower_body_image_default_alt_and_hash_ref() -> None:
    img = ir.ImageInline(src="m/img.png", alt="", asset_id="abc123.png")
    para = ir.Paragraph(inlines=[img])
    body = ir_lower.lower(ir.Document(blocks=[para]), "ru")
    assert body.strip() == "![Иллюстрация](./images/abc123.png)"
