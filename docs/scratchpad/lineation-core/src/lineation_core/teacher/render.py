# research-pure: renders an authored DOCX page span to a vision composite; reads source read-only,
# writes only temp files. scratch only.
"""The vision evidence builder: a region's COMPOSITE assets are the LibreOffice-faithful render of
the authored page span the region covers — the page(s) the reader actually judges (their real line
breaks, indent, alignment, spacing), ONE image per page. The page authority is
`pancratius.docx_render` (production); only the per-page labeling and the data-URI encoding are
local. The page RENDERER is INJECTED, so labeling + encoding are unit-tested WITHOUT LibreOffice.

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


class RenderError(RuntimeError):
    """A region's vision evidence cannot be built."""


def libreoffice_pages(*, dpi: int = 140) -> PageRenderer:
    """The real page renderer: `pancratius.docx_render` (LibreOffice → PDF → PNG) over a docx slice."""
    def render(docx: Path, lo: int, hi: int, out_png: Path) -> list[Path]:
        return docx_render.render(docx, lo, hi, out_png, dpi=dpi)
    return render


def make_compositor(render_page: PageRenderer, *, docx_for: DocxFor = paths.book_docx,
                    max_span: int = 120, margin: int = 3) -> Compositor:
    """A `RenderFn` that gives each region ONE COMPOSITE asset PER PAGE of the authored `<w:p>` span
    it covers — separate page images, not one tall stack. The page renderer is injected (the
    LibreOffice seam); the docx resolver defaults to the committed source tree. `max_span`/`margin`
    (paragraphs) bound the rendered window around the votable lines — config, not baked into the
    render abstraction.

    One image PER PAGE because a vision reader's per-image token budget is FIXED (Gemini 3 caps each
    image part at ~1120 tokens and downsamples the whole image to fit): a tall 3-page stack would
    share one budget — a third of the resolution per page, illegible dense text — whereas three page
    parts each get the full budget. Grok tiles either shape at full resolution, so per-page is
    token-neutral there and strictly better for the capped readers."""
    def compose(specs: Sequence[ItemSpec]) -> dict[RegionId, tuple[EvidenceAsset, ...]]:
        return {spec.region_id: _region_assets(spec, render_page, docx_for,
                                               max_span=max_span, margin=margin) for spec in specs}
    return compose


def _region_assets(spec: ItemSpec, render_page: PageRenderer, docx_for: DocxFor, *,
                   max_span: int = 120, margin: int = 3) -> tuple[EvidenceAsset, ...]:
    """Render the authored-page WINDOW around the region's VOTABLE lines — `[min..max votable
    src_ordinal]` widened by `margin` paragraphs, clamped to the region's own mapped span, and capped
    at `max_span` source paragraphs — and return ONE asset per rendered page (the window may straddle
    a page break). NOT the full region span: `tile_regions` keeps a whole authorial run intact, so a
    region's context can be hundreds–thousands of paragraphs; rendering all of it yields a many-page,
    multi-MB image a vision model REJECTS ("unable to process input image"). The votable lines are the
    decision focus; their page layout is what the reader judges, and the orientation context beyond the
    window remains in the text listing. Unmapped context lines (no real `<w:p>` ordinal) cannot anchor a
    slice and are excluded."""
    mapped = [lid for lid in spec.region if lid.is_mapped]
    if not mapped:
        raise RenderError(f"region {spec.region_id!r}: no mapped lines to render a page from")
    book_id, lang = mapped[0].book_id, mapped[0].lang
    if any((lid.book_id, lid.lang) != (book_id, lang) for lid in mapped):   # render is a trust edge
        raise RenderError(f"region {spec.region_id!r}: mixes book/lang across lines — one region "
                          f"must be a single (book, lang) to anchor one authored page span")
    region_lo = min(lid.src_ordinal for lid in mapped)
    region_hi = max(lid.src_ordinal for lid in mapped)
    focus = [lid for lid in spec.votable if lid.is_mapped] or mapped
    vlo = min(lid.src_ordinal for lid in focus)
    vhi = max(lid.src_ordinal for lid in focus)
    if vhi - vlo > max_span:                         # the votable lines themselves are too wide for one
        raise RenderError(                           # page — FAIL LOUD, never silently render a window
            f"region {spec.region_id!r}: votable lines span {vhi - vlo} paragraphs > max_span "
            f"{max_span} — too wide for one authored page; the region should be split upstream")
    lo = max(region_lo, vlo - margin)                # hug the votable lines (+ margin): both endpoints
    hi = min(region_hi, vhi + margin)                # are always inside the rendered window
    docx = docx_for(book_id, lang)
    with tempfile.TemporaryDirectory(prefix="lc-render-") as td:
        pages = render_page(docx, lo, hi, Path(td) / "page.png")
        if not pages:
            raise RenderError(f"region {spec.region_id!r}: renderer produced no page image")
        imgs = [Image.open(p).convert("RGB") for p in pages]
        return tuple(
            EvidenceAsset(kind=AssetKind.COMPOSITE,
                          data_uri=_data_uri(_labeled(img, f"{spec.region_id} · p{i + 1}/{len(imgs)}")),
                          caption=spec.region_id)
            for i, img in enumerate(imgs))


def _labeled(page: Image.Image, label: str) -> Image.Image:
    """One page render under a thin label bar (region id + page-of-n) — the unit image the UI/panel
    inlines. Each page is its own image part, so a capped-budget reader sees it at full resolution."""
    canvas = Image.new("RGB", (page.width, _BAR_H + page.height), _BG)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, page.width - 1, _BAR_H - 1), fill=_BAR)
    draw.text((8, 8), label, fill=_BAR_TEXT)
    canvas.paste(page, (0, _BAR_H))
    return canvas


def _data_uri(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
