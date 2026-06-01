from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest

from pancratius import docx_outline

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC_NS = "http://schemas.openxmlformats.org/drawingml/2006/picture"
W = f"{{{W_NS}}}"


def _para(text: str, style: str | None = None) -> str:
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>"


def _drawing_para() -> str:
    return (
        "<w:p><w:r><w:drawing><wp:inline>"
        '<wp:extent cx="1" cy="1"/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        "<pic:pic><pic:blipFill>"
        '<a:blip r:embed="rId8"/>'
        "</pic:blipFill></pic:pic>"
        "</a:graphicData></a:graphic>"
        "</wp:inline></w:drawing></w:r></w:p>"
    )


def _toc_block() -> str:
    return (
        "<w:sdt><w:sdtContent><w:p>"
        "<w:r><w:instrText>TOC \\o &quot;1-3&quot; \\h \\z \\u</w:instrText></w:r>"
        "<w:r><w:t>Contents</w:t></w:r>"
        "</w:p></w:sdtContent></w:sdt>"
    )


def _write_docx(path: Path, *paragraphs: str, existing_heading2: bool = False) -> None:
    heading2 = """
  <w:style w:type="paragraph" w:styleId="21">
    <w:name w:val="heading 2"/>
    <w:pPr><w:outlineLvl w:val="1"/></w:pPr>
  </w:style>
""" if existing_heading2 else ""
    styles = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:styles xmlns:w="{W_NS}">
  <w:style w:type="paragraph" w:styleId="1">
    <w:name w:val="heading 1"/>
    <w:pPr><w:outlineLvl w:val="0"/></w:pPr>
  </w:style>
  {heading2}
  <w:style w:type="paragraph" w:styleId="Body">
    <w:name w:val="body"/>
  </w:style>
</w:styles>
"""
    document = f"""<?xml version="1.0" encoding="UTF-8"?>
<w:document
  xmlns:w="{W_NS}"
  xmlns:r="{R_NS}"
  xmlns:wp="{WP_NS}"
  xmlns:a="{A_NS}"
  xmlns:pic="{PIC_NS}">
  <w:body>
    {''.join(paragraphs)}
    <w:sectPr/>
  </w:body>
</w:document>
"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/styles.xml", styles)
        zf.writestr("word/document.xml", document)


def _document_root(path: Path) -> ET.Element:
    with zipfile.ZipFile(path) as zf:
        return ET.fromstring(zf.read("word/document.xml"))


def _styles_root(path: Path) -> ET.Element:
    with zipfile.ZipFile(path) as zf:
        return ET.fromstring(zf.read("word/styles.xml"))


def _text(p: ET.Element) -> str:
    return "".join(t.text or "" for t in p.findall(f".//{W}t"))


def _style(p: ET.Element) -> str:
    style = p.find(f"./{W}pPr/{W}pStyle")
    return "" if style is None else style.get(f"{W}val", "")


def test_part_outline_inserts_parts_and_demotes_chapters(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    _write_docx(
        source,
        _para("Chapter 1. One", "1"),
        _para("Body", "Body"),
        _para("Chapter 1. Two", "1"),
    )

    summary = docx_outline.apply_part_outline(
        source,
        output,
        (
            docx_outline.PartBoundary("Part 1", "Chapter 1. One"),
            docx_outline.PartBoundary("Part 2", "Chapter 1. Two"),
        ),
    )

    assert summary.inserted_parts == 2
    assert summary.removed_toc_blocks == 0
    assert summary.demoted_headings == 2
    assert summary.cleared_empty_headings == 0

    paragraphs = _document_root(output).findall(f".//{W}body/{W}p")
    assert [_text(p) for p in paragraphs[:5]] == [
        "Part 1",
        "Chapter 1. One",
        "Body",
        "Part 2",
        "Chapter 1. Two",
    ]
    assert [_style(p) for p in paragraphs[:5]] == ["1", "Heading2", "Body", "1", "Heading2"]

    heading2 = _styles_root(output).find(f".//{W}style[@{W}styleId='Heading2']")
    assert heading2 is not None
    outline = heading2.find(f"{W}pPr/{W}outlineLvl")
    assert outline is not None
    assert outline.get(f"{W}val") == "1"


def test_part_outline_rejects_ambiguous_markers(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    _write_docx(
        source,
        _para("Chapter 1. Repeated", "1"),
        _para("Chapter 1. Repeated again", "1"),
    )

    with pytest.raises(docx_outline.DocxOutlineError, match="ambiguous"):
        docx_outline.apply_part_outline(
            source,
            output,
            (docx_outline.PartBoundary("Part 1", "Chapter 1."),),
        )


def test_part_outline_rejects_two_part_specs_targeting_same_paragraph(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    _write_docx(source, _para("Chapter 1. One", "1"))

    with pytest.raises(docx_outline.DocxOutlineError, match="target the same paragraph"):
        docx_outline.apply_part_outline(
            source,
            output,
            (
                docx_outline.PartBoundary("Part 1", "Chapter 1"),
                docx_outline.PartBoundary("Part 2", "Chapter 1. One"),
            ),
        )


def test_part_outline_preserves_preface_heading_before_first_part(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    _write_docx(
        source,
        _para("Preface", "1"),
        _para("Chapter 1. One", "1"),
        existing_heading2=True,
    )

    summary = docx_outline.apply_part_outline(
        source,
        output,
        (docx_outline.PartBoundary("Part 1", "Chapter 1. One"),),
    )

    assert summary.demoted_headings == 1
    paragraphs = _document_root(output).findall(f".//{W}body/{W}p")
    assert [_text(p) for p in paragraphs[:3]] == ["Preface", "Part 1", "Chapter 1. One"]
    assert [_style(p) for p in paragraphs[:3]] == ["1", "1", "21"]


def test_part_outline_reuses_existing_heading2_style(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    _write_docx(
        source,
        _para("Chapter 1. One", "1"),
        _para("Body", "Body"),
        existing_heading2=True,
    )

    summary = docx_outline.apply_part_outline(
        source,
        output,
        (docx_outline.PartBoundary("Part 1", "Chapter 1. One"),),
    )

    assert summary.demoted_headings == 1
    paragraphs = _document_root(output).findall(f".//{W}body/{W}p")
    assert [_style(p) for p in paragraphs[:3]] == ["1", "21", "Body"]
    styles = _styles_root(output).findall(f".//{W}style")
    heading2_named = [
        style.get(f"{W}styleId")
        for style in styles
        if (name := style.find(f"{W}name")) is not None and name.get(f"{W}val") == "heading 2"
    ]
    assert heading2_named == ["21"]


def test_part_outline_removes_generated_toc_sdt(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    _write_docx(
        source,
        _toc_block(),
        _para("Chapter 1. One", "1"),
        existing_heading2=True,
    )

    summary = docx_outline.apply_part_outline(
        source,
        output,
        (docx_outline.PartBoundary("Part 1", "Chapter 1. One"),),
    )

    assert summary.removed_toc_blocks == 1
    paragraphs = _document_root(output).findall(f".//{W}body/{W}p")
    assert [_text(p) for p in paragraphs[:2]] == ["Part 1", "Chapter 1. One"]
    with zipfile.ZipFile(output) as zf:
        document = zf.read("word/document.xml").decode()
    assert "TOC " not in document
    assert "Contents" not in document


def test_part_outline_clears_empty_heading_paragraphs(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    _write_docx(
        source,
        _para("Chapter 1. One", "1"),
        _para("", "1"),
        existing_heading2=True,
    )

    summary = docx_outline.apply_part_outline(
        source,
        output,
        (docx_outline.PartBoundary("Part 1", "Chapter 1. One"),),
    )

    assert summary.demoted_headings == 1
    assert summary.cleared_empty_headings == 1
    paragraphs = _document_root(output).findall(f".//{W}body/{W}p")
    assert [_style(p) for p in paragraphs[:3]] == ["1", "21", ""]


def test_part_outline_refuses_already_inserted_part_heading(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    _write_docx(
        source,
        _para("Part 1", "1"),
        _para("Chapter 1. One", "Heading2"),
    )

    with pytest.raises(docx_outline.DocxOutlineError, match="already exist"):
        docx_outline.apply_part_outline(
            source,
            output,
            (docx_outline.PartBoundary("Part 1", "Chapter 1. One"),),
        )


def test_part_outline_preserves_drawing_namespace_prefixes(tmp_path: Path) -> None:
    source = tmp_path / "source.docx"
    output = tmp_path / "output.docx"
    _write_docx(source, _para("Chapter 1. One", "1"), _drawing_para())

    docx_outline.apply_part_outline(
        source,
        output,
        (docx_outline.PartBoundary("Part 1", "Chapter 1. One"),),
    )

    with zipfile.ZipFile(output) as zf:
        document = zf.read("word/document.xml").decode()

    assert "a:blip" in document
    assert 'r:embed="rId8"' in document
    assert "ns0:" not in document
