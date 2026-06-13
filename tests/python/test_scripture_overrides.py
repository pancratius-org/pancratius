"""Unmarked-canon scripture pins: the sidecar rails and the wrap pass honoring them.

The sidecar (`scripture.<lang>.json`) pins source paragraphs adjudicated as quotations
of an external canonical source with no structural marker. Rails are never advisory:
a drifted text, a stale ordinal, or a missing source name FAILS the load; a pin that
no longer lands on a top-level prose paragraph FAILS the pass.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from pancratius import ir
from pancratius.lineation_overrides import paragraph_sha
from pancratius.passes.pipeline import Context, run
from pancratius.passes.register import wrap_scripture
from pancratius.scripture_overrides import load_overrides, overrides_path

pandoc_required = pytest.mark.skipif(
    shutil.which("pandoc") is None,
    reason="pandoc is required for importer-backed DOCX paths",
)


def _write_docx(path: Path, paragraphs: list[str]) -> None:
    from docx import Document

    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    doc.save(str(path))


def _write_sidecar(docx: Path, entries: dict[int, dict[str, str]]) -> Path:
    path = overrides_path(docx)
    path.write_text(json.dumps({str(k): v for k, v in entries.items()}, ensure_ascii=False))
    return path


# --- the loader rails ---------------------------------------------------------------------


def test_missing_sidecar_means_no_pins(tmp_path: Path) -> None:
    docx = tmp_path / "ru.docx"
    _write_docx(docx, ["Текст."])
    assert load_overrides(docx) == {}


def test_sidecar_path_is_language_keyed(tmp_path: Path) -> None:
    assert overrides_path(tmp_path / "ru.docx").name == "scripture.ru.json"


def test_valid_pin_loads_by_ordinal(tmp_path: Path) -> None:
    docx = tmp_path / "ru.docx"
    _write_docx(docx, ["Свой голос книги.", "Се, гряду скоро."])
    _write_sidecar(docx, {1: {"source": "Откр 22:7", "text_sha": paragraph_sha("Се, гряду скоро.")}})
    assert load_overrides(docx) == {1: "Откр 22:7"}


def test_text_drift_fails_the_load(tmp_path: Path) -> None:
    docx = tmp_path / "ru.docx"
    _write_docx(docx, ["Изменённый текст."])
    _write_sidecar(docx, {0: {"source": "Откр 22:7", "text_sha": paragraph_sha("Се, гряду скоро.")}})
    with pytest.raises(ValueError, match="drifted"):
        load_overrides(docx)


def test_stale_ordinal_fails_the_load(tmp_path: Path) -> None:
    docx = tmp_path / "ru.docx"
    _write_docx(docx, ["Один абзац."])
    _write_sidecar(docx, {99: {"source": "Откр 22:7", "text_sha": paragraph_sha("что-то")}})
    with pytest.raises(ValueError, match="no source paragraph"):
        load_overrides(docx)


def test_missing_source_name_fails_the_load(tmp_path: Path) -> None:
    docx = tmp_path / "ru.docx"
    _write_docx(docx, ["Се, гряду скоро."])
    _write_sidecar(docx, {0: {"source": "", "text_sha": paragraph_sha("Се, гряду скоро.")}})
    with pytest.raises(ValueError, match="canonical source"):
        load_overrides(docx)


# --- the wrap pass honors pins ---------------------------------------------------------------


def _para(text: str, ord_: int) -> ir.Paragraph:
    return ir.Paragraph(inlines=[ir.Text(text)], source_span=ir.SourceSpan(ord_, ord_))


def test_pinned_paragraph_wraps_as_scripture() -> None:
    blocks: list[ir.Block] = [
        _para("Свой голос книги.", 1),
        _para("Се, гряду скоро; держи, что имеешь, дабы кто не восхитил венца твоего.", 2),
        _para("Дальше — комментарий книги.", 3),
    ]
    out = wrap_scripture(blocks, pinned={2: "Откр 3:11"})
    assert [type(b).__name__ for b in out] == ["Paragraph", "QuoteBlock", "Paragraph"]
    quote = out[1]
    assert isinstance(quote, ir.QuoteBlock)
    assert quote.register is ir.Register.SCRIPTURE
    assert quote.source_span == ir.SourceSpan(2, 2)


def test_adjacent_pins_form_one_run_across_a_blank() -> None:
    blocks: list[ir.Block] = [
        _para("11 Побеждающий наследует всё.", 1),
        ir.Paragraph(inlines=[], facts=ir.SourceFacts(empty=True)),
        _para("12 И увидел я новое небо и новую землю.", 3),
    ]
    out = wrap_scripture(blocks, pinned={1: "Откр 21:7", 3: "Откр 21:1"})
    assert [type(b).__name__ for b in out] == ["QuoteBlock"]
    quote = out[0]
    assert isinstance(quote, ir.QuoteBlock)
    assert quote.source_span == ir.SourceSpan(1, 3)


def test_unclaimed_pin_fails_loud() -> None:
    blocks: list[ir.Block] = [_para("Свой голос книги.", 1)]
    with pytest.raises(ValueError, match="claim no top-level prose paragraph"):
        wrap_scripture(blocks, pinned={7: "Откр 3:11"})


def test_pin_inside_lineated_block_fails_loud() -> None:
    # A pin adjudicated on prose must not silently dissolve when the paragraph
    # later folds into a lineated run.
    line = ir.Line(inlines=[ir.Text("Се, гряду скоро.")], span=ir.SourceSpan(2, 2))
    blocks: list[ir.Block] = [
        ir.LineatedBlock(stanzas=[[line]], source_span=ir.SourceSpan(2, 2)),
    ]
    with pytest.raises(ValueError, match="claim no top-level prose paragraph"):
        wrap_scripture(blocks, pinned={2: "Откр 22:7"})


def test_no_pins_is_the_shipped_rule_behavior() -> None:
    blocks: list[ir.Block] = [_para("Свой голос книги.", 1)]
    assert wrap_scripture(blocks, pinned={}) == wrap_scripture(blocks)


# --- end to end: importer pipeline + per-ordinal surfaces -------------------------------------


@pandoc_required
def test_pipeline_wraps_pinned_ordinal_and_lineation_decisions_hold(tmp_path: Path) -> None:
    from pancratius.docx_inspect import lineation_decisions

    docx = tmp_path / "ru.docx"
    paragraphs = [
        "Книга говорит своим голосом, длинно и спокойно, не цитируя никого дословно.",
        "Се, гряду скоро, и возмездие Моё со Мною, чтобы воздать каждому по делам его.",
        "И дальше книга продолжает собственное рассуждение о времени и свете.",
    ]
    _write_docx(docx, paragraphs)
    before = lineation_decisions(docx)

    _write_sidecar(docx, {1: {"source": "Откр 22:12", "text_sha": paragraph_sha(paragraphs[1])}})

    from pancratius import docx_adapter
    from pancratius.scripture_overrides import load_overrides as load_pins

    doc = docx_adapter.adapt(docx, tmp_path / "media", [])
    doc = run(doc, Context(lang="ru", scripture_overrides=load_pins(docx)))
    quotes = [b for b in doc.blocks if isinstance(b, ir.QuoteBlock)]
    assert len(quotes) == 1
    assert quotes[0].register is ir.Register.SCRIPTURE
    assert quotes[0].source_span == ir.SourceSpan(1, 1)

    # The per-ordinal lineation surface keeps its verdicts through the wrapper.
    after = lineation_decisions(docx)
    assert after == before
