"""DOCX → block IR (the one source adapter).

The parse stage of `docs/import-pipeline.md`: turn a DOCX into the typed IR and
stop. No Markdown string is produced here.

The primary parse is `pandoc --from docx+empty_paragraphs --to json`;
`+empty_paragraphs` keeps Word's empty paragraphs as `Para []` so stanza breaks
survive into the IR. Provenance is harvested from the projection's source
anchors (`docx_pandoc.source_anchor_name`): every leaf built from a content
`w:p` carries the ordinal(s) it renders, at any nesting depth. Shapes outside
that scope (table cells, code-converted paragraphs) surface through the
`import.provenance-unclaimed` diagnostic rather than silently. The OOXML
paragraph facts Pandoc drops (`w:jc`, borders, visual lineation groups) join
by ordinal.

NOT `import-pure`: composition delegates package projection and the Pandoc process
to `docx_pandoc`; downstream IR stages stay pure. Footnotes arrive as inline `Note` nodes and are lowered to
`FootnoteRef`/`FootnoteDef` pairs renumbered densely by reference order.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any, NamedTuple, cast

from pancratius import docx_pandoc, docx_source, ir
from pancratius.docx_source import SourceParagraph


class ProvenanceError(RuntimeError):
    """Exact anchor lineage and span projections disagree — identity is known
    false, and every downstream consumer would treat the corrupt claim as
    truth, so the import stops instead."""

# ---------------------------------------------------------------------------
# Source anchors: provenance harvested from the projection's bookmarks
# ---------------------------------------------------------------------------

def _anchor_span(ordinals: Sequence[docx_source.SourceOrdinal]) -> ir.SourceSpan | None:
    return ir.SourceSpan(min(ordinals), max(ordinals)) if ordinals else None


def _source_provenance(
    source: docx_source.DocxSourceDocument | None,
    ordinals: Sequence[docx_source.SourceOrdinal],
) -> ir.SourceProvenance | None:
    if not ordinals:
        return None
    if source is None:
        return _anchor_span(ordinals)
    lines = tuple(
        coordinate
        for ordinal in ordinals
        for coordinate in source.paragraph(docx_source.ParagraphOrdinal(ordinal)).line_coordinates
    )
    return ir.SourceProvenance.for_lines(lines) if lines else _anchor_span(ordinals)


def _member_span(members: Sequence[ir.Block]) -> ir.SourceSpan | None:
    """A container's derived span: the range its anchored members prove.
    Span-less members (empty paragraphs are never anchored) don't poison it."""
    present = [span for m in members if (span := m.source_span) is not None]
    return ir.merge_source_spans(present) if present else None


def _paragraph_facts(
    block: ir.Paragraph, consumed: Sequence[SourceParagraph]
) -> tuple[ir.SourceFacts, bool]:
    """The OOXML facts a paragraph's consumed source records prove, plus whether
    right alignment was newly assigned (the signature/epigraph signal the
    `import.align-unreconciled` rail counts).

    Strict agreement: every text-bearing consumed record must carry the SAME
    border kind. A Pandoc-fused block spanning bordered and plain source rows
    stays unbordered — assigning the border would drag the plain text into a
    set-apart register. Same discipline for visual-continuity groups."""
    facts = block.facts
    if any(record.indent_departure for record in consumed):
        facts = replace(facts, indented=True)
    text_borders = {record.border.value for record in consumed if record.text}
    if len(text_borders) == 1 and (kind := text_borders.pop()):
        facts = replace(facts, border=cast("ir.BorderKind", kind))
    groups = {
        record.visual_group.value
        for record in consumed
        if record.visual_group is not None
    }
    if len(groups) == 1:
        facts = replace(facts, lineation_group=groups.pop())
    right = any(r.alignment.is_right_edge for r in consumed) and not facts.align
    if right:
        facts = replace(facts, align="right")
    return facts, right


class AppliedSourceFacts(NamedTuple):
    """What the by-ordinal facts join changed: paragraphs whose facts were
    updated, and how many gained right alignment (the signature/epigraph signal)."""

    paragraphs_updated: int
    right_aligned: int


def apply_source_facts(
    blocks: list[ir.Block], records: Sequence[SourceParagraph]
) -> AppliedSourceFacts:
    """Attach OOXML paragraph facts by exact ordinal onto top-level `Paragraph`s.

    Top-level only: widening facts into container members is a
    register-detection decision, not an identity one.
    """
    by_ordinal = {int(record.ordinal): record for record in records}
    applied = 0
    right_total = 0
    for i, block in enumerate(blocks):
        if not isinstance(block, ir.Paragraph) or block.source_span is None or block.empty:
            continue
        # Only content records vote: a fused span covers removed/blank interior
        # ordinals whose layout facts the block never rendered.
        consumed = [
            record
            for ordinal in range(block.source_span.start, block.source_span.end + 1)
            if (record := by_ordinal.get(ordinal)) is not None
            and record.disposition is docx_source.ParagraphDisposition.CONTENT
        ]
        if not consumed:
            continue
        facts, right = _paragraph_facts(block, consumed)
        right_total += int(right)
        if facts != block.facts:
            applied += 1
            blocks[i] = replace(block, facts=facts)
    return AppliedSourceFacts(applied, right_total)


# ---------------------------------------------------------------------------
# Inline lowering: Pandoc inline node -> IR Inline
# ---------------------------------------------------------------------------

_EMPH_MAP: dict[str, ir.EmphKind] = {
    "Strong": "strong", "Emph": "emph", "Strikeout": "strike",
    "Superscript": "sup", "Subscript": "sub",
}


class _Ctx:
    """Per-document state threaded through the inline/block walk: the running
    footnote index, the footnote definitions collected in reference order, the
    anchor-alias recovery map, and the source ordinals claimed so far."""

    def __init__(
        self,
        anchor_aliases: dict[docx_pandoc.AnchorAlias, docx_source.SourceOrdinal] | None = None,
        *,
        source: docx_source.DocxSourceDocument | None = None,
    ) -> None:
        self.fn_index = 0
        self.fn_defs: list[tuple[int, list[ir.Block]]] = []
        self.anchor_aliases: dict[docx_pandoc.AnchorAlias, docx_source.SourceOrdinal] = (
            anchor_aliases or {}
        )
        self.source = source
        self.claimed: set[docx_source.SourceOrdinal] = set()

    def claim(self, ordinals: Sequence[docx_source.SourceOrdinal]) -> ir.SourceSpan | None:
        self.claimed.update(ordinals)
        return self.provenance(ordinals)

    def provenance(
        self, ordinals: Sequence[docx_source.SourceOrdinal]
    ) -> ir.SourceProvenance | None:
        return _source_provenance(self.source, ordinals)


def _inlines(nodes: docx_pandoc.PandocInlines, ctx: _Ctx) -> list[ir.Inline]:
    out: list[ir.Inline] = []
    for node in nodes:
        out.extend(_inline(node, ctx))
    return out


def _inline(node: docx_pandoc.PandocNode, ctx: _Ctx) -> list[ir.Inline]:
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
            kind: ir.QuoteKind = (
                "single" if isinstance(qt, dict) and qt.get("t") == "SingleQuote" else "double"
            )
            return [ir.Quoted(kind, _inlines(quoted, ctx))]
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
            ctx.fn_defs.append((idx, _blocks(c, ctx)))
            return [ir.FootnoteRef(raw_index=idx, id=idx)]
        case "RawInline" if isinstance(c, list):
            fmt, raw = c
            return [ir.Text(str(raw))] if fmt in {"html", "markdown"} else []
        case _:
            if isinstance(c, list):
                return [ir.UnknownInline(note=str(t), children=_inlines(c, ctx))]
            return [ir.UnknownInline(note=str(t))]


def _plain(nodes: docx_pandoc.PandocInlines) -> str:
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
        nd = docx_pandoc.as_node(v)
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


def _blocks(nodes: list[Any] | None, ctx: _Ctx) -> list[ir.Block]:
    """A block sequence with `Div`/`Figure` children spliced in place.

    Production unwraps Divs; splicing at parse time means a quote block in the
    IR always carries reading semantics, never plumbing. A `Figure` contributes
    its content blocks then its caption blocks, so neither is lost."""
    out: list[ir.Block] = []
    for node in nodes or []:
        t = node.get("t") if isinstance(node, dict) else None
        c = node.get("c") if isinstance(node, dict) else None
        if t == "Div" and isinstance(c, list):
            _attr, children = c
            out.extend(_blocks(children, ctx))
        elif t == "Figure" and isinstance(c, list):
            _attr, caption, content = c
            out.extend(_blocks(content, ctx))
            cap_blocks = caption[1] if isinstance(caption, list) and len(caption) > 1 else None
            if cap_blocks:
                out.extend(_blocks(cap_blocks, ctx))
        else:
            out.append(_block(node, ctx))
    return out


def _block(node: docx_pandoc.PandocNode, ctx: _Ctx) -> ir.Block:
    # Dispatch on Pandoc's string tag; the `isinstance(c, list)` guards inside arms
    # are intrinsic — `c` is positional Pandoc JSON, not a typed shape.
    t = node.get("t")
    c = node.get("c")
    match t:
        case "Div" | "Figure":
            raise AssertionError(f"{t} reaches _block; containers are spliced in _blocks")
        case "Header" if isinstance(c, list):
            level, attr, inlines = c
            ordinals, cleaned = docx_pandoc.split_inline_anchors(
                inlines if isinstance(inlines, list) else [], ctx.anchor_aliases
            )
            # A heading's own bookmark is folded into the Header id by Pandoc;
            # the farm recovery maps that id back to the ordinal.
            ident = str(attr[0]) if isinstance(attr, list) and attr else ""
            if (recovered := ctx.anchor_aliases.get(ident)) is not None:
                ordinals.append(recovered)
            return ir.Heading(
                level=int(level),
                inlines=_inlines(cleaned, ctx),
                source_span=ctx.claim(ordinals),
            )
        case "Para" | "Plain":
            ordinals, inlines = docx_pandoc.split_inline_anchors(
                c if isinstance(c, list) else [], ctx.anchor_aliases
            )
            span = ctx.claim(ordinals)
            if not inlines:
                return ir.Paragraph(
                    inlines=[], facts=ir.SourceFacts(empty=True), source_span=span
                )
            return ir.Paragraph(
                inlines=_inlines(inlines, ctx),
                facts=ir.SourceFacts(italic=_all_italic(inlines)),
                source_span=span,
            )
        case "HorizontalRule":
            return ir.ThematicBreak()
        case "BlockQuote" if isinstance(c, list):
            members = _blocks(c, ctx)
            return ir.QuoteBlock(
                blocks=members,
                register=ir.Register.ORDINARY,
                source_span=_member_span(members),
            )
        case "BulletList" if isinstance(c, list):
            items = [_blocks(item, ctx) for item in c]
            return ir.ListBlock(
                ordered=False, items=items,
                source_span=_member_span([m for item in items for m in item]),
            )
        case "OrderedList" if isinstance(c, list):
            attr, raw_items = c  # attr = [start, style, delim]; keep the source start ordinal
            start = int(attr[0]) if isinstance(attr, list) and attr else 1
            items = [_blocks(item, ctx) for item in raw_items]
            return ir.ListBlock(
                ordered=True, start=start, items=items,
                source_span=_member_span([m for item in items for m in item]),
            )
        case "LineBlock" if isinstance(c, list):
            # Pandoc `LineBlock` proves structural lineation, not verse register.
            # Normalization may promote it later if surrounding register context
            # warrants that; the adapter only preserves the authored line shape.
            ordinals: list[docx_source.SourceOrdinal] = []
            stanza: list[ir.Line] = []
            for line in c:
                if not isinstance(line, list):
                    continue
                line_ordinals, cleaned = docx_pandoc.split_inline_anchors(line, ctx.anchor_aliases)
                ordinals.extend(line_ordinals)
                stanza.append(
                    ir.Line(_inlines(cleaned, ctx), span=ctx.provenance(line_ordinals))
                )
            return ir.LineatedBlock(
                stanzas=[stanza],
                evidence=ir.LineationEvidence(pandoc_line_block=True),
                source_span=ctx.claim(ordinals),
            )
        case "CodeBlock" if isinstance(c, list):
            _attr, text = c
            return ir.CodeBlock(text=str(text))
        case "Table":
            return _table(node, ctx)
        case _:
            # Unmodelled kind: preserve best-effort reading text (lowering emits it +
            # surfaces a diagnostic) so content is never silently dropped.
            return ir.UnknownBlock(note=str(t), text=_node_plain(c))


def _all_italic(inlines: docx_pandoc.PandocInlines) -> bool:
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


def _inline_md(nodes: docx_pandoc.PandocInlines) -> str:
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


def _table(node: docx_pandoc.PandocNode, ctx: _Ctx) -> ir.Table:
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
            b = docx_pandoc.as_node(raw)
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


def adapt(
    source: docx_source.DocxSourceDocument,
    media_dir: Path,
    diagnostics: ir.DiagnosticSink,
) -> ir.Document:
    """Parse one source aggregate into IR, extracting media into `media_dir`.

    `diagnostics` is the caller's sink — the same one the passes and the backend
    take. Provenance comes from the projection's source anchors, so every
    text-bearing leaf carries its `w:p` ordinal(s) at any nesting depth; a
    `warning` fires for any content ordinal no block claims, so provenance loss
    can't ship silently. OOXML paragraph facts (`w:jc`, borders, visual groups)
    join by ordinal. Footnote definitions collected during the inline walk are
    attached densely renumbered.
    """
    ast, warns = docx_pandoc.run_json(source, media_dir)
    if warns:
        diagnostics.append(ir.Diagnostic("info", "import.pandoc-warn", warns))

    raw_blocks = ast.get("blocks") or []
    if not isinstance(raw_blocks, list):
        raw_blocks = []
    farm_targets: list[docx_pandoc.FarmLinkTarget] = []
    content_nodes: docx_pandoc.PandocBlocks = []
    for node in raw_blocks:
        targets = docx_pandoc.farm_link_targets(node)
        if targets is None:
            content_nodes.append(node)
        else:
            farm_targets.extend(targets)

    ctx = _Ctx(
        docx_pandoc.source_anchor_aliases(farm_targets, source) if farm_targets else None,
        source=source,
    )
    blocks = _blocks(content_nodes, ctx)
    facts_applied, right_assigned = apply_source_facts(blocks, source.paragraphs)

    interval_claimed: set[docx_source.SourceOrdinal] = set()
    for hit_block in blocks:
        if (hit_span := hit_block.source_span) is not None:
            interval_claimed.update(range(hit_span.start, hit_span.end + 1))
    phantom = sorted(
        (interval_claimed & source.content_ordinals) - ctx.claimed
    )
    if phantom:
        raise ProvenanceError(
            f"{len(phantom)} content paragraph(s) claimed by span interval but by no "
            f"anchor (first: {phantom[:10]})"
        )
    unclaimed = sorted(source.content_ordinals - ctx.claimed)
    diagnostics.append(ir.Diagnostic(
        "info", "import.provenance",
        f"anchors={len(ctx.claimed)} unclaimed-content={len(unclaimed)} "
        f"facts={facts_applied} right-assigned={right_assigned}",
    ))
    if unclaimed:
        diagnostics.append(ir.Diagnostic(
            "warning", "import.provenance-unclaimed",
            f"{len(unclaimed)} content paragraph(s) claimed by no block "
            f"(first: {unclaimed[:10]})",
        ))
    right_records = sum(
        1
        for r in source.reconciliation_paragraphs
        if r.alignment.is_right_edge and r.text
    )
    if right_records and not right_assigned:
        diagnostics.append(ir.Diagnostic(
            "warning", "import.align-unreconciled",
            f"{right_records} right-aligned source paragraph(s) but 0 carried onto "
            f"the IR — alignment-driven signatures/epigraphs may be lost",
        ))

    return ir.Document(
        blocks=blocks,
        footnotes=[ir.FootnoteDef(id=i, blocks=bs) for i, bs in ctx.fn_defs],
    )
