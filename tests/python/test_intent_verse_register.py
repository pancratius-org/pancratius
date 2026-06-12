"""The learned verse-register enrichment pass (pancratius.intent)."""

from __future__ import annotations

from pancratius import ir
from pancratius.intent.features import FEATURE_NAMES
from pancratius.intent.runtime import StudentModel, apply_verse_register


def _model(*, bias: float) -> StudentModel:
    """A constant student: p = sigmoid(bias) for every block."""
    n = len(FEATURE_NAMES)
    return StudentModel(
        version=0, features=FEATURE_NAMES,
        mean=(0.0,) * n, std=(1.0,) * n, coef=(0.0,) * n,
        intercept=bias, threshold=0.5,
    )


def _verse(*lines: str) -> ir.VerseBlock:
    return ir.VerseBlock(stanzas=[[[ir.Text(t)] for t in lines]])


def _lineated(*lines: str) -> ir.LineatedBlock:
    return ir.LineatedBlock(stanzas=[[[ir.Text(t)] for t in lines]])


def _doc(*blocks: ir.Block) -> ir.Document:
    return ir.Document(blocks=list(blocks))


DEMOTE = _model(bias=-3.0)   # p ~ 0.05: confident not-verse
PROMOTE = _model(bias=3.0)   # p ~ 0.95: confident verse
ABSTAIN = _model(bias=0.0)   # p = 0.5: inside the band


def test_noop_without_artifact() -> None:
    doc = _doc(_verse("Свет мой тихий,", "в сердце горит."))
    before = doc.blocks[0]
    apply_verse_register(doc, lang="ru", model=None)
    assert doc.blocks[0] is before  # no artifact shipped in tests -> no-op


def test_confident_demotion_flips_verse_to_lineated_keeping_evidence() -> None:
    ev = ir.LineationEvidence(inferred_source_rows=True, stanza_break=True)
    doc = _doc(ir.VerseBlock(
        stanzas=[[[ir.Text("Сергей был обычным.")], [ir.Text("Ну, почти.")]]],
        evidence=ev,
        source_span=ir.SourceSpan(4, 5),
    ))
    apply_verse_register(doc, lang="ru", model=DEMOTE)
    block = doc.blocks[0]
    assert isinstance(block, ir.LineatedBlock)
    assert block.evidence == ev
    assert block.source_span == ir.SourceSpan(4, 5)
    assert any(d.code == "intent.verse-register" for d in doc.diagnostics)


def test_confident_promotion_flips_lineated_to_verse() -> None:
    doc = _doc(_lineated("Свет мой тихий,", "в сердце горит."))
    apply_verse_register(doc, lang="ru", model=PROMOTE)
    assert isinstance(doc.blocks[0], ir.VerseBlock)


def test_abstention_keeps_the_ladder_verdict() -> None:
    doc = _doc(_verse("Свет мой тихий,", "в сердце горит."),
               _lineated("Тихая строка,", "ещё одна строка."))
    apply_verse_register(doc, lang="ru", model=ABSTAIN)
    assert isinstance(doc.blocks[0], ir.VerseBlock)
    assert isinstance(doc.blocks[1], ir.LineatedBlock)


def test_named_section_is_never_demoted() -> None:
    doc = _doc(
        ir.Heading(level=2, inlines=[ir.Text("Молитва")]),
        ir.Paragraph(inlines=[], empty=True),  # blank rows are transparent
        _verse("Свет мой тихий,", "в сердце горит."),
    )
    apply_verse_register(doc, lang="ru", model=DEMOTE)
    assert isinstance(doc.blocks[2], ir.VerseBlock)


def test_scaffold_is_never_promoted() -> None:
    doc = _doc(_lineated("— возражения религиозных систем,", "— возражения обычных людей."))
    apply_verse_register(doc, lang="ru", model=PROMOTE)
    assert isinstance(doc.blocks[0], ir.LineatedBlock)


def test_en_sources_are_untouched() -> None:
    doc = _doc(_verse("My quiet light,", "burns in the heart."))
    apply_verse_register(doc, lang="en", model=DEMOTE)
    assert isinstance(doc.blocks[0], ir.VerseBlock)
