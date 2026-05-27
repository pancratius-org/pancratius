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
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

from pancratius import docx_adapter as adapter, ir  # noqa: E402

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
    ("quote_type", "text", "single"),
    [
        ("DoubleQuote", "d", False),
        ("SingleQuote", "s", True),
    ],
)
def test_quoted_carries_single_flag(quote_type: str, text: str, single: bool) -> None:
    ctx = adapter._Ctx()
    out = adapter._inline({"t": "Quoted", "c": [{"t": quote_type}, [_str(text)]]}, ctx)
    assert out == [ir.Quoted(single, [ir.Text(text)])]


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


def test_div_becomes_transparent_container() -> None:
    ctx = adapter._Ctx()
    b = adapter._block({"t": "Div", "c": [["", [], []], [_para(_str("d"))]]}, ctx)
    assert isinstance(b, ir.BlockQuote) and b.role == "_div"


def test_figure_unwraps_content_and_caption() -> None:
    ctx = adapter._Ctx()
    img: dict[str, object] = {"t": "Image", "c": [["", [], []], [], ["m/p.png", ""]]}
    figure = {"t": "Figure", "c": [["", [], []],
                                   [None, [_para(_str("the caption"))]],
                                   [_para(img)]]}
    b = adapter._block(figure, ctx)
    assert isinstance(b, ir.BlockQuote) and b.role == "_div"
    # neither the image nor the caption text is lost
    kinds = [type(x).__name__ for x in b.blocks]
    assert kinds.count("Paragraph") == 2


def test_line_block_maps_to_verse_lines_not_unknown() -> None:
    # Bug 4(a): a Pandoc LineBlock is verse-like lines — it must map to REAL content
    # (a VerseBlock with each line preserved), not an UnknownBlock that lowering
    # drops. LineBlock c = [[inlines for line 1], [inlines for line 2], ...].
    ctx = adapter._Ctx()
    node = {"t": "LineBlock", "c": [
        [_str("Roses are red,")],
        [_str("violets are blue.")],
    ]}
    b = adapter._block(node, ctx)
    assert isinstance(b, ir.VerseBlock), f"LineBlock should map to VerseBlock, got {type(b).__name__}"
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


def _docx_from_document(document: str) -> Path:
    """Wrap a `word/document.xml` string into a minimal in-memory .docx temp file."""
    import tempfile
    fd = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
    with zipfile.ZipFile(fd, "w") as zf:
        zf.writestr("word/document.xml", document)
    fd.close()
    return Path(fd.name)


def _docx_with_paragraphs(*jcs: str | None) -> Path:
    """Build a minimal in-memory .docx whose body has one `w:p` per `jcs` entry
    (a `w:jc` with that val when not None). Returned as a temp file path."""
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
    return _docx_from_document(document)


def _aligns(records: list[adapter._JcRecord]) -> list[str]:
    return [r.align for r in records]


def _groups(records: list[adapter._JcRecord]) -> list[int | None]:
    return [r.lineation_group for r in records]


def test_read_w_jc_returns_alignment_per_body_paragraph() -> None:
    path = _docx_with_paragraphs("right", None, "center")
    try:
        assert _aligns(adapter.read_w_jc(path)) == ["right", "", "center"]
    finally:
        path.unlink()


def test_read_w_jc_skips_table_paragraphs() -> None:
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
    path = _docx_from_document(document)
    try:
        assert _aligns(adapter.read_w_jc(path)) == ["right", ""]  # the table para is skipped
    finally:
        path.unlink()


def test_read_w_jc_skips_list_item_paragraphs() -> None:
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
    path = _docx_from_document(document)
    try:
        records = adapter.read_w_jc(path)
        assert _aligns(records) == ["", "right"]  # the two list items are skipped
        assert [r.text for r in records] == ["before", "after"]
    finally:
        path.unlink()


def test_read_w_jc_marks_contextual_spacing_visual_group() -> None:
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
    path = _docx_from_document(document)
    try:
        records = adapter.read_w_jc(path)
        assert [r.text for r in records] == ["first line", "second line", "third line"]
        assert _groups(records) == [1, 1, 1]
    finally:
        path.unlink()


def test_read_w_jc_visual_group_does_not_bridge_list_item() -> None:
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
    path = _docx_from_document(document)
    try:
        records = adapter.read_w_jc(path)
        assert [r.text for r in records] == ["before list", "after list"]
        assert _groups(records) == [None, None]
    finally:
        path.unlink()


def test_paragraph_text_drops_mc_fallback_duplicate() -> None:
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
    path = _docx_from_document(document)
    try:
        records = adapter.read_w_jc(path)
        assert [r.text for r in records] == ["Title"]  # not "TitleTitle"
    finally:
        path.unlink()


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
        adapter._JcRecord(align="", text="Opening prose paragraph."),
        adapter._JcRecord(align="", text=""),  # the image-only paragraph
        adapter._JcRecord(align="right", text="Signed Pankratius"),
    ]
    paragraphs = [b for b in blocks if isinstance(b, ir.Paragraph)]
    assigned = adapter.reconcile_alignment(paragraphs, records)
    # The signature paragraph (the only Para carrying that text) is align='right'.
    sig = next(p for p in paragraphs if ir.Text("Signed Pankratius") in p.inlines)
    assert sig.align == "right"
    assert assigned == 1
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
        adapter._JcRecord(align="right", text="Тогда волк"),
        adapter._JcRecord(align="right", text="будет жить"),
    ]
    assigned = adapter.reconcile_alignment([para], records)
    assert para.align == "right" and assigned == 1


def test_reconcile_alignment_no_match_assigns_nothing() -> None:
    """When NO source word aligns to the AST (the precondition for the
    `import.align-unreconciled` safety warning), reconciliation assigns nothing —
    it never guesses an alignment onto an unrelated paragraph."""
    ctx = adapter._Ctx()
    para = adapter._block(_para(_str("completely different prose")), ctx)
    assert isinstance(para, ir.Paragraph)
    records = [adapter._JcRecord(align="right", text="unrelated source words")]
    assigned = adapter.reconcile_alignment([para], records)
    assert assigned == 0 and para.align == ""


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
    with pytest.raises(RuntimeError, match="(?i)timed out|timeout"):
        adapter.run_pandoc_json(tmp_path / "x.docx", tmp_path / "media")
