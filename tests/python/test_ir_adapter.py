"""Unit tests for the DOCX→IR adapter's pure mapping logic (`docx_adapter`).

These exercise the adapter's Pandoc-AST → typed-IR mapping on hand-built AST
fixtures and the OOXML `w:jc` side-channel read on a synthetic in-memory DOCX —
no real DOCX and (almost) no pandoc, so they run everywhere. These lock the
per-node contracts the spec calls out: Note → dense-renumbered footnote ref +
def, ``w:jc`` → paragraph
``align``, Image → ``ImageInline`` assetref source, plus the inline/block kind
mapping (emphasis, quoted, underline/smallcaps unwrap, Div/Figure containers,
ordered-list start, table structuring).
"""

from __future__ import annotations

import subprocess
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pytest

from pancratius import docx_adapter as adapter
from pancratius import docx_pandoc, docx_source, ir
from pancratius.ooxml import W_NS


def _read_source(path: Path) -> tuple[docx_source.SourceParagraph, ...]:
    return docx_source.read(path).reconciliation_paragraphs


def _source_paragraph(
    text: str,
    *,
    ordinal: int = 0,
    align: str = "",
    segment: int = 0,
    has_opaque_payload: bool = False,
) -> docx_source.SourceParagraph:
    """Small valid source aggregate member for reconciliation-unit tests."""
    return docx_source.SourceParagraph(
        ordinal=docx_source.ParagraphOrdinal(ordinal),
        reconciliation_position=docx_source.ReconciliationPosition(ordinal),
        semantics=docx_source.ParagraphSemantics(
            content=docx_source.ParagraphContent(
                (docx_source.TextAtom(text),) if text else ()
            ),
            page_break_before=False,
            payload=docx_source.ParagraphPayload(
                frozenset({docx_source.ParagraphPayloadKind.OPAQUE})
                if has_opaque_payload
                else frozenset()
            ),
        ),
        resolved_style="",
        direct_style="",
        layout=docx_source.ParagraphLayout(
            source_alignment=docx_source.ParagraphAlignment(align),
        ),
        contextual_spacing=False,
        indent_departure=False,
        border=docx_source.BorderGesture.NONE,
        markers=docx_source.ParagraphMarkers(),
        segment=docx_source.SourceSegment(segment),
        bold=False,
        italic=False,
    )


# ---------------------------------------------------------------------------
# tiny AST builders (Pandoc JSON node shapes)
# ---------------------------------------------------------------------------


def _str(s: str) -> dict[str, object]:
    return {"t": "Str", "c": s}


def _para(*inlines: dict[str, object]) -> dict[str, object]:
    return {"t": "Para", "c": list(inlines)}


# ---------------------------------------------------------------------------
# Note → footnote ref + dense renumbered def
# ---------------------------------------------------------------------------


def test_note_becomes_dense_renumbered_ref_and_def() -> None:
    ctx = adapter._Ctx()
    # Two notes in reference order get ids 1, 2 regardless of any source w:id.
    first = adapter._inline({"t": "Note", "c": [_para(_str("first body"))]}, ctx)
    second = adapter._inline({"t": "Note", "c": [_para(_str("second body"))]}, ctx)
    assert first == [ir.FootnoteRef(raw_index=1, id=1)]
    assert second == [ir.FootnoteRef(raw_index=2, id=2)]
    assert [idx for idx, _blocks in ctx.fn_defs] == [1, 2]
    body1 = ctx.fn_defs[0][1][0]
    assert isinstance(body1, ir.Paragraph)
    assert body1.inlines == [ir.Text("first body")]


def test_note_with_multi_paragraph_body_keeps_all_blocks() -> None:
    ctx = adapter._Ctx()
    adapter._inline({"t": "Note", "c": [_para(_str("p1")), _para(_str("p2"))]}, ctx)
    _idx, blocks = ctx.fn_defs[0]
    assert len(blocks) == 2  # both paragraphs of the def survive structurally


# ---------------------------------------------------------------------------
# inline kind mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tag", "kind"),
    [
        ("Strong", "strong"),
        ("Emph", "emph"),
        ("Strikeout", "strike"),
        ("Superscript", "sup"),
        ("Subscript", "sub"),
    ],
)
def test_emphasis_kind_mapped(tag: str, kind: ir.EmphKind) -> None:
    ctx = adapter._Ctx()
    out = adapter._inline({"t": tag, "c": [_str("x")]}, ctx)
    assert out == [ir.Emphasis(kind, [ir.Text("x")])]


@pytest.mark.parametrize(("tag", "text"), [("Underline", "u"), ("SmallCaps", "s")])
def test_style_wrapper_unwraps_to_plain_text(tag: str, text: str) -> None:
    ctx = adapter._Ctx()
    assert adapter._inline({"t": tag, "c": [_str(text)]}, ctx) == [ir.Text(text)]


@pytest.mark.parametrize(
    ("quote_type", "text", "kind"),
    [
        ("DoubleQuote", "d", "double"),
        ("SingleQuote", "s", "single"),
    ],
)
def test_quoted_carries_quote_kind(quote_type: str, text: str, kind: ir.QuoteKind) -> None:
    ctx = adapter._Ctx()
    out = adapter._inline({"t": "Quoted", "c": [{"t": quote_type}, [_str(text)]]}, ctx)
    assert out == [ir.Quoted(kind, [ir.Text(text)])]


def test_span_unwraps_to_children() -> None:
    ctx = adapter._Ctx()
    out = adapter._inline({"t": "Span", "c": [["", [], []], [_str("inner")]]}, ctx)
    assert out == [ir.Text("inner")]


def test_span_with_dir_rtl_becomes_directional_span() -> None:
    # A bidi Span carrying `dir=rtl` (Hebrew/Arabic) is modelled, not flattened —
    # the direction governs visual ordering (I2). Other Span attrs still unwrap.
    ctx = adapter._Ctx()
    node = {"t": "Span", "c": [["", [], [["dir", "rtl"]]], [_str("פקד")]]}
    out = adapter._inline(node, ctx)
    assert out == [ir.DirectionalSpan(direction="rtl", children=[ir.Text("פקד")])]


def test_span_with_non_dir_attr_still_unwraps() -> None:
    ctx = adapter._Ctx()
    node = {"t": "Span", "c": [["", ["foo"], [["data-x", "1"]]], [_str("kept")]]}
    assert adapter._inline(node, ctx) == [ir.Text("kept")]


def test_image_becomes_imageinline_with_src_and_alt() -> None:
    ctx = adapter._Ctx()
    node = {"t": "Image", "c": [["", [], []], [_str("Caption")], ["media/x.png", ""]]}
    assert adapter._inline(node, ctx) == [ir.ImageInline(src="media/x.png", alt="Caption")]


def test_unknown_inline_preserves_children() -> None:
    ctx = adapter._Ctx()
    out = adapter._inline({"t": "Bogus", "c": [_str("kept")]}, ctx)
    assert out == [ir.UnknownInline(note="Bogus", children=[ir.Text("kept")])]


# ---------------------------------------------------------------------------
# block kind mapping
# ---------------------------------------------------------------------------


def test_empty_para_is_marked_empty() -> None:
    ctx = adapter._Ctx()
    b = adapter._block({"t": "Para", "c": []}, ctx)
    assert isinstance(b, ir.Paragraph) and b.empty


def test_header_carries_level() -> None:
    ctx = adapter._Ctx()
    b = adapter._block({"t": "Header", "c": [2, ["", [], []], [_str("T")]]}, ctx)
    assert isinstance(b, ir.Heading) and b.level == 2 and b.inlines == [ir.Text("T")]


def test_ordered_list_preserves_start_ordinal() -> None:
    ctx = adapter._Ctx()
    node = {"t": "OrderedList", "c": [[4, {"t": "Decimal"}, {"t": "Period"}],
                                      [[_para(_str("four"))], [_para(_str("five"))]]]}
    b = adapter._block(node, ctx)
    assert isinstance(b, ir.ListBlock) and b.ordered and b.start == 4 and len(b.items) == 2


def test_div_children_are_spliced_in_place() -> None:
    ctx = adapter._Ctx()
    blocks = adapter._blocks([
        _para(_str("before")),
        {"t": "Div", "c": [["", [], []], [_para(_str("inside"))]]},
        _para(_str("after")),
    ], ctx)
    assert [type(b).__name__ for b in blocks] == ["Paragraph"] * 3


def test_figure_splices_content_then_caption() -> None:
    ctx = adapter._Ctx()
    img: dict[str, object] = {"t": "Image", "c": [["", [], []], [], ["m/p.png", ""]]}
    figure = {"t": "Figure", "c": [["", [], []],
                                   [None, [_para(_str("the caption"))]],
                                   [_para(img)]]}
    blocks = adapter._blocks([figure], ctx)
    # neither the image nor the caption text is lost; no container survives
    assert [type(b).__name__ for b in blocks] == ["Paragraph", "Paragraph"]


def test_line_block_maps_to_lineated_lines_not_unknown() -> None:
    # Bug 4(a): a Pandoc LineBlock is structurally lineated reading content. It
    # must map to REAL content (not an UnknownBlock lowering would drop), but the
    # adapter must not assign verse register by itself.
    ctx = adapter._Ctx()
    node = {"t": "LineBlock", "c": [
        [_str("Roses are red,")],
        [_str("violets are blue.")],
    ]}
    b = adapter._block(node, ctx)
    assert isinstance(b, ir.LineatedBlock), f"LineBlock should map to LineatedBlock, got {type(b).__name__}"
    lines = [line for stanza in b.stanzas for line in stanza]
    assert len(lines) == 2
    assert lines[0] == ir.Line([ir.Text("Roses are red,")])
    assert lines[1] == ir.Line([ir.Text("violets are blue.")])


def test_unknown_block_preserves_plain_text_content() -> None:
    # Bug 4(b): a genuinely-unknown block must PRESERVE its readable text content so
    # lowering can emit it (not silently drop it). The adapter records the block's
    # best-effort plain text on the UnknownBlock.
    ctx = adapter._Ctx()
    node = {"t": "Bogus", "c": [_para(_str("important reading content"))]}
    b = adapter._block(node, ctx)
    assert isinstance(b, ir.UnknownBlock)
    assert b.note == "Bogus"
    assert "important reading content" in b.text


def test_para_all_italic_flag_set_for_epigraph_signal() -> None:
    ctx = adapter._Ctx()
    italic = adapter._block(_para({"t": "Emph", "c": [_str("all italic")]}), ctx)
    plain = adapter._block(_para(_str("not italic")), ctx)
    assert isinstance(italic, ir.Paragraph) and italic.italic
    assert isinstance(plain, ir.Paragraph) and not plain.italic


# ---------------------------------------------------------------------------
# table structuring (rows of cells of inlines + raw kept)
# ---------------------------------------------------------------------------


def _cell(*inlines: dict[str, object]) -> list[object]:
    # Pandoc Cell = [attr, alignment, rowspan, colspan, blocks]
    return [["", [], []], {"t": "AlignDefault"}, 1, 1, [_para(*inlines)]]


def _row(*cells: list[object]) -> list[object]:
    return [["", [], []], list(cells)]


def test_table_structures_rows_and_keeps_raw() -> None:
    ctx = adapter._Ctx()
    body_rows = [_row(_cell(_str("a")), _cell(_str("b")))]
    node = {"t": "Table", "c": [
        ["", [], []],            # attr
        [None, []],              # caption
        [],                      # colspecs
        [["", [], []], []],      # thead (no header rows)
        [[["", [], []], 0, [], body_rows]],  # tbodies = [[attr, rhc, headerrows, bodyrows]]
        [["", [], []], []],      # tfoot
    ]}
    t = adapter._table(node, ctx)
    assert isinstance(t, ir.Table)
    assert t.raw is node  # raw node retained for the bibliography classifier
    assert [[ir.Text("a")], [ir.Text("b")]] == t.rows[0]


def test_table_unknown_shape_keeps_raw_with_empty_rows() -> None:
    ctx = adapter._Ctx()
    node = {"t": "Table", "c": "unexpected"}
    t = adapter._table(node, ctx)
    assert isinstance(t, ir.Table) and t.rows == [] and t.raw is node


# ---------------------------------------------------------------------------
# OOXML w:jc side-channel + alignment zip
# ---------------------------------------------------------------------------


def _docx_from_document(tmp_path: Path, document: str, *, styles: str | None = None) -> Path:
    """Wrap a `word/document.xml` string into a minimal .docx file."""
    path = tmp_path / "fixture.docx"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", document)
        if styles is not None:
            zf.writestr("word/styles.xml", styles)
    return path


def _docx_with_paragraphs(tmp_path: Path, *jcs: str | None) -> Path:
    """Build a minimal .docx whose body has one `w:p` per `jcs` entry."""
    paras = []
    for jc in jcs:
        ppr = f'<w:pPr><w:jc w:val="{jc}"/></w:pPr>' if jc is not None else ""
        paras.append(f"<w:p>{ppr}<w:r><w:t>x</w:t></w:r></w:p>")
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        + "".join(paras)
        + "</w:body></w:document>"
    )
    return _docx_from_document(tmp_path, document)


def _aligns(records: Sequence[docx_source.SourceParagraph]) -> list[str]:
    return [record.alignment.value for record in records]


def _groups(records: Sequence[docx_source.SourceParagraph]) -> list[int | None]:
    return [record.visual_group.value if record.visual_group is not None else None for record in records]


def test_read_w_jc_returns_alignment_per_body_paragraph(tmp_path: Path) -> None:
    path = _docx_with_paragraphs(tmp_path, "right", None, "center")
    records = _read_source(path)
    assert _aligns(records) == ["right", "", "center"]
    assert [int(record.ordinal) for record in records] == [0, 1, 2]


def test_read_w_jc_skips_table_paragraphs(tmp_path: Path) -> None:
    # A w:tbl in the body must NOT contribute alignment entries (its cell paras are
    # not top-level AST paragraphs), so the records stay lined up with the AST.
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        '<w:p><w:pPr><w:jc w:val="right"/></w:pPr><w:r><w:t>a</w:t></w:r></w:p>'
        '<w:tbl><w:tr><w:tc><w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
        '<w:r><w:t>cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        '<w:p><w:r><w:t>b</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    path = _docx_from_document(tmp_path, document)
    assert _aligns(_read_source(path)) == ["right", ""]  # the table para is skipped


def test_read_w_jc_skips_list_item_paragraphs(tmp_path: Path) -> None:
    # A list-item w:p (carrying w:numPr) is collapsed by Pandoc into a single List
    # block, so it never surfaces as a top-level Para. It must NOT contribute an
    # alignment record, or the vector lags by one per list item (the dominant C1
    # drift source). The text-bearing records around it are kept.
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        '<w:p><w:r><w:t>before</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
        '<w:r><w:t>item one</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
        '<w:r><w:t>item two</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:jc w:val="right"/></w:pPr><w:r><w:t>after</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    path = _docx_from_document(tmp_path, document)
    records = _read_source(path)
    assert _aligns(records) == ["", "right"]  # the two list items are skipped
    assert [r.text for r in records] == ["before", "after"]


def test_read_w_jc_marks_contextual_spacing_visual_group(tmp_path: Path) -> None:
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        '<w:p><w:pPr><w:contextualSpacing/><w:spacing w:after="100"/></w:pPr>'
        '<w:r><w:t>first line</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:contextualSpacing/><w:spacing w:before="100"/></w:pPr>'
        '<w:r><w:t>second line</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:contextualSpacing/><w:spacing w:before="100"/></w:pPr>'
        '<w:r><w:t>third line</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    path = _docx_from_document(tmp_path, document)
    records = _read_source(path)
    assert [r.text for r in records] == ["first line", "second line", "third line"]
    assert _groups(records) == [1, 1, 1]


def test_read_w_jc_uses_doc_default_spacing_for_visual_group(tmp_path: Path) -> None:
    styles = (
        '<?xml version="1.0"?>'
        f'<w:styles xmlns:w="{W_NS}">'
        '<w:docDefaults><w:pPrDefault><w:pPr><w:spacing w:after="100"/>'
        "</w:pPr></w:pPrDefault></w:docDefaults>"
        "</w:styles>"
    )
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        '<w:p><w:pPr><w:contextualSpacing/></w:pPr>'
        '<w:r><w:t>first line</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:contextualSpacing/></w:pPr>'
        '<w:r><w:t>second line</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    path = _docx_from_document(tmp_path, document, styles=styles)
    records = _read_source(path)
    assert [r.text for r in records] == ["first line", "second line"]
    assert _groups(records) == [1, 1]


def test_read_w_jc_style_spacing_overrides_doc_default_spacing(tmp_path: Path) -> None:
    styles = (
        '<?xml version="1.0"?>'
        f'<w:styles xmlns:w="{W_NS}">'
        '<w:docDefaults><w:pPrDefault><w:pPr><w:spacing w:after="100"/>'
        "</w:pPr></w:pPrDefault></w:docDefaults>"
        '<w:style w:type="paragraph" w:styleId="NoGap">'
        '<w:pPr><w:spacing w:after="0"/></w:pPr>'
        "</w:style>"
        "</w:styles>"
    )
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        '<w:p><w:pPr><w:pStyle w:val="NoGap"/><w:contextualSpacing/></w:pPr>'
        '<w:r><w:t>first paragraph</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="NoGap"/><w:contextualSpacing/></w:pPr>'
        '<w:r><w:t>second paragraph</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    path = _docx_from_document(tmp_path, document, styles=styles)
    records = _read_source(path)
    assert [r.text for r in records] == ["first paragraph", "second paragraph"]
    assert _groups(records) == [None, None]


def test_read_resolves_inherited_alignment_and_indent_once(tmp_path: Path) -> None:
    styles = (
        '<?xml version="1.0"?>'
        f'<w:styles xmlns:w="{W_NS}">'
        '<w:docDefaults><w:pPrDefault><w:pPr><w:jc w:val="center"/>'
        '<w:ind w:left="120"/></w:pPr></w:pPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:styleId="Base">'
        '<w:pPr><w:jc w:val="both"/><w:ind w:left="240" w:firstLine="80"/></w:pPr>'
        '</w:style>'
        '<w:style w:type="paragraph" w:styleId="Derived">'
        '<w:basedOn w:val="Base"/><w:pPr><w:ind w:left="480"/></w:pPr>'
        '</w:style>'
        '</w:styles>'
    )
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        '<w:p><w:pPr><w:pStyle w:val="Derived"/></w:pPr>'
        '<w:r><w:t>styled</w:t></w:r></w:p>'
        '</w:body></w:document>'
    )
    paragraph = _read_source(_docx_from_document(tmp_path, document, styles=styles))[0]

    assert paragraph.alignment.value == "both"
    assert paragraph.layout.alignment is docx_source.TextAlignment.JUST
    assert dict(paragraph.indent) == {"firstLine": "80", "left": "480"}
    assert paragraph.layout.first_line_indent == docx_source.Twips(80)
    assert paragraph.layout.left_indent == docx_source.Twips(480)


def test_read_w_jc_marks_structural_empty_paragraphs(tmp_path: Path) -> None:
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        '<w:p><w:r><w:t>before</w:t></w:r></w:p>'
        "<w:p/>"
        '<w:p><w:r><w:t>after</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    path = _docx_from_document(tmp_path, document)
    records = _read_source(path)
    assert [r.text for r in records] == ["before", "", "after"]
    assert [r.empty for r in records] == [False, True, False]
    assert [int(record.ordinal) for record in records] == [0, 1, 2]


def test_read_w_jc_visual_group_does_not_bridge_list_item(tmp_path: Path) -> None:
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        '<w:p><w:pPr><w:contextualSpacing/><w:spacing w:after="100"/></w:pPr>'
        '<w:r><w:t>before list</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>'
        '<w:contextualSpacing/><w:spacing w:before="100" w:after="100"/></w:pPr>'
        '<w:r><w:t>list item</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:contextualSpacing/><w:spacing w:before="100"/></w:pPr>'
        '<w:r><w:t>after list</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    path = _docx_from_document(tmp_path, document)
    records = _read_source(path)
    assert [r.text for r in records] == ["before list", "after list"]
    assert _groups(records) == [None, None]


def test_paragraph_text_selects_mc_fallback_without_concatenating_choices(
    tmp_path: Path,
) -> None:
    # AlternateContent branches are mutually exclusive. Pancratius has the same
    # baseline capability profile as Pandoc: no extension Choice is claimed, so
    # only Fallback contributes source atoms.
    mc = "http://schemas.openxmlformats.org/markup-compatibility/2006"
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{W_NS}" xmlns:mc="{mc}" '
        'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
        'xmlns:x="urn:unsupported"><w:body>'
        "<w:p><w:r>"
        "<mc:AlternateContent>"
        '<mc:Choice Requires="wps"><w:t>MODERN</w:t></mc:Choice>'
        '<mc:Choice Requires="x"><w:t>FUTURE</w:t></mc:Choice>'
        "<mc:Fallback><w:t>BASELINE</w:t></mc:Fallback>"
        "</mc:AlternateContent>"
        "</w:r></w:p>"
        "</w:body></w:document>"
    )
    path = _docx_from_document(tmp_path, document)
    records = _read_source(path)
    assert [r.text for r in records] == ["BASELINE"]


def test_paragraph_text_keeps_no_break_hyphen(tmp_path: Path) -> None:
    # Word stores a non-breaking hyphen as a textless `w:noBreakHyphen` between two
    # `w:t` runs; Pandoc renders it U+2011. Dropping it here fuses `кто‑то`→`ктото`,
    # so the match fingerprint diverges from the AST, the paragraph never matches its
    # source `w:p`, and its span stays None → the line is UNMAPPED, hence non-votable.
    # (Fingerprint-desync, distinct from the §14-P1 verse-merge MIXED case.) Survive it.
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        "<w:p><w:r><w:t>кто</w:t><w:noBreakHyphen/><w:t>то</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    path = _docx_from_document(tmp_path, document)
    records = _read_source(path)
    assert [r.text for r in records] == ["кто‑то"]  # not "ктото"


def test_paragraph_text_keeps_soft_hyphen(tmp_path: Path) -> None:
    # The same desync via Word's optional hyphen: a textless `w:softHyphen` renders
    # U+00AD in Pandoc. Dropping it fuses the flanking words just like the non-breaking
    # hyphen, so the record must key on the same glyph.
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        "<w:p><w:r><w:t>кто</w:t><w:softHyphen/><w:t>то</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    path = _docx_from_document(tmp_path, document)
    records = _read_source(path)
    assert [r.text for r in records] == ["кто­то"]  # soft hyphen kept, not "ктото"


def _bordered_para(text: str, *sides: str, val: str = "single") -> str:
    edges = "".join(f'<w:{side} w:val="{val}" w:sz="4"/>' for side in sides)
    pbdr = f"<w:pPr><w:pBdr>{edges}</w:pBdr></w:pPr>" if sides else ""
    return f"<w:p>{pbdr}<w:r><w:t>{text}</w:t></w:r></w:p>"


def test_read_w_jc_classifies_border_kind(tmp_path: Path) -> None:
    # The two editorially meaningful w:pBdr gestures: a full four-side box
    # (framed/quoted canonical text) and a left-rule bar (set-apart inset).
    # Other side combinations are "other"; val="none" sides do not count.
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        + _bordered_para("boxed", "top", "bottom", "left", "right")
        + _bordered_para("ruled", "left")
        + _bordered_para("topped", "top")
        + _bordered_para("noned", "top", "bottom", "left", "right", val="none")
        + _bordered_para("plain")
        + "</w:body></w:document>"
    )
    records = _read_source(_docx_from_document(tmp_path, document))
    assert [r.border for r in records] == ["box", "rule", "other", "", ""]


def test_read_w_jc_classifies_divider_markers_as_thematic(tmp_path: Path) -> None:
    paras = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in ["---", "===", "***"])
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        + paras
        + "</w:body></w:document>"
    )

    records = _read_source(_docx_from_document(tmp_path, document))

    assert [r.thematic for r in records] == [True, True, True]


def test_facts_fused_bordered_and_plain_stays_unbordered(tmp_path: Path) -> None:
    # A paragraph whose span consumed a bordered AND a plain source row must NOT
    # inherit the border — that would drag plain text into a set-apart register.
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        + _bordered_para("framed words", "left")
        + "<w:p><w:r><w:t>plain words</w:t></w:r></w:p>"
        + "</w:body></w:document>"
    )
    records = _read_source(_docx_from_document(tmp_path, document))
    blocks: list[ir.Block] = [
        ir.Paragraph(
            inlines=[ir.Text("framed words plain words")],
            source_span=ir.SourceSpan(0, 1),
        )
    ]
    adapter.apply_source_facts(blocks, records)
    reconciled = blocks[0]
    assert isinstance(reconciled, ir.Paragraph) and reconciled.border == ""


def test_facts_assign_border_kind(tmp_path: Path) -> None:
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        + _bordered_para("set-apart inset passage", "left")
        + "</w:body></w:document>"
    )
    records = _read_source(_docx_from_document(tmp_path, document))
    blocks: list[ir.Block] = [
        ir.Paragraph(
            inlines=[ir.Text("set-apart inset passage")],
            source_span=ir.SourceSpan(0, 0),
        )
    ]
    adapter.apply_source_facts(blocks, records)
    reconciled = blocks[0]
    assert isinstance(reconciled, ir.Paragraph) and reconciled.border == "rule"


def test_facts_fused_right_paragraphs_mark_alignment() -> None:
    """Several short right-aligned w:p fused into one multi-line Para (the epigraph
    shape) join by their span range and mark that one paragraph align='right'."""
    blocks: list[ir.Block] = [
        ir.Paragraph(
            inlines=[ir.Text("Тогда волк"), ir.LineBreak(), ir.Text("будет жить")],
            source_span=ir.SourceSpan(5, 6),
        )
    ]
    records = [
        _source_paragraph("Тогда волк", ordinal=5, align="right"),
        _source_paragraph("будет жить", ordinal=6, align="right"),
    ]
    _facts, right = adapter.apply_source_facts(blocks, records)
    reconciled = blocks[0]
    assert isinstance(reconciled, ir.Paragraph)
    assert reconciled.align == "right" and right == 1


def test_facts_leave_spanless_paragraphs_untouched() -> None:
    """No provenance, no facts: a paragraph without a span is never guessed at."""
    para = ir.Paragraph(inlines=[ir.Text("completely different prose")])
    _facts, right = adapter.apply_source_facts([para], [
        _source_paragraph("unrelated source words", align="right"),
    ])
    assert right == 0 and para.align == "" and para.source_span is None


def test_direction_indents_book_default_indent_is_not_indented(tmp_path: Path) -> None:
    """`indented` is within-book directioned: when (almost) every body paragraph
    carries the same first-line indent, that indent is the book default and
    discriminates nothing; only a DEPARTING indent marks a paragraph."""
    ind = '<w:pPr><w:ind w:firstLine="708"/></w:pPr>'
    other = '<w:pPr><w:ind w:left="720"/></w:pPr>'
    paras = [f"<w:p>{ind}<w:r><w:t>body {i}</w:t></w:r></w:p>" for i in range(3)]
    paras.append(f"<w:p>{other}<w:r><w:t>block quote</w:t></w:r></w:p>")
    paras.append("<w:p><w:r><w:t>plain</w:t></w:r></w:p>")
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{W_NS}"><w:body>'
        + "".join(paras)
        + "</w:body></w:document>"
    )
    records = _read_source(_docx_from_document(tmp_path, document))
    assert [record.indent_departure for record in records] == [False, False, False, True, False]


# ---------------------------------------------------------------------------
# Fix F: pandoc subprocess runs with a timeout; a hang raises a clear error
# ---------------------------------------------------------------------------


def test_run_pandoc_passes_a_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # A pandoc invocation with no timeout can hang the import forever on a
    # pathological/large input. The subprocess.run call must carry a generous
    # `timeout=` kwarg.
    captured: dict[str, object] = {}

    class _FakeProc:
        returncode = 0
        stdout = '{"blocks":[],"pandoc-api-version":[1,23],"meta":{}}'
        stderr = ""

    def fake_run(cmd: list[str], **kwargs: object) -> _FakeProc:
        assert Path(cmd[0]).name == "pandoc"
        captured.update(kwargs)
        return _FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        docx_pandoc, "project_package", lambda source, _media: source.path
    )
    source = docx_source.DocxSourceDocument(tmp_path / "x.docx", ())
    docx_pandoc.run_json(source, tmp_path / "media")
    assert "timeout" in captured, "pandoc subprocess.run must pass a timeout"
    assert isinstance(captured["timeout"], (int, float)) and captured["timeout"] > 0


def test_run_pandoc_timeout_raises_clear_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # On a TimeoutExpired the adapter must raise a clear, actionable error (not let
    # the raw subprocess exception bubble unexplained).
    def fake_run(cmd: list[str], **kwargs: object) -> object:
        timeout = kwargs.get("timeout", 0)
        raise subprocess.TimeoutExpired(cmd, float(timeout) if isinstance(timeout, (int, float)) else 0.0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(
        docx_pandoc, "project_package", lambda source, _media: source.path
    )
    with pytest.raises(RuntimeError, match=r"(?i)timed out|timeout"):
        source = docx_source.DocxSourceDocument(tmp_path / "x.docx", ())
        docx_pandoc.run_json(source, tmp_path / "media")
