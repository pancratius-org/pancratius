# import-pure: no filesystem mutation
"""Canonical source-domain view of a Word document.

One package read produces physical paragraph facts, rich blocks and inlines,
relationships, notes, media, compatibility choices, and exact source identity.
Import, diagnostics, correction rails, and research project from this aggregate
instead of reopening the package or reconciling another document view.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Collection, Iterator
from dataclasses import dataclass, field, replace
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from typing import assert_never

from pancratius import ooxml
from pancratius.ooxml import W
from pancratius.thematic import is_thematic_marker

DOCUMENT_PART = "word/document.xml"
STYLES_PART = "word/styles.xml"


class StoryPart(StrEnum):
    """Word story parts represented by canonical source addresses."""

    DOCUMENT = DOCUMENT_PART
    FOOTNOTES = "word/footnotes.xml"
    ENDNOTES = "word/endnotes.xml"

class DocxSourceError(ValueError):
    """A Word source cannot be represented by the canonical source model."""


# The raw `w:p` index in document order — the int a `ParagraphOrdinal` wraps.
# Source coordinates and semantic spans key on it directly.
type SourceOrdinal = int


@dataclass(frozen=True, slots=True, order=True)
class ParagraphOrdinal:
    """Stable zero-based identity of a top-level ``w:p`` in document order."""

    value: int

    def __post_init__(self) -> None:
        if self.value < 0:
            raise DocxSourceError("paragraph ordinal must be non-negative")

    def __int__(self) -> int:
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class SourceLineCoordinate:
    """One non-empty natural line inside a source paragraph."""

    ordinal: ParagraphOrdinal
    sub: int

    def __post_init__(self) -> None:
        if self.sub < 0:
            raise DocxSourceError("source line sub-index must be non-negative")


@dataclass(frozen=True, slots=True)
class SourceLine:
    """One natural source line with its stable coordinate and text."""

    coordinate: SourceLineCoordinate
    text: str


@dataclass(frozen=True, slots=True, order=True)
class SourceSegment:
    """A contiguous top-level flow region; increments at each table and each
    numbered paragraph. Visual grouping and run segmentation never join
    across it."""

    value: int

    def __post_init__(self) -> None:
        if self.value < 0:
            raise DocxSourceError("source segment must be non-negative")


@dataclass(frozen=True, slots=True, order=True)
class VisualLineationGroup:
    """Identity of adjacent paragraphs Word renders without paragraph spacing."""

    value: int

    def __post_init__(self) -> None:
        if self.value < 1:
            raise DocxSourceError("visual lineation group ids start at one")


class BreakKind(StrEnum):
    """The authored meaning of an inline Word break."""

    LINE = "line"
    PAGE = "page"
    COLUMN = "column"

    @classmethod
    def from_ooxml(cls, raw: str | None) -> BreakKind:
        match raw:
            case None | "textWrapping":
                return cls.LINE
            case "page":
                return cls.PAGE
            case "column":
                return cls.COLUMN
            case unsupported:
                raise DocxSourceError(f"unsupported w:br type {unsupported!r}")

    @property
    def is_pagination(self) -> bool:
        """Whether this break controls layout rather than authored lineation."""
        match self:
            case BreakKind.LINE:
                return False
            case BreakKind.PAGE | BreakKind.COLUMN:
                return True
        assert_never(self)


@dataclass(frozen=True, slots=True)
class TextAtom:
    """One source text fragment in document order."""

    value: str


type ParagraphAtom = TextAtom | BreakKind

class SourceAdjudicationKind(StrEnum):
    """The consumer-owned equivalence relation for a source adjudication."""

    LINEATION = "lineation"
    SCRIPTURE = "scripture"


@dataclass(frozen=True, slots=True)
class LineationFingerprint:
    """Identity of words and authored line boundaries; pagination is irrelevant."""

    value: str

    def __post_init__(self) -> None:
        _validate_fingerprint(self.value, kind="lineation")


@dataclass(frozen=True, slots=True)
class ScriptureFingerprint:
    """Identity of readable quotation text; layout and lineation are irrelevant."""

    value: str

    def __post_init__(self) -> None:
        _validate_fingerprint(self.value, kind="scripture")


type AdjudicationFingerprint = LineationFingerprint | ScriptureFingerprint


def _validate_fingerprint(value: str, *, kind: str) -> None:
    if re.fullmatch(r"[0-9a-f]{16}", value) is None:
        raise DocxSourceError(f"invalid {kind} fingerprint {value!r}")


def _fingerprint(domain: str, value: object) -> str:
    encoded = json.dumps(
        [domain, value],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class ParagraphContent:
    """One ordered truth from which every paragraph interpretation is derived."""

    atoms: tuple[ParagraphAtom, ...] = ()

    def __post_init__(self) -> None:
        """Erase OOXML run boundaries: adjacent text fragments are one domain atom."""
        canonical: list[ParagraphAtom] = []
        for atom in self.atoms:
            match atom:
                case TextAtom(value=""):
                    continue
                case TextAtom(value=value) if canonical and isinstance(canonical[-1], TextAtom):
                    previous = canonical[-1]
                    assert isinstance(previous, TextAtom)
                    canonical[-1] = TextAtom(previous.value + value)
                case TextAtom() | BreakKind.LINE | BreakKind.PAGE | BreakKind.COLUMN:
                    canonical.append(atom)
                case _ as unreachable:
                    assert_never(unreachable)
        object.__setattr__(self, "atoms", tuple(canonical))

    @property
    def reading(self) -> str:
        """Normalized reading text; every break is layout whitespace."""
        parts: list[str] = []
        for atom in self.atoms:
            match atom:
                case TextAtom(value=value):
                    parts.append(value)
                case BreakKind.LINE | BreakKind.PAGE | BreakKind.COLUMN:
                    parts.append(" ")
                case _ as unreachable:
                    assert_never(unreachable)
        return " ".join("".join(parts).split())

    @property
    def line_segments(self) -> tuple[str, ...]:
        """Natural source lines; only an authored line break starts a new line."""
        lines: list[list[str]] = [[]]
        for atom in self.atoms:
            match atom:
                case TextAtom(value=value):
                    lines[-1].append(value)
                case BreakKind.LINE:
                    lines.append([])
                case BreakKind.PAGE | BreakKind.COLUMN:
                    lines[-1].append(" ")
                case _ as unreachable:
                    assert_never(unreachable)
        return tuple(" ".join("".join(parts).split()) for parts in lines)

    @property
    def lineated(self) -> str:
        return "\n".join(self.line_segments)

    def adjudication_fingerprint(
        self,
        kind: SourceAdjudicationKind,
    ) -> AdjudicationFingerprint:
        match kind:
            case SourceAdjudicationKind.LINEATION:
                lines = tuple(
                    unicodedata.normalize("NFC", line)
                    for line in self.line_segments
                )
                return LineationFingerprint(_fingerprint("lineation-v1", lines))
            case SourceAdjudicationKind.SCRIPTURE:
                reading = unicodedata.normalize("NFC", self.reading)
                return ScriptureFingerprint(_fingerprint("scripture-v1", reading))
        assert_never(kind)

    @property
    def breaks(self) -> tuple[BreakKind, ...]:
        return tuple(atom for atom in self.atoms if isinstance(atom, BreakKind))

    @property
    def pagination_only(self) -> bool:
        """True when atoms carry pagination and no readable/lineating content."""
        return any(break_kind.is_pagination for break_kind in self.breaks) and all(
            (isinstance(atom, TextAtom) and not atom.value.strip())
            or (isinstance(atom, BreakKind) and atom.is_pagination)
            for atom in self.atoms
        )

    @property
    def text_only_blank(self) -> bool:
        """True when content is empty or consists solely of whitespace text."""
        return all(
            isinstance(atom, TextAtom) and not atom.value.strip()
            for atom in self.atoms
        )


class ParagraphDisposition(StrEnum):
    """Why a source paragraph does or does not carry readable content."""

    CONTENT = "content"
    STRUCTURAL_EMPTY = "structural_empty"
    PAGINATION_ONLY = "pagination_only"
    NON_TEXT = "non_text"


class ParagraphPayloadKind(StrEnum):
    """Non-text paragraph meaning retained outside the normalized atom stream."""

    DIRECT_NUMBERING = "direct_numbering"
    RESOLVED_NUMBERING = "resolved_numbering"
    SECTION = "section"
    OPAQUE = "opaque"


@dataclass(frozen=True, slots=True)
class ParagraphPayload:
    """Closed source payload facts relevant to preservation and deletion."""

    kinds: frozenset[ParagraphPayloadKind] = frozenset()

    @property
    def has_meaning(self) -> bool:
        return bool(self.kinds)

    @property
    def has_opaque(self) -> bool:
        return ParagraphPayloadKind.OPAQUE in self.kinds

    @property
    def atomic_deletion_safe(self) -> bool:
        """Whole-paragraph deletion may discard presentation, but not content."""
        return self.kinds <= {
            ParagraphPayloadKind.DIRECT_NUMBERING,
            ParagraphPayloadKind.RESOLVED_NUMBERING,
        }

    def adding(self, kind: ParagraphPayloadKind) -> ParagraphPayload:
        return ParagraphPayload(self.kinds | {kind})


@dataclass(frozen=True, slots=True)
class ParagraphSemantics:
    """The one source-owned analysis of a raw Word paragraph."""

    content: ParagraphContent
    page_break_before: bool
    payload: ParagraphPayload = ParagraphPayload()

    @property
    def has_opaque_payload(self) -> bool:
        return self.payload.has_opaque

    @property
    def text(self) -> str:
        """Readable paragraph text; layout controls never become content."""
        return self.content.reading.strip()

    @property
    def disposition(self) -> ParagraphDisposition:
        """Derive removability from source facts; never cache a second truth."""
        if self.text:
            return ParagraphDisposition.CONTENT
        if not self.payload.has_meaning and (
            self.content.pagination_only
            or (self.page_break_before and self.content.text_only_blank)
        ):
            return ParagraphDisposition.PAGINATION_ONLY
        if not self.payload.has_meaning and self.content.text_only_blank:
            return ParagraphDisposition.STRUCTURAL_EMPTY
        return ParagraphDisposition.NON_TEXT


@dataclass(frozen=True, slots=True)
class ParagraphStyles:
    """Resolved paragraph-style facts needed by semantic analysis."""

    default: str = "Normal"
    numbered: frozenset[str] = frozenset()

    def is_numbered(self, direct_style: str) -> bool:
        return (direct_style or self.default) in self.numbered


@dataclass(frozen=True, slots=True)
class ParagraphMarkers:
    """Independent source observations; none pretends to be the paragraph's sole role."""

    numbered: bool = False
    heading_style: bool = False
    thematic_marker: bool = False


class BorderGesture(StrEnum):
    """Editorial paragraph-border gesture used by display-register inference."""

    NONE = ""
    BOX = "box"
    RULE = "rule"
    OTHER = "other"


class TextAlignment(StrEnum):
    """Reading alignment after OOXML start/end aliases are resolved."""

    LEFT = "left"
    JUST = "just"
    CENTER = "center"
    RIGHT = "right"


_TEXT_ALIGNMENT = {
    "": TextAlignment.LEFT,
    "left": TextAlignment.LEFT,
    "start": TextAlignment.LEFT,
    "both": TextAlignment.JUST,
    "center": TextAlignment.CENTER,
    "right": TextAlignment.RIGHT,
    "end": TextAlignment.RIGHT,
}


@dataclass(frozen=True, slots=True)
class ParagraphAlignment:
    """Validated OOXML alignment token and its semantic projection."""

    value: str = ""

    def __post_init__(self) -> None:
        if self.value not in _TEXT_ALIGNMENT:
            raise DocxSourceError(f"unsupported w:jc value {self.value!r}")

    @property
    def normalized(self) -> TextAlignment:
        return _TEXT_ALIGNMENT[self.value]

@dataclass(frozen=True, slots=True, order=True)
class Twips:
    """A signed OOXML twentieth-of-a-point measurement."""

    value: int = 0

    @property
    def points(self) -> float:
        return self.value / 20.0


@dataclass(frozen=True, slots=True)
class GeometryUnavailable:
    """The package does not state a geometry fact the consumer needs."""


@dataclass(frozen=True, slots=True)
class ObservedColumnWidth:
    width: Twips

    def __post_init__(self) -> None:
        if self.width.value <= 0:
            raise DocxSourceError("document column width must be positive")


@dataclass(frozen=True, slots=True)
class HeterogeneousColumnWidths:
    """Distinct observed section widths that cannot support one document-wide fill model."""

    widths: tuple[Twips, ...]

    def __post_init__(self) -> None:
        values = tuple(sorted({width.value for width in self.widths}))
        if len(values) < 2 or values[0] <= 0:
            raise DocxSourceError("heterogeneous column widths require two positive values")
        object.__setattr__(self, "widths", tuple(Twips(value) for value in values))


@dataclass(frozen=True, slots=True)
class PartiallyObservedColumnWidths:
    """Some sections have a width and others do not; no global width may be inferred."""

    widths: tuple[Twips, ...]

    def __post_init__(self) -> None:
        values = tuple(sorted({width.value for width in self.widths}))
        if not values or values[0] <= 0:
            raise DocxSourceError("partial column geometry requires a positive observed width")
        object.__setattr__(self, "widths", tuple(Twips(value) for value in values))


@dataclass(frozen=True, slots=True)
class ObservedFontSize:
    half_points: int

    def __post_init__(self) -> None:
        if self.half_points <= 0:
            raise DocxSourceError("document default font size must be positive")


type ColumnWidth = (
    ObservedColumnWidth
    | HeterogeneousColumnWidths
    | PartiallyObservedColumnWidths
    | GeometryUnavailable
)
type DefaultFontSize = ObservedFontSize | GeometryUnavailable


@dataclass(frozen=True, slots=True)
class DocumentLayout:
    """Only package geometry actually observed in OOXML; policy fallbacks live downstream."""

    column_width: ColumnWidth = GeometryUnavailable()
    default_font_size: DefaultFontSize = GeometryUnavailable()


type OoxmlAttributes = tuple[tuple[str, str], ...]


def _attributes(element: ET.Element | None) -> OoxmlAttributes:
    return () if element is None else tuple(
        sorted((key.removeprefix(W), value) for key, value in element.attrib.items())
    )


def _attribute(attributes: OoxmlAttributes, key: str) -> str | None:
    return dict(attributes).get(key)


def _twips(
    attributes: OoxmlAttributes,
    key: str,
    *,
    alias: str | None = None,
) -> Twips:
    """Decode one known layout measurement; malformed source never becomes zero."""
    values = dict(attributes)
    raw = values.get(key)
    if raw is None and alias is not None:
        raw = values.get(alias)
    if raw is None:
        return Twips()
    try:
        return Twips(int(raw))
    except ValueError as exc:
        raise DocxSourceError(f"invalid w:{key} twips value {raw!r}") from exc


@dataclass(frozen=True, slots=True)
class ParagraphSpacing:
    """One resolved OOXML spacing value; semantic measurements are derived views."""

    attributes: OoxmlAttributes = ()

    @property
    def before(self) -> Twips:
        return _twips(self.attributes, "before")

    @property
    def after(self) -> Twips:
        return _twips(self.attributes, "after")

    def is_real(self, edge: str) -> bool:
        if _attribute(self.attributes, f"{edge}Autospacing") == "1":
            return True
        return (self.before if edge == "before" else self.after).value > 0


@dataclass(frozen=True, slots=True)
class ParagraphIndent:
    """One resolved OOXML indentation value; aliases lower to typed measurements."""

    attributes: OoxmlAttributes = ()

    @property
    def first_line(self) -> Twips:
        return _twips(self.attributes, "firstLine")

    @property
    def left(self) -> Twips:
        return _twips(self.attributes, "left", alias="start")

    @property
    def hanging(self) -> Twips:
        return _twips(self.attributes, "hanging")


@dataclass(frozen=True, slots=True)
class ParagraphLayout:
    """The single resolved paragraph-layout value."""

    source_alignment: ParagraphAlignment = ParagraphAlignment()
    spacing: ParagraphSpacing = ParagraphSpacing()
    indent: ParagraphIndent = ParagraphIndent()

    @property
    def alignment(self) -> TextAlignment:
        return self.source_alignment.normalized

    @property
    def first_line_indent(self) -> Twips:
        return self.indent.first_line

    @property
    def left_indent(self) -> Twips:
        return self.indent.left

    @property
    def spacing_after(self) -> Twips:
        return self.spacing.after


@dataclass(frozen=True, slots=True)
class SourceParagraphPresentation:
    """Resolved physical properties shared by rich and body paragraph views."""

    resolved_style: str = ""
    direct_style: str = ""
    layout: ParagraphLayout = ParagraphLayout()
    contextual_spacing: bool = False
    border: BorderGesture = BorderGesture.NONE
    numbering: SourceNumbering | None = None
    heading_level: int | None = None


@dataclass(frozen=True, slots=True)
class SourceParagraph:
    """One canonical top-level Word paragraph and all source-owned facts."""

    ordinal: ParagraphOrdinal
    semantics: ParagraphSemantics
    presentation: SourceParagraphPresentation
    indent_departure: bool
    markers: ParagraphMarkers
    segment: SourceSegment
    bold: bool
    italic: bool
    visual_group: VisualLineationGroup | None = None

    @property
    def resolved_style(self) -> str:
        return self.presentation.resolved_style

    @property
    def direct_style(self) -> str:
        return self.presentation.direct_style

    @property
    def layout(self) -> ParagraphLayout:
        return self.presentation.layout

    @property
    def contextual_spacing(self) -> bool:
        return self.presentation.contextual_spacing

    @property
    def border(self) -> BorderGesture:
        return self.presentation.border

    @property
    def numbering(self) -> SourceNumbering | None:
        return self.presentation.numbering

    @property
    def heading_level(self) -> int | None:
        return self.presentation.heading_level

    @property
    def content(self) -> ParagraphContent:
        return self.semantics.content

    @property
    def natural_lines(self) -> tuple[SourceLine, ...]:
        return tuple(
            SourceLine(SourceLineCoordinate(self.ordinal, sub), text)
            for sub, text in enumerate(line for line in self.content.line_segments if line)
        )

    @property
    def line_coordinates(self) -> tuple[SourceLineCoordinate, ...]:
        return tuple(line.coordinate for line in self.natural_lines)

    @property
    def alignment(self) -> ParagraphAlignment:
        return self.layout.source_alignment

    @property
    def spacing(self) -> OoxmlAttributes:
        return self.layout.spacing.attributes

    @property
    def indent(self) -> OoxmlAttributes:
        return self.layout.indent.attributes

    @property
    def disposition(self) -> ParagraphDisposition:
        return self.semantics.disposition

    @property
    def page_break_before(self) -> bool:
        return self.semantics.page_break_before

    @property
    def atomic_deletion_safe(self) -> bool:
        return self.semantics.payload.atomic_deletion_safe

    @property
    def text(self) -> str:
        return self.semantics.text

    def adjudication_fingerprint(
        self,
        kind: SourceAdjudicationKind,
    ) -> AdjudicationFingerprint:
        return self.content.adjudication_fingerprint(kind)

    @property
    def numbered(self) -> bool:
        return self.markers.numbered

    @property
    def heading(self) -> bool:
        return self.markers.heading_style

    @property
    def thematic(self) -> bool:
        return self.markers.thematic_marker

    @property
    def empty(self) -> bool:
        return not self.text

@dataclass(frozen=True, slots=True)
class _BodyParagraphSeed:
    """Physical facts known before the rich inline grammar supplies content."""

    ordinal: ParagraphOrdinal
    page_break_before: bool
    payload: ParagraphPayload
    presentation: SourceParagraphPresentation
    segment: SourceSegment


@dataclass(frozen=True, slots=True, order=True)
class SourceAddress:
    """Stable structural address of a source unit inside one Word story.

    Body paragraph ordinals remain the editorial coordinate used by correction
    sidecars.  ``SourceAddress`` covers the richer cases that an ordinal cannot:
    table cells, note definitions, content controls, and text-box paragraphs.
    ``path`` is the selected block/inline child path after compatibility markup
    has chosen exactly one branch.
    """

    story: StoryPart
    path: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class SourceNumbering:
    """Resolved numbering facts for one Word paragraph."""

    num_id: int
    level: int
    ordered: bool
    start: int = 1
    level_text: str = ""
    format: str = ""


@dataclass(frozen=True, slots=True)
class SourceRunProperties:
    """Resolved character presentation without assigning product meaning."""

    style: str = ""
    bold: bool = False
    italic: bool = False
    strike: bool = False
    underline: bool = False
    small_caps: bool = False
    superscript: bool = False
    subscript: bool = False
    rtl: bool = False
    code: bool = False


@dataclass(frozen=True, slots=True)
class SourceText:
    value: str


@dataclass(frozen=True, slots=True)
class SourceRun:
    children: tuple[SourceInline, ...]
    properties: SourceRunProperties = SourceRunProperties()


@dataclass(frozen=True, slots=True)
class SourceHyperlink:
    children: tuple[SourceInline, ...]
    target: str
    relationship_id: str | None = None


@dataclass(frozen=True, slots=True)
class SourceImage:
    relationship_id: str
    target: str
    alt: str = ""
    media_part: str | None = None


class NoteKind(StrEnum):
    FOOTNOTE = "footnote"
    ENDNOTE = "endnote"


@dataclass(frozen=True, slots=True)
class SourceNoteReference:
    kind: NoteKind
    note_id: int


@dataclass(frozen=True, slots=True)
class SourceSymbol:
    font: str
    character: str
    code: str = ""


_SYMBOL_CHARACTERS = {
    ("wingdings", "F04A"): "☺",
}


def _symbol_character(font: str, code: str) -> str:
    normalized = code.upper()
    if character := _SYMBOL_CHARACTERS.get((font.casefold(), normalized)):
        return character
    try:
        codepoint = int(normalized, 16)
    except ValueError:
        return ""
    # A private-use value in w:sym is an index into the named symbol font, not
    # a Unicode character. Unknown mappings must remain explicit downstream.
    if 0xE000 <= codepoint <= 0xF8FF:
        return ""
    return chr(codepoint) if 0 <= codepoint <= 0x10FFFF else ""


@dataclass(frozen=True, slots=True)
class SourceRenderedPageBreak:
    """Pagination inserted by a renderer, never an authored break."""


@dataclass(frozen=True, slots=True)
class SourceHorizontalRule:
    """A physical VML horizontal rule; product meaning is decided downstream."""


@dataclass(frozen=True, slots=True)
class SourceFieldInstruction:
    value: str


@dataclass(frozen=True, slots=True)
class SourceFieldBoundary:
    kind: str


def _field_hyperlink_target(instruction: str) -> str | None:
    quoted_or_bare = r'(?:"([^"]+)"|(\S+))'
    local = re.search(
        rf"\\l\s+{quoted_or_bare}", instruction, flags=re.IGNORECASE
    )
    external = re.match(
        rf"\s*HYPERLINK\s+(?!\\[A-Za-z]){quoted_or_bare}",
        instruction,
        flags=re.IGNORECASE,
    )
    target = ""
    if external is not None:
        target = external.group(1) or external.group(2) or ""
    if local is not None:
        anchor = local.group(1) or local.group(2) or ""
        target = f"{target}#{anchor}" if target else f"#{anchor}"
    return target or None


def _field_kind(instruction: str) -> str:
    match = re.match(r"\s*([A-Za-z]+)", instruction)
    return match.group(1).upper() if match else ""


@dataclass(frozen=True, slots=True)
class SourceField:
    """A complex field result, with a shared identity across source paragraphs."""

    instruction: str
    children: tuple[SourceInline, ...]
    field_id: int | None = None

    @property
    def kind(self) -> str:
        return _field_kind(self.instruction)

    @property
    def hyperlink_target(self) -> str | None:
        return (
            _field_hyperlink_target(self.instruction)
            if self.kind == "HYPERLINK"
            else None
        )


@dataclass(frozen=True, slots=True)
class SourceUnknownInline:
    """An inline construct outside the supported grammar, with readable text."""

    name: str
    text: str = ""


@dataclass(frozen=True, slots=True)
class SourceTextBox:
    blocks: tuple[SourceBlock, ...]


type SourceInline = (
    SourceText
    | BreakKind
    | SourceRun
    | SourceHyperlink
    | SourceImage
    | SourceNoteReference
    | SourceSymbol
    | SourceRenderedPageBreak
    | SourceHorizontalRule
    | SourceFieldInstruction
    | SourceFieldBoundary
    | SourceField
    | SourceUnknownInline
    | SourceTextBox
)


@dataclass(frozen=True, slots=True)
class SourceParagraphBlock:
    """One rich paragraph tied directly to its physical source unit."""

    address: SourceAddress
    inlines: tuple[SourceInline, ...]
    body_ordinal: ParagraphOrdinal | None = None
    presentation: SourceParagraphPresentation = SourceParagraphPresentation()
    paragraph: SourceParagraph | None = None
    field_kinds: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if (
            self.paragraph is not None
            and self.body_ordinal != self.paragraph.ordinal
        ):
            raise DocxSourceError(
                "rich block and body paragraph must share one ordinal"
            )
        if (
            self.paragraph is not None
            and self.presentation is not self.paragraph.presentation
        ):
            raise DocxSourceError(
                "rich block and body paragraph must share one presentation value"
            )

    @property
    def reading(self) -> str:
        return inline_reading(self.inlines)

    @property
    def coordinates(self) -> tuple[SourceLineCoordinate, ...]:
        return self.paragraph.line_coordinates if self.paragraph is not None else ()

    @property
    def direct_style(self) -> str:
        return self.presentation.direct_style

    @property
    def resolved_style(self) -> str:
        return self.presentation.resolved_style

    @property
    def heading_level(self) -> int | None:
        return self.presentation.heading_level

    @property
    def numbering(self) -> SourceNumbering | None:
        return self.presentation.numbering

    @property
    def alignment(self) -> ParagraphAlignment:
        return self.presentation.layout.source_alignment

    @property
    def indent(self) -> ParagraphIndent:
        return self.presentation.layout.indent


@dataclass(frozen=True, slots=True)
class SourceTableCell:
    address: SourceAddress
    blocks: tuple[SourceBlock, ...]
    row_span: int = 1
    column_span: int = 1


@dataclass(frozen=True, slots=True)
class SourceTableRow:
    cells: tuple[SourceTableCell, ...]


@dataclass(frozen=True, slots=True)
class SourceTableBlock:
    address: SourceAddress
    rows: tuple[SourceTableRow, ...]


@dataclass(frozen=True, slots=True)
class SourceContentControl:
    address: SourceAddress
    blocks: tuple[SourceBlock, ...]


@dataclass(frozen=True, slots=True)
class SourceUnknownBlock:
    address: SourceAddress
    name: str
    text: str = ""


type SourceBlock = (
    SourceParagraphBlock | SourceTableBlock | SourceContentControl | SourceUnknownBlock
)


@dataclass(frozen=True, slots=True)
class SourceNoteDefinition:
    kind: NoteKind
    note_id: int
    blocks: tuple[SourceBlock, ...]


@dataclass(frozen=True, slots=True)
class SourceMedia:
    part_name: str
    data: bytes


class SourceDiagnosticCode(StrEnum):
    COMPATIBILITY_FALLBACK = "source.compatibility-fallback"
    COMPATIBILITY_UNSUPPORTED = "source.compatibility-unsupported"
    FIELD_CONTROL_UNMATCHED = "source.field-control-unmatched"
    FIELD_INCOMPLETE = "source.field-incomplete"
    FIELD_INSTRUCTION_IN_RESULT = "source.field-instruction-in-result"
    IMAGE_RELATIONSHIP = "source.image-relationship"
    RELATIONSHIP = "source.relationship"
    RELATIONSHIP_MISSING = "source.relationship-missing"
    TABLE_CAPTION_UNSUPPORTED = "source.table-caption-unsupported"
    TABLE_NESTED_UNSUPPORTED = "source.table-nested-unsupported"
    TABLE_VERTICAL_MERGE_UNSUPPORTED = "source.table-vertical-merge-unsupported"


@dataclass(frozen=True, slots=True)
class SourceDiagnostic:
    code: SourceDiagnosticCode
    message: str
    address: SourceAddress | None = None


@dataclass(frozen=True, slots=True)
class DocxSourceDocument:
    """Aggregate root for the source facts of one DOCX body."""

    path: Path
    paragraphs: tuple[SourceParagraph, ...]
    styles: ParagraphStyles = ParagraphStyles()
    layout: DocumentLayout = DocumentLayout()
    body: tuple[SourceBlock, ...] = ()
    notes: tuple[SourceNoteDefinition, ...] = ()
    media: tuple[SourceMedia, ...] = ()
    diagnostics: tuple[SourceDiagnostic, ...] = ()

    @property
    def body_readings(self) -> tuple[str, ...]:
        """Readable source rows projected from the canonical rich body."""
        return block_readings(self.body)

    @property
    def note_readings(self) -> tuple[str, ...]:
        """Readable source rows from authored note-story definitions."""
        return tuple(
            reading
            for note in self.notes
            for reading in block_readings(note.blocks)
        )

    @property
    def content_ordinals(self) -> frozenset[SourceOrdinal]:
        """Raw identities eligible for per-paragraph reading/lineation truth."""
        return frozenset(
            paragraph.ordinal.value
            for paragraph in self.paragraphs
            if paragraph.disposition is ParagraphDisposition.CONTENT
        )

    @property
    def semantic_ordinals(self) -> frozenset[SourceOrdinal]:
        """Raw identities that carry readable or opaque source meaning."""
        return frozenset(
            paragraph.ordinal.value
            for paragraph in self.paragraphs
            if paragraph.disposition
            in {ParagraphDisposition.CONTENT, ParagraphDisposition.NON_TEXT}
        )

    def paragraph(self, ordinal: ParagraphOrdinal) -> SourceParagraph:
        if ordinal.value >= len(self.paragraphs):
            raise DocxSourceError(
                f"{self.path.name}: no source paragraph at ordinal {ordinal.value}"
            )
        return self.paragraphs[ordinal.value]

def inline_reading(inlines: tuple[SourceInline, ...]) -> str:
    """Presentation-free readable text for one canonical inline sequence."""
    return inline_content(inlines).reading


def inline_content(inlines: tuple[SourceInline, ...]) -> ParagraphContent:
    """Canonical physical atoms derived from the rich inline model."""
    atoms: list[ParagraphAtom] = []

    def append_inlines(items: tuple[SourceInline, ...]) -> None:
        for inline in items:
            match inline:
                case SourceText(value=value):
                    atoms.append(TextAtom(value))
                case BreakKind() as break_kind:
                    atoms.append(break_kind)
                case SourceRun() | SourceHyperlink() | SourceField():
                    append_inlines(inline.children)
                case SourceSymbol(character=character):
                    atoms.append(TextAtom(character))
                case SourceUnknownInline(text=text):
                    atoms.append(TextAtom(text))
                case SourceTextBox(blocks=blocks):
                    atoms.append(TextAtom(" "))
                    append_blocks(blocks)
                    atoms.append(TextAtom(" "))
                case (
                    SourceImage()
                    | SourceNoteReference()
                    | SourceRenderedPageBreak()
                    | SourceHorizontalRule()
                    | SourceFieldInstruction()
                    | SourceFieldBoundary()
                ):
                    continue
                case _ as unreachable:
                    assert_never(unreachable)

    def append_blocks(blocks: tuple[SourceBlock, ...]) -> None:
        first = True
        for block in blocks:
            if not first:
                atoms.append(TextAtom(" "))
            first = False
            match block:
                case SourceParagraphBlock(inlines=children):
                    append_inlines(children)
                case SourceTableBlock(rows=rows):
                    nested = tuple(
                        child
                        for row in rows
                        for cell in row.cells
                        for child in cell.blocks
                    )
                    append_blocks(nested)
                case SourceContentControl(blocks=children):
                    append_blocks(children)
                case SourceUnknownBlock(text=text):
                    atoms.append(TextAtom(text))
                case _ as unreachable:
                    assert_never(unreachable)

    append_inlines(inlines)
    return ParagraphContent(tuple(atoms))


def walk_source_inlines(
    inlines: tuple[SourceInline, ...],
) -> Iterator[SourceInline]:
    """Walk one inline tree in source order.

    A text box is yielded as an inline boundary. Its blocks are covered by
    :func:`walk_source_blocks`, so callers traversing a whole source tree combine
    the two walkers without seeing nested content twice.
    """
    for inline in inlines:
        yield inline
        match inline:
            case SourceRun(children=children) | SourceHyperlink(children=children) | SourceField(children=children):
                yield from walk_source_inlines(children)
            case (
                SourceText()
                | BreakKind.LINE
                | BreakKind.PAGE
                | BreakKind.COLUMN
                | SourceImage()
                | SourceNoteReference()
                | SourceSymbol()
                | SourceRenderedPageBreak()
                | SourceHorizontalRule()
                | SourceFieldInstruction()
                | SourceFieldBoundary()
                | SourceUnknownInline()
                | SourceTextBox()
            ):
                continue
            case _ as unreachable:
                assert_never(unreachable)


def walk_source_blocks(blocks: tuple[SourceBlock, ...]) -> Iterator[SourceBlock]:
    """Walk the closed source block tree, including blocks inside text boxes."""
    for block in blocks:
        yield block
        match block:
            case SourceParagraphBlock(inlines=inlines):
                for inline in walk_source_inlines(inlines):
                    if isinstance(inline, SourceTextBox):
                        yield from walk_source_blocks(inline.blocks)
            case SourceTableBlock(rows=rows):
                for row in rows:
                    for cell in row.cells:
                        yield from walk_source_blocks(cell.blocks)
            case SourceContentControl(blocks=children):
                yield from walk_source_blocks(children)
            case SourceUnknownBlock():
                continue
            case _ as unreachable:
                assert_never(unreachable)


def _source_runs(inlines: tuple[SourceInline, ...]) -> Iterator[SourceRun]:
    for inline in walk_source_inlines(inlines):
        if isinstance(inline, SourceRun):
            yield inline
        elif isinstance(inline, SourceTextBox):
            for block in walk_source_blocks(inline.blocks):
                if not isinstance(block, SourceParagraphBlock):
                    continue
                yield from (
                    child
                    for child in walk_source_inlines(block.inlines)
                    if isinstance(child, SourceRun)
                )


def block_readings(
    blocks: tuple[SourceBlock, ...],
    *,
    exclude_field_kinds: Collection[str] = (),
) -> tuple[str, ...]:
    """Readable rows from a canonical block tree, optionally excluding fields."""
    out: list[str] = []
    for block in blocks:
        match block:
            case SourceParagraphBlock(field_kinds=field_kinds):
                if field_kinds.intersection(exclude_field_kinds):
                    continue
                out.append(block.reading)
            case SourceTableBlock(rows=rows):
                out.extend(
                    reading
                    for row in rows
                    for cell in row.cells
                    for reading in block_readings(
                        cell.blocks,
                        exclude_field_kinds=exclude_field_kinds,
                    )
                )
            case SourceContentControl(blocks=children):
                out.extend(block_readings(
                    children,
                    exclude_field_kinds=exclude_field_kinds,
                ))
            case SourceUnknownBlock(text=text):
                out.append(text)
            case _ as unreachable:
                assert_never(unreachable)
    return tuple(out)


@dataclass(frozen=True, slots=True)
class SourceAdjudication:
    """A sidecar payload bound to the exact source paragraph it adjudicates."""

    paragraph: SourceParagraph
    payload: dict[str, object]


def read_adjudications(
    source: DocxSourceDocument,
    sidecar: Path,
    *,
    kind: SourceAdjudicationKind,
) -> tuple[SourceAdjudication, ...]:
    """Load entries under the consumer's typed source-equivalence relation."""
    if not sidecar.is_file():
        return ()

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError(f"{sidecar.name}: duplicate key in sidecar object")
        return result

    try:
        raw = json.loads(sidecar.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{sidecar.name}: not valid JSON — {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{sidecar.name}: must be an object keyed by source ordinal")

    out: list[SourceAdjudication] = []
    for key, payload in raw.items():
        if not (key.isdigit() and str(int(key)) == key):
            raise ValueError(f"{sidecar.name}: key {key!r} is not a canonical ordinal")
        ordinal = ParagraphOrdinal(int(key))
        try:
            paragraph = source.paragraph(ordinal)
        except DocxSourceError as exc:
            raise ValueError(
                f"{sidecar.name}: ordinal {ordinal.value} has no source paragraph in "
                f"{source.path.name} — the adjudication is stale; re-adjudicate or remove it"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(f"{sidecar.name}: ordinal {ordinal.value} entry must be an object")
        field = f"{kind.value}_fingerprint"
        raw_fingerprint = payload.get(field)
        if not isinstance(raw_fingerprint, str):
            raise ValueError(
                f"{sidecar.name}: ordinal {ordinal.value} is missing the {field} rail"
            )
        try:
            match kind:
                case SourceAdjudicationKind.LINEATION:
                    expected: AdjudicationFingerprint = LineationFingerprint(raw_fingerprint)
                case SourceAdjudicationKind.SCRIPTURE:
                    expected = ScriptureFingerprint(raw_fingerprint)
                case _ as unreachable:
                    assert_never(unreachable)
        except DocxSourceError as exc:
            raise ValueError(f"{sidecar.name}: ordinal {ordinal.value}: {exc}") from exc
        current = paragraph.adjudication_fingerprint(kind)
        if current != expected:
            raise ValueError(
                f"{sidecar.name}: ordinal {ordinal.value} source content drifted under the "
                f"adjudication (rail {expected.value} != live {current.value}) — "
                "re-adjudicate against the current source"
            )
        out.append(SourceAdjudication(paragraph, payload))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class _RunPropertyDelta:
    bold: bool | None = None
    italic: bool | None = None
    strike: bool | None = None
    underline: bool | None = None
    small_caps: bool | None = None
    vertical_align: str | None = None
    rtl: bool | None = None


@dataclass(frozen=True, slots=True)
class _StyleDefinition:
    name: str
    based_on: str
    contextual_spacing: bool
    alignment: ParagraphAlignment
    spacing: OoxmlAttributes
    indent: OoxmlAttributes
    font_half_points: int | None
    numbered: bool
    num_id: int | None
    num_level: int | None
    outline_level: int | None
    run: _RunPropertyDelta


@dataclass(frozen=True, slots=True)
class _CharacterStyleDefinition:
    name: str
    based_on: str
    run: _RunPropertyDelta


@dataclass(frozen=True, slots=True)
class _StyleSheet:
    paragraphs: dict[str, _StyleDefinition]
    characters: dict[str, _CharacterStyleDefinition] = field(default_factory=dict)
    default_paragraph: str = "Normal"
    default_alignment: ParagraphAlignment = ParagraphAlignment()
    default_spacing: OoxmlAttributes = ()
    default_indent: OoxmlAttributes = ()
    default_font_half_points: int | None = None
    default_run: _RunPropertyDelta = _RunPropertyDelta()
    _paragraph_chains: dict[str, tuple[_StyleDefinition, ...]] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )
    _character_chains: dict[str, tuple[_CharacterStyleDefinition, ...]] = field(
        default_factory=dict,
        init=False,
        repr=False,
        compare=False,
    )

    def paragraph_chain(self, style: str) -> tuple[_StyleDefinition, ...]:
        if style not in self._paragraph_chains:
            self._paragraph_chains[style] = tuple(_style_chain(style, self.paragraphs))
        return self._paragraph_chains[style]

    def character_chain(self, style: str) -> tuple[_CharacterStyleDefinition, ...]:
        if style not in self._character_chains:
            self._character_chains[style] = tuple(
                _character_style_chain(style, self.characters)
            )
        return self._character_chains[style]


def _w_val(element: ET.Element | None) -> str:
    return str(element.get(f"{W}val") or "") if element is not None else ""


def _compatibility_fallback(alternate: ET.Element) -> ET.Element | None:
    """Select the one compatibility branch Pancratius can interpret.

    The direct reader does not claim extension namespaces.  It therefore uses
    the explicit fallback wherever ``mc:AlternateContent`` occurs and records
    that choice in the aggregate diagnostics.
    """
    fallbacks = alternate.findall(ooxml.MC_FALLBACK)
    if len(fallbacks) > 1:
        raise DocxSourceError("mc:AlternateContent has multiple fallback branches")
    return fallbacks[0] if fallbacks else None


def iter_source_children(parent: ET.Element) -> Iterator[ET.Element]:
    """Yield direct children from the canonical compatibility view."""
    for child in parent:
        if child.tag == ooxml.MC_ALTERNATE_CONTENT:
            fallback = _compatibility_fallback(child)
            if fallback is not None:
                yield from iter_source_children(fallback)
            continue
        yield child


def iter_source_descendants(root: ET.Element) -> Iterator[ET.Element]:
    """Walk every descendant from the canonical compatibility view."""
    for child in iter_source_children(root):
        yield child
        yield from iter_source_descendants(child)


def story_paragraph_elements(root: ET.Element) -> tuple[ET.Element, ...]:
    """Paragraphs visible under one Word story in selected document order."""
    return tuple(
        element
        for element in iter_source_descendants(root)
        if element.tag == f"{W}p"
    )


def paragraph_has_drawing(paragraph: ET.Element) -> bool:
    """Whether the selected compatibility branch carries a drawing."""
    return any(
        element.tag in {f"{W}drawing", f"{W}pict"}
        for element in iter_source_descendants(paragraph)
    )


def _enabled(element: ET.Element | None) -> bool:
    return element is not None and element.get(f"{W}val") not in {"0", "false", "False", "off"}


def _optional_toggle(parent: ET.Element | None, name: str) -> bool | None:
    element = parent.find(f"{W}{name}") if parent is not None else None
    return None if element is None else _enabled(element)


def _run_delta(rpr: ET.Element | None) -> _RunPropertyDelta:
    vertical = _w_val(rpr.find(f"{W}vertAlign") if rpr is not None else None)
    underline_element = rpr.find(f"{W}u") if rpr is not None else None
    underline = None
    if underline_element is not None:
        underline = _w_val(underline_element) not in {"none", "0", "false", "off"}
    return _RunPropertyDelta(
        bold=_optional_toggle(rpr, "b"),
        italic=_optional_toggle(rpr, "i"),
        strike=(
            _optional_toggle(rpr, "strike")
            if _optional_toggle(rpr, "strike") is not None
            else _optional_toggle(rpr, "dstrike")
        ),
        underline=underline,
        small_caps=_optional_toggle(rpr, "smallCaps"),
        vertical_align=vertical or None,
        rtl=_optional_toggle(rpr, "rtl"),
    )


def _num_properties(ppr: ET.Element | None) -> tuple[int | None, int | None]:
    num_pr = ppr.find(f"{W}numPr") if ppr is not None else None
    if num_pr is None:
        return None, None
    num_id = _ooxml_optional_int(num_pr.find(f"{W}numId"), "val")
    level = _ooxml_optional_int(num_pr.find(f"{W}ilvl"), "val")
    return num_id, level


def _style_sheet_xml(styles_xml: bytes | None) -> _StyleSheet:
    if styles_xml is None:
        return _StyleSheet({})
    root = ET.fromstring(styles_xml)

    styles: dict[str, _StyleDefinition] = {}
    characters: dict[str, _CharacterStyleDefinition] = {}
    default = "Normal"
    for style in root.findall(f".//{W}style"):
        style_id = str(style.get(f"{W}styleId") or "")
        if not style_id:
            continue
        style_type = style.get(f"{W}type")
        name = _w_val(style.find(f"{W}name"))
        based_on = _w_val(style.find(f"{W}basedOn"))
        run = _run_delta(style.find(f"{W}rPr"))
        if style_type == "character":
            characters[style_id] = _CharacterStyleDefinition(name, based_on, run)
            continue
        if style_type != "paragraph":
            continue
        if style.get(f"{W}default") == "1":
            default = style_id
        ppr = style.find(f"{W}pPr")
        num_id, num_level = _num_properties(ppr)
        styles[style_id] = _StyleDefinition(
            name=name,
            based_on=based_on,
            contextual_spacing=(
                ppr.find(f"{W}contextualSpacing") is not None if ppr is not None else False
            ),
            alignment=ParagraphAlignment(
                _w_val(ppr.find(f"{W}jc") if ppr is not None else None)
            ),
            spacing=_attributes(ppr.find(f"{W}spacing") if ppr is not None else None),
            indent=_attributes(ppr.find(f"{W}ind") if ppr is not None else None),
            font_half_points=_ooxml_optional_int(style.find(f"{W}rPr/{W}sz"), "val"),
            numbered=ppr is not None and ppr.find(f"{W}numPr") is not None,
            num_id=num_id,
            num_level=num_level,
            outline_level=_ooxml_optional_int(
                ppr.find(f"{W}outlineLvl") if ppr is not None else None,
                "val",
            ),
            run=run,
        )
    default_ppr = root.find(f"{W}docDefaults/{W}pPrDefault/{W}pPr")
    default_rpr = root.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr")
    return _StyleSheet(
        paragraphs=styles,
        characters=characters,
        default_paragraph=default,
        default_alignment=ParagraphAlignment(
            _w_val(default_ppr.find(f"{W}jc") if default_ppr is not None else None)
        ),
        default_spacing=_attributes(
            default_ppr.find(f"{W}spacing") if default_ppr is not None else None
        ),
        default_indent=_attributes(
            default_ppr.find(f"{W}ind") if default_ppr is not None else None
        ),
        default_font_half_points=_ooxml_optional_int(
            root.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr/{W}sz"),
            "val",
        ),
        default_run=_run_delta(default_rpr),
    )


def _ooxml_optional_int(element: ET.Element | None, attribute: str) -> int | None:
    if element is None:
        return None
    raw = element.get(f"{W}{attribute}")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise DocxSourceError(
            f"invalid {element.tag.removeprefix(W)} w:{attribute} value {raw!r}"
        ) from exc


def _default_font_size(style_sheet: _StyleSheet) -> DefaultFontSize:
    half_points = style_sheet.default_font_half_points
    for definition in reversed(style_sheet.paragraph_chain(style_sheet.default_paragraph)):
        if definition.font_half_points is not None:
            half_points = definition.font_half_points
    return (
        ObservedFontSize(half_points)
        if half_points is not None
        else GeometryUnavailable()
    )


def _section_column_width(section: ET.Element) -> int | None:
    page = section.find(f"{W}pgSz")
    margins = section.find(f"{W}pgMar")
    if page is None or margins is None:
        return None
    width = _ooxml_optional_int(page, "w")
    left = _ooxml_optional_int(
        margins,
        "left" if margins.get(f"{W}left") is not None else "start",
    )
    right = _ooxml_optional_int(
        margins,
        "right" if margins.get(f"{W}right") is not None else "end",
    )
    if width is None or left is None or right is None:
        return None
    available = width - left - right
    columns = section.find(f"{W}cols")
    explicit = columns.find(f"{W}col") if columns is not None else None
    if explicit is not None:
        return _ooxml_optional_int(explicit, "w")
    count = _ooxml_optional_int(columns, "num") if columns is not None else 1
    if count is None or count == 1:
        return available
    space = _ooxml_optional_int(columns, "space")
    return (available - (count - 1) * space) // count if space is not None else None


def _document_layout(root: ET.Element, style_sheet: _StyleSheet) -> DocumentLayout:
    """Observe all section widths without promoting partial evidence to a global fact."""
    sections = root.findall(f".//{W}sectPr")
    observations = tuple(_section_column_width(section) for section in sections)
    widths = {width for width in observations if width is not None}
    if widths and any(width is None for width in observations):
        column_width: ColumnWidth = PartiallyObservedColumnWidths(
            tuple(Twips(width) for width in widths)
        )
    elif len(widths) == 1:
        column_width = ObservedColumnWidth(Twips(next(iter(widths))))
    elif widths:
        column_width = HeterogeneousColumnWidths(tuple(Twips(width) for width in widths))
    else:
        column_width = GeometryUnavailable()
    return DocumentLayout(
        column_width=column_width,
        default_font_size=_default_font_size(style_sheet),
    )


def _style_chain(style: str, styles: dict[str, _StyleDefinition]) -> Iterator[_StyleDefinition]:
    seen: set[str] = set()
    current = style
    while current and current not in seen:
        seen.add(current)
        definition = styles.get(current)
        if definition is None:
            return
        yield definition
        current = definition.based_on


def _paragraph_styles(style_sheet: _StyleSheet) -> ParagraphStyles:
    return ParagraphStyles(
        default=style_sheet.default_paragraph,
        numbered=frozenset(
            style
            for style in style_sheet.paragraphs
            if any(
                definition.numbered
                for definition in style_sheet.paragraph_chain(style)
            )
        ),
    )


def paragraph_styles(styles_xml: bytes | None) -> ParagraphStyles:
    """Resolve the style environment shared by every story in one DOCX package."""
    return _paragraph_styles(_style_sheet_xml(styles_xml))


def _resolved_contextual_spacing(
    style: str,
    style_sheet: _StyleSheet,
    *,
    direct: bool,
) -> bool:
    return direct or any(
        definition.contextual_spacing
        for definition in style_sheet.paragraph_chain(style)
    )


def _resolved_spacing(
    style: str,
    style_sheet: _StyleSheet,
    direct: OoxmlAttributes,
) -> OoxmlAttributes:
    values = dict(style_sheet.default_spacing)
    for definition in reversed(style_sheet.paragraph_chain(style)):
        values.update(definition.spacing)
    values.update(direct)
    return tuple(sorted(values.items()))


def _resolved_alignment(
    style: str,
    style_sheet: _StyleSheet,
    direct: ParagraphAlignment,
) -> ParagraphAlignment:
    value = style_sheet.default_alignment
    for definition in reversed(style_sheet.paragraph_chain(style)):
        if definition.alignment.value:
            value = definition.alignment
    return direct if direct.value else value


def _resolved_indent(
    style: str,
    style_sheet: _StyleSheet,
    direct: OoxmlAttributes,
) -> OoxmlAttributes:
    values = dict(style_sheet.default_indent)
    for definition in reversed(style_sheet.paragraph_chain(style)):
        values.update(definition.indent)
    values.update(direct)
    return tuple(sorted(values.items()))


def _lexical_run_inlines(element: ET.Element) -> tuple[SourceInline, ...] | None:
    """Interpret one run-level OOXML token for every paragraph consumer."""
    if element.tag in {f"{W}t", f"{W}delText", f"{_M}t"}:
        return (SourceText(element.text),) if element.text else ()
    if element.tag == f"{W}tab":
        return (SourceText("\t"),)
    if element.tag == f"{W}br":
        return (BreakKind.from_ooxml(element.get(f"{W}type")),)
    if element.tag == f"{W}cr":
        return (BreakKind.LINE,)
    if element.tag == f"{W}lastRenderedPageBreak":
        return (SourceRenderedPageBreak(),)
    if element.tag == f"{W}noBreakHyphen":
        return (SourceText("‑"),)
    if element.tag == f"{W}softHyphen":
        return (SourceText("­"),)
    if element.tag == f"{W}footnoteReference":
        note_id = _ooxml_optional_int(element, "id")
        return () if note_id is None else (
            SourceNoteReference(NoteKind.FOOTNOTE, note_id),
        )
    if element.tag == f"{W}endnoteReference":
        note_id = _ooxml_optional_int(element, "id")
        return () if note_id is None else (
            SourceNoteReference(NoteKind.ENDNOTE, note_id),
        )
    if element.tag == f"{W}sym":
        font = str(element.get(f"{W}font") or "")
        raw = str(element.get(f"{W}char") or "")
        return (SourceSymbol(font, _symbol_character(font, raw), raw),)
    if element.tag == f"{W}instrText":
        return (SourceFieldInstruction(element.text or ""),)
    if element.tag == f"{W}fldChar":
        return (SourceFieldBoundary(str(element.get(f"{W}fldCharType") or "")),)
    if element.tag in {
        f"{W}separator", f"{W}continuationSeparator", f"{W}footnoteRef",
        f"{W}endnoteRef",
    }:
        return ()
    return None


def _paragraph_content(paragraph: ET.Element) -> ParagraphContent:
    """Read display atoms for package operations that already own a ``w:p``.

    This deliberately excludes ``w:pPr``.  A tab stop in paragraph properties
    describes layout; it is not an authored tab in the paragraph's text stream.
    Production ingestion derives the same facts from the rich source grammar and
    does not call this package-operation projection.
    """
    atoms: list[ParagraphAtom] = []

    def append(element: ET.Element) -> None:
        lexical = _lexical_run_inlines(element)
        if lexical is not None:
            atoms.extend(inline_content(lexical).atoms)
            return
        for child in iter_source_children(element):
            if child.tag == f"{W}pPr":
                continue
            if child.tag == f"{W}txbxContent":
                atoms.append(TextAtom(" "))
                for index, nested in enumerate(story_paragraph_elements(child)):
                    if index:
                        atoms.append(TextAtom(" "))
                    atoms.extend(_paragraph_content(nested).atoms)
                atoms.append(TextAtom(" "))
                continue
            append(child)

    append(paragraph)
    return ParagraphContent(tuple(atoms))


_ATOM_MARKUP = frozenset(
    {
        f"{W}p",
        f"{W}r",
        f"{W}t",
        f"{W}br",
        f"{W}cr",
        f"{W}tab",
        f"{W}noBreakHyphen",
        f"{W}softHyphen",
        f"{W}lastRenderedPageBreak",
    }
)
_PROPERTY_CONTAINERS = frozenset({f"{W}pPr", f"{W}rPr"})
_HEADING_STYLE = re.compile(r"(?:Heading\d+|[1-9])")
_BORDER_SIDES = ("top", "bottom", "left", "right")


def _paragraph_payload(paragraph: ET.Element) -> ParagraphPayload:
    """Classify non-text meaning outside the normalized atom stream.

    Direct numbering and section controls are known semantic payload. Inherited
    numbering is resolved separately against ``ParagraphStyles`` because it needs
    package context. Everything outside the tiny atom/wrapper vocabulary is opaque.
    """

    kinds: set[ParagraphPayloadKind] = set()

    def walk(element: ET.Element) -> None:
        for child in element:
            if child.tag == ooxml.MC_ALTERNATE_CONTENT:
                fallback = _compatibility_fallback(child)
                if fallback is None:
                    kinds.add(ParagraphPayloadKind.OPAQUE)
                else:
                    walk(fallback)
                continue
            if child.tag in _PROPERTY_CONTAINERS:
                if child.find(f".//{W}numPr") is not None:
                    kinds.add(ParagraphPayloadKind.DIRECT_NUMBERING)
                if child.find(f".//{W}sectPr") is not None:
                    kinds.add(ParagraphPayloadKind.SECTION)
                continue
            if child.tag not in _ATOM_MARKUP:
                kinds.add(ParagraphPayloadKind.OPAQUE)
                continue
            walk(child)

    walk(paragraph)
    return ParagraphPayload(frozenset(kinds))


def _border_gesture(ppr: ET.Element | None) -> BorderGesture:
    borders = ppr.find(f"{W}pBdr") if ppr is not None else None
    if borders is None:
        return BorderGesture.NONE
    sides = {
        side
        for side in _BORDER_SIDES
        if (element := borders.find(f"{W}{side}")) is not None
        and element.get(f"{W}val", "none") not in {"none", "nil"}
    }
    if not sides:
        return BorderGesture.NONE
    if len(sides) == 4:
        return BorderGesture.BOX
    if sides == {"left"}:
        return BorderGesture.RULE
    return BorderGesture.OTHER


def _paragraph_markers(
    *,
    numbered: bool,
    direct_style: str,
    text: str,
    heading_level: int | None = None,
) -> ParagraphMarkers:
    return ParagraphMarkers(
        numbered=numbered,
        heading_style=(
            heading_level is not None
            or _HEADING_STYLE.fullmatch(direct_style) is not None
        ),
        thematic_marker=is_thematic_marker(text),
    )


def _page_break_before(ppr: ET.Element | None) -> bool:
    element = ppr.find(f"{W}pageBreakBefore") if ppr is not None else None
    return _enabled(element)


def _paragraph_semantic_facts(
    paragraph: ET.Element,
    styles: ParagraphStyles,
) -> tuple[bool, ParagraphPayload]:
    ppr = paragraph.find(f"{W}pPr")
    payload = _paragraph_payload(paragraph)
    direct_style = _w_val(ppr.find(f"{W}pStyle") if ppr is not None else None)
    if styles.is_numbered(direct_style):
        payload = payload.adding(ParagraphPayloadKind.RESOLVED_NUMBERING)
    return _page_break_before(ppr), payload


def _paragraph_semantics(
    paragraph: ET.Element,
    *,
    styles: ParagraphStyles,
    content: ParagraphContent,
) -> ParagraphSemantics:
    """Combine a caller-owned content projection with non-content facts."""
    page_break_before, payload = _paragraph_semantic_facts(paragraph, styles)
    return ParagraphSemantics(
        content=content,
        page_break_before=page_break_before,
        payload=payload,
    )


def analyze_paragraph(
    paragraph: ET.Element,
    *,
    styles: ParagraphStyles,
) -> ParagraphSemantics:
    """Project one paragraph for a package-mutation workflow.

    Canonical ingestion uses :func:`read`, whose rich grammar owns content.
    This narrower helper exists for tools that already hold mutable XML nodes.
    """
    return _paragraph_semantics(
        paragraph,
        styles=styles,
        content=_paragraph_content(paragraph),
    )


def paragraph_text(paragraph: ET.Element) -> str:
    """Local reading text for consumers that do not interpret disposition."""
    return _paragraph_content(paragraph).reading.strip()


def body_paragraph_elements(body: ET.Element) -> tuple[ET.Element, ...]:
    """Top-level paragraphs in the canonical body order (tables excluded)."""
    return tuple(event for event in _body_events(body) if event is not None)


def _body_events(body: ET.Element) -> Iterator[ET.Element | None]:
    """Yield top-level paragraphs and ``None`` for table boundaries."""
    for child in iter_source_children(body):
        if child.tag == f"{W}p":
            yield child
        elif child.tag == f"{W}tbl":
            yield None
        elif child.tag == f"{W}sdt":
            content = next(
                (
                    candidate
                    for candidate in iter_source_children(child)
                    if candidate.tag == f"{W}sdtContent"
                ),
                None,
            )
            if content is not None:
                yield from _body_events(content)


def _paragraphs_join(left: SourceParagraph, right: SourceParagraph) -> bool:
    if left.segment != right.segment:
        return False
    if not (left.contextual_spacing and right.contextual_spacing):
        return False
    if left.resolved_style != right.resolved_style:
        return False
    if not left.text or not right.text:
        return False
    if left.heading or left.thematic:
        return False
    if right.heading or right.thematic:
        return False
    if left.indent_departure or right.indent_departure:
        return False
    if left.border is not BorderGesture.NONE or right.border is not BorderGesture.NONE:
        return False
    if left.alignment != right.alignment:
        return False
    return left.layout.spacing.is_real("after") or right.layout.spacing.is_real("before")


def _direction_indents(paragraphs: tuple[SourceParagraph, ...]) -> tuple[SourceParagraph, ...]:
    body = [p for p in paragraphs if not p.numbered and p.text]
    counts: dict[ParagraphIndent, int] = {}
    for paragraph in body:
        indent = paragraph.layout.indent
        counts[indent] = counts.get(indent, 0) + 1
    dominant = max(counts, key=lambda signature: counts[signature], default=ParagraphIndent())
    return tuple(
        replace(
            paragraph,
            indent_departure=(
                bool(paragraph.layout.indent.attributes)
                and paragraph.layout.indent != dominant
            ),
        )
        for paragraph in paragraphs
    )


def _assign_visual_groups(paragraphs: tuple[SourceParagraph, ...]) -> tuple[SourceParagraph, ...]:
    eligible = [
        paragraph
        for paragraph in paragraphs
        if not paragraph.numbered
        and paragraph.disposition is not ParagraphDisposition.PAGINATION_ONLY
    ]
    groups: dict[ParagraphOrdinal, VisualLineationGroup] = {}
    next_id = 1
    run: list[SourceParagraph] = []

    def finish() -> None:
        nonlocal next_id
        if len(run) <= 1:
            return
        group = VisualLineationGroup(next_id)
        next_id += 1
        for paragraph in run:
            groups[paragraph.ordinal] = group

    if eligible:
        run = [eligible[0]]
        for previous, current in pairwise(eligible):
            if _paragraphs_join(previous, current):
                run.append(current)
                continue
            finish()
            run = [current]
        finish()
    return tuple(
        replace(paragraph, visual_group=groups.get(paragraph.ordinal))
        for paragraph in paragraphs
    )


@dataclass(frozen=True, slots=True)
class _NumberLevel:
    ordered: bool
    start: int
    text: str
    format: str


@dataclass(frozen=True, slots=True)
class _NumberingCatalog:
    levels: dict[tuple[int, int], _NumberLevel] = field(default_factory=dict)

    def resolve(self, num_id: int, level: int) -> SourceNumbering:
        value = self.levels.get(
            (num_id, level),
            _NumberLevel(ordered=True, start=1, text="", format="decimal"),
        )
        return SourceNumbering(
            num_id=num_id,
            level=level,
            ordered=value.ordered,
            start=value.start,
            level_text=value.text,
            format=value.format,
        )


def _numbering_catalog(zf: zipfile.ZipFile) -> _NumberingCatalog:
    try:
        root = ET.fromstring(zf.read("word/numbering.xml"))
    except KeyError:
        return _NumberingCatalog()

    abstract: dict[int, dict[int, _NumberLevel]] = {}
    for definition in root.findall(f"{W}abstractNum"):
        abstract_id = _ooxml_optional_int(definition, "abstractNumId")
        if abstract_id is None:
            continue
        levels: dict[int, _NumberLevel] = {}
        for level in definition.findall(f"{W}lvl"):
            index = _ooxml_optional_int(level, "ilvl")
            if index is None:
                continue
            fmt = _w_val(level.find(f"{W}numFmt")) or "decimal"
            start = _ooxml_optional_int(level.find(f"{W}start"), "val") or 1
            levels[index] = _NumberLevel(
                ordered=fmt not in {"bullet", "none"},
                start=start,
                text=_w_val(level.find(f"{W}lvlText")),
                format=fmt,
            )
        abstract[abstract_id] = levels

    resolved: dict[tuple[int, int], _NumberLevel] = {}
    for instance in root.findall(f"{W}num"):
        num_id = _ooxml_optional_int(instance, "numId")
        abstract_id = _ooxml_optional_int(instance.find(f"{W}abstractNumId"), "val")
        if num_id is None or abstract_id is None:
            continue
        instance_levels = dict(abstract.get(abstract_id, {}))
        for override in instance.findall(f"{W}lvlOverride"):
            level_index = _ooxml_optional_int(override, "ilvl")
            if level_index is None:
                continue
            embedded = override.find(f"{W}lvl")
            if embedded is not None:
                fmt = _w_val(embedded.find(f"{W}numFmt")) or "decimal"
                start = _ooxml_optional_int(embedded.find(f"{W}start"), "val") or 1
                instance_levels[level_index] = _NumberLevel(
                    ordered=fmt not in {"bullet", "none"},
                    start=start,
                    text=_w_val(embedded.find(f"{W}lvlText")),
                    format=fmt,
                )
            if (start_override := _ooxml_optional_int(
                override.find(f"{W}startOverride"), "val"
            )) is not None:
                previous = instance_levels.get(
                    level_index,
                    _NumberLevel(ordered=True, start=1, text="", format="decimal"),
                )
                instance_levels[level_index] = replace(previous, start=start_override)
        for level_index, level in instance_levels.items():
            resolved[(num_id, level_index)] = level
    return _NumberingCatalog(resolved)


def _resolved_numbering(
    ppr: ET.Element | None,
    resolved_style: str,
    style_sheet: _StyleSheet,
    numbering: _NumberingCatalog,
) -> SourceNumbering | None:
    num_id, level = _num_properties(ppr)
    if num_id is None:
        for definition in style_sheet.paragraph_chain(resolved_style):
            if definition.num_id is not None:
                num_id = definition.num_id
                level = definition.num_level
                break
    if num_id is None or num_id == 0:
        return None
    return numbering.resolve(num_id, level or 0)


_HEADING_NAME = re.compile(r"(?:heading|заголовок)\s*([1-9])", re.IGNORECASE)


def _heading_level(
    ppr: ET.Element | None,
    resolved_style: str,
    style_sheet: _StyleSheet,
) -> int | None:
    direct_outline = _ooxml_optional_int(
        ppr.find(f"{W}outlineLvl") if ppr is not None else None,
        "val",
    )
    if direct_outline is not None and 0 <= direct_outline <= 5:
        return direct_outline + 1
    candidates = [resolved_style]
    for definition in style_sheet.paragraph_chain(resolved_style):
        if definition.outline_level is not None and 0 <= definition.outline_level <= 5:
            return definition.outline_level + 1
        candidates.append(definition.name)
    for candidate in candidates:
        if match := _HEADING_NAME.fullmatch(candidate.replace("_", " ")):
            return int(match.group(1))
        if candidate in "123456" and len(candidate) == 1:
            return int(candidate)
    return None


def _resolve_presentation(
    ppr: ET.Element | None,
    style_sheet: _StyleSheet,
    numbering: _NumberingCatalog,
) -> SourceParagraphPresentation:
    direct_style = _w_val(ppr.find(f"{W}pStyle") if ppr is not None else None)
    resolved_style = direct_style or style_sheet.default_paragraph
    return SourceParagraphPresentation(
        resolved_style=resolved_style,
        direct_style=direct_style,
        layout=ParagraphLayout(
            source_alignment=_resolved_alignment(
                resolved_style,
                style_sheet,
                ParagraphAlignment(_w_val(
                    ppr.find(f"{W}jc") if ppr is not None else None
                )),
            ),
            spacing=ParagraphSpacing(_resolved_spacing(
                resolved_style,
                style_sheet,
                _attributes(ppr.find(f"{W}spacing") if ppr is not None else None),
            )),
            indent=ParagraphIndent(_resolved_indent(
                resolved_style,
                style_sheet,
                _attributes(ppr.find(f"{W}ind") if ppr is not None else None),
            )),
        ),
        contextual_spacing=_resolved_contextual_spacing(
            resolved_style,
            style_sheet,
            direct=(
                ppr.find(f"{W}contextualSpacing") is not None
                if ppr is not None
                else False
            ),
        ),
        border=_border_gesture(ppr),
        numbering=_resolved_numbering(
            ppr,
            resolved_style,
            style_sheet,
            numbering,
        ),
        heading_level=_heading_level(ppr, resolved_style, style_sheet),
    )


def _apply_run_delta(
    values: dict[str, object],
    delta: _RunPropertyDelta,
) -> None:
    for name in ("bold", "italic", "strike", "underline", "small_caps", "rtl"):
        if (value := getattr(delta, name)) is not None:
            values[name] = value
    if delta.vertical_align is not None:
        values["vertical_align"] = delta.vertical_align


def _apply_style_run_delta(
    values: dict[str, object],
    delta: _RunPropertyDelta,
) -> None:
    """Apply OOXML toggle properties from a style hierarchy.

    A true toggle in a style reverses its inherited value; false leaves the
    inherited value alone.  Direct formatting is absolute and continues to use
    ``_apply_run_delta``.
    """
    for name in ("bold", "italic", "strike", "underline", "small_caps", "rtl"):
        if getattr(delta, name) is True:
            values[name] = not bool(values[name])
    if delta.vertical_align is not None:
        values["vertical_align"] = delta.vertical_align


def _character_style_chain(
    style: str,
    styles: dict[str, _CharacterStyleDefinition],
) -> Iterator[_CharacterStyleDefinition]:
    seen: set[str] = set()
    current = style
    while current and current not in seen:
        seen.add(current)
        definition = styles.get(current)
        if definition is None:
            return
        yield definition
        current = definition.based_on


def _resolved_run_properties(
    rpr: ET.Element | None,
    *,
    paragraph_style: str,
    style_sheet: _StyleSheet,
) -> SourceRunProperties:
    values: dict[str, object] = {
        "bold": False,
        "italic": False,
        "strike": False,
        "underline": False,
        "small_caps": False,
        "rtl": False,
        "vertical_align": "",
    }
    _apply_run_delta(values, style_sheet.default_run)
    for definition in reversed(style_sheet.paragraph_chain(paragraph_style)):
        _apply_style_run_delta(values, definition.run)

    # ``w:pPr/w:rPr`` formats the paragraph mark, not every run in the
    # paragraph.  Only document defaults, paragraph/character styles, and this
    # run's own ``w:rPr`` participate in readable run formatting.

    direct_style = _w_val(rpr.find(f"{W}rStyle") if rpr is not None else None)
    character_names: list[str] = []
    for definition in reversed(style_sheet.character_chain(direct_style)):
        character_names.append(definition.name)
        _apply_style_run_delta(values, definition.run)
    _apply_run_delta(values, _run_delta(rpr))
    vertical = str(values["vertical_align"])
    style_names = " ".join([direct_style, *character_names]).casefold()
    return SourceRunProperties(
        style=direct_style,
        bold=bool(values["bold"]),
        italic=bool(values["italic"]),
        strike=bool(values["strike"]),
        underline=bool(values["underline"]),
        small_caps=bool(values["small_caps"]),
        superscript=vertical == "superscript",
        subscript=vertical == "subscript",
        rtl=bool(values["rtl"]),
        code=any(token in style_names for token in ("code", "verbatim", "source")),
    )


def _relationship_map(
    zf: zipfile.ZipFile,
    source_part: str,
) -> tuple[dict[str, ooxml.OoxmlRelationship], tuple[str, ...]]:
    rels_name = ooxml.relationships_part_for(source_part)
    try:
        root = ET.fromstring(zf.read(rels_name))
    except KeyError:
        return {}, ()
    result = ooxml.read_ooxml_relationships(root, rels_name, set(zf.namelist()))
    return result.relationships, result.issues


_A = f"{{{ooxml.A_NS}}}"
_ASVG = f"{{{ooxml.ASVG_NS}}}"
_V = f"{{{ooxml.V_NS}}}"
_O = f"{{{ooxml.O_NS}}}"
_M = f"{{{ooxml.M_NS}}}"
_IMAGE_REL = f"{ooxml.R_NS}/image"


def _hoist_field_controls(
    inlines: tuple[SourceInline, ...],
) -> tuple[SourceInline, ...]:
    """Lift field controls out of their formatting runs without losing results."""
    out: list[SourceInline] = []
    for inline in inlines:
        if not isinstance(inline, SourceRun):
            out.append(inline)
            continue
        buffered: list[SourceInline] = []
        for child in inline.children:
            if isinstance(child, SourceFieldInstruction | SourceFieldBoundary):
                if buffered:
                    out.append(SourceRun(tuple(buffered), inline.properties))
                    buffered.clear()
                out.append(child)
            else:
                buffered.append(child)
        if buffered:
            out.append(SourceRun(tuple(buffered), inline.properties))
    return tuple(out)


class _RichReader:
    def __init__(
        self,
        zf: zipfile.ZipFile,
        *,
        style_sheet: _StyleSheet,
        numbering: _NumberingCatalog,
        body_seeds: dict[ET.Element, _BodyParagraphSeed],
    ) -> None:
        self.zf = zf
        self.style_sheet = style_sheet
        self.numbering = numbering
        self.body_seeds = body_seeds
        self.relationships: dict[str, dict[str, ooxml.OoxmlRelationship]] = {}
        self.media: dict[str, SourceMedia] = {}
        self.diagnostics: list[SourceDiagnostic] = []

    def _selected_children(
        self,
        parent: ET.Element,
        story: StoryPart,
        path: tuple[int, ...],
    ) -> Iterator[tuple[tuple[int, ...], ET.Element]]:
        for index, child in enumerate(parent):
            child_path = (*path, index)
            if child.tag != ooxml.MC_ALTERNATE_CONTENT:
                yield child_path, child
                continue
            fallback = _compatibility_fallback(child)
            if fallback is None:
                self.diagnostics.append(SourceDiagnostic(
                    SourceDiagnosticCode.COMPATIBILITY_UNSUPPORTED,
                    "mc:AlternateContent has no fallback branch",
                    SourceAddress(story, child_path),
                ))
                continue
            choices = child.findall(f"{{{ooxml.MC_NS}}}Choice")
            requirements = ", ".join(
                choice.get("Requires", "") for choice in choices if choice.get("Requires")
            )
            self.diagnostics.append(SourceDiagnostic(
                SourceDiagnosticCode.COMPATIBILITY_FALLBACK,
                f"selected fallback branch; unclaimed requirements: {requirements or 'none'}",
                SourceAddress(story, child_path),
            ))
            yield from self._selected_children(
                fallback,
                story,
                (*child_path, tuple(child).index(fallback)),
            )

    def _rels(self, part: str) -> dict[str, ooxml.OoxmlRelationship]:
        if part not in self.relationships:
            relationships, issues = _relationship_map(self.zf, part)
            self.relationships[part] = relationships
            self.diagnostics.extend(
                SourceDiagnostic(SourceDiagnosticCode.RELATIONSHIP, issue)
                for issue in issues
            )
        return self.relationships[part]

    def _selected_descendants(
        self,
        parent: ET.Element,
        story: StoryPart,
        path: tuple[int, ...],
    ) -> Iterator[tuple[tuple[int, ...], ET.Element]]:
        """Walk the canonical compatibility view with exact package paths."""
        for child_path, child in self._selected_children(parent, story, path):
            yield child_path, child
            yield from self._selected_descendants(child, story, child_path)

    def _relationship(
        self,
        part: str,
        rel_id: str,
        address: SourceAddress,
    ) -> ooxml.OoxmlRelationship | None:
        relationship = self._rels(part).get(rel_id)
        if relationship is None:
            self.diagnostics.append(SourceDiagnostic(
                SourceDiagnosticCode.RELATIONSHIP_MISSING,
                f"{part} references missing relationship {rel_id}",
                address,
            ))
        return relationship

    def _image(
        self,
        part: str,
        rel_id: str,
        alt: str,
        address: SourceAddress,
    ) -> SourceImage:
        relationship = self._relationship(part, rel_id, address)
        if relationship is None:
            return SourceImage(rel_id, "", alt)
        if relationship.rel_type != _IMAGE_REL:
            self.diagnostics.append(SourceDiagnostic(
                SourceDiagnosticCode.IMAGE_RELATIONSHIP,
                f"{rel_id} has relationship type {relationship.rel_type!r}, not image",
                address,
            ))
            return SourceImage(rel_id, relationship.target, alt)
        media_part = relationship.resolved_target
        if media_part is not None and media_part not in self.media:
            self.media[media_part] = SourceMedia(media_part, self.zf.read(media_part))
        target = relationship.target if relationship.target_mode == "External" else (media_part or "")
        return SourceImage(rel_id, target, alt, media_part)

    @staticmethod
    def _drawing_alt(elements: Iterator[ET.Element]) -> str:
        descriptions: list[str] = []
        titles: list[str] = []
        for descendant in elements:
            if descendant.tag in ooxml.DRAWING_METADATA_ELEMENT_TAGS:
                descriptions.append(str(descendant.get("descr") or ""))
                titles.append(str(descendant.get("title") or ""))
            elif descendant.tag == f"{_V}shape":
                descriptions.append(str(descendant.get("alt") or ""))
                titles.append(str(descendant.get("title") or ""))
        return next(
            (
                value
                for value in (*descriptions, *titles)
                if value.strip()
            ),
            "",
        )

    def _drawing(
        self,
        element: ET.Element,
        *,
        story: StoryPart,
        part: str,
        path: tuple[int, ...],
    ) -> tuple[SourceInline, ...]:
        out: list[SourceInline] = []
        descendants = tuple(self._selected_descendants(element, story, path))
        alt = self._drawing_alt(descendant for _, descendant in descendants)
        svg_relationships: list[tuple[tuple[int, ...], str]] = []
        fallback_relationships: list[tuple[tuple[int, ...], str]] = []
        for descendant_path, descendant in descendants:
            if descendant.tag == f"{_A}blip":
                rel_id = descendant.get(f"{ooxml.R}embed") or descendant.get(f"{ooxml.R}link")
            elif descendant.tag == f"{_ASVG}svgBlip":
                rel_id = descendant.get(f"{ooxml.R}embed") or descendant.get(f"{ooxml.R}link")
            elif descendant.tag == f"{_V}imagedata":
                rel_id = descendant.get(f"{ooxml.R}id")
            else:
                continue
            if not rel_id:
                continue
            target = (
                svg_relationships
                if descendant.tag == f"{_ASVG}svgBlip"
                else fallback_relationships
            )
            target.append((descendant_path, rel_id))
        seen_relationships: set[str] = set()
        for descendant_path, rel_id in svg_relationships or fallback_relationships:
            if rel_id in seen_relationships:
                continue
            seen_relationships.add(rel_id)
            out.append(self._image(
                part,
                rel_id,
                alt,
                SourceAddress(story, descendant_path),
            ))
        for text_box_path, text_box in descendants:
            if text_box.tag != f"{W}txbxContent":
                continue
            out.append(SourceTextBox(self.blocks(
                text_box,
                story=story,
                part=part,
                path=text_box_path,
            )))
        if not out and any(
            descendant.tag == f"{_V}rect"
            and descendant.get(f"{_O}hr") in {"t", "true", "1"}
            for _, descendant in descendants
        ):
            out.append(SourceHorizontalRule())
        if not out:
            text = self._readable_text(element)
            out.append(SourceUnknownInline(ooxml.local_name(element.tag), text))
        return tuple(out)

    def _readable_text(self, element: ET.Element) -> str:
        pieces = [
            descendant.text or ""
            for descendant in element.iter()
            if descendant.tag in {f"{W}t", f"{_M}t"}
        ]
        return " ".join("".join(pieces).split())

    def _run(
        self,
        element: ET.Element,
        *,
        story: StoryPart,
        part: str,
        path: tuple[int, ...],
        paragraph_style: str,
    ) -> SourceRun:
        rpr = element.find(f"{W}rPr")
        properties = _resolved_run_properties(
            rpr,
            paragraph_style=paragraph_style,
            style_sheet=self.style_sheet,
        )
        children: list[SourceInline] = []
        for child_path, child in self._selected_children(element, story, path):
            if child.tag == f"{W}rPr":
                continue
            lexical = _lexical_run_inlines(child)
            if lexical is not None:
                children.extend(lexical)
            elif child.tag in {f"{W}drawing", f"{W}pict", f"{W}object"}:
                children.extend(self._drawing(
                    child, story=story, part=part, path=child_path
                ))
            else:
                children.append(SourceUnknownInline(
                    ooxml.local_name(child.tag), self._readable_text(child)
                ))
        return SourceRun(tuple(children), properties)

    def _inline_container(
        self,
        element: ET.Element,
        *,
        story: StoryPart,
        part: str,
        path: tuple[int, ...],
        paragraph_style: str,
    ) -> tuple[SourceInline, ...]:
        out: list[SourceInline] = []
        for child_path, child in self._selected_children(element, story, path):
            if child.tag == f"{W}r":
                out.append(self._run(
                    child,
                    story=story,
                    part=part,
                    path=child_path,
                    paragraph_style=paragraph_style,
                ))
            elif child.tag == f"{W}hyperlink":
                rel_id = child.get(f"{ooxml.R}id")
                anchor = str(child.get(f"{W}anchor") or "")
                target = f"#{anchor}" if anchor else ""
                if rel_id:
                    relationship = self._relationship(
                        part, rel_id, SourceAddress(story, child_path)
                    )
                    if relationship is not None:
                        target = relationship.target
                out.append(SourceHyperlink(
                    self._inline_container(
                        child,
                        story=story,
                        part=part,
                        path=child_path,
                        paragraph_style=paragraph_style,
                    ),
                    target,
                    rel_id,
                ))
            elif child.tag == f"{W}fldSimple":
                nested = self._inline_container(
                    child,
                    story=story,
                    part=part,
                    path=child_path,
                    paragraph_style=paragraph_style,
                )
                instruction = str(child.get(f"{W}instr") or "")
                field = SourceField(instruction, nested)
                out.append(
                    SourceHyperlink(nested, target)
                    if (target := field.hyperlink_target) is not None
                    else field
                )
            elif child.tag in {f"{W}drawing", f"{W}pict", f"{W}object"}:
                out.extend(self._drawing(
                    child, story=story, part=part, path=child_path
                ))
            elif child.tag in {
                f"{W}sdt", f"{W}sdtContent", f"{W}smartTag", f"{W}customXml",
                f"{W}ins", f"{W}moveTo",
            }:
                out.extend(self._inline_container(
                    child,
                    story=story,
                    part=part,
                    path=child_path,
                    paragraph_style=paragraph_style,
                ))
            elif child.tag in {
                f"{W}bookmarkStart", f"{W}bookmarkEnd", f"{W}proofErr",
                f"{W}permStart", f"{W}permEnd", f"{W}commentRangeStart",
                f"{W}commentRangeEnd",
            }:
                continue
            elif child.tag in {f"{_M}oMath", f"{_M}oMathPara"}:
                out.append(SourceUnknownInline("math", self._readable_text(child)))
            elif child.tag == f"{W}pPr":
                continue
            else:
                out.append(SourceUnknownInline(
                    ooxml.local_name(child.tag), self._readable_text(child)
                ))
        return _hoist_field_controls(tuple(out))

    def _paragraph_block(
        self,
        element: ET.Element,
        *,
        story: StoryPart,
        part: str,
        path: tuple[int, ...],
    ) -> SourceParagraphBlock:
        ppr = element.find(f"{W}pPr")
        seed = self.body_seeds.get(element)
        presentation = (
            seed.presentation
            if seed is not None
            else _resolve_presentation(ppr, self.style_sheet, self.numbering)
        )
        try:
            inlines = self._inline_container(
                element,
                story=story,
                part=part,
                path=path,
                paragraph_style=presentation.resolved_style,
            )
        except DocxSourceError as exc:
            location = (
                f"paragraph {seed.ordinal.value}"
                if seed is not None
                else f"{story.value} source unit {path}"
            )
            raise DocxSourceError(f"{location}: {exc}") from exc
        return SourceParagraphBlock(
            address=SourceAddress(story, path),
            inlines=inlines,
            body_ordinal=seed.ordinal if seed is not None else None,
            presentation=presentation,
        )

    def _table(
        self,
        element: ET.Element,
        *,
        story: StoryPart,
        part: str,
        path: tuple[int, ...],
    ) -> SourceTableBlock:
        address = SourceAddress(story, path)
        table_properties = element.find(f"{W}tblPr")
        caption = (
            table_properties.find(f"{W}tblCaption")
            if table_properties is not None
            else None
        )
        if caption is not None:
            self.diagnostics.append(SourceDiagnostic(
                SourceDiagnosticCode.TABLE_CAPTION_UNSUPPORTED,
                f"table caption {str(caption.get(f'{W}val') or '')!r} is not represented",
                address,
            ))
        rows: list[SourceTableRow] = []
        for row_path, row in self._selected_children(element, story, path):
            if row.tag != f"{W}tr":
                continue
            cells: list[SourceTableCell] = []
            for cell_path, cell in self._selected_children(row, story, row_path):
                if cell.tag != f"{W}tc":
                    continue
                tc_pr = cell.find(f"{W}tcPr")
                if tc_pr is not None and tc_pr.find(f"{W}vMerge") is not None:
                    self.diagnostics.append(SourceDiagnostic(
                        SourceDiagnosticCode.TABLE_VERTICAL_MERGE_UNSUPPORTED,
                        "vertically merged table cells are not represented",
                        SourceAddress(story, cell_path),
                    ))
                if cell.find(f"{W}tbl") is not None:
                    self.diagnostics.append(SourceDiagnostic(
                        SourceDiagnosticCode.TABLE_NESTED_UNSUPPORTED,
                        "nested tables are not represented in Pancratius table IR",
                        SourceAddress(story, cell_path),
                    ))
                column_span = _ooxml_optional_int(
                    tc_pr.find(f"{W}gridSpan") if tc_pr is not None else None,
                    "val",
                ) or 1
                cells.append(SourceTableCell(
                    address=SourceAddress(story, cell_path),
                    blocks=self.blocks(
                        cell, story=story, part=part, path=cell_path
                    ),
                    column_span=column_span,
                ))
            rows.append(SourceTableRow(tuple(cells)))
        return SourceTableBlock(address, tuple(rows))

    def blocks(
        self,
        parent: ET.Element,
        *,
        story: StoryPart,
        part: str,
        path: tuple[int, ...] = (),
    ) -> tuple[SourceBlock, ...]:
        blocks: list[SourceBlock] = []
        for child_path, child in self._selected_children(parent, story, path):
            if child.tag == f"{W}p":
                blocks.append(self._paragraph_block(
                    child, story=story, part=part, path=child_path
                ))
            elif child.tag == f"{W}tbl":
                blocks.append(self._table(
                    child, story=story, part=part, path=child_path
                ))
            elif child.tag == f"{W}sdt":
                content_entry = next(
                    (
                        (content_path, candidate)
                        for content_path, candidate in self._selected_children(
                            child, story, child_path
                        )
                        if candidate.tag == f"{W}sdtContent"
                    ),
                    None,
                )
                if content_entry is None:
                    content_blocks: tuple[SourceBlock, ...] = ()
                else:
                    content_path, content = content_entry
                    content_blocks = self.blocks(
                        content,
                        story=story,
                        part=part,
                        path=content_path,
                    )
                blocks.append(SourceContentControl(
                    SourceAddress(story, child_path),
                    content_blocks,
                ))
            elif child.tag in {
                f"{W}tcPr", f"{W}sectPr", f"{W}sdtPr", f"{W}sdtEndPr",
                f"{W}bookmarkStart", f"{W}bookmarkEnd", f"{W}proofErr",
            }:
                continue
            else:
                blocks.append(SourceUnknownBlock(
                    SourceAddress(story, child_path),
                    ooxml.local_name(child.tag),
                    self._readable_text(child),
                ))
        return tuple(blocks)

    def note_definitions(self) -> tuple[SourceNoteDefinition, ...]:
        definitions: list[SourceNoteDefinition] = []
        for kind, story in (
            (NoteKind.FOOTNOTE, StoryPart.FOOTNOTES),
            (NoteKind.ENDNOTE, StoryPart.ENDNOTES),
        ):
            try:
                root = ET.fromstring(self.zf.read(story.value))
            except KeyError:
                continue
            note_tag = f"{W}{kind.value}"
            for note_path, note in self._selected_children(root, story, ()):
                if note.tag != note_tag:
                    continue
                note_id = _ooxml_optional_int(note, "id")
                note_type = str(note.get(f"{W}type") or "")
                if note_id is None or note_id < 0 or note_type in {
                    "separator", "continuationSeparator", "continuationNotice"
                }:
                    continue
                definitions.append(SourceNoteDefinition(
                    kind,
                    note_id,
                    _resolve_story_fields(
                        self.blocks(
                            note,
                            story=story,
                            part=story.value,
                            path=note_path,
                        ),
                        self.diagnostics,
                    ),
                ))
        return tuple(definitions)


@dataclass(slots=True)
class _OpenField:
    field_id: int
    address: SourceAddress
    instruction: list[str] = field(default_factory=list)
    separated: bool = False


class _FieldResolver:
    """Resolve complex fields after rich blocks establish story order.

    Word permits a field's begin, displayed result, and end to occupy different
    paragraphs.  Resolving at the story boundary preserves those paragraph
    identities while giving every result fragment the same typed field identity.
    """

    def __init__(self, diagnostics: list[SourceDiagnostic]) -> None:
        self.diagnostics = diagnostics
        self.next_id = 1

    def _id(self) -> int:
        value = self.next_id
        self.next_id += 1
        return value

    def _diagnostic(
        self,
        code: SourceDiagnosticCode,
        message: str,
        address: SourceAddress,
    ) -> None:
        self.diagnostics.append(SourceDiagnostic(code, message, address))

    def _incomplete(self, stack: list[_OpenField]) -> None:
        for active in stack:
            self._diagnostic(
                SourceDiagnosticCode.FIELD_INCOMPLETE,
                f"complex field {active.field_id} has no closing boundary",
                active.address,
            )

    def _container_inlines(
        self,
        inlines: tuple[SourceInline, ...],
        address: SourceAddress,
    ) -> tuple[SourceInline, ...]:
        stack: list[_OpenField] = []
        resolved = self._inlines(inlines, stack, address)
        self._incomplete(stack)
        return resolved

    def _nested(self, inline: SourceInline, address: SourceAddress) -> SourceInline:
        match inline:
            case SourceRun(children=children):
                nested = tuple(self._nested(child, address) for child in children)
                return inline if nested == children else replace(inline, children=nested)
            case SourceHyperlink(children=children):
                nested = self._container_inlines(children, address)
                return inline if nested == children else replace(inline, children=nested)
            case SourceField(instruction=instruction, children=children, field_id=field_id):
                nested = self._container_inlines(children, address)
                resolved_id = field_id if field_id is not None else self._id()
                if nested == children and resolved_id == field_id:
                    return inline
                return SourceField(
                    instruction,
                    nested,
                    resolved_id,
                )
            case SourceTextBox(blocks=blocks):
                nested = self.container(blocks)
                return inline if nested == blocks else SourceTextBox(nested)
            case (
                SourceText()
                | BreakKind.LINE
                | BreakKind.PAGE
                | BreakKind.COLUMN
                | SourceImage()
                | SourceNoteReference()
                | SourceSymbol()
                | SourceRenderedPageBreak()
                | SourceHorizontalRule()
                | SourceFieldInstruction()
                | SourceFieldBoundary()
                | SourceUnknownInline()
            ):
                return inline
            case _ as unreachable:
                assert_never(unreachable)

    def _inlines(
        self,
        inlines: tuple[SourceInline, ...],
        stack: list[_OpenField],
        address: SourceAddress,
    ) -> tuple[SourceInline, ...]:
        out: list[SourceInline] = []
        for inline in inlines:
            match inline:
                case SourceFieldBoundary(kind="begin"):
                    stack.append(_OpenField(self._id(), address))
                case SourceFieldInstruction(value=value) if stack:
                    if stack[-1].separated:
                        self._diagnostic(
                            SourceDiagnosticCode.FIELD_INSTRUCTION_IN_RESULT,
                            "field instruction appeared after its result separator",
                            address,
                        )
                    else:
                        stack[-1].instruction.append(value)
                case SourceFieldBoundary(kind="separate") if stack:
                    stack[-1].separated = True
                case SourceFieldBoundary(kind="end") if stack:
                    stack.pop()
                case SourceFieldInstruction() | SourceFieldBoundary():
                    self._diagnostic(
                        SourceDiagnosticCode.FIELD_CONTROL_UNMATCHED,
                        "complex field control has no matching field boundary",
                        address,
                    )
                    out.append(inline)
                case _:
                    if stack and any(not active.separated for active in stack):
                        continue
                    resolved = self._nested(inline, address)
                    for active in reversed(stack):
                        resolved = SourceField(
                            "".join(active.instruction).strip(),
                            (resolved,),
                            active.field_id,
                        )
                    out.append(resolved)
        return tuple(out)

    def _flow(
        self,
        blocks: tuple[SourceBlock, ...],
        stack: list[_OpenField],
    ) -> tuple[SourceBlock, ...]:
        out: list[SourceBlock] = []
        for block in blocks:
            match block:
                case SourceParagraphBlock(address=address, inlines=inlines):
                    resolved = self._inlines(inlines, stack, address)
                    field_kinds = set(block.field_kinds)
                    field_kinds.update(
                        item.kind
                        for item in walk_source_inlines(resolved)
                        if isinstance(item, SourceField) and item.kind
                    )
                    field_kinds.update(
                        kind
                        for active in stack
                        if (kind := _field_kind("".join(active.instruction)))
                    )
                    out.append(
                        block
                        if resolved == inlines and field_kinds == block.field_kinds
                        else replace(
                            block,
                            inlines=resolved,
                            field_kinds=frozenset(field_kinds),
                        )
                    )
                case SourceContentControl(address=address, blocks=children):
                    resolved = self._flow(children, stack)
                    out.append(
                        block if resolved == children else SourceContentControl(address, resolved)
                    )
                case SourceTableBlock(address=address, rows=rows):
                    resolved_rows = tuple(
                        SourceTableRow(tuple(
                            (
                                cell
                                if (resolved := self.container(cell.blocks)) == cell.blocks
                                else replace(cell, blocks=resolved)
                            )
                            for cell in row.cells
                        ))
                        for row in rows
                    )
                    out.append(
                        block
                        if resolved_rows == rows
                        else SourceTableBlock(address, resolved_rows)
                    )
                case SourceUnknownBlock():
                    out.append(block)
                case _ as unreachable:
                    assert_never(unreachable)
        return tuple(out)

    def container(self, blocks: tuple[SourceBlock, ...]) -> tuple[SourceBlock, ...]:
        if not any(
            isinstance(
                inline,
                SourceFieldInstruction | SourceFieldBoundary | SourceField,
            )
            for block in walk_source_blocks(blocks)
            if isinstance(block, SourceParagraphBlock)
            for inline in walk_source_inlines(block.inlines)
        ):
            return blocks
        stack: list[_OpenField] = []
        resolved = self._flow(blocks, stack)
        self._incomplete(stack)
        return resolved


def _resolve_story_fields(
    blocks: tuple[SourceBlock, ...],
    diagnostics: list[SourceDiagnostic],
) -> tuple[SourceBlock, ...]:
    return _FieldResolver(diagnostics).container(blocks)


def _finalize_body_paragraphs(
    seeds: tuple[_BodyParagraphSeed, ...],
    body: tuple[SourceBlock, ...],
) -> tuple[tuple[SourceParagraph, ...], tuple[SourceBlock, ...]]:
    """Make the rich body the sole owner of paragraph content.

    Paragraph layout and style resolution need the top-level ``w:p`` pass, while
    fields, compatibility markup, and text boxes need the rich grammar. Joining
    those facts by element identity is deterministic. A final paragraph is only
    constructed here, so no public source value can contain placeholder content.
    """
    by_ordinal: dict[ParagraphOrdinal, SourceParagraph] = {}
    seed_by_ordinal = {seed.ordinal: seed for seed in seeds}

    def collect(blocks: tuple[SourceBlock, ...]) -> None:
        for block in blocks:
            match block:
                case SourceParagraphBlock(body_ordinal=ordinal, inlines=inlines):
                    if ordinal is None:
                        continue
                    if ordinal in by_ordinal:
                        raise DocxSourceError(
                            "body paragraph has more than one rich source block: "
                            f"{ordinal.value}"
                        )
                    seed = seed_by_ordinal.get(ordinal)
                    if seed is None:
                        raise DocxSourceError(
                            f"rich body references unknown paragraph {ordinal.value}"
                        )
                    content = inline_content(inlines)
                    readable_runs = tuple(
                        run
                        for run in _source_runs(inlines)
                        if inline_reading(run.children).strip()
                    )
                    by_ordinal[ordinal] = SourceParagraph(
                        ordinal=ordinal,
                        semantics=ParagraphSemantics(
                            content=content,
                            page_break_before=seed.page_break_before,
                            payload=seed.payload,
                        ),
                        presentation=seed.presentation,
                        indent_departure=False,
                        markers=_paragraph_markers(
                            numbered=seed.presentation.numbering is not None,
                            direct_style=seed.presentation.direct_style,
                            text=content.reading.strip(),
                            heading_level=seed.presentation.heading_level,
                        ),
                        segment=seed.segment,
                        bold=any(run.properties.bold for run in readable_runs),
                        italic=any(run.properties.italic for run in readable_runs),
                    )
                case SourceTableBlock(rows=rows):
                    for row in rows:
                        for cell in row.cells:
                            collect(cell.blocks)
                case SourceContentControl(blocks=children):
                    collect(children)
                case SourceUnknownBlock():
                    continue
                case _ as unreachable:
                    assert_never(unreachable)

    collect(body)
    expected = set(seed_by_ordinal)
    if by_ordinal.keys() != expected:
        missing = sorted(ordinal.value for ordinal in expected - by_ordinal.keys())
        unexpected = sorted(ordinal.value for ordinal in by_ordinal.keys() - expected)
        raise DocxSourceError(
            "rich body does not cover canonical body paragraphs "
            f"(missing={missing}, unexpected={unexpected})"
        )

    finalized = _assign_visual_groups(_direction_indents(tuple(
        by_ordinal[seed.ordinal] for seed in seeds
    )))
    finalized_by_ordinal = {paragraph.ordinal: paragraph for paragraph in finalized}

    def relink(blocks: tuple[SourceBlock, ...]) -> tuple[SourceBlock, ...]:
        linked: list[SourceBlock] = []
        for block in blocks:
            match block:
                case SourceParagraphBlock(body_ordinal=ordinal):
                    if ordinal is None:
                        linked.append(block)
                        continue
                    current = finalized_by_ordinal[ordinal]
                    linked.append(replace(
                        block,
                        paragraph=current,
                    ))
                case SourceTableBlock(address=address, rows=rows):
                    linked.append(SourceTableBlock(
                        address,
                        tuple(
                            SourceTableRow(tuple(
                                replace(cell, blocks=relink(cell.blocks))
                                for cell in row.cells
                            ))
                            for row in rows
                        ),
                    ))
                case SourceContentControl(address=address, blocks=children):
                    linked.append(SourceContentControl(address, relink(children)))
                case SourceUnknownBlock():
                    linked.append(block)
                case _ as unreachable:
                    assert_never(unreachable)
        return tuple(linked)

    return finalized, relink(body)


def _read_package(docx: Path) -> DocxSourceDocument:
    with zipfile.ZipFile(docx) as zf:
        try:
            styles_xml = zf.read(STYLES_PART)
        except KeyError:
            styles_xml = None
        style_sheet = _style_sheet_xml(styles_xml)
        paragraph_styles = _paragraph_styles(style_sheet)
        numbering_catalog = _numbering_catalog(zf)
        root = ET.fromstring(zf.read(DOCUMENT_PART))
        document_layout = _document_layout(root, style_sheet)
        body = root.find(f"{W}body")
        if body is None:
            reader = _RichReader(
                zf,
                style_sheet=style_sheet,
                numbering=numbering_catalog,
                body_seeds={},
            )
            return DocxSourceDocument(
                path=docx,
                paragraphs=(),
                styles=paragraph_styles,
                layout=document_layout,
                notes=reader.note_definitions(),
                media=tuple(reader.media.values()),
                diagnostics=tuple(reader.diagnostics),
            )

        events = tuple(_body_events(body))
        paragraph_elements: list[ET.Element] = []
        seeds: list[_BodyParagraphSeed] = []
        segment = 0
        for event in events:
            if event is None:
                segment += 1
                continue
            paragraph_elements.append(event)
            ordinal = ParagraphOrdinal(len(seeds))
            ppr = event.find(f"{W}pPr")
            direct_numbered = ppr is not None and ppr.find(f"{W}numPr") is not None
            try:
                page_break_before, payload = _paragraph_semantic_facts(
                    event, paragraph_styles
                )
            except DocxSourceError as exc:
                raise DocxSourceError(
                    f"{docx.name}: paragraph {ordinal.value}: {exc}"
                ) from exc
            seed = _BodyParagraphSeed(
                ordinal=ordinal,
                page_break_before=page_break_before,
                payload=payload,
                presentation=_resolve_presentation(
                    ppr, style_sheet, numbering_catalog
                ),
                segment=SourceSegment(segment),
            )
            seeds.append(seed)
            if direct_numbered:
                segment += 1

        paragraph_seeds = tuple(seeds)
        by_element = dict(zip(paragraph_elements, paragraph_seeds, strict=True))
        reader = _RichReader(
            zf,
            style_sheet=style_sheet,
            numbering=numbering_catalog,
            body_seeds=by_element,
        )
        try:
            rich_body = reader.blocks(
                body,
                story=StoryPart.DOCUMENT,
                part=DOCUMENT_PART,
            )
        except DocxSourceError as exc:
            raise DocxSourceError(f"{docx.name}: {exc}") from exc
        rich_body = _resolve_story_fields(rich_body, reader.diagnostics)
        finalized, rich_body = _finalize_body_paragraphs(paragraph_seeds, rich_body)
        notes = reader.note_definitions()
        return DocxSourceDocument(
            path=docx,
            paragraphs=finalized,
            styles=paragraph_styles,
            layout=document_layout,
            body=rich_body,
            notes=notes,
            media=tuple(reader.media[name] for name in sorted(reader.media)),
            diagnostics=tuple(reader.diagnostics),
        )


def read(docx: Path) -> DocxSourceDocument:
    """Read one DOCX into the canonical source aggregate.

    Package and XML failures cross this boundary as ``DocxSourceError`` so
    semantic consumers never need ZIP or ElementTree exception knowledge.
    """
    try:
        return _read_package(docx)
    except DocxSourceError:
        raise
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise DocxSourceError(f"{docx}: invalid DOCX package: {exc}") from exc
    except KeyError as exc:
        detail = str(exc.args[0]) if exc.args else "unknown part"
        matched = re.fullmatch(r"There is no item named '(.+)' in the archive", detail)
        missing = matched.group(1) if matched is not None else detail
        raise DocxSourceError(
            f"{docx}: DOCX package is missing required part {missing!r}"
        ) from exc
    except ET.ParseError as exc:
        raise DocxSourceError(f"{docx}: malformed DOCX XML: {exc}") from exc
    except OSError as exc:
        raise DocxSourceError(f"{docx}: cannot read DOCX package: {exc}") from exc
