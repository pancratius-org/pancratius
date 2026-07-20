# import-pure: no filesystem mutation
"""Reading-text and inline-tree helpers shared across the import pipeline."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import assert_never

from pancratius import ir


def inline_plain(inlines: list[ir.Inline]) -> str:
    """Flatten inlines to a single whitespace-collapsed reading-text string."""
    out: list[str] = []

    def append(items: list[ir.Inline]) -> None:
        for item in items:
            match item:
                case ir.Text():
                    out.append(item.value)
                case ir.LineBreak():
                    out.append(" ")
                case ir.Code():
                    out.append(item.value)
                case (
                    ir.Emphasis()
                    | ir.Link()
                    | ir.DirectionalSpan()
                    | ir.UnknownInline()
                ):
                    append(item.children)
                case ir.ImageInline():
                    out.append(item.alt)
                case ir.FootnoteRef():
                    pass  # a ref carries no reading text
                case _:
                    assert_never(item)

    append(inlines)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def block_plain(block: ir.Block) -> str:
    """Presentation-free reading text for one block tree."""
    match block:
        case ir.Heading() | ir.Paragraph():
            return inline_plain(block.inlines)
        case ir.LineatedBlock():
            return " ".join(
                inline_plain(line.inlines)
                for stanza in block.stanzas
                for line in stanza
            )
        case ir.Signature():
            return " ".join(block.lines)
        case ir.Epigraph():
            return " ".join([*block.quote, *block.footer])
        case ir.DialogueLabel():
            return block.speaker
        case ir.ThematicBreak():
            return "***"
        case ir.QuoteBlock():
            return " ".join(block_plain(child) for child in block.blocks)
        case ir.ListBlock():
            return " ".join(
                block_plain(child)
                for item in block.items
                for child in item
            )
        case ir.Table():
            return " ".join(inline_plain(cell) for row in block.rows for cell in row)
        case ir.ImageBlock():
            return block.alt
        case ir.UnknownBlock():
            return block.text
        case _:
            assert_never(block)


def blocks_as_inlines(blocks: list[ir.Block]) -> list[ir.Inline]:
    """Flatten block content for an IR slot that only admits rich inlines.

    DOCX table cells use this when a source cell contains several paragraphs or
    nested containers. A single exhaustive owner keeps that lossy boundary in
    sync with the closed block vocabulary.
    """
    out: list[ir.Inline] = []
    for block in blocks:
        inlines = _block_as_inlines(block)
        if out and inlines:
            out.append(ir.Text(" "))
        out.extend(inlines)
    return out


def _block_as_inlines(block: ir.Block) -> list[ir.Inline]:
    match block:
        case ir.Heading() | ir.Paragraph():
            return block.inlines
        case ir.LineatedBlock():
            out: list[ir.Inline] = []
            for stanza in block.stanzas:
                for line in stanza:
                    if out:
                        out.append(ir.Text(" "))
                    out.extend(line.inlines)
            return out
        case ir.QuoteBlock():
            return blocks_as_inlines(block.blocks)
        case ir.ListBlock():
            return blocks_as_inlines([
                member for item in block.items for member in item
            ])
        case ir.ImageBlock():
            return [ir.ImageInline(block.src, block.alt, block.asset_id)]
        case ir.UnknownBlock():
            children: list[ir.Inline] = [ir.Text(block.text)] if block.text else []
            return [ir.UnknownInline(block.note, children)]
        case ir.Signature():
            return [ir.Text(" ".join(block.lines))]
        case ir.Epigraph():
            return [ir.Text(" ".join([*block.quote, *block.footer]))]
        case ir.DialogueLabel():
            return [ir.Text(block.speaker)]
        case ir.ThematicBreak():
            return [ir.Text("***")]
        case ir.Table():
            return [
                inline
                for row in block.rows
                for cell in row
                for inline in cell
            ]
        case _ as unreachable:
            assert_never(unreachable)


def inline_lines(inlines: list[ir.Inline]) -> list[list[ir.Inline]]:
    """Split inlines into display lines (sub-inline lists), recursing through
    container inlines so a `LineBreak` nested inside an `Emph` span still splits the
    line (a fully-italic verse paragraph keeps its hard breaks inside the span).

    Literal wrapping inside a text node is normalized to a space by the source
    adapter. Only an authored Word line break reaches this function."""
    lines: list[list[ir.Inline]] = [[]]
    for n in inlines:
        # isinstance, not match: the container arm tests `ir.ContainerInline`
        # (a runtime tuple), which can't appear in a `case`.
        if isinstance(n, ir.LineBreak):
            lines.append([])
        elif isinstance(n, ir.ContainerInline):
            # Re-wrap each produced line fragment in the container so the surviving
            # fragments stay emphasized across the split.
            child = inline_lines(n.children)
            for idx, frag in enumerate(child):
                if idx:
                    lines.append([])
                lines[-1].append(ir.rebuild_container(n, frag))
        else:
            lines[-1].append(n)
    return [ln for ln in lines if ln]


def walk_inlines(inlines: list[ir.Inline]) -> Iterator[ir.Inline]:
    """Depth-first inline tree walk for kind probes."""
    for n in inlines:
        yield n
        if isinstance(n, ir.ContainerInline):
            yield from walk_inlines(n.children)
