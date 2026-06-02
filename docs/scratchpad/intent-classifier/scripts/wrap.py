# research-pure: reads src/content DOCX read-only; writes nothing there.
"""Wrapping simulator — the keystone PHYSICAL signal.

The author pressed Enter for every line, so a `<w:p>` boundary is noise. But the
ONE thing he cannot fake is layout physics: at the book's real reading column a
flowing-prose paragraph he typed as a block WRAPS to >=2 lines, while a discrete
line he put on its own (verse / litany / list) occupies ONE line. This module
reproduces LibreOffice's greedy line-fill so we can compute, per paragraph:

  * ``lines``      — predicted rendered line count at the real reading column;
  * ``fill``       — natural single-line advance / column width (a smooth 0..N
                     "how full" ratio; >1 means it must wrap);
  * ``wraps``      — lines >= 2.

LibreOffice renders the DOCX body in its default serif (LiberationSerif, metric-
compatible with Times New Roman) at the docDefaults size (12pt across this corpus)
into the column ``pgSz.w - pgMar.left - pgMar.right`` from ``sectPr``. We measure
glyph advances with that exact font via PIL, so the prediction is validated against
the LibreOffice render itself (see ``__main__``), not a char-count guess.
"""
from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_LIBERATION_SERIF = (
    "/Applications/LibreOffice.app/Contents/Resources/fonts/truetype/"
    "LiberationSerif-Regular.ttf"
)
# Render at 10px per pt so glyph hinting is stable; the fill RATIO is scale-free.
_PX_PER_PT = 10.0


@lru_cache(maxsize=8)
def _font(size_pt: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(_LIBERATION_SERIF, int(round(size_pt * _PX_PER_PT)))


@dataclass(frozen=True)
class PageGeom:
    col_pt: float          # reading-column width in points
    size_pt: float         # body font size in points


def page_geom(docx: Path) -> PageGeom:
    """Reading column width and body font size from ``sectPr`` + ``docDefaults``.

    The column is taken from the LAST body ``sectPr`` (the document section); if a
    book lacks explicit page geometry we fall back to the corpus norm (8400 twips
    wide, 1701/850 twip margins -> 5849 twip column) and 12pt.
    """
    with zipfile.ZipFile(docx) as zf:
        doc = ET.fromstring(zf.read("word/document.xml"))
        try:
            styles = ET.fromstring(zf.read("word/styles.xml"))
        except KeyError:
            styles = None
    sects = doc.findall(f".//{W}sectPr")
    col_tw = 5849.0
    for sect in sects:
        pgsz, pgmar = sect.find(f"{W}pgSz"), sect.find(f"{W}pgMar")
        if pgsz is not None and pgmar is not None:
            width = float(pgsz.get(f"{W}w", 8400))
            margin_l = float(pgmar.get(f"{W}left", pgmar.get(f"{W}start", 1701)))
            margin_r = float(pgmar.get(f"{W}right", pgmar.get(f"{W}end", 850)))
            col_tw = width - margin_l - margin_r
            break
    size_pt = 12.0
    if styles is not None:
        sz = styles.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr/{W}sz")
        if sz is not None and sz.get(f"{W}val"):
            size_pt = float(sz.get(f"{W}val")) / 2.0  # half-points -> points
    return PageGeom(col_pt=col_tw / 20.0, size_pt=size_pt)


def _adv(text: str, size_pt: float) -> float:
    """Natural single-line advance width of ``text`` in points."""
    return _font(size_pt).getlength(text) / _PX_PER_PT


def wrap_lines(text: str, geom: PageGeom) -> int:
    """LibreOffice-style greedy word-wrap line count at the reading column.

    Splits on spaces (the only break opportunity Word uses for ordinary text);
    a single word longer than the column still occupies its own line(s)."""
    words = text.split()
    if not words:
        return 0
    col = geom.col_pt
    space = _adv(" ", geom.size_pt)
    lines = 1
    cur = _adv(words[0], geom.size_pt)
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
    text_chars: int
    lines: int
    fill: float            # natural single-line advance / column width
    wraps: bool


def wrap_stat(text: str, geom: PageGeom) -> WrapStat:
    t = " ".join(text.split())
    if not t:
        return WrapStat(0, 0, 0.0, False)
    fill = _adv(t, geom.size_pt) / geom.col_pt
    lines = wrap_lines(t, geom)
    return WrapStat(text_chars=len(t), lines=lines, fill=round(fill, 3), wraps=lines >= 2)


# ---------------------------------------------------------------------------
# validation harness: predicted line counts for a book range, to eyeball vs render
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
    from pancratius import docx_inspect as di

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--book", type=int, required=True)
    ap.add_argument("--range", required=True, help="LO:HI inclusive paragraph indices")
    args = ap.parse_args(argv)

    docx = di._book_docx(args.book)
    geom = page_geom(docx)
    rows = di.read_rows(docx)
    lo, hi = (int(x) for x in args.range.split(":"))
    print(f"# book #{args.book}  col={geom.col_pt:.1f}pt  size={geom.size_pt}pt")
    print(f"{'idx':>4} {'lines':>5} {'fill':>5}  text")
    for r in rows:
        if lo <= r.index <= hi and not r.empty:
            s = wrap_stat(r.text, geom)
            print(f"{r.index:>4} {s.lines:>5} {s.fill:>5.2f}  {r.text[:70]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
