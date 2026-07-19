# import-pure: no filesystem mutation
"""Read-only DOCX → IR fidelity inspector (a diagnostic, never writes src/content).

This is the inspector the ``docx_adapter`` debugging note calls for: it prints,
per source body paragraph, the OOXML signals that verse / signature / epigraph
detection consumes — resolved style, ``w:contextualSpacing``, spacing attrs,
``w:jc`` alignment, ``w:ind`` indent, ``w:numPr`` list, ``w:pBdr`` border, the
hard ``<w:br/>`` count, and the assigned visual ``lineation_group`` — beside the
IR block the paragraph actually became after the full ``adapt`` → pass
pipeline. A human can then inspect Q1 and Q2 separately: whether source rows
were folded into a ``LineatedBlock``, and which display register that block
received.

It projects ``pancratius.docx_source`` so the signals shown are the same domain
facts the importer reads — there is no diagnostic-side OOXML interpretation to
drift from the converter.

PURE: opens the DOCX zip for READ only and runs the pure import passes into a
scratch media dir. It mutates nothing under ``src/content``.

Run it:

    uv run pancratius docx inspect <docx> --contains "Память кого"
    uv run pancratius docx inspect book:13 --around "Память кого" --context 8
    uv run pancratius docx inspect book:13 --verse-only
"""
from __future__ import annotations

import re
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, assert_never

from pancratius import docx_adapter as da
from pancratius import docx_source, docx_structure, ir
from pancratius.ir.inlines import inline_plain
from pancratius.locales import DEFAULT_LOCALE, Locale
from pancratius.passes.pipeline import (
    Context,
    LineationCorrections,
    ScripturePins,
    run,
)

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
    br_count: int         # hard <w:br/>/<w:cr/> LINE breaks (authored lineation; page and
                          # column breaks are pagination, not lineation — excluded)
    empty: bool
    disposition: docx_source.ParagraphDisposition
    lineation_group: int | None = None
    block_kind: str = "?"  # the IR block this paragraph's text landed in
    block_source_span: ir.SourceSpan | None = None
    block_registers: frozenset[ir.Register] = frozenset()
    page_break_before: bool = False  # w:pPr/w:pageBreakBefore — starts a new page
    page_break_inline: bool = False  # a run-level <w:br w:type="page"/> inside the paragraph
    column_break_inline: bool = False


class DocxInspectError(ValueError):
    """The requested DOCX inspection cannot be completed from the given input."""


@dataclass(frozen=True, slots=True)
class ParagraphIndexRange:
    lo: int
    hi: int

    def __post_init__(self) -> None:
        if self.lo < 0 or self.hi < self.lo:
            raise DocxInspectError("--range must be shaped as LO:HI with 0 <= LO <= HI")


@dataclass(frozen=True, slots=True)
class InspectAll:
    pass


@dataclass(frozen=True, slots=True)
class InspectContains:
    text: str


@dataclass(frozen=True, slots=True)
class InspectAround:
    text: str


@dataclass(frozen=True, slots=True)
class InspectRange:
    index_range: ParagraphIndexRange


@dataclass(frozen=True, slots=True)
class InspectVerseOnly:
    pass


@dataclass(frozen=True, slots=True)
class InspectLineatedOnly:
    pass


type InspectFilter = (
    InspectAll
    | InspectContains
    | InspectAround
    | InspectRange
    | InspectVerseOnly
    | InspectLineatedOnly
)


@dataclass(frozen=True)
class InspectOptions:
    filter: InspectFilter = InspectAll()
    context: int = 6

    @classmethod
    def from_cli(
        cls,
        *,
        contains: str | None = None,
        around: str | None = None,
        context: int = 6,
        index_range: ParagraphIndexRange | None = None,
        verse_only: bool = False,
        lineated_only: bool = False,
    ) -> InspectOptions:
        filters = [
            InspectContains(contains) if contains is not None else None,
            InspectAround(around) if around is not None else None,
            InspectRange(index_range) if index_range is not None else None,
            InspectVerseOnly() if verse_only else None,
            InspectLineatedOnly() if lineated_only else None,
        ]
        selected = [filter_ for filter_ in filters if filter_ is not None]
        if len(selected) > 1:
            raise DocxInspectError(
                "choose only one inspect filter: --contains, --around, --range, "
                "--verse-only, or --lineated-only"
            )
        return cls(filter=selected[0] if selected else InspectAll(), context=context)

    def __post_init__(self) -> None:
        if self.context < 0:
            raise DocxInspectError("--context must be non-negative")


@dataclass(frozen=True)
class InspectResult:
    docx: Path
    rows: tuple[ParaRow, ...]
    selected: tuple[ParaRow, ...]
    verse_blocks: int = 0

    @property
    def lineated_paragraphs(self) -> int:
        return sum(1 for row in self.rows if row.block_kind == "LineatedBlock")

    @property
    def lineation_groups(self) -> int:
        return len({row.lineation_group for row in self.rows if row.lineation_group is not None})

    @property
    def ambiguous_paragraphs(self) -> int:
        return sum(1 for row in self.rows if row.block_kind.startswith("Ambiguous["))


type InspectBlockKind = Literal[
    "BlockQuote",
    "DialogueLabel",
    "Epigraph",
    "Heading",
    "ImageBlock",
    "LineatedBlock",
    "ListBlock",
    "Paragraph",
    "Signature",
    "Table",
    "ThematicBreak",
]


def read_rows(source: docx_source.DocxSourceDocument) -> list[ParaRow]:
    """Diagnostic rows projected from the canonical DOCX source aggregate."""
    return [
        ParaRow(
            index=int(paragraph.ordinal),
            text=paragraph.text,
            style=paragraph.resolved_style,
            direct_style=paragraph.direct_style,
            align=paragraph.alignment.value,
            contextual=paragraph.contextual_spacing,
            spacing=dict(paragraph.spacing),
            indent=dict(paragraph.indent),
            numbered=paragraph.numbered,
            border=paragraph.border.value,
            heading=paragraph.heading,
            thematic=paragraph.thematic,
            br_count=paragraph.content.breaks.count(docx_source.BreakKind.LINE),
            empty=paragraph.empty,
            disposition=paragraph.disposition,
            lineation_group=(
                paragraph.visual_group.value if paragraph.visual_group is not None else None
            ),
            page_break_before=paragraph.page_break_before,
            page_break_inline=docx_source.BreakKind.PAGE in paragraph.content.breaks,
            column_break_inline=docx_source.BreakKind.COLUMN in paragraph.content.breaks,
        )
        for paragraph in source.paragraphs
    ]


# ---------------------------------------------------------------------------
# IR classification: what block each paragraph's reading text became
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub("", text)).strip()


def _block_lines(block: ir.Block) -> list[str]:
    """The normalized reading lines a block contributes, for membership lookup."""
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


type BlockKindsByText = dict[str, frozenset[docx_structure.CompilerBlockKind]]


BlockSourceHit = docx_structure.SourceBlockHit


type BlockKindsBySource = dict[int, BlockSourceHit]


@dataclass(frozen=True)
class BlockClassifications:
    by_text: BlockKindsByText
    by_source: BlockKindsBySource

    @property
    def verse_block_paths(self) -> frozenset[tuple[int, ...]]:
        return frozenset(
            claim.path
            for hit in self.by_source.values()
            for claim in hit.claims
            if claim.is_verse
        )


def classify_blocks(source: docx_source.DocxSourceDocument) -> BlockClassifications:
    """Classify normalized import blocks by reading text and source paragraph span.

    Source ordinals are the stable diagnostic path. Text remains as a fallback for
    legacy/unknown blocks without provenance and for tests that exercise repeated
    text ambiguity explicitly.
    """
    from pancratius.lineation_overrides import load_overrides
    from pancratius.scripture_overrides import load_overrides as load_scripture_pins

    with tempfile.TemporaryDirectory(prefix="docx-inspect-") as td:
        doc = da.adapt(source, Path(td), [])
        # rules-only: no register model (see fold_decisions)
        doc = run(doc, Context(
            lang=DEFAULT_LOCALE,
            lineation=LineationCorrections(load_overrides(source)),
            scripture=ScripturePins(load_scripture_pins(source)),
        ))

    kind_of: dict[str, set[docx_structure.CompilerBlockKind]] = {}
    for block in doc.blocks:
        name = docx_structure.compiler_block_kind(block)
        for line in _block_lines(block):
            if line:
                kind_of.setdefault(line, set()).add(name)
    return BlockClassifications(
        by_text={line: frozenset(kinds) for line, kinds in kind_of.items()},
        by_source=docx_structure.source_block_hits(doc.blocks, source.semantic_ordinals),
    )


def classify(docx: Path) -> BlockKindsByText:
    """Map normalized reading-line text → possible IR block kinds after import."""
    return classify_blocks(docx_source.read(docx)).by_text


def classify_source_spans(docx: Path) -> BlockKindsBySource:
    """Map raw source paragraph index → normalized IR block kind/span."""
    return classify_blocks(docx_source.read(docx)).by_source


# Structural and lineation observations live in `docx_structure`; this module only
# renders them for diagnostics.


def _kind_label(kinds: frozenset[docx_structure.CompilerBlockKind]) -> str:
    if not kinds:
        return "Paragraph?"
    if len(kinds) == 1:
        return next(iter(kinds)).value
    return "Ambiguous[" + "|".join(sorted(kind.value for kind in kinds)) + "]"


def _row_may_be_kind(row: ParaRow, kind: InspectBlockKind) -> bool:
    return row.block_kind == kind or (
        row.block_kind.startswith("Ambiguous[") and kind in row.block_kind
    )


def annotate(
    rows: list[ParaRow], source: docx_source.DocxSourceDocument
) -> BlockClassifications:
    classifications = classify_blocks(source)
    for row in rows:
        if source_hit := classifications.by_source.get(row.index):
            row.block_kind = _kind_label(source_hit.kinds)
            row.block_source_span = source_hit.span
            row.block_registers = frozenset(
                claim.register for claim in source_hit.claims if claim.register is not None
            )
            continue
        if row.empty:
            row.block_kind = "—"
            continue
        # A paragraph with a hard break contributes several lines; key on the first.
        first = _norm(row.text.split("\n", 1)[0])
        candidates = set(classifications.by_text.get(first, ()))
        candidates.update(classifications.by_text.get(_norm(row.text), ()))
        row.block_kind = _kind_label(frozenset(candidates))
        # Text fallback can recover only a block kind. Never guess Q2 without a source claim.
    return classifications


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
    if row.disposition is docx_source.ParagraphDisposition.PAGINATION_ONLY:
        out.append("pagination")
    if row.page_break_before:
        out.append("pageBefore")
    if row.page_break_inline:
        out.append("pageBr")
    if row.column_break_inline:
        out.append("colBr")
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
    "Signature": "SIGN ",
    "Epigraph": "EPIG ",
    "DialogueLabel": "DLG  ",
    "Heading": "HEAD ",
    "ThematicBreak": "HR   ",
    "Paragraph": "prose",
    "Paragraph?": "prose",
    "—": "—    ",
}


def _kind_mark(row: ParaRow) -> str:
    if row.block_kind.startswith("Ambiguous["):
        return "AMBIG"
    if row.block_kind == "LineatedBlock" and ir.Register.VERSE in row.block_registers:
        return "VERSE"
    return _KIND_MARK.get(row.block_kind, row.block_kind[:5])


def render(rows: list[ParaRow], *, width: int = 58) -> str:
    lines: list[str] = []
    header = f"{'idx':>4}  {'kind':<5}  {'lg':>3}  {'style':<14}  signals"
    lines.append(header)
    lines.append("-" * len(header))
    for row in rows:
        mark = _kind_mark(row)
        lg = str(row.lineation_group) if row.lineation_group is not None else "·"
        preview = re.sub(r"\s+", " ", row.text)[:width] or "∅"
        style = (row.style or "Normal")[:14]
        lines.append(f"{row.index:>4}  {mark:<5}  {lg:>3}  {style:<14}  {_flags(row)}")
        lines.append(f"        “{preview}”")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# inspection API
# ---------------------------------------------------------------------------


def resolve_book_docx(
    number: int,
    *,
    lang: Locale = DEFAULT_LOCALE,
    content_root: Path | None = None,
) -> Path:
    root = Path(__file__).resolve().parents[1]
    books_root = content_root / "books" if content_root is not None else root / "src" / "content" / "books"
    matches = sorted(books_root.glob(f"{number:02d}-*"))
    if not matches:
        raise DocxInspectError(f"no book folder for #{number}")
    docx = matches[0] / f"{lang}.docx"
    if not docx.is_file():
        raise DocxInspectError(f"no {lang}.docx in {matches[0].name}")
    return docx


def parse_index_range(raw: str | None) -> ParagraphIndexRange | None:
    if raw is None:
        return None
    pieces = raw.split(":")
    if len(pieces) != 2 or not pieces[0] or not pieces[1]:
        raise DocxInspectError("--range must be shaped as LO:HI")
    try:
        lo, hi = (int(piece) for piece in pieces)
    except ValueError as exc:
        raise DocxInspectError("--range bounds must be integers") from exc
    return ParagraphIndexRange(lo=lo, hi=hi)


def select_rows(rows: list[ParaRow], options: InspectOptions) -> list[ParaRow]:
    match options.filter:
        case InspectAround(text):
            hits = [r.index for r in rows if text in r.text]
            if not hits:
                raise DocxInspectError(f"no paragraph contains {text!r}")
            keep: set[int] = set()
            for h in hits:
                keep.update(
                    range(max(0, h - options.context), min(len(rows), h + options.context + 1))
                )
            return [r for r in rows if r.index in keep]
        case InspectContains(text):
            return [r for r in rows if text in r.text]
        case InspectVerseOnly():
            return [r for r in rows if ir.Register.VERSE in r.block_registers]
        case InspectLineatedOnly():
            return [r for r in rows if _row_may_be_kind(r, "LineatedBlock")]
        case InspectRange(index_range):
            return [r for r in rows if index_range.lo <= r.index <= index_range.hi]
        case InspectAll():
            return rows
        case _:
            assert_never(options.filter)


def inspect_docx(docx: Path, options: InspectOptions | None = None) -> InspectResult:
    options = options or InspectOptions()
    if docx.suffix.lower() != ".docx":
        raise DocxInspectError(f"expected a .docx file, got {docx}")
    if not docx.is_file():
        raise DocxInspectError(f"DOCX not found: {docx}")
    try:
        source = docx_source.read(docx)
        rows = read_rows(source)
        classifications = annotate(rows, source)
    except zipfile.BadZipFile as exc:
        raise DocxInspectError(f"{docx} is not a valid ZIP/DOCX package") from exc
    except KeyError as exc:
        raise DocxInspectError(f"{docx} is missing required DOCX part: {exc}") from exc
    except ET.ParseError as exc:
        raise DocxInspectError(f"{docx} contains malformed DOCX XML: {exc}") from exc
    except docx_source.DocxSourceError as exc:
        raise DocxInspectError(str(exc)) from exc
    except RuntimeError as exc:
        raise DocxInspectError(exc) from exc
    except FileNotFoundError as exc:
        if exc.filename == "pandoc":
            raise DocxInspectError(
                "pandoc not found on PATH; install with `brew install pandoc`."
            ) from exc
        raise
    selected = select_rows(rows, options)
    return InspectResult(
        docx=docx,
        rows=tuple(rows),
        selected=tuple(selected),
        verse_blocks=len(classifications.verse_block_paths),
    )


def render_inspection(result: InspectResult) -> str:
    lines = [
        f"# {result.docx}  ({len(result.rows)} body paragraphs, {len(result.selected)} shown)",
        (
            f"# verse-register blocks: {result.verse_blocks}   "
            f"lineated source paragraphs: {result.lineated_paragraphs}   "
            f"visual lineation-groups: {result.lineation_groups}   "
            f"ambiguous text matches: {result.ambiguous_paragraphs}"
        ),
        render(list(result.selected)),
    ]
    return "\n".join(lines)
