from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from pancratius import cli, docx_inspect, ir
from pancratius.docx_inspect import (
    BlockSourceHit,
    DocxInspectError,
    InspectOptions,
    MaskVerdict,
    ParaRow,
    _verdict_for,
    parse_index_range,
    votability_mask,
)


def _write_docx(path: Path, paragraphs: list[str]) -> None:
    from docx import Document

    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    doc.save(str(path))


pandoc_required = pytest.mark.skipif(
    shutil.which("pandoc") is None,
    reason="pandoc is required for importer-backed DOCX inspection",
)


@pandoc_required
def test_docx_inspect_cli_smoke_with_temp_docx_fixture(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source.docx"
    _write_docx(source, ["Alpha opening", "Beta marker", "Gamma close"])

    rc = cli.main(["docx", "inspect", str(source)])

    assert rc == 0
    out = capsys.readouterr().out
    assert "body paragraphs" in out
    assert "Alpha opening" in out
    assert "Beta marker" in out


@pandoc_required
def test_docx_inspect_cli_contains_filter(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "source.docx"
    _write_docx(source, ["Alpha opening", "Beta marker", "Gamma close"])

    rc = cli.main(["docx", "inspect", str(source), "--contains", "Beta"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "3 body paragraphs, 1 shown" in out
    assert "Beta marker" in out
    assert "Alpha opening" not in out
    assert "Gamma close" not in out


def test_docx_inspect_cli_missing_file_is_usage_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.docx"

    rc = cli.main(["docx", "inspect", str(missing)])

    assert rc == 2
    assert "DOCX not found" in capsys.readouterr().err


def test_docx_inspect_rejects_ambiguous_filters() -> None:
    with pytest.raises(DocxInspectError, match="choose only one inspect filter"):
        InspectOptions(contains="Alpha", index_range=parse_index_range("0:2"))


def test_docx_inspect_marks_repeated_text_with_mixed_import_roles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = ParaRow(
        index=0,
        text="Same",
        style="Normal",
        direct_style="",
        align="",
        contextual=False,
        spacing={},
        indent={},
        numbered=False,
        border="",
        heading=False,
        thematic=False,
        br_count=0,
        empty=False,
    )

    monkeypatch.setattr(
        docx_inspect,
        "classify_blocks",
        lambda _docx: docx_inspect.BlockClassifications(
            by_text={"Same": frozenset({"Paragraph", "VerseBlock"})},
            by_source={},
        ),
    )

    docx_inspect.annotate([row], Path("source.docx"))

    assert row.block_kind == "Ambiguous[Paragraph|VerseBlock]"


def test_docx_inspect_prefers_source_span_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = ParaRow(
        index=4,
        text="Repeated",
        style="Normal",
        direct_style="",
        align="",
        contextual=False,
        spacing={},
        indent={},
        numbered=False,
        border="",
        heading=False,
        thematic=False,
        br_count=0,
        empty=False,
    )
    span = ir.SourceSpan(4, 6)

    monkeypatch.setattr(
        docx_inspect,
        "classify_blocks",
        lambda _docx: docx_inspect.BlockClassifications(
            by_text={"Repeated": frozenset({"Paragraph"})},
            by_source={4: docx_inspect.BlockSourceHit(frozenset({"VerseBlock"}), span)},
        ),
    )

    docx_inspect.annotate([row], Path("source.docx"))

    assert row.block_kind == "VerseBlock"
    assert row.block_source_span == span


def test_docx_inspect_classifies_empty_rows_inside_source_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = ParaRow(
        index=5,
        text="",
        style="Normal",
        direct_style="",
        align="",
        contextual=False,
        spacing={},
        indent={},
        numbered=False,
        border="",
        heading=False,
        thematic=False,
        br_count=0,
        empty=True,
    )
    span = ir.SourceSpan(4, 6)

    monkeypatch.setattr(
        docx_inspect,
        "classify_blocks",
        lambda _docx: docx_inspect.BlockClassifications(
            by_text={},
            by_source={5: docx_inspect.BlockSourceHit(frozenset({"VerseBlock"}), span)},
        ),
    )

    docx_inspect.annotate([row], Path("source.docx"))

    assert row.block_kind == "VerseBlock"
    assert "ir=4..6" in docx_inspect.render([row])


def test_docx_inspect_classifies_empty_rows_from_real_block_span(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = ParaRow(
        index=5,
        text="",
        style="Normal",
        direct_style="",
        align="",
        contextual=False,
        spacing={},
        indent={},
        numbered=False,
        border="",
        heading=False,
        thematic=False,
        br_count=0,
        empty=True,
    )

    def fake_adapt(_docx: Path, _media_dir: Path) -> ir.Document:
        return ir.Document(blocks=[
            ir.VerseBlock(
                stanzas=[[[ir.Text("before")]], [[ir.Text("after")]]],
                source_span=ir.SourceSpan(4, 6),
            )
        ])

    monkeypatch.setattr(docx_inspect.da, "adapt", fake_adapt)

    docx_inspect.annotate([row], Path("source.docx"))

    assert row.block_kind == "VerseBlock"
    assert row.block_source_span == ir.SourceSpan(4, 6)


def test_docx_inspect_kind_filters_keep_ambiguous_candidates() -> None:
    rows = [
        ParaRow(
            index=0,
            text="same",
            style="Normal",
            direct_style="",
            align="",
            contextual=False,
            spacing={},
            indent={},
            numbered=False,
            border="",
            heading=False,
            thematic=False,
            br_count=0,
            empty=False,
            block_kind="Ambiguous[LineatedBlock|Paragraph]",
        )
    ]

    selected = docx_inspect.select_rows(rows, InspectOptions(lineated_only=True))

    assert selected == rows


# --- votability mask (Slice 0): contract = which source ordinals are votable body ----------
# These pin the VERDICT contract, not the mask's internals: at the structural seam the mask
# observes, body is plain Paragraph; structural-only is context; every ambiguous case
# (mixed/unknown/unmapped/unexpected-merge) stays votable-but-flagged — never silently masked.
# The same outcomes must hold when the re-architecture reads votability off the structural-IR
# seam directly instead of this shim.

def _hit(kinds: set[str], start: int, end: int) -> BlockSourceHit:
    return BlockSourceHit(kinds=frozenset(kinds), span=ir.SourceSpan(start, end))


def test_verdict_body_kinds_are_votable() -> None:
    assert _verdict_for(_hit({"Paragraph"}, 5, 5)) is MaskVerdict.BODY
    # lineated/verse do not appear at the structural seam, but are listed as body for
    # robustness — if ever observed past the seam they stay BODY, never a leaked verdict.
    assert _verdict_for(_hit({"LineatedBlock"}, 5, 8)) is MaskVerdict.BODY
    assert _verdict_for(_hit({"VerseBlock"}, 5, 8)) is MaskVerdict.BODY


def test_verdict_structural_only_is_context() -> None:
    for kind in ("Heading", "Signature", "DialogueLabel", "Table", "ThematicBreak"):
        assert _verdict_for(_hit({kind}, 3, 3)) is MaskVerdict.CONTEXT


def test_verdict_mixed_kinds_stay_votable_flagged() -> None:
    # a <w:p> that split into label + body must never collapse to the structural half
    assert _verdict_for(_hit({"DialogueLabel", "Paragraph"}, 7, 7)) is MaskVerdict.REVIEW


def test_verdict_unknown_kind_is_review() -> None:
    assert _verdict_for(_hit({"FootnoteDef"}, 2, 2)) is MaskVerdict.REVIEW


def test_verdict_unmapped_ordinal_is_review() -> None:
    assert _verdict_for(None) is MaskVerdict.REVIEW


def test_verdict_unexpected_paragraph_merge_is_review() -> None:
    # a plain Paragraph owns ONE ordinal; spanning >1 is an unexpected merge → flag it
    assert _verdict_for(_hit({"Paragraph"}, 5, 7)) is MaskVerdict.REVIEW


def test_votability_mask_keys_per_ordinal_and_leaves_unmapped_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = ir.Document(blocks=[
        ir.Paragraph(inlines=[ir.Text("prose")], source_span=ir.SourceSpan(0, 0)),
        ir.Heading(level=1, inlines=[ir.Text("H")], source_span=ir.SourceSpan(1, 1)),
        ir.VerseBlock(stanzas=[[[ir.Text("v")]]], source_span=ir.SourceSpan(2, 4)),
    ])
    monkeypatch.setattr(docx_inspect.da, "adapt", lambda _docx, _media: doc)
    monkeypatch.setattr("pancratius.ir.normalize.normalize", lambda d, **_k: d)

    mask = votability_mask(Path("source.docx"))

    assert mask[0] is MaskVerdict.BODY
    assert mask[1] is MaskVerdict.CONTEXT
    # the verse block's merged ordinals 2..4 each resolve to a clean BODY
    assert mask[2] is mask[3] is mask[4] is MaskVerdict.BODY
    assert 5 not in mask   # unmapped ordinal is absent → the caller defaults it to REVIEW


@pandoc_required
def test_lineation_decisions_per_ordinal_surface(tmp_path: Path) -> None:
    """The per-`w:p`-ordinal prose/lineated surface the lineation gold joins on:
    prose stays False, an authored hard break with a prose-length line is reported
    prose (the break is display, not register), structure is absent, and a folded
    couplet after a heading is True."""
    from docx import Document

    doc = Document()
    doc.add_paragraph(
        "Это длинное прозаическое предложение, которое заведомо длиннее любой "
        "стихотворной строки и читается как обычный абзац без всякой лиричности."
    )
    broken = doc.add_paragraph()
    run = broken.add_run("1. Вода")
    run.add_break()
    broken.add_run("Мир — как река. " * 12)
    doc.add_heading("Псалом", level=2)
    doc.add_paragraph("Свет мой тихий,")
    doc.add_paragraph("в сердце горит.")
    path = tmp_path / "fixture.docx"
    doc.save(str(path))

    decisions = docx_inspect.lineation_decisions(path)

    assert decisions[0] is False
    assert decisions[1] is False  # hard break preserved for display, prose register
    assert 2 not in decisions     # the heading is structure, not a votable body line
    assert decisions[3] is True and decisions[4] is True


@pandoc_required
def test_lineation_decisions_cover_register_quote_members(tmp_path: Path) -> None:
    """Paragraphs the display-register pass wraps (scripture/inset quotes) keep
    their per-ordinal lineation coverage: a prose-length bordered paragraph is
    still a False label, and a bordered hard-break couplet stays True — the
    wrapped run must not vanish from the gold-join surface."""
    from docx import Document
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph as DocxParagraph

    def set_border(paragraph: DocxParagraph, *sides: str) -> None:
        ppr = paragraph._p.get_or_add_pPr()  # fixture-only OOXML poke
        pbdr = ppr.makeelement(qn("w:pBdr"), {})
        for side in sides:
            el = ppr.makeelement(qn(f"w:{side}"), {qn("w:val"): "single", qn("w:sz"): "4"})
            pbdr.append(el)
        ppr.append(pbdr)

    doc = Document()
    filler = (
        "Это длинное прозаическое предложение, которое заведомо длиннее любой "
        "стихотворной строки и читается как обычный абзац без всякой лиричности."
    )
    for _ in range(8):
        doc.add_paragraph(filler)
    boxed = doc.add_paragraph(
        "7 Се, грядет с облаками, и узрит Его всякое око и те, которые пронзили Его."
    )
    set_border(boxed, "top", "bottom", "left", "right")
    ruled = doc.add_paragraph()
    run = ruled.add_run("Я — не форма,")
    run.add_break()
    ruled.add_run("но во всех формах живу.")
    set_border(ruled, "left")
    path = tmp_path / "fixture-borders.docx"
    doc.save(str(path))

    decisions = docx_inspect.lineation_decisions(path)

    assert decisions[8] is False   # boxed prose verse: covered, prose register
    assert decisions[9] is True    # ruled hard-break couplet: covered, lineated


@pandoc_required
def test_lineation_decisions_en_edition_mirrors_ru(tmp_path: Path) -> None:
    """The EN editions get the same per-ordinal surface: EN prose stays False,
    an EN speaker turn (`Answer from the Creator:`) is structure — absent, never
    a verse line — and an EN couplet after a heading folds True."""
    from docx import Document

    doc = Document()
    doc.add_paragraph(
        "This is a long prose sentence that is obviously longer than any line of "
        "verse and reads like an ordinary paragraph without any lyricism at all."
    )
    turn = doc.add_paragraph()
    turn.add_run("Answer from the Creator:").bold = True
    doc.add_paragraph("A plain single answer sentence follows the speaker label.")
    doc.add_heading("Psalm", level=2)
    doc.add_paragraph("My quiet light,")
    doc.add_paragraph("burns in the heart.")
    path = tmp_path / "fixture-en.docx"
    doc.save(str(path))

    decisions = docx_inspect.lineation_decisions(path)

    assert decisions[0] is False
    assert decisions.get(1) is not True   # the speaker turn is never lineated
    assert decisions[2] is False
    assert 3 not in decisions             # the heading is structure
    assert decisions[4] is True and decisions[5] is True
