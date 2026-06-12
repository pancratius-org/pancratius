# import-pure: no filesystem mutation
"""Read-only DOCX → IR fidelity inspector (a diagnostic, never writes src/content).

This is the inspector the ``docx_adapter`` debugging note calls for: it prints,
per source body paragraph, the OOXML signals that verse / signature / epigraph
detection consumes — resolved style, ``w:contextualSpacing``, spacing attrs,
``w:jc`` alignment, ``w:ind`` indent, ``w:numPr`` list, ``w:pBdr`` border, the
hard ``<w:br/>`` count, and the assigned visual ``lineation_group`` — beside the
IR block the paragraph actually became after the full ``adapt`` → ``normalize``
pipeline. A human can then see WHY a run was (or was not) folded into a
``verse``: the source signals on the left, the classifier's verdict on the
right.

It reuses ``pancratius.docx_adapter`` so the signals shown are exactly the ones
the importer reads — no parallel re-implementation that could drift from the
converter.

PURE: opens the DOCX zip for READ only and runs the pure import passes into a
scratch media dir. It mutates nothing under ``src/content``.

Run it:

    uv run pancratius docx inspect <docx> --contains "Память кого"
    uv run pancratius docx inspect --book 13 --around "Память кого" --context 8
    uv run pancratius docx inspect --book 13 --verse-only
"""
from __future__ import annotations

import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pancratius import docx_adapter as da
from pancratius import ir
from pancratius.passes.pipeline import PER_ORDINAL_SEAM, Context, run

W = da.W


# ---------------------------------------------------------------------------
# rich per-paragraph source record (everything the importer's signals derive from)
# ---------------------------------------------------------------------------


@dataclass
class ParaRow:
    index: int
    text: str
    style: str            # resolved style id (direct pStyle or document default)
    direct_style: str     # the paragraph's own w:pStyle (``""`` = inherits default)
    align: str            # w:jc (``""`` = inherit/left)
    contextual: bool      # resolved w:contextualSpacing (suppresses para spacing)
    spacing: dict[str, str]
    indent: dict[str, str]  # w:ind attrs (firstLine / left / hanging) — prose tell
    numbered: bool        # w:numPr — a list item
    border: ir.BorderKind  # w:pBdr gesture kind ("box"/"rule"/"other"; "" = none)
    heading: bool
    thematic: bool
    br_count: int         # hard <w:br/> inside the paragraph (authored lineation)
    empty: bool
    lineation_group: int | None = None
    block_kind: str = "?"  # the IR block this paragraph's text landed in
    block_source_span: ir.SourceSpan | None = None


class DocxInspectError(ValueError):
    """The requested DOCX inspection cannot be completed from the given input."""


@dataclass(frozen=True)
class InspectOptions:
    contains: str | None = None
    around: str | None = None
    context: int = 6
    index_range: tuple[int, int] | None = None
    verse_only: bool = False
    lineated_only: bool = False

    def __post_init__(self) -> None:
        filters = [
            self.contains is not None,
            self.around is not None,
            self.index_range is not None,
            self.verse_only,
            self.lineated_only,
        ]
        if sum(filters) > 1:
            raise DocxInspectError(
                "choose only one inspect filter: --contains, --around, --range, "
                "--verse-only, or --lineated-only"
            )
        if self.context < 0:
            raise DocxInspectError("--context must be non-negative")
        if self.index_range is not None:
            lo, hi = self.index_range
            if lo < 0 or hi < lo:
                raise DocxInspectError("--range must be shaped as LO:HI with 0 <= LO <= HI")


@dataclass(frozen=True)
class InspectResult:
    docx: Path
    rows: tuple[ParaRow, ...]
    selected: tuple[ParaRow, ...]

    @property
    def verse_paragraphs(self) -> int:
        return sum(1 for row in self.rows if row.block_kind == "VerseBlock")

    @property
    def lineated_paragraphs(self) -> int:
        return sum(1 for row in self.rows if row.block_kind == "LineatedBlock")

    @property
    def lineation_groups(self) -> int:
        return len({row.lineation_group for row in self.rows if row.lineation_group is not None})

    @property
    def ambiguous_paragraphs(self) -> int:
        return sum(1 for row in self.rows if row.block_kind.startswith("Ambiguous["))


def _ind_attrs(ppr: ET.Element | None) -> dict[str, str]:
    ind = ppr.find(f"{W}ind") if ppr is not None else None
    if ind is None:
        return {}
    return {k.removeprefix(W): v for k, v in ind.attrib.items()}


def _br_count(p: ET.Element) -> int:
    return sum(1 for el in p.iter() if el.tag in {f"{W}br", f"{W}cr"})


def read_rows(docx: Path) -> list[ParaRow]:
    """Every top-level body paragraph with its full source signal set, in order.

    Unlike ``docx_adapter.read_w_jc`` (which emits boundary sentinels for lists and
    tables and is trimmed to reconciliation needs), this keeps every paragraph and
    every signal so the inspector can show the human the complete picture.
    """
    with zipfile.ZipFile(docx) as zf:
        styles, default_style = da._paragraph_styles(zf)
        doc_default_spacing = da._doc_default_spacing(zf)
        root = ET.fromstring(zf.read("word/document.xml"))
    body = root.find(f"{W}body")
    if body is None:
        return []

    rows: list[ParaRow] = []
    # Mirror docx_adapter.read_w_jc's walk so the lineation_group ids match the
    # importer's exactly — build _SourceParagraph records the same way, then read
    # the group assignment back.
    src: list[da._SourceParagraph] = []
    raw_index: list[int] = []  # rows index aligned to reconcile-eligible src records
    source_segment = 0

    def walk(el: ET.Element) -> None:
        nonlocal source_segment
        for child in el:
            if child.tag == f"{W}p":
                ppr = child.find(f"{W}pPr")
                numbered = ppr is not None and ppr.find(f"{W}numPr") is not None
                direct_style = da._w_val(ppr.find(f"{W}pStyle") if ppr is not None else None)
                style = direct_style or default_style
                spacing = {**doc_default_spacing,
                           **da._resolved_spacing(style, styles, da._spacing_attrs(ppr))}
                txt = da._paragraph_text(child).strip()
                heading = bool(re.fullmatch(r"(?:Heading\d+|[1-9])", direct_style))
                thematic = txt in {"***", "* * *", "---"}
                contextual = da._resolved_contextual_spacing(
                    style,
                    styles,
                    direct_contextual_spacing=(
                        ppr.find(f"{W}contextualSpacing") is not None
                        if ppr is not None
                        else False
                    ),
                )
                border = da.border_kind(ppr)
                align = da._w_val(ppr.find(f"{W}jc") if ppr is not None else None)
                row = ParaRow(
                    index=len(rows),
                    text=txt,
                    style=style,
                    direct_style=direct_style,
                    align=align,
                    contextual=contextual,
                    spacing=spacing,
                    indent=_ind_attrs(ppr),
                    numbered=numbered,
                    border=border,
                    heading=heading,
                    thematic=thematic,
                    br_count=_br_count(child),
                    empty=not txt,
                )
                rows.append(row)
                # The source-paragraph record the importer would build (list items
                # and tables become boundaries there; keep them aligned to rows).
                if numbered:
                    src.append(da._source_boundary(
                        ir.SourceSpan(row.index, row.index),
                        source_segment=source_segment,
                    ))
                    source_segment += 1
                else:
                    src.append(da._SourceParagraph(
                        align=align, text=txt, style=style,
                        contextual_spacing=contextual, spacing=spacing,
                        indent=da._indent_attrs(ppr), border=border,
                        heading=heading, thematic=thematic,
                        source_span=ir.SourceSpan(row.index, row.index),
                        source_segment=source_segment,
                        empty=da._paragraph_is_empty_source(child, txt),
                    ))
                raw_index.append(row.index)
            elif child.tag == f"{W}tbl":
                src.append(da._source_boundary(source_segment=source_segment))
                source_segment += 1
                raw_index.append(-1)
            elif child.tag == f"{W}sdt":
                content = child.find(f"{W}sdtContent")
                if content is not None:
                    walk(content)

    walk(body)
    da._direction_indents(src)
    da._assign_lineation_groups(src)
    for ri, sp in zip(raw_index, src, strict=True):
        if ri >= 0:
            rows[ri].lineation_group = sp.lineation_group
    return rows


# ---------------------------------------------------------------------------
# IR classification: what block each paragraph's reading text became
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub("", text)).strip()


def _block_kind_name(block: ir.Block) -> str:
    """The inspector's kind vocabulary. External tooling joins on these names, so
    they are derived from the register, not the node class: a verse-registered
    `LineatedBlock` is "VerseBlock", a `QuoteBlock` is "BlockQuote"."""
    if isinstance(block, ir.LineatedBlock):
        return "VerseBlock" if block.register is ir.Register.VERSE else "LineatedBlock"
    if isinstance(block, ir.QuoteBlock):
        return "BlockQuote"
    return type(block).__name__


def _block_lines(block: ir.Block) -> list[str]:
    """The normalized reading lines a block contributes, for membership lookup."""
    from pancratius.ir.normalize import inline_plain

    match block:
        case ir.LineatedBlock():
            return [_norm(inline_plain(line.inlines)) for stanza in block.stanzas for line in stanza]
        case ir.Signature():
            return [_norm(s) for s in block.lines]
        case ir.Epigraph():
            return [_norm(s) for s in (*block.quote, *block.footer)]
        case ir.Paragraph() | ir.Heading():
            return [_norm(inline_plain(block.inlines))]
        case ir.DialogueLabel():
            return [_norm(block.speaker)]
        case _:
            return []


type BlockKindsByText = dict[str, frozenset[str]]


@dataclass(frozen=True)
class BlockSourceHit:
    kinds: frozenset[str]
    span: ir.SourceSpan


type BlockKindsBySource = dict[int, BlockSourceHit]


@dataclass(frozen=True)
class BlockClassifications:
    by_text: BlockKindsByText
    by_source: BlockKindsBySource


@dataclass
class _SourceClassificationBuilder:
    kinds: dict[int, set[str]] = field(default_factory=dict)
    spans: dict[int, ir.SourceSpan] = field(default_factory=dict)

    def add(self, *, name: str, span: ir.SourceSpan) -> None:
        for index in range(span.start, span.end + 1):
            self.kinds.setdefault(index, set()).add(name)
            previous = self.spans.get(index)
            self.spans[index] = (
                span if previous is None
                else ir.SourceSpan(
                    start=min(previous.start, span.start),
                    end=max(previous.end, span.end),
                )
            )

    def build(self) -> BlockKindsBySource:
        return {
            index: BlockSourceHit(kinds=frozenset(kinds), span=self.spans[index])
            for index, kinds in self.kinds.items()
        }


def classify_blocks(docx: Path) -> BlockClassifications:
    """Classify normalized import blocks by reading text and source paragraph span.

    Source ordinals are the stable diagnostic path. Text remains as a fallback for
    legacy/unknown blocks without provenance and for tests that exercise repeated
    text ambiguity explicitly.
    """
    from pancratius.ir.normalize import normalize

    with tempfile.TemporaryDirectory(prefix="docx-inspect-") as td:
        doc = da.adapt(docx, Path(td), [])
        doc = normalize(doc)

    kind_of: dict[str, set[str]] = {}
    by_source = _SourceClassificationBuilder()
    for block in doc.blocks:
        name = _block_kind_name(block)
        for line in _block_lines(block):
            if line:
                kind_of.setdefault(line, set()).add(name)
        span = block.source_span
        if span is None:
            continue
        by_source.add(name=name, span=span)
    return BlockClassifications(
        by_text={line: frozenset(kinds) for line, kinds in kind_of.items()},
        by_source=by_source.build(),
    )


def classify(docx: Path) -> BlockKindsByText:
    """Map normalized reading-line text → possible IR block kinds after import."""
    return classify_blocks(docx).by_text


def classify_source_spans(docx: Path) -> BlockKindsBySource:
    """Map raw source paragraph index → normalized IR block kind/span."""
    return classify_blocks(docx).by_source


# ---------------------------------------------------------------------------
# votability mask: what the production compiler says a source paragraph IS,
# reduced to a conservative "is this votable body, or not, or unsure?" verdict.
# This exists so a downstream dataset stops GUESSING a paragraph's structural
# role and instead defers to the real classifier — defaulting to votable when
# the classifier is silent or mixed, never silently masking an ambiguous case.
# ---------------------------------------------------------------------------

# The IR block kinds that are non-body STRUCTURE: a source ordinal that became
# ONLY these is confidently not a prose/verse candidate (it is a boundary or
# non-body content). Anything not listed here (and not the BODY kinds below) is
# treated as UNKNOWN → votable-with-review rather than silently masked.
_STRUCTURAL_KINDS = frozenset({
    "Heading", "ThematicBreak", "Table", "ListBlock", "Signature",
    "Epigraph", "BlockQuote", "ImageBlock", "DialogueLabel",
})

# The IR block kinds that ARE votable body. At the structural seam the mask observes
# (see `votability_mask`), lineated/verse runs have NOT been merged yet — every body
# line is still a `Paragraph`. `LineatedBlock`/`VerseBlock` are listed for robustness
# so that if the mask is ever observed past the lineation seam they are still treated
# as body (the lineation verdict — the decision the dataset re-judges — never leaks).
_BODY_KINDS = frozenset({"Paragraph", "LineatedBlock", "VerseBlock"})


class MaskVerdict(StrEnum):
    """The conservative votability verdict for one source paragraph.

    `BODY` — the classifier says this ordinal is prose body; it is a votable candidate.
    `CONTEXT` — the classifier says this ordinal is ONLY non-body structure
        (heading, table, dialogue label, …); it is not a candidate.
    `REVIEW` — votable like BODY, but flagged: the classifier was mixed, the kind
        is unknown, the ordinal is unmapped (no entry / no span), or a paragraph
        unexpectedly merges several source ordinals. Never silently masked away.
    """

    BODY = "body"
    CONTEXT = "context"
    REVIEW = "review"


def _verdict_for(hit: BlockSourceHit | None) -> MaskVerdict:
    """Reduce one source ordinal's structural-IR hit to a votability verdict."""
    if hit is None:
        # No block carried this ordinal at the structural seam: dropped (TOC/
        # endmatter/bibliography) or unreconciled (§14-P1). Stay votable, but flag —
        # we cannot faithfully render it against a known block.
        return MaskVerdict.REVIEW
    kinds = hit.kinds
    if not kinds <= (_BODY_KINDS | _STRUCTURAL_KINDS):
        # An IR kind we do not model here. Default to votable, flagged — never
        # mask a paragraph out of voting on an unrecognized kind.
        return MaskVerdict.REVIEW
    if len(kinds) > 1:
        # mixed kinds (e.g. {DialogueLabel, Paragraph}): the <w:p> split into a
        # label + a body fragment. Stay votable, flagged — never collapse to the
        # structural half.
        return MaskVerdict.REVIEW
    (kind,) = tuple(kinds)
    if kind in _STRUCTURAL_KINDS:
        return MaskVerdict.CONTEXT
    # a BODY kind. A *Paragraph* owns one source ordinal; spanning >1 is an
    # unexpected merge (e.g. a dialogue coda fused onto its lead) → flag it.
    if kind == "Paragraph" and hit.span.end != hit.span.start:
        return MaskVerdict.REVIEW
    return MaskVerdict.BODY


def votability_mask(docx: Path) -> dict[int, MaskVerdict]:
    """Per source-paragraph-ordinal votability verdict from the production compiler.

    Runs ``adapt`` then the pass pipeline ONCE — stopping at the structural seam
    (``until=PER_ORDINAL_SEAM``: after dialogue labels, before the span-merging
    passes) — and reduces each source ordinal's resulting IR block kind(s) to a
    conservative ``MaskVerdict``. The caller looks a paragraph up by its
    ``SourceSpan`` start; an ordinal absent from the returned map is unmapped and
    should be treated as ``REVIEW`` (votable, flagged), never silently masked.

    Why the seam, not the full pipeline: the lineation fold merges lineated/verse
    runs into one block, and ``merge_source_spans`` drops that block's provenance if
    any member (e.g. an empty stanza-gap) lacks a span — poisoning provenance for
    whole verse sections and falsely flagging thousands of real body lines.
    Observing before that pass keeps each body line an addressable ``Paragraph`` AND
    avoids surfacing the lineation verdict (it has not been computed yet).
    Structural roles (heading/signature/dialogue-label/…) are already assigned by
    this point.

    ``slug_lookup`` is omitted (as in ``classify_blocks``): it only resolves
    bibliography cross-reference targets, never a block's kind, so verdicts are
    identical to the production import path.
    """
    with tempfile.TemporaryDirectory(prefix="docx-mask-") as td:
        doc = da.adapt(docx, Path(td), [])
        doc = run(doc, Context(lang=""), until=PER_ORDINAL_SEAM)
    blocks = tuple(doc.blocks)

    by_source = _SourceClassificationBuilder()
    for block in blocks:
        span = block.source_span
        if span is not None:
            by_source.add(name=_block_kind_name(block), span=span)
    # `add()` already keys every ordinal a span covers, so `hits` is complete per-ordinal.
    hits = by_source.build()
    return {
        ordinal: _verdict_for(hit)
        for ordinal, hit in hits.items()
    }


def lineation_decisions(docx: Path) -> dict[int, bool]:
    """THE production lineation verdict per source `w:p` ordinal.

    Runs the full import pipeline (``adapt`` → ``normalize``) and reads each
    ordinal's fate: ``True`` when its text lowered inside a lineated/verse block,
    ``False`` when it stayed a body prose paragraph. Non-body structure (headings,
    tables, labels, …), blank paragraphs, and ordinals whose provenance did not
    survive (no source span) are absent — score only on the covered ordinals and
    report the rest as uncovered, never guessed.

    This is the per-line ``prose``/``lineated`` surface the lineation gold set
    (``LineId(lang, book, src_ordinal, sub)``) joins against; every ``sub``
    segment of one ``w:p`` shares the ordinal's verdict.
    """
    from pancratius.ir.normalize import normalize

    with tempfile.TemporaryDirectory(prefix="docx-lineation-") as td:
        doc = da.adapt(docx, Path(td), [])
        doc = normalize(doc)

    from pancratius.ir.normalize import VERSE_SHORT_LINE_MAX, inline_plain

    def hard_break_prose(block: ir.LineatedBlock) -> bool:
        """A block folded ONLY because of an authored `<w:br>` whose lines are
        prose-length: the importer rightly preserves the break for display, but
        as a register the human truth reads such lines as prose, not lineation."""
        e = block.evidence
        if not e.hard_break or e.pandoc_line_block or e.inferred_source_rows or e.compact_callout:
            return False
        return any(
            len(inline_plain(line.inlines)) > VERSE_SHORT_LINE_MAX
            for stanza in block.stanzas
            for line in stanza
        )

    from pancratius.ir.normalize import inline_lines

    def paragraph_verdict(p: ir.Paragraph) -> set[int]:
        """A paragraph's per-line truth: prose, unless it carries authored hard
        breaks with verse-length lines (the same prose-length mirror as
        ``hard_break_prose``)."""
        lines = inline_lines(p.inlines, soft_break=False)
        if len(lines) <= 1:
            return prose
        if any(len(inline_plain(line)) > VERSE_SHORT_LINE_MAX for line in lines):
            return prose
        return lineated

    lineated: set[int] = set()
    prose: set[int] = set()

    def claim(block: ir.Block) -> None:
        span = block.source_span
        if span is None:
            return
        target: set[int] | None = None
        if isinstance(block, ir.LineatedBlock):
            if block.register is ir.Register.VERSE:
                target = lineated
            else:
                target = prose if hard_break_prose(block) else lineated
        elif isinstance(block, ir.Paragraph) and not block.empty:
            target = paragraph_verdict(block)
        elif isinstance(block, ir.QuoteBlock) and block.register in {
            ir.Register.SCRIPTURE, ir.Register.INSET,
        }:
            # The display-register pass wraps source paragraphs; their per-line
            # lineation truth must keep its coverage (the lineation gold set
            # joins on these ordinals).
            for member in block.blocks:
                claim(member)
            return
        if target is not None:
            target.update(range(span.start, span.end + 1))

    for block in doc.blocks:
        claim(block)
    # An ordinal claimed by both kinds is ambiguous: drop it rather than guess.
    return {
        **dict.fromkeys(lineated - prose, True),
        **dict.fromkeys(prose - lineated, False),
    }


def _kind_label(kinds: frozenset[str]) -> str:
    if not kinds:
        return "Paragraph?"
    if len(kinds) == 1:
        return next(iter(kinds))
    return "Ambiguous[" + "|".join(sorted(kinds)) + "]"


def _row_may_be_kind(row: ParaRow, kind: str) -> bool:
    return row.block_kind == kind or (
        row.block_kind.startswith("Ambiguous[") and kind in row.block_kind
    )


def annotate(rows: list[ParaRow], docx: Path) -> None:
    classifications = classify_blocks(docx)
    for row in rows:
        if source_hit := classifications.by_source.get(row.index):
            row.block_kind = _kind_label(source_hit.kinds)
            row.block_source_span = source_hit.span
            continue
        if row.empty:
            row.block_kind = "—"
            continue
        # A paragraph with a hard break contributes several lines; key on the first.
        first = _norm(row.text.split("\n", 1)[0])
        candidates = set(classifications.by_text.get(first, ()))
        candidates.update(classifications.by_text.get(_norm(row.text), ()))
        row.block_kind = _kind_label(frozenset(candidates))


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _flags(row: ParaRow) -> str:
    out: list[str] = []
    if row.block_source_span is not None and (
        row.block_source_span.start != row.index or row.block_source_span.end != row.index
    ):
        out.append(f"ir={row.block_source_span.start}..{row.block_source_span.end}")
    if row.align:
        out.append(f"jc={row.align}")
    if row.contextual:
        out.append("ctxSp")
    if row.indent:
        fl = row.indent.get("firstLine")
        hg = row.indent.get("hanging")
        lf = row.indent.get("left") or row.indent.get("start")
        bits = []
        if fl:
            bits.append(f"first{fl}")
        if hg:
            bits.append(f"hang{hg}")
        if lf:
            bits.append(f"left{lf}")
        out.append("ind:" + ",".join(bits) if bits else "ind")
    if row.br_count:
        out.append(f"br×{row.br_count}")
    if row.numbered:
        out.append("list")
    if row.border:
        out.append(f"bdr:{row.border}")
    if row.heading:
        out.append("H")
    if row.thematic:
        out.append("***")
    before = row.spacing.get("before")
    after = row.spacing.get("after")
    if before and before != "0":
        out.append(f"sb{before}")
    if after and after != "0":
        out.append(f"sa{after}")
    return " ".join(out)


_KIND_MARK = {
    "LineatedBlock": "LINE ",
    "VerseBlock": "VERSE",
    "Signature": "SIGN ",
    "Epigraph": "EPIG ",
    "DialogueLabel": "DLG  ",
    "Heading": "HEAD ",
    "ThematicBreak": "HR   ",
    "Paragraph": "prose",
    "Paragraph?": "prose",
    "—": "—    ",
}


def render(rows: list[ParaRow], *, width: int = 58) -> str:
    lines: list[str] = []
    header = f"{'idx':>4}  {'kind':<5}  {'lg':>3}  {'style':<14}  signals"
    lines.append(header)
    lines.append("-" * len(header))
    for row in rows:
        mark = (
            "AMBIG"
            if row.block_kind.startswith("Ambiguous[")
            else _KIND_MARK.get(row.block_kind, row.block_kind[:5])
        )
        lg = str(row.lineation_group) if row.lineation_group is not None else "·"
        preview = re.sub(r"\s+", " ", row.text)[:width] or "∅"
        style = (row.style or "Normal")[:14]
        lines.append(f"{row.index:>4}  {mark:<5}  {lg:>3}  {style:<14}  {_flags(row)}")
        lines.append(f"        “{preview}”")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# inspection API
# ---------------------------------------------------------------------------


def resolve_book_docx(number: int, *, lang: str = "ru", content_root: Path | None = None) -> Path:
    root = Path(__file__).resolve().parents[1]
    books_root = content_root / "books" if content_root is not None else root / "src" / "content" / "books"
    matches = sorted(books_root.glob(f"{number:02d}-*"))
    if not matches:
        raise DocxInspectError(f"no book folder for #{number}")
    docx = matches[0] / f"{lang}.docx"
    if not docx.is_file():
        raise DocxInspectError(f"no {lang}.docx in {matches[0].name}")
    return docx


def parse_index_range(raw: str | None) -> tuple[int, int] | None:
    if raw is None:
        return None
    pieces = raw.split(":")
    if len(pieces) != 2 or not pieces[0] or not pieces[1]:
        raise DocxInspectError("--range must be shaped as LO:HI")
    try:
        lo, hi = (int(piece) for piece in pieces)
    except ValueError as exc:
        raise DocxInspectError("--range bounds must be integers") from exc
    if lo < 0 or hi < lo:
        raise DocxInspectError("--range must be shaped as LO:HI with 0 <= LO <= HI")
    return lo, hi


def select_rows(rows: list[ParaRow], options: InspectOptions) -> list[ParaRow]:
    if options.around is not None:
        hits = [r.index for r in rows if options.around in r.text]
        if not hits:
            raise DocxInspectError(f"no paragraph contains {options.around!r}")
        keep: set[int] = set()
        for h in hits:
            keep.update(
                range(max(0, h - options.context), min(len(rows), h + options.context + 1))
            )
        return [r for r in rows if r.index in keep]
    if options.contains is not None:
        return [r for r in rows if options.contains in r.text]
    if options.verse_only:
        return [r for r in rows if _row_may_be_kind(r, "VerseBlock")]
    if options.lineated_only:
        return [r for r in rows if _row_may_be_kind(r, "LineatedBlock")]
    if options.index_range:
        lo, hi = options.index_range
        return [r for r in rows if lo <= r.index <= hi]
    return rows


def inspect_docx(docx: Path, options: InspectOptions | None = None) -> InspectResult:
    options = options or InspectOptions()
    if docx.suffix.lower() != ".docx":
        raise DocxInspectError(f"expected a .docx file, got {docx}")
    if not docx.is_file():
        raise DocxInspectError(f"DOCX not found: {docx}")
    try:
        rows = read_rows(docx)
        annotate(rows, docx)
    except zipfile.BadZipFile as exc:
        raise DocxInspectError(f"{docx} is not a valid ZIP/DOCX package") from exc
    except KeyError as exc:
        raise DocxInspectError(f"{docx} is missing required DOCX part: {exc}") from exc
    except ET.ParseError as exc:
        raise DocxInspectError(f"{docx} contains malformed DOCX XML: {exc}") from exc
    except RuntimeError as exc:
        raise DocxInspectError(exc) from exc
    except FileNotFoundError as exc:
        if exc.filename == "pandoc":
            raise DocxInspectError(
                "pandoc not found on PATH; install with `brew install pandoc`."
            ) from exc
        raise
    selected = select_rows(rows, options)
    return InspectResult(docx=docx, rows=tuple(rows), selected=tuple(selected))


def render_inspection(result: InspectResult) -> str:
    lines = [
        f"# {result.docx}  ({len(result.rows)} body paragraphs, {len(result.selected)} shown)",
        (
            f"# verse-register paragraphs: {result.verse_paragraphs}   "
            f"lineated-prose paragraphs: {result.lineated_paragraphs}   "
            f"visual lineation-groups: {result.lineation_groups}   "
            f"ambiguous text matches: {result.ambiguous_paragraphs}"
        ),
        render(list(result.selected)),
    ]
    return "\n".join(lines)
