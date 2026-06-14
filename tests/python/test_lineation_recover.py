"""Q1 contextual repair: `recover_numbered_rows` re-absorbs numbered prose rows
that `is_lineated_line` rejected back into the fused lineated run they belong to.

The pass runs over the POST-FOLD block list (a mix of `LineatedBlock` folds and
the bare `Paragraph`s the fold rejected), so these tests construct that shape
directly. The four RCA counterexample classes are pinned by name:

  * A (book-54 shape) — RECOVER a clause-incomplete numbered checklist couplet
    sandwiched, ordinal-contiguous, between two same-group folds;
  * B (book-06/254) — KEEP a numbered math-section TITLE that opens a fold (no
    lineated fold precedes it, so the sandwich never forms);
  * C/clause-complete — KEEP a numbered list ITEM that terminates a sentence;
  * D (book-63) — KEEP a numbered section HEADING set off by a blank `<w:p>`
    (the ordinal-contiguity gate breaks on the blank);
  * numPr — a real OOXML ordered list adapts to `ListBlock`, never a bare
    `Paragraph`, so it is absent from the stream the pass walks.
"""

from __future__ import annotations

from pancratius import ir
from pancratius.ir.inlines import inline_plain
from pancratius.passes.lineation import recover_numbered_rows


def _para(text: str, ordinal: int, *, lineation_group: int | None = 10,
          indented: bool = False) -> ir.Paragraph:
    return ir.Paragraph(
        inlines=[ir.Text(text)],
        facts=ir.SourceFacts(lineation_group=lineation_group, indented=indented),
        source_span=ir.SourceSpan(ordinal, ordinal),
    )


def _empty(ordinal: int) -> ir.Paragraph:
    return ir.Paragraph(
        inlines=[], facts=ir.SourceFacts(empty=True),
        source_span=ir.SourceSpan(ordinal, ordinal),
    )


def _fold(start: int, end: int, *lines: str) -> ir.LineatedBlock:
    """A folded run covering ordinals `start..end` (one line per ordinal)."""
    assert len(lines) == end - start + 1
    stanza = [
        ir.Line([ir.Text(t)], span=ir.SourceSpan(start + k, start + k))
        for k, t in enumerate(lines)
    ]
    return ir.LineatedBlock(
        stanzas=[stanza],
        evidence=ir.LineationEvidence(inferred_source_rows=True),
        source_span=ir.SourceSpan(start, end),
    )


def _lines(block: ir.Block) -> list[str]:
    assert isinstance(block, ir.LineatedBlock)
    return [inline_plain(line.inlines) for stanza in block.stanzas for line in stanza]


def _ordinals_in_folds(blocks: list[ir.Block]) -> set[int]:
    out: set[int] = set()
    for b in blocks:
        if isinstance(b, ir.LineatedBlock):
            for stanza in b.stanzas:
                for line in stanza:
                    if line.span is not None:
                        out.update(range(line.span.start, line.span.end + 1))
    return out


# --- A: the book-54 pin recovers --------------------------------------------

def _book54_sandwich() -> list[ir.Block]:
    """`LineatedBlock(790..793)` · numbered couplet rows 794..798 · fold 799..801,
    one fused lg=10, ordinal-contiguous, clause-incomplete numbered rows."""
    return [
        _fold(
            790, 793,
            "Не потому, что ты строг,",
            "а потому, что сердце — святыня,",
            "и Я запрещаю относиться к нему как к расходному материалу.",
            "Проверь себя:",
        ),
        _para("1. Ты говоришь «да» потому, что хочешь —", 794),
        _para("или потому, что боишься последствий «нет»?", 795),
        _para("2. Ты терпишь, потому что любишь —", 796),
        _para("или потому, что боишься одиночества?", 797),
        _para("3. Ты молчишь, потому что мудр —", 798),
        _fold(
            799, 801,
            "или потому что сломлен?",
            "Граница — это не агрессия.",
            "Это честность.",
        ),
    ]


def test_book54_shape_recovers_into_one_block() -> None:
    out = recover_numbered_rows(_book54_sandwich())
    assert len(out) == 1, "the sandwich must fuse into ONE lineated block"
    block = out[0]
    assert isinstance(block, ir.LineatedBlock)
    # every recovered ordinal now sits inside the fused block's coverage
    assert _ordinals_in_folds(out) >= set(range(790, 802))
    assert block.source_span == ir.SourceSpan(790, 801)
    # the numbered rows and their collateral fragments are now display lines
    text = _lines(block)
    assert "1. Ты говоришь «да» потому, что хочешь —" in text
    assert "или потому, что боишься последствий «нет»?" in text
    assert "3. Ты молчишь, потому что мудр —" in text
    # repair asserts source-row lineation as its provenance
    assert block.evidence.inferred_source_rows


def test_book54_recovers_collateral_fragment_between_numbered_rows() -> None:
    # ord 796 (`2. Ты терпишь…`) is only recoverable as a RUN repair: its raw
    # neighbours 795/797 are themselves collateral fragments. The run-level pass
    # absorbs the whole bridge, so 796 lands inside the fused block.
    out = recover_numbered_rows(_book54_sandwich())
    assert 796 in _ordinals_in_folds(out)
    assert 795 in _ordinals_in_folds(out)
    assert 797 in _ordinals_in_folds(out)


# --- B: book-06/254 math-section title stays prose ---------------------------

def test_book06_numbered_title_before_scaffold_stays_prose() -> None:
    # `2. Условия фотонности:` (ord 254) is a math-section TITLE: the row BEFORE
    # it is prose/list scaffold (NOT a fold), so the lineated sandwich never
    # forms. The title is left as a bare prose paragraph — the false positive the
    # RCA flagged is excluded structurally.
    blocks: list[ir.Block] = [
        _para("В отличие от массы, фотон в ТЕО — это не удержанная форма,", 250,
              lineation_group=23),
        _para("ℜᵢⱼ мал, но совершенно синхронен", 251, lineation_group=None),
        _para("S близко к единице, но не превышает порог массы", 253,
              lineation_group=None),
        _para("2. Условия фотонности:", 254, lineation_group=24),
        _fold(255, 256, "S ≈ 1 m ≈ 0 τ — минимально возможное", "Это означает:"),
    ]
    out = recover_numbered_rows(blocks)
    # nothing fused: the title is still a bare prose paragraph, not in a fold
    assert 254 not in _ordinals_in_folds(out)
    title = next(
        b for b in out
        if isinstance(b, ir.Paragraph) and inline_plain(b.inlines).startswith("2.")
    )
    assert inline_plain(title.inlines) == "2. Условия фотонности:"


# --- C: a clause-complete numbered list item stays prose ---------------------

def test_clause_complete_numbered_item_stays_prose() -> None:
    # Even inside a perfect lineated sandwich, a numbered row that TERMINATES a
    # sentence is a list item / title, never a verse continuation. One such row
    # vetoes the whole bridge.
    blocks: list[ir.Block] = [
        _fold(100, 101, "the run opens here,", "and continues to the marker."),
        _para("1. Твоя способность входить в тишину.", 102),
        _para("2. Твоя способность вызывать тишину.", 103),
        _fold(104, 105, "a later verse line,", "closing the section."),
    ]
    out = recover_numbered_rows(blocks)
    assert 102 not in _ordinals_in_folds(out)
    assert 103 not in _ordinals_in_folds(out)
    # the folds and the bare numbered rows are left exactly as given (no fuse)
    assert sum(1 for b in out if isinstance(b, ir.LineatedBlock)) == 2


# --- D: book-63 blank-delimited numbered heading stays prose -----------------

def test_book63_blank_delimited_numbered_heading_stays_prose() -> None:
    # A numbered section HEADING is set off by a blank `<w:p>`: the empty
    # paragraph breaks the bridge run (the pass only bridges NON-empty
    # paragraphs), so no sandwich forms and the heading stays prose.
    blocks: list[ir.Block] = [
        _fold(360, 361, "the previous section ends,", "with its own verse."),
        _empty(362),
        _para("3) Zealots: center = political liberation", 363),
        _fold(364, 365, "the next section's verse,", "in its own fold."),
    ]
    out = recover_numbered_rows(blocks)
    assert 363 not in _ordinals_in_folds(out)
    # the blank survives and the heading is untouched
    assert any(isinstance(b, ir.Paragraph) and b.empty for b in out)


def test_blank_inside_bridge_breaks_recovery() -> None:
    # Same defence at the other edge: a blank between the numbered row and the
    # closing fold makes the run non-contiguous → no recovery.
    blocks: list[ir.Block] = [
        _fold(200, 201, "opening verse,", "leading to the marker —"),
        _para("1. a clause that does not terminate,", 202),
        _empty(203),
        _fold(204, 205, "a different section,", "in its own fold."),
    ]
    out = recover_numbered_rows(blocks)
    assert 202 not in _ordinals_in_folds(out)


# --- book-62 numbered section HEADING inside a sandwich stays prose ----------

def test_book62_numbered_heading_in_sandwich_stays_prose() -> None:
    # The dominant false positive: a numbered section TITLE sits, ordinal-
    # contiguous and same-group, between two verse folds — but it ENDS ON A WORD
    # (or a closing quote), naming its own section, not a clause the next row
    # completes. The continuation-mark rule refuses it.
    for heading in (
        "2. Почему даны «десять слов»",   # ends on a closing guillemet
        "1. Историческое измерение",       # ends on a letter
        "3) Zealots: center = political liberation",  # interior colon, ends on a word
    ):
        blocks: list[ir.Block] = [
            _fold(900, 901, "a verse passage,", "ending its section."),
            _para(heading, 902, lineation_group=92),
            _fold(903, 904, "a fresh verse passage,", "in its own fold."),
        ]
        out = recover_numbered_rows(blocks)
        assert 902 not in _ordinals_in_folds(out), heading


def test_continuation_marks_are_recoverable() -> None:
    # The positive side of the rule: every continuation terminator a genuine
    # split verse line ends on is admitted (comma, em/en dash, semicolon, colon).
    for k, mark in enumerate((",", " —", " –", ";", ":")):
        ordp = 1000 + k * 10
        blocks: list[ir.Block] = [
            _fold(ordp, ordp, "opening verse —"),
            _para(f"{k + 1}. a continuing clause{mark}", ordp + 1, lineation_group=70),
            _para("the lowercase completion.", ordp + 2, lineation_group=70),
            _fold(ordp + 3, ordp + 3, "closing verse."),
        ]
        out = recover_numbered_rows(blocks)
        assert ordp + 1 in _ordinals_in_folds(out), mark


# --- numPr: a real ordered list is a ListBlock, never a bridge Paragraph ------

def test_numpr_list_block_is_not_a_bridge_and_is_left_untouched() -> None:
    # OOXML `w:numPr` ordered lists adapt to `ListBlock`, not bare `Paragraph`s,
    # so a real list between two folds is never a recovery bridge — the pass only
    # bridges non-empty `Paragraph`s. The ListBlock passes straight through.
    list_block = ir.ListBlock(
        items=[[ir.Paragraph(inlines=[ir.Text("first item.")])]],
        ordered=True,
        source_span=ir.SourceSpan(302, 302),
    )
    blocks: list[ir.Block] = [
        _fold(300, 301, "opening verse,", "leading on —"),
        list_block,
        _fold(303, 304, "more verse,", "closing."),
    ]
    out = recover_numbered_rows(blocks)
    assert list_block in out
    assert sum(1 for b in out if isinstance(b, ir.LineatedBlock)) == 2


# --- discipline: structural guards -------------------------------------------

def test_requires_lineated_block_on_both_sides() -> None:
    # A numbered clause-incomplete row with a fold only AFTER it (prose before)
    # does not recover: the sandwich needs folds on BOTH sides.
    blocks: list[ir.Block] = [
        _para("ordinary prose leading in,", 400),
        _para("1. a clause continuing,", 401),
        _fold(402, 403, "verse after,", "but no verse before."),
    ]
    out = recover_numbered_rows(blocks)
    assert 401 not in _ordinals_in_folds(out)


def test_non_contiguous_ordinals_do_not_recover() -> None:
    # Folds and bridge are adjacent in the block list but the ORDINALS skip
    # (an unmapped/dropped row sat between in the source) → no recovery.
    blocks: list[ir.Block] = [
        _fold(500, 501, "opening verse,", "leading to —"),
        _para("1. a clause continuing,", 510),  # ordinal gap before this row
        _fold(511, 512, "verse after,", "closing."),
    ]
    out = recover_numbered_rows(blocks)
    assert 510 not in _ordinals_in_folds(out)


def test_indented_bridge_does_not_recover() -> None:
    # A departing indent reads as running prose, not the tight contiguous verse
    # this repair targets.
    blocks: list[ir.Block] = [
        _fold(600, 601, "opening verse,", "leading to —"),
        _para("1. a clause continuing,", 602, indented=True),
        _fold(603, 604, "verse after,", "closing."),
    ]
    out = recover_numbered_rows(blocks)
    assert 602 not in _ordinals_in_folds(out)


def test_bridge_without_a_numbered_row_does_not_recover() -> None:
    # Recovery is triggered by a rejected NUMBERED row; a bare short line between
    # two folds is the existing fold machinery's concern, not this pass.
    blocks: list[ir.Block] = [
        _fold(700, 701, "opening verse,", "leading to —"),
        _para("just a continuation, not numbered,", 702),
        _fold(703, 704, "verse after,", "closing."),
    ]
    out = recover_numbered_rows(blocks)
    assert 702 not in _ordinals_in_folds(out)


def test_mixed_group_bridge_does_not_recover() -> None:
    # The bridge must be ONE fused lineation group; two different groups are two
    # visual units, not one split run.
    blocks: list[ir.Block] = [
        _fold(800, 801, "opening verse,", "leading to —"),
        _para("1. a clause continuing,", 802, lineation_group=10),
        _para("still going,", 803, lineation_group=11),
        _fold(804, 805, "verse after,", "closing."),
    ]
    out = recover_numbered_rows(blocks)
    assert 802 not in _ordinals_in_folds(out)
