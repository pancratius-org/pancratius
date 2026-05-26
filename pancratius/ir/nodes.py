# import-pure: no filesystem mutation
"""The block/inline intermediate representation for the import pipeline.

This is the semantic boundary from `docs/import-pipeline.md` ("The block IR"):
after the DOCX adapter, nothing is DOCX-shaped — it is blocks, inlines, footnote
definitions, assets, and diagnostics. Lowering turns this typed structure into
canonical Markdown exactly once; no Markdown string exists before then.

The model is deliberately minimal: only the block and inline kinds the Pancratius
canonical Markdown body actually needs, plus explicit `UnknownBlock`/`UnknownInline`
escape hatches for anything the adapter does not recognize. Do not smuggle
structure through string conventions — add a typed kind instead.

This module is PURE (no filesystem access); the marker above keys the PAN018
writer-only-mutation contract.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, assert_never

# The emphasis kinds the IR models. Exported so the adapter (mapping Pandoc node
# tags to it) and the lowering (mapping it to Markdown/HTML) share ONE source of
# truth for the closed set, instead of each re-spelling the string literals.
EmphKind = Literal["strong", "emph", "strike", "sup", "sub"]
# The two lineated-run kinds verse detection classifies (verse vs Q&A answer).
VerseRole = Literal["verse-block", "answer-block"]

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
    """A typographically quoted span (Pandoc `Quoted`); `single` selects the
    quote glyphs at lowering time."""

    single: bool
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
            return Quoted(node.single, children)
        case DirectionalSpan():
            return DirectionalSpan(node.direction, children)
        case UnknownInline():
            return UnknownInline(node.note, children)
    assert_never(node)


# ---------------------------------------------------------------------------
# Block kinds
# ---------------------------------------------------------------------------


@dataclass
class Heading:
    """A section heading at `level` (1..6)."""

    level: int
    inlines: list[Inline]


@dataclass
class Paragraph:
    """A body paragraph.

    `align` is the OOXML `w:jc` alignment zipped on positionally by the adapter
    (`""` for the default); it drives signature/epigraph detection. `empty` marks
    a Word empty paragraph — meaningful as a stanza break, so it is captured in
    the IR before any Markdown output could lose it. `italic` records that every
    text-bearing run carried italic (an epigraph signal).
    """

    inlines: list[Inline]
    align: str = ""
    empty: bool = False
    italic: bool = False


@dataclass
class VerseBlock:
    """Lineated verse / Q&A answer run.

    `stanzas` is a list of stanzas; each stanza is a list of display lines; each
    line is a list of inlines. A `***` stanza separator is represented as a
    one-line stanza whose single line is `[Text("***")]`.
    """

    stanzas: list[list[list[Inline]]]
    role: VerseRole = "verse-block"


@dataclass
class Signature:
    """A right-aligned authorial signature block (`<p class="signature">`)."""

    lines: list[str]


@dataclass
class Epigraph:
    """A right-aligned epigraph: a quote plus an attribution footer
    (`<blockquote class="epigraph">`)."""

    quote: list[str]
    footer: list[str]


@dataclass
class DialogueLabel:
    """A canonicalized speaker label (lowered as `**Speaker:**`)."""

    speaker: str


@dataclass
class ThematicBreak:
    """A `***` thematic break."""


@dataclass
class BlockQuote:
    """A blockquote. `role == "_div"` marks a transparent container the adapter
    used to flatten a Pandoc `Div` (production unwraps Divs); such a container is
    lowered as its children with no `>` prefix."""

    blocks: list[Block]
    role: str | None = None


@dataclass
class ListBlock:
    """An ordered or bullet list; each item is its own block list. `start` is the
    first ordinal of an ordered list (preserved from the source: Pandoc may split
    one authored list into chunks that resume at 4, 6, … — keeping `start` means
    the lowered Markdown reproduces those ordinals rather than renumbering)."""

    ordered: bool
    items: list[list[Block]]
    start: int = 1


@dataclass
class CodeBlock:
    """A fenced code block."""

    text: str


@dataclass
class Table:
    """A table. `rows` is structured cell content — rows of cells, each cell a list
    of inlines — so reading-content tables flow through the same AI-alt scrub and
    asset-rewrite passes as body prose before lowering to a GFM pipe table. `raw`
    keeps the opaque source node (the Pandoc Table JSON object, or `None`) for the
    bibliography classifier (it needs the hrefs and image alts the flattened cells
    would drop); it is typed `dict[str, object] | None` — a JSON object whose
    schema the pure IR makes no claim about, not the bare `object` it was — so the
    classifier still narrows it itself. `role == "bibliography"` once classified
    (then lifted, not lowered)."""

    rows: list[list[list[Inline]]]
    raw: dict[str, object] | None = None
    role: str | None = None


@dataclass
class ImageBlock:
    """A block-level image."""

    src: str
    alt: str
    asset_id: str | None = None


@dataclass
class UnknownBlock:
    """A block kind the adapter does not model. `note` records the source kind;
    `text` carries the block's best-effort plain reading text so lowering can
    PRESERVE it rather than silently dropping the block (the design's "unknown →
    preserve content / emit a diagnostic"). `text` is `""` for kinds that genuinely
    carry no reading content (e.g. `Null`); the block's presence is surfaced as a
    diagnostic at lowering regardless."""

    note: str
    text: str = ""


type Block = (
    Heading | Paragraph | VerseBlock | Signature | Epigraph | DialogueLabel
    | ThematicBreak | BlockQuote | ListBlock | CodeBlock | Table | ImageBlock
    | UnknownBlock
)


def map_block_inlines(block: Block, fn: Callable[[list[Inline]], list[Inline]]) -> None:
    """Walk the container-block skeleton of `block`, applying `fn` to every leaf
    inline list it reaches, mutating IN PLACE.

    Leaf inline lists: a `Heading`/`Paragraph`'s `inlines`, each verse display line
    in a `VerseBlock`'s `stanzas`, each `Table` cell; `BlockQuote`/`ListBlock`
    recurse. `fn` returns the replacement list. Blocks with no inline list (image
    and footnote leaves, the inline-free kinds) are skipped — their leaf content is
    handled by the caller, hence `case _` rather than `assert_never`."""
    match block:
        case Heading() | Paragraph():
            block.inlines = fn(block.inlines)
        case VerseBlock():
            block.stanzas = [[fn(line) for line in stanza] for stanza in block.stanzas]
        case Table():
            block.rows = [[fn(cell) for cell in row] for row in block.rows]
        case BlockQuote():
            for inner in block.blocks:
                map_block_inlines(inner, fn)
        case ListBlock():
            for item in block.items:
                for inner in item:
                    map_block_inlines(inner, fn)
        case _:
            pass  # inline-free block kinds carry no mappable inline list


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


@dataclass
class Document:
    """The IR document. Blocks plus footnotes, bibliography, and diagnostics
    travel SIDE BY SIDE — never inside the prose. The body images the lowering
    references are returned as `PlannedAsset`s from the asset pass (the writer
    copies them); they are not stored on the document.

    `bibliography` is an open `dict` record: a `TypedDict` isn't assignable across
    the `docx_conversion` sidecar boundary (it takes `list[dict[str, Any]]`, and
    `list` is invariant), so the open dict is the honest type."""

    blocks: list[Block] = field(default_factory=list)
    footnotes: list[FootnoteDef] = field(default_factory=list)
    bibliography: list[dict[str, object]] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
