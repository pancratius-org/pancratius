"""Editorial lineation corrections: the sidecar rails and the importer's prose override.

The sidecar (`lineation.<lang>.json`) pins a human-adjudicated register per source paragraph.
Rails are never advisory: a drifted text, a stale ordinal, or an unknown register FAILS the
load. In the ladder, a prose-pinned paragraph never enters a lineation unit; the `lineated`
direction is not yet appliable and must fail loud rather than be silently ignored.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import pancratius.ir.normalize as normalize
from pancratius import ir
from pancratius.lineation_overrides import load_overrides, overrides_path, paragraph_sha

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


def test_missing_sidecar_means_no_overrides(tmp_path: Path) -> None:
    docx = tmp_path / "ru.docx"
    _write_docx(docx, ["Текст."])
    assert load_overrides(docx) == {}


def test_sidecar_path_is_language_keyed(tmp_path: Path) -> None:
    assert overrides_path(tmp_path / "en.docx").name == "lineation.en.json"


def test_valid_override_loads_by_ordinal(tmp_path: Path) -> None:
    docx = tmp_path / "ru.docx"
    _write_docx(docx, ["Первый абзац.", "Пиши. Дальше."])
    _write_sidecar(docx, {1: {"register": "prose", "text_sha": paragraph_sha("Пиши. Дальше.")}})
    assert load_overrides(docx) == {1: "prose"}


def test_text_drift_fails_the_load(tmp_path: Path) -> None:
    docx = tmp_path / "ru.docx"
    _write_docx(docx, ["Первый абзац.", "Изменённый текст."])
    _write_sidecar(docx, {1: {"register": "prose", "text_sha": paragraph_sha("Пиши. Дальше.")}})
    with pytest.raises(ValueError, match="drifted"):
        load_overrides(docx)


def test_stale_ordinal_fails_the_load(tmp_path: Path) -> None:
    docx = tmp_path / "ru.docx"
    _write_docx(docx, ["Один абзац."])
    _write_sidecar(docx, {99: {"register": "prose", "text_sha": paragraph_sha("что-то")}})
    with pytest.raises(ValueError, match="no source paragraph"):
        load_overrides(docx)


def test_unknown_register_fails_the_load(tmp_path: Path) -> None:
    docx = tmp_path / "ru.docx"
    _write_docx(docx, ["Один абзац."])
    _write_sidecar(docx, {0: {"register": "verse", "text_sha": paragraph_sha("Один абзац.")}})
    with pytest.raises(ValueError, match="register"):
        load_overrides(docx)


# --- the ladder honors the override --------------------------------------------------------


def _row(text: str, ordinal: int) -> ir.Paragraph:
    return ir.Paragraph(inlines=[ir.Text(text)],
                        source_span=ir.SourceSpan(start=ordinal, end=ordinal))


def _short_run() -> list[ir.Block]:
    # A heading-opened run of short source rows the ladder folds (the inference path).
    return [
        ir.Heading(level=2, inlines=[ir.Text("Молитва")]),
        _row("Ты — Мой храм.", 10),
        _row("Я — твой свет.", 11),
        _row("Мы — одно.", 12),
        _row("Пиши. Дальше.", 13),
    ]


def test_precondition_the_run_folds_without_overrides() -> None:
    out = normalize.lineated_blocks(_short_run())
    assert any(isinstance(b, ir.LineatedBlock) for b in out)
    (block,) = [b for b in out if isinstance(b, ir.LineatedBlock)]
    assert block.source_span == ir.SourceSpan(start=10, end=13)


def test_prose_override_excludes_the_row_and_keeps_the_rest_folding() -> None:
    out = normalize.lineated_blocks(_short_run(), lineation_overrides={13: "prose"})
    (block,) = [b for b in out if isinstance(b, ir.LineatedBlock)]
    assert block.source_span == ir.SourceSpan(start=10, end=12)   # 13 stayed out
    tail = out[-1]
    assert isinstance(tail, ir.Paragraph)
    assert normalize.inline_plain(tail.inlines) == "Пиши. Дальше."


def test_prose_override_mid_run_splits_the_unit() -> None:
    out = normalize.lineated_blocks(_short_run(), lineation_overrides={11: "prose"})
    for b in out:
        if isinstance(b, ir.LineatedBlock) and b.source_span is not None:
            assert not b.source_span.start <= 11 <= b.source_span.end
    overridden = [b for b in out if isinstance(b, ir.Paragraph)
                  and normalize.inline_plain(b.inlines) == "Я — твой свет."]
    assert len(overridden) == 1


def test_lineated_override_is_not_silently_ignored() -> None:
    with pytest.raises(ValueError, match="cannot force"):
        normalize.lineated_blocks(_short_run(), lineation_overrides={11: "lineated"})


# --- end to end: the production verdict reader reflects the correction ---------------------


@pandoc_required
def test_lineation_decisions_honor_the_sidecar(tmp_path: Path) -> None:
    from pancratius.docx_inspect import lineation_decisions

    docx = tmp_path / "ru.docx"
    paragraphs = [
        "Молитва",                # 0 — short opener; folds with the run below
        "Ты — Мой храм.",         # 1
        "Я — твой свет.",         # 2
        "Мы — одно.",             # 3
        "Пиши. Дальше.",          # 4 — the corrected line
    ]
    _write_docx(docx, paragraphs)
    before = lineation_decisions(docx)
    assert before.get(4) is True, "precondition: the importer lineates row 4 in this fixture"

    _write_sidecar(docx, {4: {"register": "prose", "text_sha": paragraph_sha("Пиши. Дальше.")}})
    after = lineation_decisions(docx)
    assert after.get(4) is False
    assert all(after.get(o) is True for o in (1, 2, 3))
