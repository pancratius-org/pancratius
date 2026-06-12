"""The verse-register decision pass (`passes.register.assign_register`)."""

from __future__ import annotations

from pancratius import ir
from pancratius.passes.pipeline import Context
from pancratius.passes.register import (
    FEATURE_NAMES,
    RegisterModel,
    assign_register,
    scaffold_line_labeler,
    segment_lineated,
)


def _model(*, bias: float) -> RegisterModel:
    """A constant model: p = sigmoid(bias) for every block."""
    n = len(FEATURE_NAMES)
    return RegisterModel(
        version=0, langs=("ru",), features=FEATURE_NAMES,
        mean=(0.0,) * n, std=(1.0,) * n, coef=(0.0,) * n,
        intercept=bias, threshold=0.6,
    )


def _verse(*lines: str) -> ir.LineatedBlock:
    return ir.LineatedBlock(
        stanzas=[[ir.Line([ir.Text(t)]) for t in lines]], register=ir.Register.VERSE,
    )


def _is_verse(b: ir.Block) -> bool:
    return isinstance(b, ir.LineatedBlock) and b.register is ir.Register.VERSE


def _lineated(*lines: str, evidence: ir.LineationEvidence | None = None) -> ir.LineatedBlock:
    return ir.LineatedBlock(
        stanzas=[[ir.Line([ir.Text(t)]) for t in lines]],
        evidence=evidence or ir.LineationEvidence(),
    )


def _ctx(model: RegisterModel | None) -> Context:
    return Context(lang="ru", register_model=model)


def _doc(*blocks: ir.Block) -> ir.Document:
    return ir.Document(blocks=list(blocks))


DEMOTE = _model(bias=-3.0)   # p ~ 0.05: under threshold
PROMOTE = _model(bias=3.0)   # p ~ 0.95: over threshold


def test_no_model_runs_the_ladder() -> None:
    # stanza_break evidence + 3 short lines is a ladder promotion.
    doc = _doc(_lineated(
        "Свет мой тихий,", "в сердце горит,", "и не гаснет.",
        evidence=ir.LineationEvidence(stanza_break=True),
    ))
    doc = assign_register(doc, _ctx(None))
    assert _is_verse(doc.blocks[0])


def test_model_over_threshold_promotes() -> None:
    doc = _doc(_lineated("Тихая строка,", "ещё одна строка."))
    doc = assign_register(doc, _ctx(PROMOTE))
    block = doc.blocks[0]
    assert _is_verse(block)


def test_model_under_threshold_blocks_ladder_promotion() -> None:
    ev = ir.LineationEvidence(stanza_break=True)
    doc = _doc(_lineated("Сергей был обычным.", "Ну, почти.", "Он не кричал.", evidence=ev))
    doc = assign_register(doc, _ctx(DEMOTE))
    block = doc.blocks[0]
    assert isinstance(block, ir.LineatedBlock)
    assert block.register is ir.Register.ORDINARY
    assert block.evidence == ev  # provenance untouched by the decision


def test_named_section_takes_the_ladder_not_the_model() -> None:
    doc = _doc(
        ir.Heading(level=2, inlines=[ir.Text("Молитва")]),
        _lineated("Свет мой тихий,", "в сердце горит."),
    )
    doc = assign_register(doc, _ctx(DEMOTE))
    assert _is_verse(doc.blocks[1])


def test_scaffold_is_never_promoted_even_by_a_confident_model() -> None:
    doc = _doc(_lineated("— возражения религиозных систем,", "— возражения обычных людей."))
    doc = assign_register(doc, _ctx(PROMOTE))
    block = doc.blocks[0]
    assert isinstance(block, ir.LineatedBlock)
    assert block.register is ir.Register.ORDINARY


def test_equations_are_never_promoted() -> None:
    doc = _doc(_lineated("143 = 11 × 13", "а 153 = 9 × 17"))
    doc = assign_register(doc, _ctx(PROMOTE))
    block = doc.blocks[0]
    assert isinstance(block, ir.LineatedBlock)
    assert block.register is ir.Register.ORDINARY


# ---------------------------------------------------------------------------
# mixed-run segmentation (`segment_lineated`)
# ---------------------------------------------------------------------------


def _spanned_line(text: str, ordinal: int | None = None) -> ir.Line:
    span = ir.SourceSpan(ordinal, ordinal) if ordinal is not None else None
    return ir.Line([ir.Text(text)], span=span)


_EVIDENCE = ir.LineationEvidence(stanza_break=True)


def _verse_block(*stanzas: list[ir.Line]) -> ir.LineatedBlock:
    return ir.LineatedBlock(
        stanzas=list(stanzas),
        register=ir.Register.VERSE,
        evidence=_EVIDENCE,
        source_span=ir.merge_source_spans(
            line.span for stanza in stanzas for line in stanza
        ),
    )


def test_segment_pure_verse_run_does_not_split() -> None:
    block = _verse_block([
        _spanned_line("Свет мой тихий,", 10),
        _spanned_line("в сердце горит,", 11),
        _spanned_line("и не гаснет.", 12),
    ])
    assert segment_lineated(block, scaffold_line_labeler(block)) == [block]


def test_segment_verse_equation_verse_sandwich() -> None:
    block = _verse_block([
        _spanned_line("Свет мой тихий,", 10),
        _spanned_line("в сердце горит.", 11),
        _spanned_line("143 = 11 × 13", 12),
        _spanned_line("153 = 9 × 17", 13),
        _spanned_line("Ты читал и молчал,", 14),
        _spanned_line("я писал и ждал.", 15),
    ])
    fragments = segment_lineated(block, scaffold_line_labeler(block))
    assert len(fragments) == 3
    head, middle, tail = fragments
    assert isinstance(head, ir.LineatedBlock) and head.register is ir.Register.VERSE
    assert isinstance(middle, ir.LineatedBlock) and middle.register is ir.Register.ORDINARY
    assert isinstance(tail, ir.LineatedBlock) and tail.register is ir.Register.VERSE
    assert [ir.Text("143 = 11 × 13")] == middle.stanzas[0][0].inlines


def test_segment_spans_derive_from_member_lines() -> None:
    block = _verse_block([
        _spanned_line("Свет мой тихий,", 10),
        _spanned_line("в сердце горит.", 11),
        _spanned_line("143 = 11 × 13", 12),
        _spanned_line("153 = 9 × 17", 13),
        _spanned_line("Ты читал и молчал,", 14),
        _spanned_line("я писал и ждал.", 15),
    ])
    spans = [f.source_span for f in segment_lineated(block, scaffold_line_labeler(block))]
    assert spans == [
        ir.SourceSpan(10, 11), ir.SourceSpan(12, 13), ir.SourceSpan(14, 15),
    ]


def test_segment_copies_parent_evidence_to_every_fragment() -> None:
    block = _verse_block([
        _spanned_line("Свет мой тихий,", 10),
        _spanned_line("143 = 11 × 13", 11),
        _spanned_line("153 = 9 × 17", 12),
    ])
    fragments = segment_lineated(block, scaffold_line_labeler(block))
    assert len(fragments) == 2
    assert all(
        isinstance(f, ir.LineatedBlock) and f.evidence == _EVIDENCE for f in fragments
    )


def test_segment_respects_stanza_boundaries() -> None:
    # Stanza 1 is verse, stanza 2 is wholly equations: the split falls at the
    # stanza boundary and each fragment keeps whole stanzas.
    block = _verse_block(
        [_spanned_line("Свет мой тихий,", 10), _spanned_line("в сердце горит.", 11)],
        [_spanned_line("143 = 11 × 13", 13), _spanned_line("153 = 9 × 17", 14)],
    )
    head, tail = segment_lineated(block, scaffold_line_labeler(block))
    assert isinstance(head, ir.LineatedBlock) and head.register is ir.Register.VERSE
    assert isinstance(tail, ir.LineatedBlock) and tail.register is ir.Register.ORDINARY
    assert len(head.stanzas) == 1 and len(head.stanzas[0]) == 2
    assert len(tail.stanzas) == 1 and len(tail.stanzas[0]) == 2


def test_segment_fragment_spanning_stanzas_keeps_the_boundary() -> None:
    block = _verse_block(
        [_spanned_line("Свет мой тихий,", 10), _spanned_line("в сердце горит.", 11)],
        [_spanned_line("Ты читал и молчал,", 13), _spanned_line("я писал и ждал.", 14)],
        [_spanned_line("143 = 11 × 13", 16), _spanned_line("153 = 9 × 17", 17)],
    )
    head, tail = segment_lineated(block, scaffold_line_labeler(block))
    assert isinstance(head, ir.LineatedBlock) and head.register is ir.Register.VERSE
    assert len(head.stanzas) == 2  # the verse fragment keeps its stanza break
    assert isinstance(tail, ir.LineatedBlock) and tail.register is ir.Register.ORDINARY


def test_segment_single_scaffold_island_stays_with_the_run() -> None:
    # One dash line inside a litany is verse texture, never split out.
    block = _verse_block([
        _spanned_line("Свет мой тихий,", 10),
        _spanned_line("— возражение одно,", 11),
        _spanned_line("и не гаснет.", 12),
    ])
    assert segment_lineated(block, scaffold_line_labeler(block)) == [block]


def test_segment_single_line_scaffold_stanza_splits() -> None:
    # A stanza wholly scaffold splits out even as one line.
    block = _verse_block(
        [_spanned_line("Свет мой тихий,", 10), _spanned_line("в сердце горит.", 11)],
        [_spanned_line("143 = 11 × 13", 13)],
    )
    head, tail = segment_lineated(block, scaffold_line_labeler(block))
    assert isinstance(head, ir.LineatedBlock) and head.register is ir.Register.VERSE
    assert isinstance(tail, ir.LineatedBlock) and tail.register is ir.Register.ORDINARY


def test_segment_dash_scaffold_stanza_splits_as_ordinary() -> None:
    # A stanza that is wholly a dash enumeration (colon opener included)
    # splits out of the verse run.
    block = _verse_block(
        [_spanned_line("Свет мой тихий,", 10), _spanned_line("в сердце горит.", 11)],
        [
            _spanned_line("Он перечислил:", 13),
            _spanned_line("— возражения религиозных систем,", 14),
            _spanned_line("— возражения обычных людей.", 15),
        ],
    )
    head, tail = segment_lineated(block, scaffold_line_labeler(block))
    assert isinstance(head, ir.LineatedBlock) and head.register is ir.Register.VERSE
    assert isinstance(tail, ir.LineatedBlock) and tail.register is ir.Register.ORDINARY
    assert tail.source_span == ir.SourceSpan(13, 15)


def test_segment_dash_dialogue_pair_mid_stanza_stays_verse() -> None:
    # Contiguous dash lines MIXED with verse lines inside one stanza are
    # dialogue/litany texture (the refuted looser dash demotion), never split.
    block = _verse_block([
        _spanned_line("Но однажды сказал:", 10),
        _spanned_line("— А как узнать, кто ты?", 11),
        _spanned_line("— По тому, что ты делаешь.", 12),
        _spanned_line("И по тому, как ты слушаешь.", 13),
    ])
    assert segment_lineated(block, scaffold_line_labeler(block)) == [block]


def test_segment_fragments_tile_the_parent_span_across_gap_rows() -> None:
    # Stanza-gap blank rows (12, 16-17) sit between fragments; the preceding
    # fragment's span extends through them so per-ordinal coverage holds.
    block = _verse_block(
        [_spanned_line("Свет мой тихий,", 10), _spanned_line("в сердце горит.", 11)],
        [_spanned_line("143 = 11 × 13", 13), _spanned_line("153 = 9 × 17", 15)],
        [_spanned_line("Ты читал и молчал,", 18), _spanned_line("я писал и ждал.", 19)],
    )
    spans = [f.source_span for f in segment_lineated(block, scaffold_line_labeler(block))]
    assert spans == [
        ir.SourceSpan(10, 12), ir.SourceSpan(13, 17), ir.SourceSpan(18, 19),
    ]


def test_segment_spanless_lines_yield_spanless_fragments() -> None:
    block = _verse_block([
        _spanned_line("Свет мой тихий,"),
        _spanned_line("в сердце горит."),
        _spanned_line("143 = 11 × 13"),
        _spanned_line("153 = 9 × 17"),
    ])
    fragments = segment_lineated(block, scaffold_line_labeler(block))
    assert len(fragments) == 2
    assert all(f.source_span is None for f in fragments)


def test_assign_register_splits_promoted_mixed_run() -> None:
    # The pass itself: a verse-promoted run with an interior equation pair
    # comes out as three blocks, the middle one ORDINARY.
    block = ir.LineatedBlock(
        stanzas=[[
            _spanned_line("Свет мой тихий,", 10),
            _spanned_line("в сердце горит.", 11),
            _spanned_line("143 = 11 × 13", 12),
            _spanned_line("153 = 9 × 17", 13),
            _spanned_line("Ты читал и молчал,", 14),
            _spanned_line("я писал и ждал.", 15),
        ]],
        evidence=ir.LineationEvidence(stanza_break=True),
        source_span=ir.SourceSpan(10, 15),
    )
    doc = assign_register(_doc(block), _ctx(None))
    registers = [
        b.register for b in doc.blocks if isinstance(b, ir.LineatedBlock)
    ]
    assert registers == [
        ir.Register.VERSE, ir.Register.ORDINARY, ir.Register.VERSE,
    ]


def test_existing_verse_blocks_keep_coda_machinery() -> None:
    doc = _doc(
        _verse("Свет мой тихий,", "в сердце горит."),
        ir.Paragraph(inlines=[], facts=ir.SourceFacts(empty=True)),
        _lineated("Ты читал.", "Я писал."),
        ir.Heading(level=2, inlines=[ir.Text("Глава")]),
    )
    doc = assign_register(doc, _ctx(None))
    first = doc.blocks[0]
    assert isinstance(first, ir.LineatedBlock)
    assert first.register is ir.Register.VERSE
    assert len(first.stanzas) == 2  # the compact coda folded into the verse block
