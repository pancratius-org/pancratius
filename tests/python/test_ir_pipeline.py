"""Unit tests for the typed-IR import pipeline (ir / normalize / lower).

These exercise the pure stages directly on hand-built IR fixtures — no pandoc, no
DOCX — so they run everywhere (``test_ir_adapter`` covers the adapter end-to-end
where pandoc is available). They lock in the IR behaviours: dialogue-label
canonicalization (including the mixed-inline split the spike left unfinished),
empty-emphasis artifact stripping, the bare-bibliography-heading strip, AI-alt
scrubbing via the shared `AI_ALT_FRAGMENTS` constant, thematic breaks, verse
detection, ordered-list start preservation, and the generated footnote appendix.
"""

from __future__ import annotations

from pathlib import Path

import pancratius.ir.lower as lower  # noqa: E402
import pancratius.ir.normalize as normalize  # noqa: E402
from pancratius import ir  # noqa: E402
from pancratius.ir.normalize import AI_ALT_FRAGMENTS  # noqa: E402


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
    assert normalize.inline_plain(inlines) == "a b c"


def test_inline_lines_splits_on_breaks() -> None:
    inlines: list[ir.Inline] = [
        ir.Text("one"), ir.LineBreak(), ir.Text("two"), ir.SoftBreak(), ir.Text("three"),
    ]
    lines = normalize.inline_lines(inlines)
    assert [normalize.inline_plain(ln) for ln in lines] == ["one", "two", "three"]


# ---------------------------------------------------------------------------
# dialogue labels
# ---------------------------------------------------------------------------


def test_dialogue_label_whole_strong_with_body_splits() -> None:
    para = ir.Paragraph(inlines=[ir.Emphasis("strong", [ir.Text("Панкратиус: привет")])])
    out = normalize.dialogue_labels([para])
    assert isinstance(out[0], ir.DialogueLabel) and out[0].speaker == "Панкратиус"
    assert normalize.inline_plain(_para(out[1]).inlines) == "привет"


def test_dialogue_label_bare_label_no_body() -> None:
    para = ir.Paragraph(inlines=[ir.Emphasis("strong", [ir.Text("Светозар:")])])
    out = normalize.dialogue_labels([para])
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
    out = normalize.dialogue_labels([para])
    assert isinstance(out[0], ir.DialogueLabel) and out[0].speaker == "Творец"
    assert normalize.inline_plain(_para(out[1]).inlines) == "body continues here"


def test_dialogue_label_inside_body_ending_in_opening_quote_no_stray_space() -> None:
    # Nit: when the inside-body text of `**Speaker: «**` ends in an OPENING glyph,
    # joining it to the trailing inlines must NOT insert a space (`« Почему` is wrong;
    # `«Почему` is right).
    para = ir.Paragraph(inlines=[
        ir.Emphasis("strong", [ir.Text("Творец: «")]),
        ir.Text("Почему ты здесь?»"),
    ])
    out = normalize.dialogue_labels([para])
    assert isinstance(out[0], ir.DialogueLabel) and out[0].speaker == "Творец"
    assert normalize.inline_plain(_para(out[1]).inlines) == "«Почему ты здесь?»"


def test_dialogue_label_inside_body_normal_word_keeps_join_space() -> None:
    # The default case: a normal word body still joins with a space to the tail.
    para = ir.Paragraph(inlines=[
        ir.Emphasis("strong", [ir.Text("Творец: Смотри")]),
        ir.Text("внимательно."),
    ])
    out = normalize.dialogue_labels([para])
    assert normalize.inline_plain(_para(out[1]).inlines) == "Смотри внимательно."


def test_dialogue_label_ignores_non_speaker_strong() -> None:
    para = ir.Paragraph(inlines=[ir.Emphasis("strong", [ir.Text("Заголовок главы")])])
    out = normalize.dialogue_labels([para])
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
    out = normalize.dialogue_labels([para])
    kinds = [type(b).__name__ for b in out]
    assert kinds == ["Paragraph", "DialogueLabel", "Paragraph", "DialogueLabel", "Paragraph"]
    assert normalize.inline_plain(_para(out[0]).inlines) == "April 22, 2025"
    assert isinstance(out[1], ir.DialogueLabel) and out[1].speaker == "Pankratius"
    assert normalize.inline_plain(_para(out[2]).inlines) == "Explain string theory."
    assert isinstance(out[3], ir.DialogueLabel) and out[3].speaker == "Svetozar"
    assert normalize.inline_plain(_para(out[4]).inlines) == "I will explain it simply."


def test_dialogue_single_hard_break_segment_not_over_split() -> None:
    # A paragraph with hard breaks but only ONE speaker turn is not multi-turn: it
    # stays the existing single-paragraph mixed-inline behaviour (label + body),
    # never spuriously fragmented.
    para = ir.Paragraph(inlines=[
        ir.Emphasis("strong", [ir.Text("Творец:")]), ir.Text(" первая строка"),
        ir.LineBreak(), ir.Text("вторая строка"),
    ])
    out = normalize.dialogue_labels([para])
    assert isinstance(out[0], ir.DialogueLabel) and out[0].speaker == "Творец"
    # the body keeps both lines (single turn, not split into two turns)
    assert len([b for b in out if isinstance(b, ir.DialogueLabel)]) == 1


# ---------------------------------------------------------------------------
# empty-emphasis artifact strip (the stray `** **`)
# ---------------------------------------------------------------------------


def test_strip_formatting_artifacts_drops_whole_husk_paragraph() -> None:
    husk = ir.Paragraph(inlines=[ir.Emphasis("strong", [ir.LineBreak()])])
    assert normalize.strip_formatting_artifacts([husk]) == []


def test_strip_formatting_artifacts_removes_trailing_empty_emphasis_in_content() -> None:
    para = ir.Paragraph(inlines=[ir.Text("real text "), ir.Emphasis("strong", [ir.Text(" ")])])
    out = normalize.strip_formatting_artifacts([para])
    assert len(out) == 1
    assert lower._inlines_md(_para(out[0]).inlines, "ru").strip() == "real text"


# ---------------------------------------------------------------------------
# AI-alt scrub uses the shared production constant
# ---------------------------------------------------------------------------


def test_scrub_ai_alt_uses_production_constant_and_recurses_containers() -> None:
    frag = AI_ALT_FRAGMENTS[0]
    img = ir.ImageInline(src="x.png", alt=f"{frag} extra")
    para = ir.Paragraph(inlines=[img])
    container = ir.BlockQuote(blocks=[ir.Paragraph(inlines=[ir.ImageInline(src="y.png", alt=frag)])], role="_div")
    out = normalize.scrub_ai_alt([para, container])
    assert _first_image_alt(out[0]) == ""  # scrubbed in a top-level paragraph
    assert _first_image_alt(_blockquote(out[1]).blocks[0]) == ""  # inside an unwrapped Figure/Div


def test_scrub_ai_alt_keeps_real_alt() -> None:
    para = ir.Paragraph(inlines=[ir.ImageInline(src="x.png", alt="A real caption")])
    out = normalize.scrub_ai_alt([para])
    assert _first_image_alt(out[0]) == "A real caption"


# ---------------------------------------------------------------------------
# bare bibliography heading strip
# ---------------------------------------------------------------------------


def test_strip_bare_bibliography_heading_after_lift() -> None:
    blocks: list[ir.Block] = [
        ir.Heading(level=2, inlines=[ir.Text("Библиография")]),
        ir.Paragraph(inlines=[], empty=True),
    ]
    assert normalize.strip_bare_bibliography_heading(blocks) == []


def test_strip_bare_bibliography_heading_keeps_real_section() -> None:
    blocks: list[ir.Block] = [
        ir.Heading(level=2, inlines=[ir.Text("Bibliography")]),
        ir.Paragraph(inlines=[ir.Text("Real prose still here.")]),
    ]
    assert normalize.strip_bare_bibliography_heading(blocks) == blocks


# ---------------------------------------------------------------------------
# thematic breaks + heading demotion
# ---------------------------------------------------------------------------


def test_thematic_break_from_stars_paragraph() -> None:
    out = normalize.thematic_breaks([ir.Paragraph(inlines=[ir.Text("***")])])
    assert len(out) == 1 and isinstance(out[0], ir.ThematicBreak)


def test_demote_headings_h1_to_h2() -> None:
    h = ir.Heading(level=1, inlines=[ir.Text("Title")])
    normalize.demote_headings([h], 1)
    assert h.level == 2


# ---------------------------------------------------------------------------
# signatures + epigraphs from right alignment
# ---------------------------------------------------------------------------


def test_right_aligned_signature_detected() -> None:
    para = ir.Paragraph(inlines=[ir.Text("Панкратиус")], align="right")
    out = normalize.structural_blocks([para])
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
    out = normalize.verse_blocks(blocks)
    verse = [b for b in out if isinstance(b, ir.VerseBlock)]
    assert verse and verse[0].role == "verse-block"
    lines = [normalize.inline_plain(line) for s in verse[0].stanzas for line in s]
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
    lines = normalize.inline_lines(inlines, soft_break=False)
    assert [normalize.inline_plain(ln) for ln in lines] == ["one two three"]
    # A hard LineBreak STILL splits, even with soft_break=False.
    inlines2: list[ir.Inline] = [ir.Text("a"), ir.LineBreak(), ir.Text("b")]
    lines2 = normalize.inline_lines(inlines2, soft_break=False)
    assert [normalize.inline_plain(ln) for ln in lines2] == ["a", "b"]


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
    lines = normalize.inline_lines(para_inlines, soft_break=False)
    assert [normalize.inline_plain(ln) for ln in lines] == [
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
    out = normalize.verse_blocks(blocks)
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
    out = normalize.verse_blocks(blocks)
    verse = [b for b in out if isinstance(b, ir.VerseBlock)]
    assert verse, "an Emph-nested-LineBreak paragraph must be detected as verse"
    lines = [normalize.inline_plain(line) for s in verse[0].stanzas for line in s]
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
    body = lower.lower(doc, "ru", poem=True)
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
    body = lower.lower(doc, "ru", poem=True)
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
    body = lower.lower(doc, "ru")
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
    body = lower.lower(doc, "ru")
    assert 'The Name <span dir="rtl">פקד</span> appears.' in body


def test_lower_directional_span_in_verse_line() -> None:
    # The same bidi span survives the verse HTML-line path (verse blocks render
    # inlines to balanced HTML lines, not prose markdown).
    vb = ir.VerseBlock(stanzas=[[[
        ir.DirectionalSpan(direction="rtl", children=[ir.Text("יהוה")]),
    ]]])
    out = lower._verse_md(vb, "ru")
    assert '<span dir="rtl">יהוה</span>' in out


def test_inline_plain_descends_into_directional_span() -> None:
    # The directional span is a container inline: plain-text flattening (used by
    # detection passes) must read through it, not drop its text.
    assert normalize.inline_plain(
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
    body = lower.lower(doc, "ru")
    assert "### Глава 25[^1]. Число" in body
    assert "[^1]: the note" in body


def test_lower_heading_keeps_partial_emphasis_strips_full_bold() -> None:
    # Partial emphasis in a heading survives; a heading wrapped ENTIRELY in bold
    # loses the wrapper (`# **TEXT**` -> `# TEXT`).
    partial = ir.Document(blocks=[
        ir.Heading(level=2, inlines=[ir.Text("О "), ir.Emphasis("strong", [ir.Text("Слове")])]),
    ])
    assert "## О **Слове**" in lower.lower(partial, "ru")

    full = ir.Document(blocks=[
        ir.Heading(level=2, inlines=[ir.Emphasis("strong", [ir.Text("Глава 1")])]),
    ])
    assert "## Глава 1" in lower.lower(full, "ru")


def test_lower_preserves_ordered_list_start() -> None:
    lst = ir.ListBlock(ordered=True, start=4, items=[
        [ir.Paragraph(inlines=[ir.Text("four")])],
        [ir.Paragraph(inlines=[ir.Text("five")])],
    ])
    body = lower.lower(ir.Document(blocks=[lst]), "ru")
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
    body = lower.lower(doc, "ru")
    assert "1\\. Евангелие говорит" in body
    assert "2\\) Коран говорит" in body


def test_lower_does_not_escape_leading_date() -> None:
    # A date like "25.06.2025" is `25.` followed by a DIGIT (no space) — never a
    # list marker — so it must NOT be escaped.
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[ir.Text("25.06.2025, Сочи")])])
    body = lower.lower(doc, "ru")
    assert body.strip() == "25.06.2025, Сочи"


def test_lower_real_ordered_list_still_renders_as_list() -> None:
    # A genuine source OrderedList is lowered by ListBlock, not the prose path, so
    # it still renders as a real list (its markers are NOT escaped).
    lst = ir.ListBlock(ordered=True, start=1, items=[
        [ir.Paragraph(inlines=[ir.Text("first")])],
        [ir.Paragraph(inlines=[ir.Text("second")])],
    ])
    body = lower.lower(ir.Document(blocks=[lst]), "ru")
    assert "1. first" in body and "2. second" in body
    assert "1\\." not in body


def test_lower_verse_block_emits_div_with_lines() -> None:
    vb = ir.VerseBlock(stanzas=[[[ir.Text("line one")], [ir.Text("line two")]]], role="verse-block")
    body = lower.lower(ir.Document(blocks=[vb]), "ru")
    assert '<div class="verse-block">' in body
    assert "line one\nline two" in body
    assert "</div>" in body


def test_lower_signature_emits_p_signature() -> None:
    body = lower.lower(ir.Document(blocks=[ir.Signature(lines=["Панкратиус"])]), "ru")
    assert body.strip() == '<p class="signature">\nПанкратиус\n</p>'


def test_lower_poem_keeps_lines_and_stanza_breaks() -> None:
    # Two stanzas: lines within a stanza on adjacent lines, blank between stanzas.
    doc = ir.Document(blocks=[
        ir.Paragraph(inlines=[ir.Text("first line")]),
        ir.Paragraph(inlines=[ir.Text("second line")]),
        ir.Paragraph(inlines=[], empty=True),
        ir.Paragraph(inlines=[ir.Text("third line")]),
    ])
    body = lower.lower(doc, "ru", poem=True)
    assert body == "first line\nsecond line\n\nthird line\n"


def test_lower_body_image_default_alt_and_hash_ref() -> None:
    img = ir.ImageInline(src="m/img.png", alt="", asset_id="abc123.png")
    para = ir.Paragraph(inlines=[img])
    body = lower.lower(ir.Document(blocks=[para]), "ru")
    assert body.strip() == "![Иллюстрация](./images/abc123.png)"


# ---------------------------------------------------------------------------
# Bug 2: literal Markdown/HTML in Text nodes is escaped at prose lowering
# (Pandoc Str/Text values are LITERAL source text, not markup — emitting them
# raw lets a DOCX literal become a real link/emphasis/HTML, like the OLD GFM
# writer that DID escape them). The escaping applies ONLY to literal Text-node
# values, never to the intentional markup the IR nodes emit (Link/Emphasis/Code/
# DirectionalSpan), so a real Link still renders as a working link.
# ---------------------------------------------------------------------------


def test_literal_bracket_link_text_does_not_render_as_link() -> None:
    # A DOCX literal `[not a link](https://example.com)` is one Text run; raw it
    # parses as a real Markdown link. Lowered, the `[` `]` `(` are escaped so it
    # renders as plain text, not an anchor.
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[ir.Text("[not a link](https://example.com)")])])
    body = lower.lower(doc, "ru")
    # the open bracket is escaped so no `[label](url)` link survives
    assert "\\[not a link\\]" in body
    assert "[not a link](https://example.com)" not in body


def test_literal_html_script_is_inert() -> None:
    # A literal `<script>alert(1)</script>` Text run must not pass through as raw
    # HTML — the angle brackets are escaped so it renders as inert text.
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[ir.Text("<script>alert(1)</script>")])])
    body = lower.lower(doc, "ru")
    assert "<script>" not in body
    assert "\\<script\\>alert(1)\\</script\\>" in body


def test_literal_emphasis_stars_are_escaped() -> None:
    # A literal `*not emphasis*` Text run must not become emphasis; the `*` are
    # escaped so the asterisks render verbatim.
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[ir.Text("*not emphasis*")])])
    body = lower.lower(doc, "ru")
    assert "\\*not emphasis\\*" in body


def test_literal_leading_hash_and_quote_are_escaped() -> None:
    # A literal leading `#` (would parse as a heading) and a leading `>` (a
    # blockquote) at the start of a prose paragraph are escaped.
    h = ir.Document(blocks=[ir.Paragraph(inlines=[ir.Text("# not a heading")])])
    assert "\\# not a heading" in lower.lower(h, "ru")
    q = ir.Document(blocks=[ir.Paragraph(inlines=[ir.Text("> not a quote")])])
    assert "\\> not a quote" in lower.lower(q, "ru")


def test_intentional_link_node_still_renders_as_working_link() -> None:
    # A genuine IR Link node (intentional markup) is NOT over-escaped — its `[` `]`
    # `(` `)` are the link syntax and must survive so it renders as a real link.
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[
        ir.Link(children=[ir.Text("Anthropic")], target="https://anthropic.com"),
    ])])
    body = lower.lower(doc, "ru")
    assert "[Anthropic](https://anthropic.com)" in body
    assert "\\[" not in body


def test_intentional_emphasis_node_still_renders() -> None:
    # A genuine IR Emphasis node still emits working `**bold**` / `*italic*`; the
    # markup asterisks the node produces must not be escaped.
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[
        ir.Text("see "),
        ir.Emphasis("strong", [ir.Text("this")]),
        ir.Text(" and "),
        ir.Emphasis("emph", [ir.Text("that")]),
    ])])
    body = lower.lower(doc, "ru")
    assert "**this**" in body
    assert "*that*" in body


def test_intentional_code_node_text_not_escaped() -> None:
    # Inline `Code` is literal-but-protected-by-backticks: its content is NOT
    # Markdown-escaped (backticks already make it literal), so a `*` inside code
    # stays a `*`.
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[ir.Code("a*b_c")])])
    body = lower.lower(doc, "ru")
    assert "`a*b_c`" in body


def test_literal_pipe_in_table_cell_is_escaped() -> None:
    # A literal `|` in a reading-content table cell must stay escaped for the GFM
    # grid, and other literal markup chars in the cell are escaped too.
    t = ir.Table(rows=[[[ir.Text("a|b")], [ir.Text("*c*")]]])
    doc = ir.Document(blocks=[t])
    body = lower.lower(doc, "ru")
    assert "a\\|b" in body
    assert "\\*c\\*" in body


def test_literal_markup_in_footnote_body_is_escaped() -> None:
    # A footnote body carrying a literal `[x](y)` must be escaped too (the appendix
    # lowers footnote blocks through the same prose path).
    doc = ir.Document(
        blocks=[ir.Paragraph(inlines=[ir.Text("ref"), ir.FootnoteRef(raw_index=1, id=1)])],
        footnotes=[ir.FootnoteDef(id=1, blocks=[ir.Paragraph(inlines=[ir.Text("see [x](y)")])])],
    )
    body = lower.lower(doc, "ru")
    assert "[^1]: see \\[x\\](y)" in body


def test_literal_markup_in_heading_text_is_escaped() -> None:
    # A heading whose literal Text carries markup chars escapes them, while a real
    # FootnoteRef / Emphasis node in the SAME heading still renders.
    doc = ir.Document(blocks=[ir.Heading(level=2, inlines=[ir.Text("A*B and [c]")])])
    body = lower.lower(doc, "ru")
    assert "## A\\*B and \\[c\\]" in body


# ---------------------------------------------------------------------------
# Bug 3 (now hardened by Fix A): a body-image asset `src` must stay UNDER the pandoc
# media-extraction dir. An absolute `src` (e.g. `/etc/passwd`) or a `..`-escaping
# `src` is REJECTED — the importer must never read/copy a file outside `media_root`.
# Fix A makes this a FATAL local-image-unresolved diagnostic (blocking the write)
# and DROPS the ref so no dangling/escaping path is emitted into the body.
# ---------------------------------------------------------------------------


def test_asset_absolute_src_is_rejected_with_diagnostic(tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    # A real sensitive file OUTSIDE the media root the absolute src points at.
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\nSENSITIVE")

    doc = ir.Document(blocks=[
        ir.Paragraph(inlines=[ir.ImageInline(src=str(outside), alt="")]),
    ])
    planned = lower.assign_assets(doc, media_root, "ru")

    # No asset planned for the escaping ref; the ref is DROPPED (not kept).
    assert planned == []
    assert _para(doc.blocks[0]).inlines == [], "the escaping image ref must be dropped"
    # A FATAL diagnostic surfaced (not a silent read of the outside file).
    fatal = [d for d in doc.diagnostics if d.severity == "fatal" and "image" in d.code.lower()]
    assert fatal
    # And the body never leaks the absolute path.
    assert str(outside) not in lower.lower(doc, "ru")


def test_asset_parent_escaping_src_is_rejected_with_diagnostic(tmp_path: Path) -> None:
    media_root = tmp_path / "wd" / "media"
    media_root.mkdir(parents=True)
    # A real file two levels up that `../../secret.png` would resolve to.
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\nSENSITIVE")

    doc = ir.Document(blocks=[
        ir.Paragraph(inlines=[ir.ImageInline(src="../../secret.png", alt="")]),
    ])
    planned = lower.assign_assets(doc, media_root, "ru")

    assert planned == []
    assert _para(doc.blocks[0]).inlines == [], "the parent-escaping image ref must be dropped"
    fatal = [d for d in doc.diagnostics if d.severity == "fatal" and "image" in d.code.lower()]
    assert fatal
    assert "../../secret.png" not in lower.lower(doc, "ru")


def test_asset_legit_src_under_media_root_still_planned(tmp_path: Path) -> None:
    # The confinement must NOT reject a legitimate in-root media file — a normal
    # `media/<name>.png` (and a nested subdir) still resolves and is planned.
    media_root = tmp_path / "media"
    (media_root / "sub").mkdir(parents=True)
    img_file = media_root / "sub" / "pic.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\nlegit-bytes")

    doc = ir.Document(blocks=[
        ir.Paragraph(inlines=[ir.ImageInline(src="sub/pic.png", alt="")]),
    ])
    planned = lower.assign_assets(doc, media_root, "ru")

    assert len(planned) == 1
    assert planned[0].rel_within.startswith("images/")
    img = _para(doc.blocks[0]).inlines[0]
    assert isinstance(img, ir.ImageInline) and img.asset_id is not None


# ---------------------------------------------------------------------------
# Bug 4: a LineBlock is verse content (not dropped); a genuinely-unknown block
# PRESERVES its readable text AND surfaces a diagnostic (never silently dropped).
# ---------------------------------------------------------------------------


def test_lower_line_block_produces_verse_lines() -> None:
    # Bug 4(a): a LineBlock (mapped to a VerseBlock by the adapter) lowers to a
    # non-empty verse `<div>` preserving its lines — not empty output.
    vb = ir.VerseBlock(stanzas=[[[ir.Text("Roses are red,")], [ir.Text("violets are blue.")]]])
    body = lower.lower(ir.Document(blocks=[vb]), "ru")
    assert "Roses are red," in body
    assert "violets are blue." in body
    assert '<div class="verse-block">' in body


def test_lower_unknown_block_preserves_text_and_emits_diagnostic() -> None:
    # Bug 4(b): a genuinely-unknown block must NOT be silently dropped — its readable
    # text is emitted AND a diagnostic is surfaced on the document.
    doc = ir.Document(blocks=[ir.UnknownBlock(note="Bogus", text="important reading content")])
    body = lower.lower(doc, "ru")
    assert "important reading content" in body
    surfaced = [d for d in doc.diagnostics if d.severity in {"warning", "fatal"} and "unknown" in d.code]
    assert surfaced, "an unknown block must surface a diagnostic, not be silently dropped"


def test_lower_empty_unknown_block_still_emits_diagnostic() -> None:
    # An unknown block with NO recoverable text (e.g. Pandoc Null) carries no
    # reading content, but its presence is still surfaced as a diagnostic — the
    # importer never drops a block silently.
    doc = ir.Document(blocks=[ir.UnknownBlock(note="Null", text="")])
    lower.lower(doc, "ru")
    surfaced = [d for d in doc.diagnostics if d.severity in {"warning", "fatal"} and "unknown" in d.code]
    assert surfaced


# ---------------------------------------------------------------------------
# Fix B: code-delimiter-safe lowering (Markdown breakout via literal backticks)
# ---------------------------------------------------------------------------


def test_inline_code_with_literal_backtick_cannot_break_out() -> None:
    # SECURITY (defense-in-depth): a literal backtick inside Code lowered with a
    # FIXED single-backtick delimiter (`` ` ``) closes the span early, so the rest
    # of the run escapes back into prose markup. The delimiter must be a run of
    # N+1 backticks where N is the longest internal backtick run, so the content
    # round-trips inert. Per CommonMark, when the content begins/ends with a
    # backtick a single space pad is required inside the fence.
    md = lower._inline_md(ir.Code("a ` b"), "ru")
    # The opening fence is at least two backticks (longer than the internal run of 1).
    assert md.startswith("``")
    assert md.endswith("``")
    # The literal content survives verbatim between the fences.
    assert "a ` b" in md
    # Round-trip: rendering this Markdown must yield exactly one <code> element whose
    # text is the original — i.e. the inner backtick did not terminate the span.
    import re as _re

    fence_match = _re.match(r"^(`+) ?", md)
    assert fence_match is not None
    fence = fence_match.group(1)
    # The internal backtick run (1) must be strictly shorter than the fence.
    assert len(fence) >= 2


def test_inline_code_pure_backtick_content_is_space_padded() -> None:
    # Content that is itself only backticks needs both a longer fence AND space
    # padding so the renderer does not strip the leading/trailing backtick.
    md = lower._inline_md(ir.Code("`"), "ru")
    # fence is at least 2 backticks; padded with single spaces around the content.
    assert md == "`` ` ``"


def test_code_block_with_internal_fence_uses_longer_fence() -> None:
    # SECURITY: a code block whose content contains a ``` line closes the block
    # early under a FIXED triple-fence, leaking the remainder as raw Markdown. The
    # fence must be longer than the longest internal backtick run.
    cb = ir.CodeBlock(text="line one\n```\nstill code\n```")
    md = lower._block_md(cb, "ru")
    assert md is not None
    fence = md.split("\n", 1)[0]
    assert set(fence) == {"`"}
    # The fence (4+ backticks) is strictly longer than the internal run of 3.
    assert len(fence) >= 4
    # The whole literal body is preserved between the fences.
    assert "line one" in md
    assert "still code" in md
    # The closing fence equals the opening fence and is the last line.
    assert md.rstrip().endswith(fence)


# ---------------------------------------------------------------------------
# Fix C: URL-scheme allowlist for links + images (drop unsafe, keep text)
# ---------------------------------------------------------------------------


def test_javascript_link_drops_target_keeps_text_with_warning() -> None:
    # SECURITY: the renderer emits raw HTML with no sanitizer, so a
    # `javascript:`-scheme link target would become an active anchor. An unsafe
    # scheme must drop the link (keeping its visible text) and surface a warning.
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[ir.Link([ir.Text("click me")], "javascript:alert(1)")])])
    body = lower.lower(doc, "ru")
    assert "click me" in body
    assert "javascript:" not in body
    assert "](" not in body  # no markdown link syntax survives
    warned = [d for d in doc.diagnostics if d.severity == "warning" and "url" in d.code.lower()]
    assert warned, "an unsafe link scheme must surface a warning diagnostic"


def test_https_link_is_preserved() -> None:
    # A normal http(s) link is untouched.
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[ir.Link([ir.Text("home")], "https://example.org/x")])])
    body = lower.lower(doc, "ru")
    assert "[home](https://example.org/x)" in body


def test_relative_and_anchor_and_mailto_links_preserved() -> None:
    for target in ("./other", "/works/x", "#section", "mailto:a@b.org"):
        doc = ir.Document(blocks=[ir.Paragraph(inlines=[ir.Link([ir.Text("L")], target)])])
        body = lower.lower(doc, "ru")
        assert f"[L]({target})" in body, target


def test_unsafe_link_in_verse_drops_target_keeps_text() -> None:
    # The verse/HTML lowering path must apply the same allowlist (it emits raw <a>).
    vb = ir.VerseBlock(stanzas=[[[ir.Link([ir.Text("x")], "javascript:alert(1)")]]])
    doc = ir.Document(blocks=[vb])
    body = lower.lower(doc, "ru")
    assert "javascript:" not in body
    assert "<a " not in body  # the anchor element is gone
    assert ">x<" not in body or "x" in body  # the text remains


def test_unsafe_scheme_image_is_dropped_with_warning() -> None:
    # An image whose src is an unsafe non-image scheme (e.g. data:text/html or
    # javascript:) must be dropped entirely (no <img>, no ![]() ) with a warning.
    img = ir.ImageInline(src="javascript:alert(1)", alt="bad")
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[ir.Text("before "), img, ir.Text(" after")])])
    body = lower.lower(doc, "ru")
    assert "javascript:" not in body
    assert "![" not in body
    assert "before" in body and "after" in body
    warned = [d for d in doc.diagnostics if d.severity == "warning" and "url" in d.code.lower()]
    assert warned


# ---------------------------------------------------------------------------
# Fix A: an unresolvable / unsafe LOCAL image is FATAL; lowerer emits no dangling path
# ---------------------------------------------------------------------------


def test_unresolvable_local_image_is_fatal_and_ref_not_emitted(tmp_path: Path) -> None:
    # docs/import-pipeline.md: an unresolvable LOCAL image is FATAL. A relative src
    # that names no real file under the media dir must (a) surface a FATAL
    # diagnostic and (b) NOT leave a dangling ref in the lowered body.
    media = tmp_path / "media"
    media.mkdir()
    img = ir.ImageInline(src="media/missing.png", alt="x")
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[ir.Text("before "), img, ir.Text(" after")])])
    lower.assign_assets(doc, media, "ru")
    fatal = [d for d in doc.diagnostics if d.severity == "fatal" and "image" in d.code.lower()]
    assert fatal, "an unresolvable local image must be FATAL"
    body = lower.lower(doc, "ru")
    assert "missing.png" not in body, "no dangling local image ref may reach the body"
    assert "before" in body and "after" in body


def test_escaping_absolute_image_is_fatal_and_ref_not_emitted(tmp_path: Path) -> None:
    # An absolute/`..`-escaping src (e.g. /Users/...) must be FATAL and never emitted
    # into the body — it must not become an exfiltrating or path-leaking ref.
    media = tmp_path / "media"
    media.mkdir()
    img = ir.ImageInline(src="/etc/passwd.png", alt="x")
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[img])])
    lower.assign_assets(doc, media, "ru")
    fatal = [d for d in doc.diagnostics if d.severity == "fatal" and "image" in d.code.lower()]
    assert fatal
    body = lower.lower(doc, "ru")
    assert "/etc/passwd" not in body


def test_resolvable_local_image_assigns_asset_and_is_not_fatal(tmp_path: Path) -> None:
    # A normal in-root image resolves to a content-hash asset; no fatal.
    media = tmp_path / "media" / "media"
    media.mkdir(parents=True)
    (media / "image1.png").write_bytes(b"\x89PNGfakebytes")
    img = ir.ImageInline(src="media/image1.png", alt="x")
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[img])])
    planned = lower.assign_assets(doc, tmp_path / "media", "ru")
    assert planned, "a resolvable image must produce a planned asset"
    assert not [d for d in doc.diagnostics if d.severity == "fatal"]
    body = lower.lower(doc, "ru")
    assert "./images/" in body


def test_remote_http_image_is_kept_not_fatal(tmp_path: Path) -> None:
    # A safe REMOTE image (http/https) is not a LOCAL image: it is a valid remote
    # ref, kept as-is, never fatal.
    media = tmp_path / "media"
    media.mkdir()
    img = ir.ImageInline(src="https://example.org/a.png", alt="x")
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[img])])
    lower.assign_assets(doc, media, "ru")
    assert not [d for d in doc.diagnostics if d.severity == "fatal"]
    body = lower.lower(doc, "ru")
    assert "https://example.org/a.png" in body


def test_container_forms_in_sync() -> None:
    # The union (ir.ContainerInlineNode) and the isinstance tuple (ir.ContainerInline)
    # must list the same kinds — they are maintained as two forms of one set.
    from typing import get_args

    assert set(get_args(ir.ContainerInlineNode.__value__)) == set(ir.ContainerInline)


def test_emph_tables_total() -> None:
    # The lowering tables must cover every EmphKind (a dict[Literal,...] is NOT
    # exhaustiveness-checked by the type checker, so pin it here).
    from typing import get_args

    kinds = set(get_args(ir.EmphKind))
    assert set(lower._EMPH_MD) == kinds
    assert set(lower._EMPH_HTML_TAG) == kinds
