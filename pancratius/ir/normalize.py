# import-pure: no filesystem mutation
"""Verse-register promotion (Q2): promote already-lineated blocks to `VerseBlock`s.

Also a compat facade: every pass name this module previously exposed is
re-exported from its `pancratius.passes` home.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pancratius import ir
from pancratius.ir.inlines import inline_lines, inline_plain
from pancratius.passes.endmatter import (
    _resolve_target,  # noqa: F401  re-export
    _SlugLookup,
    lift_bibliography,
    strip_bare_bibliography_heading,
    strip_endmatter_sections,
)
from pancratius.passes.lineation import (
    _CODA_PSEUDO_HEADING_RE,
    VERSE_SHORT_LINE_MAX,
    _is_compact_coda,
    _skip_empty_paragraphs,
    is_lineated_line,
)
from pancratius.passes.lineation import fold_lineation as lineated_blocks
from pancratius.passes.register import fold_quote_registers as display_register_blocks
from pancratius.passes.scrub import (
    AI_ALT_FRAGMENTS,
    RIGHTS_PATTERNS,
    demote_headings,
    drop_empty_headings,
    drop_toc,
    scrub_ai_alt,
    scrub_rights,
    strip_formatting_artifacts,
    thematic_breaks,
)
from pancratius.passes.structure import (
    _DIALOGUE_PREFIXES,  # noqa: F401  re-export
    dialogue_labels,
)
from pancratius.passes.structure import fold_right_aligned as structural_blocks

__all__ = (
    "AI_ALT_FRAGMENTS",
    "RIGHTS_PATTERNS",
    "VERSE_SHORT_LINE_MAX",
    "demote_headings",
    "dialogue_labels",
    "display_register_blocks",
    "drop_empty_headings",
    "drop_toc",
    "inline_lines",
    "inline_plain",
    "is_dash_scaffold",
    "is_equation_scaffold",
    "is_lineated_line",
    "is_verse_section_title",
    "lift_bibliography",
    "lineated_blocks",
    "normalize",
    "promote_verse_register",
    "scrub_ai_alt",
    "scrub_rights",
    "strip_bare_bibliography_heading",
    "strip_endmatter_sections",
    "strip_formatting_artifacts",
    "structural_blocks",
    "thematic_breaks",
    "verse_blocks",
)

# ---------------------------------------------------------------------------
# section-title vocab
# ---------------------------------------------------------------------------

_VERSE_SECTION_TITLE_RE = re.compile(
    r"^(?:posвящение|посвящение|dedication|"
    r"предисловие\s+от\s+творца|preface\s+(?:from|by)\s+the\s+creator|"
    r"слово\s+творца|the\s+word\s+of\s+the\s+creator|creator'?s\s+word|"
    r"голос\s+творца|voice\s+of\s+the\s+creator|"
    r"ответ\s+творца|creator'?s\s+answer|"
    r"пояснение\s+творца|annotation\s+from\s+the\s+creator|"
    r"благословляющее\s+слово\s+творца|"
    r"молитва|prayer|псалом|psalm)\b",
    re.IGNORECASE,
)


def is_verse_section_title(t: str) -> bool:
    return bool(_VERSE_SECTION_TITLE_RE.match(re.sub(r"\s+", " ", t.strip().lower())))


# ---------------------------------------------------------------------------
# verse-register promotion (Q2 ladder)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _PrecedingContext:
    """Q2 register context preceding an already-lineated block.

    The heading text and thematic separators may promote a lineated substrate to
    verse register. They are intentionally absent from `LineationEvidence`, which
    belongs to Q1 line-boundary provenance.
    """

    named: bool = False
    heading: bool = False
    separator: bool = False


_NEUTRAL_CONTEXT = _PrecedingContext()


def verse_blocks(blocks: list[ir.Block]) -> list[ir.Block]:
    """Q1 lineation, then Q2 verse-register promotion.

    This compatibility entry point preserves the old public pass name while making
    the two questions explicit: it first decides line breaks, then only wraps
    already-lineated blocks in the `verse` register.
    """
    return promote_verse_register(lineated_blocks(blocks))


def promote_verse_register(blocks: list[ir.Block]) -> list[ir.Block]:
    """Q2: promote already-lineated blocks to `VerseBlock`s.

    This pass may use register context such as headings, named verse titles, and
    separators. It never folds paragraphs and therefore cannot create hard breaks.
    """
    out: list[ir.Block] = []
    i = 0
    ctx = _NEUTRAL_CONTEXT

    while i < len(blocks):
        b = blocks[i]
        if isinstance(b, ir.Heading):
            title = inline_plain(b.inlines)
            ctx = _PrecedingContext(
                named=is_verse_section_title(title),
                heading=True,
            )
            out.append(b)
            i += 1
            continue
        if isinstance(b, ir.ThematicBreak):
            ctx = _PrecedingContext(separator=True)
            out.append(b)
            i += 1
            continue
        if isinstance(b, ir.LineatedBlock):
            if (kind := _lineated_block_kind(b, ctx)) is not None:
                verse = ir.VerseBlock(
                    stanzas=b.stanzas, role=kind, evidence=b.evidence,
                    source_span=b.source_span,
                )
                if (segment := _lineated_coda_segment(blocks, i + 1, verse)) is not None:
                    verse, next_i = segment
                    out.append(verse)
                    ctx = _NEUTRAL_CONTEXT
                    i = next_i
                    continue
                out.append(verse)
            else:
                out.append(b)
            ctx = _NEUTRAL_CONTEXT
            i += 1
            continue
        if isinstance(b, ir.VerseBlock):
            if (segment := _existing_verse_coda_segment(blocks, i)) is not None:
                verse, next_i = segment
                out.append(verse)
                ctx = _NEUTRAL_CONTEXT
                i = next_i
                continue
            ctx = _NEUTRAL_CONTEXT
            out.append(b)
            i += 1
            continue
        ctx = _NEUTRAL_CONTEXT
        out.append(b)
        i += 1
    return out


def _lineated_coda_candidate(
    blocks: list[ir.Block],
    i: int,
) -> tuple[ir.LineatedBlock, int] | None:
    """A local coda segment after a verse run.

    Shape: one or more empty paragraphs, an exact two-line lineated
    candidate, optional empty paragraphs, then a heading/thematic boundary. The
    candidate must be compact; this keeps prose previews before the next heading in
    prose without naming their words.
    """
    i, saw_gap = _skip_empty_paragraphs(blocks, i)
    if not saw_gap:
        return None

    first = blocks[i] if i < len(blocks) else None
    if not isinstance(first, ir.LineatedBlock):
        return None
    i += 1

    coda_lines = _lineated_block_lines(first)
    if not _is_compact_coda(coda_lines):
        return None
    if any(_CODA_PSEUDO_HEADING_RE.match(line) for line in coda_lines):
        return None

    boundary_i, _saw_trailing_gap = _skip_empty_paragraphs(blocks, i)
    boundary = blocks[boundary_i] if boundary_i < len(blocks) else None
    if not isinstance(boundary, (ir.Heading, ir.ThematicBreak)):
        return None

    return first, boundary_i


def _append_coda_copy(prev: ir.VerseBlock, coda: ir.LineatedBlock) -> ir.VerseBlock:
    return ir.VerseBlock(
        stanzas=[*prev.stanzas, *coda.stanzas],
        role=prev.role,
        evidence=prev.evidence,
        source_span=ir.merge_source_spans((prev.source_span, coda.source_span)),
    )


def _lineated_coda_segment(
    blocks: list[ir.Block],
    i: int,
    prev: ir.VerseBlock,
) -> tuple[ir.VerseBlock, int] | None:
    candidate = _lineated_coda_candidate(blocks, i)
    if candidate is None:
        return None
    coda, next_i = candidate
    return _append_coda_copy(prev, coda), next_i


def _existing_verse_coda_segment(
    blocks: list[ir.Block],
    i: int,
) -> tuple[ir.VerseBlock, int] | None:
    prev = blocks[i]
    assert isinstance(prev, ir.VerseBlock)
    return _lineated_coda_segment(blocks, i + 1, prev)


def _lineated_block_lines(block: ir.LineatedBlock) -> list[str]:
    return [
        inline_plain(line)
        for stanza in block.stanzas
        for line in stanza
        if inline_plain(line)
    ]


def _lineated_block_kind(
    block: ir.LineatedBlock,
    ctx: _PrecedingContext,
) -> ir.VerseRole | None:
    """Promote an already-structural lineated block to verse register."""
    lines = _lineated_block_lines(block)
    return _kind_for_lines(lines, block.evidence, ctx)


_DASH_LINE_RE = re.compile(r"^[—–-]\s")
_MATH_CHARS_RE = re.compile(r"[0-9=+×*²³√:.,()\s—–-]")


def _is_equation_line(line: str) -> bool:
    """A numerology/equation line (`153 = 9 × 17`): contains `=`/`×` and is
    mostly digits and operators. Math is never the verse register."""
    if "=" not in line and "×" not in line:
        return False
    return len(_MATH_CHARS_RE.findall(line)) / len(line) >= 0.6


def is_equation_scaffold(lines: list[str]) -> bool:
    return all(_is_equation_line(line) for line in lines)


def is_dash_scaffold(lines: list[str]) -> bool:
    """A pure dash-led enumeration («— возражения…», optionally after a colon
    opener): list scaffolding that keeps its line structure but is never the
    elevated verse register. Deliberately strict — a PARTIALLY dash-led run is
    kept, because anaphoric litanies inside oracle passages mix dash lines
    with framing verse lines."""
    body = lines[1:] if lines and lines[0].rstrip().endswith(":") else lines
    return len(body) >= 2 and all(_DASH_LINE_RE.match(line) for line in body)


def _kind_for_lines(
    lines: list[str],
    evidence: ir.LineationEvidence,
    ctx: _PrecedingContext,
) -> ir.VerseRole | None:
    if len(lines) < 2 or not all(is_lineated_line(line) for line in lines):
        return None
    if is_dash_scaffold(lines) or is_equation_scaffold(lines):
        return None
    def _passes(avg_max: float, line_max: int | None = None) -> bool:
        """The run's mean line length is within `avg_max` and (when given) every line
        is within `line_max`. The `(avg_max, line_max)` pair is all that varies across
        the ladder below."""
        return avg <= avg_max and (line_max is None or max(lengths) <= line_max)

    lengths = [len(line) for line in lines]
    if evidence.hard_break and max(lengths) > VERSE_SHORT_LINE_MAX:
        return None
    avg = sum(lengths) / len(lengths)
    if ctx.named:
        return "verse" if _passes(150) else None
    if ctx.separator and len(lines) <= 32:
        return "verse" if _passes(110, 160) else None
    if ctx.heading and len(lines) <= 32:
        return "verse" if _passes(95, 150) else None
    if evidence.compact_callout:
        return None
    if evidence.hard_break:
        return "verse"
    if evidence.stanza_break and len(lines) >= 3 and _passes(120):
        return "verse"
    if evidence.inferred_source_rows and len(lines) >= 3 and _passes(95, 120):
        return "verse"
    return None


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def normalize(
    doc: ir.Document,
    *,
    demote_levels: int = 1,
    slug_lookup: _SlugLookup | None = None,
) -> ir.Document:
    """Run the full book pass pipeline over `doc` (a shim over `passes.pipeline.run`).

    Callers that need a partial run observe at a named seam via
    `passes.pipeline.run(..., until=...)` instead.
    """
    # Function-level import: `passes.pipeline` wraps this module's pass functions.
    from pancratius.passes.pipeline import Context, run

    # No pass behind this shim reads `lang`; composition points that know the
    # language build their own Context and call `run` directly.
    return run(doc, Context(lang="", demote_levels=demote_levels, slug_lookup=slug_lookup))
