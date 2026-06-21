"""Render a focused DOCX paragraph slice to an image for visual QA.

The OOXML signal table from ``docx_inspect`` tells you what the source *says*
(``w:jc``, ``w:ind``, ``w:contextualSpacing`` …). This tells you what the source
selected paragraph range looks like in isolation, so a prose-vs-verse call can be
checked against a rendered slice rather than only against XML interpretation.

It works by SLICING: it copies the DOCX zip unchanged except for
``word/document.xml``, whose body is trimmed to the selected ``w:p`` range with
every paragraph's own markup kept byte-for-byte (alignment, indent, spacing,
``contextualSpacing``, runs). The slice is then rendered by **LibreOffice** with a
temporary user profile, so an open GUI session cannot leak into the diagnostic.
LibreOffice interprets the OOXML directly — an authority INDEPENDENT of this
package's own reader, so the render can actually contradict it. This is not a
full original page render: surrounding paragraphs, live pagination, and inline
drawings are removed.
Pandoc is deliberately NOT used because its Markdown/HTML path drops alignment and
indent, the very signals under test.

PURE w.r.t. the library: it reads the source DOCX and writes only the requested
PNG(s). It never touches ``src/content``.

    uv run pancratius docx render-slice --book 13 --around "Память кого" \
        --context 12 --out /tmp/b13.png
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pancratius import docx_inspect as di

W = di.W
W_NS = di.da.W_NS


class DocxRenderError(RuntimeError):
    """The requested DOCX visual slice cannot be rendered."""


@dataclass(frozen=True, slots=True)
class ResolvedParagraphSlice:
    index_range: di.ParagraphIndexRange
    rows: tuple[di.ParaRow, ...]


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
        raise DocxRenderError("no w:body in document.xml")

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
        raise DocxRenderError(
            "LibreOffice not found. Install it for an authoritative Word-faithful render:\n"
            "    brew install --cask libreoffice\n"
            "(pandoc/typst are NOT used here — they drop the alignment/indent signals "
            "this tool exists to show.)"
        )
    with tempfile.TemporaryDirectory(prefix="docx-render-") as td:
        tdp = Path(td)
        sliced = slice_docx(docx, lo, hi, tdp / "slice.docx")
        profile = tdp / "libreoffice-profile"
        profile.mkdir()
        try:
            lo_result = subprocess.run(
                [
                    soffice,
                    "--headless",
                    "--nologo",
                    "--nodefault",
                    "--nofirststartwizard",
                    "--nolockcheck",
                    f"-env:UserInstallation={profile.as_uri()}",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(tdp),
                    str(sliced),
                ],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError as exc:
            detail = "\n".join(part for part in (exc.stdout, exc.stderr) if part)
            raise DocxRenderError(f"LibreOffice failed to render the DOCX slice: {detail}") from exc
        pdf = tdp / "slice.pdf"
        if not pdf.is_file():
            detail = "\n".join(part for part in (lo_result.stdout, lo_result.stderr) if part)
            message = "LibreOffice produced no PDF"
            if detail:
                message = f"{message}: {detail}"
            raise DocxRenderError(message)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        stem = out_png.with_suffix("")
        try:
            subprocess.run(
                ["pdftoppm", "-png", "-r", str(dpi), str(pdf), str(stem)],
                check=True, capture_output=True, text=True,
            )
        except FileNotFoundError as exc:
            raise DocxRenderError("pdftoppm not found on PATH; install poppler.") from exc
        except subprocess.CalledProcessError as exc:
            detail = "\n".join(part for part in (exc.stdout, exc.stderr) if part)
            raise DocxRenderError(f"pdftoppm failed to rasterize the DOCX slice: {detail}") from exc
    pages = sorted(out_png.parent.glob(f"{out_png.stem}-*.png"))
    if not pages:  # single-page docs land at exactly the stem
        single = out_png.parent / f"{out_png.stem}.png"
        pages = [single] if single.is_file() else []
    return pages


def resolve_range(
    docx: Path,
    *,
    around: str | None = None,
    context: int = 10,
    index_range: di.ParagraphIndexRange | None = None,
) -> ResolvedParagraphSlice:
    if docx.suffix.lower() != ".docx":
        raise DocxRenderError(f"expected a .docx file, got {docx}")
    if not docx.is_file():
        raise DocxRenderError(f"DOCX not found: {docx}")
    rows = di.read_rows(docx)
    if index_range is not None:
        return ResolvedParagraphSlice(index_range=index_range, rows=tuple(rows))
    if around is not None:
        hits = [r.index for r in rows if around in r.text]
        if not hits:
            raise DocxRenderError(f"no paragraph contains {around!r}")
        if len(hits) > 1:
            preview = ", ".join(str(index) for index in hits[:8])
            suffix = "" if len(hits) <= 8 else ", …"
            raise DocxRenderError(
                f"{around!r} matched {len(hits)} paragraphs ({preview}{suffix}); "
                "use --range with `docx inspect --around` output, or search for a more specific fragment"
            )
        lo = max(0, hits[0] - context)
        hi = min(len(rows) - 1, hits[0] + context)
        return ResolvedParagraphSlice(
            index_range=di.ParagraphIndexRange(lo=lo, hi=hi),
            rows=tuple(rows),
        )
    raise DocxRenderError("need --around or --range")


def range_key(selection: ResolvedParagraphSlice) -> list[str]:
    """Text key for correlating rendered lines with `docx inspect` rows."""
    return [
        f"{row.index:>5}  {re.sub(r'\\s+', ' ', row.text)[:80]}"
        for row in selection.rows
        if selection.index_range.lo <= row.index <= selection.index_range.hi and not row.empty
    ]
