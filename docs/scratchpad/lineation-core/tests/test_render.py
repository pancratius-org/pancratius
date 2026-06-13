# research-pure: render.py turns the authored page render into one COMPOSITE data-URI per page.
"""Locks the vision evidence builder WITHOUT LibreOffice: the page renderer is stubbed, so these
prove the per-page labeling + encoding + the src_ordinal span logic, not the LibreOffice render itself.
The slice span comes from the region's MAPPED lines only (unmapped context cannot anchor a `<w:p>`
range); a multi-page region yields one image per page; an empty/unmappable region fails LOUD (never a
silent text task)."""
from __future__ import annotations

import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from lineation_core.identity import LineId
from lineation_core.teacher import render
from lineation_core.teacher.tasks import AssetKind, ItemSpec


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


def _NO_BREAKS(_docx: Path) -> frozenset[int]:
    """A boundary source for a docx with no page breaks (the stub paths are never parsed)."""
    return frozenset()


def _spec(region_id: str, ids: list[LineId], *, votable: list[LineId] | None = None) -> ItemSpec:
    return ItemSpec(region_id=region_id, region=tuple(ids),
                    votable=frozenset(votable if votable is not None else ids))


def _decode(data_uri: str) -> Image.Image:
    assert data_uri.startswith("data:image/png;base64,")
    return Image.open(io.BytesIO(base64.b64decode(data_uri.split(",", 1)[1])))


def test_composite_is_a_labeled_png_data_uri():
    spec = _spec("b57-r0", [LineId.mapped("ru", "57", o, 0) for o in (10, 11, 12)])
    compose = render.make_compositor(_stub_renderer(size=(120, 50)),
                                     docx_for=_docx_for_stub([]), page_boundaries=_NO_BREAKS)
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
    render.make_compositor(rp, docx_for=_docx_for_stub(captured), page_boundaries=_NO_BREAKS)([spec])
    assert rp.calls == [(Path("/nonexistent/57/ru.docx"), 10, 14)]
    assert captured == [("57", "ru")]


def test_render_window_hugs_the_votable_lines_not_the_whole_region_span():
    # tile_regions keeps a whole authorial run as context, so a region's span can be huge (1000..3000)
    # while its VOTABLE lines are a tight cluster (2010..2014). The rendered slice must hug the votable
    # window (+ margin) — rendering the 2000-line span would be a many-page, oversized image a vision
    # model rejects ("unable to process input image").
    context = [LineId.mapped("ru", "57", o, 0) for o in (1000, 2010, 2012, 2014, 3000)]
    votable = [LineId.mapped("ru", "57", o, 0) for o in (2010, 2014)]
    rp = _stub_renderer()
    render.make_compositor(rp, docx_for=_docx_for_stub([]), page_boundaries=_NO_BREAKS)([_spec("b57-r0", context, votable=votable)])
    _, lo, hi = rp.calls[0]
    assert (lo, hi) == (2007, 2017)                  # votable [2010..2014] ± margin 3, NOT [1000..3000]


def test_render_fails_loud_when_votable_lines_exceed_one_page():
    # if the votable lines themselves span more than max_span, a single bounded page cannot show them
    # all — FAIL LOUD rather than silently render a window that excludes endpoints (wrong evidence).
    votable = [LineId.mapped("ru", "57", 1000, 0), LineId.mapped("ru", "57", 1400, 0)]   # 400 > 120
    rp = _stub_renderer()
    compose = render.make_compositor(rp, docx_for=_docx_for_stub([]), page_boundaries=_NO_BREAKS)
    with pytest.raises(render.RenderError, match="too wide for one authored page"):
        compose([_spec("b57-r0", votable, votable=votable)])
    assert rp.calls == []                            # never rendered a misleading window


def test_multipage_region_yields_one_image_per_page():
    # a region straddling a page break gives ONE labeled image PER PAGE (separate parts), not one tall
    # stack — so a per-image-budget vision reader sees each page at full resolution.
    spec = _spec("b57-r0", [LineId.mapped("ru", "57", o, 0) for o in (10, 11)])
    compose = render.make_compositor(_stub_renderer(n_pages=2, size=(80, 30)),
                                     docx_for=_docx_for_stub([]), page_boundaries=_NO_BREAKS)
    assets = compose([spec])["b57-r0"]
    assert len(assets) == 2
    for a in assets:
        assert a.kind is AssetKind.COMPOSITE and a.caption == "b57-r0"
        img = _decode(a.data_uri)
        assert img.width == 80 and img.height == render._BAR_H + 30   # one page under its own bar


def test_region_with_no_mapped_lines_fails_loud():
    spec = _spec("b57-r0", [LineId.unmapped("ru", "57", 1, 0), LineId.unmapped("ru", "57", 2, 0)],
                 votable=[])
    compose = render.make_compositor(_stub_renderer(), docx_for=_docx_for_stub([]), page_boundaries=_NO_BREAKS)
    with pytest.raises(render.RenderError, match="no mapped lines"):
        compose([spec])


def test_renderer_producing_no_pages_fails_loud():
    spec = _spec("b57-r0", [LineId.mapped("ru", "57", 10, 0)])

    def empty(docx: Path, lo: int, hi: int, out_png: Path) -> list[Path]:
        return []

    compose = render.make_compositor(empty, docx_for=_docx_for_stub([]), page_boundaries=_NO_BREAKS)
    with pytest.raises(render.RenderError, match="no page image"):
        compose([spec])


def test_region_mixing_book_or_lang_fails_loud():
    # render is a trust boundary: a region whose mapped lines span two books cannot anchor one
    # authored page slice, so it must fail loud rather than slice the first line's book silently.
    rp = _stub_renderer()
    mixed_book = _spec("x", [LineId.mapped("ru", "57", 10, 0), LineId.mapped("ru", "16", 11, 0)])
    mixed_lang = _spec("y", [LineId.mapped("ru", "57", 10, 0), LineId.mapped("en", "57", 11, 0)])
    compose = render.make_compositor(rp, docx_for=_docx_for_stub([]), page_boundaries=_NO_BREAKS)
    for spec in (mixed_book, mixed_lang):
        with pytest.raises(render.RenderError, match="mixes book/lang"):
            compose([spec])
    assert rp.calls == []                                   # never reached the renderer


# --- the cross-page context trim (found live in E1: book 36's chapter break drew an empty page) --

def test_trim_drops_leading_context_behind_a_page_break():
    # window [7708..7714], votable [7711..7714], boundary after 7709 (inline page break):
    # paragraphs 7708-7709 live on an earlier page that renders nearly blank — drop them.
    assert render.trim_cross_page_context(7708, 7714, 7711, 7714,
                                          frozenset({7709})) == (7710, 7714)


def test_trim_drops_trailing_context_beyond_a_page_break():
    assert render.trim_cross_page_context(10, 20, 12, 15, frozenset({16})) == (10, 16)


def test_trim_ignores_boundaries_inside_the_votable_span():
    # the votable lines straddle the break: both pages carry decision content — keep them all.
    assert render.trim_cross_page_context(10, 20, 12, 18, frozenset({14})) == (10, 20)


def test_trim_takes_the_nearest_boundaries_on_each_side():
    assert render.trim_cross_page_context(0, 30, 10, 12,
                                          frozenset({2, 6, 20, 25})) == (7, 20)


def test_trim_noop_without_boundaries():
    assert render.trim_cross_page_context(5, 9, 6, 8, frozenset()) == (5, 9)


def test_compositor_renders_only_the_votable_lines_page():
    votable = [LineId.mapped("ru", "36", o, 0) for o in (7711, 7712)]
    region = [LineId.mapped("ru", "36", o, 0) for o in (7709, 7710)] + votable
    rp = _stub_renderer()
    render.make_compositor(rp, docx_for=_docx_for_stub([]),
                           page_boundaries=lambda _d: frozenset({7709}))(
        [_spec("b36-r0", region, votable=votable)])
    _, lo, hi = rp.calls[0]
    assert (lo, hi) == (7710, 7712)        # the pre-break context line 7709 is out of the image
