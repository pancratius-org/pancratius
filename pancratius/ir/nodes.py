# import-pure: no filesystem mutation
"""The block/inline intermediate representation for the import pipeline.

This is the semantic boundary from `docs/import-pipeline.md` ("The block IR"):
after the DOCX adapter, nothing is DOCX-shaped — it is blocks, inlines, footnote
definitions, and assets; diagnostics flow through the pass-context sink, not the
document. Lowering turns this typed structure into
canonical Markdown exactly once; no Markdown string exists before then.

The model is deliberately minimal: only the block and inline kinds the Pancratius
canonical Markdown body actually needs, plus explicit `UnknownBlock`/`UnknownInline`
escape hatches for anything the adapter does not recognize. Do not smuggle
structure through string conventions — add a typed kind instead.

This module is PURE (no filesystem access); the marker above keys the PAN018
writer-only-mutation contract.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Literal, assert_never

# The emphasis kinds the IR models. Exported so the adapter (mapping Pandoc node
# tags to it) and the lowering (mapping it to Markdown/HTML) share ONE source of
# truth for the closed set, instead of each re-spelling the string literals.
EmphKind = Literal["strong", "emph", "strike", "sup", "sub"]
QuoteKind = Literal["single", "double"]
# Paragraph border gesture (OOXML `w:pBdr`), reduced to the two editorially
# meaningful kinds: a full four-side box ("box" — quoted/framed canonical text)
# and a left-rule bar ("rule" — a set-apart inset passage). Any other side
# combination is "other"; no border is "".
BorderKind = Literal["", "box", "rule", "other"]
# Open JSON-ish records at the IR boundary. Pandoc table raw nodes stay opaque;
# bibliography entries are structured enough to name, but intentionally open-ended.
type JsonObject = dict[str, object]
type BibliographyEntry = dict[str, object]


@dataclass(frozen=True)
class SourceSpan:
    """Inclusive top-level source paragraph ordinals.

    This is provenance, not semantics: it says which DOCX body ``w:p`` rows a block
    came from, so diagnostics can render or inspect the original source slice. It
    is optional on blocks because hand-built IR, future non-DOCX adapters, and
    genuinely synthetic nodes must not fake provenance they cannot prove.
    """

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"invalid SourceSpan({self.start}, {self.end})")


def merge_source_spans(spans: Iterable[SourceSpan | None]) -> SourceSpan | None:
    """Return the smallest span covering a complete span set, or ``None``.

    Normalization passes use this when they merge or wrap source-derived blocks.
    Missing spans poison the merge: a composite block may carry provenance only when
    every source piece can prove provenance. Returning a partial span would make
    diagnostics render too narrow a source slice.
    """
    collected = list(spans)
    if not collected or any(span is None for span in collected):
        return None
    present = [span for span in collected if span is not None]
    return SourceSpan(
        start=min(span.start for span in present),
        end=max(span.end for span in present),
    )

# ---------------------------------------------------------------------------
# Inline kinds
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Text:
    """A literal text run."""

    value: str


@dataclass(frozen=True)
class Emphasis:
    """An emphasis span. `underline`/`smallcaps` are unwrapped at the adapter
    (the GFM engine drops them too), so they never reach this kind set."""

    kind: EmphKind
    children: list[Inline]


@dataclass(frozen=True)
class Code:
    """Inline code."""

    value: str


@dataclass(frozen=True)
class Link:
    """A hyperlink: label inlines + target URL."""

    children: list[Inline]
    target: str


@dataclass(frozen=True)
class ImageInline:
    """An inline image. `asset_id` (the content-hash `<hash>.<ext>` filename) is
    assigned during the lowering asset pass; `src` is the adapter-extracted path
    used to resolve and hash the bytes until then."""

    src: str
    alt: str
    asset_id: str | None = None


@dataclass(frozen=True)
class Quoted:
    """A typographically quoted span (Pandoc `Quoted`)."""

    kind: QuoteKind
    children: list[Inline]


@dataclass(frozen=True)
class FootnoteRef:
    """A footnote reference. `id` is the dense 1..N id assigned by the adapter in
    reference order; `raw_index` is the adapter's running index (kept for
    diagnostics / stability)."""

    raw_index: int
    id: int | None = None


@dataclass(frozen=True)
class LineBreak:
    """A hard in-run line break (Word `w:br`). Verse-significant: it separates
    display lines inside one paragraph."""


@dataclass(frozen=True)
class SoftBreak:
    """A soft break Pandoc emits for wrapped source. Collapses to a space in
    prose; still a display-line boundary inside verse."""


@dataclass(frozen=True)
class DirectionalSpan:
    """A bidi span carrying an explicit writing direction (`dir="rtl"`/`"ltr"`),
    lowered to `<span dir="…">…</span>`.

    Pandoc emits `Span` with a `dir` attribute for Hebrew/Arabic runs whose visual
    ordering depends on the direction (scripture-heavy book62). The adapter unwraps
    other `Span` attributes (production flattens them), but the direction is
    reading-significant — flattening it reverses mixed RTL/LTR ordering — so it is
    modelled as a typed kind rather than silently dropped (the design's "add a
    typed kind instead of flattening")."""

    direction: str
    children: list[Inline]


@dataclass(frozen=True)
class UnknownInline:
    """An inline kind the adapter does not model. `children` preserves any nested
    inlines so no reading content is lost; `note` records the source kind."""

    note: str
    children: list[Inline] = field(default_factory=list)


type Inline = (
    Text | Emphasis | Code | Link | ImageInline | Quoted | FootnoteRef
    | LineBreak | SoftBreak | DirectionalSpan | UnknownInline
)


# Shared lineated-block shape: stanzas -> display lines -> inline content.
@dataclass(frozen=True)
class Line:
    """One display line of a lineated run. `span` is the source ``w:p``
    ordinal(s) the line came from; lines split from one hard-break paragraph
    share that paragraph's span."""

    inlines: list[Inline]
    span: SourceSpan | None = None


type Stanza = list[Line]
type LineatedStanzas = list[Stanza]


@dataclass(frozen=True)
class LineationEvidence:
    """Q1 provenance for a structural lineated run.

    This records why the importer decided source rows should lower as line breaks.
    It deliberately excludes register cues such as title text, headings, anaphora,
    and visual style. Lowering ignores it; Q2 register promotion may consult it as
    lineation provenance, but it must not encode "almost verse" state here.
    """

    pandoc_line_block: bool = False
    hard_break: bool = False
    inferred_source_rows: bool = False
    stanza_break: bool = False
    compact_callout: bool = False

# Container inline kinds (those nesting a `children` list), in two forms: the union
# types a known container; the tuple is `isinstance`'s 2nd arg (a `type` alias can't
# be). `test_container_forms_in_sync` keeps them aligned.
type ContainerInlineNode = Emphasis | Link | Quoted | DirectionalSpan | UnknownInline
ContainerInline = (Emphasis, Link, Quoted, DirectionalSpan, UnknownInline)


def is_container_inline(node: Inline) -> bool:
    """True when `node` nests a `children` inline list (Emphasis/Link/Quoted/
    DirectionalSpan/UnknownInline) — the kinds a recursive inline pass descends into."""
    return isinstance(node, ContainerInline)


def rebuild_container(node: ContainerInlineNode, children: list[Inline]) -> Inline:
    """Return a copy of a container inline with its `children` replaced, preserving
    the kind's other fields. The ONE place the container shapes are reconstructed,
    so a recursive inline pass (AI-alt scrub, empty-emphasis drop, asset
    assignment) maps children without re-spelling the per-kind constructors.

    The `match` is exhaustive over `ContainerInlineNode`: adding a container kind
    makes the type checker flag the `assert_never` until a new arm is added."""
    match node:
        case Emphasis():
            return Emphasis(node.kind, children)
        case Link():
            return Link(children, node.target)
        case Quoted():
            return Quoted(node.kind, children)
        case DirectionalSpan():
            return DirectionalSpan(node.direction, children)
        case UnknownInline():
            return UnknownInline(node.note, children)
    assert_never(node)


# ---------------------------------------------------------------------------
# Block kinds
# ---------------------------------------------------------------------------


class Register(StrEnum):
    """The open display-register axis on the run-bearing substrates
    (`LineatedBlock`/`QuoteBlock`); substrates stay the closed ADT. Lowering
    dispatches on it through total mapping tables, never on node class."""

    ORDINARY = "ordinary"
    VERSE = "verse"
    SCRIPTURE = "scripture"
    INSET = "inset"
    VOICE = "voice"


@dataclass(frozen=True)
class Heading:
    """A section heading at `level` (1..6)."""

    level: int
    inlines: list[Inline]
    source_span: SourceSpan | None = None


@dataclass(frozen=True)
class SourceFacts:
    """The OOXML facts the frontend (extraction + reconciliation) records on a
    paragraph; read-only afterwards.

    `align` is the OOXML `w:jc` alignment reconciled by the adapter (`""` for the
    default); it drives signature/epigraph detection. `lineation_group` is a
    read-only DOCX visual-continuity group: adjacent Word paragraphs whose
    `w:contextualSpacing` suppresses same-style paragraph spacing share the same
    id. It may bound source-row inference, but it is not itself lineation truth or
    verse-register evidence. `empty` marks a Word empty paragraph — meaningful as
    a stanza break, so it is captured in the IR before any Markdown output could
    lose it. `indented` records a source indent DEPARTING from the book-dominant
    paragraph shape (within-book directioned, not the raw presence of `w:ind`):
    such short paragraphs are usually running prose, while undeparting compact
    callouts can be source lineation. `italic` records that every text-bearing
    run carried italic (an epigraph signal). `border` is the paragraph's
    `w:pBdr` gesture kind — display-register evidence (scripture box / inset
    rule), meaningful only against the book's own border baseline.
    """

    align: str = ""
    empty: bool = False
    italic: bool = False
    indented: bool = False
    border: BorderKind = ""
    lineation_group: int | None = None


@dataclass(frozen=True)
class Paragraph:
    """A body paragraph: inlines plus the frontend-written `SourceFacts`.

    The read-only properties below delegate to `facts` so readers stay
    construction-agnostic."""

    inlines: list[Inline]
    facts: SourceFacts = field(default_factory=SourceFacts)
    source_span: SourceSpan | None = None

    @property
    def align(self) -> str:
        return self.facts.align

    @property
    def empty(self) -> bool:
        return self.facts.empty

    @property
    def italic(self) -> bool:
        return self.facts.italic

    @property
    def indented(self) -> bool:
        return self.facts.indented

    @property
    def border(self) -> BorderKind:
        return self.facts.border

    @property
    def lineation_group(self) -> int | None:
        return self.facts.lineation_group


@dataclass(frozen=True)
class LineatedBlock:
    """A lineated run: stanzas of display lines, each line a list of inlines.

    A `***` stanza separator is a one-line stanza whose single line is
    `[Text("***")]`. It lowers to a `<div class="lineated …">` wrapper whose
    body uses Markdown hard breaks (two trailing spaces) and blank-line stanza
    separators; `register` selects the wrapper class through
    `lower.LINEATED_CLASS`. `evidence` is the run's Q1 lineation provenance —
    register flips carry it unchanged.
    """

    stanzas: LineatedStanzas
    register: Register = Register.ORDINARY
    evidence: LineationEvidence = field(default_factory=LineationEvidence)
    source_span: SourceSpan | None = None


@dataclass(frozen=True)
class Signature:
    """A right-aligned authorial signature block (`<p class="signature">`)."""

    lines: list[str]
    source_span: SourceSpan | None = None


@dataclass(frozen=True)
class Epigraph:
    """A right-aligned epigraph: a quote plus an attribution footer
    (`<blockquote class="epigraph">`)."""

    quote: list[str]
    footer: list[str]
    source_span: SourceSpan | None = None


@dataclass(frozen=True)
class DialogueLabel:
    """A canonicalized speaker label (lowered as `**Speaker:**`)."""

    speaker: str
    source_span: SourceSpan | None = None


@dataclass(frozen=True)
class ThematicBreak:
    """A `***` thematic break."""

    source_span: SourceSpan | None = None


@dataclass(frozen=True)
class QuoteBlock:
    """A set-apart run of blocks. `register` selects the emission through
    `lower.QUOTE_LOWERING`: `ORDINARY` is the Pandoc-born Word-Quote-style quote
    (plain `>` lowering), `SCRIPTURE`/`INSET` are the bordered registers."""

    blocks: list[Block]
    register: Register = Register.ORDINARY
    source_span: SourceSpan | None = None


@dataclass(frozen=True)
class ListBlock:
    """An ordered or bullet list; each item is its own block list. `start` is the
    first ordinal of an ordered list (preserved from the source: Pandoc may split
    one authored list into chunks that resume at 4, 6, … — keeping `start` means
    the lowered Markdown reproduces those ordinals rather than renumbering)."""

    ordered: bool
    items: list[list[Block]]
    start: int = 1
    source_span: SourceSpan | None = None


@dataclass(frozen=True)
class CodeBlock:
    """A fenced code block."""

    text: str
    source_span: SourceSpan | None = None


@dataclass(frozen=True)
class Table:
    """A table. `rows` is structured cell content — rows of cells, each cell a list
    of inlines — so reading-content tables flow through the same AI-alt scrub and
    asset-rewrite passes as body prose before lowering to a GFM pipe table. `raw`
    keeps the opaque source node (the Pandoc Table JSON object, or `None`) for the
    bibliography classifier (it needs the hrefs and image alts the flattened cells
    would drop); it is typed `JsonObject | None` — a JSON object whose schema the
    pure IR makes no claim about, not the bare `object` it was — so the
    classifier still narrows it itself. The classifier lifts a bibliography table
    in the same pass that recognizes it, so no verdict is stored here."""

    rows: list[list[list[Inline]]]
    raw: JsonObject | None = None
    source_span: SourceSpan | None = None


@dataclass(frozen=True)
class ImageBlock:
    """A block-level image."""

    src: str
    alt: str
    asset_id: str | None = None
    source_span: SourceSpan | None = None


@dataclass(frozen=True)
class UnknownBlock:
    """A block kind the adapter does not model. `note` records the source kind;
    `text` carries the block's best-effort plain reading text so lowering can
    PRESERVE it rather than silently dropping the block (the design's "unknown →
    preserve content / emit a diagnostic"). `text` is `""` for kinds that genuinely
    carry no reading content (e.g. `Null`); the block's presence is surfaced as a
    diagnostic at lowering regardless."""

    note: str
    text: str = ""
    source_span: SourceSpan | None = None


type Block = (
    Heading | Paragraph | LineatedBlock | Signature | Epigraph | DialogueLabel
    | ThematicBreak | QuoteBlock | ListBlock | CodeBlock | Table | ImageBlock
    | UnknownBlock
)


def map_block_inlines(block: Block, fn: Callable[[list[Inline]], list[Inline]]) -> Block:
    """Walk the container-block skeleton of `block`, applying `fn` to every leaf
    inline list it reaches, REBUILDING: returns a new block, never mutating.

    Leaf inline lists: a `Heading`/`Paragraph`'s `inlines`, each display line in
    a `LineatedBlock`'s `stanzas` (rebuilt as `Line`s preserving each `span`),
    each `Table` cell; `QuoteBlock`/`ListBlock` recurse. `fn` returns the
    replacement list. Blocks with no inline list (image and footnote leaves, the
    inline-free kinds) are returned unchanged — their leaf content is handled by
    the caller, hence `case _` rather than `assert_never`."""
    match block:
        case Heading() | Paragraph():
            return replace(block, inlines=fn(block.inlines))
        case LineatedBlock():
            return replace(block, stanzas=[
                [Line(fn(line.inlines), line.span) for line in stanza]
                for stanza in block.stanzas
            ])
        case Table():
            return replace(block, rows=[[fn(cell) for cell in row] for row in block.rows])
        case QuoteBlock():
            return replace(block, blocks=[map_block_inlines(inner, fn) for inner in block.blocks])
        case ListBlock():
            return replace(block, items=[
                [map_block_inlines(inner, fn) for inner in item] for item in block.items
            ])
        case _:
            return block  # inline-free block kinds carry no mappable inline list


# ---------------------------------------------------------------------------
# Side-channel document data (travels beside the blocks, never inside prose)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FootnoteDef:
    """A footnote definition: its dense id and its body blocks. Footnotes stay
    structured to lowering, where the appendix is generated last — so a definition
    can never be tail-stripped (the Phase-4 win, now structural)."""

    id: int
    blocks: list[Block]


@dataclass(frozen=True)
class Diagnostic:
    """A first-class finding with a severity and a stable code. `fatal` blocks the
    write; `warning` prints before the write summary; `info` records provenance."""

    severity: Literal["fatal", "warning", "info"]
    code: str
    message: str


@dataclass(frozen=True)
class Document:
    """The IR document — pure content. Blocks plus footnotes and bibliography
    travel SIDE BY SIDE, never inside the prose. Diagnostics flow through the
    one sink on the pass `Context`, not the document. The body images the
    lowering references are returned as `PlannedAsset`s from the asset pass
    (the writer copies them); they are not stored on the document.

    `bibliography` is an open record: the sidecar can grow fields without the IR
    pretending to own a closed schema."""

    blocks: list[Block] = field(default_factory=list)
    footnotes: list[FootnoteDef] = field(default_factory=list)
    bibliography: list[BibliographyEntry] = field(default_factory=list)
