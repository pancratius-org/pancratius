# import-pure: no filesystem mutation
"""Endmatter passes: bibliography lift, endmatter-section strip, bare-heading strip."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from dataclasses import replace

from pancratius import ir
from pancratius.content_catalog import IndexHit
from pancratius.ir.inlines import inline_plain
from pancratius.passes.scrub import is_ai_alt

# The slug→(slug, number, kind) corpus index the bibliography lift resolves
# titles against; an entry resolves to a `{kind, number}` target.
type _SlugLookup = Mapping[str, IndexHit]

_COPYRIGHT_HEADING_RE = re.compile(r"^(?:copyright|копирайт)\s*$", re.IGNORECASE)
_CONTACTS_HEADING_RE = re.compile(r"^(?:contacts|контакты)\s*$", re.IGNORECASE)


def _head_region_end(blocks: list[ir.Block]) -> int:
    """Exclusive end of headmatter: first H1 or a bounded document prefix."""
    n = len(blocks)
    first_h1 = next((i for i, b in enumerate(blocks) if isinstance(b, ir.Heading) and b.level == 1), n)
    return min(first_h1, max(20, int(n * 0.03)))

# Endmatter bibliography/catalog heading whose lifted section is dropped from the body.
_BIBLIO_HEADING_RE = re.compile(
    r"^(?:библиография|bibliography|список\s+литературы|литература)\s*$",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# bibliography table classification + lift
# ---------------------------------------------------------------------------


def lift_bibliography(
    doc: ir.Document,
    slug_lookup: _SlugLookup,
    diagnostics: ir.DiagnosticSink,
) -> ir.Document:
    """Lift catalog/bibliography tables out of the body into the returned
    document's `bibliography`.

    Classification is on the actual catalog signal (cover images / LitRes / kindbook
    URLs), not a row count: reading-content tables (scripture/archetype grids) carry
    neither and are kept in the body."""
    kept: list[ir.Block] = []
    lifted: list[dict[str, object]] = []
    for b in doc.blocks:
        if isinstance(b, ir.Table) and _looks_like_biblio(b):
            lifted.extend(_parse_biblio(b, slug_lookup))
            continue
        kept.append(b)
    bibliography = [*doc.bibliography, *lifted]
    if bibliography:
        diagnostics.append(ir.Diagnostic(
            "warning", "import.bibliography",
            f"{len(bibliography)} entries lifted to the bibliography sidecar",
        ))
    return replace(doc, blocks=kept, bibliography=bibliography)


def _renders_as_html_table(t: ir.Table) -> bool:
    """Whether the factual table shape needs richer-than-pipe treatment."""
    return t.shape.complex


def _table_inlines(table: ir.Table) -> list[ir.Inline]:
    return [inline for row in table.rows for cell in row for inline in cell]


def _walk_inlines(inlines: list[ir.Inline]) -> Iterator[ir.Inline]:
    for inline in inlines:
        yield inline
        if isinstance(inline, ir.ContainerInline):
            yield from _walk_inlines(inline.children)


def _looks_like_biblio(t: ir.Table) -> bool:
    """A catalog/bibliography table to lift: a catalog signal (cover images / LitRes
    / kindbook URLs) AND its source shape needs an HTML table. A reading-content
    grid (a pipe table) is never lifted, even if it embeds a thumbnail."""
    if not _renders_as_html_table(t):
        return False
    return any(
        isinstance(inline, ir.ImageInline)
        or (isinstance(inline, ir.Link) and _A_RE.search(inline.target))
        for inline in _walk_inlines(_table_inlines(t))
    )


_A_RE = re.compile(r"litres\.ru|kindbook\.net")
_GENERIC_IMAGE_ALT_RE = re.compile(
    r"^(?:рисунок|figure|picture|image)\s*\d+$",
    re.IGNORECASE,
)


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
    """Pull store-link titles and non-AI cover alts from structured cells."""
    titles: list[tuple[str, str | None]] = []
    for inline in _walk_inlines(_table_inlines(t)):
        if isinstance(inline, ir.Link) and _A_RE.search(inline.target):
            title = inline_plain(inline.children).strip()
            if len(title) >= 2:
                titles.append((title, inline.target))
        elif isinstance(inline, ir.ImageInline):
            alt = inline.alt.strip()
            if (
                len(alt) > 2
                and not is_ai_alt(alt)
                and _GENERIC_IMAGE_ALT_RE.fullmatch(alt) is None
            ):
                titles.append((alt, None))
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


# ---------------------------------------------------------------------------
# bare bibliography heading strip (after the table was lifted)
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

    Copyright/contact sections are not an "anywhere" heading scrub:
    they must be anchored in source headmatter or tailmatter. Bibliography/catalog
    headings are different; body bibliography belongs in the sidecar, so any
    remaining heading-delimited bibliography section is removed. After the first
    anchored endmatter section, adjacent endmatter headings are stripped too.
    """
    n = len(blocks)
    if n == 0:
        return blocks
    head_end = _head_region_end(blocks)
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
