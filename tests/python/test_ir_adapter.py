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
from pathlib import Path

import pytest

from pancratius import docx_adapter as adapter
from pancratius import ir

W = adapter.W


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
    assert lines[0] == [ir.Text("Roses are red,")]
    assert lines[1] == [ir.Text("violets are blue.")]


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
        f'<w:document xmlns:w="{adapter.W_NS}"><w:body>'
        + "".join(paras)
        + "</w:body></w:document>"
    )
    return _docx_from_document(tmp_path, document)


def _aligns(records: list[adapter._SourceParagraph]) -> list[str]:
    return [r.align for r in records]


def _groups(records: list[adapter._SourceParagraph]) -> list[int | None]:
    return [r.lineation_group for r in records]


def test_read_w_jc_returns_alignment_per_body_paragraph(tmp_path: Path) -> None:
    path = _docx_with_paragraphs(tmp_path, "right", None, "center")
    records = adapter.read_w_jc(path)
    assert _aligns(records) == ["right", "", "center"]
    assert [record.source_span for record in records] == [
        ir.SourceSpan(0, 0),
        ir.SourceSpan(1, 1),
        ir.SourceSpan(2, 2),
    ]


def test_read_w_jc_skips_table_paragraphs(tmp_path: Path) -> None:
    # A w:tbl in the body must NOT contribute alignment entries (its cell paras are
    # not top-level AST paragraphs), so the records stay lined up with the AST.
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{adapter.W_NS}"><w:body>'
        '<w:p><w:pPr><w:jc w:val="right"/></w:pPr><w:r><w:t>a</w:t></w:r></w:p>'
        '<w:tbl><w:tr><w:tc><w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
        '<w:r><w:t>cell</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        '<w:p><w:r><w:t>b</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    path = _docx_from_document(tmp_path, document)
    assert _aligns(adapter.read_w_jc(path)) == ["right", ""]  # the table para is skipped


def test_read_w_jc_skips_list_item_paragraphs(tmp_path: Path) -> None:
    # A list-item w:p (carrying w:numPr) is collapsed by Pandoc into a single List
    # block, so it never surfaces as a top-level Para. It must NOT contribute an
    # alignment record, or the vector lags by one per list item (the dominant C1
    # drift source). The text-bearing records around it are kept.
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{adapter.W_NS}"><w:body>'
        '<w:p><w:r><w:t>before</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
        '<w:r><w:t>item one</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
        '<w:r><w:t>item two</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:jc w:val="right"/></w:pPr><w:r><w:t>after</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    path = _docx_from_document(tmp_path, document)
    records = adapter.read_w_jc(path)
    assert _aligns(records) == ["", "right"]  # the two list items are skipped
    assert [r.text for r in records] == ["before", "after"]


def test_read_w_jc_marks_contextual_spacing_visual_group(tmp_path: Path) -> None:
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{adapter.W_NS}"><w:body>'
        '<w:p><w:pPr><w:contextualSpacing/><w:spacing w:after="100"/></w:pPr>'
        '<w:r><w:t>first line</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:contextualSpacing/><w:spacing w:before="100"/></w:pPr>'
        '<w:r><w:t>second line</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:contextualSpacing/><w:spacing w:before="100"/></w:pPr>'
        '<w:r><w:t>third line</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    path = _docx_from_document(tmp_path, document)
    records = adapter.read_w_jc(path)
    assert [r.text for r in records] == ["first line", "second line", "third line"]
    assert _groups(records) == [1, 1, 1]


def test_read_w_jc_uses_doc_default_spacing_for_visual_group(tmp_path: Path) -> None:
    styles = (
        '<?xml version="1.0"?>'
        f'<w:styles xmlns:w="{adapter.W_NS}">'
        '<w:docDefaults><w:pPrDefault><w:pPr><w:spacing w:after="100"/>'
        "</w:pPr></w:pPrDefault></w:docDefaults>"
        "</w:styles>"
    )
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{adapter.W_NS}"><w:body>'
        '<w:p><w:pPr><w:contextualSpacing/></w:pPr>'
        '<w:r><w:t>first line</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:contextualSpacing/></w:pPr>'
        '<w:r><w:t>second line</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    path = _docx_from_document(tmp_path, document, styles=styles)
    records = adapter.read_w_jc(path)
    assert [r.text for r in records] == ["first line", "second line"]
    assert _groups(records) == [1, 1]


def test_read_w_jc_style_spacing_overrides_doc_default_spacing(tmp_path: Path) -> None:
    styles = (
        '<?xml version="1.0"?>'
        f'<w:styles xmlns:w="{adapter.W_NS}">'
        '<w:docDefaults><w:pPrDefault><w:pPr><w:spacing w:after="100"/>'
        "</w:pPr></w:pPrDefault></w:docDefaults>"
        '<w:style w:type="paragraph" w:styleId="NoGap">'
        '<w:pPr><w:spacing w:after="0"/></w:pPr>'
        "</w:style>"
        "</w:styles>"
    )
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{adapter.W_NS}"><w:body>'
        '<w:p><w:pPr><w:pStyle w:val="NoGap"/><w:contextualSpacing/></w:pPr>'
        '<w:r><w:t>first paragraph</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="NoGap"/><w:contextualSpacing/></w:pPr>'
        '<w:r><w:t>second paragraph</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    path = _docx_from_document(tmp_path, document, styles=styles)
    records = adapter.read_w_jc(path)
    assert [r.text for r in records] == ["first paragraph", "second paragraph"]
    assert _groups(records) == [None, None]


def test_read_w_jc_marks_structural_empty_paragraphs(tmp_path: Path) -> None:
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{adapter.W_NS}"><w:body>'
        '<w:p><w:r><w:t>before</w:t></w:r></w:p>'
        "<w:p/>"
        '<w:p><w:r><w:t>after</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )
    path = _docx_from_document(tmp_path, document)
    records = adapter.read_w_jc(path)
    assert [r.text for r in records] == ["before", "", "after"]
    assert [r.empty for r in records] == [False, True, False]
    assert [r.source_span for r in records] == [
        ir.SourceSpan(0, 0),
        ir.SourceSpan(1, 1),
        ir.SourceSpan(2, 2),
    ]


def test_read_w_jc_visual_group_does_not_bridge_list_item(tmp_path: Path) -> None:
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{adapter.W_NS}"><w:body>'
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
    records = adapter.read_w_jc(path)
    assert [r.text for r in records] == ["before list", "after list"]
    assert _groups(records) == [None, None]


def test_paragraph_text_drops_mc_fallback_duplicate(tmp_path: Path) -> None:
    # A run with both an mc:Choice and an mc:Fallback rendering of the SAME text
    # must be counted ONCE (walking every w:t would double it and desync matching).
    mc = "http://schemas.openxmlformats.org/markup-compatibility/2006"
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{adapter.W_NS}" xmlns:mc="{mc}"><w:body>'
        "<w:p><w:r>"
        "<mc:AlternateContent>"
        '<mc:Choice Requires="wpg"><w:t>Title</w:t></mc:Choice>'
        "<mc:Fallback><w:t>Title</w:t></mc:Fallback>"
        "</mc:AlternateContent>"
        "</w:r></w:p>"
        "</w:body></w:document>"
    )
    path = _docx_from_document(tmp_path, document)
    records = adapter.read_w_jc(path)
    assert [r.text for r in records] == ["Title"]  # not "TitleTitle"


def test_paragraph_text_keeps_no_break_hyphen(tmp_path: Path) -> None:
    # Word stores a non-breaking hyphen as a textless `w:noBreakHyphen` between two
    # `w:t` runs; Pandoc renders it U+2011. Dropping it here fuses `кто‑то`→`ктото`,
    # so the match fingerprint diverges from the AST, the paragraph never matches its
    # source `w:p`, and its span stays None → the line is UNMAPPED, hence non-votable.
    # (Fingerprint-desync, distinct from the §14-P1 verse-merge MIXED case.) Survive it.
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{adapter.W_NS}"><w:body>'
        "<w:p><w:r><w:t>кто</w:t><w:noBreakHyphen/><w:t>то</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    path = _docx_from_document(tmp_path, document)
    records = adapter.read_w_jc(path)
    assert [r.text for r in records] == ["кто‑то"]  # not "ктото"


def test_paragraph_text_keeps_soft_hyphen(tmp_path: Path) -> None:
    # The same desync via Word's optional hyphen: a textless `w:softHyphen` renders
    # U+00AD in Pandoc. Dropping it fuses the flanking words just like the non-breaking
    # hyphen, so the record must key on the same glyph.
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{adapter.W_NS}"><w:body>'
        "<w:p><w:r><w:t>кто</w:t><w:softHyphen/><w:t>то</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    path = _docx_from_document(tmp_path, document)
    records = adapter.read_w_jc(path)
    assert [r.text for r in records] == ["кто­то"]  # soft hyphen kept, not "ктото"


def test_no_break_hyphen_paragraph_keeps_source_span(tmp_path: Path) -> None:
    # End-to-end: the fingerprint match must survive a non-breaking hyphen so the
    # paragraph keeps a source span. The AST carries Pandoc's U+2011 rendering and the
    # raw record (read from the XML) must key on the SAME glyph, not a fused word.
    para = adapter._block(_para(_str("кто‑то")), adapter._Ctx())
    assert isinstance(para, ir.Paragraph)
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{adapter.W_NS}"><w:body>'
        "<w:p><w:r><w:t>кто</w:t><w:noBreakHyphen/><w:t>то</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    records = adapter.read_w_jc(_docx_from_document(tmp_path, document))
    adapter.reconcile_source([para], records)
    assert para.source_span == ir.SourceSpan(0, 0)


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
        f'<w:document xmlns:w="{adapter.W_NS}"><w:body>'
        + _bordered_para("boxed", "top", "bottom", "left", "right")
        + _bordered_para("ruled", "left")
        + _bordered_para("topped", "top")
        + _bordered_para("noned", "top", "bottom", "left", "right", val="none")
        + _bordered_para("plain")
        + "</w:body></w:document>"
    )
    records = adapter.read_w_jc(_docx_from_document(tmp_path, document))
    assert [r.border for r in records] == ["box", "rule", "other", "", ""]


def test_reconcile_fused_bordered_and_plain_stays_unbordered(tmp_path: Path) -> None:
    # A Pandoc-fused block consuming a bordered AND a plain source row must NOT
    # inherit the border — that would drag plain text into a set-apart register.
    para = adapter._block(_para(_str("framed words plain words")), adapter._Ctx())
    assert isinstance(para, ir.Paragraph)
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{adapter.W_NS}"><w:body>'
        + _bordered_para("framed words", "left")
        + "<w:p><w:r><w:t>plain words</w:t></w:r></w:p>"
        + "</w:body></w:document>"
    )
    records = adapter.read_w_jc(_docx_from_document(tmp_path, document))
    adapter.reconcile_source([para], records)
    assert para.border == ""


def test_reconcile_assigns_border_kind(tmp_path: Path) -> None:
    para = adapter._block(_para(_str("set-apart inset passage")), adapter._Ctx())
    assert isinstance(para, ir.Paragraph)
    document = (
        '<?xml version="1.0"?>'
        f'<w:document xmlns:w="{adapter.W_NS}"><w:body>'
        + _bordered_para("set-apart inset passage", "left")
        + "</w:body></w:document>"
    )
    records = adapter.read_w_jc(_docx_from_document(tmp_path, document))
    adapter.reconcile_source([para], records)
    assert para.border == "rule"


# ---------------------------------------------------------------------------
# C1: alignment is reconciled by CONTENT, not position — a collapsed list /
# image-only paragraph before a right-aligned paragraph no longer drifts the zip.
# ---------------------------------------------------------------------------


def test_reconcile_alignment_survives_list_and_image_before_right_para() -> None:
    """The C1 regression: a list (N w:p → 1 ListBlock) and an image-only paragraph
    appear BEFORE a right-aligned signature paragraph. Under the old positional
    zip the index lagged after the collapse and the right-aligned paragraph lost
    its alignment. Reconcile-by-content must still mark it align='right'."""
    # The AST as Pandoc would emit it: the list collapses to ONE OrderedList block
    # (its two items gone from the top-level Para sequence), the image-only w:p
    # surfaces as a Para, and the signature follows.
    ctx = adapter._Ctx()
    blocks = [
        adapter._block(_para(_str("Opening prose paragraph.")), ctx),
        adapter._block(
            {"t": "OrderedList", "c": [[1, {"t": "Decimal"}, {"t": "Period"}],
                                       [[_para(_str("first step"))],
                                        [_para(_str("second step"))]]]},
            ctx,
        ),
        adapter._block(_para({"t": "Image", "c": [["", [], []], [], ["m/p.png", ""]]}), ctx),
        adapter._block(_para(_str("Signed Pankratius")), ctx),
    ]
    # The w:jc records as read_w_jc would produce them: list items are ALREADY
    # skipped by read_w_jc, but the image-only paragraph stays — and the only
    # right-aligned record is the signature.
    records = [
        adapter._SourceParagraph(align="", text="Opening prose paragraph."),
        adapter._SourceParagraph(align="", text=""),  # the image-only paragraph
        adapter._SourceParagraph(align="right", text="Signed Pankratius"),
    ]
    _spans, right = adapter.reconcile_source(blocks, records)
    paragraphs = [b for b in blocks if isinstance(b, ir.Paragraph)]
    # The signature paragraph (the only Para carrying that text) is align='right'.
    sig = next(p for p in paragraphs if ir.Text("Signed Pankratius") in p.inlines)
    assert sig.align == "right"
    assert right == 1
    # The opening prose paragraph keeps the default alignment.
    opening = next(p for p in paragraphs if ir.Text("Opening prose paragraph.") in p.inlines)
    assert opening.align == ""


def test_reconcile_alignment_merged_right_paragraphs() -> None:
    """Several short right-aligned w:p that Pandoc FUSES into one multi-line Para
    (the epigraph shape) must still mark that one AST paragraph align='right'."""
    ctx = adapter._Ctx()
    para = adapter._block(
        _para(_str("Тогда волк"), {"t": "LineBreak"}, _str("будет жить")), ctx
    )
    assert isinstance(para, ir.Paragraph)
    records = [
        adapter._SourceParagraph(align="right", text="Тогда волк", source_span=ir.SourceSpan(5, 5)),
        adapter._SourceParagraph(align="right", text="будет жить", source_span=ir.SourceSpan(6, 6)),
    ]
    spans, right = adapter.reconcile_source([para], records)
    assert para.align == "right" and right == 1 and spans == 1
    assert para.source_span == ir.SourceSpan(5, 6)


def test_reconcile_alignment_refuses_fusion_across_source_gap() -> None:
    """A fused Pandoc paragraph must not claim a span across skipped source blocks."""
    ctx = adapter._Ctx()
    para = adapter._block(
        _para(_str("before list"), {"t": "LineBreak"}, _str("after list")), ctx
    )
    assert isinstance(para, ir.Paragraph)
    records = [
        adapter._SourceParagraph(align="right", text="before list", source_span=ir.SourceSpan(5, 5)),
        adapter._SourceParagraph(align="right", text="after list", source_span=ir.SourceSpan(7, 7)),
    ]

    spans, right = adapter.reconcile_source([para], records)

    assert (spans, right) == (0, 0)
    assert para.align == ""
    assert para.source_span is None


def test_reconcile_alignment_refuses_fusion_across_table_boundary() -> None:
    """Tables are not paragraph ordinals, so segment metadata must block fusion."""
    ctx = adapter._Ctx()
    para = adapter._block(
        _para(_str("before table"), {"t": "LineBreak"}, _str("after table")), ctx
    )
    assert isinstance(para, ir.Paragraph)
    records = [
        adapter._SourceParagraph(
            align="right",
            text="before table",
            source_span=ir.SourceSpan(5, 5),
            source_segment=0,
        ),
        adapter._SourceParagraph(
            align="right",
            text="after table",
            source_span=ir.SourceSpan(6, 6),
            source_segment=1,
        ),
    ]

    spans, right = adapter.reconcile_source([para], records)

    assert (spans, right) == (0, 0)
    assert para.align == ""
    assert para.source_span is None


def test_source_span_assignment_keeps_punctuation_only_structural_paragraph() -> None:
    block = ir.Paragraph(inlines=[ir.Text("***")])
    records = [
        adapter._SourceParagraph(align="", text="***", source_span=ir.SourceSpan(12, 12)),
    ]

    spans, _right = adapter.reconcile_source([block], records)

    assert spans == 1
    assert block.source_span == ir.SourceSpan(12, 12)


def test_source_span_assignment_matches_blockquote_before_duplicate_later_text() -> None:
    blocks: list[ir.Block] = [
        ir.Paragraph(inlines=[ir.Text("Title")]),
        ir.BlockQuote(blocks=[ir.Paragraph(inlines=[ir.Text("Repeated dedication")])]),
        ir.Heading(level=2, inlines=[ir.Text("Chapter")]),
        ir.Paragraph(inlines=[ir.Text("Repeated dedication")]),
    ]
    records = [
        adapter._SourceParagraph(align="", text="Title", source_span=ir.SourceSpan(1, 1)),
        adapter._SourceParagraph(
            align="",
            text="Repeated dedication",
            source_span=ir.SourceSpan(2, 2),
        ),
        adapter._SourceParagraph(align="", text="Chapter", source_span=ir.SourceSpan(3, 3)),
        adapter._SourceParagraph(
            align="",
            text="Repeated dedication",
            source_span=ir.SourceSpan(4, 4),
        ),
    ]

    spans, _right = adapter.reconcile_source(blocks, records)

    assert spans == 4
    assert [block.source_span for block in blocks] == [
        ir.SourceSpan(1, 1),
        ir.SourceSpan(2, 2),
        ir.SourceSpan(3, 3),
        ir.SourceSpan(4, 4),
    ]


def test_source_span_assignment_keeps_structural_empty_paragraph() -> None:
    blocks: list[ir.Block] = [
        ir.Paragraph(inlines=[ir.Text("before")]),
        ir.Paragraph(inlines=[], empty=True),
        ir.Paragraph(inlines=[ir.Text("after")]),
    ]
    records = [
        adapter._SourceParagraph(align="", text="before", source_span=ir.SourceSpan(1, 1)),
        adapter._SourceParagraph(align="", text="", source_span=ir.SourceSpan(2, 2), empty=True),
        adapter._SourceParagraph(align="", text="after", source_span=ir.SourceSpan(3, 3)),
    ]

    spans, _right = adapter.reconcile_source(blocks, records)

    assert spans == 3
    assert [block.source_span for block in blocks] == [
        ir.SourceSpan(1, 1),
        ir.SourceSpan(2, 2),
        ir.SourceSpan(3, 3),
    ]


def test_reconcile_alignment_no_match_assigns_nothing() -> None:
    """When NO source word aligns to the AST (the precondition for the
    `import.align-unreconciled` safety warning), reconciliation assigns nothing —
    it never guesses an alignment onto an unrelated paragraph."""
    ctx = adapter._Ctx()
    para = adapter._block(_para(_str("completely different prose")), ctx)
    assert isinstance(para, ir.Paragraph)
    records = [adapter._SourceParagraph(align="right", text="unrelated source words")]
    _spans, right = adapter.reconcile_source([para], records)
    assert right == 0 and para.align == ""


def test_reconcile_source_duplicate_text_does_not_overshoot_early_right_para() -> None:
    """The book#32 failure class: a duplicated prose text whose FIRST AST occurrence
    sits ahead of an early right-aligned signature. A single global greedy cursor
    binds the duplicate's record to the early occurrence and overshoots the
    signature; the anchored alignment windows the scan so both reconcile."""
    ctx = adapter._Ctx()
    blocks = [
        adapter._block(_para(_str("Repeated chorus line")), ctx),     # 1st occurrence
        adapter._block(_para(_str("Signed Pankratius")), ctx),        # early signature
        adapter._block(_para(_str("Unique middle paragraph")), ctx),  # anchor
        adapter._block(_para(_str("Repeated chorus line")), ctx),     # 2nd occurrence
    ]
    records = [
        adapter._SourceParagraph(align="", text="Repeated chorus line", source_span=ir.SourceSpan(0, 0)),
        adapter._SourceParagraph(align="right", text="Signed Pankratius", source_span=ir.SourceSpan(1, 1)),
        adapter._SourceParagraph(align="", text="Unique middle paragraph", source_span=ir.SourceSpan(2, 2)),
        adapter._SourceParagraph(align="", text="Repeated chorus line", source_span=ir.SourceSpan(3, 3)),
    ]
    spans, right = adapter.reconcile_source(blocks, records)
    assert spans == 4 and right == 1
    assert [b.source_span for b in blocks] == [
        ir.SourceSpan(0, 0), ir.SourceSpan(1, 1), ir.SourceSpan(2, 2), ir.SourceSpan(3, 3),
    ]
    sig = blocks[1]
    assert isinstance(sig, ir.Paragraph) and sig.align == "right"


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
        f'<w:document xmlns:w="{adapter.W_NS}"><w:body>'
        + "".join(paras)
        + "</w:body></w:document>"
    )
    records = adapter.read_w_jc(_docx_from_document(tmp_path, document))
    assert [r.indented for r in records] == [False, False, False, True, False]


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
        assert cmd[0] == "pandoc"
        captured.update(kwargs)
        return _FakeProc()

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter.run_pandoc_json(tmp_path / "x.docx", tmp_path / "media")
    assert "timeout" in captured, "pandoc subprocess.run must pass a timeout"
    assert isinstance(captured["timeout"], (int, float)) and captured["timeout"] > 0


def test_run_pandoc_timeout_raises_clear_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # On a TimeoutExpired the adapter must raise a clear, actionable error (not let
    # the raw subprocess exception bubble unexplained).
    def fake_run(cmd: list[str], **kwargs: object) -> object:
        timeout = kwargs.get("timeout", 0)
        raise subprocess.TimeoutExpired(cmd, float(timeout) if isinstance(timeout, (int, float)) else 0.0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match=r"(?i)timed out|timeout"):
        adapter.run_pandoc_json(tmp_path / "x.docx", tmp_path / "media")
