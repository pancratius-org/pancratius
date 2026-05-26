"""DOCX → block IR (the one source adapter).

The parse stage of `docs/import-pipeline.md`: turn a DOCX into the typed IR and
stop. No Markdown string is produced here.

The primary parse is `pandoc --from docx+empty_paragraphs --to json`;
`+empty_paragraphs` keeps Word's empty paragraphs as `Para []` so stanza breaks
survive into the IR. The one OOXML side-channel read is paragraph alignment `w:jc`,
which Pandoc drops; it is reconciled onto the IR's `Paragraph` blocks by content.

NOT `import-pure`: it shells to pandoc, reads the DOCX zip, and extracts media into
a caller-provided scratch dir — that impurity is isolated here so downstream stages
stay pure. Footnotes arrive as inline `Note` nodes and are lowered to
`FootnoteRef`/`FootnoteDef` pairs renumbered densely by reference order.
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

from pancratius import ir

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
# OOXML markup-compatibility: a run can carry both an `mc:Choice` and an
# `mc:Fallback` rendering of the same content; the side-channel reader drops the
# `mc:Fallback` subtree so its `w:t` text is not double-counted.
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
MC_FALLBACK = f"{{{MC_NS}}}Fallback"

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _words(text: str) -> list[str]:
    """The casefolded reading-word stream of `text` (script-agnostic via `\\w` under
    `re.UNICODE`)."""
    return [m.group(0).casefold() for m in _WORD_RE.finditer(text)]


def _node(value: object) -> dict[str, Any] | None:
    """View an opaque value as a Pandoc `{"t":…, "c":…}` node when it is a dict. The
    cast is needed because a bare `isinstance(x, dict)` narrows to
    `dict[Unknown, Unknown]`, whose keys ty types as `Never`."""
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


# ---------------------------------------------------------------------------
# Pandoc JSON
# ---------------------------------------------------------------------------


# Wall-clock cap on a single pandoc invocation: a loose bound that fires only on a
# pathological input that would otherwise hang the import indefinitely.
PANDOC_TIMEOUT_SECONDS = 300


def run_pandoc_json(docx: Path, media_dir: Path) -> tuple[dict[str, Any], str]:
    """Parse `docx` to the Pandoc JSON AST, extracting media into `media_dir`.

    Returns `(ast, stderr)`. `+empty_paragraphs` preserves Word empty paragraphs
    so stanza structure reaches the IR. Raises on a non-zero pandoc exit, and on a
    `PANDOC_TIMEOUT_SECONDS` wall-clock overrun (a hung/pathological conversion is
    turned into a clear error instead of an indefinite hang).
    """
    cmd = [
        "pandoc", "--from", "docx+empty_paragraphs", "--to", "json",
        "--extract-media", str(media_dir), str(docx),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=PANDOC_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"pandoc timed out after {PANDOC_TIMEOUT_SECONDS}s on {docx.name}; "
            "the conversion was aborted (no partial output is trusted)."
        ) from exc
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed on {docx.name}: {proc.stderr.strip()}")
    return json.loads(proc.stdout), proc.stderr.strip()


# ---------------------------------------------------------------------------
# OOXML w:jc side-channel (the one signal Pandoc drops)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _JcRecord:
    """One body `w:p`'s alignment plus its reading text, for content reconciliation
    against the AST paragraph sequence."""

    align: str
    text: str


def _paragraph_text(p: ET.Element) -> str:
    """The reading text of a `w:p` (its `w:t` runs, hard breaks as spaces,
    `mc:Fallback` duplicates dropped). Used only to MATCH the paragraph to its AST
    counterpart."""
    parts: list[str] = []

    def walk(el: ET.Element, in_fallback: bool) -> None:
        for child in el:
            if child.tag == MC_FALLBACK:
                walk(child, True)
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

    Only top-level body paragraphs are walked: the body's direct `w:p` children plus
    those nested in `w:sdt` content controls, skipping `w:tbl` contents (table cells
    are not top-level AST paragraphs).

    List-item paragraphs (`w:numPr`) are skipped: Pandoc collapses a run of list
    `w:p` into one `OrderedList`/`BulletList` block, so they never surface as
    top-level `Para`s and emitting an entry per item would desync this vector from
    the AST sequence. The other collapse/fusion shapes are absorbed by the content
    reconciliation in `reconcile_alignment`, not enumerated here.
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
    """A whitespace/case-insensitive fingerprint of a paragraph's reading words — the
    comparison key reconciliation diffs on. Joining the word stream makes the AST
    `_plain` rendering and the raw `w:t` text comparable."""
    return " ".join(_words(text))


def reconcile_alignment(
    paragraphs: list[ir.Paragraph], records: list[_JcRecord]
) -> int:
    """Assign each AST `Paragraph` its source `w:jc` alignment by CONTENT, returning
    the count given a non-default alignment.

    Position cannot be trusted: Pandoc collapses some `w:p` out of the top-level
    sequence (lists, `Div`s, `Figure`s, image-only paragraphs) and FUSES others
    (several short right-aligned `w:p` become one multi-line `Para`), so a positional
    zip drifts and drops a later right-aligned signature/epigraph.

    Only right/end alignment is reconciled — the sole alignment any downstream pass
    reads (signature/epigraph detection); center/left/justify are inert, so a
    document with no right-aligned source paragraph does nothing (the common case).

    A single order-preserving forward pass over the AST fingerprints — near-linear
    where a paragraph `difflib` would be O(n·m). For each right record in order, the
    cursor advances to the next paragraph carrying its text, accepting EXACT first so
    a record never binds to an unrelated paragraph sharing a prefix:

      * EXACT fingerprint — a standalone right `w:p` → one `Para`;
      * a FUSION — consecutive right `w:p` Pandoc joined into one multi-line `Para`,
        whose fingerprint equals the concatenated records', consuming them all.

    A record whose text never surfaces is skipped; a paragraph keeps `""` when no
    reconciled record carried a right `w:jc`."""
    if not any(r.align in {"right", "end"} for r in records):
        return 0

    a_fps = [_fingerprint(_paragraph_plain(p)) for p in paragraphs]
    rec_fps = [_fingerprint(r.text) for r in records]
    rec_right = [r.align in {"right", "end"} for r in records]

    n = len(a_fps)

    def fusion_len(scan: int, ri: int) -> int:
        """How many consecutive records starting at `ri` the paragraph at `scan`
        consumes when their concatenated fingerprints EXACTLY equal it (else 0). Full
        equality, not a prefix, so a record never binds to an unrelated paragraph
        that merely shares a word prefix."""
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
        # Scan forward for an EXACT match or a confirmed fusion — never a bare prefix.
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
    from pancratius.ir.normalize import inline_plain

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
    # Dispatch on Pandoc's string tag; the `isinstance(c, list)` guards inside arms
    # are intrinsic — `c` is positional Pandoc JSON, not a typed shape.
    t = node.get("t")
    c = node.get("c")
    match t:
        case "Str":
            return [ir.Text(str(c))]
        case "Space":
            return [ir.Text(" ")]
        case "SoftBreak":
            return [ir.SoftBreak()]
        case "LineBreak":
            return [ir.LineBreak()]
        case "Strong" | "Emph" | "Strikeout" | "Superscript" | "Subscript":
            children = c if isinstance(c, list) else []
            return [ir.Emphasis(_EMPH_MAP[t], _inlines(children, ctx))]
        case "Underline" | "SmallCaps":  # production unwraps to plain text
            return _inlines(c if isinstance(c, list) else [], ctx)
        case "Quoted" if isinstance(c, list):
            qt, quoted = c
            single = isinstance(qt, dict) and qt.get("t") == "SingleQuote"
            return [ir.Quoted(single, _inlines(quoted, ctx))]
        case "Code" if isinstance(c, list):
            return [ir.Code(str(c[1]))]
        case "Link" if isinstance(c, list):
            _attr, label, target = c
            return [ir.Link(_inlines(label, ctx), str(target[0]))]
        case "Image" if isinstance(c, list):
            _attr, label, target = c
            return [ir.ImageInline(src=str(target[0]), alt=_plain(label))]
        case "Span" if isinstance(c, list):
            # Production unwraps a Span, EXCEPT a `dir` attribute (Hebrew/Arabic
            # bidi) governs visual ordering, so it survives as `DirectionalSpan`.
            # `attr` is `[id, classes, [(k, v), ...]]`; only `dir` is preserved.
            attr, span = c
            direction = ""
            if isinstance(attr, list) and len(attr) == 3 and isinstance(attr[2], list):
                for pair in attr[2]:
                    if isinstance(pair, list) and len(pair) == 2 and pair[0] == "dir":
                        direction = str(pair[1])
            children = _inlines(span, ctx)
            if direction:
                return [ir.DirectionalSpan(direction=direction, children=children)]
            return children
        case "Note" if isinstance(c, list):
            # `c` is footnote body blocks. Renumber densely by reference order so the
            # id never depends on Word's internal `w:id`.
            ctx.fn_index += 1
            idx = ctx.fn_index
            ctx.fn_defs.append((idx, [_block(b, ctx) for b in c]))
            return [ir.FootnoteRef(raw_index=idx, id=idx)]
        case "RawInline" if isinstance(c, list):
            fmt, raw = c
            return [ir.Text(str(raw))] if fmt in {"html", "markdown"} else []
        case _:
            if isinstance(c, list):
                return [ir.UnknownInline(note=str(t), children=_inlines(c, ctx))]
            return [ir.UnknownInline(note=str(t))]


def _plain(nodes: list[dict[str, Any]]) -> str:
    """Plain-text flatten of inlines (image alt + table cells)."""
    out: list[str] = []
    for node in nodes:
        t = node.get("t")
        c = node.get("c")
        match t:
            case "Str":
                out.append(str(c))
            case "Space" | "SoftBreak" | "LineBreak":
                out.append(" ")
            case _ if t in _EMPH_MAP or t in {"Underline", "SmallCaps", "Span"}:
                payload = c[1] if t == "Span" and isinstance(c, list) else c
                out.append(_plain(payload if isinstance(payload, list) else []))
            case "Quoted" if isinstance(c, list):
                out.append(_plain(c[1]))
            case "Code" if isinstance(c, list):
                out.append(str(c[1]))
            case "Link" | "Image" if isinstance(c, list):
                out.append(_plain(c[1]))
            case _ if isinstance(c, list):
                out.append(_plain(c))
    return "".join(out).strip()


def _node_plain(value: object) -> str:
    """Best-effort readable text of an arbitrary Pandoc node/subtree, so an
    UnknownBlock carries its content instead of dropping it at lowering.

    Structure-agnostic (never assumes the kind's `c` is inlines vs blocks): walks
    dicts/lists generically — a `Str` contributes its text, spacing nodes a space,
    any other `c` list recurses. Inert kinds (e.g. `Null`) yield `""`."""
    parts: list[str] = []

    def walk(v: object) -> None:
        nd = _node(v)
        if nd is not None:
            t = nd.get("t")
            c = nd.get("c")
            if t == "Str":
                parts.append(str(c))
            elif t in {"Space", "SoftBreak", "LineBreak"}:
                parts.append(" ")
            elif isinstance(c, (list, dict)):
                walk(c)
        elif isinstance(v, list):
            for item in v:
                walk(item)

    walk(value)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


# ---------------------------------------------------------------------------
# Block lowering: Pandoc block node -> IR Block
# ---------------------------------------------------------------------------


def _block(node: dict[str, Any], ctx: _Ctx) -> ir.Block:
    # Dispatch on Pandoc's string tag; the `isinstance(c, list)` guards inside arms
    # are intrinsic — `c` is positional Pandoc JSON, not a typed shape.
    t = node.get("t")
    c = node.get("c")
    match t:
        case "Header" if isinstance(c, list):
            level, _attr, inlines = c
            return ir.Heading(level=int(level), inlines=_inlines(inlines, ctx))
        case "Para" | "Plain":
            inlines = c if isinstance(c, list) else []
            if not inlines:
                return ir.Paragraph(inlines=[], empty=True)
            para = ir.Paragraph(inlines=_inlines(inlines, ctx))
            para.italic = _all_italic(inlines)
            return para
        case "HorizontalRule":
            return ir.ThematicBreak()
        case "BlockQuote" if isinstance(c, list):
            return ir.BlockQuote(blocks=[_block(b, ctx) for b in c])
        case "BulletList" if isinstance(c, list):
            return ir.ListBlock(ordered=False, items=[[_block(b, ctx) for b in item] for item in c])
        case "OrderedList" if isinstance(c, list):
            attr, items = c  # attr = [start, style, delim]; keep the source start ordinal
            start = int(attr[0]) if isinstance(attr, list) and attr else 1
            return ir.ListBlock(
                ordered=True, start=start,
                items=[[_block(b, ctx) for b in item] for item in items],
            )
        case "LineBlock" if isinstance(c, list):
            # Verse-like lines (each a list of inlines) — real reading content, so a
            # one-stanza VerseBlock rather than an UnknownBlock lowering would drop.
            stanza = [_inlines(line, ctx) for line in c if isinstance(line, list)]
            return ir.VerseBlock(stanzas=[stanza])
        case "CodeBlock" if isinstance(c, list):
            _attr, text = c
            return ir.CodeBlock(text=str(text))
        case "Table":
            return _table(node, ctx)
        case "Div" if isinstance(c, list):
            # Production unwraps Divs; a transparent container (role "_div") keeps the
            # structure until lowering inlines its children.
            _attr, blocks = c
            return ir.BlockQuote(blocks=[_block(b, ctx) for b in (blocks or [])], role="_div")
        case "Figure" if isinstance(c, list):
            # Pandoc 3.x wraps a standalone image: [attr, caption, content_blocks].
            # Unwrap to a transparent container of content blocks plus the caption,
            # so neither the image nor its caption is lost.
            _attr, caption, content = c
            inner: list[ir.Block] = [_block(b, ctx) for b in (content or [])]
            cap_blocks = caption[1] if isinstance(caption, list) and len(caption) > 1 else None
            if cap_blocks:
                inner.extend(_block(b, ctx) for b in cap_blocks)
            return ir.BlockQuote(blocks=inner, role="_div")
        case _:
            # Unmodelled kind: preserve best-effort reading text (lowering emits it +
            # surfaces a diagnostic) so content is never silently dropped.
            return ir.UnknownBlock(note=str(t), text=_node_plain(c))


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
        match t:
            case "Str":
                out.append(str(c))
            case "Space" | "SoftBreak" | "LineBreak":
                out.append(" ")
            case "Strong" | "Emph" | "Strikeout" | "Superscript" | "Subscript" if isinstance(c, list):
                o, cl = _EMPH_WRAP[t]
                out.append(f"{o}{_inline_md(c)}{cl}")
            case ("Underline" | "SmallCaps") if isinstance(c, list):
                out.append(_inline_md(c))
            case "Quoted" if isinstance(c, list):
                qt, quoted = c
                o, cl = ("'", "'") if isinstance(qt, dict) and qt.get("t") == "SingleQuote" else ("«", "»")
                out.append(f"{o}{_inline_md(quoted)}{cl}")
            case "Code" if isinstance(c, list):
                out.append(f"`{c[1]}`")
            case "Link" if isinstance(c, list):
                _a, label, target = c
                out.append(f"[{_inline_md(label)}]({target[0]})")
            case "Image" if isinstance(c, list):
                _a, label, target = c
                out.append(f"![{_plain(label)}]({target[0]})")
            case "Span" if isinstance(c, list):
                out.append(_inline_md(c[1]))
            case _ if isinstance(c, list):
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
        # cell = [attr, alignment, rowspan, colspan, blocks]; narrow before indexing.
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

    `w:jc` alignment is assigned onto the top-level `Paragraph` blocks by CONTENT
    (`reconcile_alignment`); a `warning` fires when right-aligned source paragraphs
    exist but none reconcile, so a future drift can't ship silently. Footnote
    definitions collected during the inline walk are attached densely renumbered.
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
    # Right-aligned source paragraphs that none reconciled onto the AST — a warning
    # the caller propagates so a future drift fails loud.
    if right_records and not right_assigned:
        doc.diagnostics.append(ir.Diagnostic(
            "warning", "import.align-unreconciled",
            f"{right_records} right-aligned source paragraph(s) but 0 reconciled "
            f"onto the AST — alignment-driven signatures/epigraphs may be lost",
        ))

    doc.footnotes = [ir.FootnoteDef(id=i, blocks=bs) for i, bs in ctx.fn_defs]
    return doc
