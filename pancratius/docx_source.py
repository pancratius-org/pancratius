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

from pancratius.ooxml import MC_NS, W
from pancratius.thematic import is_thematic_marker

MC_FALLBACK = f"{{{MC_NS}}}Fallback"
DOCUMENT_PART = "word/document.xml"
STYLES_PART = "word/styles.xml"


class DocxSourceError(ValueError):
    """A Word source cannot be represented by the canonical source model."""


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
class SourceSegment:
    """A reconciliation region that cannot cross a list or table boundary."""

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
        if raw == "page":
            return cls.PAGE
        if raw == "column":
            return cls.COLUMN
        # Only explicit pagination is excluded; other values retain line behavior.
        return cls.LINE


@dataclass(frozen=True, slots=True)
class TextAtom:
    """One source text fragment in document order."""

    value: str


type ParagraphAtom = TextAtom | BreakKind


@dataclass(frozen=True, slots=True)
class ParagraphContent:
    """One ordered truth from which every paragraph text view is derived."""

    atoms: tuple[ParagraphAtom, ...] = ()

    def _project(self, *, lineated: bool) -> str:
        def text_value(atom: TextAtom) -> str:
            if lineated:
                return atom.value
            return "".join(" " if character == "\t" else character for character in atom.value)

        return "".join(
            text_value(atom)
            if isinstance(atom, TextAtom)
            else "\n" if lineated and atom is BreakKind.LINE else " "
            for atom in self.atoms
        )

    @property
    def reading(self) -> str:
        return self._project(lineated=False)

    @property
    def lineated(self) -> str:
        return self._project(lineated=True)

    @property
    def breaks(self) -> tuple[BreakKind, ...]:
        return tuple(atom for atom in self.atoms if isinstance(atom, BreakKind))

    @property
    def line_segments(self) -> tuple[str, ...]:
        """Natural source lines; pagination never mints a sub-line."""
        return tuple(part.strip() for part in self.lineated.split("\n"))

    @property
    def pagination_only(self) -> bool:
        """True when atoms carry pagination and no readable/lineating content."""
        return any(
            isinstance(atom, BreakKind) and atom is not BreakKind.LINE
            for atom in self.atoms
        ) and all(
            (isinstance(atom, TextAtom) and not atom.value.strip())
            or (isinstance(atom, BreakKind) and atom is not BreakKind.LINE)
            for atom in self.atoms
        )


class ParagraphDisposition(StrEnum):
    """Why a source paragraph does or does not carry readable content."""

    CONTENT = "content"
    STRUCTURAL_EMPTY = "structural_empty"
    PAGINATION_ONLY = "pagination_only"
    NON_TEXT = "non_text"


class ParagraphRole(StrEnum):
    """Structural role relevant at the DOCX adapter boundary."""

    BODY = "body"
    LIST_ITEM = "list_item"
    HEADING = "heading"
    THEMATIC = "thematic"


class BorderGesture(StrEnum):
    """Editorial paragraph-border gesture used by display-register inference."""

    NONE = ""
    BOX = "box"
    RULE = "rule"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class ParagraphAlignment:
    """OOXML alignment value with the semantic query consumers actually need."""

    value: str = ""

    @property
    def is_right_edge(self) -> bool:
        return self.value in {"right", "end"}


type OoxmlAttributes = tuple[tuple[str, str], ...]


def _attributes(element: ET.Element | None) -> OoxmlAttributes:
    return () if element is None else tuple(
        sorted((key.removeprefix(W), value) for key, value in element.attrib.items())
    )


def _attribute(attributes: OoxmlAttributes, key: str) -> str | None:
    return dict(attributes).get(key)


@dataclass(frozen=True, slots=True)
class SourceParagraph:
    """One canonical top-level Word paragraph and all source-owned facts."""

    ordinal: ParagraphOrdinal
    reconciliation_position: ReconciliationPosition | None
    content: ParagraphContent
    disposition: ParagraphDisposition
    resolved_style: str
    direct_style: str
    alignment: ParagraphAlignment
    contextual_spacing: bool
    spacing: OoxmlAttributes
    indent: OoxmlAttributes
    indent_departure: bool
    border: BorderGesture
    roles: frozenset[ParagraphRole]
    segment: SourceSegment
    page_break_before: bool
    bold: bool
    italic: bool
    visual_group: VisualLineationGroup | None = None

    @property
    def text(self) -> str:
        return self.content.reading.strip()

    @property
    def numbered(self) -> bool:
        return ParagraphRole.LIST_ITEM in self.roles

    @property
    def heading(self) -> bool:
        return ParagraphRole.HEADING in self.roles

    @property
    def thematic(self) -> bool:
        return ParagraphRole.THEMATIC in self.roles

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

    @property
    def reconciliation_paragraphs(self) -> tuple[SourceParagraph, ...]:
        """Paragraphs that can correspond to top-level Pandoc paragraph blocks."""
        return tuple(p for p in self.paragraphs if p.reconciliation_position is not None)

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


def paragraph_sha(text: str) -> str:
    """Stable NFC-normalized text identity used by committed source sidecars."""
    return hashlib.sha256(unicodedata.normalize("NFC", text).encode()).hexdigest()[:16]


def read_adjudications(
    source: DocxSourceDocument, sidecar: Path
) -> tuple[SourceAdjudication, ...]:
    """Load canonical ordinal/text-railed entries from a source sidecar."""
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
        text_sha = payload.get("text_sha")
        if not isinstance(text_sha, str):
            raise ValueError(f"{sidecar.name}: ordinal {ordinal.value} is missing the text_sha rail")
        live_sha = paragraph_sha(paragraph.text)
        if live_sha != text_sha:
            raise ValueError(
                f"{sidecar.name}: ordinal {ordinal.value} text drifted under the adjudication "
                f"(rail {text_sha} != live {live_sha}) — re-adjudicate against the current text"
            )
        out.append(SourceAdjudication(paragraph, payload))
    return tuple(out)


@dataclass(frozen=True, slots=True)
class _StyleDefinition:
    based_on: str
    contextual_spacing: bool
    spacing: OoxmlAttributes


@dataclass(frozen=True, slots=True)
class _StyleSheet:
    paragraphs: dict[str, _StyleDefinition]
    default_paragraph: str = "Normal"
    default_spacing: OoxmlAttributes = ()


def _w_val(element: ET.Element | None) -> str:
    return str(element.get(f"{W}val") or "") if element is not None else ""


def _enabled(element: ET.Element | None) -> bool:
    return element is not None and element.get(f"{W}val") not in {"0", "false", "False", "off"}


def _style_sheet(zf: zipfile.ZipFile) -> _StyleSheet:
    try:
        root = ET.fromstring(zf.read(STYLES_PART))
    except KeyError:
        return _StyleSheet({})

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
            spacing=_attributes(ppr.find(f"{W}spacing") if ppr is not None else None),
        )
    return _StyleSheet(
        paragraphs=styles,
        default_paragraph=default,
        default_spacing=_attributes(
            root.find(f"{W}docDefaults/{W}pPrDefault/{W}pPr/{W}spacing")
        ),
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


def _paragraph_content(paragraph: ET.Element) -> ParagraphContent:
    atoms: list[ParagraphAtom] = []

    def walk(element: ET.Element) -> None:
        for child in element:
            if child.tag == MC_FALLBACK:
                # ``mc:Choice`` already contributed the active representation.
                # Counting fallback text or controls would duplicate content.
                continue
            if child.tag == f"{W}t":
                if child.text:
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
            else:
                walk(child)

    walk(paragraph)
    return ParagraphContent(tuple(atoms))


_NON_TEXT_SOURCE_CONTENT = frozenset(
    {f"{W}br", f"{W}cr", f"{W}tab", f"{W}drawing", f"{W}pict", f"{W}object"}
)
_OPAQUE_SOURCE_CONTENT = frozenset({f"{W}drawing", f"{W}pict", f"{W}object"})
_HEADING_STYLE = re.compile(r"(?:Heading\d+|[1-9])")
_BORDER_SIDES = ("top", "bottom", "left", "right")


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


def _paragraph_roles(
    *, numbered: bool, direct_style: str, text: str
) -> frozenset[ParagraphRole]:
    roles: set[ParagraphRole] = set()
    if numbered:
        roles.add(ParagraphRole.LIST_ITEM)
    if _HEADING_STYLE.fullmatch(direct_style):
        roles.add(ParagraphRole.HEADING)
    if is_thematic_marker(text):
        roles.add(ParagraphRole.THEMATIC)
    return frozenset(roles or {ParagraphRole.BODY})


def _paragraph_disposition(
    paragraph: ET.Element, content: ParagraphContent
) -> ParagraphDisposition:
    if content.reading.strip():
        return ParagraphDisposition.CONTENT
    tags = {element.tag for element in paragraph.iter()}
    if content.pagination_only and not tags & _OPAQUE_SOURCE_CONTENT:
        return ParagraphDisposition.PAGINATION_ONLY
    if not tags & _NON_TEXT_SOURCE_CONTENT:
        return ParagraphDisposition.STRUCTURAL_EMPTY
    return ParagraphDisposition.NON_TEXT


def _page_break_before(ppr: ET.Element | None) -> bool:
    element = ppr.find(f"{W}pageBreakBefore") if ppr is not None else None
    return element is not None and element.get(f"{W}val") not in {"false", "0", "none"}


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


def _spacing_is_real(spacing: OoxmlAttributes, edge: str) -> bool:
    if _attribute(spacing, f"{edge}Autospacing") == "1":
        return True
    try:
        return int(_attribute(spacing, edge) or "0") > 0
    except ValueError:
        return False


def _paragraphs_join(left: SourceParagraph, right: SourceParagraph) -> bool:
    if left.segment != right.segment:
        return False
    if not (left.contextual_spacing and right.contextual_spacing):
        return False
    if left.resolved_style != right.resolved_style:
        return False
    if not left.text or not right.text:
        return False
    if left.roles & {ParagraphRole.HEADING, ParagraphRole.THEMATIC}:
        return False
    if right.roles & {ParagraphRole.HEADING, ParagraphRole.THEMATIC}:
        return False
    if left.indent_departure or right.indent_departure:
        return False
    if left.border is not BorderGesture.NONE or right.border is not BorderGesture.NONE:
        return False
    if left.alignment != right.alignment:
        return False
    return _spacing_is_real(left.spacing, "after") or _spacing_is_real(right.spacing, "before")


def _direction_indents(paragraphs: tuple[SourceParagraph, ...]) -> tuple[SourceParagraph, ...]:
    body = [p for p in paragraphs if not p.numbered and p.text]
    counts: dict[OoxmlAttributes, int] = {}
    for paragraph in body:
        counts[paragraph.indent] = counts.get(paragraph.indent, 0) + 1
    dominant = max(counts, key=lambda signature: counts[signature], default=())
    return tuple(
        replace(
            paragraph,
            indent_departure=bool(paragraph.indent) and paragraph.indent != dominant,
        )
        for paragraph in paragraphs
    )


def _assign_visual_groups(paragraphs: tuple[SourceParagraph, ...]) -> tuple[SourceParagraph, ...]:
    eligible = [p for p in paragraphs if not p.numbered]
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
        style_sheet = _style_sheet(zf)
        root = ET.fromstring(zf.read(DOCUMENT_PART))
    body = root.find(f"{W}body")
    if body is None:
        return DocxSourceDocument(path=docx, paragraphs=())

    paragraphs: list[SourceParagraph] = []
    segment = 0
    reconciliation_position = 0
    for event in _body_events(body):
        if event is None:
            segment += 1
            continue
        ordinal = ParagraphOrdinal(len(paragraphs))
        ppr = event.find(f"{W}pPr")
        numbered = ppr is not None and ppr.find(f"{W}numPr") is not None
        direct_style = _w_val(ppr.find(f"{W}pStyle") if ppr is not None else None)
        resolved_style = direct_style or style_sheet.default_paragraph
        direct_spacing = _attributes(
            ppr.find(f"{W}spacing") if ppr is not None else None
        )
        content = _paragraph_content(event)
        text = content.reading.strip()
        paragraph = SourceParagraph(
            ordinal=ordinal,
            reconciliation_position=(
                None if numbered else ReconciliationPosition(reconciliation_position)
            ),
            content=content,
            disposition=_paragraph_disposition(event, content),
            resolved_style=resolved_style,
            direct_style=direct_style,
            alignment=ParagraphAlignment(
                _w_val(ppr.find(f"{W}jc") if ppr is not None else None)
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
            spacing=_resolved_spacing(
                resolved_style,
                style_sheet.paragraphs,
                style_sheet.default_spacing,
                direct_spacing,
            ),
            indent=_attributes(
                ppr.find(f"{W}ind") if ppr is not None else None
            ),
            indent_departure=False,
            border=_border_gesture(ppr),
            roles=_paragraph_roles(
                numbered=numbered,
                direct_style=direct_style,
                text=text,
            ),
            segment=SourceSegment(segment),
            page_break_before=_page_break_before(ppr),
            bold=any(_enabled(element) for element in event.findall(f".//{W}b")),
            italic=any(_enabled(element) for element in event.findall(f".//{W}i")),
        )
        paragraphs.append(paragraph)
        if numbered:
            segment += 1
        else:
            reconciliation_position += 1

    directed = _direction_indents(tuple(paragraphs))
    return DocxSourceDocument(path=docx, paragraphs=_assign_visual_groups(directed))
