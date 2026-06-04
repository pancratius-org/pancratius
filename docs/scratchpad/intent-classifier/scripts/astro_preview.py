# research-only: docx page  ↔  Astro-rendered candidate(s), side by side.
"""See the SAME passage two ways, in one image:

  LEFT   the docx page (LibreOffice via `render-slice`) — ground truth for how the
         author's lines actually sit on the page.
  RIGHT  one or more CANDIDATE layouts rendered with the REAL site CSS
         (src/styles/{tokens,global,prose}.css) — how Astro would show that passage
         under a given lineation decision.

The three render classes (the contract): prose = flowing <p>; lineated =
<div class="lineated"> (stanza <p>s, lines joined by <br>); verse = the same with the
additive `.verse` register. This helper exists so a human (or a judge) can adjudicate a
region by LOOKING — the page is the authority, the candidate is the proposal.

Built on the faithful IR substrate (ir_view) and the SourceSpan bridge, so the docx span
and the candidate structure cover the same source paragraphs.

Presets rendered for every region: `prose` (join each hard-bounded body run into flowing
paragraphs) and `lineated` (wrap each run in a .lineated div). Pass --candidates a JSON
file of explicit block typings to render a specific proposal (e.g. the gold, or a model's):
  [{"name": "gold", "blocks": [{"type": "verse", "ir": [1670, 1676]}, ...]}]
Body paragraphs not covered by any block default to prose.

Run:
  uv run --with pillow python astro_preview.py --book 02 --around "Ты странный" --ctx 14
  uv run --with pillow python astro_preview.py --book 71 --range 880:930 --candidates cand.json
"""
from __future__ import annotations

import argparse
import html
import json
import subprocess
import sys
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING, Literal, TypedDict

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import features as feat  # noqa: E402
import ir_view as iv  # noqa: E402

if TYPE_CHECKING:
    from PIL.Image import Image

HERE = Path(__file__).resolve().parent
STYLES = ROOT / "src" / "styles"
OUT = HERE.parent / "renders" / "preview"
OUT.mkdir(parents=True, exist_ok=True)

# The three render classes (the rendering contract): prose = flowing <p>; lineated =
# <div class="lineated"> (lines joined by <br>); verse = the same with the additive
# `.verse` register. A Literal, not a StrEnum: it is pure data threaded to the renderer,
# carries no behavior, and never needs to round-trip through json.
type RenderClass = Literal["prose", "lineated", "verse"]


def _purge_lo_locks() -> None:
    """LibreOffice leaves `.~lock.*#` files beside the docx it renders; remove them so a
    later open doesn't see a stale lock. The one place that touches src/content — a delete,
    done natively (no shelling to `find`)."""
    for lock in (ROOT / "src" / "content").rglob(".~lock.*#"):
        lock.unlink(missing_ok=True)


# --------------------------------------------------------------------------- regions
def _book_docx(book: str) -> Path:
    return {f"{n:02d}": q for n, q in feat.book_dirs()}[book]


def _region(paras: list[iv.Para], lo: int, hi: int) -> list[iv.Para]:
    return [p for p in paras if lo <= p.index <= hi]


class RegionUnlocatable(Exception):
    """A region has no SourceSpan, so the docx page it came from can't be located.
    Recoverable: a caller batching regions should skip this one, not abort the run.
    (Replaces the old `raise SystemExit` that forced callers to catch SystemExit.)"""


class CandidateBlock(TypedDict):
    """An explicit block typing in a --candidates JSON file (external, untrusted input)."""

    ir: tuple[int, int]   # inclusive ir-index span [lo, hi]
    type: RenderClass


class Candidate(TypedDict):
    name: str
    blocks: list[CandidateBlock]


def _src_span(region: list[iv.Para]) -> tuple[int, int]:
    starts = [p.src_start for p in region if p.src_start is not None]
    ends = [p.src_end for p in region if p.src_end is not None]
    if not starts:
        raise RegionUnlocatable("no SourceSpans in region — cannot locate the docx page")
    return min(starts), max(ends)


# --------------------------------------------------------------------------- candidates
def _type_map(
    region: list[iv.Para], blocks: list[CandidateBlock] | None
) -> dict[int, RenderClass]:
    """ir-index -> render class for body paragraphs. Default prose; explicit blocks win."""
    tm: dict[int, RenderClass] = {p.index: "prose" for p in region if p.role == iv.ROLE_BODY}
    for blk in blocks or []:
        lo, hi = blk["ir"]
        for i in list(tm):
            if lo <= i <= hi:
                tm[i] = blk["type"]
    return tm


def _emph(ln: iv.Line) -> str:
    # per-line inline HTML (partial emphasis preserved); fall back to escaped plain text for
    # lines built without it (none on the body path).
    return ln.html or html.escape(ln.text)


def _block_html(members: list[iv.Para], cls: RenderClass) -> str:
    """Render a run of body/empty paras as prose or as a (verse-)lineated wrapper.
    EMPTY paras split the run into stanzas / flowing paragraphs."""
    stanzas: list[list[iv.Para]] = [[]]
    for p in members:
        if p.role == iv.ROLE_EMPTY:
            if stanzas[-1]:
                stanzas.append([])
        else:
            stanzas[-1].append(p)
    stanzas = [s for s in stanzas if s]
    # Source-author DOCX indentation: a faithful EVIDENCE cue (some <w:p> are indented in the
    # source; the reader uses that to tell running prose from lineation). Shown as a block
    # left-indent — visually distinct from the site's typographic first-line indent — via an
    # inline style only (no production CSS change). NEVER a lineation label rule.
    si = ' style="padding-left:1.6em"'
    if cls == "prose":
        # one <p> per SOURCE paragraph (this corpus separates paragraphs by spacing, not blank
        # lines). A hard <w:br> WITHIN a paragraph is a deliberate author break → rendered as <br>,
        # never collapsed to a space (which would glue e.g. a "1. Вода" title onto its body). A
        # genuine flowing prose paragraph is a single line, so it is unaffected.
        return "\n".join(f"<p{si if p.indented else ''}>{'<br>'.join(_emph(ln) for ln in p.lines)}</p>"
                         for s in stanzas for p in s)
    wrap_cls = "lineated verse" if cls == "verse" else "lineated"
    out = [f'<div class="{wrap_cls}">']
    for s in stanzas:
        # indent PER SOURCE PARAGRAPH, not per stanza: in a mixed stanza the indented
        # paragraph's lines shift while a non-indented neighbour stays at the margin, so the
        # selective source-indent cue survives. Stanza grouping/gaps are unchanged (one <p>).
        parts = [f'<span style="padding-left:1.6em">{_emph(ln)}</span>' if p.indented else _emph(ln)
                 for p in s for ln in p.lines]
        out.append("<p>" + "<br>\n".join(parts) + "</p>")
    out.append("</div>")
    return "\n".join(out)


def _struct_html(p: iv.Para) -> str:
    t = html.escape(p.text)
    match p.role:
        case iv.ROLE_HEADING:
            tag = "h2" if p.level <= 1 else ("h3" if p.level == 2 else "h4")
            return f"<{tag}>{t}</{tag}>"
        case iv.ROLE_THEMATIC:
            return "<hr>"
        case iv.ROLE_IMAGE:
            return ('<figure style="margin:1.4em 0;text-align:center">'
                    '<div style="border:1px dashed var(--ink-mute);color:var(--ink-mute);'
                    'padding:1.1em;font-size:.78em;letter-spacing:.1em">IMAGE</div></figure>')
        case iv.ROLE_SIGNATURE | iv.ROLE_EPIGRAPH:
            return f'<p style="text-align:right;font-style:italic;color:var(--ink-soft)">{t}</p>'
        case iv.ROLE_BLOCKQUOTE:
            return f"<blockquote>{t}</blockquote>"
        case iv.ROLE_LIST:
            return "<ul>" + "".join(f"<li>{ln.html or html.escape(ln.text)}</li>" for ln in p.lines) + "</ul>"
        case iv.ROLE_TABLE:
            if not p.lines:
                return '<p style="color:var(--ink-mute)">[table]</p>'
            rows = "".join("<tr>" + "".join(f"<td style=\"border:1px solid var(--ink-mute);"
                           f"padding:2px 8px\">{html.escape(c)}</td>" for c in ln.text.split(" | "))
                           + "</tr>" for ln in p.lines)
            return f'<table style="border-collapse:collapse;margin:1em 0;font-size:.92em">{rows}</table>'
        case iv.ROLE_CONTEXT:    # production compiler: non-body structure (dialogue label/…)
            return f'<p style="color:var(--ink-soft)">{t}</p>'
        case _:
            return f'<p style="color:var(--ink-mute)">[{t}]</p>' if t else ""


def _body_html(region: list[iv.Para], tm: dict[int, RenderClass]) -> str:
    """Walk the region in order; group contiguous same-class body runs (EMPTY allowed
    inside) into one block; render structural paras between them."""
    out: list[str] = []
    i, n = 0, len(region)
    while i < n:
        p = region[i]
        if p.role == iv.ROLE_BODY:
            cls = tm[p.index]
            members, j = [], i
            while j < n:
                q = region[j]
                if q.role == iv.ROLE_BODY:
                    if tm[q.index] != cls:
                        break
                    members.append(q)
                elif q.role == iv.ROLE_EMPTY:
                    # an empty stays in the run only if a same-class body follows it
                    k = j + 1
                    while k < n and region[k].role == iv.ROLE_EMPTY:
                        k += 1
                    if k < n and region[k].role == iv.ROLE_BODY and tm[region[k].index] == cls:
                        members.append(q)
                    else:
                        break
                else:
                    break
                j += 1
            out.append(_block_html(members, cls))
            i = j
        elif p.role == iv.ROLE_EMPTY:
            i += 1
        else:
            out.append(_struct_html(p))
            i += 1
    return "\n".join(out)


_PAGE = Template("""<!doctype html>
<html data-theme="light"><head><meta charset="utf-8">
<link rel="stylesheet" href="file://$tok">
<link rel="stylesheet" href="file://$glb">
<link rel="stylesheet" href="file://$pr">
<style>
  html, body { margin:0; background:var(--bg); }
  #shot { width:${w}px; padding:26px 30px 34px; box-sizing:border-box; background:var(--bg); }
  .pv-title { font-family:var(--serif); font-size:12px; color:var(--ink-mute);
    letter-spacing:.12em; text-transform:uppercase; margin:0 0 16px;
    border-bottom:1px solid var(--ink-mute); padding-bottom:8px; }
</style></head><body><div id="shot">
  <div class="pv-title">$title</div>
  <article class="prose">$body</article>
</div></body></html>""")


def _render_html(
    title: str, body: str, width: int, rid: str, name: str, out_dir: Path = OUT
) -> Path:
    page = _PAGE.substitute(
        tok=STYLES / "tokens.css", glb=STYLES / "global.css", pr=STYLES / "prose.css",
        w=width, title=html.escape(title), body=body,
    )
    hp = out_dir / f"{rid}_{name}.html"
    hp.write_text(page)
    out = out_dir / f"{rid}_{name}.png"
    r = subprocess.run(["node", str(HERE / "html_shot.mjs"), str(hp), str(out), str(width)],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"html_shot failed for {name}: {r.stderr[:400]}")
    return out


def _render_docx(book: str, lo: int, hi: int, rid: str, out_dir: Path = OUT) -> list[Path]:
    out = out_dir / f"{rid}_docx.png"
    subprocess.run(["uv", "run", "pancratius", "docx", "render-slice", "--book", str(int(book)),
                    "--range", f"{lo}:{hi}", "--out", str(out)],
                   cwd=ROOT, capture_output=True, text=True)
    _purge_lo_locks()
    pages = sorted(out_dir.glob(f"{rid}_docx*.png"))
    return pages or [out]


# --------------------------------------------------------------------------- compose
def _label(img: Image, text: str) -> Image:
    from PIL import Image as PILImage
    from PIL import ImageDraw
    bar = 30
    canvas = PILImage.new("RGB", (img.width, img.height + bar), (251, 250, 246))
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 0, img.width, bar], fill=(34, 34, 40))
    d.text((10, 8), text, fill=(245, 242, 232))
    canvas.paste(img, (0, bar))
    return canvas


def _vstack(paths: list[Path]) -> Image:
    from PIL import Image
    imgs = [Image.open(p).convert("RGB") for p in paths]
    w = max(i.width for i in imgs)
    h = sum(i.height for i in imgs) + 6 * (len(imgs) - 1)
    canvas = Image.new("RGB", (w, h), (251, 250, 246))
    y = 0
    for im in imgs:
        canvas.paste(im, (0, y))
        y += im.height + 6
    return canvas


def _hcat(panels: list[Image], gap: int = 20) -> Image:
    from PIL import Image
    h = max(p.height for p in panels)
    w = sum(p.width for p in panels) + gap * (len(panels) - 1)
    canvas = Image.new("RGB", (w, h), (224, 220, 210))
    x = 0
    for p in panels:
        canvas.paste(p, (x, 0))
        x += p.width + gap
    return canvas


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--around")
    g.add_argument("--range", help="ir index range lo:hi")
    ap.add_argument("--ctx", type=int, default=14)
    ap.add_argument("--width", type=int, default=680)
    ap.add_argument("--candidates", help="JSON file of explicit candidate block typings")
    ap.add_argument("--rid", default="pv")
    args = ap.parse_args()

    from PIL import Image
    paras = iv.read_view(_book_docx(args.book))
    idxs = [p.index for p in paras]
    if args.range:
        lo, hi = (int(x) for x in args.range.split(":"))
    else:
        c = next((p.index for p in paras if args.around in p.text), None)
        if c is None:
            raise SystemExit(f"{args.around!r} not found in #{args.book}")
        lo, hi = max(min(idxs), c - args.ctx), min(max(idxs), c + args.ctx)
    region = _region(paras, lo, hi)
    src_lo, src_hi = _src_span(region)
    rid = f"{args.rid}_b{args.book}_{lo}_{hi}"
    print(f"region ir[{lo}..{hi}] ({len(region)} paras) → docx src[{src_lo}..{src_hi}]")

    # (name, blocks): blocks=[] forces all-prose; blocks=None is the special 'lineated'
    # preset (every body run forced to a lineated wrapper).
    cands: list[tuple[str, list[CandidateBlock] | None]] = [("prose", []), ("lineated", None)]
    if args.candidates:
        loaded: list[Candidate] = json.loads(Path(args.candidates).read_text())
        cands += [(c["name"], c["blocks"]) for c in loaded]

    panels = []
    docx_pages = _render_docx(args.book, src_lo, src_hi, rid)
    panels.append(_label(_vstack(docx_pages), f"DOCX  #{args.book}  src[{src_lo}:{src_hi}]"))

    for name, blocks in cands:
        tm: dict[int, RenderClass]
        if name == "lineated" and blocks is None:
            tm = {p.index: "lineated" for p in region if p.role == iv.ROLE_BODY}
        else:
            tm = _type_map(region, blocks)
        body = _body_html(region, tm)
        png = _render_html(name, body, args.width, rid, name)
        panels.append(_label(Image.open(png).convert("RGB"), name.upper()))

    final = _hcat(panels)
    fp = OUT / f"{rid}.png"
    final.save(fp)
    print(f"wrote {fp}")
    print(f"open: ! open {fp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
