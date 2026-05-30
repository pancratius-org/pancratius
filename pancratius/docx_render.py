"""Render a focused DOCX paragraph range to an image for VISUAL fidelity checks.

The OOXML signal table from ``docx_inspect`` tells you what the source *says*
(``w:jc``, ``w:ind``, ``w:contextualSpacing`` …). This tells you what the source
*looks like* — the rendered layout a human reads — so a prose-vs-verse call is
made against the page, not against one's interpretation of the XML. (The current
PAN006B oracle codified an XML interpretation that disagrees with the page; a
visual check is how that class of mistake is caught.)

It works by SLICING: it copies the DOCX zip unchanged except for
``word/document.xml``, whose body is trimmed to the selected ``w:p`` range with
every paragraph's own markup kept byte-for-byte (alignment, indent, spacing,
``contextualSpacing``, runs). The slice is then rendered by **LibreOffice**, which
interprets the OOXML directly — an authority INDEPENDENT of this package's own
reader, so the render can actually contradict (and thereby validate) it. Pandoc is
deliberately NOT used: its Markdown/HTML path drops alignment and indent, the very
signals under test.

PURE w.r.t. the library: it reads the source DOCX and writes only into a
caller-provided scratch/output dir (a PDF + PNGs). It never touches ``src/content``.

    uv run python -m pancratius.docx_render --book 13 --around "Память кого" \
        --context 12 --out /tmp/b13.png
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from pancratius import docx_inspect as di

W = di.W
W_NS = di.da.W_NS


def _soffice() -> str | None:
    for cand in (
        "soffice",
        "libreoffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ):
        if shutil.which(cand) or Path(cand).exists():
            return cand
    return None


def _ordered_paragraphs(body: ET.Element) -> list[ET.Element]:
    """Every body ``w:p`` in document order, recursing ``w:sdt`` content controls —
    the SAME walk (and therefore the same indices) as ``docx_inspect.read_rows``.
    ``w:tbl`` paragraphs are excluded: read_rows treats a table as one boundary, so
    table cells never advance the paragraph index."""
    out: list[ET.Element] = []

    def walk(el: ET.Element) -> None:
        for child in el:
            if child.tag == f"{W}p":
                out.append(child)
            elif child.tag == f"{W}sdt":
                content = child.find(f"{W}sdtContent")
                if content is not None:
                    walk(content)
            # w:tbl is intentionally not descended (matches read_rows' boundary)
    walk(body)
    return out


def slice_docx(docx: Path, lo: int, hi: int, dest: Path) -> Path:
    """Write a DOCX to ``dest`` holding only body paragraphs [lo, hi] (inclusive).

    Paragraph indices match ``docx_inspect.read_rows`` exactly (same recursive
    walk). The kept ``w:p`` elements are re-parented as direct body children
    (``w:sdt`` wrappers dropped — inert for layout) and inline drawings/pictures are
    stripped so the slice is small and never explodes into image pages; each
    paragraph keeps its own ``pPr`` (alignment, indent, spacing,
    ``contextualSpacing``) byte-for-byte, which is all the visual call needs. The
    final ``sectPr`` (page geometry) is preserved.
    """
    ET.register_namespace("w", W_NS)
    with zipfile.ZipFile(docx) as zf:
        names = zf.namelist()
        document = zf.read("word/document.xml")
        payload = {n: zf.read(n) for n in names if n != "word/document.xml"}

    root = ET.fromstring(document)
    body = root.find(f"{W}body")
    if body is None:
        raise SystemExit("no w:body in document.xml")

    paras = _ordered_paragraphs(body)
    keep = paras[lo:hi + 1]
    for p in keep:
        # Drop inline drawings so the slice stays small and never explodes into
        # image pages; the paragraph's own pPr (the layout signal) is untouched.
        parents = {child: parent for parent in p.iter() for child in parent}
        for drawing in p.findall(f".//{W}drawing"):
            parents[drawing].remove(drawing)
    sect = body.find(f"{W}sectPr")
    for child in list(body):
        body.remove(child)
    for p in keep:
        body.append(p)
    if sect is not None:
        body.append(sect)

    out_bytes = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in payload.items():
            zf.writestr(name, data)
        zf.writestr("word/document.xml", out_bytes)
    return dest


def render(docx: Path, lo: int, hi: int, out_png: Path, *, dpi: int = 140) -> list[Path]:
    soffice = _soffice()
    if soffice is None:
        raise SystemExit(
            "LibreOffice not found. Install it for an authoritative Word-faithful render:\n"
            "    brew install --cask libreoffice\n"
            "(pandoc/typst are NOT used here — they drop the alignment/indent signals "
            "this tool exists to show.)"
        )
    with tempfile.TemporaryDirectory(prefix="docx-render-") as td:
        tdp = Path(td)
        sliced = slice_docx(docx, lo, hi, tdp / "slice.docx")
        subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf", "--outdir", str(tdp), str(sliced)],
            check=True, capture_output=True, text=True,
        )
        pdf = tdp / "slice.pdf"
        if not pdf.is_file():
            raise SystemExit("LibreOffice produced no PDF")
        out_png.parent.mkdir(parents=True, exist_ok=True)
        stem = out_png.with_suffix("")
        subprocess.run(
            ["pdftoppm", "-png", "-r", str(dpi), str(pdf), str(stem)],
            check=True, capture_output=True, text=True,
        )
    pages = sorted(out_png.parent.glob(f"{out_png.stem}-*.png"))
    if not pages:  # single-page docs land at exactly the stem
        single = out_png.parent / f"{out_png.stem}.png"
        pages = [single] if single.is_file() else []
    return pages


def _resolve_range(docx: Path, args: argparse.Namespace) -> tuple[int, int, list[di.ParaRow]]:
    rows = di.read_rows(docx)
    if args.range:
        lo, hi = (int(x) for x in args.range.split(":"))
        return lo, hi, rows
    if args.around:
        hits = [r.index for r in rows if args.around in r.text]
        if not hits:
            raise SystemExit(f"no paragraph contains {args.around!r}")
        lo = max(0, hits[0] - args.context)
        hi = min(len(rows) - 1, hits[-1] + args.context)
        return lo, hi, rows
    raise SystemExit("need --around or --range")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("docx", nargs="?", type=Path)
    src.add_argument("--book", type=int)
    ap.add_argument("--around", help="render around paragraphs containing this text")
    ap.add_argument("--context", type=int, default=10)
    ap.add_argument("--range", help="paragraph index range LO:HI")
    ap.add_argument("--out", type=Path, required=True, help="output PNG path (pages get -1,-2 suffixes)")
    ap.add_argument("--dpi", type=int, default=140)
    args = ap.parse_args(argv)

    docx = di._book_docx(args.book) if args.book else args.docx
    if not docx or not docx.is_file():
        raise SystemExit(f"not a file: {docx}")

    lo, hi, rows = _resolve_range(docx, args)
    pages = render(docx, lo, hi, args.out, dpi=args.dpi)
    print(f"rendered paragraphs [{lo}..{hi}] of {docx.name} -> {len(pages)} page(s)")
    for p in pages:
        print(f"  {p}")
    # The index↔text key so a viewer can map a rendered line to its row signals.
    print("\nparagraph index → text (correlate with `docx_inspect`):")
    for r in rows:
        if lo <= r.index <= hi and not r.empty:
            print(f"  {r.index:>5}  {re.sub(r'\\s+', ' ', r.text)[:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
