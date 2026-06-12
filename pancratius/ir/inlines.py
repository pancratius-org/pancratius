# import-pure: no filesystem mutation
"""Inline-tree helpers shared by the adapter, passes, lowerer, and inspector."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import assert_never

from pancratius import ir


def inline_plain(inlines: list[ir.Inline]) -> str:
    """Flatten inlines to a single whitespace-collapsed reading-text string."""
    out: list[str] = []
    for n in inlines:
        match n:
            case ir.Text():
                out.append(n.value)
            case ir.SoftBreak() | ir.LineBreak():
                out.append(" ")
            case ir.Quoted():
                o, c = ("'", "'") if n.kind == "single" else ("«", "»")
                out.append(o + inline_plain(n.children) + c)
            case ir.Code():
                out.append(n.value)
            case ir.Emphasis() | ir.Link() | ir.DirectionalSpan() | ir.UnknownInline():
                out.append(inline_plain(n.children))
            case ir.ImageInline():
                out.append(n.alt)
            case ir.FootnoteRef():
                pass  # a ref carries no reading text
            case _:
                assert_never(n)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def inline_lines(
    inlines: list[ir.Inline], *, soft_break: bool = True
) -> list[list[ir.Inline]]:
    """Split inlines into display lines (sub-inline lists), recursing through
    container inlines so a `LineBreak` nested inside an `Emph` span still splits the
    line (a fully-italic verse paragraph keeps its hard breaks inside the span).

    `soft_break` selects a `SoftBreak`'s meaning: a display-line boundary (default,
    for signature/epigraph extraction) or prose wrapping joined as a space (verse
    detection passes `soft_break=False`). Pandoc emits `SoftBreak` for a literal
    `\\r\\n` inside one `<w:t>` run — prose wrapping, not a hard `<w:br/>` — so verse
    detection must not treat it as a verse-line boundary; only a hard `LineBreak`
    is."""
    lines: list[list[ir.Inline]] = [[]]
    for n in inlines:
        # isinstance, not match: the container arm tests `ir.ContainerInline`
        # (a runtime tuple), which can't appear in a `case`.
        if isinstance(n, ir.LineBreak):
            lines.append([])
        elif isinstance(n, ir.SoftBreak):
            if soft_break:
                lines.append([])
            else:
                lines[-1].append(ir.Text(" "))  # wrapping → a joining space
        elif isinstance(n, ir.ContainerInline):
            # Re-wrap each produced line fragment in the container so the surviving
            # fragments stay emphasized across the split.
            child = inline_lines(n.children, soft_break=soft_break)
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
