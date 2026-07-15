# research-pure: reading-column geometry + a LibreOffice greedy-wrap simulator.
"""Per-line PHYSICAL signal — the keystone feature. The author pressed Enter for every line, so a
`<w:p>` boundary is noise; what he cannot fake is layout physics: at the observed reading column
a flowing-prose line WRAPS to >=2 rendered lines, while a discrete verse/litany line occupies ONE.
We approximate LibreOffice's greedy line-fill with hash-pinned Liberation Serif metrics at the
source's resolved default font size, computing `fill` and `wraps` per display line.

This is the one primitive production lacks — production ships whole paragraphs to
LibreOffice for real rendering and never needed a per-line simulator. It belongs upstream eventually
(next to `docx_render`); kept here while the artifact contract stabilises.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import assert_never

from PIL import ImageFont

from pancratius import docx_source

# The reproducible surrogate metric oracle. OOXML provides font size here, not a fully resolved
# typeface, so glyph advances always come from this explicit Liberation Serif surrogate.
_LIBERATION_SERIF = Path(__file__).resolve().parent / "vendor" / "LiberationSerif-Regular.ttf"
_LIBERATION_SHA256 = "058ea80864aef09a23f45cbec2bb5400bc3dfbdea01c3f10538a21fcb497fb74"
_PX_PER_PT = 10.0   # render at 10px/pt for stable hinting; the fill RATIO is scale-free
_FALLBACK_COLUMN_TWIPS = 5849
_FALLBACK_FONT_HALF_POINTS = 24


def _verify_font() -> Path:
    """Fail loud if the vendored oracle is missing or its bytes drift from the pin."""
    digest = hashlib.sha256(_LIBERATION_SERIF.read_bytes()).hexdigest()
    if digest != _LIBERATION_SHA256:
        raise RuntimeError(
            f"metric font {_LIBERATION_SERIF.name} hash {digest} != pinned {_LIBERATION_SHA256}; "
            "the physics oracle drifted — refusing to emit features against an unknown font")
    return _LIBERATION_SERIF


_FONT_FILE = _verify_font()


@lru_cache(maxsize=8)
def _font(size_pt: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_FONT_FILE), int(round(size_pt * _PX_PER_PT)))


class GeometryBasis(StrEnum):
    OBSERVED = "observed"
    RESEARCH_FALLBACK = "research_fallback"


class FontMetricsBasis(StrEnum):
    SURROGATE_LIBERATION_SERIF = "surrogate_liberation_serif"


@dataclass(frozen=True)
class PageGeom:
    col_pt: float          # reading-column width in points
    size_pt: float         # body font size in points
    column_basis: GeometryBasis
    font_size_basis: GeometryBasis
    font_metrics_basis: FontMetricsBasis = FontMetricsBasis.SURROGATE_LIBERATION_SERIF

    def __post_init__(self) -> None:
        if self.col_pt <= 0.0 or self.size_pt <= 0.0:
            raise ValueError("page geometry must be positive")


def page_geom(layout: docx_source.DocumentLayout) -> PageGeom:
    """Lower observations, choosing research-owned fallbacks explicitly when absent."""
    match layout.column_width:
        case docx_source.ObservedColumnWidth(width=width):
            column_twips = width.value
            column_basis = GeometryBasis.OBSERVED
        case docx_source.GeometryUnavailable():
            column_twips = _FALLBACK_COLUMN_TWIPS
            column_basis = GeometryBasis.RESEARCH_FALLBACK
        case docx_source.HeterogeneousColumnWidths(widths=widths):
            values = ", ".join(str(width.value) for width in widths)
            raise ValueError(
                f"document has heterogeneous section column widths ({values} twips); "
                "a section-scoped fill model is required"
            )
        case docx_source.PartiallyObservedColumnWidths(widths=widths):
            values = ", ".join(str(width.value) for width in widths)
            raise ValueError(
                f"document section geometry is only partially observed ({values} twips known); "
                "a document-wide fill model would overstate its provenance"
            )
        case unsupported:
            assert_never(unsupported)
    match layout.default_font_size:
        case docx_source.ObservedFontSize(half_points=half_points):
            font_size_basis = GeometryBasis.OBSERVED
        case docx_source.GeometryUnavailable():
            half_points = _FALLBACK_FONT_HALF_POINTS
            font_size_basis = GeometryBasis.RESEARCH_FALLBACK
        case unsupported:
            assert_never(unsupported)
    return PageGeom(
        col_pt=column_twips / 20.0,
        size_pt=half_points / 2.0,
        column_basis=column_basis,
        font_size_basis=font_size_basis,
    )


def _adv(text: str, size_pt: float) -> float:
    return _font(size_pt).getlength(text) / _PX_PER_PT


def wrap_lines(text: str, geom: PageGeom) -> int:
    """LibreOffice-style greedy word-wrap line count at the reading column. Splits on spaces (Word's
    only break opportunity for ordinary text); an over-long single word still takes its own line."""
    words = text.split()
    if not words:
        return 0
    col, space = geom.col_pt, _adv(" ", geom.size_pt)
    lines, cur = 1, _adv(words[0], geom.size_pt)
    for w in words[1:]:
        wlen = _adv(w, geom.size_pt)
        if cur + space + wlen <= col:
            cur += space + wlen
        else:
            lines += 1
            cur = wlen
    return lines


@dataclass(frozen=True)
class WrapStat:
    fill: float            # natural single-line advance / column width
    wraps: bool            # the line wraps to >=2 rendered lines at the reading column


def wrap_stat(text: str, geom: PageGeom) -> WrapStat:
    if not text:
        return WrapStat(0.0, False)
    fill = _adv(text, geom.size_pt) / geom.col_pt
    # If the whole line's advance fits the column (fill <= 1), greedy wrap keeps it on one
    # line — no per-word measurement needed. Only when it overflows do we run `wrap_lines`,
    # which also catches the no-break case (a single over-long token cannot wrap).
    wraps = fill > 1.0 and wrap_lines(text, geom) >= 2
    return WrapStat(fill=round(fill, 3), wraps=wraps)
