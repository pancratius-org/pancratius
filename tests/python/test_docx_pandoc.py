"""The semantics-preserving DOCX projection consumed by Pandoc.

Some source DOCX bind the correct OOXML drawing URIs to GENERIC prefixes
(`ns3:`/`ns5:`/`ns7:` …); pandoc 3.x then resolves no images and drops every one. The adapter
re-prefixes such a doc before pandoc reads it — changing prefixes only, never URIs, so the
recovered images are real and no text is lost. A conventionally-prefixed DOCX is passed through
untouched when it also has no page/column breaks.
"""

from __future__ import annotations

import shutil
import warnings
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
from docx import Document
from docx.enum.text import WD_BREAK
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import qn

from pancratius import docx_adapter as da
from pancratius import docx_pandoc, docx_source, ir, ooxml
from pancratius.ir.inlines import inline_plain

pandoc_required = pytest.mark.skipif(
    shutil.which("pandoc") is None,
    reason="pandoc is required for importer-backed DOCX paths",
)

_REPO = Path(__file__).resolve().parents[2]
# A conventionally-prefixed book (images already work) and a generic-prefix book (images dropped
# by pandoc until canonicalized) — both small, both committed.
_CONVENTIONAL = _REPO / "src/content/books/11-kniga-ischezayushchego-ya/en.docx"
_GENERIC = _REPO / "src/content/books/27-mikki-17/ru.docx"
_BOOK17 = _REPO / "src/content/books/17-obolgannye-iisus-i-tvorets/ru.docx"
_GENERIC_IMAGE_RICH = (
    _REPO / "src/content/books/33-ya-esm-vsadnik-kon-i-mech/ru.docx"
)
_GENERIC_NAMESPACE_DOCX = (_GENERIC, _GENERIC_IMAGE_RICH)


def test_inline_text_keeps_break_policy_and_hides_opaque_payload() -> None:
    inlines = [
        {"t": "Str", "c": "first"},
        {"t": "SoftBreak"},
        {"t": "Str", "c": "second"},
        {"t": "LineBreak"},
        {
            "t": "Note",
            "c": [{"t": "Para", "c": [{"t": "Str", "c": "MINTED"}]}],
        },
        {"t": "Image", "c": [["", [], []], [], ["x.png", ""]]},
        {"t": "Cite", "c": [[], [{"t": "Str", "c": "cited"}]]},
        {"t": "Space"},
        {"t": "Math", "c": [{"t": "InlineMath"}, "x+y"]},
        {"t": "RawInline", "c": ["html", "<b>OPAQUE</b>"]},
        {"t": "Str", "c": "third"},
    ]

    assert (
        docx_pandoc.inline_text(
            inlines,
            soft_break=docx_pandoc.SoftBreakRendering.SPACE,
        )
        == "first second\ncited x+ythird"
    )
    assert (
        docx_pandoc.inline_text(
            inlines,
            soft_break=docx_pandoc.SoftBreakRendering.LINE,
        )
        == "first\nsecond\ncited x+ythird"
    )


@pytest.mark.parametrize(
    "inlines",
    [
        [{"t": "Str", "c": ["not text"]}],
        [{"t": "Cite", "c": [[], "not inlines"]}],
        [{"t": "Future", "c": [{"t": "Str", "c": "LEAK"}]}],
    ],
)
def test_inline_text_rejects_malformed_or_unknown_payload(inlines: object) -> None:
    with pytest.raises(docx_pandoc.PandocInlineError):
        docx_pandoc.inline_text(
            inlines,
            soft_break=docx_pandoc.SoftBreakRendering.SPACE,
        )


def test_conventional_docx_is_passed_through_unchanged(tmp_path: Path) -> None:
    source = docx_source.read(_CONVENTIONAL)
    assert docx_pandoc.project_package(source, tmp_path) == _CONVENTIONAL


def test_alphabetic_source_alias_is_canonicalized_for_pandoc() -> None:
    xml = (
        f'<w:document xmlns:w="{ooxml.W_NS}" xmlns:foo="{ooxml.WP_NS}">'
        '<w:body><foo:inline/></w:body></w:document>'
    ).encode()

    projection = docx_pandoc._project_part(docx_source.DOCUMENT_PART, xml)

    assert b"<wp:inline" in projection
    assert ET.fromstring(projection).find(f".//{{{ooxml.WP_NS}}}inline") is not None


def _body_words(docx: Path, media: Path) -> list[str]:
    from pancratius import ir
    from pancratius.ir.inlines import inline_plain

    doc = da.adapt(docx_source.read(docx), media, [])
    text = " ".join(inline_plain(b.inlines) for b in doc.blocks
                    if isinstance(b, ir.Paragraph) and b.inlines)
    return text.split()


@pandoc_required
def test_canonicalization_loses_no_body_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Real comparison: import WITHOUT the rewrite (force passthrough) vs WITH it. The rewrite
    # is strictly ADDITIVE — it recovers images and the body words pandoc dropped alongside them
    # (e.g. textbox text), and removes nothing. So every passthrough word survives in the
    # rewritten import; a regression that corrupted existing text would drop one here.
    rewritten = _body_words(_GENERIC_IMAGE_RICH, tmp_path / "with")

    monkeypatch.setattr(
        docx_pandoc,
        "project_package",
        lambda source, _work_dir: source.path,
    )
    passthrough = _body_words(_GENERIC_IMAGE_RICH, tmp_path / "without")

    remaining = iter(rewritten)
    assert len(passthrough) > 1000
    assert all(any(candidate == word for candidate in remaining) for word in passthrough)
    assert len(rewritten) > len(passthrough)


def _break_docx(tmp_path: Path) -> Path:
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("before")
    paragraph.add_run().add_break(WD_BREAK.PAGE)
    paragraph.add_run("middle")
    paragraph.add_run().add_break(WD_BREAK.LINE)
    paragraph.add_run("line")
    paragraph.add_run().add_break(WD_BREAK.COLUMN)
    paragraph.add_run("after")
    path = tmp_path / "typed-breaks.docx"
    document.save(str(path))
    return path


def _pagination_only_docx(tmp_path: Path) -> Path:
    document = Document()
    document.add_paragraph("before")
    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    document.add_paragraph("after")
    page_before = document.add_paragraph()
    page_before.paragraph_format.page_break_before = True
    document.add_paragraph("end")
    document.add_paragraph()  # a real empty paragraph remains a structural boundary
    path = tmp_path / "pagination-only.docx"
    document.save(str(path))
    return path


def _fallback_pagination_only_docx(tmp_path: Path) -> Path:
    document = Document()
    document.add_paragraph("before")
    run = document.add_paragraph().add_run()
    run._r.append(
        parse_xml(
            f'<mc:AlternateContent xmlns:mc="{ooxml.MC_NS}" '
            f'xmlns:w="{ooxml.W_NS}" xmlns:x="urn:unsupported">'
            '<mc:Choice Requires="x"><w:t>INACTIVE</w:t></mc:Choice>'
            '<mc:Fallback><w:br w:type="page"/></mc:Fallback>'
            '</mc:AlternateContent>'
        )
    )
    document.add_paragraph("after")
    path = tmp_path / "fallback-pagination-only.docx"
    document.save(str(path))
    return path


def _opaque_pagination_docx(tmp_path: Path) -> Path:
    document = Document()
    document.add_paragraph("before")

    reference = document.add_paragraph()
    reference.add_run().add_break(WD_BREAK.PAGE)
    footnote = OxmlElement("w:footnoteReference")
    footnote.set(qn("w:id"), "1")
    reference.add_run()._r.append(footnote)

    equation = document.add_paragraph()
    equation.add_run().add_break(WD_BREAK.PAGE)
    equation._p.append(OxmlElement("m:oMath"))

    anchored = document.add_paragraph()
    anchored.add_run().add_break(WD_BREAK.PAGE)
    bookmark = OxmlElement("w:bookmarkStart")
    bookmark.set(qn("w:id"), "9")
    bookmark.set(qn("w:name"), "anchor")
    anchored._p.append(bookmark)
    anchored.add_run()._r.append(OxmlElement("w:fldChar"))

    section = document.add_paragraph()
    section.paragraph_format.page_break_before = True
    section._p.get_or_add_pPr().append(OxmlElement("w:sectPr"))
    document.add_paragraph("after")
    path = tmp_path / "opaque-pagination.docx"
    document.save(str(path))
    return path


def test_break_projection_neutralizes_only_pagination(tmp_path: Path) -> None:
    path = _break_docx(tmp_path)
    with zipfile.ZipFile(path) as archive:
        document_xml = archive.read("word/document.xml")

    projection = docx_pandoc._project_part(
        docx_source.DOCUMENT_PART,
        document_xml,
        docx_source.read(path),
    )

    root = ET.fromstring(projection)
    remaining = root.findall(f".//{docx_source.W}br")
    assert len(remaining) == 1
    assert docx_source.BreakKind.from_ooxml(
        remaining[0].get(f"{docx_source.W}type")
    ) is docx_source.BreakKind.LINE


def test_inactive_choice_paragraph_is_never_removed() -> None:
    xml = (
        f'<w:footnotes xmlns:w="{ooxml.W_NS}" '
        f'xmlns:mc="{ooxml.MC_NS}" xmlns:x="urn:unsupported">'
        '<w:footnote w:id="1"><w:p><w:r><mc:AlternateContent>'
        '<mc:Choice Requires="x"><w:p><w:r>'
        '<w:br w:type="page"/>'
        '</w:r></w:p></mc:Choice>'
        '<mc:Fallback><w:t>BASELINE</w:t></mc:Fallback>'
        '</mc:AlternateContent></w:r></w:p></w:footnote></w:footnotes>'
    ).encode()

    projection = docx_pandoc._project_part("word/footnotes.xml", xml)

    root = ET.fromstring(projection)
    paragraphs = root.findall(f".//{docx_source.W}p")
    assert len(paragraphs) == 1
    assert root.find(f".//{docx_source.W}br") is None
    assert root.find(f".//{ooxml.MC_ALTERNATE_CONTENT}") is None
    assert "".join(element.text or "" for element in root.iter(f"{docx_source.W}t")) == "BASELINE"


@pandoc_required
def test_pagination_only_paragraphs_do_not_mint_empty_ir_blocks(tmp_path: Path) -> None:
    path = _pagination_only_docx(tmp_path)
    source = docx_source.read(path)

    assert [
        paragraph.reconciliation_position.value
        if paragraph.reconciliation_position is not None
        else None
        for paragraph in source.paragraphs
    ] == [0, None, 1, None, 2, 3]
    assert source.paragraphs[1].disposition is docx_source.ParagraphDisposition.PAGINATION_ONLY
    assert source.paragraphs[3].disposition is docx_source.ParagraphDisposition.PAGINATION_ONLY

    projected = docx_pandoc.project_package(source, tmp_path / "projected")
    with zipfile.ZipFile(projected) as archive:
        root = ET.fromstring(archive.read(docx_source.DOCUMENT_PART))
    body = root.find(f"{docx_source.W}body")
    assert body is not None
    assert len(docx_source.body_paragraph_elements(body)) == 4

    imported = da.adapt(source, tmp_path / "media", [])
    assert [
        inline_plain(block.inlines)
        for block in imported.blocks
        if isinstance(block, ir.Paragraph)
    ] == ["before", "after", "end", ""]


def test_pagination_never_deletes_opaque_source_payload(tmp_path: Path) -> None:
    path = _opaque_pagination_docx(tmp_path)
    source = docx_source.read(path)
    assert [paragraph.disposition for paragraph in source.paragraphs[1:5]] == [
        docx_source.ParagraphDisposition.NON_TEXT,
        docx_source.ParagraphDisposition.NON_TEXT,
        docx_source.ParagraphDisposition.NON_TEXT,
        docx_source.ParagraphDisposition.NON_TEXT,
    ]

    projected = docx_pandoc.project_package(source, tmp_path / "projected")
    with zipfile.ZipFile(projected) as archive:
        root = ET.fromstring(archive.read(docx_source.DOCUMENT_PART))

    assert len(root.findall(f".//{docx_source.W}footnoteReference")) == 1
    assert len(root.findall(".//{http://schemas.openxmlformats.org/officeDocument/2006/math}oMath")) == 1
    assert len(root.findall(f".//{docx_source.W}bookmarkStart")) == 1
    assert len(root.findall(f".//{docx_source.W}fldChar")) == 1
    assert len(root.findall(f".//{docx_source.W}sectPr")) >= 2


def test_alternate_content_uses_fallback_for_source_and_pagination() -> None:
    """An unselected Choice cannot contribute text or pagination evidence."""
    xml = (
        f'<w:document xmlns:w="{ooxml.W_NS}" '
        f'xmlns:mc="{ooxml.MC_NS}" xmlns:x="urn:unsupported">'
        '<w:body><w:p><w:r><mc:AlternateContent>'
        '<mc:Choice Requires="x"><w:r><w:br w:type="page"/></w:r></mc:Choice>'
        '<mc:Fallback><w:br/><w:t>VISIBLE FALLBACK</w:t></mc:Fallback>'
        '</mc:AlternateContent></w:r></w:p></w:body></w:document>'
    ).encode()
    paragraph = ET.fromstring(xml).find(f".//{docx_source.W}p")
    assert paragraph is not None

    semantics = docx_source.analyze_paragraph(paragraph)
    projection = docx_pandoc._project_part(docx_source.DOCUMENT_PART, xml)

    assert semantics.content.reading.strip() == "VISIBLE FALLBACK"
    assert semantics.content.lineated == "\nVISIBLE FALLBACK"
    assert semantics.content.breaks == (docx_source.BreakKind.LINE,)
    assert semantics.disposition is docx_source.ParagraphDisposition.CONTENT
    assert b"VISIBLE FALLBACK" in projection
    assert ET.fromstring(projection).find(f".//{ooxml.MC_ALTERNATE_CONTENT}") is None


def test_alternate_content_without_fallback_remains_opaque() -> None:
    xml = (
        f'<w:document xmlns:w="{ooxml.W_NS}" '
        f'xmlns:mc="{ooxml.MC_NS}" xmlns:x="urn:unsupported">'
        '<w:body><w:p><w:r><mc:AlternateContent>'
        '<mc:Choice Requires="x"><w:t>CHOICE ONLY</w:t></mc:Choice>'
        '</mc:AlternateContent></w:r></w:p></w:body></w:document>'
    ).encode()
    paragraph = ET.fromstring(xml).find(f".//{docx_source.W}p")
    assert paragraph is not None

    semantics = docx_source.analyze_paragraph(paragraph)
    projection = docx_pandoc._project_part(docx_source.DOCUMENT_PART, xml)

    assert semantics.content.atoms == ()
    assert semantics.has_opaque_payload
    assert semantics.disposition is docx_source.ParagraphDisposition.NON_TEXT
    assert len(ET.fromstring(projection).findall(f".//{docx_source.W}p")) == 1
    assert b"CHOICE ONLY" not in projection


def test_alternate_content_outside_run_contributes_no_atoms() -> None:
    xml = (
        f'<w:document xmlns:w="{ooxml.W_NS}" '
        f'xmlns:mc="{ooxml.MC_NS}"><w:body><w:p><mc:AlternateContent>'
        '<mc:Fallback><w:r><w:t>WRONG PLACEMENT</w:t></w:r></mc:Fallback>'
        '</mc:AlternateContent></w:p></w:body></w:document>'
    ).encode()
    paragraph = ET.fromstring(xml).find(f".//{docx_source.W}p")
    assert paragraph is not None
    semantics = docx_source.analyze_paragraph(paragraph)
    projection = docx_pandoc._project_part(docx_source.DOCUMENT_PART, xml)

    root = ET.fromstring(projection)
    assert semantics.content.atoms == ()
    assert semantics.disposition is docx_source.ParagraphDisposition.NON_TEXT
    assert len(root.findall(f".//{docx_source.W}p")) == 1
    assert root.find(f".//{ooxml.MC_ALTERNATE_CONTENT}") is None
    assert b"WRONG PLACEMENT" not in projection


def test_nested_alternate_content_keeps_its_original_capability_context() -> None:
    xml = (
        f'<w:document xmlns:w="{ooxml.W_NS}" '
        f'xmlns:mc="{ooxml.MC_NS}" xmlns:x="urn:unsupported">'
        '<w:body><w:p><w:r><mc:AlternateContent>'
        '<mc:Choice Requires="x"><w:t>OUTER CHOICE</w:t></mc:Choice>'
        '<mc:Fallback><mc:AlternateContent>'
        '<mc:Choice Requires="x"><w:t>INNER CHOICE</w:t></mc:Choice>'
        '<mc:Fallback><w:t>LEAKED</w:t></mc:Fallback>'
        '</mc:AlternateContent></mc:Fallback>'
        '</mc:AlternateContent></w:r></w:p></w:body></w:document>'
    ).encode()
    paragraph = ET.fromstring(xml).find(f".//{docx_source.W}p")
    assert paragraph is not None

    semantics = docx_source.analyze_paragraph(paragraph)
    projection = docx_pandoc._project_part(docx_source.DOCUMENT_PART, xml)
    projected_root = ET.fromstring(projection)

    assert semantics.content.atoms == ()
    assert semantics.disposition is docx_source.ParagraphDisposition.NON_TEXT
    assert projected_root.find(f".//{ooxml.MC_ALTERNATE_CONTENT}") is None
    assert b"LEAKED" not in projection


def test_removed_choices_cannot_conflict_in_namespace_closure() -> None:
    xml = (
        f'<w:document xmlns:w="{ooxml.W_NS}" xmlns:mc="{ooxml.MC_NS}">'
        '<w:body><w:p><w:r><mc:AlternateContent>'
        '<mc:Choice xmlns:x="urn:choice-one" Requires="x">'
        '<w:t>FIRST</w:t></mc:Choice>'
        '<mc:Choice xmlns:x="urn:choice-two" Requires="x">'
        '<w:t>SECOND</w:t></mc:Choice>'
        '<mc:Fallback><w:t>BASELINE</w:t></mc:Fallback>'
        '</mc:AlternateContent></w:r></w:p></w:body></w:document>'
    ).encode()

    projection = docx_pandoc._project_part(docx_source.DOCUMENT_PART, xml)

    assert b"BASELINE" in projection
    assert b"FIRST" not in projection
    assert b"SECOND" not in projection


def test_removed_pagination_attributes_cannot_conflict_in_namespace_closure() -> None:
    xml = (
        f'<w:document xmlns:w="{ooxml.W_NS}" xmlns:mc="{ooxml.MC_NS}">'
        '<w:body>'
        '<w:p><w:r><w:t>FIRST</w:t>'
        '<w:br xmlns:x="urn:first" w:type="page" mc:Ignorable="x"/>'
        '</w:r></w:p>'
        '<w:p><w:r><w:t>SECOND</w:t>'
        '<w:br xmlns:x="urn:second" w:type="page" mc:Ignorable="x"/>'
        '</w:r></w:p>'
        '</w:body></w:document>'
    ).encode()

    projection = docx_pandoc._project_part(docx_source.DOCUMENT_PART, xml)
    root = ET.fromstring(projection)

    assert [element.text for element in root.iter(f"{docx_source.W}t")] == [
        "FIRST",
        " ",
        "SECOND",
        " ",
    ]
    assert not ooxml.unresolved_prefix_value_references(projection)


def test_alternate_content_rejects_duplicate_fallback_with_context() -> None:
    xml = (
        f'<w:footnotes xmlns:w="{ooxml.W_NS}" '
        f'xmlns:mc="{ooxml.MC_NS}" xmlns:x="urn:unsupported">'
        '<w:footnote w:id="1"><w:p><w:r><mc:AlternateContent>'
        '<mc:Choice Requires="x"><w:t>CHOICE</w:t></mc:Choice>'
        '<mc:Fallback><w:t>FIRST</w:t></mc:Fallback>'
        '<mc:Fallback><w:t>SECOND</w:t></mc:Fallback>'
        '</mc:AlternateContent></w:r></w:p></w:footnote></w:footnotes>'
    ).encode()

    with pytest.raises(
        docx_source.DocxSourceError,
        match=(
            r"word/footnotes\.xml: paragraph 0: "
            r"mc:AlternateContent has multiple fallback branches"
        ),
    ):
        docx_pandoc._project_part("word/footnotes.xml", xml)


def test_only_selected_fallback_pagination_is_neutralized() -> None:
    xml = (
        f'<w:footnotes xmlns:w="{ooxml.W_NS}" '
        f'xmlns:mc="{ooxml.MC_NS}" xmlns:x="urn:unsupported">'
        '<w:footnote w:id="1"><w:p><w:r><mc:AlternateContent>'
        '<mc:Choice Requires="x"><w:t>CHOICE</w:t></mc:Choice>'
        '<mc:Fallback><w:br w:type="page"/></mc:Fallback>'
        '</mc:AlternateContent></w:r></w:p></w:footnote></w:footnotes>'
    ).encode()

    projection = docx_pandoc._project_part("word/footnotes.xml", xml)
    root = ET.fromstring(projection)

    assert root.find(f".//{docx_source.W}br") is None
    assert root.find(f".//{docx_source.W}p") is None
    assert b"CHOICE" not in projection


@pandoc_required
def test_fallback_only_pagination_paragraph_mints_no_ir_block(tmp_path: Path) -> None:
    path = _fallback_pagination_only_docx(tmp_path)
    source = docx_source.read(path)

    assert [paragraph.disposition for paragraph in source.paragraphs] == [
        docx_source.ParagraphDisposition.CONTENT,
        docx_source.ParagraphDisposition.PAGINATION_ONLY,
        docx_source.ParagraphDisposition.CONTENT,
    ]
    projected = docx_pandoc.project_package(source, tmp_path / "projected")
    with zipfile.ZipFile(projected) as archive:
        root = ET.fromstring(archive.read(docx_source.DOCUMENT_PART))
    assert root.find(f".//{ooxml.MC_ALTERNATE_CONTENT}") is None
    assert len(root.findall(f".//{docx_source.W}p")) == 2

    imported = da.adapt(source, tmp_path / "media", [])
    assert [
        inline_plain(block.inlines)
        for block in imported.blocks
        if isinstance(block, ir.Paragraph)
    ] == ["before", "after"]


def _alternate_content_docx(tmp_path: Path) -> Path:
    document = Document()
    run = document.add_paragraph().add_run()
    run._r.append(
        parse_xml(
            f'<mc:AlternateContent xmlns:mc="{ooxml.MC_NS}" '
            f'xmlns:w="{ooxml.W_NS}" '
            'xmlns:wps="http://schemas.microsoft.com/office/word/2010/'
            'wordprocessingShape" xmlns:x="urn:unsupported">'
            '<mc:Choice Requires="wps"><w:t>MODERN</w:t></mc:Choice>'
            '<mc:Choice Requires="x"><w:t>FUTURE</w:t></mc:Choice>'
            '<mc:Fallback><w:t>BASELINE</w:t></mc:Fallback>'
            '</mc:AlternateContent>'
        )
    )
    path = tmp_path / "alternate-content.docx"
    document.save(str(path))
    return path


@pandoc_required
def test_source_and_pandoc_share_the_mc_fallback_profile(tmp_path: Path) -> None:
    path = _alternate_content_docx(tmp_path)
    source = docx_source.read(path)
    imported = da.adapt(source, tmp_path / "media", [])
    paragraphs = [block for block in imported.blocks if isinstance(block, ir.Paragraph)]

    assert [paragraph.text for paragraph in source.paragraphs] == ["BASELINE"]
    assert [inline_plain(block.inlines) for block in paragraphs] == ["BASELINE"]


@pytest.mark.parametrize("part", ["word/footnotes.xml", "word/endnotes.xml"])
def test_note_story_parts_neutralize_pagination_without_modeling_notes(part: str) -> None:
    root_name = "footnotes" if "footnotes" in part else "endnotes"
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:{root_name} xmlns:w="{ooxml.W_NS}">'
        '<w:p><w:r><w:t>before</w:t><w:br w:type="page"/>'
        '<w:t>after</w:t><w:br/></w:r></w:p>'
        f'</w:{root_name}>'
    ).encode()

    projection = docx_pandoc._project_part(part, xml)

    rendered = ET.fromstring(projection)
    breaks = rendered.findall(f".//{docx_source.W}br")
    assert len(breaks) == 1
    assert breaks[0].get(f"{docx_source.W}type") is None


def test_story_projection_reports_unknown_break_with_part_and_paragraph() -> None:
    xml = (
        f'<w:footnotes xmlns:w="{ooxml.W_NS}">'
        '<w:footnote w:id="1"><w:p><w:r>'
        '<w:br w:type="future-layout"/>'
        '</w:r></w:p></w:footnote></w:footnotes>'
    ).encode()

    with pytest.raises(
        docx_source.DocxSourceError,
        match=(
            r"word/footnotes\.xml: paragraph 0: "
            r"unsupported w:br type 'future-layout'"
        ),
    ):
        docx_pandoc._project_part("word/footnotes.xml", xml)


def test_break_validation_applies_only_to_selected_fallback() -> None:
    def story(choice_type: str, fallback_type: str) -> bytes:
        return (
            f'<w:footnotes xmlns:w="{ooxml.W_NS}" '
            f'xmlns:mc="{ooxml.MC_NS}" xmlns:x="urn:unsupported">'
            '<w:footnote w:id="1"><w:p><w:r><mc:AlternateContent>'
            f'<mc:Choice Requires="x"><w:br w:type="{choice_type}"/></mc:Choice>'
            f'<mc:Fallback><w:br w:type="{fallback_type}"/></mc:Fallback>'
            '</mc:AlternateContent></w:r></w:p></w:footnote></w:footnotes>'
        ).encode()

    projection = docx_pandoc._project_part(
        "word/footnotes.xml",
        story("future-layout", "page"),
    )
    assert ET.fromstring(projection).find(f".//{docx_source.W}p") is None

    with pytest.raises(
        docx_source.DocxSourceError,
        match=(
            r"word/footnotes\.xml: paragraph 0: "
            r"unsupported w:br type 'future-layout'"
        ),
    ):
        docx_pandoc._project_part(
            "word/footnotes.xml",
            story("page", "future-layout"),
        )


def test_note_story_parts_remove_only_payload_free_pagination_paragraphs() -> None:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<w:footnotes xmlns:w="{ooxml.W_NS}">'
        '<w:footnote w:id="1"><w:p><w:r><w:br w:type="page"/></w:r></w:p></w:footnote>'
        '<w:footnote w:id="2"><w:p><w:r><w:br w:type="page"/>'
        '<w:footnoteReference w:id="2"/></w:r></w:p></w:footnote>'
        '</w:footnotes>'
    ).encode()

    projection = docx_pandoc._project_part("word/footnotes.xml", xml)

    root = ET.fromstring(projection)
    assert len(root.findall(f".//{docx_source.W}p")) == 1
    assert len(root.findall(f".//{docx_source.W}footnoteReference")) == 1


def test_disabled_page_break_before_remains_a_structural_empty_paragraph() -> None:
    xml = (
        f'<w:footnotes xmlns:w="{ooxml.W_NS}"><w:footnote w:id="1">'
        '<w:p><w:pPr><w:pageBreakBefore w:val="off"/></w:pPr></w:p>'
        '</w:footnote></w:footnotes>'
    ).encode()
    paragraph = ET.fromstring(xml).find(f".//{docx_source.W}p")
    assert paragraph is not None

    semantics = docx_source.analyze_paragraph(paragraph)
    projection = docx_pandoc._project_part("word/footnotes.xml", xml)

    assert not semantics.page_break_before
    assert semantics.disposition is docx_source.ParagraphDisposition.STRUCTURAL_EMPTY
    assert len(ET.fromstring(projection).findall(f".//{docx_source.W}p")) == 1


@pytest.mark.parametrize("path", [_BOOK17, _GENERIC], ids=["book17", "book27"])
def test_projected_parts_close_every_lexical_namespace_reference(
    path: Path,
    tmp_path: Path,
) -> None:
    projected = docx_pandoc.project_package(docx_source.read(path), tmp_path)
    with zipfile.ZipFile(projected) as archive:
        for part in docx_pandoc._PANDOC_STORY_PARTS:
            if part in archive.namelist():
                assert ooxml.unresolved_prefix_value_references(archive.read(part)) == set()


@pytest.mark.parametrize(
    "path",
    _GENERIC_NAMESPACE_DOCX,
    ids=lambda path: path.parent.name.split("-", 1)[0],
)
def test_namespace_projection_preserves_lexical_meaning_across_generic_books(
    path: Path,
) -> None:
    """All known generic-prefix sources retain scoped MC namespace meaning."""
    with zipfile.ZipFile(path) as archive:
        source_xml = archive.read(docx_source.DOCUMENT_PART)
    source = ooxml.parse_xml(source_xml)
    projected = ooxml.serialize_xml(
        source,
        bindings=docx_pandoc._PANDOC_NAMESPACE_BINDINGS,
    )

    assert ET.tostring(ET.fromstring(projected)) == ET.tostring(source.root)
    common = {binding.prefix: binding.uri for binding in ooxml.COMMON_NAMESPACES}
    expected = tuple(
        ooxml.PrefixValueReference(
            reference.prefix,
            reference.uri or common[reference.prefix],
        )
        for reference in ooxml.prefix_value_references_in_xml(source_xml)
    )
    assert ooxml.prefix_value_references_in_xml(projected) == expected


def _zip_fingerprint(info: zipfile.ZipInfo) -> tuple[object, ...]:
    return (
        info.filename,
        info.date_time,
        info.compress_type,
        info.comment,
        info.extra,
        info.internal_attr,
        info.external_attr,
        info.create_system,
        info.flag_bits,
        info.create_version,
        info.extract_version,
        info.volume,
        info.reserved,
    )


def test_projected_package_preserves_entry_order_metadata_and_duplicates(
    tmp_path: Path,
) -> None:
    path = _break_docx(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "a") as archive:
            archive.comment = b"preserve package comment"
            archive.writestr("custom/duplicate.bin", b"first")
            archive.writestr("custom/duplicate.bin", b"second")
    source = docx_source.read(path)

    with zipfile.ZipFile(path) as archive:
        original_comment = archive.comment
        original_payloads = [archive.read(info) for info in archive.infolist()]
        before = [_zip_fingerprint(info) for info in archive.infolist()]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        projected = docx_pandoc.project_package(source, tmp_path / "projected")
    with zipfile.ZipFile(projected) as archive:
        assert archive.comment == original_comment
        after = [_zip_fingerprint(info) for info in archive.infolist()]
        duplicate_payloads = [
            archive.read(info)
            for info in archive.infolist()
            if info.filename == "custom/duplicate.bin"
        ]
        projected_payloads = [archive.read(info) for info in archive.infolist()]
    assert after == before
    assert duplicate_payloads == [b"first", b"second"]
    for index, (original, projected) in enumerate(
        zip(original_payloads, projected_payloads, strict=True)
    ):
        if before[index][0] != docx_source.DOCUMENT_PART:
            assert projected == original


def test_projection_rejects_duplicate_document_story_part(tmp_path: Path) -> None:
    path = _break_docx(tmp_path)
    with zipfile.ZipFile(path) as archive:
        duplicate = archive.read(docx_source.DOCUMENT_PART)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr(docx_source.DOCUMENT_PART, duplicate)
    source = docx_source.read(path)

    with pytest.raises(
        RuntimeError,
        match=r"duplicate Word story part.*word/document\.xml",
    ):
        docx_pandoc.project_package(source, tmp_path / "projected")


def test_projection_fails_closed_when_an_optional_story_part_is_malformed(
    tmp_path: Path,
) -> None:
    path = _break_docx(tmp_path)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("word/footnotes.xml", b"<w:footnotes")
    source = docx_source.read(path)

    with pytest.raises(RuntimeError, match=r"word/footnotes\.xml"):
        docx_pandoc.project_package(source, tmp_path / "projected")


@pandoc_required
def test_pandoc_projection_keeps_line_breaks_and_demotes_pagination(
    tmp_path: Path,
) -> None:
    path = _break_docx(tmp_path)
    document = da.adapt(docx_source.read(path), tmp_path / "media", [])
    paragraph = next(block for block in document.blocks if isinstance(block, ir.Paragraph))

    assert inline_plain(paragraph.inlines) == "before middle line after"
    assert sum(isinstance(inline, ir.LineBreak) for inline in paragraph.inlines) == 1


@pandoc_required
def test_book17_former_sidecar_is_output_equivalent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from pancratius import docx_conversion

    def convert(media: str) -> docx_conversion.ConvertedDocx:
        return docx_conversion.convert_single_docx(
            _BOOK17,
            kind="book",
            lang="ru",
            work_key="book:17",
            title="Оболганные Иисус и Творец",
            title_index={},
            media_out=tmp_path / media,
        )

    monkeypatch.setattr(
        docx_conversion.lineation_overrides,
        "load_overrides",
        lambda _source: {140: "prose", 141: "prose"},
    )
    pinned = convert("pinned")
    monkeypatch.setattr(
        docx_conversion.lineation_overrides,
        "load_overrides",
        lambda _source: {},
    )
    unpinned = convert("unpinned")

    assert (unpinned.body, unpinned.bibliography, unpinned.cross_refs) == (
        pinned.body,
        pinned.bibliography,
        pinned.cross_refs,
    )
