"""Q1 compact-coda repair after lineation folding."""

from __future__ import annotations

from pancratius import ir
from pancratius.ir.inlines import inline_plain
from pancratius.passes.lineation import attach_compact_coda_lineation
from pancratius.passes.pipeline import Context, run


def _fold(
    start: int,
    end: int,
    *lines: str,
    register: ir.Register = ir.Register.ORDINARY,
    evidence: ir.LineationEvidence | None = None,
) -> ir.LineatedBlock:
    assert len(lines) == end - start + 1
    return ir.LineatedBlock(
        stanzas=[[
            ir.Line([ir.Text(line)], span=ir.SourceSpan(start + offset, start + offset))
            for offset, line in enumerate(lines)
        ]],
        register=register,
        evidence=evidence or ir.LineationEvidence(),
        source_span=ir.SourceSpan(start, end),
    )


def _empty(ordinal: int) -> ir.Paragraph:
    return ir.Paragraph(
        inlines=[],
        facts=ir.SourceFacts(empty=True),
        source_span=ir.SourceSpan(ordinal, ordinal),
    )


def _heading(ordinal: int = 20) -> ir.Heading:
    return ir.Heading(
        level=2,
        inlines=[ir.Text("Следующая глава")],
        source_span=ir.SourceSpan(ordinal, ordinal),
    )


def _stanzas(block: ir.Block) -> list[list[str]]:
    assert isinstance(block, ir.LineatedBlock)
    return [
        [inline_plain(line.inlines) for line in stanza]
        for stanza in block.stanzas
    ]


def test_attach_compact_coda_lineation_fuses_before_register_assignment() -> None:
    blocks: list[ir.Block] = [
        _fold(
            10, 11,
            "Свет мой тихий,",
            "в сердце горит.",
            evidence=ir.LineationEvidence(stanza_break=True),
        ),
        _empty(12),
        _fold(
            13, 14,
            "Если готов —",
            "Я поведу тебя дальше.",
            evidence=ir.LineationEvidence(inferred_source_rows=True),
        ),
        _heading(15),
    ]

    out = attach_compact_coda_lineation(blocks)

    assert [type(block).__name__ for block in out] == ["LineatedBlock", "Heading"]
    merged = out[0]
    assert isinstance(merged, ir.LineatedBlock)
    assert merged.register is ir.Register.ORDINARY
    assert merged.source_span == ir.SourceSpan(10, 14)
    assert _stanzas(merged) == [
        ["Свет мой тихий,", "в сердце горит."],
        ["Если готов —", "Я поведу тебя дальше."],
    ]
    assert merged.evidence == ir.LineationEvidence(
        inferred_source_rows=True,
        stanza_break=True,
    )
    assert merged.lineation_repairs == (
        ir.LineationRepair(
            kind=ir.LineationRepairKind.COMPACT_CODA_ATTACHMENT,
            body_stanza_count=1,
            body_source_span=ir.SourceSpan(10, 11),
            body_evidence=ir.LineationEvidence(stanza_break=True),
            attached_source_span=ir.SourceSpan(13, 14),
            attached_evidence=ir.LineationEvidence(inferred_source_rows=True),
        ),
    )


def test_pipeline_runs_compact_coda_repair_before_assign_register() -> None:
    doc = ir.Document(blocks=[
        _fold(10, 11, "Свет мой тихий,", "в сердце горит."),
        _empty(12),
        ir.Paragraph(
            inlines=[ir.Text("Если готов —")],
            facts=ir.SourceFacts(lineation_group=2),
            source_span=ir.SourceSpan(13, 13),
        ),
        ir.Paragraph(
            inlines=[ir.Text("Я поведу тебя дальше.")],
            facts=ir.SourceFacts(lineation_group=2),
            source_span=ir.SourceSpan(14, 14),
        ),
        _heading(15),
    ])

    out = run(doc, Context(lang="ru"), until="assign_register")

    assert [type(block).__name__ for block in out.blocks] == ["LineatedBlock", "Heading"]
    merged = out.blocks[0]
    assert isinstance(merged, ir.LineatedBlock)
    assert _stanzas(merged) == [
        ["Свет мой тихий,", "в сердце горит."],
        ["Если готов —", "Я поведу тебя дальше."],
    ]
    assert (
        merged.lineation_repairs[0].kind
        is ir.LineationRepairKind.COMPACT_CODA_ATTACHMENT
    )


def test_attach_compact_coda_lineation_refuses_pseudo_heading_fragments() -> None:
    blocks: list[ir.Block] = [
        _fold(10, 11, "Я — Свет.", "Я — Слово."),
        _empty(12),
        _fold(
            13, 14,
            "138",
            "Вопрос:",
            evidence=ir.LineationEvidence(inferred_source_rows=True),
        ),
        _heading(15),
    ]

    out = attach_compact_coda_lineation(blocks)

    assert len([block for block in out if isinstance(block, ir.LineatedBlock)]) == 2
    assert _stanzas(out[0]) == [["Я — Свет.", "Я — Слово."]]
    assert _stanzas(out[2]) == [["138", "Вопрос:"]]


def test_attach_compact_coda_lineation_refuses_uninferred_neighboring_block() -> None:
    blocks: list[ir.Block] = [
        _fold(10, 11, "Я — Свет.", "Я — Слово."),
        _empty(12),
        _fold(13, 14, "Если готов —", "Я поведу тебя дальше."),
        _heading(15),
    ]

    out = attach_compact_coda_lineation(blocks)

    assert len([block for block in out if isinstance(block, ir.LineatedBlock)]) == 2
    assert _stanzas(out[0]) == [["Я — Свет.", "Я — Слово."]]
    assert _stanzas(out[2]) == [["Если готов —", "Я поведу тебя дальше."]]


def test_attach_compact_coda_lineation_refuses_next_section_prose_preview() -> None:
    blocks: list[ir.Block] = [
        _fold(10, 11, "Тело — это не обременение.", "Это — Мой Храм."),
        _empty(12),
        _fold(
            13, 14,
            "Следующая Песнь — о том, как простота тела становится орудием великого Творения.",
            "О том, как все действия становятся актом божественного сотворения.",
            evidence=ir.LineationEvidence(inferred_source_rows=True),
        ),
        _heading(15),
    ]

    out = attach_compact_coda_lineation(blocks)

    assert len([block for block in out if isinstance(block, ir.LineatedBlock)]) == 2
    assert _stanzas(out[0]) == [["Тело — это не обременение.", "Это — Мой Храм."]]
    assert _stanzas(out[2])[0][0].startswith("Следующая Песнь")
