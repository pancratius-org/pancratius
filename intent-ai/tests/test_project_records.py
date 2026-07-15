"""Portable contract test for the typed production-to-research seam."""
from __future__ import annotations

import re
from pathlib import Path

from intent_ai import producer
from intent_ai.identity import BookId
from intent_ai.records import Role, feature_field_names

from pancratius import docx_source, docx_structure


def _paragraph(
    ordinal: int,
    atoms: tuple[docx_source.ParagraphAtom, ...],
) -> docx_source.SourceParagraph:
    return docx_source.SourceParagraph(
        ordinal=docx_source.ParagraphOrdinal(ordinal),
        reconciliation_position=docx_source.ReconciliationPosition(ordinal),
        semantics=docx_source.ParagraphSemantics(
            content=docx_source.ParagraphContent(atoms),
            page_break_before=False,
        ),
        resolved_style="Normal",
        direct_style="Normal",
        layout=docx_source.ParagraphLayout(),
        contextual_spacing=False,
        indent_departure=False,
        border=docx_source.BorderGesture.NONE,
        markers=docx_source.ParagraphMarkers(),
        segment=docx_source.SourceSegment(0),
        bold=False,
        italic=False,
    )


def test_project_records_folds_one_typed_source_without_reopening_the_compiler() -> None:
    source = docx_source.DocxSourceDocument(
        path=Path("synthetic.docx"),
        paragraphs=(
            _paragraph(
                0,
                (
                    docx_source.TextAtom("First line"),
                    docx_source.BreakKind.LINE,
                    docx_source.TextAtom("second line."),
                ),
            ),
            _paragraph(1, (docx_source.TextAtom("A heading"),)),
            _paragraph(2, (docx_source.TextAtom("Third line"),)),
        ),
        layout=docx_source.DocumentLayout(
            column_width=docx_source.ObservedColumnWidth(docx_source.Twips(6000)),
            default_font_size=docx_source.ObservedFontSize(24),
        ),
    )
    observation = docx_structure.StructuralObservation(
        source,
        "ru",
        (
            (docx_source.ParagraphOrdinal(0), docx_structure.BodyParagraph()),
            (
                docx_source.ParagraphOrdinal(1),
                docx_structure.ContextParagraph(
                    docx_structure.ContextReason.STRUCTURAL_KIND,
                    docx_structure.ContextRole.HEADING,
                ),
            ),
            (docx_source.ParagraphOrdinal(2), docx_structure.BodyParagraph()),
        ),
    )
    records = producer.project_records(
        observation,
        book_id=BookId("01"),
    )

    assert [(record.id.src_ordinal, record.id.sub) for record in records] == [
        (0, 0),
        (0, 1),
        (1, 0),
        (2, 0),
    ]
    assert [record.role for record in records] == [
        Role.BODY,
        Role.BODY,
        Role.HEADING,
        Role.BODY,
    ]
    assert [(record.features.run_len, record.features.run_pos) for record in records] == [
        (2, 0),
        (2, 1),
        (1, 0),
        (1, 0),
    ]


def test_producer_contract_has_no_truth_input() -> None:
    signature = set(
        producer.read_lines.__code__.co_varnames[: producer.read_lines.__code__.co_argcount]
    )
    assert signature == {"docx", "lang", "book_id"}
    assert not any(
        re.search(r"label|gold|predict|class", name) for name in feature_field_names()
    )
