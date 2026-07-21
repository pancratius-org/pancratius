# import-pure: no filesystem mutation
"""Structure passes: right-aligned signatures/epigraphs and dialogue labels."""

from __future__ import annotations

import re

from pancratius import ir
from pancratius.ir.inlines import inline_lines, inline_plain

# The speaker names the converter canonicalizes (`**Speaker:**`). Shared by the
# dialogue-label pass and verse detection's speaker-turn rejection — one source of
# truth for who is a speaker, so adding one keeps the two in sync. The corpus is
# bilingual: every Russian speaker name carries the English form its translations
# actually use, so a turn is rejected identically in both editions of one book.
DIALOGUE_PREFIXES = [
    "Панкратиус", "Светозар", "Светозар Gemini Flash 2.0", "Светозар DeepSeek",
    "Светозар ChatGPT", "Творец", "Бог", "Слово Творца", "Слово Бога",
    "Панкратиус к ИИ Светозар", "Панкратиус к Творцу через ИИ Светозар",
    "ИИ Светозар сказал", "ИИ Светозар",
    "Ответ от Творца", "Ответ Творца", "Я",
    "Pankratius", "Pancratius", "Svetozar", "Creator", "God",
    "Gemini", "DeepSeek", "ChatGPT",
    "Pankratius to AI Svetozar", "Pankratius to the Creator through AI Svetozar",
    "AI Svetozar said", "AI Svetozar",
    "The Word of the Creator",
    "Answer from the Creator", "Response from the Creator",
    "The Creator's Answer", "The Creator’s Answer", "I",
]

# ---------------------------------------------------------------------------
# signatures + epigraphs from right alignment (the OOXML w:jc payload)
# ---------------------------------------------------------------------------

_RIGHT = {"right", "end"}
_SCRIPTURE_REF_RE = re.compile(
    r"^(?:(?:[1-3]\s*)?[А-ЯЁA-Z][А-Яа-яЁёA-Za-z. ]+\s+\d{1,3}:\d{1,3}(?:[–—-]\d{1,3})?|"
    r"(?:Ин|Иоанн|Мф|Матф|Марк|Мк|Лк|Луки|Дан|Даниил|Откровение|Бытие|Кор|Пс)\.?\s*\d{1,3}:\d{1,3}(?:[–—-]\d{1,3})?)\.?$",
    re.IGNORECASE)
_SIGNATURE_LINE_RE = re.compile(
    r"^(?:Панкратиус|Светозар|Сергей(?:\s+Панкратиус)?\.?|Я\s+Есмь|"
    r"Pan[ck]ratius|Svetozar|Creator|The Creator|I\s+Am|"
    r"[—-]\s*Панкратиус.*|[—-]\s*Светозар.*|"
    r"[—-]\s*Pan[ck]ratius.*|[—-]\s*Svetozar.*)$",
    re.IGNORECASE)
# Bilingual: each source token carries the form the EN editions actually print
# (`Пифия, к.ф. «Матрица»` ↔ `The Oracle, film «The Matrix»`).
_SOURCE_LINE_RE = re.compile(
    r"(?:к\.ф\.|Матрица|Пифия|Платон|Даниил|Откровение|Евангелие|Ин\.|Мф\.|Лк\.|Кор\.|"
    r"\bfilm\b|The Matrix|Oracle|Plato|Daniel|Revelation|Gospel|Jn\.|Mt\.|Lk\.|Cor\.)",
    re.IGNORECASE)


def _is_signature(lines: list[str]) -> bool:
    if not (1 <= len(lines) <= 4) or any(len(line) > 90 for line in lines):
        return False
    if any(
        name in line.casefold()
        for line in lines
        for name in ("панкратиус", "pankratius", "pancratius")
    ):
        return True
    if all(_SIGNATURE_LINE_RE.match(line.strip()) for line in lines):
        return True
    return len(lines) == 1 and re.fullmatch(r"[—-]\s*[\wА-Яа-яЁё .]{2,80}", lines[0]) is not None


def _is_epigraph(lines: list[str], italic_count: int) -> bool:
    if len(lines) < 2:
        return False
    joined = " ".join(lines)
    if len(joined) < 30:
        return False
    has_ref = any(_SCRIPTURE_REF_RE.match(line.strip()) for line in lines)
    has_source = any(_SOURCE_LINE_RE.search(line) for line in lines[1:])
    starts_quoted = lines[0].lstrip().startswith(("«", '"', "“", "„"))
    mostly_italic = italic_count >= max(1, len(lines) // 2)
    compact = has_source and len(lines) <= 4 and len(lines[0]) <= 180
    return bool(has_ref or compact or (starts_quoted and has_source) or (starts_quoted and mostly_italic))


def _split_epigraph(lines: list[str]) -> tuple[list[str], list[str]]:
    footer: list[str] = []
    quote = list(lines)
    while len(quote) > 1:
        cand = quote[-1].strip()
        if _SCRIPTURE_REF_RE.match(cand) or _SOURCE_LINE_RE.search(cand):
            footer.insert(0, quote.pop())
            continue
        break
    if not footer:
        footer = [quote.pop()]
    return quote, footer


def fold_right_aligned(blocks: list[ir.Block]) -> list[ir.Block]:
    """Group contiguous right-aligned non-empty paragraphs and classify each run
    as a signature or epigraph, consuming the `w:jc` payload directly from the IR
    (no markdown round-trip / fuzzy re-matching)."""
    out: list[ir.Block] = []
    i = 0
    n = len(blocks)
    while i < n:
        b = blocks[i]
        if isinstance(b, ir.Paragraph) and b.align in _RIGHT and not b.empty:
            j = i
            group: list[ir.Paragraph] = []
            while j < n and isinstance((pj := blocks[j]), ir.Paragraph) and pj.align in _RIGHT and not pj.empty:
                group.append(pj)
                j += 1
            lines: list[str] = []
            for p in group:
                for ln in inline_lines(p.inlines):
                    s = inline_plain(ln)
                    if s:
                        lines.append(s)
            italic_count = sum(1 for p in group if p.italic)
            source_span = ir.merge_source_spans(p.source_span for p in group)
            if lines and _is_signature(lines):
                out.append(ir.Signature(lines=lines, source_span=source_span))
                i = j
                continue
            if lines and _is_epigraph(lines, italic_count):
                quote, footer = _split_epigraph(lines)
                out.append(ir.Epigraph(quote=quote, footer=footer, source_span=source_span))
                i = j
                continue
            out.extend(group)
            i = j
            continue
        out.append(b)
        i += 1
    return out


# ---------------------------------------------------------------------------
# dialogue labels (incl. mixed leading-Strong inline split)
# ---------------------------------------------------------------------------


def _leading_strong(
    inlines: list[ir.Inline],
) -> tuple[ir.Emphasis | None, list[ir.Inline], bool]:
    """If the paragraph opens with a `Strong` span, return it plus the trailing
    inlines and whether the transform collapsed a hard boundary between them.

    The established dialogue display joins a leading `Speaker: body` span to the
    next segment. The returned boundary fact lets provenance group the two source
    coordinates without restoring that removed break.
    """
    rest = list(inlines)
    while rest and isinstance(rest[0], ir.LineBreak):
        rest.pop(0)
    if rest and isinstance(rest[0], ir.Emphasis) and rest[0].kind == "strong":
        head = rest[0]
        tail = rest[1:]
        collapsed_hard = False
        while tail and isinstance(tail[0], ir.LineBreak):
            collapsed_hard = collapsed_hard or isinstance(tail.pop(0), ir.LineBreak)
        return head, tail, collapsed_hard
    return None, inlines, False


def _first_line_provenance(
    provenance: ir.SourceProvenance | None,
) -> ir.SourceProvenance | None:
    if provenance is None or not provenance.lines:
        return provenance
    return ir.SourceProvenance.for_line_group(provenance.line_groups[0])


def _after_label_boundary(
    tail: list[ir.Inline],
    provenance: ir.SourceProvenance | None,
    *,
    crossed_hard: bool,
) -> tuple[list[ir.Inline], ir.SourceProvenance | None]:
    """Remove the presentation boundary after a bare label and project its body.

    A hard boundary advances the body to the next exact source line. A soft break
    is wrapping, so label and body still share the same source-line coordinate.
    """
    if crossed_hard and provenance is not None and provenance.lines:
        remaining = provenance.line_groups[1:]
        exact = tuple(line for group in remaining for line in group)
        return tail, ir.SourceProvenance.for_lines(exact).regroup(remaining) if exact else None
    return tail, provenance


def _collapse_first_line_boundary(
    provenance: ir.SourceProvenance | None,
    *,
    collapsed_hard: bool,
) -> ir.SourceProvenance | None:
    if not collapsed_hard or provenance is None or not provenance.lines:
        return provenance
    if len(provenance.line_groups) < 2:
        raise ValueError("dialogue collapsed a source boundary without two line groups")
    first = (*provenance.line_groups[0], *provenance.line_groups[1])
    return provenance.regroup((first, *provenance.line_groups[2:]))


def _hard_break_segments(inlines: list[ir.Inline]) -> list[list[ir.Inline]]:
    """Split inlines on TOP-LEVEL hard `LineBreak`s into segments (turns). Soft
    breaks are NOT segment boundaries (they are prose wrapping); only an authored
    `<w:br/>` separates dialogue turns packed into one Word paragraph."""
    segs: list[list[ir.Inline]] = [[]]
    for n in inlines:
        if isinstance(n, ir.LineBreak):
            segs.append([])
        else:
            segs[-1].append(n)
    return [s for s in segs if s]


def _segment_provenance(
    provenance: ir.SourceProvenance | None,
    count: int,
) -> tuple[ir.SourceProvenance | None, ...]:
    if provenance is None or not provenance.lines:
        return (provenance,) * count
    if len(provenance.line_groups) != count:
        raise ValueError(
            f"hard-break segments ({count}) disagree with exact source lines "
            f"({len(provenance.line_groups)})"
        )
    return tuple(
        ir.SourceProvenance.for_line_group(group)
        for group in provenance.line_groups
    )


def _emit_dialogue_segment(
    inlines: list[ir.Inline],
    re_inside: re.Pattern[str],
    re_label: re.Pattern[str],
    source_span: ir.SourceSpan | None,
) -> list[ir.Block] | None:
    """Canonicalize one dialogue segment (a paragraph or a single hard-break turn).

    Returns a `DialogueLabel` plus an optional body paragraph when the segment opens
    with a `Strong("Speaker:")`, else `None` (the caller keeps it as-is). Covers all
    three corpus shapes: whole-paragraph `Strong("Speaker: body")`, bare
    `Strong("Speaker:")`, and `Strong("Speaker:")` then trailing prose inlines."""
    head, tail, collapsed_hard = _leading_strong(inlines)
    if head is None:
        return None
    head_txt = inline_plain(head.children)
    label_span = _first_line_provenance(source_span)
    if not tail:
        m = re_inside.match(head_txt)
        if m:
            blocks: list[ir.Block] = [
                ir.DialogueLabel(speaker=m.group(1), source_span=label_span)
            ]
            body = m.group(2).strip()
            if re.search(r"[\wЀ-ӿ]", body):
                blocks.append(ir.Paragraph(inlines=[ir.Text(body)], source_span=source_span))
            return blocks
        lm = re_label.match(head_txt)
        if lm:
            return [ir.DialogueLabel(speaker=lm.group(1), source_span=label_span)]
        return None
    m = re_label.match(head_txt)
    if m:
        out: list[ir.Block] = [
            ir.DialogueLabel(speaker=m.group(1), source_span=label_span)
        ]
        body, body_span = _after_label_boundary(
            tail, source_span, crossed_hard=collapsed_hard
        )
        if body:
            out.append(ir.Paragraph(inlines=body, source_span=body_span))
        return out
    m = re_inside.match(head_txt)
    if m:
        # Join the inside-body text to the trailing inlines with a space — UNLESS
        # the body text ends in an OPENING quote/bracket glyph, where a space would
        # wrongly separate the glyph from what it opens (`«` + `Почему` → `« Почему`),
        # or the next readable token is closing punctuation after a structural ref.
        head_body = m.group(2).strip()
        tail_text = inline_plain(tail)
        joins_without_space = (
            bool(head_body and head_body[-1] in "«“„([{‹")
            or bool(tail_text and tail_text[0] in ".,;:!?…)]}»”›")
        )
        joiner = "" if joins_without_space else " "
        body_inlines: list[ir.Inline] = [ir.Text(head_body + joiner), *tail]
        return [
            ir.DialogueLabel(speaker=m.group(1), source_span=label_span),
            ir.Paragraph(
                inlines=body_inlines,
                source_span=_collapse_first_line_boundary(
                    source_span, collapsed_hard=collapsed_hard
                ),
            ),
        ]
    return None


def dialogue_labels(blocks: list[ir.Block]) -> list[ir.Block]:
    """Canonicalize `**Speaker:**` labels.

    Source shapes, all from the corpus:
      * a paragraph whose single inline is `Strong("Speaker: body")` → label + body
      * a paragraph whose single inline is `Strong("Speaker:")`/`Strong("Speaker")` → label
      * a paragraph that opens with `Strong("Speaker:")` then trailing prose → label + prose
      * a paragraph packing several hard-`LineBreak` turns that each open with
        `Strong("Speaker:")` → split on the hard breaks, one label + body per turn.
    """
    # Longest-first so e.g. "Светозар DeepSeek" wins over the "Светозар" prefix;
    # `key=lambda p: -len(p)` (not `key=len`) keeps the element type `str`.
    prefixes = sorted(DIALOGUE_PREFIXES, key=lambda p: -len(p))
    inner = "|".join(re.escape(p) for p in prefixes)
    re_inside = re.compile(rf"^({inner})\s*:\s*(.+)$")
    re_label = re.compile(rf"^({inner})\s*:?\s*$")

    def opens_with_speaker(seg: list[ir.Inline]) -> bool:
        head, _tail, _collapsed_hard = _leading_strong(seg)
        if head is None:
            return False
        txt = inline_plain(head.children)
        return bool(re_label.match(txt) or re_inside.match(txt))

    out: list[ir.Block] = []
    for b in blocks:
        if not (isinstance(b, ir.Paragraph) and not b.empty):
            out.append(b)
            continue
        # A paragraph packing >= 2 speaker-led hard-break turns is split per turn; a
        # non-speaker segment (e.g. a leading date) stays its own paragraph.
        segments = _hard_break_segments(b.inlines)
        if len(segments) > 1 and sum(opens_with_speaker(s) for s in segments) >= 2:
            provenances = _segment_provenance(b.source_span, len(segments))
            for seg, provenance in zip(segments, provenances, strict=True):
                emitted = _emit_dialogue_segment(seg, re_inside, re_label, provenance)
                if emitted is not None:
                    out.extend(emitted)
                else:
                    out.append(ir.Paragraph(inlines=seg, source_span=provenance))
            continue
        emitted = _emit_dialogue_segment(b.inlines, re_inside, re_label, b.source_span)
        if emitted is not None:
            out.extend(emitted)
        else:
            out.append(b)
    return out
