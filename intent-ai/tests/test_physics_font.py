# research-pure: guards the metric oracle — the vendored font the physics simulator measures with.
"""The `fill`/`wraps` signal is only as faithful as Liberation Serif's advances, so two things must
hold: the vendored bytes match the pin (import would already fail loud otherwise — asserted plainly
here for a readable message), and the pin still matches the LibreOffice the corpus is laid out in.
The second is the drift guard: a LibreOffice upgrade that ships a re-metricked Liberation fails this
test instead of silently shifting every label. Skipped where that bundle is absent (CI / Linux)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from intent_ai import physics

from pancratius import docx_source

# Where LibreOffice ships its default serif on macOS — the renderer the gold corpus was produced
# against. Absent off-macOS, where the drift check simply can't run.
_LIBREOFFICE_SERIF = Path(
    "/Applications/LibreOffice.app/Contents/Resources/fonts/truetype/LiberationSerif-Regular.ttf")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_vendored_font_matches_pin() -> None:
    assert physics._LIBERATION_SERIF.is_file(), "vendored metric font is missing"
    assert _sha256(physics._LIBERATION_SERIF) == physics._LIBERATION_SHA256


def test_geometry_fallback_is_research_policy_not_source_fact() -> None:
    missing = physics.page_geom(docx_source.DocumentLayout())
    assert missing.col_pt == physics._FALLBACK_COLUMN_TWIPS / 20.0
    assert missing.size_pt == physics._FALLBACK_FONT_HALF_POINTS / 2.0

    observed = physics.page_geom(docx_source.DocumentLayout(
        column_width=docx_source.ObservedColumnWidth(docx_source.Twips(6000)),
        default_font_size=docx_source.ObservedFontSize(22),
    ))
    assert observed.col_pt == 300.0 and observed.size_pt == 11.0


def test_heterogeneous_sections_refuse_a_false_document_wide_fill_model() -> None:
    layout = docx_source.DocumentLayout(
        column_width=docx_source.HeterogeneousColumnWidths(
            (docx_source.Twips(5000), docx_source.Twips(6000))
        )
    )
    with pytest.raises(ValueError, match="section-scoped fill model"):
        physics.page_geom(layout)

    partial = docx_source.DocumentLayout(
        column_width=docx_source.PartiallyObservedColumnWidths((docx_source.Twips(5000),))
    )
    with pytest.raises(ValueError, match="overstate its provenance"):
        physics.page_geom(partial)


@pytest.mark.corpus_source
def test_pin_matches_libreoffice_bundle() -> None:
    if not _LIBREOFFICE_SERIF.is_file():
        pytest.skip("LibreOffice not installed at the macOS bundle path")
    assert _sha256(_LIBREOFFICE_SERIF) == physics._LIBERATION_SHA256, (
        "vendored metric font has drifted from the installed LibreOffice — re-pin only after "
        "confirming the gold corpus is re-rendered against the new Liberation metrics")
