# import-pure: no filesystem mutation
"""Q1 lineation fold: decision units, inference gates, evidence, stanza building."""

from __future__ import annotations

import re
from typing import cast

from pancratius import ir
from pancratius.ir.inlines import inline_lines, inline_plain, walk_inlines
from pancratius.passes.structure import _DIALOGUE_PREFIXES

# A display line longer than this is prose-length, not verse: it separates genuine
# verse lines (well under 120 chars) from one-sentence-per-paragraph prose
# (clustering at 121-144). audit/book_verse.py encodes the same threshold.
VERSE_SHORT_LINE_MAX = 120


def _speaker_turn_re() -> re.Pattern[str]:
    """A speaker-led colon line: `<dialogue prefix>:` or `<Name> (qual):` then
    content (a dialogue/source TURN, never verse). Only a speaker name or a
    parenthetical-qualified speaker before the colon is rejected, not an arbitrary
    verb phrase, so a mid-sentence colon (`Ты спросил: кто они?`) stays verse.
    Built from `_DIALOGUE_PREFIXES` so adding a speaker keeps this in sync."""
    prefixes = sorted(_DIALOGUE_PREFIXES, key=lambda p: -len(p))
    inner = "|".join(re.escape(p) for p in prefixes)
    return re.compile(
        rf"^\**\s*(?:(?:{inner})|[A-ZА-ЯЁ][\wА-Яа-яЁё.\- ]{{0,40}}\s*\([^)]{{1,40}}\))"
        rf"\s*:(?:\s|\*|$)"
    )


_SPEAKER_TURN_RE = _speaker_turn_re()


def is_lineated_line(text: str) -> bool:
    """True for a single short source line that reads as a verse line rather
    than prose / a label / a speaker turn / a list item.

    Short colon opener lines such as `Он говорил:` and `Разве не сказал Я:` stay
    in the run. Only explicit speaker/source turns are rejected."""
    s = re.sub(r"\s+", " ", text).strip()
    if not s or len(s) > VERSE_SHORT_LINE_MAX:
        return False
    if s in {"—", "–", "-"}:
        return False
    if s.startswith(("!", "<", "|", ">", "[]")):
        return False
    if re.match(r"^[-*+]\s+", s) or re.match(r"^(?:\d+|[IVXLCDM]+)[.)]\s+", s):
        return False
    if _SPEAKER_TURN_RE.match(s):
        return False
    return "http://" not in s and "https://" not in s


def _is_wrapped_prose(p: ir.Paragraph) -> bool:
    """True when a paragraph's only in-run breaks are `SoftBreak`s (prose wrapping,
    a literal `\\r\\n` in one `<w:t>`) with no hard `LineBreak`: its lineation was
    never authored, so it is prose even when collapsed to one short line. A hard
    break — or no break at all (one Word paragraph per line) — stays verse-eligible."""
    has_soft = False
    has_hard = False
    for n in walk_inlines(p.inlines):
        has_soft = has_soft or isinstance(n, ir.SoftBreak)
        has_hard = has_hard or isinstance(n, ir.LineBreak)
    return has_soft and not has_hard


def _para_lineated(p: ir.Paragraph) -> bool:
    if not p.inlines:
        return False
    for n in walk_inlines(p.inlines):
        if isinstance(n, (ir.ImageInline, ir.Link, ir.Code)):
            return False
    if _is_wrapped_prose(p):
        return False  # wrapping, not authored lineation
    # Detection: a `SoftBreak` is prose wrapping (joined as a space); only a hard
    # `LineBreak` is a verse-line boundary. Recurse into containers.
    lines = [inline_plain(ln) for ln in inline_lines(p.inlines, soft_break=False)]
    lines = [line for line in lines if line]
    return bool(lines) and all(is_lineated_line(line) for line in lines)


def _para_has_hard_lineation(p: ir.Paragraph) -> bool:
    """True when the source paragraph carries an explicit hard `w:br` boundary.

    This is LINEATION evidence even when the lines are not verse-register lines:
    lowering must preserve the authored break instead of collapsing it as prose.
    """
    return any(isinstance(n, ir.LineBreak) for n in walk_inlines(p.inlines))


def _para_structurally_lineated(p: ir.Paragraph) -> bool:
    """True when a paragraph can participate in a source-lineated run.

    Hard breaks are structural on their own. Short standalone paragraphs remain
    eligible for the existing verse classifier, but are only emitted as bare
    `LineatedBlock`s when surrounding source evidence makes that safe.
    """
    return not p.empty and bool(p.inlines) and (
        _para_has_hard_lineation(p) or _para_lineated(p)
    )


# `ответ` is rendered as both `answer` and `response` across the EN editions.
_CODA_PSEUDO_HEADING_RE = re.compile(
    r"^(?:\d{1,4}|вопрос|ответ|question|answer|response)\s*:?\s*$",
    re.IGNORECASE,
)
_VISUAL_CODA_LINE_MAX = 64
_VISUAL_CODA_AVG_MAX = 48.0


def _block_lines(p: ir.Paragraph) -> list[list[ir.Inline]]:
    # Verse display lines as detection sees them: hard `LineBreak`s (incl. nested in
    # `Emph`) split; `SoftBreak` wrapping joins as a space.
    return [ln for ln in inline_lines(p.inlines, soft_break=False) if inline_plain(ln)]


def _all_lines(paras: list[ir.Paragraph]) -> list[str]:
    return [inline_plain(ln) for p in paras for ln in _block_lines(p)]


def _is_compact_coda(lines: list[str]) -> bool:
    """A coda is a compact closing couplet, not two prose preview sentences."""
    if len(lines) != 2:
        return False
    lengths = [len(line) for line in lines]
    return max(lengths) <= _VISUAL_CODA_LINE_MAX and (
        sum(lengths) / len(lengths)
    ) <= _VISUAL_CODA_AVG_MAX


def _collect_unit(
    blocks: list[ir.Block],
    i: int,
) -> tuple[list[ir.Paragraph], int]:
    """Collect ONE lineation decision unit, starting at an eligible paragraph.

    A unit is a maximal run of verse-eligible paragraphs plus its interior empty
    Word paragraphs. Within one authored flow, a blank row is a stanza break the
    unit spans — and a `lineation_group` id change across that blank (or beside
    ungrouped rows) is the same stanza structure, because `w:contextualSpacing`
    continuity restarts at every blank: one poem arrives as one group PER STANZA,
    never one group per poem. The one real seam is two DIFFERENT visual groups
    directly abutting — Word renders fused rows, a spacing change, then fused
    rows — which is two visual units and therefore two decisions. Trailing
    empties stay with the unit as source evidence; edge gaps are never emitted
    as stanzas.
    """
    first = blocks[i]
    assert isinstance(first, ir.Paragraph)
    run: list[ir.Paragraph] = [first]
    i += 1
    n = len(blocks)
    pending_from = i  # rewind point: gap paragraphs not yet committed to the unit
    while i < n:
        b = blocks[i]
        if not isinstance(b, ir.Paragraph):
            break
        if b.empty:
            i += 1
            continue
        if not _para_structurally_lineated(b):
            break
        prev = run[-1]  # pending_from == i implies run[-1] is the adjacent content row
        if (
            pending_from == i
            and prev.lineation_group is not None
            and b.lineation_group is not None
            and prev.lineation_group != b.lineation_group
        ):
            return run, i
        run.extend(cast("list[ir.Paragraph]", blocks[pending_from:i]))
        run.append(b)
        i += 1
        pending_from = i
    # Trailing empties (before prose/structure/end) stay with the unit as evidence.
    run.extend(cast("list[ir.Paragraph]", blocks[pending_from:i]))
    return run, i


def fold_lineation(blocks: list[ir.Block]) -> list[ir.Block]:
    """Q1: fold source rows into `LineatedBlock`s, never `VerseBlock`s.

    Explicit/mechanical lineation is axiomatic: Pandoc `LineBlock`s already arrive
    as `LineatedBlock`, and paragraphs with hard `<w:br/>` boundaries are folded
    here regardless of verse register. The only non-explicit path is the named
    `_should_infer_source_row_lineation` gate below; it is source-row inference,
    not register promotion.

    The walk hands `_fold_unit` one decision unit at a time with its section
    context: `after_boundary` (the unit opens a heading/thematic section — empty
    paragraphs are transparent to it; an ineligible paragraph consumes it UNLESS
    it shares the unit's visual group, where Word renders the whole group as one
    block attached to the boundary) and `before_boundary` (a boundary follows
    across at most a gap).
    """
    out: list[ir.Block] = []
    i = 0
    n = len(blocks)
    after_boundary = True
    boundary_group: int | None = None  # visual group still holding the boundary
    after_lineated = False             # a lineated block precedes, across at most a gap

    while i < n:
        b = blocks[i]
        if isinstance(b, (ir.Heading, ir.ThematicBreak)):
            out.append(b)
            after_boundary = True
            boundary_group = None
            after_lineated = False
            i += 1
            continue
        if not isinstance(b, ir.Paragraph):
            after_lineated = isinstance(b, (ir.LineatedBlock, ir.VerseBlock))
            out.append(b)
            after_boundary = False
            boundary_group = None
            i += 1
            continue
        if b.empty:
            out.append(b)
            i += 1
            continue
        if _para_structurally_lineated(b):
            gid = b.lineation_group
            run, i = _collect_unit(blocks, i)
            folded = _fold_unit(
                run,
                after_source_boundary=after_boundary and boundary_group in (None, gid),
                before_source_boundary=_has_source_boundary_after_gap(blocks, i),
                after_lineated=after_lineated,
            )
            out.extend(folded)
            # The unit may return trimmed edge prose around its block: what
            # "precedes" the next unit is the LAST content block emitted.
            last_content = next(
                (x for x in reversed(folded)
                 if not (isinstance(x, ir.Paragraph) and x.empty)),
                None,
            )
            after_lineated = isinstance(last_content, ir.LineatedBlock)
            after_boundary = False
            boundary_group = None
            continue
        # Ineligible prose: consumes the section boundary, unless its visual group
        # keeps the attachment alive for a later eligible sub-run of the SAME group
        # (a lineated tail after a long opening citation inside one fused group).
        if after_boundary and b.lineation_group is not None and boundary_group in (None, b.lineation_group):
            boundary_group = b.lineation_group
        else:
            after_boundary = False
            boundary_group = None
        after_lineated = False
        out.append(b)
        i += 1
    return out


def _skip_empty_paragraphs(blocks: list[ir.Block], i: int) -> tuple[int, bool]:
    start = i
    while i < len(blocks) and isinstance((p := blocks[i]), ir.Paragraph) and p.empty:
        i += 1
    return i, i > start


def _has_source_boundary_after_gap(blocks: list[ir.Block], i: int) -> bool:
    i, _saw_gap = _skip_empty_paragraphs(blocks, i)
    return i >= len(blocks) or isinstance(blocks[i], (ir.Heading, ir.ThematicBreak))


def _segment_spans(run: list[ir.Paragraph]) -> list[tuple[int, int]]:
    """`[start, end)` index spans of the unit's gap-separated content segments."""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for idx, p in enumerate(run):
        if p.empty:
            if start is not None:
                spans.append((start, idx))
                start = None
        elif start is None:
            start = idx
    if start is not None:
        spans.append((start, len(run)))
    return spans


# "Departs from the unit's own register": a stanza-shaped piece whose mean line
# length is far past the rest of the unit's. Absolute floors keep short-line
# units' slightly-longer pieces; the RELATIVE ratio keeps long-line poems'
# homogeneous pieces — judge a row set against its unit, not by absolutes. A
# ratio of means is scale-invariant, so it reads the same across languages even
# though EN renders the same content ~10% longer in characters than RU.
_REGISTER_DEPART_RATIO = 1.5


def _trim_prose_register_tail(
    run: list[ir.Paragraph],
) -> tuple[list[ir.Paragraph], list[ir.Paragraph]]:
    """Split `(core, tail)`: trailing stanzas that are not the unit's verse.

    Two trailing shapes leave the unit before the lineation decision:

      * a stanza of pure pseudo-heading fragments (`138`, `Вопрос:`) — the next
        section's furniture, never this unit's closing stanza;
      * a stanza whose register departs from the unit's own (see the constants
        above) — following prose that travelled with the unit.

    A single-segment unit is never trimmed; the lineation gate judges it whole.
    """
    spans = _segment_spans(run)
    tail = len(spans)

    def seg_lines(span: tuple[int, int]) -> list[str]:
        return _all_lines(run[span[0]:span[1]])

    def mean(lines: list[str]) -> float:
        return sum(len(line) for line in lines) / len(lines)

    def departs(edge: tuple[int, int], rest: list[tuple[int, int]]) -> bool:
        # Only a TWO-LINE tail (the closing-couplet position `_is_compact_coda`
        # already owns) can be a preview pair; longer tails are stanza structure.
        edge_lines = seg_lines(edge)
        if len(edge_lines) != 2:
            return False
        rest_mean = mean([line for span in rest for line in seg_lines(span)])
        return (
            max(len(line) for line in edge_lines) > _VISUAL_CODA_LINE_MAX
            and mean(edge_lines) > _REGISTER_DEPART_RATIO * rest_mean
        )

    while tail > 1 and all(
        _CODA_PSEUDO_HEADING_RE.match(line) for line in seg_lines(spans[tail - 1])
    ):
        tail -= 1
    while tail > 1 and departs(spans[tail - 1], spans[:tail - 1]):
        tail -= 1

    if tail == len(spans):
        # No trim: trailing empties remain the unit's source evidence, as before.
        return list(run), []
    # Cut at the trimmed segment's first row: the separating gap stays with the
    # core — the author DID set the core off with a blank, and that stanza
    # evidence must not leave with the trimmed prose.
    end = spans[tail][0]
    return list(run[:end]), list(run[end:])


def _gate_and_build(
    run: list[ir.Paragraph],
    *,
    after_source_boundary: bool,
    before_source_boundary: bool,
    after_lineated: bool,
) -> ir.LineatedBlock | None:
    """One inference-gate decision over `run`: the folded block, or `None`."""
    evidence = _run_evidence(run)
    if _should_infer_source_row_lineation(
        run,
        after_source_boundary=after_source_boundary,
        before_source_boundary=before_source_boundary,
        after_lineated=after_lineated,
    ):
        evidence = ir.LineationEvidence(
            hard_break=evidence.hard_break,
            inferred_source_rows=True,
            stanza_break=evidence.stanza_break,
            compact_callout=evidence.compact_callout,
        )
    if not (evidence.inferred_source_rows or evidence.compact_callout):
        return None
    return _build_lineated(run, evidence=evidence)


def _fold_sub_units(
    run: list[ir.Paragraph],
    *,
    after_source_boundary: bool,
    before_source_boundary: bool,
    after_lineated: bool,
) -> list[ir.Block] | None:
    """Decide each visual sub-unit of a failed merged unit on its own.

    The merge lets a poem's stanzas share evidence; it must never DILUTE a
    fused group's own evidence below folding. When the whole unit is not
    verse, re-decide its `lineation_group`-delimited sub-runs independently
    (the pre-merge unit shape). Returns `None` when nothing folds.
    """
    pieces: list[tuple[list[ir.Paragraph], bool]] = []  # (rows, is_sub_unit)
    sub: list[ir.Paragraph] = []
    pending: list[ir.Paragraph] = []
    for p in run:
        if p.empty:
            pending.append(p)
            continue
        if sub and p.lineation_group != sub[-1].lineation_group:
            pieces.append((sub, True))
            pieces.append((pending, False))
            sub, pending = [p], []
            continue
        sub.extend(pending)
        pending = []
        sub.append(p)
    if sub:
        pieces.append((sub, True))
    if pending:
        pieces.append((pending, False))

    sub_units = [rows for rows, is_sub in pieces if is_sub]
    if len(sub_units) <= 1:
        return None
    out: list[ir.Block] = []
    folded_any = False
    for rows, is_sub in pieces:
        if not is_sub:
            out.extend(rows)
            continue
        block = _gate_and_build(
            rows,
            after_source_boundary=after_source_boundary and rows is sub_units[0],
            before_source_boundary=before_source_boundary and rows is sub_units[-1],
            after_lineated=after_lineated,
        )
        if block is None:
            out.extend(rows)
            after_lineated = False
        else:
            out.append(block)
            after_lineated = True
            folded_any = True
    return out if folded_any else None


# The compact-source-line boundary, shared by the two rules that draw it: a
# callout row must fit under it, and a lone row between blank rows past it (when
# its register also departs from the unit's) is a prose paragraph, not a
# one-line stanza. One constant so the two rules cannot drift apart. Measured in
# characters: EN renders the same content ~10% longer than RU, so the cap reads
# slightly stricter on EN — the conservative direction (refuses to fold).
_COMPACT_SOURCE_LINE_MAX = 80


def _split_at_prose_singletons(
    run: list[ir.Paragraph],
) -> list[tuple[list[ir.Paragraph], bool]] | None:
    """Pieces of `(rows, is_unit)` around lone prose-length stanzas, or `None`
    when the unit has none and stands whole."""
    spans = _segment_spans(run)
    all_lines = _all_lines([p for p in run if not p.empty])

    def is_prose_singleton(span: tuple[int, int]) -> bool:
        lines = _all_lines(run[span[0]:span[1]])
        if len(lines) != 1 or len(lines[0]) <= _COMPACT_SOURCE_LINE_MAX:
            return False
        if len(all_lines) < 2:
            return False
        rest_mean = (
            sum(len(line) for line in all_lines) - len(lines[0])
        ) / (len(all_lines) - 1)
        return len(lines[0]) > _REGISTER_DEPART_RATIO * rest_mean

    cut = [span for span in spans if is_prose_singleton(span)]
    if not cut:
        return None
    pieces: list[tuple[list[ir.Paragraph], bool]] = []
    pos = 0
    for start, end in cut:
        if start > pos:
            pieces.append((list(run[pos:start]), True))
        pieces.append((list(run[start:end]), False))
        pos = end
    if pos < len(run):
        pieces.append((list(run[pos:]), True))
    return pieces


def _fold_unit(
    run: list[ir.Paragraph],
    *,
    after_source_boundary: bool,
    before_source_boundary: bool,
    after_lineated: bool = False,
) -> list[ir.Block]:
    """Return the unit folded into one structural lineated block (with any
    trimmed edge prose back as paragraphs), or its original paragraphs when no
    lineation evidence holds."""
    content = [p for p in run if not p.empty]
    if len(_all_lines(content)) < 2:
        return list(run)
    if (evidence := _run_evidence(run)).hard_break:
        # Authored `<w:br>` lineation is axiomatic: the unit folds whole.
        return [_build_lineated(run, evidence=evidence)]
    if (pieces := _split_at_prose_singletons(run)) is not None:
        out: list[ir.Block] = []
        first = True
        for rows, is_unit in pieces:
            if not is_unit:
                out.extend(rows)
                after_lineated = False
            elif any(not p.empty for p in rows):
                folded = _fold_unit(
                    rows,
                    after_source_boundary=after_source_boundary and first,
                    before_source_boundary=(
                        before_source_boundary and rows is pieces[-1][0]
                    ),
                    after_lineated=after_lineated,
                )
                out.extend(folded)
                after_lineated = isinstance(folded[-1], ir.LineatedBlock)
            else:
                out.extend(rows)
            first = False
        return out
    core, tail = _trim_prose_register_tail(run)
    if len(_all_lines([p for p in core if not p.empty])) >= 2:
        block = _gate_and_build(
            core,
            after_source_boundary=after_source_boundary,
            before_source_boundary=before_source_boundary and not tail,
            after_lineated=after_lineated,
        )
        if block is not None:
            return [block, *tail]
    folded = _fold_sub_units(
        run,
        after_source_boundary=after_source_boundary,
        before_source_boundary=before_source_boundary,
        after_lineated=after_lineated,
    )
    return folded if folded is not None else list(run)


def _is_strong_colon_opener(p: ir.Paragraph) -> bool:
    if len(p.inlines) != 1:
        return False
    only = p.inlines[0]
    return (
        isinstance(only, ir.Emphasis)
        and only.kind == "strong"
        and inline_plain(only.children).rstrip().endswith(":")
    )


def _is_compact_strong_opener_callout(run: list[ir.Paragraph]) -> bool:
    """A narrow source-lineation signal for DOCX callouts.

    This is not "blank before short lines". It requires the run itself to be a
    compact unindented callout with a bold colon opener followed by very short
    source paragraphs. Indented paragraph runs stay prose, which protects
    one-sentence-per-paragraph body text.
    """
    content = [p for p in run if not p.empty]
    if not (3 <= len(content) <= 8):
        return False
    if any(p.indented for p in content):
        return False
    if not _is_strong_colon_opener(content[0]):
        return False
    lines = _all_lines(content)
    if len(lines) != len(content):
        return False
    lengths = [len(line) for line in lines]
    return max(lengths) <= _COMPACT_SOURCE_LINE_MAX and (sum(lengths) / len(lengths)) <= 45.0


def _stanza_segment_lines(run: list[ir.Paragraph]) -> list[list[str]]:
    """Display lines of each gap-delimited stanza the unit would build."""
    return [
        _all_lines(run[start:end])
        for start, end in _segment_spans(run)
    ]


def _should_infer_source_row_lineation(
    run: list[ir.Paragraph],
    *,
    after_source_boundary: bool,
    before_source_boundary: bool,
    after_lineated: bool = False,
) -> bool:
    """Q1b gate: infer lineation from compact source rows.

    This is intentionally named as inference and uses only source-row shape:
    short label-free rows, stanza empties, a structural section boundary, or a
    narrow unindented strong-colon callout. It does not inspect heading titles or
    decide verse register. The rules, in order:

      * CALLOUT — a compact unindented strong-colon callout;
      * GROUPED — the unit carries visual-continuity fusion (`w:contextualSpacing`
        renders some of its rows as tight contiguous lines, the way Word displays
        verse) over ≥ 3 lines with verse geometry (mean ≤ 60) — wherever it sits
        in the document; a poem's interior stanzas carry no boundary or gap
        evidence, only this fused-rows signal;
      * ATTACHED — the unit opens a heading/thematic section and reads as verse
        on its own geometry (≤ 32 lines, mean ≤ 60, max ≤ 150) — a heading
        followed by a few prose sentences is the dominant prose shape, and
        genuine attached runs in this corpus sit well under the cap;
      * CLOSING — the unit continues a preceding lineated block as its compact
        two-line coda right before the next section boundary (and is not a
        pseudo-heading fragment); without a lineated antecedent a compact pair
        before a heading is just two closing prose sentences;
      * GAPPED — the unit carries authored stanza gaps around ≥ 3 lines; the
        loose cap (mean ≤ 120) is earned only by real stanza STRUCTURE (≥ 2
        stanzas, at least one multi-line), else the strict geometry below.

    Throughout, a unit without stanza structure — blank-separated SINGLE-line
    rows (chapter prose is stored exactly so: one sentence per `w:p`, blank rows
    between) or one lone stanza with a trailing gap — proves nothing by its
    gaps, so it must read as verse on its own geometry (mean ≤ 45).
    """
    content = [p for p in run if not p.empty]
    if not content or not all(_para_lineated(p) for p in content):
        return False
    if any(p.indented for p in content):
        return False
    if _is_compact_strong_opener_callout(run):
        return True
    lines = _all_lines(content)
    if len(lines) < 2:
        return False
    lengths = [len(line) for line in lines]
    avg = sum(lengths) / len(lengths)
    grouped = any(p.lineation_group is not None for p in content)
    if grouped and len(lines) >= 3 and avg <= 60:
        return True
    segments = _stanza_segment_lines(run)
    structured = len(segments) >= 2 and any(len(seg) > 1 for seg in segments)
    all_singleton = all(len(seg) == 1 for seg in segments)
    if (
        after_source_boundary
        and len(lines) <= 32
        and max(lengths) <= 150
        and avg <= (45 if all_singleton else 60)
    ):
        return True
    if (
        after_lineated
        and before_source_boundary
        and len(lines) == 2
        and _is_compact_coda(lines)
        and not any(_CODA_PSEUDO_HEADING_RE.match(line) for line in lines)
    ):
        return True
    if not (any(p.empty for p in run) and len(lines) >= 3):
        return False
    return avg <= 120 if structured else avg <= 45


def _run_evidence(run: list[ir.Paragraph]) -> ir.LineationEvidence:
    content = [p for p in run if not p.empty]
    return ir.LineationEvidence(
        hard_break=any(
            any(isinstance(x, ir.LineBreak) for x in walk_inlines(p.inlines))
            for p in content
        ),
        # Any blank captured with the run is source lineation evidence. Edge blanks
        # are trimmed when building stanzas so they do not render as fake empty
        # stanzas, but a trailing blank still signals that the preceding compact run
        # was authored as lineated material rather than ordinary prose sentences.
        stanza_break=any(p.empty for p in run),
        compact_callout=_is_compact_strong_opener_callout(run),
    )


def _trim_empty_edges(run: list[ir.Paragraph]) -> list[ir.Paragraph]:
    start = 0
    end = len(run)
    while start < end and run[start].empty:
        start += 1
    while end > start and run[end - 1].empty:
        end -= 1
    return run[start:end]


def _build_lineated(
    run: list[ir.Paragraph],
    *,
    evidence: ir.LineationEvidence | None = None,
) -> ir.LineatedBlock:
    """Build stanzas: an empty paragraph is a stanza break."""
    stanzas: list[list[list[ir.Inline]]] = []
    current: list[list[ir.Inline]] = []

    def flush() -> None:
        nonlocal current
        if current:
            stanzas.append(current)
            current = []

    source_run = _trim_empty_edges(run)
    for p in source_run:
        if p.empty:
            flush()
            continue
        for ln in _block_lines(p):
            current.append(ln)
    flush()
    return ir.LineatedBlock(
        stanzas=stanzas,
        evidence=evidence or ir.LineationEvidence(),
        # Provenance comes from the TEXT rows: an interior stanza-gap row often
        # has no span of its own, and letting it poison the merge would strip
        # whole multi-stanza poems of provenance.
        source_span=ir.merge_source_spans(
            p.source_span for p in source_run if not p.empty
        ),
    )
