"""Canonical DOCX source boundary and its pure IR projection."""

from __future__ import annotations

import importlib.util
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from pancratius import docx_adapter, docx_source, ir, lower
from pancratius.ir.inlines import inline_plain

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
ASVG_NS = "http://schemas.microsoft.com/office/drawing/2016/SVG/main"
V_NS = "urn:schemas-microsoft-com:vml"
O_NS = "urn:schemas-microsoft-com:office:office"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
IMAGE_REL = f"{R_NS}/image"
HYPERLINK_REL = f"{R_NS}/hyperlink"


def _write_docx(
    tmp_path: Path,
    body: str,
    *,
    styles: str | None = None,
    numbering: str | None = None,
    relationships: str = "",
    footnotes: str | None = None,
    endnotes: str | None = None,
    media: dict[str, bytes] | None = None,
) -> Path:
    path = tmp_path / "source.docx"
    document = (
        f'<w:document xmlns:w="{W_NS}" xmlns:r="{R_NS}" xmlns:mc="{MC_NS}" '
        f'xmlns:a="{A_NS}" xmlns:wp="{WP_NS}" xmlns:pic="{PIC_NS}" '
        f'xmlns:asvg="{ASVG_NS}" xmlns:v="{V_NS}" xmlns:o="{O_NS}" '
        f'xmlns:m="{M_NS}"><w:body>{body}</w:body></w:document>'
    )
    rels = f'<Relationships xmlns="{REL_NS}">{relationships}</Relationships>'
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", document)
        if relationships:
            archive.writestr("word/_rels/document.xml.rels", rels)
        if styles is not None:
            archive.writestr("word/styles.xml", styles)
        if numbering is not None:
            archive.writestr("word/numbering.xml", numbering)
        if footnotes is not None:
            archive.writestr("word/footnotes.xml", footnotes)
        if endnotes is not None:
            archive.writestr("word/endnotes.xml", endnotes)
        for name, data in (media or {}).items():
            archive.writestr(name, data)
    return path


def _adapt(
    source: docx_source.DocxSourceDocument,
    tmp_path: Path,
) -> tuple[ir.Document, list[ir.Diagnostic]]:
    diagnostics: list[ir.Diagnostic] = []
    document = docx_adapter.adapt(source, tmp_path / "media", diagnostics)
    return document, diagnostics


def _visual_groups(
    source: docx_source.DocxSourceDocument,
) -> list[int | None]:
    return [
        paragraph.visual_group.value
        if paragraph.visual_group is not None
        else None
        for paragraph in source.paragraphs
    ]


def _paragraph_blocks(
    blocks: tuple[docx_source.SourceBlock, ...],
) -> list[docx_source.SourceParagraphBlock]:
    out: list[docx_source.SourceParagraphBlock] = []
    for block in blocks:
        if isinstance(block, docx_source.SourceParagraphBlock):
            out.append(block)
            for inline in block.inlines:
                if isinstance(inline, docx_source.SourceRun):
                    out.extend(_text_box_paragraphs(inline.children))
        elif isinstance(block, docx_source.SourceTableBlock):
            for row in block.rows:
                for cell in row.cells:
                    out.extend(_paragraph_blocks(cell.blocks))
        elif isinstance(block, docx_source.SourceContentControl):
            out.extend(_paragraph_blocks(block.blocks))
    return out


def _text_box_paragraphs(
    inlines: tuple[docx_source.SourceInline, ...],
) -> list[docx_source.SourceParagraphBlock]:
    out: list[docx_source.SourceParagraphBlock] = []
    for inline in inlines:
        if isinstance(inline, docx_source.SourceTextBox):
            out.extend(_paragraph_blocks(inline.blocks))
        elif isinstance(
            inline,
            docx_source.SourceRun | docx_source.SourceHyperlink | docx_source.SourceField,
        ):
            out.extend(_text_box_paragraphs(inline.children))
    return out


def test_one_read_links_physical_facts_to_rich_blocks(tmp_path: Path) -> None:
    path = _write_docx(
        tmp_path,
        """
        <w:p/>
        <w:p><w:r><w:t>first</w:t><w:br/><w:t>second</w:t>
          <w:br w:type="page"/><w:t>third</w:t></w:r></w:p>
        <w:tbl><w:tr><w:tc><w:p><w:r><w:t>cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
        <w:sdt><w:sdtPr/><w:sdtContent>
          <w:p><w:r><w:t>controlled</w:t></w:r></w:p>
        </w:sdtContent></w:sdt>
        """,
    )

    source = docx_source.read(path)

    assert [paragraph.content.reading for paragraph in source.paragraphs] == [
        "",
        "first second third",
        "controlled",
    ]
    assert [line.text for line in source.paragraphs[1].natural_lines] == [
        "first",
        "second third",
    ]
    assert [type(block).__name__ for block in source.body] == [
        "SourceParagraphBlock",
        "SourceParagraphBlock",
        "SourceTableBlock",
        "SourceContentControl",
    ]
    direct = [
        block
        for block in _paragraph_blocks(source.body)
        if block.paragraph is not None
    ]
    assert [block.paragraph for block in direct] == list(source.paragraphs)
    assert all(
        block.reading == block.paragraph.content.reading
        for block in direct
        if block.paragraph is not None
    )
    assert all(
        block.presentation is block.paragraph.presentation
        for block in direct
        if block.paragraph is not None
    )

    document, diagnostics = _adapt(source, tmp_path)
    assert diagnostics == []
    paragraph = document.blocks[0]
    assert isinstance(paragraph, ir.Paragraph)
    assert paragraph.source_span is not None
    assert (paragraph.source_span.start, paragraph.source_span.end) == (1, 1)
    assert inline_plain(paragraph.inlines) == "first second third"
    assert any(isinstance(inline, ir.LineBreak) for inline in paragraph.inlines)
    assert isinstance(document.blocks[1], ir.Table)
    assert inline_plain(document.blocks[1].rows[0][0]) == "cell"
    controlled = document.blocks[2]
    assert isinstance(controlled, ir.Paragraph)
    assert controlled.source_span is not None
    assert (controlled.source_span.start, controlled.source_span.end) == (2, 2)


def test_contextual_spacing_forms_one_visual_group(tmp_path: Path) -> None:
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p><w:pPr><w:contextualSpacing/><w:spacing w:after="100"/></w:pPr>
          <w:r><w:t>first</w:t></w:r></w:p>
        <w:p><w:pPr><w:contextualSpacing/><w:spacing w:before="100"/></w:pPr>
          <w:r><w:t>second</w:t></w:r></w:p>
        <w:p><w:pPr><w:contextualSpacing/><w:spacing w:before="100"/></w:pPr>
          <w:r><w:t>third</w:t></w:r></w:p>
        """,
    ))

    assert _visual_groups(source) == [1, 1, 1]


def test_visual_group_uses_resolved_document_spacing(tmp_path: Path) -> None:
    styles = f"""
        <w:styles xmlns:w="{W_NS}">
          <w:docDefaults><w:pPrDefault><w:pPr><w:spacing w:after="100"/></w:pPr>
          </w:pPrDefault></w:docDefaults>
        </w:styles>
    """
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p><w:pPr><w:contextualSpacing/></w:pPr><w:r><w:t>first</w:t></w:r></w:p>
        <w:p><w:pPr><w:contextualSpacing/></w:pPr><w:r><w:t>second</w:t></w:r></w:p>
        """,
        styles=styles,
    ))

    assert _visual_groups(source) == [1, 1]


def test_style_spacing_zero_overrides_document_default(tmp_path: Path) -> None:
    styles = f"""
        <w:styles xmlns:w="{W_NS}">
          <w:docDefaults><w:pPrDefault><w:pPr><w:spacing w:after="100"/></w:pPr>
          </w:pPrDefault></w:docDefaults>
          <w:style w:type="paragraph" w:styleId="NoGap">
            <w:pPr><w:spacing w:after="0"/></w:pPr>
          </w:style>
        </w:styles>
    """
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p><w:pPr><w:pStyle w:val="NoGap"/><w:contextualSpacing/></w:pPr>
          <w:r><w:t>first</w:t></w:r></w:p>
        <w:p><w:pPr><w:pStyle w:val="NoGap"/><w:contextualSpacing/></w:pPr>
          <w:r><w:t>second</w:t></w:r></w:p>
        """,
        styles=styles,
    ))

    assert _visual_groups(source) == [None, None]


def test_visual_group_does_not_bridge_numbering_segment(tmp_path: Path) -> None:
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p><w:pPr><w:contextualSpacing/><w:spacing w:after="100"/></w:pPr>
          <w:r><w:t>before</w:t></w:r></w:p>
        <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>
          <w:contextualSpacing/><w:spacing w:before="100" w:after="100"/></w:pPr>
          <w:r><w:t>item</w:t></w:r></w:p>
        <w:p><w:pPr><w:contextualSpacing/><w:spacing w:before="100"/></w:pPr>
          <w:r><w:t>after</w:t></w:r></w:p>
        """,
    ))

    assert [paragraph.text for paragraph in source.paragraphs] == [
        "before", "item", "after",
    ]
    assert _visual_groups(source) == [None, None, None]


def test_paragraph_layout_resolves_style_inheritance_once(tmp_path: Path) -> None:
    styles = f"""
        <w:styles xmlns:w="{W_NS}">
          <w:docDefaults><w:pPrDefault><w:pPr><w:jc w:val="center"/>
            <w:ind w:left="120"/></w:pPr></w:pPrDefault></w:docDefaults>
          <w:style w:type="paragraph" w:styleId="Base">
            <w:pPr><w:jc w:val="both"/><w:ind w:left="240" w:firstLine="80"/></w:pPr>
          </w:style>
          <w:style w:type="paragraph" w:styleId="Derived">
            <w:basedOn w:val="Base"/><w:pPr><w:ind w:left="480"/></w:pPr>
          </w:style>
        </w:styles>
    """
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p><w:pPr><w:pStyle w:val="Derived"/></w:pPr>
          <w:r><w:t>styled</w:t></w:r></w:p>
        """,
        styles=styles,
    ))
    paragraph = source.paragraphs[0]

    assert paragraph.layout.alignment is docx_source.TextAlignment.JUST
    assert paragraph.layout.first_line_indent == docx_source.Twips(80)
    assert paragraph.layout.left_indent == docx_source.Twips(480)


def test_hyphen_atoms_survive_the_canonical_rich_path(tmp_path: Path) -> None:
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p><w:r><w:t>кто</w:t><w:noBreakHyphen/><w:t>то</w:t>
          <w:t xml:space="preserve"> и </w:t><w:t>когда</w:t><w:softHyphen/>
          <w:t>нибудь</w:t></w:r></w:p>
        """,
    ))

    assert source.paragraphs[0].content.atoms == (
        docx_source.TextAtom("кто‑то и когда­нибудь"),
    )


def test_border_gesture_matrix_is_source_owned(tmp_path: Path) -> None:
    def paragraph(text: str, *sides: str, value: str = "single") -> str:
        borders = "".join(
            f'<w:{side} w:val="{value}" w:sz="4"/>' for side in sides
        )
        properties = f"<w:pPr><w:pBdr>{borders}</w:pBdr></w:pPr>" if sides else ""
        return f"<w:p>{properties}<w:r><w:t>{text}</w:t></w:r></w:p>"

    source = docx_source.read(_write_docx(
        tmp_path,
        "".join((
            paragraph("boxed", "top", "bottom", "left", "right"),
            paragraph("ruled", "left"),
            paragraph("topped", "top"),
            paragraph("disabled", "top", "bottom", "left", "right", value="none"),
            paragraph("plain"),
        )),
    ))

    assert [paragraph.border for paragraph in source.paragraphs] == [
        docx_source.BorderGesture.BOX,
        docx_source.BorderGesture.RULE,
        docx_source.BorderGesture.OTHER,
        docx_source.BorderGesture.NONE,
        docx_source.BorderGesture.NONE,
    ]


def test_indent_departure_is_relative_to_document_body(tmp_path: Path) -> None:
    body = "".join(
        f'<w:p><w:pPr><w:ind w:firstLine="708"/></w:pPr>'
        f"<w:r><w:t>body {index}</w:t></w:r></w:p>"
        for index in range(3)
    )
    body += (
        '<w:p><w:pPr><w:ind w:left="720"/></w:pPr>'
        '<w:r><w:t>inset</w:t></w:r></w:p>'
        '<w:p><w:r><w:t>plain</w:t></w:r></w:p>'
    )

    source = docx_source.read(_write_docx(tmp_path, body))

    assert [paragraph.indent_departure for paragraph in source.paragraphs] == [
        False, False, False, True, False,
    ]


def test_read_uses_the_rich_tree_as_its_only_paragraph_content_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(_paragraph: ET.Element) -> docx_source.ParagraphContent:
        raise AssertionError("read() invoked the legacy physical-content projection")

    monkeypatch.setattr(docx_source, "_paragraph_content", forbidden)

    source = docx_source.read(_write_docx(
        tmp_path,
        "<w:p><w:r><w:t>canonical content</w:t></w:r></w:p>",
    ))

    assert source.paragraphs[0].content.reading == "canonical content"
    block = source.body[0]
    assert isinstance(block, docx_source.SourceParagraphBlock)
    assert block.paragraph is source.paragraphs[0]


def test_package_paragraph_projection_does_not_read_property_tabs_as_text() -> None:
    paragraph = ET.fromstring(
        f"""
        <w:p xmlns:w="{W_NS}">
          <w:pPr><w:tabs><w:tab w:val="right" w:pos="5944"/></w:tabs></w:pPr>
          <w:r><w:t>A</w:t><w:tab/><w:t>B</w:t></w:r>
        </w:p>
        """
    )

    semantics = docx_source.analyze_paragraph(
        paragraph,
        styles=docx_source.ParagraphStyles(),
    )

    assert semantics.content.atoms == (docx_source.TextAtom("A\tB"),)
    assert semantics.text == "A B"


def test_package_paragraph_projection_shares_the_canonical_lexical_grammar(
    tmp_path: Path,
) -> None:
    paragraph_xml = """
        <w:p><w:r>
          <w:t>kept</w:t><w:delText>deleted</w:delText>
          <w:sym w:font="Wingdings" w:char="F04A"/>
          <w:noBreakHyphen/><w:softHyphen/><w:br/><w:t>next</w:t>
        </w:r></w:p>
    """
    source = docx_source.read(_write_docx(tmp_path, paragraph_xml))
    wrapper = ET.fromstring(f'<w:body xmlns:w="{W_NS}">{paragraph_xml}</w:body>')
    paragraph = wrapper[0]

    projected = docx_source.analyze_paragraph(
        paragraph,
        styles=docx_source.ParagraphStyles(),
    )

    assert projected.content == source.paragraphs[0].content
    assert projected.content.atoms == (
        docx_source.TextAtom("keptdeleted☺‑­"),
        docx_source.BreakKind.LINE,
        docx_source.TextAtom("next"),
    )


def test_package_paragraph_projection_shares_text_box_boundaries(
    tmp_path: Path,
) -> None:
    paragraph_xml = """
        <w:p><w:r><w:t>before</w:t><w:pict><v:shape><v:textbox>
          <w:txbxContent><w:p><w:r><w:t>inside</w:t></w:r></w:p></w:txbxContent>
        </v:textbox></v:shape></w:pict><w:t>after</w:t></w:r></w:p>
    """
    source = docx_source.read(_write_docx(tmp_path, paragraph_xml))
    wrapper = ET.fromstring(
        f'<w:body xmlns:w="{W_NS}" xmlns:v="{V_NS}">{paragraph_xml}</w:body>'
    )

    projected = docx_source.analyze_paragraph(
        wrapper[0],
        styles=docx_source.ParagraphStyles(),
    )

    assert projected.content == source.paragraphs[0].content
    assert projected.content.reading == "before inside after"


def test_read_wraps_package_and_xml_failures_at_the_source_boundary(
    tmp_path: Path,
) -> None:
    with pytest.raises(docx_source.DocxSourceError, match="cannot read DOCX package"):
        docx_source.read(tmp_path / "missing.docx")

    invalid_package = tmp_path / "invalid.docx"
    invalid_package.write_bytes(b"not a zip")
    with pytest.raises(docx_source.DocxSourceError, match="invalid DOCX package"):
        docx_source.read(invalid_package)

    missing_part = tmp_path / "missing-part.docx"
    with zipfile.ZipFile(missing_part, "w"):
        pass
    with pytest.raises(
        docx_source.DocxSourceError,
        match=r"missing required part 'word/document.xml'",
    ):
        docx_source.read(missing_part)

    malformed_xml = tmp_path / "malformed.docx"
    with zipfile.ZipFile(malformed_xml, "w") as archive:
        archive.writestr("word/document.xml", "<w:document")
    with pytest.raises(docx_source.DocxSourceError, match="malformed DOCX XML"):
        docx_source.read(malformed_xml)


def test_adapter_cannot_reopen_or_reinterpret_the_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = docx_source.read(
        _write_docx(tmp_path, "<w:p><w:r><w:t>one parse</w:t></w:r></w:p>")
    )

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the adapter attempted to reopen the DOCX")

    monkeypatch.setattr(docx_source.zipfile, "ZipFile", forbidden)
    document, diagnostics = _adapt(source, tmp_path)

    assert diagnostics == []
    block = document.blocks[0]
    assert isinstance(block, ir.Paragraph)
    assert inline_plain(block.inlines) == "one parse"
    assert importlib.util.find_spec("pancratius.docx_pandoc") is None


def test_compatibility_fallback_is_one_policy_at_every_depth(tmp_path: Path) -> None:
    path = _write_docx(
        tmp_path,
        """
        <mc:AlternateContent>
          <mc:Choice Requires="future"><w:p><w:r><w:t>BODY CHOICE</w:t></w:r></w:p></mc:Choice>
          <mc:Fallback><w:p><w:r><w:t>body fallback</w:t></w:r></w:p></mc:Fallback>
        </mc:AlternateContent>
        <w:p><w:r><mc:AlternateContent>
          <mc:Choice Requires="future"><w:t>RUN CHOICE</w:t></mc:Choice>
          <mc:Fallback><w:t>run fallback</w:t></mc:Fallback>
        </mc:AlternateContent></w:r></w:p>
        """,
    )

    source = docx_source.read(path)

    assert [paragraph.content.reading for paragraph in source.paragraphs] == [
        "body fallback",
        "run fallback",
    ]
    assert source.body_readings == ("body fallback", "run fallback")
    assert [finding.code for finding in source.diagnostics] == [
        "source.compatibility-fallback",
        "source.compatibility-fallback",
    ]
    assert [
        finding.address.path if finding.address is not None else None
        for finding in source.diagnostics
    ] == [(0,), (1, 0, 0)]

    _document, diagnostics = _adapt(source, tmp_path)
    assert [finding.severity for finding in diagnostics] == ["info", "info"]
    assert {finding.code for finding in diagnostics} == {
        "import.source-compatibility-fallback",
    }


def test_missing_compatibility_fallback_is_explicit(tmp_path: Path) -> None:
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <mc:AlternateContent>
          <mc:Choice Requires="future"><w:p><w:r><w:t>hidden</w:t></w:r></w:p></mc:Choice>
        </mc:AlternateContent>
        """,
    ))

    assert source.paragraphs == ()
    assert source.body == ()
    finding, = source.diagnostics
    assert finding.code == "source.compatibility-unsupported"
    assert finding.address == docx_source.SourceAddress(
        docx_source.StoryPart.DOCUMENT,
        (0,),
    )

    _document, diagnostics = _adapt(source, tmp_path)
    assert diagnostics == [ir.Diagnostic(
        "fatal",
        "import.source-compatibility-unsupported",
        "word/document.xml:0: mc:AlternateContent has no fallback branch",
    )]


def test_resolved_styles_and_direct_run_properties_share_the_source_model(
    tmp_path: Path,
) -> None:
    styles = f"""
    <w:styles xmlns:w="{W_NS}">
      <w:docDefaults><w:rPrDefault><w:rPr><w:i/></w:rPr></w:rPrDefault></w:docDefaults>
      <w:style w:type="paragraph" w:styleId="Base">
        <w:name w:val="Body"/><w:pPr><w:jc w:val="right"/><w:ind w:left="720"/></w:pPr>
        <w:rPr><w:b/></w:rPr>
      </w:style>
      <w:style w:type="paragraph" w:styleId="Derived">
        <w:basedOn w:val="Base"/><w:pPr><w:outlineLvl w:val="1"/></w:pPr>
      </w:style>
      <w:style w:type="character" w:styleId="Marked">
        <w:name w:val="Marked"/><w:rPr><w:strike/></w:rPr>
      </w:style>
    </w:styles>
    """
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p><w:pPr><w:pStyle w:val="Derived"/><w:rPr><w:i w:val="0"/></w:rPr></w:pPr>
          <w:r><w:rPr><w:rStyle w:val="Marked"/><w:b w:val="0"/></w:rPr><w:t>styled</w:t></w:r>
        </w:p>
        """,
        styles=styles,
    ))

    paragraph = source.paragraphs[0]
    block = source.body[0]
    assert isinstance(block, docx_source.SourceParagraphBlock)
    run = block.inlines[0]
    assert isinstance(run, docx_source.SourceRun)
    assert paragraph.alignment.value == "right"
    assert dict(paragraph.indent) == {"left": "720"}
    assert block.heading_level == 2
    assert run.properties == docx_source.SourceRunProperties(
        style="Marked",
        bold=False,
        italic=True,
        strike=True,
    )

    document, _ = _adapt(source, tmp_path)
    heading = document.blocks[0]
    assert isinstance(heading, ir.Heading)
    assert heading.level == 2
    assert heading.inlines == [
        ir.Emphasis("emph", [ir.Emphasis("strike", [ir.Text("styled")])]),
    ]


def test_empty_outline_paragraph_remains_boundary_not_heading(tmp_path: Path) -> None:
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:p>
        <w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr>
          <w:r><w:t>Title</w:t></w:r>
        </w:p>
        """,
    ))

    empty, title = source.body
    assert isinstance(empty, docx_source.SourceParagraphBlock)
    assert isinstance(title, docx_source.SourceParagraphBlock)
    assert empty.heading_level == title.heading_level == 1

    document, _ = _adapt(source, tmp_path)
    assert len(document.blocks) == 1
    assert isinstance(document.blocks[0], ir.Heading)
    assert inline_plain(document.blocks[0].inlines) == "Title"


def test_deep_outline_level_remains_a_source_fact_not_a_product_heading(
    tmp_path: Path,
) -> None:
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p><w:pPr><w:outlineLvl w:val="4"/></w:pPr>
          <w:r><w:t>Low-level outline entry</w:t></w:r>
        </w:p>
        """,
    ))

    block = source.body[0]
    assert isinstance(block, docx_source.SourceParagraphBlock)
    assert block.heading_level == 5

    document, diagnostics = _adapt(source, tmp_path)
    assert diagnostics == []
    assert len(document.blocks) == 1
    assert isinstance(document.blocks[0], ir.Paragraph)
    assert inline_plain(document.blocks[0].inlines) == "Low-level outline entry"


def test_indented_thematic_marker_is_not_reinterpreted_as_a_quote(
    tmp_path: Path,
) -> None:
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p><w:pPr><w:ind w:left="720"/></w:pPr>
          <w:r><w:t>* * *</w:t></w:r>
        </w:p>
        """,
    ))

    document, diagnostics = _adapt(source, tmp_path)
    assert diagnostics == []
    assert len(document.blocks) == 1
    assert isinstance(document.blocks[0], ir.Paragraph)
    assert inline_plain(document.blocks[0].inlines) == "* * *"


def test_left_indentation_projects_to_a_quote(tmp_path: Path) -> None:
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p><w:r><w:t>ordinary body baseline</w:t></w:r></w:p>
        <w:p><w:pPr><w:ind w:left="708"/></w:pPr>
          <w:r><w:t>quoted source row</w:t></w:r>
        </w:p>
        """,
    ))

    document, diagnostics = _adapt(source, tmp_path)
    assert diagnostics == []
    assert len(document.blocks) == 2
    quote = document.blocks[1]
    assert isinstance(quote, ir.QuoteBlock)
    assert len(quote.blocks) == 1
    paragraph = quote.blocks[0]
    assert isinstance(paragraph, ir.Paragraph)
    assert inline_plain(paragraph.inlines) == "quoted source row"


def test_hanging_indent_is_layout_not_a_quote(tmp_path: Path) -> None:
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p><w:r><w:t>ordinary body baseline</w:t></w:r></w:p>
        <w:p><w:pPr><w:ind w:left="720" w:hanging="720"/></w:pPr>
          <w:r><w:t>dialogue opener</w:t><w:br/><w:t>— reply</w:t></w:r>
        </w:p>
        """,
    ))

    document, diagnostics = _adapt(source, tmp_path)

    assert diagnostics == []
    assert len(document.blocks) == 2
    dialogue = document.blocks[1]
    assert isinstance(dialogue, ir.Paragraph)
    assert any(isinstance(inline, ir.LineBreak) for inline in dialogue.inlines)


def test_plain_reading_keeps_a_break_between_style_spans() -> None:
    assert inline_plain([
        ir.Emphasis("emph", [ir.Text("before"), ir.LineBreak()]),
        ir.Emphasis("strong", [ir.Text("after")]),
    ]) == "before after"


def test_style_toggles_and_product_emphasis_are_resolved_once(tmp_path: Path) -> None:
    styles = f"""
    <w:styles xmlns:w="{W_NS}">
      <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
        <w:name w:val="Normal"/>
      </w:style>
      <w:style w:type="paragraph" w:styleId="BoldBase">
        <w:name w:val="Bold base"/><w:rPr><w:b/></w:rPr>
      </w:style>
      <w:style w:type="paragraph" w:styleId="DoubleBold">
        <w:name w:val="Double bold"/><w:basedOn w:val="BoldBase"/>
        <w:rPr><w:b/></w:rPr>
      </w:style>
      <w:style w:type="paragraph" w:styleId="BoldHeading">
        <w:name w:val="Heading 1"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr>
        <w:rPr><w:b/></w:rPr>
      </w:style>
      <w:style w:type="paragraph" w:styleId="ItalicBody">
        <w:name w:val="Italic body"/><w:rPr><w:i/></w:rPr>
      </w:style>
    </w:styles>
    """
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p><w:pPr><w:pStyle w:val="BoldHeading"/></w:pPr>
          <w:r><w:t>Heading</w:t></w:r></w:p>
        <w:p><w:pPr><w:pStyle w:val="DoubleBold"/></w:pPr>
          <w:r><w:t>off </w:t></w:r>
          <w:r><w:rPr><w:b/></w:rPr><w:t>on</w:t></w:r></w:p>
        <w:p><w:pPr><w:pStyle w:val="ItalicBody"/></w:pPr>
          <w:r><w:t xml:space="preserve">one </w:t></w:r>
          <w:r><w:t xml:space="preserve"> two</w:t></w:r></w:p>
        """,
        styles=styles,
    ))

    heading, toggled, italic = source.body
    assert isinstance(heading, docx_source.SourceParagraphBlock)
    assert isinstance(toggled, docx_source.SourceParagraphBlock)
    assert isinstance(italic, docx_source.SourceParagraphBlock)
    heading_run = heading.inlines[0]
    assert isinstance(heading_run, docx_source.SourceRun)
    assert heading_run.properties.bold
    assert [
        run.properties.bold
        for run in toggled.inlines
        if isinstance(run, docx_source.SourceRun)
    ] == [False, True]
    assert all(
        run.properties.italic
        for run in italic.inlines
        if isinstance(run, docx_source.SourceRun)
    )

    document, diagnostics = _adapt(source, tmp_path)
    assert diagnostics == []
    adapted_heading, adapted_toggled, adapted_italic = document.blocks
    assert isinstance(adapted_heading, ir.Heading)
    assert adapted_heading.inlines == [ir.Text("Heading")]
    assert isinstance(adapted_toggled, ir.Paragraph)
    assert adapted_toggled.inlines == [
        ir.Text("off "),
        ir.Emphasis("strong", [ir.Text("on")]),
    ]
    assert isinstance(adapted_italic, ir.Paragraph)
    assert adapted_italic.inlines == [
        ir.Emphasis("emph", [ir.Text("one two")]),
    ]


def test_continuous_italic_span_absorbs_bold_runs(tmp_path: Path) -> None:
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p>
          <w:r><w:t xml:space="preserve">outside </w:t></w:r>
          <w:r><w:rPr><w:i/></w:rPr><w:t xml:space="preserve">italic </w:t></w:r>
          <w:r><w:rPr><w:b/><w:i/></w:rPr><w:t>bold</w:t></w:r>
          <w:r><w:rPr><w:i/></w:rPr><w:t xml:space="preserve"> continuation</w:t></w:r>
          <w:r><w:t xml:space="preserve"> outside</w:t></w:r>
        </w:p>
        """,
    ))

    document, diagnostics = _adapt(source, tmp_path)

    assert diagnostics == []
    paragraph = document.blocks[0]
    assert isinstance(paragraph, ir.Paragraph)
    assert paragraph.inlines == [
        ir.Text("outside "),
        ir.Emphasis("emph", [
            ir.Text("italic "),
            ir.Emphasis("strong", [ir.Text("bold")]),
            ir.Text(" continuation"),
        ]),
        ir.Text(" outside"),
    ]


def test_continuous_bold_span_absorbs_italic_runs(tmp_path: Path) -> None:
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p>
          <w:r><w:t xml:space="preserve">outside </w:t></w:r>
          <w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">bold </w:t></w:r>
          <w:r><w:rPr><w:b/><w:i/></w:rPr><w:t>italic</w:t></w:r>
          <w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve"> continuation</w:t></w:r>
          <w:r><w:t xml:space="preserve"> outside</w:t></w:r>
        </w:p>
        """,
    ))

    document, diagnostics = _adapt(source, tmp_path)

    assert diagnostics == []
    paragraph = document.blocks[0]
    assert isinstance(paragraph, ir.Paragraph)
    assert paragraph.inlines == [
        ir.Text("outside "),
        ir.Emphasis("strong", [
            ir.Text("bold "),
            ir.Emphasis("emph", [ir.Text("italic")]),
            ir.Text(" continuation"),
        ]),
        ir.Text(" outside"),
    ]


def test_nonbreaking_space_survives_emphasis_normalization(tmp_path: Path) -> None:
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p>
          <w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">cannot\u00a0</w:t></w:r>
          <w:r><w:rPr><w:b/><w:i/></w:rPr><w:t>break</w:t></w:r>
          <w:r><w:rPr><w:b/></w:rPr><w:t>.</w:t></w:r>
        </w:p>
        """,
    ))
    document, diagnostics = _adapt(source, tmp_path)

    assert diagnostics == []
    assert "cannot\u00a0*break*" in lower.lower(document, "ru", diagnostics)


def test_element_and_complex_field_hyperlinks_keep_targets(tmp_path: Path) -> None:
    relationships = (
        f'<Relationship Id="rIdLink" Type="{HYPERLINK_REL}" '
        'Target="https://ordinary.example" TargetMode="External"/>'
    )
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p>
          <w:hyperlink r:id="rIdLink"><w:r><w:t>ordinary</w:t></w:r></w:hyperlink>
          <w:r><w:t> and </w:t><w:fldChar w:fldCharType="begin"/></w:r>
          <w:r><w:instrText xml:space="preserve"> HYPERLINK "https://field.example" </w:instrText></w:r>
          <w:r><w:fldChar w:fldCharType="separate"/></w:r>
          <w:r><w:rPr><w:b/></w:rPr><w:t>field link</w:t></w:r>
          <w:r><w:fldChar w:fldCharType="end"/><w:t> number </w:t></w:r>
          <w:r><w:fldChar w:fldCharType="begin"/></w:r>
          <w:r><w:instrText> SEQ Figure </w:instrText></w:r>
          <w:r><w:fldChar w:fldCharType="separate"/><w:t>2</w:t></w:r>
          <w:r><w:fldChar w:fldCharType="end"/></w:r>
        </w:p>
        """,
        relationships=relationships,
    ))

    block = source.body[0]
    assert isinstance(block, docx_source.SourceParagraphBlock)
    fields = [inline for inline in block.inlines if isinstance(inline, docx_source.SourceField)]
    assert [field.kind for field in fields] == ["HYPERLINK", "SEQ"]
    assert block.reading == "ordinary and field link number 2"

    document, diagnostics = _adapt(source, tmp_path)
    assert diagnostics == []
    paragraph = document.blocks[0]
    assert isinstance(paragraph, ir.Paragraph)
    links = [inline for inline in paragraph.inlines if isinstance(inline, ir.Link)]
    assert [link.target for link in links] == [
        "https://ordinary.example",
        "https://field.example",
    ]
    assert inline_plain(paragraph.inlines) == "ordinary and field link number 2"


def test_adjacent_hyperlink_fragments_share_one_semantic_link(tmp_path: Path) -> None:
    relationships = (
        f'<Relationship Id="rIdLink" Type="{HYPERLINK_REL}" '
        'Target="https://books.example/title" TargetMode="External"/>'
    )
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p>
          <w:hyperlink r:id="rIdLink"><w:r><w:t xml:space="preserve">One </w:t></w:r></w:hyperlink>
          <w:hyperlink r:id="rIdLink"><w:r><w:t>title</w:t></w:r></w:hyperlink>
        </w:p>
        """,
        relationships=relationships,
    ))

    document, diagnostics = _adapt(source, tmp_path)
    assert diagnostics == []
    paragraph = document.blocks[0]
    assert isinstance(paragraph, ir.Paragraph)
    assert paragraph.inlines == [ir.Link(
        [ir.Text("One title")],
        "https://books.example/title",
    )]


def test_complex_field_identity_survives_across_table_paragraphs(tmp_path: Path) -> None:
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:tbl><w:tr><w:tc>
          <w:p>
            <w:r><w:fldChar w:fldCharType="begin"/></w:r>
            <w:r><w:instrText>HYPERLINK "https://books.example/title"</w:instrText></w:r>
            <w:r><w:fldChar w:fldCharType="separate"/></w:r>
          </w:p>
          <w:p><w:r><w:t>First title</w:t></w:r></w:p>
          <w:p><w:r><w:t>Second title</w:t><w:fldChar w:fldCharType="end"/></w:r></w:p>
        </w:tc></w:tr></w:tbl>
        """,
    ))

    table = source.body[0]
    assert isinstance(table, docx_source.SourceTableBlock)
    paragraphs = table.rows[0].cells[0].blocks
    fields = [
        inline
        for paragraph in paragraphs
        if isinstance(paragraph, docx_source.SourceParagraphBlock)
        for inline in paragraph.inlines
        if isinstance(inline, docx_source.SourceField)
    ]
    assert [field.kind for field in fields] == ["HYPERLINK", "HYPERLINK"]
    assert fields[0].field_id == fields[1].field_id
    assert source.diagnostics == ()

    document, diagnostics = _adapt(source, tmp_path)
    assert diagnostics == []
    adapted = document.blocks[0]
    assert isinstance(adapted, ir.Table)
    links = [inline for inline in adapted.rows[0][0] if isinstance(inline, ir.Link)]
    assert [inline_plain(link.children) for link in links] == [
        "First title",
        "Second title",
    ]
    assert {link.target for link in links} == {"https://books.example/title"}


def test_absent_table_grammar_fails_explicitly_instead_of_flattening(
    tmp_path: Path,
) -> None:
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:tbl>
          <w:tblPr><w:tblCaption w:val="Load-bearing caption"/></w:tblPr>
          <w:tr><w:tc>
            <w:tcPr><w:vMerge w:val="restart"/></w:tcPr>
            <w:p><w:r><w:t>outer cell</w:t></w:r></w:p>
            <w:tbl><w:tr><w:tc><w:p><w:r><w:t>nested cell</w:t></w:r></w:p>
            </w:tc></w:tr></w:tbl>
          </w:tc></w:tr>
        </w:tbl>
        """,
    ))

    assert [finding.code for finding in source.diagnostics] == [
        "source.table-caption-unsupported",
        "source.table-vertical-merge-unsupported",
        "source.table-nested-unsupported",
    ]
    _document, diagnostics = _adapt(source, tmp_path)
    assert [finding.severity for finding in diagnostics] == ["fatal", "fatal", "fatal"]


def _drawing(
    rel_id: str,
    *,
    svg: bool = False,
    svg_fallback_rel: str | None = None,
    alt: str | None = "picture",
    name: str = "Picture",
) -> str:
    blip = (
        f'<a:blip{f" r:embed={svg_fallback_rel!r}" if svg_fallback_rel else ""}>'
        f'<a:extLst><a:ext uri="svg"><asvg:svgBlip r:embed="{rel_id}"/>'
        "</a:ext></a:extLst></a:blip>"
        if svg
        else f'<a:blip r:embed="{rel_id}"/>'
    )
    description = f' descr="{alt}"' if alt is not None else ""
    return (
        f'<w:drawing><wp:inline><wp:docPr id="1" name="{name}"{description}/>'
        f'<a:graphic><a:graphicData><pic:pic><pic:blipFill>{blip}</pic:blipFill>'
        "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing>"
    )


def test_drawing_object_name_is_not_accessible_text(tmp_path: Path) -> None:
    relationships = (
        f'<Relationship Id="rIdImage" Type="{IMAGE_REL}" Target="media/picture.png"/>'
    )
    source = docx_source.read(_write_docx(
        tmp_path,
        f'<w:p><w:r>{_drawing("rIdImage", alt=None, name="Drawing 23")}</w:r>'
        '<w:r><w:t>body</w:t></w:r></w:p>',
        relationships=relationships,
        media={"word/media/picture.png": b"png bytes"},
    ))

    block = source.body[0]
    assert isinstance(block, docx_source.SourceParagraphBlock)
    run = block.inlines[0]
    assert isinstance(run, docx_source.SourceRun)
    image = run.children[0]
    assert isinstance(image, docx_source.SourceImage)
    assert image.alt == ""

    document, diagnostics = _adapt(source, tmp_path)
    assert diagnostics == []
    paragraph = document.blocks[0]
    assert isinstance(paragraph, ir.Paragraph)
    assert inline_plain(paragraph.inlines) == "body"


def test_symbol_font_codes_are_decoded_or_rejected_explicitly(tmp_path: Path) -> None:
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p><w:r><w:sym w:font="Wingdings" w:char="F04A"/></w:r></w:p>
        <w:p><w:r><w:sym w:font="Wingdings" w:char="F099"/></w:r></w:p>
        """,
    ))

    first = source.body[0]
    second = source.body[1]
    assert isinstance(first, docx_source.SourceParagraphBlock)
    assert isinstance(second, docx_source.SourceParagraphBlock)
    first_run = first.inlines[0]
    second_run = second.inlines[0]
    assert isinstance(first_run, docx_source.SourceRun)
    assert isinstance(second_run, docx_source.SourceRun)
    assert first_run.children == (
        docx_source.SourceSymbol("Wingdings", "☺", "F04A"),
    )
    assert second_run.children == (
        docx_source.SourceSymbol("Wingdings", "", "F099"),
    )

    document, diagnostics = _adapt(source, tmp_path)
    assert [
        inline_plain(block.inlines)
        for block in document.blocks
        if isinstance(block, ir.Paragraph)
    ] == ["☺", ""]
    assert diagnostics == [ir.Diagnostic(
        "warning",
        "import.source-inline-unsupported",
        "unsupported source inlines preserved explicitly: symbol:Wingdings:F099=1",
    )]


def test_images_svg_relationships_and_notes_are_first_class(tmp_path: Path) -> None:
    relationships = "".join([
        f'<Relationship Id="rIdPng" Type="{IMAGE_REL}" Target="media/picture.png"/>',
        f'<Relationship Id="rIdSvg" Type="{IMAGE_REL}" Target="media/vector.svg"/>',
        f'<Relationship Id="rIdSvgFallback" Type="{IMAGE_REL}" Target="media/vector.png"/>',
    ])
    footnotes = (
        f'<w:footnotes xmlns:w="{W_NS}">'
        '<w:footnote w:id="-1" w:type="separator"><w:p/></w:footnote>'
        '<w:footnote w:id="7"><w:p><w:r><w:t>note body</w:t></w:r></w:p></w:footnote>'
        "</w:footnotes>"
    )
    source = docx_source.read(_write_docx(
        tmp_path,
        f"""
        <w:p><w:r>{_drawing('rIdPng', alt='raster alt')}</w:r>
          <w:r>{_drawing('rIdSvg', svg=True, svg_fallback_rel='rIdSvgFallback', alt='vector alt')}</w:r>
          <w:r><w:footnoteReference w:id="7"/></w:r></w:p>
        """,
        relationships=relationships,
        footnotes=footnotes,
        media={
            "word/media/picture.png": b"png bytes",
            "word/media/vector.svg": b"<svg/>",
            "word/media/vector.png": b"fallback png bytes",
        },
    ))

    assert [(part.part_name, part.data) for part in source.media] == [
        ("word/media/picture.png", b"png bytes"),
        ("word/media/vector.svg", b"<svg/>"),
    ]
    note, = source.notes
    assert (note.kind, note.note_id) == (docx_source.NoteKind.FOOTNOTE, 7)
    assert docx_source.block_readings(note.blocks) == ("note body",)

    document, diagnostics = _adapt(source, tmp_path)
    assert diagnostics == []
    paragraph = document.blocks[0]
    assert isinstance(paragraph, ir.Paragraph)
    images = [inline for inline in paragraph.inlines if isinstance(inline, ir.ImageInline)]
    assert [image.alt for image in images] == ["raster alt", "vector alt"]
    assert [Path(image.src).read_bytes() for image in images] == [b"png bytes", b"<svg/>"]
    assert any(isinstance(inline, ir.FootnoteRef) for inline in paragraph.inlines)
    assert len(document.footnotes) == 1
    note_block = document.footnotes[0].blocks[0]
    assert isinstance(note_block, ir.Paragraph)
    assert inline_plain(note_block.inlines) == "note body"


def test_numbering_tables_and_source_order_are_structured(tmp_path: Path) -> None:
    numbering = f"""
    <w:numbering xmlns:w="{W_NS}">
      <w:abstractNum w:abstractNumId="9">
        <w:lvl w:ilvl="0"><w:start w:val="4"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>
        <w:lvl w:ilvl="1"><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/></w:lvl>
      </w:abstractNum>
      <w:num w:numId="3"><w:abstractNumId w:val="9"/></w:num>
    </w:numbering>
    """
    item = (
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="3"/>'
        '</w:numPr></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>'
    )
    source = docx_source.read(_write_docx(
        tmp_path,
        item.format(level=0, text="four")
        + item.format(level=0, text="five")
        + item.format(level=1, text="nested")
        + """
          <w:tbl><w:tr>
            <w:tc><w:p><w:r><w:t>left</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>right</w:t></w:r></w:p></w:tc>
          </w:tr></w:tbl>
        """,
        numbering=numbering,
    ))

    assert [paragraph.text for paragraph in source.paragraphs] == ["four", "five", "nested"]
    assert [paragraph.numbering.level for paragraph in source.paragraphs if paragraph.numbering] == [0, 0, 1]
    assert source.body_readings == ("four", "five", "nested", "left", "right")

    document, diagnostics = _adapt(source, tmp_path)
    assert diagnostics == []
    listing = document.blocks[0]
    assert isinstance(listing, ir.ListBlock)
    assert listing.ordered and listing.start == 4
    assert len(listing.items) == 2
    nested = listing.items[1][1]
    assert isinstance(nested, ir.ListBlock) and not nested.ordered
    table = document.blocks[1]
    assert isinstance(table, ir.Table)
    assert [[inline_plain(cell) for cell in row] for row in table.rows] == [["left", "right"]]


def test_numbered_heading_remains_a_section_boundary(tmp_path: Path) -> None:
    numbering = f"""
    <w:numbering xmlns:w="{W_NS}">
      <w:abstractNum w:abstractNumId="9">
        <w:lvl w:ilvl="0"><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/></w:lvl>
      </w:abstractNum>
      <w:num w:numId="3"><w:abstractNumId w:val="9"/></w:num>
    </w:numbering>
    """
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p><w:pPr><w:outlineLvl w:val="1"/><w:numPr>
          <w:ilvl w:val="0"/><w:numId w:val="3"/>
        </w:numPr></w:pPr><w:r><w:t>Section</w:t></w:r></w:p>
        """,
        numbering=numbering,
    ))

    block = source.body[0]
    assert isinstance(block, docx_source.SourceParagraphBlock)
    assert block.heading_level == 2
    assert block.numbering is not None

    document, _ = _adapt(source, tmp_path)
    assert len(document.blocks) == 1
    assert isinstance(document.blocks[0], ir.Heading)
    assert inline_plain(document.blocks[0].inlines) == "Section"


def test_text_boxes_rules_and_unknowns_are_distinct_source_facts(tmp_path: Path) -> None:
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p><w:r><w:t>before </w:t><w:pict><v:shape><v:textbox><w:txbxContent>
          <w:p><w:r><w:t>inside</w:t></w:r></w:p>
        </w:txbxContent></v:textbox></v:shape></w:pict><w:t> after</w:t></w:r></w:p>
        <w:p><w:r><w:pict><v:rect o:hr="t"/></w:pict></w:r></w:p>
        <w:p><m:oMath><m:r><m:t>x+y</m:t></m:r></m:oMath></w:p>
        <w:future><w:r><w:t>future block</w:t></w:r></w:future>
        """,
    ))

    first = source.body[0]
    assert isinstance(first, docx_source.SourceParagraphBlock)
    assert first.paragraph is source.paragraphs[0]
    assert first.reading == first.paragraph.content.reading == "before inside after"
    second = source.body[1]
    assert isinstance(second, docx_source.SourceParagraphBlock)
    run = second.inlines[0]
    assert isinstance(run, docx_source.SourceRun)
    assert run.children == (docx_source.SourceHorizontalRule(),)
    third = source.body[2]
    assert isinstance(third, docx_source.SourceParagraphBlock)
    assert isinstance(third.inlines[0], docx_source.SourceUnknownInline)
    assert isinstance(source.body[3], docx_source.SourceUnknownBlock)

    document, diagnostics = _adapt(source, tmp_path)
    assert [
        inline_plain(block.inlines)
        for block in document.blocks[:3]
        if isinstance(block, ir.Paragraph)
    ] == ["before", "inside", "after"]
    assert isinstance(document.blocks[3], ir.ThematicBreak)
    assert document.blocks[3].source_span == ir.SourceProvenance(1, 1)
    assert any(isinstance(block, ir.UnknownBlock) for block in document.blocks)
    assert {diagnostic.code for diagnostic in diagnostics} == {
        "import.source-inline-unsupported",
        "import.source-block-unsupported",
    }


def test_mixed_content_horizontal_rule_preserves_block_order(tmp_path: Path) -> None:
    source = docx_source.read(_write_docx(
        tmp_path,
        """
        <w:p><w:r>
          <w:t>before</w:t>
          <w:pict><v:rect o:hr="t"/></w:pict>
          <w:t>after</w:t>
        </w:r></w:p>
        """,
    ))

    document, diagnostics = _adapt(source, tmp_path)

    assert diagnostics == []
    assert [type(block) for block in document.blocks] == [
        ir.Paragraph,
        ir.ThematicBreak,
        ir.Paragraph,
    ]
    before, rule, after = document.blocks
    assert isinstance(before, ir.Paragraph)
    assert isinstance(rule, ir.ThematicBreak)
    assert isinstance(after, ir.Paragraph)
    assert inline_plain(before.inlines) == "before"
    assert inline_plain(after.inlines) == "after"
    assert before.source_span is not None
    assert (before.source_span.start, before.source_span.end) == (0, 0)
    assert rule.source_span is None
    assert after.source_span is None


def test_real_text_box_document_needs_no_identity_reconstruction(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    source = docx_source.read(
        root / "src/content/books/27-mikki-17/ru.docx"
    )
    linked = [
        block
        for block in _paragraph_blocks(source.body)
        if block.paragraph is not None
    ]

    assert [block.paragraph for block in linked] == list(source.paragraphs)
    assert all(
        block.reading == block.paragraph.content.reading
        for block in linked
        if block.paragraph is not None
    )
    assert any(
        isinstance(inline, docx_source.SourceTextBox)
        for block in linked
        for inline in block.inlines
        if isinstance(inline, docx_source.SourceRun)
        for inline in inline.children
    )
    document, diagnostics = _adapt(source, tmp_path)
    assert document.blocks
    assert not any(diagnostic.severity == "fatal" for diagnostic in diagnostics)
