"""Intent projection of the canonical DOCX source aggregate.

The source compiler owns paragraph identity, breaks, layout, and structural
classification. This module only adds line-width physics; it never reopens
OOXML or reconstructs lines from another document view.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from pancratius import docx_source, docx_structure

from . import physics
from .records import RecordDisposition


@dataclass(frozen=True, slots=True)
class Line:
    coordinate: docx_source.SourceLineCoordinate
    disposition: RecordDisposition
    text: str
    fill: float
    wraps: bool


@dataclass(frozen=True, slots=True)
class Para:
    """One source paragraph with its derived, independently scoped natural lines."""

    source: docx_source.SourceParagraph
    lines: tuple[Line, ...] = ()

    @property
    def text(self) -> str:
        return self.source.text


def _disposition(observation: docx_structure.SourceLineObservation) -> RecordDisposition:
    """THE research-scope table: which compiler classification is a lineation
    candidate.

    Quote members are in scope: the importer makes real fold decisions inside
    quotes (scripture/inset verse) and those ship — flip the QUOTE line to
    exclude them from the corpus."""
    kind = docx_structure.CompilerBlockKind
    if observation.is_body_kind:
        match observation.enclosure:
            case None | kind.QUOTE:
                return RecordDisposition.BODY
            case kind.LIST:
                return RecordDisposition.LIST
    match observation.kind:
        case kind.HEADING:
            return RecordDisposition.HEADING
        case kind.THEMATIC:
            return RecordDisposition.THEMATIC
        case kind.LIST:
            return RecordDisposition.LIST
        case _:
            return RecordDisposition.CONTEXT


def read_view(
    observation: docx_structure.StructuralObservation,
) -> tuple[Para, ...]:
    """Project one already-hydrated source document into intent paragraphs."""
    out: list[Para] = []
    by_line = observation.by_line
    lost = observation.lost
    geom = physics.page_geom(observation.source.layout)
    for paragraph in observation.source.paragraphs:
        match paragraph.disposition:
            case docx_source.ParagraphDisposition.PAGINATION_ONLY:
                continue
            case docx_source.ParagraphDisposition.STRUCTURAL_EMPTY:
                out.append(Para(source=paragraph))
                continue
            case docx_source.ParagraphDisposition.NON_TEXT:
                out.append(Para(source=paragraph))
                continue
            case docx_source.ParagraphDisposition.CONTENT:
                pass
            case unsupported:
                assert_never(unsupported)

        lines = tuple(
            Line(
                coordinate=source_line.coordinate,
                disposition=(
                    RecordDisposition.LOST
                    if int(paragraph.ordinal) in lost
                    else _disposition(by_line[source_line.coordinate])
                    if source_line.coordinate in by_line
                    else RecordDisposition.CONTEXT
                ),
                text=source_line.text,
                fill=stat.fill,
                wraps=stat.wraps,
            )
            for source_line in paragraph.natural_lines
            for stat in (physics.wrap_stat(source_line.text, geom),)
        )
        out.append(Para(source=paragraph, lines=lines))
    return tuple(out)
