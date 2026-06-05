# research-pure: reading-column geometry + a LibreOffice greedy-wrap simulator. Reads the docx
# XML (page geometry) and PIL font metrics only — no scratchpad, no production dependency.
"""Per-line PHYSICAL signal — the keystone feature. The author pressed Enter for every line, so a
`<w:p>` boundary is noise; what he cannot fake is layout physics: at the book's real reading column
a flowing-prose line WRAPS to >=2 rendered lines, while a discrete verse/litany line occupies ONE.
We reproduce LibreOffice's greedy line-fill (LiberationSerif at docDefaults size into the section
column) to compute, per display line, `fill` (single-line advance / column width) and `wraps`.

Vendored from the proven `intent-classifier/scripts/wrap.py` core (its `__main__` validation harness
dropped). This is the ONE primitive production lacks — production ships whole paragraphs to
LibreOffice for real rendering and never needed a per-line simulator. It belongs upstream eventually
(next to `docx_render`); kept here while the artifact contract stabilises.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_LIBERATION_SERIF = ("/Applications/LibreOffice.app/Contents/Resources/fonts/truetype/"
                     "LiberationSerif-Regular.ttf")
_PX_PER_PT = 10.0   # render at 10px/pt for stable hinting; the fill RATIO is scale-free


@lru_cache(maxsize=8)
def _font(size_pt: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_LIBERATION_SERIF, int(round(size_pt * _PX_PER_PT)))


@dataclass(frozen=True)
class PageGeom:
    col_pt: float          # reading-column width in points
    size_pt: float         # body font size in points


def page_geom(docx: Path) -> PageGeom:
    """Reading-column width + body font size from the section `sectPr` + `docDefaults`. Falls back
    to the corpus norm (5849-twip column, 12pt) when a book lacks explicit geometry."""
    with zipfile.ZipFile(docx) as zf:
        doc = ET.fromstring(zf.read("word/document.xml"))
        try:
            styles = ET.fromstring(zf.read("word/styles.xml"))
        except KeyError:
            styles = None
    col_tw = 5849.0
    for sect in doc.findall(f".//{_W}sectPr"):
        pgsz, pgmar = sect.find(f"{_W}pgSz"), sect.find(f"{_W}pgMar")
        if pgsz is not None and pgmar is not None:
            width = float(pgsz.get(f"{_W}w", 8400))
            margin_l = float(pgmar.get(f"{_W}left", pgmar.get(f"{_W}start", 1701)))
            margin_r = float(pgmar.get(f"{_W}right", pgmar.get(f"{_W}end", 850)))
            col_tw = width - margin_l - margin_r
            break
    size_pt = 12.0
    if styles is not None:
        sz = styles.find(f"{_W}docDefaults/{_W}rPrDefault/{_W}rPr/{_W}sz")
        if sz is not None and sz.get(f"{_W}val"):
            size_pt = float(sz.get(f"{_W}val")) / 2.0  # half-points -> points
    return PageGeom(col_pt=col_tw / 20.0, size_pt=size_pt)


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
    t = " ".join(text.split())
    if not t:
        return WrapStat(0.0, False)
    fill = _adv(t, geom.size_pt) / geom.col_pt
    # If the whole line's advance fits the column (fill <= 1), greedy wrap keeps it on one
    # line — no per-word measurement needed. Only when it overflows do we run `wrap_lines`,
    # which also catches the no-break case (a single over-long token cannot wrap).
    wraps = fill > 1.0 and wrap_lines(t, geom) >= 2
    return WrapStat(fill=round(fill, 3), wraps=wraps)
