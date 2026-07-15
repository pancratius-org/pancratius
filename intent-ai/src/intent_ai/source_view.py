"""Intent projection of the canonical DOCX source aggregate.

The source compiler owns paragraph identity, breaks, layout, and structural
classification. This module only adds line-width physics; it never reopens
OOXML or reconstructs lines from Pandoc IR.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from pancratius import docx_source, docx_structure

from . import physics
from .records import Role


@dataclass(frozen=True, slots=True)
class Line:
    text: str
    fill: float
    wraps: bool


@dataclass(frozen=True, slots=True)
class Para:
    """One source paragraph with an intent role and derived natural lines."""

    source: docx_source.SourceParagraph
    role: Role
    lines: tuple[Line, ...] = ()

    @property
    def text(self) -> str:
        return self.source.text


def _role(
    observation: docx_structure.ParagraphObservation,
) -> Role:
    match observation:
        case docx_structure.BodyParagraph():
            return Role.BODY
        case docx_structure.ReviewParagraph():
            return Role.BODY_REVIEW
        case docx_structure.ContextParagraph(role=role):
            match role:
                case docx_structure.ContextRole.HEADING:
                    return Role.HEADING
                case docx_structure.ContextRole.LIST:
                    return Role.LIST
                case docx_structure.ContextRole.THEMATIC:
                    return Role.THEMATIC
                case docx_structure.ContextRole.CONTEXT:
                    return Role.CONTEXT
            raise ValueError(f"unsupported source context role {role!r}")
        case unsupported:
            assert_never(unsupported)


def read_view(
    observation: docx_structure.StructuralObservation,
) -> tuple[Para, ...]:
    """Project one already-hydrated source document into intent paragraphs."""
    out: list[Para] = []
    by_ordinal = observation.by_ordinal
    geom = physics.page_geom(observation.source.layout)
    for paragraph in observation.source.paragraphs:
        match paragraph.disposition:
            case docx_source.ParagraphDisposition.PAGINATION_ONLY:
                continue
            case docx_source.ParagraphDisposition.STRUCTURAL_EMPTY:
                out.append(Para(source=paragraph, role=Role.CONTEXT))
                continue
            case docx_source.ParagraphDisposition.NON_TEXT:
                out.append(Para(source=paragraph, role=Role.CONTEXT))
                continue
            case docx_source.ParagraphDisposition.CONTENT:
                pass
            case unsupported:
                assert_never(unsupported)

        lines = tuple(
            Line(text=text, fill=stat.fill, wraps=stat.wraps)
            for segment in paragraph.content.line_segments
            if (text := segment)
            for stat in (physics.wrap_stat(text, geom),)
        )
        out.append(
            Para(
                source=paragraph,
                role=_role(by_ordinal[paragraph.ordinal]),
                lines=lines,
            )
        )
    return tuple(out)
