"""The verse-register decision pass (`passes.register.assign_register`)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from pancratius import ir
from pancratius.intent_inference.artifacts import (
    RegisterPolicyLoadOutcome,
    load_register_policy_for,
)
from pancratius.intent_inference.decisions import (
    ArtifactId,
    ArtifactSchemaId,
    DecisionOutcome,
    FeatureSetId,
    IntentTask,
    LabelSpaceId,
    ObservationSchemaId,
    PredictorRef,
    RegisterDecision,
    RegisterDecisionReason,
    ScorerFamily,
)
from pancratius.intent_inference.observations import RegisterCandidate, RegisterDocumentContext
from pancratius.intent_inference.policies import ModelBackedRegisterPolicy, RegisterRolloutMode
from pancratius.intent_inference.scorers.standardized_linear import (
    FEATURE_NAMES,
    StandardizedLinearRegisterScorer,
)
from pancratius.ir.inlines import inline_plain
from pancratius.passes.pipeline import Context, ScripturePins
from pancratius.passes.register import (
    SpanLabel,
    assign_register,
    scaffold_line_labeler,
    segment_lineated,
)

_TEST_PREDICTOR = PredictorRef(
    task=IntentTask.DISPLAY_REGISTER,
    artifact_id=ArtifactId("test-register-model"),
    artifact_schema=ArtifactSchemaId.REGISTER_ARTIFACT_V1,
    observation_schema=ObservationSchemaId.REGISTER_OBSERVATION_V1,
    label_space=LabelSpaceId.DISPLAY_REGISTER_LABELS_V1,
    scorer_family=ScorerFamily.STANDARDIZED_LINEAR_V1,
    feature_set=FeatureSetId.VERSE_REGISTER_FEATURES_V1,
)


def _model(*, bias: float) -> StandardizedLinearRegisterScorer:
    """A constant model: p = sigmoid(bias) for every block."""
    n = len(FEATURE_NAMES)
    return StandardizedLinearRegisterScorer(
        version=0, langs=("ru",), features=FEATURE_NAMES,
        mean=(0.0,) * n, std=(1.0,) * n, coef=(0.0,) * n,
        intercept=bias, threshold=0.6, predictor_ref=_TEST_PREDICTOR,
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


def _ctx(model: StandardizedLinearRegisterScorer | None) -> Context:
    return (
        Context(lang="ru", register_policy=ModelBackedRegisterPolicy(model))
        if model is not None
        else Context(lang="ru")
    )


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


def test_shipped_model_promotes_where_rules_do_not_and_reports_delta() -> None:
    policy_load = load_register_policy_for("ru")
    assert policy_load.outcome is RegisterPolicyLoadOutcome.MODEL_ASSISTED_ARTIFACT_LOADED
    block = _lineated("Тихая строка,", "ещё одна строка.")

    rules = assign_register(_doc(block), Context(lang="ru"))
    with_model_ctx = Context(lang="ru", register_policy=policy_load.policy)
    with_model = assign_register(_doc(block), with_model_ctx)

    assert not _is_verse(rules.blocks[0])
    assert _is_verse(with_model.blocks[0])
    assert [
        (d.severity, d.code, d.message)
        for d in with_model_ctx.diagnostics
    ] == [
        (
            "info",
            "register.model_rules_disagree",
            "model chose verse; rules chose ordinary",
        ),
        (
            "info",
            "register.model",
            "register model v1: 1 verse blocks (rules alone: 0)",
        )
    ]


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
# scaffold-robust verse verdict: the verdict and the segmentation share ONE
# line labeling, so a scaffold island embedded in a verse body no longer
# poisons the verse decision of the body it sits in.
# ---------------------------------------------------------------------------


def _dash_poison_model() -> StandardizedLinearRegisterScorer:
    """A model whose probability collapses below threshold as the dash-line rate
    rises: a verse body alone clears 0.6, the same body with a dash island sinks.
    Used to prove the verdict reads the verse-candidate view, not the monolith."""
    n = len(FEATURE_NAMES)
    coef = [0.0] * n
    coef[FEATURE_NAMES.index("dash_rate")] = -6.0
    return StandardizedLinearRegisterScorer(
        version=0, langs=("ru",), features=FEATURE_NAMES,
        mean=(0.0,) * n, std=(1.0,) * n, coef=tuple(coef),
        intercept=1.0, threshold=0.6, predictor_ref=_TEST_PREDICTOR,
    )


def _stanza(*lines: str) -> list[ir.Line]:
    return [ir.Line([ir.Text(t)]) for t in lines]


def _multi(*stanzas: list[ir.Line]) -> ir.LineatedBlock:
    return ir.LineatedBlock(stanzas=list(stanzas))


def test_dash_island_no_longer_poisons_the_verse_verdict() -> None:
    # A cadenced verse body with a whole-dash scaffold stanza between its two
    # verse stanzas. The monolith's dash_rate sinks the model below threshold;
    # the verse-candidate view (dash stanza dropped) clears it, so the body
    # promotes and segmentation splits the island back out as ORDINARY.
    doc = _doc(_multi(
        _stanza("Свет мой тихий,", "в сердце горит."),
        _stanza("— возражение одно,", "— возражение два,"),
        _stanza("и не гаснет.", "и не молчит."),
    ))
    out = assign_register(doc, _ctx(_dash_poison_model())).blocks
    kinds = [(type(b).__name__, getattr(b, "register", None)) for b in out]
    assert kinds == [
        ("LineatedBlock", ir.Register.VERSE),
        ("LineatedBlock", ir.Register.ORDINARY),
        ("LineatedBlock", ir.Register.VERSE),
    ]
    middle = out[1]
    assert isinstance(middle, ir.LineatedBlock)
    assert [inline_plain(line.inlines) for line in middle.stanzas[0]] == [
        "— возражение одно,", "— возражение два,",
    ]


def test_book54_numbered_self_exam_promotes_with_the_verse_body() -> None:
    # The book-54 «Проверь себя» shape: numbered self-exam rows that
    # `recover_numbered_rows` re-absorbed are the verse body's own cadence, not a
    # scaffold island — the guard tolerates the ordinal lead and the run promotes
    # whole (the numbered rows stay VERSE, not split out).
    doc = _doc(_multi(_stanza(
        "Граница — это акт любви,",
        "потому что сердце — святыня.",
        "Проверь себя:",
        "1. Ты говоришь «да» потому, что хочешь —",
        "или потому, что боишься последствий «нет»?",
        "2. Ты молчишь, потому что мудр —",
        "или потому что сломлен?",
    )))
    out = assign_register(doc, _ctx(PROMOTE)).blocks
    assert len(out) == 1
    block = out[0]
    assert isinstance(block, ir.LineatedBlock)
    assert block.register is ir.Register.VERSE
    # the numbered rows survive inside the verse block (not demoted/split)
    texts = [inline_plain(line.inlines) for stanza in block.stanzas for line in stanza]
    assert "1. Ты говоришь «да» потому, что хочешь —" in texts


def test_genuinely_ordinary_numbered_run_stays_ordinary() -> None:
    # A numbered run the model rejects on its verse-candidate view stays ordinary:
    # the guard tolerating the ordinal lead does not force a promotion — the
    # model/ladder remain the authority.
    doc = _doc(_multi(_stanza(
        "1. Распиши задачу на шаги.",
        "2. Оцени каждый шаг.",
        "3. Выполни по порядку.",
    )))
    out = assign_register(doc, _ctx(DEMOTE)).blocks
    assert len(out) == 1
    block = out[0]
    assert isinstance(block, ir.LineatedBlock)
    assert block.register is ir.Register.ORDINARY


def test_pure_scaffold_run_stays_ordinary_even_with_a_confident_model() -> None:
    # A run that is ENTIRELY scaffold has an empty verse-candidate view (every
    # line dropped) → fewer than two candidate lines → never promotes, however
    # confident the model.
    doc = _doc(_multi(_stanza("143 = 11 × 13", "153 = 9 × 17", "289 = 17 × 17")))
    out = assign_register(doc, _ctx(PROMOTE)).blocks
    block = out[0]
    assert isinstance(block, ir.LineatedBlock)
    assert block.register is ir.Register.ORDINARY


def test_long_prose_numbered_run_stays_ordinary() -> None:
    # Book-32 gold-prose shape: a numbered run whose lines are long prose
    # sentences. Even with the ordinal lead tolerated, the geometry ladder reads
    # the lengths and refuses — numbered exposition is not verse.
    long = "потому что ум различает только поведение, а не присутствие Света"
    doc = _doc(_lineated(
        f"1. Ты не мог отличить, {long}, {long}.",
        f"2. И ты стал делателем, {long}, {long}.",
        evidence=ir.LineationEvidence(stanza_break=True),
    ))
    out = assign_register(doc, _ctx(None)).blocks  # ladder only
    block = out[0]
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
        ir.LineatedBlock(
            stanzas=[[
                _spanned_line("Свет мой тихий,", 10),
                _spanned_line("в сердце горит.", 11),
            ]],
            register=ir.Register.VERSE,
            source_span=ir.SourceSpan(10, 11),
        ),
        ir.Paragraph(inlines=[], facts=ir.SourceFacts(empty=True)),
        ir.LineatedBlock(
            stanzas=[[
                _spanned_line("Ты читал.", 13),
                _spanned_line("Я писал.", 14),
            ]],
            source_span=ir.SourceSpan(13, 14),
        ),
        ir.Heading(level=2, inlines=[ir.Text("Глава")]),
    )
    doc = assign_register(doc, _ctx(None))
    first = doc.blocks[0]
    assert isinstance(first, ir.LineatedBlock)
    assert first.register is ir.Register.VERSE
    assert len(first.stanzas) == 2  # the compact coda folded into the verse block
    assert first.source_span == ir.SourceSpan(10, 14)
    assert isinstance(doc.blocks[1], ir.Heading)


def test_model_promoted_block_keeps_coda_machinery() -> None:
    doc = _doc(
        ir.LineatedBlock(
            stanzas=[[
                _spanned_line("Тихая строка,", 10),
                _spanned_line("ещё одна строка.", 11),
            ]],
            source_span=ir.SourceSpan(10, 11),
        ),
        ir.Paragraph(inlines=[], facts=ir.SourceFacts(empty=True)),
        ir.LineatedBlock(
            stanzas=[[
                _spanned_line("Ты читал.", 13),
                _spanned_line("Я писал.", 14),
            ]],
            source_span=ir.SourceSpan(13, 14),
        ),
        ir.Heading(level=2, inlines=[ir.Text("Глава")]),
    )
    ctx = _ctx(PROMOTE)

    doc = assign_register(doc, ctx)

    first = doc.blocks[0]
    assert isinstance(first, ir.LineatedBlock)
    assert first.register is ir.Register.VERSE
    assert len(first.stanzas) == 2
    assert first.source_span == ir.SourceSpan(10, 14)
    assert isinstance(doc.blocks[1], ir.Heading)
    assert [(d.severity, d.code) for d in ctx.diagnostics] == [
        ("info", "register.model_rules_disagree"),
        ("info", "register.model_rules_disagree"),
        ("info", "register.model"),
    ]


def test_unmaterializable_policy_decision_fails_loud() -> None:
    class RefusingPolicy:
        name = "refusing-register"
        rollout = RegisterRolloutMode.RULES_ONLY
        reports_model_delta = False
        model_version = None

        def decide_document(
            self,
            candidates: tuple[RegisterCandidate, ...],
            context: RegisterDocumentContext,
        ) -> tuple[RegisterDecision, ...]:
            _ = context
            return tuple(
                RegisterDecision(
                    subject=candidate.candidate_id,
                    outcome=DecisionOutcome.REFUSE_CONTRACT,
                    label=None,
                    reason=RegisterDecisionReason.ARTIFACT_CONTRACT,
                )
                for candidate in candidates
            )

    with pytest.raises(ValueError, match="refused contract"):
        assign_register(_doc(_lineated("Тихая строка,", "ещё одна строка.")), Context(
            lang="ru",
            register_policy=RefusingPolicy(),
        ))


# ---------------------------------------------------------------------------
# SCRIPTURE line-class: in-verse canonical quote lines (segmentation × scripture)
# ---------------------------------------------------------------------------


def _pin_labeler(
    block: ir.LineatedBlock, *ords: int
) -> Callable[[int, int, ir.Line], SpanLabel]:
    return scaffold_line_labeler(block, frozenset(ords))


def test_segment_verse_scripture_verse_sandwich() -> None:
    # A pinned canonical quote line between two verse lines -> three fragments,
    # the middle a scripture QuoteBlock, the verse lines unchanged.
    block = _verse_block([
        _spanned_line("Иисус говорит с женщиной,", 594),
        _spanned_line("а прямо, просто, близко:", 596),
        _spanned_line("«Отец ищет Себе поклонников…»", 597),
        _spanned_line("Никто так не говорил.", 599),
        _spanned_line("Пророки взывали к Богу.", 600),
    ])
    fragments = segment_lineated(block, _pin_labeler(block, 597))
    assert len(fragments) == 3
    head, middle, tail = fragments
    assert isinstance(head, ir.LineatedBlock) and head.register is ir.Register.VERSE
    assert isinstance(middle, ir.QuoteBlock) and middle.register is ir.Register.SCRIPTURE
    assert isinstance(tail, ir.LineatedBlock) and tail.register is ir.Register.VERSE
    member = middle.blocks[0]
    assert isinstance(member, ir.LineatedBlock)
    assert member.stanzas[0][0].inlines == [ir.Text("«Отец ищет Себе поклонников…»")]


def test_segment_scripture_fragment_tiles_span() -> None:
    # The scripture fragment carries the quote line's own ordinal; the head and
    # tail fragments tile the parent span without losing coverage.
    block = _verse_block([
        _spanned_line("а прямо, просто, близко:", 596),
        _spanned_line("«Отец ищет Себе поклонников…»", 597),
        _spanned_line("«будут поклоняться Отцу в духе и истине…»", 598),
        _spanned_line("Никто так не говорил.", 599),
    ])
    head, mid, tail = segment_lineated(block, _pin_labeler(block, 597, 598))
    assert head.source_span == ir.SourceSpan(596, 596)
    assert isinstance(mid, ir.QuoteBlock) and mid.source_span == ir.SourceSpan(597, 598)
    assert tail.source_span == ir.SourceSpan(599, 599)


def test_segment_single_scripture_line_island_splits() -> None:
    # Unlike a lone scaffold line (which rejoins the verse run), a lone scripture
    # quote line always splits out — canon recall is never dissolved as texture.
    block = _verse_block([
        _spanned_line("И когда Христос говорил:", 2460),
        _spanned_line("«Плод в жизнь вечную уже собран»,", 2461),
        _spanned_line("Он говорил это о них —", 2462),
    ])
    head, mid, tail = segment_lineated(block, _pin_labeler(block, 2461))
    assert isinstance(head, ir.LineatedBlock) and head.register is ir.Register.VERSE
    assert isinstance(mid, ir.QuoteBlock) and mid.register is ir.Register.SCRIPTURE
    assert isinstance(tail, ir.LineatedBlock) and tail.register is ir.Register.VERSE


def test_segment_own_voice_line_stays_verse() -> None:
    # No pin and no citation channel -> a bare «…» line stays a verse line; the
    # whole run is uniform verse and is returned unchanged.
    block = _verse_block([
        _spanned_line("И Христос говорит:", 854),
        _spanned_line("«Она станет источником в тебе…»", 855),
        _spanned_line("и это о духе.", 856),
    ])
    assert segment_lineated(block, _pin_labeler(block)) == [block]


def test_segment_cited_quote_line_is_scripture_without_a_pin() -> None:
    # The line-grain citation channel: a whole quote line carrying a citation
    # token splits out as scripture even with no sidecar pin.
    block = _verse_block([
        _spanned_line("Он повторил слова:", 10),
        _spanned_line("«Я есмь путь и истина» (Ин. 14:6).", 11),
        _spanned_line("и шёл дальше.", 12),
    ])
    _head, mid, _tail = segment_lineated(block, scaffold_line_labeler(block))
    assert isinstance(mid, ir.QuoteBlock) and mid.register is ir.Register.SCRIPTURE


def test_segment_uniform_scripture_run_becomes_quote_block() -> None:
    # A run that is wholly scripture lines still rebuilds as a scripture quote
    # block, not a SCRIPTURE-registered LineatedBlock (which would lower flat).
    block = _verse_block([
        _spanned_line("«Отец ищет Себе поклонников…»", 597),
        _spanned_line("«будут поклоняться Отцу в духе и истине…»", 598),
    ])
    fragments = segment_lineated(block, _pin_labeler(block, 597, 598))
    assert len(fragments) == 1
    assert isinstance(fragments[0], ir.QuoteBlock)


def test_segment_equation_line_beats_scripture_pin() -> None:
    # Scaffold wins over scripture: an equation line is never a canonical quote
    # even if its ordinal is (spuriously) pinned.
    block = _verse_block([
        _spanned_line("Свет мой тихий,", 10),
        _spanned_line("143 = 11 × 13", 11),
        _spanned_line("153 = 9 × 17", 12),
        _spanned_line("в сердце горит.", 13),
    ])
    _head, mid, _tail = segment_lineated(block, _pin_labeler(block, 11))
    assert isinstance(mid, ir.LineatedBlock) and mid.register is ir.Register.ORDINARY


def test_assign_register_splits_in_verse_scripture_pin() -> None:
    # End to end through the pass with a sidecar pin: a promoted verse run with
    # one pinned canonical line comes out verse / scripture-quote / verse, and
    # wrap_scripture does NOT fail loud (the in-verse pin is honored upstream).
    from pancratius.passes.register import wrap_scripture as _wrap
    block = ir.LineatedBlock(
        stanzas=[[
            _spanned_line("Иисус говорит с женщиной,", 594),
            _spanned_line("а прямо, просто, близко:", 596),
            _spanned_line("«Отец ищет Себе поклонников…»", 597),
            _spanned_line("Никто так не говорил.", 599),
            _spanned_line("Пророки взывали к Богу.", 600),
        ]],
        evidence=ir.LineationEvidence(stanza_break=True),
        source_span=ir.SourceSpan(594, 600),
    )
    ctx = Context(lang="ru", scripture=ScripturePins({597: "Ин 4:23"}))
    doc = assign_register(_doc(block), ctx)
    kinds = [(type(b).__name__, getattr(b, "register", None)) for b in doc.blocks]
    assert kinds == [
        ("LineatedBlock", ir.Register.VERSE),
        ("QuoteBlock", ir.Register.SCRIPTURE),
        ("LineatedBlock", ir.Register.VERSE),
    ]
    # The prose scripture pass then runs without raising on the consumed pin.
    assert _wrap(doc.blocks, pinned={597: "Ин 4:23"}) == doc.blocks


# ---------------------------------------------------------------------------
# wrap_scripture: unfenced canonical quotations (Q2c)
# ---------------------------------------------------------------------------

from pancratius.passes.register import is_scripture_quote, wrap_scripture  # noqa: E402


def _para(text: str, *, ord_: int | None = None) -> ir.Paragraph:
    span = ir.SourceSpan(ord_, ord_) if ord_ is not None else None
    return ir.Paragraph(inlines=[ir.Text(text)], source_span=span)


def test_logion_speech_anchor_is_scripture() -> None:
    assert is_scripture_quote(
        "«Иисус сказал: Блажен лев, которого человек съест, и лев станет человеком»."
    )
    assert is_scripture_quote("«И сказал Бог Моисею: Я ЕСМЬ Сущий».")


def test_cited_whole_quote_is_scripture() -> None:
    assert is_scripture_quote("«Возведите очи ваши и посмотрите на нивы» (Ин. 4:35).")
    assert is_scripture_quote("«Воистину, подобие Исы перед Аллахом — как подобие Адама» (Коран 3:59).")


def test_ref_led_quote_is_scripture() -> None:
    assert is_scripture_quote("— Откр. 19:11: «И увидел я небо отверстое, и вот конь белый…»")


def test_bare_whole_quote_is_not_scripture() -> None:
    # REFUTED channel: rhetorical/inner speech dominates bare whole-paragraph quotes.
    assert not is_scripture_quote("«Зачем мне всё это?» — подумал он.")
    assert not is_scripture_quote("«Я просто хочу тишины».")


def test_exegesis_riff_on_quoted_word_is_not_scripture() -> None:
    # Opens and closes with quotes but is the book's own commentary in between.
    assert not is_scripture_quote("«Блажен» — значит не просто счастлив, а «освещён Светом»")


def test_bold_numbered_subheading_is_not_scripture() -> None:
    # REFUTED channel: numbered section sub-headings.
    assert not is_scripture_quote("3. Бог — не Отец, и у Него нет сына")


def test_wrap_scripture_groups_contiguous_run_with_interior_blank() -> None:
    blocks: list[ir.Block] = [
        _para("Обычный абзац вокруг.", ord_=1),
        _para("«Иисус сказал: Кто ищет — найдёт».", ord_=2),
        ir.Paragraph(inlines=[], facts=ir.SourceFacts(empty=True)),
        _para("«Иисус сказал: Царство Отца подобно горчичному зерну».", ord_=4),
        _para("Комментарий Творца к логию.", ord_=5),
    ]
    out = wrap_scripture(blocks)
    assert [type(b).__name__ for b in out] == ["Paragraph", "QuoteBlock", "Paragraph"]
    quote = out[1]
    assert isinstance(quote, ir.QuoteBlock)
    assert quote.register is ir.Register.SCRIPTURE
    assert quote.source_span == ir.SourceSpan(2, 4)
    assert len(quote.blocks) == 3  # two logia + the transparent interior blank


def test_wrap_scripture_trailing_blank_stays_outside() -> None:
    blocks: list[ir.Block] = [
        _para("«Иисус сказал: Я свет миру» (Ин. 8:12).", ord_=1),
        ir.Paragraph(inlines=[], facts=ir.SourceFacts(empty=True)),
        _para("Дальше — обычная речь книги.", ord_=3),
    ]
    out = wrap_scripture(blocks)
    assert [type(b).__name__ for b in out] == ["QuoteBlock", "Paragraph", "Paragraph"]


def test_cite_adjacent_whole_quote_wraps_with_its_citation_line() -> None:
    blocks: list[ir.Block] = [
        _para("Обычный абзац вокруг.", ord_=1),
        _para("«Они не убили его и не распяли, а им только показалось так».", ord_=2),
        _para("Сура 4:157–158 (ан-Ниса)", ord_=3),
        _para("Дальше — комментарий книги.", ord_=4),
    ]
    out = wrap_scripture(blocks)
    assert [type(b).__name__ for b in out] == ["Paragraph", "QuoteBlock", "Paragraph"]
    quote = out[1]
    assert isinstance(quote, ir.QuoteBlock)
    assert quote.source_span == ir.SourceSpan(2, 3)


def test_bare_whole_quote_without_citation_stays_plain() -> None:
    blocks: list[ir.Block] = [
        _para("«Я ЕСМЬ ТОТ, КТО Я ЕСМЬ».", ord_=1),
        _para("Дальше — комментарий книги.", ord_=2),
    ]
    out = wrap_scripture(blocks)
    assert [type(b).__name__ for b in out] == ["Paragraph", "Paragraph"]


def test_first_person_dictation_quote_is_not_scripture() -> None:
    assert not is_scripture_quote(
        "«Отец постоянно говорит мне: «ты не пророк». И я не могу не принимать это слово»."
    )


def test_citation_line_does_not_pull_a_quote_across_a_heading() -> None:
    blocks: list[ir.Block] = [
        _para("«Они не убили его и не распяли, а им только показалось так».", ord_=1),
        ir.Heading(level=2, inlines=[ir.Text("Глава")]),
        _para("Сура 4:157–158 (ан-Ниса)", ord_=3),
    ]
    out = wrap_scripture(blocks)
    assert [type(b).__name__ for b in out] == ["Paragraph", "Heading", "Paragraph"]
