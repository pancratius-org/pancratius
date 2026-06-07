# research-pure: render.py composes the authored page render into a COMPOSITE data-URI.
"""Locks the vision evidence builder WITHOUT LibreOffice: the page renderer is stubbed, so these
prove the composition + encoding + the src_ordinal span logic, not the LibreOffice render itself.
The slice span comes from the region's MAPPED lines only (unmapped context cannot anchor a `<w:p>`
range); a multi-page region stacks; an empty/unmappable region fails LOUD (never a silent text task)."""
from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from lineation_core.identity import LineId
from lineation_core.teacher import render
from lineation_core.teacher.tasks import AssetKind, ItemSpec, Modality


def _stub_renderer(*, n_pages: int = 1, size: tuple[int, int] = (100, 40)):
    """A PageRenderer that writes `n_pages` solid PNGs beside `out_png` and records its args."""
    calls: list[tuple[Path, int, int]] = []

    def render_page(docx: Path, lo: int, hi: int, out_png: Path) -> list[Path]:
        calls.append((docx, lo, hi))
        out: list[Path] = []
        for i in range(n_pages):
            p = out_png.parent / f"page-{i}.png"
            Image.new("RGB", size, (200, 30, 30)).save(p)
            out.append(p)
        return out

    render_page.calls = calls  # type: ignore[attr-defined]
    return render_page


def _docx_for_stub(captured: list[tuple[str, str]]):
    def docx_for(book_id: str, lang: str) -> Path:
        captured.append((book_id, lang))
        return Path(f"/nonexistent/{book_id}/{lang}.docx")   # never opened (renderer is stubbed)

    return docx_for


def _spec(region_id: str, ids: list[LineId], *, votable: list[LineId] | None = None) -> ItemSpec:
    return ItemSpec(region_id=region_id, region=tuple(ids),
                    votable=frozenset(votable if votable is not None else ids),
                    modality=Modality.VISION)


def _decode(data_uri: str) -> Image.Image:
    assert data_uri.startswith("data:image/png;base64,")
    return Image.open(io.BytesIO(base64.b64decode(data_uri.split(",", 1)[1])))


def test_composite_is_a_labeled_png_data_uri():
    spec = _spec("b57-r0", [LineId.mapped("ru", "57", o, 0) for o in (10, 11, 12)])
    compose = render.make_compositor(_stub_renderer(size=(120, 50)),
                                     docx_for=_docx_for_stub([]))
    assets = compose([spec])
    (asset,) = assets["b57-r0"]
    assert asset.kind is AssetKind.COMPOSITE and asset.caption == "b57-r0"
    img = _decode(asset.data_uri)
    assert img.format == "PNG"
    assert img.width == 120 and img.height == render._BAR_H + 50   # one page under the label bar


def test_span_is_min_max_of_mapped_lines_only():
    # mapped ordinals 10,14,12 + one unmapped context line — the slice must be [10, 14].
    ids = [LineId.mapped("ru", "57", 10, 0), LineId.unmapped("ru", "57", 3, 0),
           LineId.mapped("ru", "57", 14, 0), LineId.mapped("ru", "57", 12, 0)]
    spec = _spec("b57-r0", ids, votable=[ids[0], ids[2], ids[3]])
    rp = _stub_renderer()
    captured: list[tuple[str, str]] = []
    render.make_compositor(rp, docx_for=_docx_for_stub(captured))([spec])
    assert rp.calls == [(Path("/nonexistent/57/ru.docx"), 10, 14)]
    assert captured == [("57", "ru")]


def test_multipage_region_stacks_vertically():
    spec = _spec("b57-r0", [LineId.mapped("ru", "57", o, 0) for o in (10, 11)])
    compose = render.make_compositor(_stub_renderer(n_pages=2, size=(80, 30)),
                                     docx_for=_docx_for_stub([]))
    img = _decode(compose([spec])["b57-r0"][0].data_uri)
    assert img.width == 80
    assert img.height == render._BAR_H + 30 + render._PAGE_GAP + 30


def test_region_with_no_mapped_lines_fails_loud():
    spec = _spec("b57-r0", [LineId.unmapped("ru", "57", 1, 0), LineId.unmapped("ru", "57", 2, 0)],
                 votable=[])
    compose = render.make_compositor(_stub_renderer(), docx_for=_docx_for_stub([]))
    with pytest.raises(render.RenderError, match="no mapped lines"):
        compose([spec])


def test_renderer_producing_no_pages_fails_loud():
    spec = _spec("b57-r0", [LineId.mapped("ru", "57", 10, 0)])

    def empty(docx: Path, lo: int, hi: int, out_png: Path) -> list[Path]:
        return []

    compose = render.make_compositor(empty, docx_for=_docx_for_stub([]))
    with pytest.raises(render.RenderError, match="no page image"):
        compose([spec])
