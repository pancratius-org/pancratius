"""DOCX → block IR (the one source adapter).

This is the parse stage of `docs/import-pipeline.md`: it turns a DOCX into the
typed IR and stops. **No Markdown string is produced here** — the adapter does not
parse to GFM and then patch the string.

The primary parse is `pandoc --from docx+empty_paragraphs --to json` (the Phase-0
decision: `+empty_paragraphs` keeps Word's empty paragraphs as `Para []`, so
stanza breaks survive into the IR). The ONLY OOXML side-channel read is paragraph
alignment `w:jc`, which Pandoc structurally drops; it is zipped onto the IR's
`Paragraph` blocks positionally by visible-body-paragraph order.

This module is NOT `import-pure`: it shells out to pandoc, reads the DOCX zip, and
extracts media into a caller-provided scratch directory. That impurity is
deliberately isolated here so every downstream stage (normalize, lower) is pure.
Footnotes are inline `Note` nodes in the AST; they are lowered to IR
`FootnoteRef` + `FootnoteDef` pairs with dense renumbering by reference order, so
a definition can never be lost to tail-stripping later.
"""

from __future__ import annotations

import json
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from lib import ir

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
# OOXML markup-compatibility (`mc:`): a run can carry both an `mc:Choice` and an
# `mc:Fallback` rendering of the SAME content (e.g. a drawing vs a VML picture);
# walking every `w:t` would DOUBLE that text, so the side-channel reader drops the
# `mc:Fallback` subtree and keeps only the Choice/primary text.
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
MC_FALLBACK = f"{{{MC_NS}}}Fallback"

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _words(text: str) -> list[str]:
    """The casefolded reading-word stream of `text` — the unit the alignment
    reconciliation diffs on (script-agnostic via `\\w` under `re.UNICODE`)."""
    return [m.group(0).casefold() for m in _WORD_RE.finditer(text)]


def _node(value: object) -> dict[str, Any] | None:
    """View an opaque value as a Pandoc `{"t":…, "c":…}` node when it is a dict
    (so `.get("t")`/`.get("c")` are string-keyed). A bare `isinstance(x, dict)`
    narrow alone yields `dict[Unknown, Unknown]`, whose keys ty types as `Never`."""
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


# ---------------------------------------------------------------------------
# Pandoc JSON
# ---------------------------------------------------------------------------


def run_pandoc_json(docx: Path, media_dir: Path) -> tuple[dict[str, Any], str]:
    """Parse `docx` to the Pandoc JSON AST, extracting media into `media_dir`.

    Returns `(ast, stderr)`. `+empty_paragraphs` preserves Word empty paragraphs
    so stanza structure reaches the IR. Raises on a non-zero pandoc exit.
    """
    cmd = [
        "pandoc", "--from", "docx+empty_paragraphs", "--to", "json",
        "--extract-media", str(media_dir), str(docx),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed on {docx.name}: {proc.stderr.strip()}")
    return json.loads(proc.stdout), proc.stderr.strip()


# ---------------------------------------------------------------------------
# OOXML w:jc side-channel (the one signal Pandoc drops)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _JcRecord:
    """One body `w:p`'s alignment plus its reading text, for reconciliation
    against the AST paragraph sequence (the positional zip the C1 fix replaces)."""

    align: str
    text: str


def _paragraph_text(p: ET.Element) -> str:
    """The reading text of a `w:p` from its `w:t` runs, hard breaks as spaces, and
    `mc:Fallback` duplicates dropped (so an `mc:Choice`/`mc:Fallback` pair is not
    double-counted). Used only to MATCH the paragraph to its AST counterpart."""
    parts: list[str] = []

    def walk(el: ET.Element, in_fallback: bool) -> None:
        for child in el:
            if child.tag == MC_FALLBACK:
                walk(child, True)  # the redundant rendering — counted by neither side
            elif child.tag == f"{W}t":
                if not in_fallback:
                    parts.append(child.text or "")
            elif child.tag in {f"{W}br", f"{W}cr", f"{W}tab"}:
                parts.append(" ")
            else:
                walk(child, in_fallback)

    walk(p, False)
    return "".join(parts)


def read_w_jc(docx: Path) -> list[_JcRecord]:
    """Per-body-paragraph `(align, text)` records in document order.

    Only top-level body paragraphs (not inside tables or footnotes) are walked:
    the body's direct `w:p` children plus those nested in `w:sdt` content controls,
    skipping `w:tbl` contents (table cells are not top-level AST paragraphs).

    List-item paragraphs (`w:numPr`) are SKIPPED: Pandoc deterministically
    collapses a run of N list `w:p` into ONE `OrderedList`/`BulletList` block, so
    they never surface as top-level `Para`s. Emitting an alignment entry for each
    would desync the vector from the AST paragraph sequence — the dominant C1
    drift source (one list of N items lagged the index by N, dropping a later
    right-aligned signature/epigraph). The remaining collapse/merge shapes (a
    `Div`/`Figure`/image-only paragraph, or several short `w:p` Pandoc fuses into
    one multi-line `Para`) are absorbed by the CONTENT reconciliation in `adapt`,
    not by trying to enumerate every collapse here.
    """
    with zipfile.ZipFile(docx) as zf:
        root = ET.fromstring(zf.read("word/document.xml"))
    body = root.find(f"{W}body")
    if body is None:
        return []
    records: list[_JcRecord] = []

    def walk(el: ET.Element) -> None:
        for child in el:
            if child.tag == f"{W}p":
                ppr = child.find(f"{W}pPr")
                if ppr is not None and ppr.find(f"{W}numPr") is not None:
                    continue  # list item: Pandoc collapses it into a List block
                jc = ppr.find(f"{W}jc") if ppr is not None else None
                align = str(jc.get(f"{W}val")) if jc is not None else ""
                records.append(_JcRecord(align=align, text=_paragraph_text(child)))
            elif child.tag == f"{W}tbl":
                continue  # table cells are not top-level AST paragraphs
            elif child.tag == f"{W}sdt":
                content = child.find(f"{W}sdtContent")
                if content is not None:
                    walk(content)

    walk(body)
    return records


def _fingerprint(text: str) -> str:
    """A whitespace/case-insensitive fingerprint of a paragraph's reading words —
    the comparison key the alignment reconciliation diffs on. Joining the word
    stream (not the raw text) makes the AST `_plain` rendering and the raw `w:t`
    text comparable (both drop punctuation/markup spacing differences)."""
    return " ".join(_words(text))


def reconcile_alignment(
    paragraphs: list[ir.Paragraph], records: list[_JcRecord]
) -> int:
    """Assign each AST `Paragraph` its source `w:jc` alignment by CONTENT, not by
    position, and return the count of paragraphs given a non-default alignment.

    The positional 1:1 (body `w:p` ↔ top-level `Para`/`Header`) assumption is
    false: Pandoc collapses some `w:p` out of the top-level sequence (lists,
    `Div`s, `Figure`s, image-only paragraphs) and FUSES others (several short
    right-aligned `w:p` become one multi-line `Para`). A positional zip therefore
    drifts after the first such shape and silently mis-assigns/drops a later
    right-aligned signature/epigraph (the C1 regression).

    ONLY right/end alignment is reconciled: it is the sole alignment any downstream
    pass reads (signature/epigraph detection in `ir_normalize.structural_blocks`);
    center/left/justify are inert, so a document with no right-aligned source
    paragraph does nothing — the common case, and what keeps the largest books
    (≈40k paragraphs, 0 right `w:jc`) instant.

    The placement is a single ORDER-PRESERVING forward pass over the AST paragraph
    fingerprints — near-linear even on the largest books (a word/paragraph
    `difflib` over a 40k-element sequence is O(n·m) and unusable). For each right
    record, in document order, the cursor advances to the next AST paragraph that
    carries that text. Two shapes are accepted, EXACT first so a record never binds
    to an unrelated paragraph that merely shares a prefix:

      * EXACT fingerprint — the 1:1 case (a standalone right `w:p` → one `Para`);
      * a FUSION — Pandoc joined several CONSECUTIVE right `w:p` into one multi-line
        `Para`; the paragraph fingerprint equals this record's concatenated with
        the next records', so those consecutive records are consumed onto the one
        fused paragraph.

    A record whose text never surfaces (collapsed away) is skipped; a paragraph
    keeps the default `""` when no reconciled record carried a right `w:jc`."""
    if not any(r.align in {"right", "end"} for r in records):
        return 0

    a_fps = [_fingerprint(_paragraph_plain(p)) for p in paragraphs]
    rec_fps = [_fingerprint(r.text) for r in records]
    rec_right = [r.align in {"right", "end"} for r in records]

    n = len(a_fps)

    def fusion_len(scan: int, ri: int) -> int:
        """If the paragraph at `scan` is the FUSION of consecutive records starting
        at `ri` whose concatenated fingerprints EXACTLY equal it, return how many
        records it consumes (≥ 2); else 0. The full equality requirement is what
        stops a record binding to an unrelated paragraph that merely shares a word
        prefix (the epigraph-vs-paraphrase false match)."""
        para = a_fps[scan]
        built = rec_fps[ri]
        if not built or not para.startswith(built):
            return 0
        k = ri + 1
        while k < len(records) and len(built) < len(para):
            nxt = rec_fps[k]
            if not nxt or not para.startswith(f"{built} {nxt}"):
                break
            built = f"{built} {nxt}"
            k += 1
        return (k - ri) if built == para else 0

    assigned = 0
    cursor = 0
    ri = 0
    nr = len(records)
    while ri < nr:
        if not rec_right[ri] or not rec_fps[ri]:
            ri += 1
            continue
        target = rec_fps[ri]
        # Scan forward for a paragraph that EXACTLY matches this record, or that is a
        # CONFIRMED fusion of this record plus the next consecutive ones. Exact and
        # confirmed-fusion only — never a bare prefix — so a record never binds to an
        # unrelated longer paragraph that just happens to start with the same words.
        scan = cursor
        consumed = 1
        while scan < n:
            if a_fps[scan] == target:
                break
            fl = fusion_len(scan, ri)
            if fl:
                consumed = fl
                break
            scan += 1
        if scan >= n:
            ri += 1  # this record's text never surfaced (collapsed away)
            continue
        if not paragraphs[scan].align:
            paragraphs[scan].align = "right"
            assigned += 1
        cursor = scan + 1
        ri += consumed
    return assigned


def _paragraph_plain(para: ir.Paragraph) -> str:
    """The reading text of an IR paragraph (lazy import of the normalize helper to
    avoid a parse↔normalize import cycle)."""
    from lib.ir_normalize import inline_plain

    return inline_plain(para.inlines)


# ---------------------------------------------------------------------------
# Inline lowering: Pandoc inline node -> IR Inline
# ---------------------------------------------------------------------------

_EMPH_MAP: dict[str, ir.EmphKind] = {
    "Strong": "strong", "Emph": "emph", "Strikeout": "strike",
    "Superscript": "sup", "Subscript": "sub",
}


class _Ctx:
    """Per-document state threaded through the inline/block walk: the running
    footnote index and the footnote definitions collected in reference order."""

    def __init__(self) -> None:
        self.fn_index = 0
        self.fn_defs: list[tuple[int, list[ir.Block]]] = []


def _inlines(nodes: list[dict[str, Any]], ctx: _Ctx) -> list[ir.Inline]:
    out: list[ir.Inline] = []
    for node in nodes:
        out.extend(_inline(node, ctx))
    return out


def _inline(node: dict[str, Any], ctx: _Ctx) -> list[ir.Inline]:
    t = node.get("t")
    c = node.get("c")
    if t == "Str":
        return [ir.Text(str(c))]
    if t == "Space":
        return [ir.Text(" ")]
    if t == "SoftBreak":
        return [ir.SoftBreak()]
    if t == "LineBreak":
        return [ir.LineBreak()]
    if t in _EMPH_MAP:
        children = c if isinstance(c, list) else []
        return [ir.Emphasis(_EMPH_MAP[str(t)], _inlines(children, ctx))]
    if t in {"Underline", "SmallCaps"}:
        # Production unwraps these to plain text.
        return _inlines(c if isinstance(c, list) else [], ctx)
    if t == "Quoted" and isinstance(c, list):
        qt, quoted = c
        single = isinstance(qt, dict) and qt.get("t") == "SingleQuote"
        return [ir.Quoted(single, _inlines(quoted, ctx))]
    if t == "Code" and isinstance(c, list):
        return [ir.Code(str(c[1]))]
    if t == "Link" and isinstance(c, list):
        _attr, label, target = c
        return [ir.Link(_inlines(label, ctx), str(target[0]))]
    if t == "Image" and isinstance(c, list):
        _attr, label, target = c
        return [ir.ImageInline(src=str(target[0]), alt=_plain(label))]
    if t == "Span" and isinstance(c, list):
        attr, span = c
        # Production unwraps a Span to its children, EXCEPT a directional span: a
        # `dir` attribute (Hebrew/Arabic bidi) governs visual ordering, so it is
        # modelled (`DirectionalSpan`) rather than flattened. `attr` is
        # `[id, classes, [(k, v), ...]]`; only the `dir` key is preserved.
        direction = ""
        if isinstance(attr, list) and len(attr) == 3 and isinstance(attr[2], list):
            for pair in attr[2]:
                if isinstance(pair, list) and len(pair) == 2 and pair[0] == "dir":
                    direction = str(pair[1])
        children = _inlines(span, ctx)
        if direction:
            return [ir.DirectionalSpan(direction=direction, children=children)]
        return children
    if t == "Note" and isinstance(c, list):
        # A footnote: `c` is a list of body blocks. Renumber densely by reference
        # order so the dense id never depends on Word's internal `w:id`.
        ctx.fn_index += 1
        idx = ctx.fn_index
        ctx.fn_defs.append((idx, [_block(b, ctx) for b in c]))
        return [ir.FootnoteRef(raw_index=idx, id=idx)]
    if t == "RawInline" and isinstance(c, list):
        fmt, raw = c
        if fmt in {"html", "markdown"}:
            return [ir.Text(str(raw))]
        return []
    if isinstance(c, list):
        return [ir.UnknownInline(note=str(t), children=_inlines(c, ctx))]
    return [ir.UnknownInline(note=str(t))]


def _plain(nodes: list[dict[str, Any]]) -> str:
    """Plain-text flatten of inlines (image alt + table cells)."""
    out: list[str] = []
    for node in nodes:
        t = node.get("t")
        c = node.get("c")
        if t == "Str":
            out.append(str(c))
        elif t in {"Space", "SoftBreak", "LineBreak"}:
            out.append(" ")
        elif t in _EMPH_MAP or t in {"Underline", "SmallCaps", "Span"}:
            payload = c[1] if t == "Span" and isinstance(c, list) else c
            out.append(_plain(payload if isinstance(payload, list) else []))
        elif t == "Quoted" and isinstance(c, list):
            out.append(_plain(c[1]))
        elif t == "Code" and isinstance(c, list):
            out.append(str(c[1]))
        elif t in {"Link", "Image"} and isinstance(c, list):
            out.append(_plain(c[1]))
        elif isinstance(c, list):
            out.append(_plain(c))
    return "".join(out).strip()


# ---------------------------------------------------------------------------
# Block lowering: Pandoc block node -> IR Block
# ---------------------------------------------------------------------------


def _block(node: dict[str, Any], ctx: _Ctx) -> ir.Block:
    t = node.get("t")
    c = node.get("c")
    if t == "Header" and isinstance(c, list):
        level, _attr, inlines = c
        return ir.Heading(level=int(level), inlines=_inlines(inlines, ctx))
    if t in {"Para", "Plain"}:
        inlines = c if isinstance(c, list) else []
        if not inlines:
            return ir.Paragraph(inlines=[], empty=True)
        para = ir.Paragraph(inlines=_inlines(inlines, ctx))
        para.italic = _all_italic(inlines)
        return para
    if t == "HorizontalRule":
        return ir.ThematicBreak()
    if t == "BlockQuote" and isinstance(c, list):
        return ir.BlockQuote(blocks=[_block(b, ctx) for b in c])
    if t == "BulletList" and isinstance(c, list):
        return ir.ListBlock(ordered=False, items=[[_block(b, ctx) for b in item] for item in c])
    if t == "OrderedList" and isinstance(c, list):
        attr, items = c
        # attr = [start, style, delim]; keep the source start ordinal.
        start = int(attr[0]) if isinstance(attr, list) and attr else 1
        return ir.ListBlock(
            ordered=True, start=start,
            items=[[_block(b, ctx) for b in item] for item in items],
        )
    if t == "CodeBlock" and isinstance(c, list):
        _attr, text = c
        return ir.CodeBlock(text=str(text))
    if t == "Table":
        return _table(node, ctx)
    if t == "Div" and isinstance(c, list):
        # Production unwraps Divs; keep a transparent container (role "_div") so
        # the structure survives until lowering inlines its children.
        _attr, blocks = c
        return ir.BlockQuote(blocks=[_block(b, ctx) for b in (blocks or [])], role="_div")
    if t == "Figure" and isinstance(c, list):
        # Pandoc 3.x wraps a standalone image in a Figure: [attr, caption,
        # content_blocks]. The GFM writer keeps the image plus the figcaption text;
        # unwrap to a transparent container of the figure's content blocks followed
        # by the caption as a paragraph, so neither the image nor its caption is
        # lost (Figure is the standalone-image shape; book-illustration content).
        _attr, caption, content = c
        inner: list[ir.Block] = [_block(b, ctx) for b in (content or [])]
        cap_blocks = caption[1] if isinstance(caption, list) and len(caption) > 1 else None
        if cap_blocks:
            inner.extend(_block(b, ctx) for b in cap_blocks)
        return ir.BlockQuote(blocks=inner, role="_div")
    return ir.UnknownBlock(note=str(t))


def _all_italic(inlines: list[dict[str, Any]]) -> bool:
    """True when every text-bearing top-level inline is wrapped in `Emph` (the
    epigraph italic signal)."""
    saw = False
    for node in inlines:
        t = node.get("t")
        if t in {"Space", "SoftBreak", "LineBreak"}:
            continue
        if t == "Emph":
            saw = True
            continue
        return False
    return saw


_EMPH_WRAP: dict[str, tuple[str, str]] = {
    "Strong": ("**", "**"), "Emph": ("*", "*"), "Strikeout": ("~~", "~~"),
    "Superscript": ("^", "^"), "Subscript": ("~", "~"),
}


def _inline_md(nodes: list[dict[str, Any]]) -> str:
    """Plain Markdown render of Pandoc inlines — used only for table cells (the
    one place the adapter flattens inlines to text for `ir.Table.rows`)."""
    out: list[str] = []
    for node in nodes:
        t = node.get("t")
        c = node.get("c")
        if t == "Str":
            out.append(str(c))
        elif t in {"Space", "SoftBreak", "LineBreak"}:
            out.append(" ")
        elif t in _EMPH_WRAP and isinstance(c, list):
            o, cl = _EMPH_WRAP[str(t)]
            out.append(f"{o}{_inline_md(c)}{cl}")
        elif t in {"Underline", "SmallCaps"} and isinstance(c, list):
            out.append(_inline_md(c))
        elif t == "Quoted" and isinstance(c, list):
            qt, quoted = c
            o, cl = ("'", "'") if isinstance(qt, dict) and qt.get("t") == "SingleQuote" else ("«", "»")
            out.append(f"{o}{_inline_md(quoted)}{cl}")
        elif t == "Code" and isinstance(c, list):
            out.append(f"`{c[1]}`")
        elif t == "Link" and isinstance(c, list):
            _a, label, target = c
            out.append(f"[{_inline_md(label)}]({target[0]})")
        elif t == "Image" and isinstance(c, list):
            _a, label, target = c
            out.append(f"![{_plain(label)}]({target[0]})")
        elif t == "Span" and isinstance(c, list):
            out.append(_inline_md(c[1]))
        elif isinstance(c, list):
            out.append(_inline_md(c))
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _table(node: dict[str, Any], ctx: _Ctx) -> ir.Table:
    """Structure a Pandoc 3.x Table into `ir.Table`. `rows` carries STRUCTURED
    cell content (rows of cells of inlines) so reading-content table cells flow
    through the same AI-alt and asset passes as prose; `raw` keeps the node for the
    bibliography classifier (it needs hrefs + image alts)."""
    c = node.get("c")
    rows: list[list[list[ir.Inline]]] = []

    def cell_inlines(cell: object) -> list[ir.Inline]:
        # cell = [attr, alignment, rowspan, colspan, blocks]; isinstance narrows the
        # opaque Pandoc node to a list before indexing (the structural try/except
        # below is the runtime guard for any unexpected shape).
        if not isinstance(cell, list) or len(cell) < 5 or not isinstance(cell[4], list):
            return []
        out: list[ir.Inline] = []
        for raw in cell[4]:
            b = _node(raw)
            if b is not None and b.get("t") in {"Para", "Plain"}:
                if out:
                    out.append(ir.Text(" "))  # join multi-block cells with a space
                payload = b.get("c")
                out.extend(_inlines(payload if isinstance(payload, list) else [], ctx))
        return out

    def cells_of(row: object) -> list[list[ir.Inline]]:
        # row = [attr, cells]
        if not isinstance(row, list) or len(row) < 2 or not isinstance(row[1], list):
            return []
        return [cell_inlines(cell) for cell in row[1]]

    if isinstance(c, list):
        try:
            _attr, _cap, _cols, thead, tbodies, _tfoot = c
            for hrow in (thead[1] if thead else []):
                rows.append(cells_of(hrow))
            for tbody in tbodies:
                # tbody = [attr, rowheadcols, headerrows, bodyrows]
                for brow in tbody[3]:
                    rows.append(cells_of(brow))
        except (ValueError, IndexError, TypeError):
            # A table shape we don't recognize keeps `raw` for the classifier and
            # an empty `rows` (lowered to nothing rather than guessed).
            pass
    return ir.Table(rows=rows, raw=node)


# ---------------------------------------------------------------------------
# Top-level adapter
# ---------------------------------------------------------------------------


def adapt(docx: Path, media_dir: Path) -> ir.Document:
    """Parse `docx` into an `ir.Document`, extracting media into `media_dir`.

    Alignment from `w:jc` is assigned onto the IR's top-level `Paragraph` blocks by
    CONTENT (`reconcile_alignment`), not by a positional zip — the positional zip
    drifted past any collapsed/fused `w:p` and silently dropped a later
    right-aligned signature/epigraph (C1). A surfaced `warning` fires when no
    right-aligned source paragraph could be reconciled despite the source having
    them, so a future regression can't ship silently. Footnote definitions
    collected during the inline walk are attached densely renumbered.
    """
    ast, warns = run_pandoc_json(docx, media_dir)
    records = read_w_jc(docx)

    ctx = _Ctx()
    doc = ir.Document()
    if warns:
        doc.diagnostics.append(ir.Diagnostic("info", "import.pandoc-warn", warns))

    raw_blocks = ast.get("blocks") or []
    if not isinstance(raw_blocks, list):
        raw_blocks = []
    for node in raw_blocks:
        doc.blocks.append(_block(node, ctx))

    paragraphs = [b for b in doc.blocks if isinstance(b, ir.Paragraph)]
    assigned = reconcile_alignment(paragraphs, records)

    right_records = sum(1 for r in records if r.align in {"right", "end"} and r.text.strip())
    right_assigned = sum(1 for p in paragraphs if p.align in {"right", "end"})
    doc.diagnostics.append(ir.Diagnostic(
        "info", "import.align-zip",
        f"w:jc records={len(records)} assigned={assigned} "
        f"right-records={right_records} right-assigned={right_assigned}",
    ))
    # Arm the safety: the source has right-aligned paragraphs but NONE survived
    # reconciliation — the signal the C1 drift used to swallow silently. Surface it
    # as a warning the caller propagates (a future drift then fails loud).
    if right_records and not right_assigned:
        doc.diagnostics.append(ir.Diagnostic(
            "warning", "import.align-unreconciled",
            f"{right_records} right-aligned source paragraph(s) but 0 reconciled "
            f"onto the AST — alignment-driven signatures/epigraphs may be lost",
        ))

    doc.footnotes = [ir.FootnoteDef(id=i, blocks=bs) for i, bs in ctx.fn_defs]
    return doc
