# import-pure: no filesystem mutation
"""Canonical source-domain view of a Word document.

Pandoc remains the rich-content decoder. This module owns the source facts it
cannot represent faithfully: paragraph identity, resolved styling, visual
groups, and typed inline breaks. Import, diagnostics, correction rails, and
research project from this one aggregate instead of reinterpreting OOXML.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, replace
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
    """Word stories whose text is consumed outside the import body model."""

    DOCUMENT = DOCUMENT_PART
    FOOTNOTES = "word/footnotes.xml"
    ENDNOTES = "word/endnotes.xml"

    @property
    def required(self) -> bool:
        match self:
            case StoryPart.DOCUMENT:
                return True
            case StoryPart.FOOTNOTES | StoryPart.ENDNOTES:
                return False
        assert_never(self)


class DocxSourceError(ValueError):
    """A Word source cannot be represented by the canonical source model."""


class AlternateContentError(DocxSourceError):
    """A malformed compatibility branch, retaining its XML location."""

    def __init__(self, element: ET.Element) -> None:
        self.element = element
        super().__init__("mc:AlternateContent has multiple fallback branches")


# The raw `w:p` index in document order — the int a `ParagraphOrdinal` wraps.
# Spans, anchors, and the recon ledgers key on it directly.
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
class ReconciliationPosition:
    """Adjacency in the paragraph stream Pandoc can represent."""

    value: int

    def __post_init__(self) -> None:
        if self.value < 0:
            raise DocxSourceError("reconciliation position must be non-negative")


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

    @property
    def is_right_edge(self) -> bool:
        return self.normalized is TextAlignment.RIGHT


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
class SourceParagraph:
    """One canonical top-level Word paragraph and all source-owned facts."""

    ordinal: ParagraphOrdinal
    reconciliation_position: ReconciliationPosition | None
    semantics: ParagraphSemantics
    resolved_style: str
    direct_style: str
    layout: ParagraphLayout
    contextual_spacing: bool
    indent_departure: bool
    border: BorderGesture
    markers: ParagraphMarkers
    segment: SourceSegment
    bold: bool
    italic: bool
    visual_group: VisualLineationGroup | None = None

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
    def has_opaque_payload(self) -> bool:
        return self.semantics.has_opaque_payload

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

    @property
    def structural_empty(self) -> bool:
        return self.disposition is ParagraphDisposition.STRUCTURAL_EMPTY


@dataclass(frozen=True, slots=True)
class DocxSourceDocument:
    """Aggregate root for the source facts of one DOCX body."""

    path: Path
    paragraphs: tuple[SourceParagraph, ...]
    styles: ParagraphStyles = ParagraphStyles()
    layout: DocumentLayout = DocumentLayout()

    @property
    def reconciliation_paragraphs(self) -> tuple[SourceParagraph, ...]:
        """Paragraphs that can correspond to top-level Pandoc paragraph blocks."""
        return tuple(p for p in self.paragraphs if p.reconciliation_position is not None)

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
class _StyleDefinition:
    based_on: str
    contextual_spacing: bool
    alignment: ParagraphAlignment
    spacing: OoxmlAttributes
    indent: OoxmlAttributes
    font_half_points: int | None
    numbered: bool


@dataclass(frozen=True, slots=True)
class _StyleSheet:
    paragraphs: dict[str, _StyleDefinition]
    default_paragraph: str = "Normal"
    default_alignment: ParagraphAlignment = ParagraphAlignment()
    default_spacing: OoxmlAttributes = ()
    default_indent: OoxmlAttributes = ()
    default_font_half_points: int | None = None


def _w_val(element: ET.Element | None) -> str:
    return str(element.get(f"{W}val") or "") if element is not None else ""


def _baseline_fallback(
    parent: ET.Element,
    alternate: ET.Element,
) -> ET.Element | None:
    if parent.tag != f"{W}r":
        return None
    fallbacks = alternate.findall(ooxml.MC_FALLBACK)
    if len(fallbacks) > 1:
        raise AlternateContentError(alternate)
    return fallbacks[0] if fallbacks else None


def iter_baseline_children(parent: ET.Element) -> Iterator[ET.Element]:
    """Yield the direct children selected by the baseline capability profile.

    Pandoc recognizes ``mc:AlternateContent`` only directly under ``w:r`` and
    consumes its fallback without claiming extension capabilities. Other
    placements contribute no selected children.
    """
    for child in parent:
        if child.tag == ooxml.MC_ALTERNATE_CONTENT:
            fallback = _baseline_fallback(parent, child)
            if fallback is not None:
                yield from iter_baseline_children(fallback)
            continue
        yield child


def iter_baseline_descendants(root: ET.Element) -> Iterator[ET.Element]:
    """Walk every descendant selected by the baseline capability profile."""
    for child in iter_baseline_children(root):
        yield child
        yield from iter_baseline_descendants(child)


def story_paragraph_elements(root: ET.Element) -> tuple[ET.Element, ...]:
    """Paragraphs visible under one Word story in selected document order."""
    return tuple(
        element
        for element in iter_baseline_descendants(root)
        if element.tag == f"{W}p"
    )


def story_contents(root: ET.Element) -> tuple[ParagraphContent, ...]:
    """Canonical atom streams for every paragraph in one selected Word story."""
    contents: list[ParagraphContent] = []
    for index, paragraph in enumerate(story_paragraph_elements(root)):
        try:
            contents.append(_paragraph_content(paragraph))
        except DocxSourceError as exc:
            raise DocxSourceError(f"paragraph {index}: {exc}") from exc
    return tuple(contents)


def read_story(docx: Path, part: StoryPart) -> tuple[ParagraphContent, ...]:
    """Read a text-bearing story through the same compatibility and break grammar as the body."""
    try:
        with zipfile.ZipFile(docx) as archive:
            try:
                payload = archive.read(part.value)
            except KeyError:
                if part.required:
                    raise DocxSourceError(f"{docx.name}: missing {part.value}") from None
                return ()
    except (OSError, zipfile.BadZipFile) as exc:
        raise DocxSourceError(f"{docx.name}: cannot read {part.value}: {exc}") from exc
    try:
        return story_contents(ET.fromstring(payload))
    except (ET.ParseError, DocxSourceError) as exc:
        raise DocxSourceError(f"{docx.name}: cannot read {part.value}: {exc}") from exc


def paragraph_has_drawing(paragraph: ET.Element) -> bool:
    """Whether the selected compatibility branch carries a drawing."""
    return any(
        element.tag in {f"{W}drawing", f"{W}pict"}
        for element in iter_baseline_descendants(paragraph)
    )


def _enabled(element: ET.Element | None) -> bool:
    return element is not None and element.get(f"{W}val") not in {"0", "false", "False", "off"}


def _style_sheet_xml(styles_xml: bytes | None) -> _StyleSheet:
    if styles_xml is None:
        return _StyleSheet({})
    root = ET.fromstring(styles_xml)

    styles: dict[str, _StyleDefinition] = {}
    default = "Normal"
    for style in root.findall(f".//{W}style"):
        if style.get(f"{W}type") != "paragraph":
            continue
        style_id = str(style.get(f"{W}styleId") or "")
        if not style_id:
            continue
        if style.get(f"{W}default") == "1":
            default = style_id
        ppr = style.find(f"{W}pPr")
        styles[style_id] = _StyleDefinition(
            based_on=_w_val(style.find(f"{W}basedOn")),
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
        )
    default_ppr = root.find(f"{W}docDefaults/{W}pPrDefault/{W}pPr")
    return _StyleSheet(
        paragraphs=styles,
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
    )


def _style_sheet(zf: zipfile.ZipFile) -> _StyleSheet:
    try:
        styles_xml = zf.read(STYLES_PART)
    except KeyError:
        styles_xml = None
    return _style_sheet_xml(styles_xml)


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
    for definition in reversed(tuple(_style_chain(
        style_sheet.default_paragraph,
        style_sheet.paragraphs,
    ))):
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
                for definition in _style_chain(style, style_sheet.paragraphs)
            )
        ),
    )


def paragraph_styles(styles_xml: bytes | None) -> ParagraphStyles:
    """Resolve the style environment shared by every story in one DOCX package."""
    return _paragraph_styles(_style_sheet_xml(styles_xml))


def _resolved_contextual_spacing(
    style: str,
    styles: dict[str, _StyleDefinition],
    *,
    direct: bool,
) -> bool:
    return direct or any(definition.contextual_spacing for definition in _style_chain(style, styles))


def _resolved_spacing(
    style: str,
    styles: dict[str, _StyleDefinition],
    document_default: OoxmlAttributes,
    direct: OoxmlAttributes,
) -> OoxmlAttributes:
    values = dict(document_default)
    for definition in reversed(tuple(_style_chain(style, styles))):
        values.update(definition.spacing)
    values.update(direct)
    return tuple(sorted(values.items()))


def _resolved_alignment(
    style: str,
    styles: dict[str, _StyleDefinition],
    document_default: ParagraphAlignment,
    direct: ParagraphAlignment,
) -> ParagraphAlignment:
    value = document_default
    for definition in reversed(tuple(_style_chain(style, styles))):
        if definition.alignment.value:
            value = definition.alignment
    return direct if direct.value else value


def _resolved_indent(
    style: str,
    styles: dict[str, _StyleDefinition],
    document_default: OoxmlAttributes,
    direct: OoxmlAttributes,
) -> OoxmlAttributes:
    values = dict(document_default)
    for definition in reversed(tuple(_style_chain(style, styles))):
        values.update(definition.indent)
    values.update(direct)
    return tuple(sorted(values.items()))


def _paragraph_content(paragraph: ET.Element) -> ParagraphContent:
    atoms: list[ParagraphAtom] = []
    for child in iter_baseline_descendants(paragraph):
        if child.tag == f"{W}t" and child.text:
            atoms.append(TextAtom(child.text))
        elif child.tag == f"{W}br":
            atoms.append(BreakKind.from_ooxml(child.get(f"{W}type")))
        elif child.tag == f"{W}cr":
            atoms.append(BreakKind.LINE)
        elif child.tag == f"{W}tab":
            atoms.append(TextAtom("\t"))
        elif child.tag == f"{W}noBreakHyphen":
            atoms.append(TextAtom("‑"))
        elif child.tag == f"{W}softHyphen":
            atoms.append(TextAtom("­"))
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
                fallback = _baseline_fallback(element, child)
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
    *, numbered: bool, direct_style: str, text: str
) -> ParagraphMarkers:
    return ParagraphMarkers(
        numbered=numbered,
        heading_style=_HEADING_STYLE.fullmatch(direct_style) is not None,
        thematic_marker=is_thematic_marker(text),
    )


def _page_break_before(ppr: ET.Element | None) -> bool:
    element = ppr.find(f"{W}pageBreakBefore") if ppr is not None else None
    return _enabled(element)


def analyze_paragraph(
    paragraph: ET.Element,
    *,
    styles: ParagraphStyles,
) -> ParagraphSemantics:
    """Interpret paragraph content, pagination, and removability exactly once."""
    content = _paragraph_content(paragraph)
    ppr = paragraph.find(f"{W}pPr")
    page_break_before = _page_break_before(ppr)
    payload = _paragraph_payload(paragraph)
    direct_style = _w_val(ppr.find(f"{W}pStyle") if ppr is not None else None)
    if styles.is_numbered(direct_style):
        payload = payload.adding(ParagraphPayloadKind.RESOLVED_NUMBERING)
    return ParagraphSemantics(
        content=content,
        page_break_before=page_break_before,
        payload=payload,
    )


def paragraph_text(paragraph: ET.Element) -> str:
    """Local reading text for consumers that do not interpret disposition."""
    return _paragraph_content(paragraph).reading.strip()


def body_paragraph_elements(body: ET.Element) -> tuple[ET.Element, ...]:
    """Top-level paragraphs in the canonical body order (tables excluded)."""
    return tuple(event for event in _body_events(body) if event is not None)


def _body_events(body: ET.Element) -> Iterator[ET.Element | None]:
    """Yield top-level paragraphs and ``None`` for table boundaries."""
    for child in body:
        if child.tag == f"{W}p":
            yield child
        elif child.tag == f"{W}tbl":
            yield None
        elif child.tag == f"{W}sdt":
            content = child.find(f"{W}sdtContent")
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
    eligible = [p for p in paragraphs if p.reconciliation_position is not None]
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


def read(docx: Path) -> DocxSourceDocument:
    """Read one DOCX into the canonical source aggregate."""
    with zipfile.ZipFile(docx) as zf:
        try:
            styles_xml = zf.read(STYLES_PART)
        except KeyError:
            styles_xml = None
        style_sheet = _style_sheet_xml(styles_xml)
        paragraph_styles = _paragraph_styles(style_sheet)
        root = ET.fromstring(zf.read(DOCUMENT_PART))
    document_layout = _document_layout(root, style_sheet)
    body = root.find(f"{W}body")
    if body is None:
        return DocxSourceDocument(
            path=docx,
            paragraphs=(),
            styles=paragraph_styles,
            layout=document_layout,
        )

    paragraphs: list[SourceParagraph] = []
    segment = 0
    reconciliation_position = 0
    for event in _body_events(body):
        if event is None:
            segment += 1
            continue
        ordinal = ParagraphOrdinal(len(paragraphs))
        ppr = event.find(f"{W}pPr")
        direct_numbered = ppr is not None and ppr.find(f"{W}numPr") is not None
        direct_style = _w_val(ppr.find(f"{W}pStyle") if ppr is not None else None)
        numbered = direct_numbered or paragraph_styles.is_numbered(direct_style)
        resolved_style = direct_style or style_sheet.default_paragraph
        direct_spacing = _attributes(
            ppr.find(f"{W}spacing") if ppr is not None else None
        )
        try:
            semantics = analyze_paragraph(event, styles=paragraph_styles)
        except DocxSourceError as exc:
            raise DocxSourceError(
                f"{docx.name}: paragraph {ordinal.value}: {exc}"
            ) from exc
        content = semantics.content
        text = content.reading.strip()
        disposition = semantics.disposition
        reconciles = not direct_numbered and disposition is not ParagraphDisposition.PAGINATION_ONLY
        alignment = _resolved_alignment(
            resolved_style,
            style_sheet.paragraphs,
            style_sheet.default_alignment,
            ParagraphAlignment(_w_val(ppr.find(f"{W}jc") if ppr is not None else None)),
        )
        spacing = _resolved_spacing(
            resolved_style,
            style_sheet.paragraphs,
            style_sheet.default_spacing,
            direct_spacing,
        )
        indent = _resolved_indent(
            resolved_style,
            style_sheet.paragraphs,
            style_sheet.default_indent,
            _attributes(ppr.find(f"{W}ind") if ppr is not None else None),
        )
        baseline = tuple(iter_baseline_descendants(event))
        paragraph = SourceParagraph(
            ordinal=ordinal,
            reconciliation_position=(
                ReconciliationPosition(reconciliation_position) if reconciles else None
            ),
            semantics=semantics,
            resolved_style=resolved_style,
            direct_style=direct_style,
            layout=ParagraphLayout(
                source_alignment=alignment,
                spacing=ParagraphSpacing(spacing),
                indent=ParagraphIndent(indent),
            ),
            contextual_spacing=_resolved_contextual_spacing(
                resolved_style,
                style_sheet.paragraphs,
                direct=(
                    ppr.find(f"{W}contextualSpacing") is not None
                    if ppr is not None
                    else False
                ),
            ),
            indent_departure=False,
            border=_border_gesture(ppr),
            markers=_paragraph_markers(
                numbered=numbered,
                direct_style=direct_style,
                text=text,
            ),
            segment=SourceSegment(segment),
            bold=any(
                element.tag == f"{W}b" and _enabled(element)
                for element in baseline
            ),
            italic=any(
                element.tag == f"{W}i" and _enabled(element)
                for element in baseline
            ),
        )
        paragraphs.append(paragraph)
        if direct_numbered:
            segment += 1
        elif reconciles:
            reconciliation_position += 1

    directed = _direction_indents(tuple(paragraphs))
    return DocxSourceDocument(
        path=docx,
        paragraphs=_assign_visual_groups(directed),
        styles=paragraph_styles,
        layout=document_layout,
    )
