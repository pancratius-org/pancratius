# import-pure: no filesystem mutation
"""Read-only DOCX → IR fidelity inspector (a diagnostic, never writes src/content).

This is the inspector the ``docx_adapter`` debugging note calls for: it prints,
per source body paragraph, the OOXML signals that verse / signature / epigraph
detection consumes — resolved style, ``w:contextualSpacing``, spacing attrs,
``w:jc`` alignment, ``w:ind`` indent, ``w:numPr`` list, ``w:pBdr`` border, the
hard ``<w:br/>`` count, and the assigned visual ``lineation_group`` — beside the
IR block the paragraph actually became after the full ``adapt`` → ``normalize``
pipeline. A human can then see WHY a run was (or was not) folded into a
``verse-block``: the source signals on the left, the classifier's verdict on the
right.

It reuses ``pancratius.docx_adapter`` so the signals shown are exactly the ones
the importer reads — no parallel re-implementation that could drift from the
converter.

PURE: opens the DOCX zip for READ only and runs the pure import passes into a
scratch media dir. It mutates nothing under ``src/content``.

Run it:

    uv run python -m pancratius.docx_inspect <docx> --contains "Память кого"
    uv run python -m pancratius.docx_inspect --book 13 --around "Память кого" --context 8
    uv run python -m pancratius.docx_inspect --book 13 --verse-only
"""
from __future__ import annotations

import argparse
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pancratius import docx_adapter as da
from pancratius import ir

W = da.W


# ---------------------------------------------------------------------------
# rich per-paragraph source record (everything the importer's signals derive from)
# ---------------------------------------------------------------------------


@dataclass
class ParaRow:
    index: int
    text: str
    style: str            # resolved style id (direct pStyle or document default)
    direct_style: str     # the paragraph's own w:pStyle (``""`` = inherits default)
    align: str            # w:jc (``""`` = inherit/left)
    contextual: bool      # resolved w:contextualSpacing (suppresses para spacing)
    spacing: dict[str, str]
    indent: dict[str, str]  # w:ind attrs (firstLine / left / hanging) — prose tell
    numbered: bool        # w:numPr — a list item
    bordered: bool        # w:pBdr
    heading: bool
    thematic: bool
    br_count: int         # hard <w:br/> inside the paragraph (authored lineation)
    empty: bool
    lineation_group: int | None = None
    block_kind: str = "?"  # the IR block this paragraph's text landed in


def _doc_default_spacing(zf: zipfile.ZipFile) -> dict[str, str]:
    """The ``w:docDefaults`` paragraph spacing — the document-wide baseline gap
    LibreOffice/Word apply when a paragraph and its style set nothing. Neither the
    importer's ``_resolved_spacing`` nor a naive style-chain read sees this, so a
    paragraph can render with a real gap while looking gap-less in the XML (the
    blind spot the visual render exposed for book #71)."""
    try:
        root = ET.fromstring(zf.read("word/styles.xml"))
    except KeyError:
        return {}
    sp = root.find(f"{W}docDefaults/{W}pPrDefault/{W}pPr/{W}spacing")
    return {k.removeprefix(W): v for k, v in sp.attrib.items()} if sp is not None else {}


def _ind_attrs(ppr: ET.Element | None) -> dict[str, str]:
    ind = ppr.find(f"{W}ind") if ppr is not None else None
    if ind is None:
        return {}
    return {k.removeprefix(W): v for k, v in ind.attrib.items()}


def _br_count(p: ET.Element) -> int:
    return sum(1 for el in p.iter() if el.tag in {f"{W}br", f"{W}cr"})


def read_rows(docx: Path) -> list[ParaRow]:
    """Every top-level body paragraph with its full source signal set, in order.

    Unlike ``docx_adapter.read_w_jc`` (which emits boundary sentinels for lists and
    tables and is trimmed to reconciliation needs), this keeps every paragraph and
    every signal so the inspector can show the human the complete picture.
    """
    with zipfile.ZipFile(docx) as zf:
        styles, default_style = da._paragraph_styles(zf)
        doc_default_spacing = _doc_default_spacing(zf)
        root = ET.fromstring(zf.read("word/document.xml"))
    body = root.find(f"{W}body")
    if body is None:
        return []

    rows: list[ParaRow] = []
    # Mirror docx_adapter.read_w_jc's walk so the lineation_group ids match the
    # importer's exactly — build _SourceParagraph records the same way, then read
    # the group assignment back.
    src: list[da._SourceParagraph] = []
    raw_index: list[int] = []  # rows index aligned to reconcile-eligible src records

    def walk(el: ET.Element) -> None:
        for child in el:
            if child.tag == f"{W}p":
                ppr = child.find(f"{W}pPr")
                numbered = ppr is not None and ppr.find(f"{W}numPr") is not None
                direct_style = da._w_val(ppr.find(f"{W}pStyle") if ppr is not None else None)
                style = direct_style or default_style
                spacing = {**doc_default_spacing,
                           **da._resolved_spacing(style, styles, da._spacing_attrs(ppr))}
                txt = da._paragraph_text(child).strip()
                heading = bool(re.fullmatch(r"(?:Heading\d+|[1-9])", direct_style))
                thematic = txt in {"***", "* * *", "---"}
                contextual = da._resolved_contextual_spacing(
                    style, styles,
                    ppr.find(f"{W}contextualSpacing") is not None if ppr is not None else False,
                )
                indented = ppr.find(f"{W}ind") is not None if ppr is not None else False
                bordered = ppr.find(f"{W}pBdr") is not None if ppr is not None else False
                align = da._w_val(ppr.find(f"{W}jc") if ppr is not None else None)
                row = ParaRow(
                    index=len(rows),
                    text=txt,
                    style=style,
                    direct_style=direct_style,
                    align=align,
                    contextual=contextual,
                    spacing=spacing,
                    indent=_ind_attrs(ppr),
                    numbered=numbered,
                    bordered=bordered,
                    heading=heading,
                    thematic=thematic,
                    br_count=_br_count(child),
                    empty=not txt,
                )
                rows.append(row)
                # The source-paragraph record the importer would build (list items
                # and tables become boundaries there; keep them aligned to rows).
                if numbered:
                    src.append(da._source_boundary())
                else:
                    src.append(da._SourceParagraph(
                        align=align, text=txt, style=style,
                        contextual_spacing=contextual, spacing=spacing,
                        indented=indented, bordered=bordered,
                        heading=heading, thematic=thematic,
                    ))
                raw_index.append(row.index)
            elif child.tag == f"{W}tbl":
                src.append(da._source_boundary())
                raw_index.append(-1)
            elif child.tag == f"{W}sdt":
                content = child.find(f"{W}sdtContent")
                if content is not None:
                    walk(content)

    walk(body)
    da._assign_lineation_groups(src)
    for ri, sp in zip(raw_index, src, strict=True):
        if ri >= 0:
            rows[ri].lineation_group = sp.lineation_group
    return rows


# ---------------------------------------------------------------------------
# IR classification: what block each paragraph's reading text became
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", _TAG_RE.sub("", text)).strip()


def _block_lines(block: ir.Block) -> list[str]:
    """The normalized reading lines a block contributes, for membership lookup."""
    from pancratius.ir.normalize import inline_plain

    match block:
        case ir.VerseBlock():
            return [_norm(inline_plain(line)) for stanza in block.stanzas for line in stanza]
        case ir.Signature():
            return [_norm(s) for s in block.lines]
        case ir.Epigraph():
            return [_norm(s) for s in (*block.quote, *block.footer)]
        case ir.Paragraph() | ir.Heading():
            return [_norm(inline_plain(block.inlines))]
        case ir.DialogueLabel():
            return [_norm(block.speaker)]
        case _:
            return []


def classify(docx: Path) -> dict[str, str]:
    """Map normalized reading-line text → IR block kind after the full pipeline."""
    from pancratius.ir.normalize import normalize

    with tempfile.TemporaryDirectory(prefix="docx-inspect-") as td:
        doc = da.adapt(docx, Path(td))
        normalize(doc)
    kind_of: dict[str, str] = {}
    for block in doc.blocks:
        name = type(block).__name__
        for line in _block_lines(block):
            if line:
                kind_of.setdefault(line, name)
    return kind_of


def annotate(rows: list[ParaRow], docx: Path) -> None:
    kind_of = classify(docx)
    for row in rows:
        if row.empty:
            row.block_kind = "—"
            continue
        # A paragraph with a hard break contributes several lines; key on the first.
        first = _norm(row.text.split("\n", 1)[0])
        row.block_kind = kind_of.get(first) or kind_of.get(_norm(row.text)) or "Paragraph?"


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def _flags(row: ParaRow) -> str:
    out: list[str] = []
    if row.align:
        out.append(f"jc={row.align}")
    if row.contextual:
        out.append("ctxSp")
    if row.indent:
        fl = row.indent.get("firstLine")
        hg = row.indent.get("hanging")
        lf = row.indent.get("left") or row.indent.get("start")
        bits = []
        if fl:
            bits.append(f"first{fl}")
        if hg:
            bits.append(f"hang{hg}")
        if lf:
            bits.append(f"left{lf}")
        out.append("ind:" + ",".join(bits) if bits else "ind")
    if row.br_count:
        out.append(f"br×{row.br_count}")
    if row.numbered:
        out.append("list")
    if row.bordered:
        out.append("bdr")
    if row.heading:
        out.append("H")
    if row.thematic:
        out.append("***")
    before = row.spacing.get("before")
    after = row.spacing.get("after")
    if before and before != "0":
        out.append(f"sb{before}")
    if after and after != "0":
        out.append(f"sa{after}")
    return " ".join(out)


_KIND_MARK = {
    "VerseBlock": "VERSE",
    "Signature": "SIGN ",
    "Epigraph": "EPIG ",
    "DialogueLabel": "DLG  ",
    "Heading": "HEAD ",
    "ThematicBreak": "HR   ",
    "Paragraph": "prose",
    "Paragraph?": "prose",
    "—": "—    ",
}


def render(rows: list[ParaRow], *, width: int = 58) -> str:
    lines: list[str] = []
    header = f"{'idx':>4}  {'kind':<5}  {'lg':>3}  {'style':<14}  signals"
    lines.append(header)
    lines.append("-" * len(header))
    for row in rows:
        mark = _KIND_MARK.get(row.block_kind, row.block_kind[:5])
        lg = str(row.lineation_group) if row.lineation_group is not None else "·"
        preview = re.sub(r"\s+", " ", row.text)[:width] or "∅"
        style = (row.style or "Normal")[:14]
        lines.append(f"{row.index:>4}  {mark:<5}  {lg:>3}  {style:<14}  {_flags(row)}")
        lines.append(f"        “{preview}”")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------


def _book_docx(number: int) -> Path:
    root = Path(__file__).resolve().parents[1]
    matches = sorted((root / "src" / "content" / "books").glob(f"{number:02d}-*"))
    if not matches:
        raise SystemExit(f"no book folder for #{number}")
    docx = matches[0] / "ru.docx"
    if not docx.is_file():
        raise SystemExit(f"no ru.docx in {matches[0].name}")
    return docx


def _select(rows: list[ParaRow], args: argparse.Namespace) -> list[ParaRow]:
    if args.around:
        hits = [r.index for r in rows if args.around in r.text]
        if not hits:
            raise SystemExit(f"no paragraph contains {args.around!r}")
        keep: set[int] = set()
        for h in hits:
            keep.update(range(max(0, h - args.context), min(len(rows), h + args.context + 1)))
        return [r for r in rows if r.index in keep]
    if args.contains:
        return [r for r in rows if args.contains in r.text]
    if args.verse_only:
        return [r for r in rows if r.block_kind == "VerseBlock"]
    if args.range:
        lo, hi = (int(x) for x in args.range.split(":"))
        return [r for r in rows if lo <= r.index <= hi]
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("docx", nargs="?", type=Path, help="path to a .docx")
    src.add_argument("--book", type=int, help="committed RU book number (uses its ru.docx)")
    ap.add_argument("--contains", help="only rows whose source text contains this substring")
    ap.add_argument("--around", help="rows near (±context) paragraphs containing this substring")
    ap.add_argument("--context", type=int, default=6, help="rows of context for --around")
    ap.add_argument("--range", help="row index range LO:HI (inclusive)")
    ap.add_argument("--verse-only", action="store_true", help="only rows the IR folded into a verse-block")
    args = ap.parse_args(argv)

    docx = _book_docx(args.book) if args.book else args.docx
    if not docx or not docx.is_file():
        raise SystemExit(f"not a file: {docx}")

    rows = read_rows(docx)
    annotate(rows, docx)
    selected = _select(rows, args)
    print(f"# {docx}  ({len(rows)} body paragraphs, {len(selected)} shown)")
    n_verse = sum(1 for r in rows if r.block_kind == "VerseBlock")
    n_groups = len({r.lineation_group for r in rows if r.lineation_group is not None})
    print(f"# verse-block paragraphs: {n_verse}   visual lineation-groups: {n_groups}")
    print(render(selected))
    return 0


if __name__ == "__main__":
    sys.exit(main())
