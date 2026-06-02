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
    _orphaned_body_ordinals,
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
        bordered=False,
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
        bordered=False,
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
        bordered=False,
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
        bordered=False,
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
            bordered=False,
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
# These pin the VERDICT contract, not the mask's internals: lineated/verse redact to votable,
# structural-only is context, and every ambiguous case (mixed/unknown/unmapped/orphan/merge)
# stays votable-but-flagged — never silently masked. The same outcomes must hold when the
# re-architecture reads votability off the structural-IR seam instead of this shim.

def _hit(kinds: set[str], start: int, end: int) -> BlockSourceHit:
    return BlockSourceHit(kinds=frozenset(kinds), span=ir.SourceSpan(start, end))


def test_verdict_body_kinds_are_votable() -> None:
    assert _verdict_for(_hit({"Paragraph"}, 5, 5), orphan_body=False) is MaskVerdict.BODY
    # lineated/verse legitimately MERGE several source ordinals — the redacted lineation
    # verdict, not an ambiguity — so they stay a clean BODY, never surfaced as a label.
    assert _verdict_for(_hit({"LineatedBlock"}, 5, 8), orphan_body=False) is MaskVerdict.BODY
    assert _verdict_for(_hit({"VerseBlock"}, 5, 8), orphan_body=False) is MaskVerdict.BODY


def test_verdict_structural_only_is_context() -> None:
    for kind in ("Heading", "Signature", "DialogueLabel", "Table", "ThematicBreak"):
        assert _verdict_for(_hit({kind}, 3, 3), orphan_body=False) is MaskVerdict.CONTEXT


def test_verdict_mixed_kinds_stay_votable_flagged() -> None:
    # a <w:p> that split into label + body must never collapse to the structural half
    assert _verdict_for(_hit({"DialogueLabel", "Paragraph"}, 7, 7),
                        orphan_body=False) is MaskVerdict.REVIEW


def test_verdict_unknown_kind_is_review() -> None:
    assert _verdict_for(_hit({"FootnoteDef"}, 2, 2), orphan_body=False) is MaskVerdict.REVIEW


def test_verdict_unmapped_ordinal_is_review() -> None:
    assert _verdict_for(None, orphan_body=False) is MaskVerdict.REVIEW


def test_verdict_orphan_body_overrides_label_context() -> None:
    # a lone DialogueLabel whose lineated body lost its span carries votable text the
    # classifier can no longer see at this ordinal — flag it, never mask it to context.
    assert _verdict_for(_hit({"DialogueLabel"}, 21, 21), orphan_body=True) is MaskVerdict.REVIEW


def test_verdict_unexpected_paragraph_merge_is_review() -> None:
    # a plain Paragraph owns ONE ordinal; spanning >1 is an unexpected merge → flag it
    assert _verdict_for(_hit({"Paragraph"}, 5, 7), orphan_body=False) is MaskVerdict.REVIEW


def test_orphaned_body_detects_label_then_spanless_lineation() -> None:
    blocks = (
        ir.DialogueLabel(speaker="Творец", source_span=ir.SourceSpan(21, 21)),
        ir.LineatedBlock(stanzas=[[[ir.Text("body line")]]], source_span=None),
    )
    assert _orphaned_body_ordinals(blocks) == frozenset({21})


def test_orphaned_body_ignores_spanned_or_nonlineated_following() -> None:
    spanned = (   # the following lineated block kept a span → not orphaned
        ir.DialogueLabel(speaker="A", source_span=ir.SourceSpan(3, 3)),
        ir.LineatedBlock(stanzas=[[[ir.Text("x")]]], source_span=ir.SourceSpan(9, 9)),
    )
    prose = (     # the following block is a plain paragraph, not lineated → not orphaned
        ir.DialogueLabel(speaker="A", source_span=ir.SourceSpan(3, 3)),
        ir.Paragraph(inlines=[], source_span=None),
    )
    assert _orphaned_body_ordinals(spanned) == frozenset()
    assert _orphaned_body_ordinals(prose) == frozenset()


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
