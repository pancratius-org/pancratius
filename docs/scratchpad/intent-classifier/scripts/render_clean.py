# research-pure: reads src/content DOCX read-only; writes only a temp clean docx + PNG.
"""Render a paragraph slice when LibreOffice rejects the source package.

Book 02's source DOCX (rewritten 2026-05-30) fails to load in LibreOffice
("source file could not be loaded") even unmodified, and even after stripping all
pPr/styles/media — the rewritten run-level markup itself is what LO refuses, while
pandoc reads it fine. The standard `docx render-slice` reuses that markup, so it
inherits the defect.

This rebuilds a PRISTINE document.xml from scratch: for each kept body w:p it emits a
fresh paragraph carrying only (a) the reading text, (b) every hard <w:br> line break
(the candidate-line boundaries the lineation task turns on), and (c) the paragraph's
alignment/indent IF present (jc/ind) — the layout signals under test. The source
sectPr (page geometry) is preserved so wrapping width matches the real page. Empty
paragraphs are kept as blank lines (stanza/section gaps are visible on the page).

    uv run python docs/scratchpad/intent-classifier/scripts/render_clean.py \
        --docx src/content/books/02-malenkii-tsar/ru.docx \
        --around "Ты странный" --ctx 12 --out /tmp/gold_02_ty.png
"""
from __future__ import annotations

import argparse
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr
import zipfile

from pancratius import docx_render as r

W = r.W

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)
ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)
NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _para_xml(p: ET.Element) -> str:
    """Fresh w:p: alignment/indent (if any) + ordered runs of text and hard breaks.

    Walks the paragraph's children in document order so a <w:br> between two text runs
    survives as a real line break (the candidate-line boundary), while bold is kept so
    bold-pseudo-headers/stanzas read as on the page."""
    ppr_parts: list[str] = []
    ppr = p.find(f"{W}pPr")
    if ppr is not None:
        jc = ppr.find(f"{W}jc")
        if jc is not None and jc.get(f"{W}val"):
            ppr_parts.append(f'<w:jc w:val={quoteattr(jc.get(f"{W}val"))}/>')
        ind = ppr.find(f"{W}ind")
        if ind is not None and ind.attrib:
            attrs = " ".join(f"w:{k.split('}')[-1]}={quoteattr(v)}" for k, v in ind.attrib.items())
            ppr_parts.append(f"<w:ind {attrs}/>")
    ppr_xml = f"<w:pPr>{''.join(ppr_parts)}</w:pPr>" if ppr_parts else ""

    runs: list[str] = []
    for run in p.findall(f"{W}r"):
        rpr = run.find(f"{W}rPr")
        bold = rpr is not None and rpr.find(f"{W}b") is not None
        italic = rpr is not None and rpr.find(f"{W}i") is not None
        rpr_parts = ("<w:b/>" if bold else "") + ("<w:i/>" if italic else "")
        rpr_xml = f"<w:rPr>{rpr_parts}</w:rPr>" if rpr_parts else ""
        body_bits: list[str] = []
        for child in run:
            tag = child.tag.split("}")[-1]
            if tag == "t":
                body_bits.append(f'<w:t xml:space="preserve">{escape(child.text or "")}</w:t>')
            elif tag == "br":
                body_bits.append("<w:br/>")
            elif tag == "tab":
                body_bits.append("<w:tab/>")
        if body_bits:
            runs.append(f"<w:r>{rpr_xml}{''.join(body_bits)}</w:r>")
    return f"<w:p>{ppr_xml}{''.join(runs)}</w:p>"


def build_clean(docx: Path, lo: int, hi: int, dest: Path) -> Path:
    with zipfile.ZipFile(docx) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    body = root.find(f"{W}body")
    paras = r._ordered_paragraphs(body)
    keep = paras[lo : hi + 1]
    para_xml = "".join(_para_xml(p) for p in keep)

    # Rebuild sectPr cleanly from pgSz/pgMar only: the source sectPr carries
    # footerReference rIds that would dangle (no document.xml.rels here) and ns-prefix
    # noise from the upstream re-serialization — both make LO refuse the package. Page
    # size + margins are all that the wrapping width (the load-bearing layout) needs.
    sect = body.find(f"{W}sectPr")

    def _attrs(el: ET.Element | None) -> str:
        if el is None:
            return ""
        return " ".join(f"w:{k.split('}')[-1]}={quoteattr(v)}" for k, v in el.attrib.items())

    pg = sect.find(f"{W}pgSz") if sect is not None else None
    mr = sect.find(f"{W}pgMar") if sect is not None else None
    pg_xml = f"<w:pgSz {_attrs(pg)}/>" if pg is not None else '<w:pgSz w:w="11906" w:h="16838"/>'
    mr_xml = (
        f"<w:pgMar {_attrs(mr)}/>" if mr is not None
        else '<w:pgMar w:top="1134" w:right="850" w:bottom="1134" w:left="1701"/>'
    )
    sect_xml = f"<w:sectPr>{pg_xml}{mr_xml}</w:sectPr>"

    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{NS}"><w:body>{para_xml}{sect_xml}</w:body></w:document>'
    )
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CONTENT_TYPES)
        zf.writestr("_rels/.rels", ROOT_RELS)
        zf.writestr("word/document.xml", doc)
    return dest


def render(docx: Path, lo: int, hi: int, out: Path, dpi: int = 140) -> list[Path]:
    soffice = r._soffice()
    with tempfile.TemporaryDirectory(prefix="render-clean-") as td:
        tdp = Path(td)
        sliced = build_clean(docx, lo, hi, tdp / "slice.docx")
        subprocess.run(
            [soffice, "--headless", f"-env:UserInstallation=file://{tdp}/loprof",
             "--convert-to", "pdf", "--outdir", str(tdp), str(sliced)],
            check=True, capture_output=True, text=True,
        )
        pdf = tdp / "slice.pdf"
        if not pdf.is_file():
            raise SystemExit("LibreOffice produced no PDF for the clean slice")
        out.parent.mkdir(parents=True, exist_ok=True)
        stem = out.with_suffix("")
        subprocess.run(["pdftoppm", "-png", "-r", str(dpi), str(pdf), str(stem)],
                       check=True, capture_output=True, text=True)
    return sorted(out.parent.glob(f"{out.stem}-*.png"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--docx", required=True)
    ap.add_argument("--around")
    ap.add_argument("--range")
    ap.add_argument("--ctx", type=int, default=12)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    docx = Path(args.docx)
    if args.range:
        lo, hi = (int(x) for x in args.range.split(":"))
    else:
        lo, hi, _ = r.resolve_range(docx, around=args.around, context=args.ctx)
    pages = render(docx, lo, hi, Path(args.out))
    print(f"range {lo}:{hi} -> {[str(p) for p in pages]}")
