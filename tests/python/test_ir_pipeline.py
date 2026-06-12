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
from typing import Literal, TypeIs

import pancratius.ir.lower as lower
import pancratius.ir.normalize as normalize
from pancratius import (
    docx_conversion,
    ir,
)
from pancratius.content_catalog import IndexHit
from pancratius.ir.normalize import AI_ALT_FRAGMENTS


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


def _blockquote(block: ir.Block) -> ir.QuoteBlock:
    assert isinstance(block, ir.QuoteBlock)
    return block


def _is_verse(block: ir.Block) -> TypeIs[ir.LineatedBlock]:
    return isinstance(block, ir.LineatedBlock) and block.register is ir.Register.VERSE


def _block_texts(blocks: list[ir.Block]) -> list[str]:
    texts: list[str] = []
    for block in blocks:
        if isinstance(block, (ir.Heading, ir.Paragraph)):
            texts.append(normalize.inline_plain(block.inlines))
    return texts


def _assert_diagnostic(
    doc: ir.Document,
    severity: Literal["fatal", "warning", "info"],
    code: str,
) -> None:
    assert any(d.severity == severity and d.code == code for d in doc.diagnostics), (
        f"expected {severity} {code}; got {doc.diagnostics!r}"
    )


# ---------------------------------------------------------------------------
# bibliography sidecar helpers
# ---------------------------------------------------------------------------


def test_bibliography_dedupe_merges_cover_alt_and_store_link() -> None:
    entries: list[dict[str, object]] = [
        {"title": "Евангелие Царствия", "target": {"kind": "book", "number": 1}},
        {
            "title": "Евангелие Царствия",
            "source_url": "https://www.litres.ru/71769250/",
        },
        {"title": "Книга огня", "target": {"kind": "book", "number": 20}},
        {
            "title": "Книга Огня",
            "source_url": "https://www.litres.ru/72343057/",
            "target": {"kind": "book", "number": 20},
        },
        {"title": "Маленький Царь. Часть 1"},
        {"title": "Часть 1", "source_url": "https://www.litres.ru/71807839/"},
        {"title": "часть 2", "source_url": "https://www.litres.ru/71831962/"},
    ]

    assert docx_conversion._dedupe_bibliography(entries) == [
        {
            "title": "Евангелие Царствия",
            "source_url": "https://www.litres.ru/71769250/",
            "target": {"kind": "book", "number": 1},
        },
        {
            "title": "Книга Огня",
            "source_url": "https://www.litres.ru/72343057/",
            "target": {"kind": "book", "number": 20},
        },
        {
            "title": "Маленький Царь. Часть 1",
            "source_url": "https://www.litres.ru/71807839/",
        },
        {"title": "часть 2", "source_url": "https://www.litres.ru/71831962/"},
    ]


def test_bibliography_target_resolution_ignores_terminal_period() -> None:
    lookup = {
        "книга бытия (живая).": IndexHit(
            work_key="65-kniga-bytiya-zhivaya",
            number=65,
            kind="book",
        ),
    }
    assert normalize._resolve_target("Книга Бытия (живая)", lookup) == {
        "kind": "book",
        "number": 65,
    }


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


def test_strip_formatting_artifacts_hoists_boundary_break_out_of_emphasis() -> None:
    # Word styles the break run with the styled line; the break must leave the span
    # so the closing delimiter stays on the emphasized line.
    para = ir.Paragraph(inlines=[
        ir.Emphasis("emph", [ir.Text("— А что для Вадима Мария есть?"), ir.LineBreak()]),
        ir.Text("— Она — как опора."),
    ])
    out = normalize.strip_formatting_artifacts([para])
    assert lower._inlines_md(_para(out[0]).inlines, "ru") == (
        "*— А что для Вадима Мария есть?*\n— Она — как опора."
    )


def test_strip_formatting_artifacts_keeps_break_of_break_only_emphasis() -> None:
    # `Emph([LineBreak])` between two verse lines: the husk goes, the break stays.
    para = ir.Paragraph(inlines=[
        ir.Text("строка раз"),
        ir.Emphasis("strong", [ir.LineBreak()]),
        ir.Text("строка два"),
    ])
    out = normalize.strip_formatting_artifacts([para])
    assert lower._inlines_md(_para(out[0]).inlines, "ru") == "строка раз\nстрока два"


def test_strip_formatting_artifacts_drops_hidden_form_markers() -> None:
    blocks: list[ir.Block] = [
        ir.Paragraph(inlines=[ir.Text("Н    ачало"), ir.SoftBreak(), ir.Text("формы")]),
        ir.Paragraph(inlines=[ir.Text("Конец формы")]),
        ir.Paragraph(inlines=[ir.Text("Beginning of the form")]),
        ir.Paragraph(inlines=[ir.Text("End of the form")]),
        ir.Paragraph(inlines=[ir.Text("Start of Form")]),
        ir.Paragraph(inlines=[ir.Text("End"), ir.Text(" of "), ir.Text("Form")]),
    ]

    assert normalize.strip_formatting_artifacts(blocks) == []


def test_strip_formatting_artifacts_keeps_real_form_prose() -> None:
    para = ir.Paragraph(inlines=[ir.Text("Начало формы жизни — не шаблон.")])

    out = normalize.strip_formatting_artifacts([para])

    assert len(out) == 1
    assert normalize.inline_plain(_para(out[0]).inlines) == "Начало формы жизни — не шаблон."


# ---------------------------------------------------------------------------
# AI-alt scrub uses the shared production constant
# ---------------------------------------------------------------------------


def test_scrub_ai_alt_uses_production_constant_and_recurses_containers() -> None:
    frag = AI_ALT_FRAGMENTS[0]
    img = ir.ImageInline(src="x.png", alt=f"{frag} extra")
    para = ir.Paragraph(inlines=[img])
    container = ir.QuoteBlock(blocks=[ir.Paragraph(inlines=[ir.ImageInline(src="y.png", alt=frag)])])
    out = normalize.scrub_ai_alt([para, container])
    assert _first_image_alt(out[0]) == ""  # scrubbed in a top-level paragraph
    assert _first_image_alt(_blockquote(out[1]).blocks[0]) == ""  # inside a quote container


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
        ir.Paragraph(inlines=[], facts=ir.SourceFacts(empty=True)),
    ]
    assert normalize.strip_bare_bibliography_heading(blocks) == []


def test_strip_bare_bibliography_heading_keeps_real_section() -> None:
    blocks: list[ir.Block] = [
        ir.Heading(level=2, inlines=[ir.Text("Bibliography")]),
        ir.Paragraph(inlines=[ir.Text("Real prose still here.")]),
    ]
    assert normalize.strip_bare_bibliography_heading(blocks) == blocks


def test_strip_endmatter_sections_drops_tail_biblio_contacts_and_copyright() -> None:
    blocks: list[ir.Block] = [
        ir.Heading(level=1, inlines=[ir.Text("Book")]),
        *[
            ir.Paragraph(inlines=[ir.Text(f"body {i}")])
            for i in range(20)
        ],
        ir.Heading(level=2, inlines=[ir.Text("Библиография")]),
        ir.Paragraph(inlines=[ir.Text("70+1")]),
        ir.Heading(level=2, inlines=[ir.Text("Контакты")]),
        ir.Paragraph(inlines=[ir.Text("Donation details")]),
        ir.Heading(level=2, inlines=[ir.Text("Копирайт")]),
        ir.Paragraph(inlines=[ir.Text("© Сергей Орехов (Панкратиус), 2025, 2026")]),
    ]

    out = normalize.strip_endmatter_sections(blocks)

    text = "\n".join(_block_texts(out))
    assert "body 19" in text
    assert "Библиография" not in text
    assert "Контакты" not in text
    assert "Копирайт" not in text
    assert "70+1" not in text
    assert "Donation details" not in text
    assert "© Сергей" not in text


def test_strip_endmatter_sections_keeps_mid_document_contact_section() -> None:
    blocks: list[ir.Block] = [
        ir.Heading(level=1, inlines=[ir.Text("Book")]),
        *[
            ir.Paragraph(inlines=[ir.Text(f"before {i}")])
            for i in range(20)
        ],
        ir.Heading(level=2, inlines=[ir.Text("Контакты")]),
        ir.Paragraph(inlines=[ir.Text("This is a real in-body section.")]),
        *[
            ir.Paragraph(inlines=[ir.Text(f"after {i}")])
            for i in range(200)
        ],
    ]

    assert normalize.strip_endmatter_sections(blocks) == blocks


def test_strip_endmatter_sections_drops_mid_document_bibliography_section() -> None:
    blocks: list[ir.Block] = [
        ir.Heading(level=1, inlines=[ir.Text("Book")]),
        ir.Paragraph(inlines=[ir.Text("body before")]),
        ir.Heading(level=3, inlines=[ir.Text("БИБЛИОГРАФИЯ")]),
        ir.Paragraph(inlines=[ir.Text("Catalog introduction.")]),
        ir.Heading(level=2, inlines=[ir.Text("Next chapter")]),
        ir.Paragraph(inlines=[ir.Text("body after")]),
    ]

    assert _block_texts(normalize.strip_endmatter_sections(blocks)) == [
        "Book",
        "body before",
        "Next chapter",
        "body after",
    ]


# ---------------------------------------------------------------------------
# thematic breaks + heading demotion
# ---------------------------------------------------------------------------


def test_thematic_break_from_stars_paragraph() -> None:
    out = normalize.thematic_breaks([ir.Paragraph(inlines=[ir.Text("***")])])
    assert len(out) == 1 and isinstance(out[0], ir.ThematicBreak)


def test_empty_heading_is_dropped_before_markdown() -> None:
    out = normalize.drop_empty_headings([
        ir.Heading(level=1, inlines=[ir.Text(" \n ")]),
        ir.Heading(level=2, inlines=[ir.Text("Real section")]),
    ])
    assert out == [ir.Heading(level=2, inlines=[ir.Text("Real section")])]


def test_demote_headings_h1_to_h2() -> None:
    h = ir.Heading(level=1, inlines=[ir.Text("Title")])
    out = normalize.demote_headings([h], 1)
    demoted = out[0]
    assert isinstance(demoted, ir.Heading) and demoted.level == 2


def test_bold_heading_with_trailing_break_has_no_trailing_space() -> None:
    doc = ir.Document(blocks=[
        ir.Heading(
            level=4,
            inlines=[
                ir.Emphasis("strong", [ir.Text("Title"), ir.LineBreak()]),
            ],
        ),
    ])
    assert lower.lower(doc, "en") == "#### Title\n"


# ---------------------------------------------------------------------------
# signatures + epigraphs from right alignment
# ---------------------------------------------------------------------------


def test_right_aligned_signature_detected() -> None:
    para = ir.Paragraph(inlines=[ir.Text("Панкратиус")], facts=ir.SourceFacts(align="right"))
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
    verse = [b for b in out if _is_verse(b)]
    assert verse and isinstance(verse[0], ir.LineatedBlock)
    assert verse[0].register is ir.Register.VERSE
    lines = [normalize.inline_plain(line.inlines) for s in verse[0].stanzas for line in s]
    assert lines == ["Свет мой тихий", "в сердце горит", "и не гаснет"]


def test_lineation_stage_builds_lineated_block_before_register_promotion() -> None:
    blocks: list[ir.Block] = [
        ir.Heading(level=2, inlines=[ir.Text("Молитва")]),
        ir.Paragraph(inlines=[ir.Text("Свет мой тихий")]),
        ir.Paragraph(inlines=[ir.Text("в сердце горит")]),
    ]

    lineated = normalize.lineated_blocks(blocks)
    assert isinstance(lineated[1], ir.LineatedBlock)
    assert lineated[1].evidence == ir.LineationEvidence(inferred_source_rows=True)
    assert not any(_is_verse(b) for b in lineated)

    promoted = normalize.verse_blocks(blocks)
    assert _is_verse(promoted[1])

    staged_promoted = normalize.verse_blocks(lineated)
    assert _is_verse(staged_promoted[1])


def test_register_promotion_does_not_create_lineation() -> None:
    blocks: list[ir.Block] = [
        ir.Heading(level=2, inlines=[ir.Text("Молитва")]),
        ir.Paragraph(inlines=[ir.Text("Свет мой тихий")]),
        ir.Paragraph(inlines=[ir.Text("в сердце горит")]),
    ]

    promoted = normalize.promote_verse_register(blocks)

    assert [type(b).__name__ for b in promoted] == ["Heading", "Paragraph", "Paragraph"]


def test_lineation_stage_preserves_source_row_inference_for_register_promotion() -> None:
    blocks: list[ir.Block] = [
        ir.Paragraph(inlines=[ir.Text("Я — Свет.")], facts=ir.SourceFacts(lineation_group=7)),
        ir.Paragraph(inlines=[ir.Text("Я — Слово.")], facts=ir.SourceFacts(lineation_group=7)),
        ir.Paragraph(inlines=[ir.Text("Я — Дыхание.")], facts=ir.SourceFacts(lineation_group=7)),
    ]

    lineated = normalize.lineated_blocks(blocks)
    direct = normalize.verse_blocks(blocks)
    staged = normalize.verse_blocks(lineated)

    assert isinstance(lineated[0], ir.LineatedBlock)
    assert lineated[0].evidence == ir.LineationEvidence(inferred_source_rows=True)
    assert len(direct) == 1 and _is_verse(direct[0])
    assert len(staged) == 1 and _is_verse(staged[0])


def test_lineation_stage_combines_source_spans() -> None:
    out = normalize.lineated_blocks([
        ir.Heading(level=2, inlines=[ir.Text("Глава")], source_span=ir.SourceSpan(9, 9)),
        ir.Paragraph(inlines=[ir.Text("Я — Свет.")], source_span=ir.SourceSpan(10, 10)),
        ir.Paragraph(inlines=[ir.Text("Я — Слово.")], source_span=ir.SourceSpan(11, 11)),
    ])

    assert isinstance(out[1], ir.LineatedBlock)
    assert out[1].source_span == ir.SourceSpan(10, 11)


def test_lineation_stage_keeps_internal_empty_paragraph_in_source_span() -> None:
    out = normalize.lineated_blocks([
        ir.Paragraph(
            inlines=[ir.Text("первая строка")],
            source_span=ir.SourceSpan(1, 1),
        ),
        ir.Paragraph(inlines=[], facts=ir.SourceFacts(empty=True), source_span=ir.SourceSpan(2, 2)),
        ir.Paragraph(
            inlines=[ir.Text("вторая строка")],
            source_span=ir.SourceSpan(3, 3),
        ),
        ir.Paragraph(
            inlines=[ir.Text("третья строка")],
            source_span=ir.SourceSpan(4, 4),
        ),
    ])

    assert isinstance(out[0], ir.LineatedBlock)
    assert out[0].source_span == ir.SourceSpan(1, 4)


def test_lineation_stage_ignores_edge_empty_paragraphs_for_source_span() -> None:
    out = normalize.lineated_blocks([
        ir.Paragraph(
            inlines=[ir.Text("первая строка")],
            source_span=ir.SourceSpan(10, 10),
        ),
        ir.Paragraph(
            inlines=[ir.Text("вторая строка")],
            source_span=ir.SourceSpan(11, 11),
        ),
        ir.Paragraph(inlines=[], facts=ir.SourceFacts(empty=True), source_span=None),
    ])

    assert isinstance(out[0], ir.LineatedBlock)
    assert out[0].source_span == ir.SourceSpan(10, 11)


def test_merged_source_span_requires_complete_provenance() -> None:
    assert ir.merge_source_spans([
        ir.SourceSpan(10, 10),
        None,
        ir.SourceSpan(12, 12),
    ]) is None


def test_explicit_hard_break_lineation_survives_without_verse_register() -> None:
    long_line = "Это намеренно длинная прозаическая строка, которая намного длиннее стихотворной строки и не должна получать стиховой регистр."
    assert len(long_line) > normalize.VERSE_SHORT_LINE_MAX
    blocks: list[ir.Block] = [
        ir.Paragraph(inlines=[ir.Text(long_line), ir.LineBreak(), ir.Text("короткая строка")]),
    ]

    out = normalize.verse_blocks(blocks)

    assert len(out) == 1
    assert isinstance(out[0], ir.LineatedBlock)
    body = lower.lower(ir.Document(blocks=out), "ru")
    assert f"{long_line}  \nкороткая строка" in body
    assert 'class="lineated verse"' not in body


def test_blank_paragraph_is_internal_stanza_break_only_between_lineated_neighbors() -> None:
    prose = (
        "Это длинное прозаическое предложение закрывает lineated run and must "
        "remain a normal paragraph afterwards, because it is too long to be a "
        "lineated source line."
    )
    blocks: list[ir.Block] = [
        ir.Paragraph(inlines=[ir.Text("первая строка")]),
        ir.Paragraph(inlines=[], facts=ir.SourceFacts(empty=True)),
        ir.Paragraph(inlines=[ir.Text("вторая строка")]),
        ir.Paragraph(inlines=[], facts=ir.SourceFacts(empty=True)),
        ir.Paragraph(inlines=[ir.Text(prose)]),
    ]

    out = normalize.lineated_blocks(blocks)

    assert isinstance(out[0], ir.LineatedBlock)
    assert [
        [normalize.inline_plain(line.inlines) for line in stanza]
        for stanza in out[0].stanzas
    ] == [["первая строка"], ["вторая строка"]]
    assert isinstance(out[1], ir.Paragraph)
    assert normalize.inline_plain(out[1].inlines) == prose


def test_leading_blank_boundary_is_not_lineation_or_register_evidence() -> None:
    prose = (
        "Это длинное прозаическое предложение открывает обычный прозаический "
        "контекст и не должно превращать следующий короткий фрагмент в стихи."
    )
    blocks: list[ir.Block] = [
        ir.Paragraph(inlines=[ir.Text(prose)]),
        ir.Paragraph(inlines=[], facts=ir.SourceFacts(empty=True)),
        ir.Paragraph(inlines=[ir.Text("первая короткая строка")]),
        ir.Paragraph(inlines=[ir.Text("вторая короткая строка")]),
        ir.Paragraph(inlines=[ir.Text("третья короткая строка")]),
    ]

    out = normalize.verse_blocks(blocks)

    assert not any(isinstance(b, ir.LineatedBlock) for b in out)


def test_compact_strong_opener_callout_preserves_lineation_without_verse_register() -> None:
    blocks: list[ir.Block] = [
        ir.Paragraph(inlines=[ir.Emphasis("strong", [ir.Text("Что Я хочу, чтобы ты знал:")])]),
        ir.Paragraph(inlines=[ir.Text("Он не один.")]),
        ir.Paragraph(inlines=[ir.Text("Я с ним.")]),
        ir.Paragraph(inlines=[ir.Text("Я в нём.")]),
        ir.Paragraph(inlines=[ir.Text("Я через него.")]),
    ]

    out = normalize.verse_blocks(blocks)

    assert len(out) == 1
    assert isinstance(out[0], ir.LineatedBlock)
    assert [
        normalize.inline_plain(line.inlines)
        for stanza in out[0].stanzas
        for line in stanza
    ] == [
        "Что Я хочу, чтобы ты знал:",
        "Он не один.",
        "Я с ним.",
        "Я в нём.",
        "Я через него.",
    ]


def test_indented_strong_opener_callout_stays_prose() -> None:
    blocks: list[ir.Block] = [
        ir.Paragraph(
            inlines=[ir.Emphasis("strong", [ir.Text("Что Я хочу, чтобы ты знал:")])],
            facts=ir.SourceFacts(indented=True),
        ),
        ir.Paragraph(inlines=[ir.Text("Он не один.")], facts=ir.SourceFacts(indented=True)),
        ir.Paragraph(inlines=[ir.Text("Я с ним.")], facts=ir.SourceFacts(indented=True)),
        ir.Paragraph(inlines=[ir.Text("Я в нём.")], facts=ir.SourceFacts(indented=True)),
    ]

    out = normalize.verse_blocks(blocks)

    assert not any(isinstance(b, ir.LineatedBlock) for b in out)


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
    # is NOT a verse candidate — a run of them stays prose, not a verse register.
    blocks: list[ir.Block] = [
        ir.Heading(level=2, inlines=[ir.Text("Глава")]),
        ir.Paragraph(inlines=[ir.Text("Свет не был сотворён."), ir.SoftBreak(), ir.Text("Он не возник."), ir.SoftBreak(), ir.Text("Он — Есть.")]),
        ir.Paragraph(inlines=[], facts=ir.SourceFacts(empty=True)),
        ir.Paragraph(inlines=[ir.Text("Он не движется."), ir.SoftBreak(), ir.Text("Он просто светит.")]),
    ]
    out = normalize.verse_blocks(blocks)
    assert not any(_is_verse(b) for b in out)


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
    verse = [b for b in out if _is_verse(b)]
    assert verse, "an Emph-nested-LineBreak paragraph must be detected as verse"
    lines = [normalize.inline_plain(line.inlines) for s in verse[0].stanzas for line in s]
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
    # Within-stanza lineation is two-trailing-space hard breaks (the cross-consumer
    # encoding); the final line of the stanza closes with the blank-line separator.
    assert stanzas[1] == "первая строка  \nвторая строка"


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


def test_lower_ignores_source_span_metadata() -> None:
    with_span = ir.Document(blocks=[
        ir.Paragraph(inlines=[ir.Text("same text")], source_span=ir.SourceSpan(10, 10)),
        ir.LineatedBlock(
            stanzas=[[ir.Line([ir.Text("line one")]), ir.Line([ir.Text("line two")])]],
            source_span=ir.SourceSpan(11, 12),
        ),
    ])
    without_span = ir.Document(blocks=[
        ir.Paragraph(inlines=[ir.Text("same text")]),
        ir.LineatedBlock(stanzas=[[ir.Line([ir.Text("line one")]), ir.Line([ir.Text("line two")])]]),
    ])

    assert lower.lower(with_span, "ru") == lower.lower(without_span, "ru")


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
    vb = ir.LineatedBlock(stanzas=[[ir.Line([
        ir.DirectionalSpan(direction="rtl", children=[ir.Text("יהוה")]),
    ])]], register=ir.Register.VERSE)
    out = lower._lineated_md(vb, "ru")
    assert out is not None
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


def test_lower_lineated_block_emits_base_wrapper_with_hard_break_lines() -> None:
    # Lineated prose preserves authored line boundaries using the same two-space
    # hard-break encoding as verse, but carries only the base `.lineated` wrapper.
    lb = ir.LineatedBlock(stanzas=[
        [ir.Line([ir.Text("line one")]), ir.Line([ir.Text("line "), ir.Emphasis("strong", [ir.Text("two")])])],
        [ir.Line([ir.Text("third line")])],
    ])
    body = lower.lower(ir.Document(blocks=[lb]), "ru")
    assert body == '<div class="lineated">\n\nline one  \nline **two**\n\nthird line\n\n</div>\n'
    assert 'class="lineated verse"' not in body


def test_lower_lineated_image_interrupts_wrapper_as_standalone_block() -> None:
    # A DOCX drawing can be anchored into the same source paragraph as text, but
    # canonical Markdown keeps body images as standalone blocks. The lineated
    # wrapper must therefore split around the image instead of producing
    # `text ![](…)` inside `.lineated`.
    lb = ir.LineatedBlock(stanzas=[
        [ir.Line([
            ir.Text("before"),
            ir.ImageInline(src="media/pic.jpg", alt="", asset_id="abc123.jpg"),
            ir.Text("after"),
        ]), ir.Line([ir.Text("tail")])],
    ])
    body = lower.lower(ir.Document(blocks=[lb]), "ru")
    assert body == (
        '<div class="lineated">\n\n'
        "before\n\n"
        "</div>\n\n"
        "![Иллюстрация](./images/abc123.jpg)\n\n"
        '<div class="lineated">\n\n'
        "after  \n"
        "tail\n\n"
        "</div>\n"
    )


def test_lower_lineated_block_escapes_structural_markers() -> None:
    # Lineated prose is parsed as ordinary Markdown inside its wrapper, so literal
    # source lines that look like headings/lists must be escaped just like verse
    # lines.
    lb = ir.LineatedBlock(stanzas=[
        [ir.Line([ir.Text("### not a heading")]), ir.Line([ir.Text("1. not a list")]), ir.Line([ir.Text("- not a bullet")])],
    ])
    body = lower.lower(ir.Document(blocks=[lb]), "ru")
    assert "\\### not a heading  " in body
    assert "1\\. not a list  " in body
    assert "\\- not a bullet" in body


def test_lower_lineated_block_escapes_literal_inline_markup() -> None:
    # A lineated-prose line is still Markdown inside its wrapper, so literal DOCX
    # text must be inert: no raw HTML and no accidental markdown link should
    # survive from Text nodes.
    lb = ir.LineatedBlock(stanzas=[
        [ir.Line([ir.Text("<script>alert(1)</script>")]), ir.Line([ir.Text("[not a link](https://example.com)")])],
    ])
    body = lower.lower(ir.Document(blocks=[lb]), "ru")
    assert "<script>" not in body
    assert "\\<script\\>alert(1)\\</script\\>" in body
    assert "[not a link](https://example.com)" not in body
    assert "\\[not a link\\]" in body


def test_lower_verse_block_emits_div_with_lines() -> None:
    # The cross-consumer canonical encoding: a blank line after `<div>` (so
    # CommonMark parses the inside), two TRAILING SPACES on every non-final stanza
    # line (the hard break), the final line bare, and a blank line before `</div>`.
    vb = ir.LineatedBlock(
        stanzas=[[ir.Line([ir.Text("line one")]), ir.Line([ir.Text("line two")])]],
        register=ir.Register.VERSE,
    )
    body = lower.lower(ir.Document(blocks=[vb]), "ru")
    assert body == '<div class="lineated verse">\n\nline one  \nline two\n\n</div>\n'


def test_lower_verse_block_markdown_emphasis_and_stanza_break() -> None:
    # Emphasis lowers to Markdown `*`/`**` (not HTML), stanzas are blank-line
    # separated, and a `***` separator stanza becomes a thematic-break line.
    vb = ir.LineatedBlock(stanzas=[
        [ir.Line([ir.Text("plain "), ir.Emphasis("strong", [ir.Text("bold")])])],
        [ir.Line([ir.Text("***")])],
        [ir.Line([ir.Emphasis("emph", [ir.Text("ital")]), ir.Text(" tail")])],
    ], register=ir.Register.VERSE)
    body = lower.lower(ir.Document(blocks=[vb]), "ru")
    assert body == (
        '<div class="lineated verse">\n\n'
        "plain **bold**\n\n"
        "***\n\n"
        "*ital* tail\n\n"
        "</div>\n"
    )


def test_lower_verse_block_escapes_leading_markdown_markers() -> None:
    # The blank line after `<div>` makes verse contents parse as Markdown. Literal
    # source lines that look like block syntax must therefore be escaped so they do
    # not become headings/lists and pollute the generated page ToC.
    vb = ir.LineatedBlock(stanzas=[
        [ir.Line([ir.Text("### not a heading")]), ir.Line([ir.Text("1. not a list")]), ir.Line([ir.Text("- not a bullet")])],
    ], register=ir.Register.VERSE)
    body = lower.lower(ir.Document(blocks=[vb]), "ru")
    assert "\\### not a heading  " in body
    assert "1\\. not a list  " in body
    assert "\\- not a bullet" in body


def test_lower_signature_emits_p_signature() -> None:
    body = lower.lower(ir.Document(blocks=[ir.Signature(lines=["Панкратиус"])]), "ru")
    assert body.strip() == '<p class="signature">\nПанкратиус\n</p>'


def test_lower_poem_keeps_lines_and_stanza_breaks() -> None:
    # Two stanzas. Within a stanza, lineation is a two-trailing-space hard break on
    # every non-final line (the cross-consumer encoding that survives Astro, pandoc
    # PDF/EPUB, and the public-Markdown export); the final line of a stanza closes
    # with the blank-line separator instead. Poems are whole-body verse (no wrapper).
    doc = ir.Document(blocks=[
        ir.Paragraph(inlines=[ir.Text("first line")]),
        ir.Paragraph(inlines=[ir.Text("second line")]),
        ir.Paragraph(inlines=[], facts=ir.SourceFacts(empty=True)),
        ir.Paragraph(inlines=[ir.Text("third line")]),
    ])
    body = lower.lower(doc, "ru", poem=True)
    assert body == "first line  \nsecond line\n\nthird line\n"


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
    doc, planned = lower.assign_assets(doc, media_root)

    # No asset planned for the escaping ref; the ref is DROPPED (not kept).
    assert planned == []
    assert _para(doc.blocks[0]).inlines == [], "the escaping image ref must be dropped"
    # A FATAL diagnostic surfaced (not a silent read of the outside file).
    _assert_diagnostic(doc, "fatal", "import.image-unresolved")
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
    doc, planned = lower.assign_assets(doc, media_root)

    assert planned == []
    assert _para(doc.blocks[0]).inlines == [], "the parent-escaping image ref must be dropped"
    _assert_diagnostic(doc, "fatal", "import.image-unresolved")
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
    doc, planned = lower.assign_assets(doc, media_root)

    assert len(planned) == 1
    assert planned[0].rel_within.startswith("images/")
    img = _para(doc.blocks[0]).inlines[0]
    assert isinstance(img, ir.ImageInline) and img.asset_id is not None


def test_asset_inline_image_inside_lineated_block_is_planned(tmp_path: Path) -> None:
    # `LineatedBlock` participates in the shared inline traversal, so images nested
    # in its display lines are resolved and rewritten just like prose/verse images.
    media_root = tmp_path / "media"
    media_root.mkdir()
    img_file = media_root / "pic.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\nlineated-image")

    doc = ir.Document(blocks=[
        ir.LineatedBlock(stanzas=[
            [ir.Line([ir.Text("see "), ir.ImageInline(src="pic.png", alt="caption")])],
        ]),
    ])
    doc, planned = lower.assign_assets(doc, media_root)

    assert len(planned) == 1
    assert isinstance(doc.blocks[0], ir.LineatedBlock)
    img = doc.blocks[0].stanzas[0][0].inlines[1]
    assert isinstance(img, ir.ImageInline)
    assert img.asset_id is not None
    assert f"./images/{img.asset_id}" in lower.lower(doc, "ru")


# ---------------------------------------------------------------------------
# Bug 4: a LineBlock is lineated content (not dropped); a genuinely-unknown block
# PRESERVES its readable text AND surfaces a diagnostic (never silently dropped).
# ---------------------------------------------------------------------------


def test_lower_line_block_produces_lineated_lines() -> None:
    # Bug 4(a): a LineBlock (mapped to a LineatedBlock by the adapter) lowers to
    # non-empty hard-break Markdown preserving its lines — not empty output.
    lb = ir.LineatedBlock(stanzas=[[ir.Line([ir.Text("Roses are red,")]), ir.Line([ir.Text("violets are blue.")])]])
    body = lower.lower(ir.Document(blocks=[lb]), "ru")
    assert "Roses are red," in body
    assert "violets are blue." in body
    assert 'class="lineated verse"' not in body
    assert "Roses are red,  \n" in body


def test_lower_unknown_block_preserves_text_and_emits_diagnostic() -> None:
    # Bug 4(b): a genuinely-unknown block must NOT be silently dropped — its readable
    # text is emitted AND a diagnostic is surfaced on the document.
    doc = ir.Document(blocks=[ir.UnknownBlock(note="Bogus", text="important reading content")])
    body = lower.lower(doc, "ru")
    assert "important reading content" in body
    _assert_diagnostic(doc, "warning", "import.unknown-block")


def test_lower_empty_unknown_block_still_emits_diagnostic() -> None:
    # An unknown block with NO recoverable text (e.g. Pandoc Null) carries no
    # reading content, but its presence is still surfaced as a diagnostic — the
    # importer never drops a block silently.
    doc = ir.Document(blocks=[ir.UnknownBlock(note="Null", text="")])
    lower.lower(doc, "ru")
    _assert_diagnostic(doc, "warning", "import.unknown-block")


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
    _assert_diagnostic(doc, "warning", "import.unsafe-url")


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
    vb = ir.LineatedBlock(
        stanzas=[[ir.Line([ir.Link([ir.Text("x")], "javascript:alert(1)")])]],
        register=ir.Register.VERSE,
    )
    doc = ir.Document(blocks=[vb])
    body = lower.lower(doc, "ru")
    assert "javascript:" not in body
    assert "<a " not in body  # the anchor element is gone
    assert ">x<" not in body or "x" in body  # the text remains


def test_unsafe_link_in_lineated_block_drops_target_keeps_text() -> None:
    lb = ir.LineatedBlock(stanzas=[
        [ir.Line([ir.Text("before "), ir.Link([ir.Text("x")], "javascript:alert(1)")])],
    ])
    doc = ir.Document(blocks=[lb])
    body = lower.lower(doc, "ru")
    assert "javascript:" not in body
    assert "before x" in body
    assert "](" not in body
    _assert_diagnostic(doc, "warning", "import.unsafe-url")


def test_unsafe_scheme_image_is_dropped_with_warning() -> None:
    # An image whose src is an unsafe non-image scheme (e.g. data:text/html or
    # javascript:) must be dropped entirely (no <img>, no ![]() ) with a warning.
    img = ir.ImageInline(src="javascript:alert(1)", alt="bad")
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[ir.Text("before "), img, ir.Text(" after")])])
    body = lower.lower(doc, "ru")
    assert "javascript:" not in body
    assert "![" not in body
    assert "before" in body and "after" in body
    _assert_diagnostic(doc, "warning", "import.unsafe-url")


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
    doc, _planned = lower.assign_assets(doc, media)
    _assert_diagnostic(doc, "fatal", "import.image-unresolved")
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
    doc, _planned = lower.assign_assets(doc, media)
    _assert_diagnostic(doc, "fatal", "import.image-unresolved")
    body = lower.lower(doc, "ru")
    assert "/etc/passwd" not in body


def test_resolvable_local_image_assigns_asset_and_is_not_fatal(tmp_path: Path) -> None:
    # A normal in-root image resolves to a content-hash asset; no fatal.
    media = tmp_path / "media" / "media"
    media.mkdir(parents=True)
    (media / "image1.png").write_bytes(b"\x89PNGfakebytes")
    img = ir.ImageInline(src="media/image1.png", alt="x")
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[img])])
    doc, planned = lower.assign_assets(doc, tmp_path / "media")
    assert planned, "a resolvable image must produce a planned asset"
    assert not [d for d in doc.diagnostics if d.severity == "fatal"]
    body = lower.lower(doc, "ru")
    assert "./images/" in body


def test_inline_body_image_lowers_as_standalone_block() -> None:
    img = ir.ImageInline(src="m/img.png", alt="", asset_id="abc123.png")
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[ir.Text("before "), img, ir.Text(" after")])])

    body = lower.lower(doc, "ru")

    assert body == "before\n\n![Иллюстрация](./images/abc123.png)\n\nafter\n"


def test_poem_inline_body_image_lowers_as_standalone_block() -> None:
    img = ir.ImageInline(src="m/img.png", alt="", asset_id="abc123.png")
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[ir.Text("before"), img, ir.Text("after")])])

    body = lower.lower(doc, "en", poem=True)

    assert body == "before\n\n![Illustration](./images/abc123.png)\n\nafter\n"


def test_remote_http_image_is_kept_not_fatal(tmp_path: Path) -> None:
    # A safe REMOTE image (http/https) is not a LOCAL image: it is a valid remote
    # ref, kept as-is, never fatal.
    media = tmp_path / "media"
    media.mkdir()
    img = ir.ImageInline(src="https://example.org/a.png", alt="x")
    doc = ir.Document(blocks=[ir.Paragraph(inlines=[img])])
    doc, _planned = lower.assign_assets(doc, media)
    assert not [d for d in doc.diagnostics if d.severity == "fatal"]
    body = lower.lower(doc, "ru")
    assert "https://example.org/a.png" in body


def test_container_forms_in_sync() -> None:
    # The union (ir.ContainerInlineNode) and the isinstance tuple (ir.ContainerInline)
    # must list the same kinds — they are maintained as two forms of one set.
    from typing import get_args

    assert set(get_args(ir.ContainerInlineNode.__value__)) == set(ir.ContainerInline)


def test_register_lowering_tables_total() -> None:
    # Both register->emission registries must cover every Register member (a
    # Mapping is not exhaustiveness-checked by the type checker, so pin it here).
    assert set(lower.LINEATED_CLASS) == set(ir.Register)
    assert set(lower.QUOTE_LOWERING) == set(ir.Register)


def test_emph_tables_total() -> None:
    # The Markdown emphasis table must cover every EmphKind (a dict[Literal,...] is
    # NOT exhaustiveness-checked by the type checker, so pin it here).
    from typing import get_args

    kinds = set(get_args(ir.EmphKind))
    assert set(lower._EMPH_MD) == kinds


def test_pipeline_never_mutates_its_input_document() -> None:
    # The frozen-IR canary: every pass rebuilds — running the FULL book pipeline
    # must leave the input document (and everything reachable from it) untouched.
    # The fixture covers the pass surface: headings (demotion), lineation-eligible
    # rows with facts + spans (fold + register), an emphasis husk (artifact strip),
    # a lineated block (AI-alt / inline maps), a list and a quote (recursive maps),
    # and a footnote body.
    import copy

    from pancratius.passes.pipeline import BOOK_PASSES, Context, run

    doc = ir.Document(
        blocks=[
            ir.Heading(level=1, inlines=[ir.Text("Глава")], source_span=ir.SourceSpan(0, 0)),
            ir.Paragraph(
                inlines=[ir.Text("Свет мой тихий,")],
                facts=ir.SourceFacts(lineation_group=1),
                source_span=ir.SourceSpan(1, 1),
            ),
            ir.Paragraph(
                inlines=[ir.Text("в сердце горит.")],
                facts=ir.SourceFacts(lineation_group=1),
                source_span=ir.SourceSpan(2, 2),
            ),
            ir.Paragraph(inlines=[], facts=ir.SourceFacts(empty=True), source_span=ir.SourceSpan(3, 3)),
            ir.Paragraph(
                inlines=[ir.Text("Обычная проза. "), ir.Emphasis("strong", [ir.Text(" ")])],
                source_span=ir.SourceSpan(4, 4),
            ),
            ir.LineatedBlock(
                stanzas=[[ir.Line([ir.Text("строка раз")], ir.SourceSpan(5, 5)),
                          ir.Line([ir.Text("строка два")], ir.SourceSpan(6, 6))]],
                evidence=ir.LineationEvidence(hard_break=True),
                source_span=ir.SourceSpan(5, 6),
            ),
            ir.QuoteBlock(blocks=[ir.Paragraph(inlines=[ir.Text("цитата")])]),
            ir.ListBlock(ordered=True, items=[[ir.Paragraph(inlines=[ir.Text("пункт")])]]),
            ir.ImageBlock(src="m/p.png", alt="ok"),
            ir.ThematicBreak(),
        ],
        footnotes=[ir.FootnoteDef(id=1, blocks=[ir.Paragraph(inlines=[ir.Text("сноска")])])],
    )
    snapshot = copy.deepcopy(doc)

    out = run(doc, Context(lang="ru"), BOOK_PASSES)

    assert out is not doc
    assert doc == snapshot, "a pass mutated the input document (aliased mutation)"
