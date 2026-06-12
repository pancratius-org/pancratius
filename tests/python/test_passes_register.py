"""The verse-register decision pass (`passes.register.assign_register`)."""

from __future__ import annotations

from pancratius import ir
from pancratius.passes.pipeline import Context
from pancratius.passes.register import FEATURE_NAMES, RegisterModel, assign_register


def _model(*, bias: float) -> RegisterModel:
    """A constant model: p = sigmoid(bias) for every block."""
    n = len(FEATURE_NAMES)
    return RegisterModel(
        version=0, langs=("ru",), features=FEATURE_NAMES,
        mean=(0.0,) * n, std=(1.0,) * n, coef=(0.0,) * n,
        intercept=bias, threshold=0.6,
    )


def _verse(*lines: str) -> ir.VerseBlock:
    return ir.VerseBlock(stanzas=[[[ir.Text(t)] for t in lines]])


def _lineated(*lines: str, evidence: ir.LineationEvidence | None = None) -> ir.LineatedBlock:
    return ir.LineatedBlock(
        stanzas=[[[ir.Text(t)] for t in lines]],
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
    assign_register(doc, _ctx(None))
    assert isinstance(doc.blocks[0], ir.VerseBlock)


def test_model_over_threshold_promotes() -> None:
    doc = _doc(_lineated("Тихая строка,", "ещё одна строка."))
    assign_register(doc, _ctx(PROMOTE))
    block = doc.blocks[0]
    assert isinstance(block, ir.VerseBlock)


def test_model_under_threshold_blocks_ladder_promotion() -> None:
    ev = ir.LineationEvidence(stanza_break=True)
    doc = _doc(_lineated("Сергей был обычным.", "Ну, почти.", "Он не кричал.", evidence=ev))
    assign_register(doc, _ctx(DEMOTE))
    block = doc.blocks[0]
    assert isinstance(block, ir.LineatedBlock)
    assert block.evidence == ev  # provenance untouched by the decision


def test_named_section_takes_the_ladder_not_the_model() -> None:
    doc = _doc(
        ir.Heading(level=2, inlines=[ir.Text("Молитва")]),
        _lineated("Свет мой тихий,", "в сердце горит."),
    )
    assign_register(doc, _ctx(DEMOTE))
    assert isinstance(doc.blocks[1], ir.VerseBlock)


def test_scaffold_is_never_promoted_even_by_a_confident_model() -> None:
    doc = _doc(_lineated("— возражения религиозных систем,", "— возражения обычных людей."))
    assign_register(doc, _ctx(PROMOTE))
    assert isinstance(doc.blocks[0], ir.LineatedBlock)


def test_equations_are_never_promoted() -> None:
    doc = _doc(_lineated("143 = 11 × 13", "а 153 = 9 × 17"))
    assign_register(doc, _ctx(PROMOTE))
    assert isinstance(doc.blocks[0], ir.LineatedBlock)


def test_existing_verse_blocks_keep_coda_machinery() -> None:
    doc = _doc(
        _verse("Свет мой тихий,", "в сердце горит."),
        ir.Paragraph(inlines=[], empty=True),
        _lineated("Ты читал.", "Я писал."),
        ir.Heading(level=2, inlines=[ir.Text("Глава")]),
    )
    assign_register(doc, _ctx(None))
    first = doc.blocks[0]
    assert isinstance(first, ir.VerseBlock)
    assert len(first.stanzas) == 2  # the compact coda folded into the verse block
