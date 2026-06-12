# import-pure: no filesystem mutation
"""Endmatter passes: bibliography lift, endmatter-section strip, bare-heading strip."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import replace
from typing import Any, cast

from pancratius import ir
from pancratius.content_catalog import IndexHit
from pancratius.ir.inlines import inline_plain
from pancratius.passes.scrub import head_region_end, is_ai_alt

# The slug→(slug, number, kind) corpus index the bibliography lift resolves
# titles against; an entry resolves to a `{kind, number}` target.
type _SlugLookup = Mapping[str, IndexHit]

_COPYRIGHT_HEADING_RE = re.compile(r"^(?:copyright|копирайт)\s*$", re.IGNORECASE)
_CONTACTS_HEADING_RE = re.compile(r"^(?:contacts|контакты)\s*$", re.IGNORECASE)

# Endmatter bibliography/catalog heading whose lifted section is dropped from the body.
_BIBLIO_HEADING_RE = re.compile(
    r"^(?:библиография|bibliography|список\s+литературы|литература)\s*$",
    re.IGNORECASE,
)

# A Pandoc JSON node `{"t": ..., "c": ...}`. `_node` views an opaque value as one
# when it is a dict, so `.get("t")`/`["c"]` are str-keyed (a bare `isinstance`
# narrow yields `dict[Unknown, Unknown]`, whose keys ty types as `Never`).
type _PandocNode = dict[str, Any]


def _node(value: object) -> _PandocNode | None:
    return cast("_PandocNode", value) if isinstance(value, dict) else None


# ---------------------------------------------------------------------------
# 4. bibliography table classification + lift
# ---------------------------------------------------------------------------


def lift_bibliography(
    doc: ir.Document,
    slug_lookup: _SlugLookup | None,
    diagnostics: ir.DiagnosticSink,
) -> ir.Document:
    """Lift catalog/bibliography tables out of the body into the returned
    document's `bibliography`.

    Classification is on the actual catalog signal (cover images / LitRes / kindbook
    URLs), not a row count: reading-content tables (scripture/archetype grids) carry
    neither and are kept in the body."""
    lookup = slug_lookup or {}
    kept: list[ir.Block] = []
    lifted: list[dict[str, object]] = []
    for b in doc.blocks:
        if isinstance(b, ir.Table) and _looks_like_biblio(b):
            lifted.extend(_parse_biblio(b, lookup))
            continue
        kept.append(b)
    bibliography = [*doc.bibliography, *lifted]
    if bibliography:
        diagnostics.append(ir.Diagnostic(
            "warning", "import.bibliography",
            f"{len(bibliography)} entries lifted to the bibliography sidecar",
        ))
    return replace(doc, blocks=kept, bibliography=bibliography)


def _raw_table_text(node: object) -> str:
    return json.dumps(node, ensure_ascii=False)


def _renders_as_html_table(t: ir.Table) -> bool:
    """True when Pandoc's GFM writer would emit this table as raw HTML `<table>`
    rather than a pipe table — the set the bibliography lift considers.

    Pandoc renders simple grids (single-block cells, no spans, no caption) as pipe
    tables, kept in the body as reading content; it falls back to HTML for a
    multi-block cell, a row/col span ≠ 1, or a caption — the richer shape a catalog
    table has. Pinned to the current Pandoc GFM writer (pandoc 3.9); if pandoc is
    bumped, re-confirm against the new writer (the goldens pin the lift outcome)."""
    node = _node(t.raw)  # opaque Pandoc Table node
    if node is None:
        return False
    c = node.get("c")
    if not isinstance(c, list) or len(c) != 6:
        return False
    _attr, caption, _cols, thead, tbodies, _tfoot = c
    if isinstance(caption, list) and len(caption) > 1 and caption[1]:
        return True  # a caption can't be expressed in a pipe table

    def cell_forces_html(cell: object) -> bool:
        # cell = [attr, alignment, rowspan, colspan, blocks]
        if not isinstance(cell, list) or len(cell) < 5:
            return False
        span2, span3 = _node(cell[2]), _node(cell[3])
        rowspan = span2["c"] if span2 else cell[2]
        colspan = span3["c"] if span3 else cell[3]
        if rowspan != 1 or colspan != 1:
            return True
        return isinstance(cell[4], list) and len(cell[4]) > 1  # multi-block cell

    def any_row_forces_html(rows: object) -> bool:
        if not isinstance(rows, list):
            return False
        for row in rows:
            if (
                isinstance(row, list)
                and len(row) > 1
                and isinstance(row[1], list)
                and any(cell_forces_html(cell) for cell in row[1])
            ):
                return True
        return False

    if isinstance(thead, list) and len(thead) > 1 and any_row_forces_html(thead[1]):
        return True
    if isinstance(tbodies, list):
        for tbody in tbodies:
            if isinstance(tbody, list) and len(tbody) > 3 and any_row_forces_html(tbody[3]):
                return True
    return False


def _looks_like_biblio(t: ir.Table) -> bool:
    """A catalog/bibliography table to lift: a catalog signal (cover images / LitRes
    / kindbook URLs) AND Pandoc would render it as an HTML table. A reading-content
    grid (a pipe table) is never lifted, even if it embeds a thumbnail."""
    if not _renders_as_html_table(t):
        return False
    raw = _raw_table_text(t.raw)
    return '"Image"' in raw or "litres.ru" in raw or "kindbook.net" in raw


_A_RE = re.compile(r"litres\.ru|kindbook\.net")


def _resolve_target(title: str, slug_lookup: _SlugLookup) -> dict[str, object] | None:
    """Resolve a title to a `{kind, number}` target when the corpus knows it.
    The record stays an open dict (it travels into `doc.bibliography`)."""
    key = re.sub(r"\s+", " ", title.lower()).strip()
    got = slug_lookup.get(key) or slug_lookup.get(key.rstrip(".")) or slug_lookup.get(f"{key}.")
    if not got:
        return None
    if got.number is not None and got.kind:
        return {"kind": got.kind, "number": got.number}
    return None


def _parse_biblio(t: ir.Table, slug_lookup: _SlugLookup) -> list[dict[str, object]]:
    """Pull entries from the structured table by walking the raw Pandoc node for
    store-link titles and (non-AI) cover-image alts."""
    titles: list[tuple[str, str | None]] = []

    def walk(value: object) -> None:  # opaque Pandoc node
        node = _node(value)
        if node is not None:
            payload = node.get("c")
            if node.get("t") == "Link" and isinstance(payload, list) and len(payload) == 3:
                _attr, label, target = payload
                href = str(target[0]) if isinstance(target, list) and target else ""
                if _A_RE.search(href):
                    title = _flat(label)
                    if title and len(title) >= 2:
                        titles.append((title, href))
            elif node.get("t") == "Image" and isinstance(payload, list) and len(payload) == 3:
                _attr, label, _target = payload
                alt = _flat(label)
                if alt and len(alt) > 2 and not is_ai_alt(alt):
                    titles.append((alt, None))
            if isinstance(payload, list):
                for v in payload:
                    walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(t.raw)
    out: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for title, href in titles:
        key = (title, href or "")
        if key in seen:
            continue
        seen.add(key)
        entry: dict[str, object] = {"title": title}
        if href:
            entry["source_url"] = href
        target = _resolve_target(title, slug_lookup)
        if target:
            entry["target"] = target
        out.append(entry)
    return out


def _flat(label: object) -> str:  # opaque Pandoc inline list; narrowed below
    out: list[str] = []
    for item in label if isinstance(label, list) else []:
        n = _node(item)
        if n is None:
            continue
        t = n.get("t")
        c = n.get("c")
        if t == "Str":
            out.append(str(c))
        elif t in {"Space", "SoftBreak", "LineBreak"}:
            out.append(" ")
        elif isinstance(c, list):
            out.append(_flat(c))
    return re.sub(r"\s+", " ", "".join(out)).strip()


# ---------------------------------------------------------------------------
# 5. bare bibliography heading strip (after the table was lifted)
# ---------------------------------------------------------------------------


def strip_bare_bibliography_heading(blocks: list[ir.Block]) -> list[ir.Block]:
    """Drop an endmatter `Библиография`/`Bibliography` heading whose section body
    (the catalog table) was lifted to the sidecar, leaving the heading orphaned.

    A heading is dropped when its remaining section (up to the next heading) holds
    no reading content — only empty paragraphs / thematic breaks (the post-lift
    bibliography-section drop)."""
    out: list[ir.Block] = []
    i = 0
    n = len(blocks)
    while i < n:
        b = blocks[i]
        if isinstance(b, ir.Heading) and _BIBLIO_HEADING_RE.match(inline_plain(b.inlines)):
            j = i + 1
            has_content = False
            while j < n and not isinstance(blocks[j], ir.Heading):
                nxt = blocks[j]
                if isinstance(nxt, ir.Paragraph) and not nxt.empty:
                    has_content = True
                elif not isinstance(nxt, (ir.Paragraph, ir.ThematicBreak)):
                    has_content = True
                j += 1
            if not has_content:
                i = j  # drop the heading and its empty trailing section
                continue
        out.append(b)
        i += 1
    return out


def _is_endmatter_heading(title: str) -> bool:
    return bool(
        _COPYRIGHT_HEADING_RE.match(title)
        or _BIBLIO_HEADING_RE.match(title)
        or _CONTACTS_HEADING_RE.match(title)
    )


def _tail_region_start(blocks: list[ir.Block]) -> int:
    n = len(blocks)
    return max(0, min(int(n * 0.75), n - 80))


def strip_endmatter(blocks: list[ir.Block]) -> list[ir.Block]:
    """Drop heading-delimited publisher endmatter from import output.

    Copyright/contact sections are deliberately not an "anywhere" heading scrub:
    they must be anchored in source headmatter or tailmatter. Bibliography/catalog
    headings are different; body bibliography belongs in the sidecar, so any
    remaining heading-delimited bibliography section is removed. After the first
    anchored endmatter section, adjacent endmatter headings are stripped too.
    """
    n = len(blocks)
    if n == 0:
        return blocks
    head_end = head_region_end(blocks)
    tail_start = _tail_region_start(blocks)
    out: list[ir.Block] = []
    i = 0
    in_endmatter = False
    while i < n:
        b = blocks[i]
        if isinstance(b, ir.Heading) and _is_endmatter_heading(inline_plain(b.inlines)):
            title = inline_plain(b.inlines)
            anchored = (
                _BIBLIO_HEADING_RE.match(title) is not None
                or i < head_end
                or i >= tail_start
                or in_endmatter
            )
            if anchored:
                in_endmatter = True
                level = b.level
                i += 1
                while i < n:
                    current = blocks[i]
                    if isinstance(current, ir.Heading) and current.level <= level:
                        break
                    i += 1
                continue
        out.append(b)
        i += 1
    return out
