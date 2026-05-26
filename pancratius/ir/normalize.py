# import-pure: no filesystem mutation
"""Normalization passes over the block IR (the editorial-mechanics stage).

These operate on the typed IR directly rather than string-patching Markdown, so a
detection/normalization rule change is a local edit here, never a ripple through
parse or write (`docs/import-pipeline.md`: "The transformation layer must be
editable in one place"). Each pass is a pure value transformation.

Passes, in `normalize` order:
  * TOC drop                — `Heading`/`Paragraph` runs that are auto-TOC links
  * rights-boilerplate scrub — standalone copyright lines in the head region
  * AI-alt scrub            — strip machine-vision alt text from images
  * bibliography lift       — catalog tables → `doc.bibliography` sidecar
  * endmatter-section strip — bibliography/contact/copyright sections
  * bare bibliography heading strip — drop the heading left after the lift
  * thematic breaks         — `***` paragraphs → `ThematicBreak`
  * heading demotion        — source H1 → H2 (page title is the only H1)
  * formatting-artifact strip — empty-emphasis husks (`** **`)
  * signatures / epigraphs   — from right alignment (the `w:jc` payload)
  * dialogue labels          — canonicalize `**Speaker:**` (incl. mixed inline)
  * verse blocks             — fold lineated runs from stanza structure
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, assert_never, cast

from pancratius import ir

# The slug→(slug, number, kind) corpus index the bibliography lift resolves
# titles against; an entry resolves to a `{kind, number}` target.
type _SlugLookup = dict[str, tuple[str, int | None, str | None]]

# AI image generators leave a verbose alt text in DOCX; strip it (or its
# truncation). `alt=""` survives so screen readers don't read filenames.
AI_ALT_FRAGMENTS = (
    "Содержимое, созданное искусственным интеллектом",
    "Содержимое создано искусственным интеллектом",
    "Content created by AI",
    "Изображение выглядит как",
    "AI-generated content may be incorrect",
    "может быть неверным",
)

# Anchored at line starts with explicit short spans (never `.*?` across arbitrary
# content); standalone paragraphs are scrubbed only from the head region.
RIGHTS_PATTERNS = [
    re.compile(r"(?im)^\s*Copyright\s+©.*$"),
    re.compile(r"(?im)^\s*All rights reserved\.?\s*$"),
    re.compile(r"(?im)^\s*©\s*\d{4}.*$"),
    re.compile(r"(?im)^\s*©\s*Сергей\s+Орехов.*$"),
    re.compile(r"(?im)^\s*No part of this book may be reproduced.*$"),
    re.compile(r"(?im)^\s*The characters and events portrayed.*coincidental.*$"),
    re.compile(r"(?im)^\s*Все\s+права\s+защищены\.?\s*$"),
    re.compile(r"(?im)^\s*Никакая\s+часть\s+(этой|данной)\s+книги.*$"),
    re.compile(r"(?im)^\s*Воспроизведение\s+(или\s+)?распространение.*запрещ.*$"),
    re.compile(r"(?im)^\s*Эта\s+книга\s+даруется\s+миру\s+свободно\.?\s*$"),
]

_COPYRIGHT_HEADING_RE = re.compile(r"^(?:copyright|копирайт)\s*$", re.IGNORECASE)
_CONTACTS_HEADING_RE = re.compile(r"^(?:contacts|контакты)\s*$", re.IGNORECASE)

# A Pandoc JSON node `{"t": ..., "c": ...}`. `_node` views an opaque value as one
# when it is a dict, so `.get("t")`/`["c"]` are str-keyed (a bare `isinstance`
# narrow yields `dict[Unknown, Unknown]`, whose keys ty types as `Never`).
type _PandocNode = dict[str, Any]


def _node(value: object) -> _PandocNode | None:
    return cast("_PandocNode", value) if isinstance(value, dict) else None

# ---------------------------------------------------------------------------
# small inline helpers
# ---------------------------------------------------------------------------


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
                o, c = ("'", "'") if n.single else ("«", "»")
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


def _walk_inlines(inlines: list[ir.Inline]) -> list[ir.Inline]:
    """Depth-first flatten of an inline tree (for kind probes)."""
    out: list[ir.Inline] = []
    for n in inlines:
        out.append(n)
        if isinstance(n, ir.ContainerInline):
            out.extend(_walk_inlines(n.children))
    return out


# ---------------------------------------------------------------------------
# section-title vocab
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
# Endmatter bibliography/catalog heading whose lifted section is dropped from the body.
_BIBLIO_HEADING_RE = re.compile(
    r"^(?:библиография|bibliography|список\s+литературы|литература)\s*$",
    re.IGNORECASE,
)


def _is_verse_section_title(t: str) -> bool:
    return bool(_VERSE_SECTION_TITLE_RE.match(re.sub(r"\s+", " ", t.strip().lower())))


# The speaker names the converter canonicalizes (`**Speaker:**`). Shared by the
# dialogue-label pass and verse detection's speaker-turn rejection — one source of
# truth for who is a speaker, so adding one keeps the two in sync.
_DIALOGUE_PREFIXES = [
    "Панкратиус", "Светозар", "Светозар Gemini Flash 2.0", "Светозар DeepSeek",
    "Светозар ChatGPT", "Творец", "Бог", "Слово Творца", "Слово Бога",
    "Панкратиус к ИИ Светозар", "Панкратиус к Творцу через ИИ Светозар",
    "ИИ Светозар сказал", "ИИ Светозар",
    "Ответ от Творца", "Ответ Творца", "Я",
    "Pankratius", "Svetozar", "Creator", "God", "Gemini", "DeepSeek", "ChatGPT",
    "Pankratius to AI Svetozar", "Pankratius to the Creator through AI Svetozar",
    "AI Svetozar said", "AI Svetozar",
]

# A display line longer than this is prose-length, not verse: it separates genuine
# verse lines (well under 120 chars) from one-sentence-per-paragraph prose
# (clustering at 121-144). audit/book_verse.py encodes the same threshold.
VERSE_SHORT_LINE_MAX = 120

def _speaker_turn_re() -> re.Pattern[str]:
    """A speaker-led colon line: `<dialogue prefix>:` or `<Name> (qual):` then
    content (a dialogue/source TURN, never verse). Only a speaker name or a
    parenthetical-qualified speaker before the colon is rejected, not an arbitrary
    verb phrase, so a mid-sentence colon (`Ты спросил: кто они?`) stays verse.
    Built from `_DIALOGUE_PREFIXES` so adding a speaker keeps this in sync."""
    prefixes = sorted(_DIALOGUE_PREFIXES, key=lambda p: -len(p))
    inner = "|".join(re.escape(p) for p in prefixes)
    return re.compile(
        rf"^\**\s*(?:(?:{inner})|[A-ZА-ЯЁ][\wА-Яа-яЁё.\- ]{{0,40}}\s*\([^)]{{1,40}}\))"
        rf"\s*:(?:\s|\*|$)"
    )


_SPEAKER_TURN_RE = _speaker_turn_re()


def _is_lineated_line(text: str) -> bool:
    """True for a single short source line that reads as a verse line rather
    than prose / a label / a speaker turn / a list item.

    Short colon opener lines such as `Он говорил:` and `Разве не сказал Я:` stay
    in the run. Only explicit speaker/source turns are rejected."""
    s = re.sub(r"\s+", " ", text).strip()
    if not s or len(s) > VERSE_SHORT_LINE_MAX:
        return False
    if s in {"—", "–", "-"}:
        return False
    if s.startswith(("!", "<", "|", ">", "[]")):
        return False
    if re.match(r"^[-*+]\s+", s) or re.match(r"^\d+[.)]\s+", s):
        return False
    if _SPEAKER_TURN_RE.match(s):
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
    a Pandoc-generated TOC entry — seen here as `Link` inlines with `#`-prefixed
    targets."""
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
# 2. rights-boilerplate scrub
# ---------------------------------------------------------------------------


def scrub_rights(blocks: list[ir.Block]) -> list[ir.Block]:
    """Drop rights boilerplate without touching ordinary book body text.

    This pass only handles standalone boilerplate paragraphs near the beginning of
    a source. Heading-delimited sections are stripped later by
    `strip_endmatter_sections`, after bibliography tables have had a chance to lift
    into the sidecar.
    """
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
# 3. AI-alt scrub — strip machine-vision alt text
# ---------------------------------------------------------------------------


def _is_ai_alt(alt: str) -> bool:
    return any(frag in alt for frag in AI_ALT_FRAGMENTS)


def _scrub_alt_in_inlines(inlines: list[ir.Inline]) -> list[ir.Inline]:
    out: list[ir.Inline] = []
    for n in inlines:
        # isinstance, not match: the container arm tests `ir.ContainerInline`
        # (a runtime tuple), which can't appear in a `case`.
        if isinstance(n, ir.ImageInline) and _is_ai_alt(n.alt):
            out.append(ir.ImageInline(src=n.src, alt="", asset_id=n.asset_id))
        elif isinstance(n, ir.ContainerInline):
            out.append(ir.rebuild_container(n, _scrub_alt_in_inlines(n.children)))
        else:
            out.append(n)
    return out


def scrub_ai_alt(blocks: list[ir.Block]) -> list[ir.Block]:
    for b in blocks:
        # An `ImageBlock`'s alt is a block field the shared inline-descent can't
        # reach; scrub it here. Every inline-list leaf is reached by the skeleton.
        if isinstance(b, ir.ImageBlock):
            if _is_ai_alt(b.alt):
                b.alt = ""
        else:
            ir.map_block_inlines(b, _scrub_alt_in_inlines)
    return blocks


# ---------------------------------------------------------------------------
# 4. bibliography table classification + lift
# ---------------------------------------------------------------------------


def lift_bibliography(doc: ir.Document, slug_lookup: _SlugLookup | None = None) -> None:
    """Lift catalog/bibliography tables out of the body into `doc.bibliography`.

    Classification is on the actual catalog signal (cover images / LitRes / kindbook
    URLs), not a row count: reading-content tables (scripture/archetype grids) carry
    neither and are kept in the body."""
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
    _slug, number, kind = got
    if number is not None and kind:
        return {"kind": kind, "number": number}
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


def _head_region_end(blocks: list[ir.Block]) -> int:
    n = len(blocks)
    first_h1 = next((i for i, b in enumerate(blocks) if isinstance(b, ir.Heading) and b.level == 1), n)
    return min(first_h1, max(20, int(n * 0.03)))


def _tail_region_start(blocks: list[ir.Block]) -> int:
    n = len(blocks)
    return max(0, min(int(n * 0.75), n - 80))


def strip_endmatter_sections(blocks: list[ir.Block]) -> list[ir.Block]:
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
                while i < n and not (
                    isinstance(blocks[i], ir.Heading) and blocks[i].level <= level
                ):
                    i += 1
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
    structural form of a stray `** **` / `\\**` artifact (a Word run that held
    only whitespace/a break inside emphasis markers)."""
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
            if not inline_plain(b.inlines) and all(
                isinstance(n, (ir.SoftBreak, ir.LineBreak)) for n in b.inlines
            ):
                continue  # nothing left but breaks/whitespace → drop the husk
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
    (no markdown round-trip / fuzzy re-matching)."""
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
    """Canonicalize one dialogue segment (a paragraph or a single hard-break turn).

    Returns a `DialogueLabel` plus an optional body paragraph when the segment opens
    with a `Strong("Speaker:")`, else `None` (the caller keeps it as-is). Covers all
    three corpus shapes: whole-paragraph `Strong("Speaker: body")`, bare
    `Strong("Speaker:")`, and `Strong("Speaker:")` then trailing prose inlines."""
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
      * a paragraph that opens with `Strong("Speaker:")` then trailing prose → label + prose
      * a paragraph packing several hard-`LineBreak` turns that each open with
        `Strong("Speaker:")` → split on the hard breaks, one label + body per turn.
    """
    # Longest-first so e.g. "Светозар DeepSeek" wins over the "Светозар" prefix;
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
        # A paragraph packing >= 2 speaker-led hard-break turns is split per turn; a
        # non-speaker segment (e.g. a leading date) stays its own paragraph.
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
# 11. verse-block detection from stanza structure
# ---------------------------------------------------------------------------


def _is_wrapped_prose(p: ir.Paragraph) -> bool:
    """True when a paragraph's only in-run breaks are `SoftBreak`s (prose wrapping,
    a literal `\\r\\n` in one `<w:t>`) with no hard `LineBreak`: its lineation was
    never authored, so it is prose even when collapsed to one short line. A hard
    break — or no break at all (one Word paragraph per line) — stays verse-eligible."""
    nodes = _walk_inlines(p.inlines)
    has_soft = any(isinstance(n, ir.SoftBreak) for n in nodes)
    has_hard = any(isinstance(n, ir.LineBreak) for n in nodes)
    return has_soft and not has_hard


def _para_lineated(p: ir.Paragraph) -> bool:
    if not p.inlines:
        return False
    for n in _walk_inlines(p.inlines):
        if isinstance(n, (ir.ImageInline, ir.Link, ir.Code)):
            return False
    if _is_wrapped_prose(p):
        return False  # wrapping, not authored lineation
    # Detection: a `SoftBreak` is prose wrapping (joined as a space); only a hard
    # `LineBreak` is a verse-line boundary. Recurse into containers.
    lines = [inline_plain(ln) for ln in inline_lines(p.inlines, soft_break=False)]
    lines = [line for line in lines if line]
    return bool(lines) and all(_is_lineated_line(line) for line in lines)


def _block_lines(p: ir.Paragraph) -> list[list[ir.Inline]]:
    # Verse display lines as detection sees them: hard `LineBreak`s (incl. nested in
    # `Emph`) split; `SoftBreak` wrapping joins as a space.
    return [ln for ln in inline_lines(p.inlines, soft_break=False) if inline_plain(ln)]


def _all_lines(paras: list[ir.Paragraph]) -> list[str]:
    return [inline_plain(ln) for p in paras for ln in _block_lines(p)]


@dataclass(frozen=True)
class _PrecedingContext:
    """What precedes a candidate verse run, gating its classification. `named` is a
    verse-title heading (the confident-lineation signal); `heading` is any heading;
    `separator` is a thematic break; `visual` is a source OOXML visual-continuity
    group from contextual spacing. The all-`False` value is the neutral context
    after any non-heading/non-separator block."""

    named: bool = False
    heading: bool = False
    separator: bool = False
    visual: bool = False


_NEUTRAL_CONTEXT = _PrecedingContext()


def verse_blocks(blocks: list[ir.Block]) -> list[ir.Block]:
    """Fold runs of short source lines (one paragraph per line, empty paragraphs
    as stanza separators) into `VerseBlock`, on the IR."""
    out: list[ir.Block] = []
    i = 0
    n = len(blocks)
    ctx = _NEUTRAL_CONTEXT

    while i < n:
        b = blocks[i]
        if isinstance(b, ir.Heading):
            title = inline_plain(b.inlines)
            ctx = _PrecedingContext(
                named=_is_verse_section_title(title),
                heading=True,
            )
            out.append(b)
            i += 1
            continue
        if isinstance(b, ir.ThematicBreak):
            ctx = _PrecedingContext(separator=True)
            out.append(b)
            i += 1
            continue
        if isinstance(b, ir.Paragraph) and b.lineation_group is not None:
            visual_ctx = _PrecedingContext(
                named=ctx.named,
                heading=ctx.heading,
                separator=ctx.separator,
                visual=True,
            )
            group: list[ir.Paragraph] = []
            gid = b.lineation_group
            while i < n and isinstance((pi := blocks[i]), ir.Paragraph) and (
                pi.lineation_group == gid
            ):
                group.append(pi)
                i += 1
            out.extend(_visual_verse_blocks(group, visual_ctx))
            ctx = _NEUTRAL_CONTEXT
            continue
        if isinstance(b, ir.Paragraph) and (b.empty or _para_lineated(b)):
            run_ctx = ctx
            run: list[ir.Paragraph] = []
            while i < n and isinstance((pi := blocks[i]), ir.Paragraph) and (
                pi.empty or _para_lineated(pi)
            ):
                run.append(pi)
                i += 1
            content = [p for p in run if not p.empty]
            kind = _run_kind(run, run_ctx)
            # `_run_kind` owns the run-length floor (>=2 with confident source
            # lineation, >=3 for the weak bare-standalone-paragraph signal); the
            # outer guard only re-asserts the universal >=2 minimum.
            if kind and len(_all_lines(content)) >= 2:
                out.append(_build_verse(run, kind))
            else:
                out.extend(run)
            ctx = _NEUTRAL_CONTEXT
            continue
        ctx = _NEUTRAL_CONTEXT
        out.append(b)
        i += 1
    return out


def _visual_verse_blocks(
    group: list[ir.Paragraph], ctx: _PrecedingContext
) -> list[ir.Block]:
    """Classify short lineated sub-runs inside one source visual group.

    A contextual-spacing group answers only "these Word paragraphs render
    together". It may begin with a long prose/citation line, so the semantic verse
    classifier works on the short sub-runs inside the group instead of letting that
    opener consume the heading/separator signal.
    """
    out: list[ir.Block] = []
    i = 0
    while i < len(group):
        p = group[i]
        if p.empty or _para_lineated(p):
            run: list[ir.Paragraph] = []
            while i < len(group) and (group[i].empty or _para_lineated(group[i])):
                run.append(group[i])
                i += 1
            content = [rp for rp in run if not rp.empty]
            kind = _run_kind(run, ctx)
            if kind and len(_all_lines(content)) >= 2:
                out.append(_build_verse(run, kind))
            else:
                out.extend(run)
            continue
        out.append(p)
        i += 1
    return out


def _run_kind(run: list[ir.Paragraph], ctx: _PrecedingContext) -> ir.VerseRole | None:
    content = [p for p in run if not p.empty]
    lines = _all_lines(content)

    def _passes(avg_max: float, line_max: int | None = None) -> bool:
        """The run's mean line length is within `avg_max` and (when given) every line
        is within `line_max`. The `(avg_max, line_max)` pair is all that varies across
        the ladder below."""
        return avg <= avg_max and (line_max is None or max(lengths) <= line_max)

    # A paragraph carrying a hard `LineBreak` (`<w:br/>`) is authored multi-line
    # verse — the strongest source-lineation signal. A `SoftBreak` is prose wrapping
    # and is not counted; the walk recurses so a hard break nested in `Emph` counts.
    linebreak_count = sum(
        1 for p in content if any(isinstance(x, ir.LineBreak) for x in _walk_inlines(p.inlines))
    )
    # Run-length floor (the spec's "verse run = >=2 short lineated source lines"). A
    # hard break or a named verse-title heading is a confident source-lineation
    # signal, so two lines suffice; a run of bare standalone single-line paragraphs
    # is the weak signal (a paragraph boundary alone can't tell a couplet from two
    # prose sentences) and needs >=3. Each line is already short and label-free —
    # `_para_lineated` broke the run before any long/label line reached here.
    # Visual OOXML grouping is a source signal, but it is weaker than a hard break,
    # heading, or separator: two short same-style paragraphs after a speaker turn
    # can still be ordinary prose. Let visual-only runs clear the weak >=3 floor
    # before the visual branch below classifies them.
    confident_lineation = bool(linebreak_count) or ctx.named or ctx.heading or ctx.separator
    min_lines = 2 if confident_lineation else 3
    if len(lines) < min_lines:
        return None
    lengths = [len(line) for line in lines]
    avg = sum(lengths) / len(lengths)
    empty_count = sum(1 for p in run if p.empty)
    if ctx.named:
        return "verse-block" if _passes(150) else None
    if ctx.separator and len(lines) <= 32:
        return "verse-block" if _passes(110, 160) else None
    if ctx.heading and len(lines) <= 32:
        return "verse-block" if _passes(95, 150) else None
    if ctx.visual and len(lines) <= 64:
        return "verse-block" if _passes(95, 120) else None
    if linebreak_count:
        return "verse-block"
    if empty_count and _passes(120):
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
    slug_lookup: _SlugLookup | None = None,
) -> ir.Document:
    """Run the full normalize chain over `doc` in dependency order."""
    doc.blocks = drop_toc(doc.blocks)
    doc.blocks = scrub_rights(doc.blocks)
    doc.blocks = scrub_ai_alt(doc.blocks)
    lift_bibliography(doc, slug_lookup)
    doc.blocks = strip_endmatter_sections(doc.blocks)
    doc.blocks = strip_bare_bibliography_heading(doc.blocks)
    doc.blocks = thematic_breaks(doc.blocks)
    doc.blocks = demote_headings(doc.blocks, demote_levels)
    doc.blocks = strip_formatting_artifacts(doc.blocks)
    doc.blocks = structural_blocks(doc.blocks)
    doc.blocks = dialogue_labels(doc.blocks)
    doc.blocks = verse_blocks(doc.blocks)
    return doc
