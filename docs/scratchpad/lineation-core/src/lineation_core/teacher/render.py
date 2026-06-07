# research-pure: renders an authored DOCX page span to a vision composite; reads source read-only,
# writes only temp files. scratch only.
"""The vision evidence builder: a region's COMPOSITE asset is the LibreOffice-faithful render of
the authored page span the region covers — the page the reader actually judges (its real line
breaks, indent, alignment, spacing). The page authority is `pancratius.docx_render` (production);
only the composition (stacking a multi-page render under a region label) and the data-URI encoding
are local. The page RENDERER is INJECTED, so composition + encoding are unit-tested WITHOUT
LibreOffice.

NO synthetic prose/lineated candidate tiles: a candidate is a HYPOTHESIS render whose fidelity
confounds the gate (a `<w:br>`-glued prose candidate made readers vote on a merged shape), and it
would drag in the site-CSS / headless-browser harness this package deliberately does not depend on.
The authored page is the privileged signal; the reader compares it against the keyed line listing
already carried in the task item."""
from __future__ import annotations

import base64
import io
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from PIL import Image, ImageDraw

from pancratius import docx_render

from .. import paths
from ..identity import BookId
from .tasks import AssetKind, EvidenceAsset, ItemSpec, RegionId

# (docx, lo, hi, out_png) → the rendered page PNG path(s) in document order. The ONLY LibreOffice
# seam: the real one wraps `pancratius.docx_render`; a test passes a stub that writes fixture PNGs.
type PageRenderer = Callable[[Path, int, int, Path], list[Path]]
type DocxFor = Callable[[BookId, str], Path]               # (book_id, lang) → source docx
type Compositor = Callable[[Sequence[ItemSpec]], dict[RegionId, tuple[EvidenceAsset, ...]]]

_BG = (251, 250, 246)        # cream margin around the page
_BAR = (34, 34, 40)          # the region-label bar
_BAR_TEXT = (245, 242, 232)
_BAR_H = 28
_PAGE_GAP = 6                # between stacked pages of a multi-page region


class RenderError(RuntimeError):
    """A region's vision evidence cannot be built."""


def libreoffice_pages(*, dpi: int = 140) -> PageRenderer:
    """The real page renderer: `pancratius.docx_render` (LibreOffice → PDF → PNG) over a docx slice."""
    def render(docx: Path, lo: int, hi: int, out_png: Path) -> list[Path]:
        return docx_render.render(docx, lo, hi, out_png, dpi=dpi)
    return render


def make_compositor(render_page: PageRenderer, *, docx_for: DocxFor = paths.book_docx) -> Compositor:
    """A `RenderFn` that gives each region one COMPOSITE asset — the authored page render of the
    region's source `<w:p>` span. The page renderer is injected (the LibreOffice seam); the docx
    resolver defaults to the committed source tree."""
    def compose(specs: Sequence[ItemSpec]) -> dict[RegionId, tuple[EvidenceAsset, ...]]:
        return {spec.region_id: (_region_asset(spec, render_page, docx_for),) for spec in specs}
    return compose


def _region_asset(spec: ItemSpec, render_page: PageRenderer, docx_for: DocxFor) -> EvidenceAsset:
    """Render the authored page span [min..max src_ordinal] of the region's MAPPED lines. Unmapped
    context lines (no real `<w:p>` ordinal) cannot anchor a slice and are excluded from the span."""
    mapped = [lid for lid in spec.region if lid.is_mapped]
    if not mapped:
        raise RenderError(f"region {spec.region_id!r}: no mapped lines to render a page from")
    book_id, lang = mapped[0].book_id, mapped[0].lang
    lo = min(lid.src_ordinal for lid in mapped)
    hi = max(lid.src_ordinal for lid in mapped)
    docx = docx_for(book_id, lang)
    with tempfile.TemporaryDirectory(prefix="lc-render-") as td:
        pages = render_page(docx, lo, hi, Path(td) / "page.png")
        if not pages:
            raise RenderError(f"region {spec.region_id!r}: renderer produced no page image")
        composite = _stack([Image.open(p).convert("RGB") for p in pages], label=spec.region_id)
        return EvidenceAsset(kind=AssetKind.COMPOSITE, data_uri=_data_uri(composite),
                             caption=spec.region_id)


def _stack(pages: list[Image.Image], *, label: str) -> Image.Image:
    """Stack a region's page renders vertically (a region can straddle a page break) under a thin
    label bar — one image the UI/panel inlines."""
    width = max(p.width for p in pages)
    body_h = sum(p.height for p in pages) + _PAGE_GAP * (len(pages) - 1)
    canvas = Image.new("RGB", (width, _BAR_H + body_h), _BG)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, width - 1, _BAR_H - 1), fill=_BAR)
    draw.text((8, 8), label, fill=_BAR_TEXT)
    y = _BAR_H
    for page in pages:
        canvas.paste(page, (0, y))
        y += page.height + _PAGE_GAP
    return canvas


def _data_uri(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
