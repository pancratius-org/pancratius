# import-pure: no filesystem mutation
"""Normalization passes over the block IR (the editorial-mechanics stage).

These reproduce the GFM engine's behaviours, but operate on the typed IR directly
instead of string-patching Markdown — so a detection/normalization rule change is
a local edit here, never a ripple through parse or write (the contract in
`docs/import-pipeline.md`, "The transformation layer must be editable in one
place"). Pure: each pass is a value transformation with no filesystem access.

Passes (the order is set by `normalize`):
  * TOC drop                — `Heading`/`Paragraph` runs that are auto-TOC links
  * rights-boilerplate scrub — copyright lines in the head region
  * AI-alt scrub            — strip machine-vision alt text from images
  * bibliography lift       — catalog tables → `doc.bibliography` sidecar
  * bare bibliography heading strip — drop the heading left after the lift
  * thematic breaks         — `***` paragraphs → `ThematicBreak`
  * heading demotion        — source H1 → H2 (page title is the only H1)
  * formatting-artifact strip — empty-emphasis husks (`** **`)
  * signatures / epigraphs   — from right alignment (the `w:jc` payload)
  * dialogue labels          — canonicalize `**Speaker:**` (incl. mixed inline)
  * verse / answer blocks    — fold lineated runs from stanza structure

The AI-alt vocabulary is the production `AI_ALT_FRAGMENTS` constant, imported (not
re-derived) so the two paths can never drift.
"""

from __future__ import annotations

import json
import re
from typing import Any, cast

from lib import ir
from lib.docx_engine import AI_ALT_FRAGMENTS, RIGHTS_PATTERNS

# Pandoc JSON nodes are `{"t": ..., "c": ...}` dicts with string keys; this is the
# project-wide shape for them (see `docx_engine`). `_node` views an opaque value as
# that dict when it is one, so `.get("t")`/`["c"]` are str-keyed (an `isinstance`
# narrow alone yields `dict[Unknown, Unknown]`, whose keys ty types as `Never`).
PandocNode = dict[str, Any]


def _node(value: object) -> PandocNode | None:
    return cast("PandocNode", value) if isinstance(value, dict) else None

# ---------------------------------------------------------------------------
# small inline helpers
# ---------------------------------------------------------------------------


def inline_plain(inlines: list[ir.Inline]) -> str:
    """Flatten inlines to a single whitespace-collapsed reading-text string."""
    out: list[str] = []
    for n in inlines:
        if isinstance(n, ir.Text):
            out.append(n.value)
        elif isinstance(n, (ir.SoftBreak, ir.LineBreak)):
            out.append(" ")
        elif isinstance(n, ir.Emphasis):
            out.append(inline_plain(n.children))
        elif isinstance(n, ir.Quoted):
            o, c = ("'", "'") if n.single else ("«", "»")
            out.append(o + inline_plain(n.children) + c)
        elif isinstance(n, ir.Code):
            out.append(n.value)
        elif isinstance(n, (ir.Link, ir.DirectionalSpan, ir.UnknownInline)):
            out.append(inline_plain(n.children))
        elif isinstance(n, ir.ImageInline):
            out.append(n.alt)
        elif isinstance(n, ir.FootnoteRef):
            pass
    return re.sub(r"\s+", " ", "".join(out)).strip()


def inline_lines(
    inlines: list[ir.Inline], *, soft_break: bool = True
) -> list[list[ir.Inline]]:
    """Split inlines into display lines (sub-inline lists), RECURSING through
    container inlines (`Emphasis`/`Link`/`Quoted`/`DirectionalSpan`/`UnknownInline`).

    Recursion is the C3 fix: a fully-italic verse paragraph keeps its hard
    `LineBreak`s INSIDE the `Emph` span; the GFM/Pandoc line-exploder splits a
    line regardless of nesting, so a non-recursing top-level scan (the old shape)
    saw one long line and rendered verse as prose. Splitting recurses and stitches
    the surviving span back around each line fragment so the emphasis still wraps
    the displayed line.

    `soft_break` selects whether a `SoftBreak` is a display-line BOUNDARY (the
    default, for signature/epigraph extraction where Pandoc's soft breaks are real
    short display lines) or wrapping joined as a SPACE (verse DETECTION passes
    `soft_break=False`). Pandoc emits `SoftBreak` for a literal `\\r\\n` inside one
    `<w:t>` run — that is PROSE WRAPPING, not a hard `<w:br/>` — so verse detection
    must NOT treat it as a verse-line boundary (the C2 over-detection fix); only a
    hard `LineBreak` is a verse-line boundary there."""
    lines: list[list[ir.Inline]] = [[]]
    for n in inlines:
        if isinstance(n, ir.LineBreak):
            lines.append([])
        elif isinstance(n, ir.SoftBreak):
            if soft_break:
                lines.append([])
            else:
                lines[-1].append(ir.Text(" "))  # wrapping → a joining space
        elif isinstance(n, ir.ContainerInline):
            # Recurse, re-wrapping each produced line fragment in the container so a
            # `LineBreak` nested in `Emph` splits the line yet the surviving fragments
            # stay emphasized.
            child = inline_lines(n.children, soft_break=soft_break)
            if not child:
                continue
            for idx, frag in enumerate(child):
                if idx:
                    lines.append([])
                lines[-1].append(ir.rebuild_container(n, frag))
        else:
            lines[-1].append(n)
    return [ln for ln in lines if ln]


def _walk_inlines(inlines: list[ir.Inline]) -> list[ir.Inline]:
    """Depth-first flatten of an inline tree (for kind probes)."""
    out: list[ir.Inline] = []
    for n in inlines:
        out.append(n)
        if isinstance(n, ir.ContainerInline):
            out.extend(_walk_inlines(n.children))
    return out


# ---------------------------------------------------------------------------
# section-title vocab (mirrors docx_engine)
# ---------------------------------------------------------------------------

_VERSE_SECTION_TITLE_RE = re.compile(
    r"^(?:posвящение|посвящение|dedication|"
    r"предисловие\s+от\s+творца|preface\s+(?:from|by)\s+the\s+creator|"
    r"слово\s+творца|the\s+word\s+of\s+the\s+creator|creator'?s\s+word|"
    r"голос\s+творца|voice\s+of\s+the\s+creator|"
    r"ответ\s+творца|creator'?s\s+answer|"
    r"пояснение\s+творца|annotation\s+from\s+the\s+creator|"
    r"благословляющее\s+слово\s+творца|"
    r"молитва|prayer|псалом|psalm)\b",
    re.IGNORECASE,
)
_NUMBERED_QUESTION_TITLE_RE = re.compile(r"^\d{1,3}[.)]\s+\S.*[?？]\s*$")
# Endmatter bibliography/catalog heading vocab (mirrors docx_engine
# `_BIBLIO_HEADING_LINE`): the heading whose section was lifted is then dropped.
_BIBLIO_HEADING_RE = re.compile(
    r"^(?:библиография|bibliography|список\s+литературы|литература)\s*$",
    re.IGNORECASE,
)


def _is_verse_section_title(t: str) -> bool:
    return bool(_VERSE_SECTION_TITLE_RE.match(re.sub(r"\s+", " ", t.strip().lower())))


def _is_question_title(t: str) -> bool:
    return bool(_NUMBERED_QUESTION_TITLE_RE.match(t.strip()))


def _is_lineated_line(text: str, allow_colon: bool = False) -> bool:
    """Mirror of `docx_engine._is_lineated_plain_text`: a single short source
    line that reads as a verse line rather than prose / a label / a list item."""
    s = re.sub(r"\s+", " ", text).strip()
    if not s or len(s) > 145:
        return False
    if s.startswith(("!", "<", "|", ">", "[]")):
        return False
    if re.match(r"^[-*+]\s+", s) or re.match(r"^\d+[.)]\s+", s):
        return False
    if not allow_colon and re.match(r"^[A-ZА-ЯЁ][\w .А-Яа-яЁё-]{1,48}:\s*$", s):
        return False
    if not allow_colon and re.match(r"^[A-ZА-ЯЁ][\w .А-Яа-яЁё-]{1,48}:\s", s):
        return False
    if "http://" in s or "https://" in s:
        return False
    return True


# ---------------------------------------------------------------------------
# 1. TOC drop — auto-generated table-of-contents link runs
# ---------------------------------------------------------------------------

_TOC_HEADING_RE = re.compile(
    r"^(?:оглавление|содержание|table\s+of\s+contents|contents)\s*$",
    re.IGNORECASE,
)


def _is_toc_paragraph(p: ir.Paragraph) -> bool:
    """A paragraph that is entirely internal-anchor links (`#...` targets), i.e.
    a Pandoc-generated TOC entry. The GFM engine matched these as `[..](#..)`
    link lines; the IR sees them as `Link` inlines with `#`-prefixed targets."""
    if p.empty:
        return False
    links = [n for n in _walk_inlines(p.inlines) if isinstance(n, ir.Link)]
    if not links or not all(ln.target.startswith("#") for ln in links):
        return False
    # The visible text must be (almost) only the link labels — no real prose.
    label_text = "".join(inline_plain(ln.children) for ln in links)
    return len(inline_plain(p.inlines)) <= len(label_text) + 4


def drop_toc(blocks: list[ir.Block]) -> list[ir.Block]:
    out: list[ir.Block] = []
    i = 0
    n = len(blocks)
    while i < n:
        b = blocks[i]
        if isinstance(b, ir.Paragraph) and _is_toc_paragraph(b):
            j = i
            count = 0
            while j < n and isinstance((pj := blocks[j]), ir.Paragraph) and (
                pj.empty or _is_toc_paragraph(pj)
            ):
                if _is_toc_paragraph(pj):
                    count += 1
                j += 1
            if count >= 3:
                # Drop a preceding "Оглавление"/"Contents" heading too.
                if out and isinstance(out[-1], ir.Heading) and _TOC_HEADING_RE.match(
                    inline_plain(out[-1].inlines)
                ):
                    out.pop()
                i = j
                continue
        out.append(b)
        i += 1
    return out


# ---------------------------------------------------------------------------
# 2. rights-boilerplate scrub (bounded to the head region, before the first H1)
# ---------------------------------------------------------------------------


def scrub_rights(blocks: list[ir.Block]) -> list[ir.Block]:
    """Drop copyright/rights paragraphs in the head region (before the first H1,
    capped at the first 3% of blocks). Mirrors `docx_engine.scrub_rights_boilerplate`
    but on whole paragraphs: a paragraph whose entire text matches a rights
    pattern is dropped."""
    n = len(blocks)
    if n == 0:
        return blocks
    first_h1 = next((i for i, b in enumerate(blocks) if isinstance(b, ir.Heading) and b.level == 1), n)
    window_end = min(first_h1, max(20, int(n * 0.03)))
    out: list[ir.Block] = []
    for i, b in enumerate(blocks):
        if i < window_end and isinstance(b, ir.Paragraph) and not b.empty:
            text = inline_plain(b.inlines)
            if any(pat.fullmatch(text) for pat in RIGHTS_PATTERNS):
                continue
        out.append(b)
    return out


# ---------------------------------------------------------------------------
# 3. AI-alt scrub — strip machine-vision alt text (uses the prod constant)
# ---------------------------------------------------------------------------


def _is_ai_alt(alt: str) -> bool:
    return any(frag in alt for frag in AI_ALT_FRAGMENTS)


def _scrub_alt_in_inlines(inlines: list[ir.Inline]) -> list[ir.Inline]:
    out: list[ir.Inline] = []
    for n in inlines:
        if isinstance(n, ir.ImageInline) and _is_ai_alt(n.alt):
            out.append(ir.ImageInline(src=n.src, alt="", asset_id=n.asset_id))
        elif isinstance(n, ir.ContainerInline):
            out.append(ir.rebuild_container(n, _scrub_alt_in_inlines(n.children)))
        else:
            out.append(n)
    return out


def scrub_ai_alt(blocks: list[ir.Block]) -> list[ir.Block]:
    for b in blocks:
        if isinstance(b, ir.Paragraph):
            b.inlines = _scrub_alt_in_inlines(b.inlines)
        elif isinstance(b, ir.ImageBlock) and _is_ai_alt(b.alt):
            b.alt = ""
        elif isinstance(b, ir.Table):
            b.rows = [[_scrub_alt_in_inlines(cell) for cell in row] for row in b.rows]
        elif isinstance(b, ir.BlockQuote):
            scrub_ai_alt(b.blocks)  # recurse into containers (e.g. unwrapped Figure)
        elif isinstance(b, ir.ListBlock):
            for item in b.items:
                scrub_ai_alt(item)
    return blocks


# ---------------------------------------------------------------------------
# 4. bibliography table classification + lift
# ---------------------------------------------------------------------------


def lift_bibliography(
    doc: ir.Document,
    slug_lookup: dict[str, tuple[str, int | None, str | None]] | None = None,
) -> None:
    """Lift catalog/bibliography tables out of the body into `doc.bibliography`.

    Reading-content tables (scripture/archetype grids) have neither catalog images
    nor store URLs and are kept in the body; lifting them was the spike's one
    content-loss bug, caught by the token-multiset diff. Classification is on the
    actual catalog signal (cover images / LitRes / kindbook URLs), not a row count.
    """
    lookup = slug_lookup or {}
    kept: list[ir.Block] = []
    for b in doc.blocks:
        if isinstance(b, ir.Table) and _looks_like_biblio(b):
            doc.bibliography.extend(_parse_biblio(b, lookup))
            continue
        kept.append(b)
    doc.blocks = kept
    if doc.bibliography:
        doc.diagnostics.append(ir.Diagnostic(
            "warning", "import.bibliography",
            f"{len(doc.bibliography)} entries lifted to the bibliography sidecar",
        ))


def _raw_table_text(node: object) -> str:
    return json.dumps(node, ensure_ascii=False)


def _renders_as_html_table(t: ir.Table) -> bool:
    """True when Pandoc's GFM writer would emit this table as raw HTML `<table>`
    rather than a pipe table.

    This is the EXACT set the GFM engine ever lifts: its `extract_bibliography`
    only scans `<table>` blocks, because Pandoc renders simple grids (single-block
    cells, no spans, no caption) as pipe tables — which the GFM engine KEEPS in the
    body. Pandoc falls back to HTML when a cell holds more than one block (e.g. a
    catalog cover image Para plus a title-link Para), when a cell has a row/col
    span ≠ 1, or when the table has a caption. Mirroring that decision here keeps
    the IR's lift set identical to the GFM engine's, so a reading-content grid
    (single-block cells) is never lifted.

    NOTE: this pipe-vs-HTML-table fallback heuristic is pinned to the CURRENT
    Pandoc GFM writer (pandoc 3.9 — the version this pipeline runs). The exact
    conditions under which Pandoc downgrades a pipe table to raw HTML
    (multi-block cells, spans ≠ 1, captions) are writer-internal and could shift
    in a future Pandoc; if the pinned pandoc is bumped, re-confirm this set against
    the new writer (the A/B oracle's bibliography-lift parity is the canary).
    """
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
            if isinstance(row, list) and len(row) > 1 and isinstance(row[1], list):
                if any(cell_forces_html(cell) for cell in row[1]):
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
    """A catalog/bibliography table to lift: it carries a catalog signal (cover
    images / LitRes / kindbook URLs) AND Pandoc would render it as an HTML table
    (the only tables the GFM engine ever lifts). A simple reading-content grid —
    rendered by Pandoc as a pipe table, kept by the GFM engine — is never lifted,
    even if it embeds a thumbnail (the spike's content-loss class)."""
    if not _renders_as_html_table(t):
        return False
    raw = _raw_table_text(t.raw)
    return '"Image"' in raw or "litres.ru" in raw or "kindbook.net" in raw


_A_RE = re.compile(r"litres\.ru|kindbook\.net")


def _resolve_target(
    title: str,
    slug_lookup: dict[str, tuple[str, int | None, str | None]],
) -> dict[str, object] | None:
    """Resolve a title to a `{kind, number}` target when the corpus knows it."""
    got = slug_lookup.get(re.sub(r"\s+", " ", title.lower()).strip())
    if not got:
        return None
    _slug, number, kind = got
    if number is not None and kind:
        return {"kind": kind, "number": number}
    return None


def _parse_biblio(
    t: ir.Table,
    slug_lookup: dict[str, tuple[str, int | None, str | None]],
) -> list[dict[str, object]]:
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
                if alt and len(alt) > 2 and not _is_ai_alt(alt):
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
    no reading content — only empty paragraphs / thematic breaks. This mirrors
    `docx_engine.strip_bibliography_sections` post-lift."""
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


# ---------------------------------------------------------------------------
# 6. thematic breaks: a Paragraph whose only text is *** -> ThematicBreak
# ---------------------------------------------------------------------------

_HR_TEXTS = {"***", "* * *", r"\*\*\*"}


def thematic_breaks(blocks: list[ir.Block]) -> list[ir.Block]:
    out: list[ir.Block] = []
    for b in blocks:
        if isinstance(b, ir.Paragraph) and not b.empty and inline_plain(b.inlines) in _HR_TEXTS:
            out.append(ir.ThematicBreak())
            continue
        out.append(b)
    return out


# ---------------------------------------------------------------------------
# 7. heading demotion: source H1 -> H2 (page title is the only H1)
# ---------------------------------------------------------------------------


def demote_headings(blocks: list[ir.Block], levels: int = 1) -> list[ir.Block]:
    if levels <= 0:
        return blocks
    for b in blocks:
        if isinstance(b, ir.Heading):
            b.level = min(6, b.level + levels)
    return blocks


# ---------------------------------------------------------------------------
# 8. formatting-artifact strip — empty-emphasis husks
# ---------------------------------------------------------------------------


def _is_empty_emphasis(n: ir.Inline) -> bool:
    """True when `n` is an emphasis span whose flattened text is empty — the
    structural form of the GFM engine's stray `** **` / `\\**` artifacts (a Word
    run that held only whitespace/a break inside emphasis markers)."""
    return isinstance(n, ir.Emphasis) and inline_plain(n.children) == ""


def _drop_empty_emphasis(inlines: list[ir.Inline]) -> list[ir.Inline]:
    """Remove empty-emphasis husks anywhere in an inline list (recursing into
    surviving spans), so `…text** **` loses the trailing `** **` while real text
    is untouched."""
    out: list[ir.Inline] = []
    for n in inlines:
        if _is_empty_emphasis(n):
            continue
        if isinstance(n, ir.ContainerInline):
            out.append(ir.rebuild_container(n, _drop_empty_emphasis(n.children)))
        else:
            out.append(n)
    return out


def strip_formatting_artifacts(blocks: list[ir.Block]) -> list[ir.Block]:
    """Drop empty-emphasis artifacts: whole husk paragraphs vanish; a trailing or
    embedded `** **` inside a content paragraph is removed in place."""
    out: list[ir.Block] = []
    for b in blocks:
        if isinstance(b, ir.Paragraph) and not b.empty:
            b.inlines = _drop_empty_emphasis(b.inlines)
            if not b.inlines or not inline_plain(b.inlines):
                # Nothing left but breaks/whitespace → drop the husk paragraph.
                if not any(not isinstance(n, (ir.SoftBreak, ir.LineBreak)) for n in b.inlines):
                    continue
        out.append(b)
    return out


# ---------------------------------------------------------------------------
# 9. signatures + epigraphs from right alignment (the OOXML w:jc payload)
# ---------------------------------------------------------------------------

_RIGHT = {"right", "end"}
_SCRIPTURE_REF_RE = re.compile(
    r"^(?:(?:[1-3]\s*)?[А-ЯЁA-Z][А-Яа-яЁёA-Za-z. ]+\s+\d{1,3}:\d{1,3}(?:[–—-]\d{1,3})?|"
    r"(?:Ин|Иоанн|Мф|Матф|Марк|Мк|Лк|Луки|Дан|Даниил|Откровение|Бытие|Кор|Пс)\.?\s*\d{1,3}:\d{1,3}(?:[–—-]\d{1,3})?)\.?$",
    re.IGNORECASE)
_SIGNATURE_LINE_RE = re.compile(
    r"^(?:Панкратиус|Светозар|Сергей(?:\s+Панкратиус)?\.?|Я\s+Есмь|"
    r"Pankratius|Svetozar|Creator|The Creator|[—-]\s*Панкратиус.*|[—-]\s*Светозар.*)$",
    re.IGNORECASE)
_SOURCE_LINE_RE = re.compile(
    r"(?:к\.ф\.|Матрица|Пифия|Платон|Даниил|Откровение|Евангелие|Ин\.|Мф\.|Лк\.|Кор\.)",
    re.IGNORECASE)


def _is_signature(lines: list[str]) -> bool:
    if not (1 <= len(lines) <= 4) or any(len(line) > 90 for line in lines):
        return False
    if any("панкратиус" in line.casefold() for line in lines):
        return True
    if all(_SIGNATURE_LINE_RE.match(line.strip()) for line in lines):
        return True
    if len(lines) == 1 and re.fullmatch(r"[—-]\s*[\wА-Яа-яЁё .]{2,80}", lines[0]):
        return True
    return False


def _is_epigraph(lines: list[str], italic_count: int) -> bool:
    if len(lines) < 2:
        return False
    joined = " ".join(lines)
    if len(joined) < 30:
        return False
    has_ref = any(_SCRIPTURE_REF_RE.match(line.strip()) for line in lines)
    has_source = any(_SOURCE_LINE_RE.search(line) for line in lines[1:])
    starts_quoted = lines[0].lstrip().startswith(("«", '"', "“", "„"))
    mostly_italic = italic_count >= max(1, len(lines) // 2)
    compact = has_source and len(lines) <= 4 and len(lines[0]) <= 180
    return bool(has_ref or compact or (starts_quoted and has_source) or (starts_quoted and mostly_italic))


def _split_epigraph(lines: list[str]) -> tuple[list[str], list[str]]:
    footer: list[str] = []
    quote = list(lines)
    while len(quote) > 1:
        cand = quote[-1].strip()
        if _SCRIPTURE_REF_RE.match(cand) or _SOURCE_LINE_RE.search(cand):
            footer.insert(0, quote.pop())
            continue
        break
    if not footer:
        footer = [quote.pop()]
    return quote, footer


def structural_blocks(blocks: list[ir.Block]) -> list[ir.Block]:
    """Group contiguous right-aligned non-empty paragraphs and classify each run
    as a signature or epigraph, consuming the `w:jc` payload directly from the IR
    (no markdown round-trip / fuzzy re-matching like the GFM engine needs)."""
    out: list[ir.Block] = []
    i = 0
    n = len(blocks)
    while i < n:
        b = blocks[i]
        if isinstance(b, ir.Paragraph) and b.align in _RIGHT and not b.empty:
            j = i
            group: list[ir.Paragraph] = []
            while j < n and isinstance((pj := blocks[j]), ir.Paragraph) and pj.align in _RIGHT and not pj.empty:
                group.append(pj)
                j += 1
            lines: list[str] = []
            for p in group:
                for ln in inline_lines(p.inlines):
                    s = inline_plain(ln)
                    if s:
                        lines.append(s)
            italic_count = sum(1 for p in group if p.italic)
            if lines and _is_signature(lines):
                out.append(ir.Signature(lines=lines))
                i = j
                continue
            if lines and _is_epigraph(lines, italic_count):
                quote, footer = _split_epigraph(lines)
                out.append(ir.Epigraph(quote=quote, footer=footer))
                i = j
                continue
            out.extend(group)
            i = j
            continue
        out.append(b)
        i += 1
    return out


# ---------------------------------------------------------------------------
# 10. dialogue labels (incl. mixed leading-Strong inline split)
# ---------------------------------------------------------------------------

_DIALOGUE_PREFIXES = [
    "Панкратиус", "Светозар", "Светозар Gemini Flash 2.0", "Светозар DeepSeek",
    "Светозар ChatGPT", "Творец", "Бог", "Слово Творца", "Слово Бога",
    "Pankratius", "Svetozar", "Creator", "God", "Gemini", "DeepSeek", "ChatGPT",
]


def _leading_strong(inlines: list[ir.Inline]) -> tuple[ir.Emphasis | None, list[ir.Inline]]:
    """If the paragraph opens with a `Strong` span, return it plus the trailing
    inlines (dropping the leading break/space between them); else (None, inlines)."""
    rest = list(inlines)
    while rest and isinstance(rest[0], (ir.SoftBreak, ir.LineBreak)):
        rest.pop(0)
    if rest and isinstance(rest[0], ir.Emphasis) and rest[0].kind == "strong":
        head = rest[0]
        tail = rest[1:]
        while tail and isinstance(tail[0], (ir.SoftBreak, ir.LineBreak)):
            tail.pop(0)
        return head, tail
    return None, inlines


def _hard_break_segments(inlines: list[ir.Inline]) -> list[list[ir.Inline]]:
    """Split inlines on TOP-LEVEL hard `LineBreak`s into segments (turns). Soft
    breaks are NOT segment boundaries (they are prose wrapping); only an authored
    `<w:br/>` separates dialogue turns packed into one Word paragraph."""
    segs: list[list[ir.Inline]] = [[]]
    for n in inlines:
        if isinstance(n, ir.LineBreak):
            segs.append([])
        else:
            segs[-1].append(n)
    return [s for s in segs if s]


def _emit_dialogue_segment(
    inlines: list[ir.Inline],
    re_inside: re.Pattern[str],
    re_label: re.Pattern[str],
) -> list[ir.Block] | None:
    """Canonicalize ONE dialogue segment (a paragraph or a single hard-break turn).

    Returns the emitted blocks (a `DialogueLabel` plus an optional body paragraph)
    when the segment opens with a `Strong("Speaker:")`, else `None` (the caller
    keeps the segment as-is). Covers all three corpus shapes: whole-paragraph
    `Strong("Speaker: body")`, bare `Strong("Speaker:")`, and `Strong("Speaker:")`
    followed by trailing prose inlines."""
    head, tail = _leading_strong(inlines)
    if head is None:
        return None
    head_txt = inline_plain(head.children)
    if not tail:
        m = re_inside.match(head_txt)
        if m:
            blocks: list[ir.Block] = [ir.DialogueLabel(speaker=m.group(1))]
            body = m.group(2).strip()
            if re.search(r"[\wЀ-ӿ]", body):
                blocks.append(ir.Paragraph(inlines=[ir.Text(body)]))
            return blocks
        lm = re_label.match(head_txt)
        if lm:
            return [ir.DialogueLabel(speaker=lm.group(1))]
        return None
    m = re_label.match(head_txt)
    if m:
        out: list[ir.Block] = [ir.DialogueLabel(speaker=m.group(1))]
        if tail:
            out.append(ir.Paragraph(inlines=tail))
        return out
    m = re_inside.match(head_txt)
    if m:
        # Join the inside-body text to the trailing inlines with a space — UNLESS
        # the body text ends in an OPENING quote/bracket glyph, where a space would
        # wrongly separate the glyph from what it opens (`«` + `Почему` → `« Почему`).
        head_body = m.group(2).strip()
        joiner = "" if head_body and head_body[-1] in "«“„([{‹" else " "
        body_inlines: list[ir.Inline] = [ir.Text(head_body + joiner)] + tail
        return [ir.DialogueLabel(speaker=m.group(1)), ir.Paragraph(inlines=body_inlines)]
    return None


def dialogue_labels(blocks: list[ir.Block]) -> list[ir.Block]:
    """Canonicalize `**Speaker:**` labels.

    Source shapes, all from the corpus:
      * a paragraph whose single inline is `Strong("Speaker: body")` → label + body
      * a paragraph whose single inline is `Strong("Speaker:")`/`Strong("Speaker")` → label
      * a paragraph that OPENS with `Strong("Speaker:")` then has trailing prose
        (the mixed-inline case the spike left unfinished) → label + the prose
      * a paragraph that packs SEVERAL turns separated by hard `LineBreak`s, each
        opening with `Strong("Speaker:")` (the H1 multi-turn case, e.g. en/05) →
        split on the hard breaks and emit one label + body PER turn. Without this
        the leading turn's label was peeled and every later turn collapsed into one
        run-on prose paragraph (hard breaks → spaces).
    """
    # Longest-first so e.g. "Светозар DeepSeek" wins over the "Светозар" prefix.
    # `key=lambda p: -len(p)` (not `key=len`) keeps the element type `str`.
    prefixes = sorted(_DIALOGUE_PREFIXES, key=lambda p: -len(p))
    inner = "|".join(re.escape(p) for p in prefixes)
    re_inside = re.compile(rf"^({inner})\s*:\s*(.+)$")
    re_label = re.compile(rf"^({inner})\s*:?\s*$")

    def opens_with_speaker(seg: list[ir.Inline]) -> bool:
        head, _tail = _leading_strong(seg)
        if head is None:
            return False
        txt = inline_plain(head.children)
        return bool(re_label.match(txt) or re_inside.match(txt))

    out: list[ir.Block] = []
    for b in blocks:
        if not (isinstance(b, ir.Paragraph) and not b.empty):
            out.append(b)
            continue
        # Multi-turn: a paragraph packing >= 2 hard-break turns that each open with
        # a speaker label is split on the hard breaks; every speaker turn becomes
        # its own label + body, and a non-speaker segment (e.g. a leading date) is
        # kept as its own paragraph (matching the GFM per-line split).
        segments = _hard_break_segments(b.inlines)
        if len(segments) > 1 and sum(opens_with_speaker(s) for s in segments) >= 2:
            for seg in segments:
                emitted = _emit_dialogue_segment(seg, re_inside, re_label)
                if emitted is not None:
                    out.extend(emitted)
                else:
                    out.append(ir.Paragraph(inlines=seg))
            continue
        emitted = _emit_dialogue_segment(b.inlines, re_inside, re_label)
        if emitted is not None:
            out.extend(emitted)
        else:
            out.append(b)
    return out


# ---------------------------------------------------------------------------
# 11. verse / answer-block detection from stanza structure
# ---------------------------------------------------------------------------


def _is_wrapped_prose(p: ir.Paragraph) -> bool:
    """True when a paragraph's only in-run breaks are `SoftBreak`s (prose wrapping:
    a literal `\\r\\n` in one `<w:t>`) with NO hard `LineBreak` — i.e. a single
    authored prose paragraph that merely wrapped. Such a paragraph is NOT a verse
    candidate even though its collapsed text is one short-enough line: its lineation
    was never authored (the C2 fix; e.g. `книга-света-и-экрана`, 711 SoftBreaks / 2
    hard breaks). A paragraph with a hard break stays a verse candidate (its
    lineation IS authored), and a no-break short paragraph (book-01-царствия verse,
    one Word paragraph per line) stays eligible too."""
    nodes = _walk_inlines(p.inlines)
    has_soft = any(isinstance(n, ir.SoftBreak) for n in nodes)
    has_hard = any(isinstance(n, ir.LineBreak) for n in nodes)
    return has_soft and not has_hard


def _para_lineated(p: ir.Paragraph, allow_colon: bool) -> bool:
    if not p.inlines:
        return False
    for n in _walk_inlines(p.inlines):
        if isinstance(n, (ir.ImageInline, ir.Link, ir.Code)):
            return False
    # A paragraph that is only wrapped prose (SoftBreaks, no hard break) is prose,
    # never a verse line — its line breaks are wrapping, not authored lineation.
    if _is_wrapped_prose(p):
        return False
    # Verse DETECTION: a `SoftBreak` is prose wrapping (join as space), only a hard
    # `LineBreak` is a verse-line boundary; recurse into containers (C2 + C3).
    lines = [inline_plain(ln) for ln in inline_lines(p.inlines, soft_break=False)]
    lines = [line for line in lines if line]
    return bool(lines) and all(_is_lineated_line(line, allow_colon) for line in lines)


def _block_lines(p: ir.Paragraph) -> list[list[ir.Inline]]:
    # Build verse display lines the same way detection sees them: hard `LineBreak`s
    # (incl. nested in `Emph`) split; `SoftBreak` wrapping is joined as a space.
    return [ln for ln in inline_lines(p.inlines, soft_break=False) if inline_plain(ln)]


def _all_lines(paras: list[ir.Paragraph]) -> list[str]:
    return [inline_plain(ln) for p in paras for ln in _block_lines(p)]


def verse_blocks(blocks: list[ir.Block]) -> list[ir.Block]:
    """Fold runs of short source lines (one paragraph per line, empty paragraphs
    as stanza separators) into `VerseBlock`/answer-block. Mirrors
    `docx_engine._lineated_runs_from_ast` + `_lineated_run_kind`, on the IR."""
    out: list[ir.Block] = []
    i = 0
    n = len(blocks)
    last_heading_named = False
    last_heading_question = False
    last_heading_any = False
    last_was_separator = False

    while i < n:
        b = blocks[i]
        if isinstance(b, ir.Heading):
            title = inline_plain(b.inlines)
            last_heading_any = True
            last_heading_named = _is_verse_section_title(title)
            last_heading_question = _is_question_title(title)
            last_was_separator = False
            out.append(b)
            i += 1
            continue
        if isinstance(b, ir.ThematicBreak):
            last_heading_any = last_heading_named = last_heading_question = False
            last_was_separator = True
            out.append(b)
            i += 1
            continue
        answer_ctx = last_heading_question
        if isinstance(b, ir.Paragraph) and (b.empty or _para_lineated(b, answer_ctx)):
            run: list[ir.Paragraph] = []
            run_after_named = last_heading_named
            run_after_question = last_heading_question
            run_after_heading = last_heading_any
            run_after_separator = last_was_separator
            while i < n and isinstance((pi := blocks[i]), ir.Paragraph) and (
                pi.empty or _para_lineated(pi, run_after_question)
            ):
                run.append(pi)
                i += 1
            content = [p for p in run if not p.empty]
            kind = _run_kind(run, run_after_named, run_after_question, run_after_heading, run_after_separator)
            if kind and len(_all_lines(content)) >= (2 if kind == "answer-block" else 3):
                out.append(_build_verse(run, kind))
            else:
                out.extend(run)
            last_heading_any = last_heading_named = last_heading_question = False
            last_was_separator = False
            continue
        last_heading_any = last_heading_named = last_heading_question = False
        last_was_separator = False
        out.append(b)
        i += 1
    return out


def _run_kind(
    run: list[ir.Paragraph],
    after_named: bool,
    after_question: bool,
    after_heading: bool,
    after_separator: bool,
) -> ir.VerseRole | None:
    content = [p for p in run if not p.empty]
    lines = _all_lines(content)
    if after_question and 2 <= len(lines) <= 12:
        lengths = [len(line) for line in lines]
        avg = sum(lengths) / len(lengths)
        return "answer-block" if avg <= 95 and max(lengths) <= 150 else None
    if len(lines) < 3:
        return None
    lengths = [len(line) for line in lines]
    avg = sum(lengths) / len(lengths)
    empty_count = sum(1 for p in run if p.empty)
    # The fallback verse signal: a paragraph carrying a HARD `LineBreak` (`<w:br/>`)
    # reads as multi-line verse. A `SoftBreak` is prose wrapping and must NOT count
    # (the C2 over-detection fix); the walk recurses into containers so a hard break
    # nested inside `Emph` still counts (the C3 fix).
    linebreak_count = sum(
        1 for p in content if any(isinstance(x, ir.LineBreak) for x in _walk_inlines(p.inlines))
    )
    if after_named:
        return "verse-block" if avg <= 150 else None
    if after_separator and len(lines) <= 24:
        return "verse-block" if avg <= 110 and max(lengths) <= 160 else None
    if after_heading and len(lines) <= 14:
        return "verse-block" if avg <= 95 and max(lengths) <= 150 else None
    if linebreak_count:
        return "verse-block"
    if empty_count and avg <= 120:
        return "verse-block"
    return None


def _build_verse(run: list[ir.Paragraph], kind: ir.VerseRole) -> ir.VerseBlock:
    """Build stanzas: an empty paragraph is a stanza break; a `***` paragraph is a
    one-line separator stanza."""
    stanzas: list[list[list[ir.Inline]]] = []
    current: list[list[ir.Inline]] = []

    def flush() -> None:
        nonlocal current
        if current:
            stanzas.append(current)
            current = []

    for p in run:
        if p.empty:
            flush()
            continue
        if inline_plain(p.inlines) in _HR_TEXTS:
            flush()
            stanzas.append([[ir.Text("***")]])
            continue
        for ln in _block_lines(p):
            current.append(ln)
    flush()
    return ir.VerseBlock(stanzas=stanzas, role=kind)


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def normalize(
    doc: ir.Document,
    *,
    demote_levels: int = 1,
    slug_lookup: dict[str, tuple[str, int | None, str | None]] | None = None,
) -> ir.Document:
    """Run the full normalize chain over `doc` in dependency order."""
    doc.blocks = drop_toc(doc.blocks)
    doc.blocks = scrub_rights(doc.blocks)
    doc.blocks = scrub_ai_alt(doc.blocks)
    lift_bibliography(doc, slug_lookup)
    doc.blocks = strip_bare_bibliography_heading(doc.blocks)
    doc.blocks = thematic_breaks(doc.blocks)
    doc.blocks = demote_headings(doc.blocks, demote_levels)
    doc.blocks = strip_formatting_artifacts(doc.blocks)
    doc.blocks = structural_blocks(doc.blocks)
    doc.blocks = dialogue_labels(doc.blocks)
    doc.blocks = verse_blocks(doc.blocks)
    return doc
