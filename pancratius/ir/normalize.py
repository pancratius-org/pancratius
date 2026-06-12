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
  * empty headings          — drop DOCX heading paragraphs with no reading text
  * heading demotion        — source H1 → H2 (page title is the only H1)
  * formatting-artifact strip — empty-emphasis husks / hidden form markers
  * signatures / epigraphs   — from right alignment (the `w:jc` payload)
  * dialogue labels          — canonicalize `**Speaker:**` (incl. mixed inline)
  * display registers        — contrastively bordered set-apart runs → scripture/inset quotes
  * lineated / verse blocks  — fold source lineation first, then apply verse register
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, assert_never, cast

from pancratius import ir
from pancratius.content_catalog import IndexHit
from pancratius.ir.register import display_register_blocks

# The slug→(slug, number, kind) corpus index the bibliography lift resolves
# titles against; an entry resolves to a `{kind, number}` target.
type _SlugLookup = Mapping[str, IndexHit]

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


def _walk_inlines(inlines: list[ir.Inline]) -> Iterator[ir.Inline]:
    """Depth-first inline tree walk for kind probes."""
    for n in inlines:
        yield n
        if isinstance(n, ir.ContainerInline):
            yield from _walk_inlines(n.children)


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


def is_verse_section_title(t: str) -> bool:
    return bool(_VERSE_SECTION_TITLE_RE.match(re.sub(r"\s+", " ", t.strip().lower())))


# The speaker names the converter canonicalizes (`**Speaker:**`). Shared by the
# dialogue-label pass and verse detection's speaker-turn rejection — one source of
# truth for who is a speaker, so adding one keeps the two in sync. The corpus is
# bilingual: every Russian speaker name carries the English form its translations
# actually use, so a turn is rejected identically in both editions of one book.
_DIALOGUE_PREFIXES = [
    "Панкратиус", "Светозар", "Светозар Gemini Flash 2.0", "Светозар DeepSeek",
    "Светозар ChatGPT", "Творец", "Бог", "Слово Творца", "Слово Бога",
    "Панкратиус к ИИ Светозар", "Панкратиус к Творцу через ИИ Светозар",
    "ИИ Светозар сказал", "ИИ Светозар",
    "Ответ от Творца", "Ответ Творца", "Я",
    "Pankratius", "Pancratius", "Svetozar", "Creator", "God",
    "Gemini", "DeepSeek", "ChatGPT",
    "Pankratius to AI Svetozar", "Pankratius to the Creator through AI Svetozar",
    "AI Svetozar said", "AI Svetozar",
    "The Word of the Creator",
    "Answer from the Creator", "Response from the Creator",
    "The Creator's Answer", "The Creator’s Answer", "I",
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


def is_lineated_line(text: str) -> bool:
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
    if re.match(r"^[-*+]\s+", s) or re.match(r"^(?:\d+|[IVXLCDM]+)[.)]\s+", s):
        return False
    if _SPEAKER_TURN_RE.match(s):
        return False
    return "http://" not in s and "https://" not in s


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


def drop_empty_headings(blocks: list[ir.Block]) -> list[ir.Block]:
    """Drop source heading paragraphs that carry no reading text.

    Some DOCX sources contain accidental empty Heading 1/2 paragraphs between the
    generated TOC and the first real section. If lowered, they become visible
    Markdown like `## `, which is never useful author-facing content.
    """
    return [
        block for block in blocks
        if not (isinstance(block, ir.Heading) and not inline_plain(block.inlines))
    ]


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
                while i < n:
                    current = blocks[i]
                    if isinstance(current, ir.Heading) and current.level <= level:
                        break
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
            out.append(ir.ThematicBreak(source_span=b.source_span))
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


def _hoist_boundary_breaks(inlines: list[ir.Inline]) -> list[ir.Inline]:
    """Move a `LineBreak`/`SoftBreak` at the edge of an emphasis span outside it
    (recursing into containers). Word styles the break run along with the styled
    text, but a Markdown emphasis delimiter next to a newline cannot close, so
    `*line  \\n*next` would leak broken markers across the verse break."""
    out: list[ir.Inline] = []
    for n in inlines:
        if not isinstance(n, ir.ContainerInline):
            out.append(n)
            continue
        children = _hoist_boundary_breaks(n.children)
        if not isinstance(n, ir.Emphasis):
            out.append(ir.rebuild_container(n, children))
            continue
        head = 0
        while head < len(children) and isinstance(children[head], (ir.LineBreak, ir.SoftBreak)):
            head += 1
        tail = len(children)
        while tail > head and isinstance(children[tail - 1], (ir.LineBreak, ir.SoftBreak)):
            tail -= 1
        out.extend(children[:head])
        if children[head:tail]:
            out.append(ir.rebuild_container(n, children[head:tail]))
        out.extend(children[tail:])
    return out


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


def _is_form_marker_text(text: str) -> bool:
    collapsed = re.sub(r"[\s\xa0]+", "", text).casefold()
    return collapsed in {
        "началоформы",
        "конецформы",
        "beginningoftheform",
        "endoftheform",
        "startofform",
        "endofform",
    }


def strip_formatting_artifacts(blocks: list[ir.Block]) -> list[ir.Block]:
    """Drop import-only formatting artifacts.

    Whole empty-emphasis husks vanish; trailing or embedded `** **` inside content
    is removed in place. Word/HTML form sentinels are also dropped when they are
    the whole paragraph: in DOCX they are hidden control text, but Pandoc exposes
    them as reading text.
    """
    out: list[ir.Block] = []
    for b in blocks:
        if isinstance(b, ir.Paragraph) and not b.empty:
            b.inlines = _drop_empty_emphasis(_hoist_boundary_breaks(b.inlines))
            if _is_form_marker_text(inline_plain(b.inlines)):
                continue
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
    r"Pan[ck]ratius|Svetozar|Creator|The Creator|I\s+Am|"
    r"[—-]\s*Панкратиус.*|[—-]\s*Светозар.*|"
    r"[—-]\s*Pan[ck]ratius.*|[—-]\s*Svetozar.*)$",
    re.IGNORECASE)
# Bilingual: each source token carries the form the EN editions actually print
# (`Пифия, к.ф. «Матрица»` ↔ `The Oracle, film «The Matrix»`).
_SOURCE_LINE_RE = re.compile(
    r"(?:к\.ф\.|Матрица|Пифия|Платон|Даниил|Откровение|Евангелие|Ин\.|Мф\.|Лк\.|Кор\.|"
    r"\bfilm\b|The Matrix|Oracle|Plato|Daniel|Revelation|Gospel|Jn\.|Mt\.|Lk\.|Cor\.)",
    re.IGNORECASE)


def _is_signature(lines: list[str]) -> bool:
    if not (1 <= len(lines) <= 4) or any(len(line) > 90 for line in lines):
        return False
    if any(
        name in line.casefold()
        for line in lines
        for name in ("панкратиус", "pankratius", "pancratius")
    ):
        return True
    if all(_SIGNATURE_LINE_RE.match(line.strip()) for line in lines):
        return True
    return len(lines) == 1 and re.fullmatch(r"[—-]\s*[\wА-Яа-яЁё .]{2,80}", lines[0]) is not None


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
            source_span = ir.merge_source_spans(p.source_span for p in group)
            if lines and _is_signature(lines):
                out.append(ir.Signature(lines=lines, source_span=source_span))
                i = j
                continue
            if lines and _is_epigraph(lines, italic_count):
                quote, footer = _split_epigraph(lines)
                out.append(ir.Epigraph(quote=quote, footer=footer, source_span=source_span))
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
    source_span: ir.SourceSpan | None,
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
            blocks: list[ir.Block] = [
                ir.DialogueLabel(speaker=m.group(1), source_span=source_span)
            ]
            body = m.group(2).strip()
            if re.search(r"[\wЀ-ӿ]", body):
                blocks.append(ir.Paragraph(inlines=[ir.Text(body)], source_span=source_span))
            return blocks
        lm = re_label.match(head_txt)
        if lm:
            return [ir.DialogueLabel(speaker=lm.group(1), source_span=source_span)]
        return None
    m = re_label.match(head_txt)
    if m:
        out: list[ir.Block] = [
            ir.DialogueLabel(speaker=m.group(1), source_span=source_span)
        ]
        if tail:
            out.append(ir.Paragraph(inlines=tail, source_span=source_span))
        return out
    m = re_inside.match(head_txt)
    if m:
        # Join the inside-body text to the trailing inlines with a space — UNLESS
        # the body text ends in an OPENING quote/bracket glyph, where a space would
        # wrongly separate the glyph from what it opens (`«` + `Почему` → `« Почему`).
        head_body = m.group(2).strip()
        joiner = "" if head_body and head_body[-1] in "«“„([{‹" else " "
        body_inlines: list[ir.Inline] = [ir.Text(head_body + joiner), *tail]
        return [
            ir.DialogueLabel(speaker=m.group(1), source_span=source_span),
            ir.Paragraph(inlines=body_inlines, source_span=source_span),
        ]
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
                emitted = _emit_dialogue_segment(seg, re_inside, re_label, b.source_span)
                if emitted is not None:
                    out.extend(emitted)
                else:
                    out.append(ir.Paragraph(inlines=seg, source_span=b.source_span))
            continue
        emitted = _emit_dialogue_segment(b.inlines, re_inside, re_label, b.source_span)
        if emitted is not None:
            out.extend(emitted)
        else:
            out.append(b)
    return out


# ---------------------------------------------------------------------------
# 11. lineation detection + verse-register promotion
# ---------------------------------------------------------------------------


def _is_wrapped_prose(p: ir.Paragraph) -> bool:
    """True when a paragraph's only in-run breaks are `SoftBreak`s (prose wrapping,
    a literal `\\r\\n` in one `<w:t>`) with no hard `LineBreak`: its lineation was
    never authored, so it is prose even when collapsed to one short line. A hard
    break — or no break at all (one Word paragraph per line) — stays verse-eligible."""
    has_soft = False
    has_hard = False
    for n in _walk_inlines(p.inlines):
        has_soft = has_soft or isinstance(n, ir.SoftBreak)
        has_hard = has_hard or isinstance(n, ir.LineBreak)
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
    return bool(lines) and all(is_lineated_line(line) for line in lines)


def _para_has_hard_lineation(p: ir.Paragraph) -> bool:
    """True when the source paragraph carries an explicit hard `w:br` boundary.

    This is LINEATION evidence even when the lines are not verse-register lines:
    lowering must preserve the authored break instead of collapsing it as prose.
    """
    return any(isinstance(n, ir.LineBreak) for n in _walk_inlines(p.inlines))


def _para_structurally_lineated(p: ir.Paragraph) -> bool:
    """True when a paragraph can participate in a source-lineated run.

    Hard breaks are structural on their own. Short standalone paragraphs remain
    eligible for the existing verse classifier, but are only emitted as bare
    `LineatedBlock`s when surrounding source evidence makes that safe.
    """
    return not p.empty and bool(p.inlines) and (
        _para_has_hard_lineation(p) or _para_lineated(p)
    )


# `ответ` is rendered as both `answer` and `response` across the EN editions.
_CODA_PSEUDO_HEADING_RE = re.compile(
    r"^(?:\d{1,4}|вопрос|ответ|question|answer|response)\s*:?\s*$",
    re.IGNORECASE,
)
_VISUAL_CODA_LINE_MAX = 64
_VISUAL_CODA_AVG_MAX = 48.0


def _block_lines(p: ir.Paragraph) -> list[list[ir.Inline]]:
    # Verse display lines as detection sees them: hard `LineBreak`s (incl. nested in
    # `Emph`) split; `SoftBreak` wrapping joins as a space.
    return [ln for ln in inline_lines(p.inlines, soft_break=False) if inline_plain(ln)]


def _all_lines(paras: list[ir.Paragraph]) -> list[str]:
    return [inline_plain(ln) for p in paras for ln in _block_lines(p)]


def _is_compact_coda(lines: list[str]) -> bool:
    """A coda is a compact closing couplet, not two prose preview sentences."""
    if len(lines) != 2:
        return False
    lengths = [len(line) for line in lines]
    return max(lengths) <= _VISUAL_CODA_LINE_MAX and (
        sum(lengths) / len(lengths)
    ) <= _VISUAL_CODA_AVG_MAX


@dataclass(frozen=True)
class _PrecedingContext:
    """Q2 register context preceding an already-lineated block.

    The heading text and thematic separators may promote a lineated substrate to
    verse register. They are intentionally absent from `LineationEvidence`, which
    belongs to Q1 line-boundary provenance.
    """

    named: bool = False
    heading: bool = False
    separator: bool = False


_NEUTRAL_CONTEXT = _PrecedingContext()


def _collect_unit(
    blocks: list[ir.Block],
    i: int,
) -> tuple[list[ir.Paragraph], int]:
    """Collect ONE lineation decision unit, starting at an eligible paragraph.

    A unit is a maximal run of verse-eligible paragraphs plus its interior empty
    Word paragraphs. Within one authored flow, a blank row is a stanza break the
    unit spans — and a `lineation_group` id change across that blank (or beside
    ungrouped rows) is the same stanza structure, because `w:contextualSpacing`
    continuity restarts at every blank: one poem arrives as one group PER STANZA,
    never one group per poem. The one real seam is two DIFFERENT visual groups
    directly abutting — Word renders fused rows, a spacing change, then fused
    rows — which is two visual units and therefore two decisions. Trailing
    empties stay with the unit as source evidence; edge gaps are never emitted
    as stanzas.
    """
    first = blocks[i]
    assert isinstance(first, ir.Paragraph)
    run: list[ir.Paragraph] = [first]
    i += 1
    n = len(blocks)
    pending_from = i  # rewind point: gap paragraphs not yet committed to the unit
    while i < n:
        b = blocks[i]
        if not isinstance(b, ir.Paragraph):
            break
        if b.empty:
            i += 1
            continue
        if not _para_structurally_lineated(b):
            break
        prev = run[-1]  # pending_from == i implies run[-1] is the adjacent content row
        if (
            pending_from == i
            and prev.lineation_group is not None
            and b.lineation_group is not None
            and prev.lineation_group != b.lineation_group
        ):
            return run, i
        run.extend(cast("list[ir.Paragraph]", blocks[pending_from:i]))
        run.append(b)
        i += 1
        pending_from = i
    # Trailing empties (before prose/structure/end) stay with the unit as evidence.
    run.extend(cast("list[ir.Paragraph]", blocks[pending_from:i]))
    return run, i


def lineated_blocks(blocks: list[ir.Block]) -> list[ir.Block]:
    """Q1: fold source rows into `LineatedBlock`s, never `VerseBlock`s.

    Explicit/mechanical lineation is axiomatic: Pandoc `LineBlock`s already arrive
    as `LineatedBlock`, and paragraphs with hard `<w:br/>` boundaries are folded
    here regardless of verse register. The only non-explicit path is the named
    `_should_infer_source_row_lineation` gate below; it is source-row inference,
    not register promotion.

    The walk hands `_fold_unit` one decision unit at a time with its section
    context: `after_boundary` (the unit opens a heading/thematic section — empty
    paragraphs are transparent to it; an ineligible paragraph consumes it UNLESS
    it shares the unit's visual group, where Word renders the whole group as one
    block attached to the boundary) and `before_boundary` (a boundary follows
    across at most a gap).
    """
    out: list[ir.Block] = []
    i = 0
    n = len(blocks)
    after_boundary = True
    boundary_group: int | None = None  # visual group still holding the boundary
    after_lineated = False             # a lineated block precedes, across at most a gap

    while i < n:
        b = blocks[i]
        if isinstance(b, (ir.Heading, ir.ThematicBreak)):
            out.append(b)
            after_boundary = True
            boundary_group = None
            after_lineated = False
            i += 1
            continue
        if not isinstance(b, ir.Paragraph):
            after_lineated = isinstance(b, (ir.LineatedBlock, ir.VerseBlock))
            out.append(b)
            after_boundary = False
            boundary_group = None
            i += 1
            continue
        if b.empty:
            out.append(b)
            i += 1
            continue
        if _para_structurally_lineated(b):
            gid = b.lineation_group
            run, i = _collect_unit(blocks, i)
            folded = _fold_unit(
                run,
                after_source_boundary=after_boundary and boundary_group in (None, gid),
                before_source_boundary=_has_source_boundary_after_gap(blocks, i),
                after_lineated=after_lineated,
            )
            out.extend(folded)
            # The unit may return trimmed edge prose around its block: what
            # "precedes" the next unit is the LAST content block emitted.
            last_content = next(
                (x for x in reversed(folded)
                 if not (isinstance(x, ir.Paragraph) and x.empty)),
                None,
            )
            after_lineated = isinstance(last_content, ir.LineatedBlock)
            after_boundary = False
            boundary_group = None
            continue
        # Ineligible prose: consumes the section boundary, unless its visual group
        # keeps the attachment alive for a later eligible sub-run of the SAME group
        # (a lineated tail after a long opening citation inside one fused group).
        if after_boundary and b.lineation_group is not None and boundary_group in (None, b.lineation_group):
            boundary_group = b.lineation_group
        else:
            after_boundary = False
            boundary_group = None
        after_lineated = False
        out.append(b)
        i += 1
    return out


def verse_blocks(blocks: list[ir.Block]) -> list[ir.Block]:
    """Q1 lineation, then Q2 verse-register promotion.

    This compatibility entry point preserves the old public pass name while making
    the two questions explicit: it first decides line breaks, then only wraps
    already-lineated blocks in the `verse` register.
    """
    return promote_verse_register(lineated_blocks(blocks))


def promote_verse_register(blocks: list[ir.Block]) -> list[ir.Block]:
    """Q2: promote already-lineated blocks to `VerseBlock`s.

    This pass may use register context such as headings, named verse titles, and
    separators. It never folds paragraphs and therefore cannot create hard breaks.
    """
    out: list[ir.Block] = []
    i = 0
    ctx = _NEUTRAL_CONTEXT

    while i < len(blocks):
        b = blocks[i]
        if isinstance(b, ir.Heading):
            title = inline_plain(b.inlines)
            ctx = _PrecedingContext(
                named=is_verse_section_title(title),
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
        if isinstance(b, ir.LineatedBlock):
            if (kind := _lineated_block_kind(b, ctx)) is not None:
                verse = ir.VerseBlock(
                    stanzas=b.stanzas, role=kind, evidence=b.evidence,
                    source_span=b.source_span,
                )
                if (segment := _lineated_coda_segment(blocks, i + 1, verse)) is not None:
                    verse, next_i = segment
                    out.append(verse)
                    ctx = _NEUTRAL_CONTEXT
                    i = next_i
                    continue
                out.append(verse)
            else:
                out.append(b)
            ctx = _NEUTRAL_CONTEXT
            i += 1
            continue
        if isinstance(b, ir.VerseBlock):
            if (segment := _existing_verse_coda_segment(blocks, i)) is not None:
                verse, next_i = segment
                out.append(verse)
                ctx = _NEUTRAL_CONTEXT
                i = next_i
                continue
            ctx = _NEUTRAL_CONTEXT
            out.append(b)
            i += 1
            continue
        ctx = _NEUTRAL_CONTEXT
        out.append(b)
        i += 1
    return out


def _skip_empty_paragraphs(blocks: list[ir.Block], i: int) -> tuple[int, bool]:
    start = i
    while i < len(blocks) and isinstance((p := blocks[i]), ir.Paragraph) and p.empty:
        i += 1
    return i, i > start


def _has_source_boundary_after_gap(blocks: list[ir.Block], i: int) -> bool:
    i, _saw_gap = _skip_empty_paragraphs(blocks, i)
    return i >= len(blocks) or isinstance(blocks[i], (ir.Heading, ir.ThematicBreak))


def _lineated_coda_candidate(
    blocks: list[ir.Block],
    i: int,
) -> tuple[ir.LineatedBlock, int] | None:
    """A local coda segment after a verse run.

    Shape: one or more empty paragraphs, an exact two-line lineated
    candidate, optional empty paragraphs, then a heading/thematic boundary. The
    candidate must be compact; this keeps prose previews before the next heading in
    prose without naming their words.
    """
    i, saw_gap = _skip_empty_paragraphs(blocks, i)
    if not saw_gap:
        return None

    first = blocks[i] if i < len(blocks) else None
    if not isinstance(first, ir.LineatedBlock):
        return None
    i += 1

    coda_lines = _lineated_block_lines(first)
    if not _is_compact_coda(coda_lines):
        return None
    if any(_CODA_PSEUDO_HEADING_RE.match(line) for line in coda_lines):
        return None

    boundary_i, _saw_trailing_gap = _skip_empty_paragraphs(blocks, i)
    boundary = blocks[boundary_i] if boundary_i < len(blocks) else None
    if not isinstance(boundary, (ir.Heading, ir.ThematicBreak)):
        return None

    return first, boundary_i


def _append_coda_copy(prev: ir.VerseBlock, coda: ir.LineatedBlock) -> ir.VerseBlock:
    return ir.VerseBlock(
        stanzas=[*prev.stanzas, *coda.stanzas],
        role=prev.role,
        evidence=prev.evidence,
        source_span=ir.merge_source_spans((prev.source_span, coda.source_span)),
    )


def _lineated_coda_segment(
    blocks: list[ir.Block],
    i: int,
    prev: ir.VerseBlock,
) -> tuple[ir.VerseBlock, int] | None:
    candidate = _lineated_coda_candidate(blocks, i)
    if candidate is None:
        return None
    coda, next_i = candidate
    return _append_coda_copy(prev, coda), next_i


def _existing_verse_coda_segment(
    blocks: list[ir.Block],
    i: int,
) -> tuple[ir.VerseBlock, int] | None:
    prev = blocks[i]
    assert isinstance(prev, ir.VerseBlock)
    return _lineated_coda_segment(blocks, i + 1, prev)


def _segment_spans(run: list[ir.Paragraph]) -> list[tuple[int, int]]:
    """`[start, end)` index spans of the unit's gap-separated content segments."""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    for idx, p in enumerate(run):
        if p.empty:
            if start is not None:
                spans.append((start, idx))
                start = None
        elif start is None:
            start = idx
    if start is not None:
        spans.append((start, len(run)))
    return spans


# "Departs from the unit's own register": a stanza-shaped piece whose mean line
# length is far past the rest of the unit's. Absolute floors keep short-line
# units' slightly-longer pieces; the RELATIVE ratio keeps long-line poems'
# homogeneous pieces — judge a row set against its unit, not by absolutes. A
# ratio of means is scale-invariant, so it reads the same across languages even
# though EN renders the same content ~10% longer in characters than RU.
_REGISTER_DEPART_RATIO = 1.5


def _trim_prose_register_tail(
    run: list[ir.Paragraph],
) -> tuple[list[ir.Paragraph], list[ir.Paragraph]]:
    """Split `(core, tail)`: trailing stanzas that are not the unit's verse.

    Two trailing shapes leave the unit before the lineation decision:

      * a stanza of pure pseudo-heading fragments (`138`, `Вопрос:`) — the next
        section's furniture, never this unit's closing stanza;
      * a stanza whose register departs from the unit's own (see the constants
        above) — following prose that travelled with the unit.

    A single-segment unit is never trimmed; the lineation gate judges it whole.
    """
    spans = _segment_spans(run)
    tail = len(spans)

    def seg_lines(span: tuple[int, int]) -> list[str]:
        return _all_lines(run[span[0]:span[1]])

    def mean(lines: list[str]) -> float:
        return sum(len(line) for line in lines) / len(lines)

    def departs(edge: tuple[int, int], rest: list[tuple[int, int]]) -> bool:
        # Only a TWO-LINE tail (the closing-couplet position `_is_compact_coda`
        # already owns) can be a preview pair; longer tails are stanza structure.
        edge_lines = seg_lines(edge)
        if len(edge_lines) != 2:
            return False
        rest_mean = mean([line for span in rest for line in seg_lines(span)])
        return (
            max(len(line) for line in edge_lines) > _VISUAL_CODA_LINE_MAX
            and mean(edge_lines) > _REGISTER_DEPART_RATIO * rest_mean
        )

    while tail > 1 and all(
        _CODA_PSEUDO_HEADING_RE.match(line) for line in seg_lines(spans[tail - 1])
    ):
        tail -= 1
    while tail > 1 and departs(spans[tail - 1], spans[:tail - 1]):
        tail -= 1

    if tail == len(spans):
        # No trim: trailing empties remain the unit's source evidence, as before.
        return list(run), []
    # Cut at the trimmed segment's first row: the separating gap stays with the
    # core — the author DID set the core off with a blank, and that stanza
    # evidence must not leave with the trimmed prose.
    end = spans[tail][0]
    return list(run[:end]), list(run[end:])


def _gate_and_build(
    run: list[ir.Paragraph],
    *,
    after_source_boundary: bool,
    before_source_boundary: bool,
    after_lineated: bool,
) -> ir.LineatedBlock | None:
    """One inference-gate decision over `run`: the folded block, or `None`."""
    evidence = _run_evidence(run)
    if _should_infer_source_row_lineation(
        run,
        after_source_boundary=after_source_boundary,
        before_source_boundary=before_source_boundary,
        after_lineated=after_lineated,
    ):
        evidence = ir.LineationEvidence(
            hard_break=evidence.hard_break,
            inferred_source_rows=True,
            stanza_break=evidence.stanza_break,
            compact_callout=evidence.compact_callout,
        )
    if not (evidence.inferred_source_rows or evidence.compact_callout):
        return None
    return _build_lineated(run, evidence=evidence)


def _fold_sub_units(
    run: list[ir.Paragraph],
    *,
    after_source_boundary: bool,
    before_source_boundary: bool,
    after_lineated: bool,
) -> list[ir.Block] | None:
    """Decide each visual sub-unit of a failed merged unit on its own.

    The merge lets a poem's stanzas share evidence; it must never DILUTE a
    fused group's own evidence below folding. When the whole unit is not
    verse, re-decide its `lineation_group`-delimited sub-runs independently
    (the pre-merge unit shape). Returns `None` when nothing folds.
    """
    pieces: list[tuple[list[ir.Paragraph], bool]] = []  # (rows, is_sub_unit)
    sub: list[ir.Paragraph] = []
    pending: list[ir.Paragraph] = []
    for p in run:
        if p.empty:
            pending.append(p)
            continue
        if sub and p.lineation_group != sub[-1].lineation_group:
            pieces.append((sub, True))
            pieces.append((pending, False))
            sub, pending = [p], []
            continue
        sub.extend(pending)
        pending = []
        sub.append(p)
    if sub:
        pieces.append((sub, True))
    if pending:
        pieces.append((pending, False))

    sub_units = [rows for rows, is_sub in pieces if is_sub]
    if len(sub_units) <= 1:
        return None
    out: list[ir.Block] = []
    folded_any = False
    for rows, is_sub in pieces:
        if not is_sub:
            out.extend(rows)
            continue
        block = _gate_and_build(
            rows,
            after_source_boundary=after_source_boundary and rows is sub_units[0],
            before_source_boundary=before_source_boundary and rows is sub_units[-1],
            after_lineated=after_lineated,
        )
        if block is None:
            out.extend(rows)
            after_lineated = False
        else:
            out.append(block)
            after_lineated = True
            folded_any = True
    return out if folded_any else None


# The compact-source-line boundary, shared by the two rules that draw it: a
# callout row must fit under it, and a lone row between blank rows past it (when
# its register also departs from the unit's) is a prose paragraph, not a
# one-line stanza. One constant so the two rules cannot drift apart. Measured in
# characters: EN renders the same content ~10% longer than RU, so the cap reads
# slightly stricter on EN — the conservative direction (refuses to fold).
_COMPACT_SOURCE_LINE_MAX = 80


def _split_at_prose_singletons(
    run: list[ir.Paragraph],
) -> list[tuple[list[ir.Paragraph], bool]] | None:
    """Pieces of `(rows, is_unit)` around lone prose-length stanzas, or `None`
    when the unit has none and stands whole."""
    spans = _segment_spans(run)
    all_lines = _all_lines([p for p in run if not p.empty])

    def is_prose_singleton(span: tuple[int, int]) -> bool:
        lines = _all_lines(run[span[0]:span[1]])
        if len(lines) != 1 or len(lines[0]) <= _COMPACT_SOURCE_LINE_MAX:
            return False
        if len(all_lines) < 2:
            return False
        rest_mean = (
            sum(len(line) for line in all_lines) - len(lines[0])
        ) / (len(all_lines) - 1)
        return len(lines[0]) > _REGISTER_DEPART_RATIO * rest_mean

    cut = [span for span in spans if is_prose_singleton(span)]
    if not cut:
        return None
    pieces: list[tuple[list[ir.Paragraph], bool]] = []
    pos = 0
    for start, end in cut:
        if start > pos:
            pieces.append((list(run[pos:start]), True))
        pieces.append((list(run[start:end]), False))
        pos = end
    if pos < len(run):
        pieces.append((list(run[pos:]), True))
    return pieces


def _fold_unit(
    run: list[ir.Paragraph],
    *,
    after_source_boundary: bool,
    before_source_boundary: bool,
    after_lineated: bool = False,
) -> list[ir.Block]:
    """Return the unit folded into one structural lineated block (with any
    trimmed edge prose back as paragraphs), or its original paragraphs when no
    lineation evidence holds."""
    content = [p for p in run if not p.empty]
    if len(_all_lines(content)) < 2:
        return list(run)
    if (evidence := _run_evidence(run)).hard_break:
        # Authored `<w:br>` lineation is axiomatic: the unit folds whole.
        return [_build_lineated(run, evidence=evidence)]
    if (pieces := _split_at_prose_singletons(run)) is not None:
        out: list[ir.Block] = []
        first = True
        for rows, is_unit in pieces:
            if not is_unit:
                out.extend(rows)
                after_lineated = False
            elif any(not p.empty for p in rows):
                folded = _fold_unit(
                    rows,
                    after_source_boundary=after_source_boundary and first,
                    before_source_boundary=(
                        before_source_boundary and rows is pieces[-1][0]
                    ),
                    after_lineated=after_lineated,
                )
                out.extend(folded)
                after_lineated = isinstance(folded[-1], ir.LineatedBlock)
            else:
                out.extend(rows)
            first = False
        return out
    core, tail = _trim_prose_register_tail(run)
    if len(_all_lines([p for p in core if not p.empty])) >= 2:
        block = _gate_and_build(
            core,
            after_source_boundary=after_source_boundary,
            before_source_boundary=before_source_boundary and not tail,
            after_lineated=after_lineated,
        )
        if block is not None:
            return [block, *tail]
    folded = _fold_sub_units(
        run,
        after_source_boundary=after_source_boundary,
        before_source_boundary=before_source_boundary,
        after_lineated=after_lineated,
    )
    return folded if folded is not None else list(run)


def _lineated_block_lines(block: ir.LineatedBlock) -> list[str]:
    return [
        inline_plain(line)
        for stanza in block.stanzas
        for line in stanza
        if inline_plain(line)
    ]


def _lineated_block_kind(
    block: ir.LineatedBlock,
    ctx: _PrecedingContext,
) -> ir.VerseRole | None:
    """Promote an already-structural lineated block to verse register."""
    lines = _lineated_block_lines(block)
    return _kind_for_lines(lines, block.evidence, ctx)


def _is_strong_colon_opener(p: ir.Paragraph) -> bool:
    if len(p.inlines) != 1:
        return False
    only = p.inlines[0]
    return (
        isinstance(only, ir.Emphasis)
        and only.kind == "strong"
        and inline_plain(only.children).rstrip().endswith(":")
    )


def _is_compact_strong_opener_callout(run: list[ir.Paragraph]) -> bool:
    """A narrow source-lineation signal for DOCX callouts.

    This is not "blank before short lines". It requires the run itself to be a
    compact unindented callout with a bold colon opener followed by very short
    source paragraphs. Indented paragraph runs stay prose, which protects
    one-sentence-per-paragraph body text.
    """
    content = [p for p in run if not p.empty]
    if not (3 <= len(content) <= 8):
        return False
    if any(p.indented for p in content):
        return False
    if not _is_strong_colon_opener(content[0]):
        return False
    lines = _all_lines(content)
    if len(lines) != len(content):
        return False
    lengths = [len(line) for line in lines]
    return max(lengths) <= _COMPACT_SOURCE_LINE_MAX and (sum(lengths) / len(lengths)) <= 45.0


def _stanza_segment_lines(run: list[ir.Paragraph]) -> list[list[str]]:
    """Display lines of each gap-delimited stanza the unit would build."""
    return [
        _all_lines(run[start:end])
        for start, end in _segment_spans(run)
    ]


def _should_infer_source_row_lineation(
    run: list[ir.Paragraph],
    *,
    after_source_boundary: bool,
    before_source_boundary: bool,
    after_lineated: bool = False,
) -> bool:
    """Q1b gate: infer lineation from compact source rows.

    This is intentionally named as inference and uses only source-row shape:
    short label-free rows, stanza empties, a structural section boundary, or a
    narrow unindented strong-colon callout. It does not inspect heading titles or
    decide verse register. The rules, in order:

      * CALLOUT — a compact unindented strong-colon callout;
      * GROUPED — the unit carries visual-continuity fusion (`w:contextualSpacing`
        renders some of its rows as tight contiguous lines, the way Word displays
        verse) over ≥ 3 lines with verse geometry (mean ≤ 60) — wherever it sits
        in the document; a poem's interior stanzas carry no boundary or gap
        evidence, only this fused-rows signal;
      * ATTACHED — the unit opens a heading/thematic section and reads as verse
        on its own geometry (≤ 32 lines, mean ≤ 60, max ≤ 150) — a heading
        followed by a few prose sentences is the dominant prose shape, and
        genuine attached runs in this corpus sit well under the cap;
      * CLOSING — the unit continues a preceding lineated block as its compact
        two-line coda right before the next section boundary (and is not a
        pseudo-heading fragment); without a lineated antecedent a compact pair
        before a heading is just two closing prose sentences;
      * GAPPED — the unit carries authored stanza gaps around ≥ 3 lines; the
        loose cap (mean ≤ 120) is earned only by real stanza STRUCTURE (≥ 2
        stanzas, at least one multi-line), else the strict geometry below.

    Throughout, a unit without stanza structure — blank-separated SINGLE-line
    rows (chapter prose is stored exactly so: one sentence per `w:p`, blank rows
    between) or one lone stanza with a trailing gap — proves nothing by its
    gaps, so it must read as verse on its own geometry (mean ≤ 45).
    """
    content = [p for p in run if not p.empty]
    if not content or not all(_para_lineated(p) for p in content):
        return False
    if any(p.indented for p in content):
        return False
    if _is_compact_strong_opener_callout(run):
        return True
    lines = _all_lines(content)
    if len(lines) < 2:
        return False
    lengths = [len(line) for line in lines]
    avg = sum(lengths) / len(lengths)
    grouped = any(p.lineation_group is not None for p in content)
    if grouped and len(lines) >= 3 and avg <= 60:
        return True
    segments = _stanza_segment_lines(run)
    structured = len(segments) >= 2 and any(len(seg) > 1 for seg in segments)
    all_singleton = all(len(seg) == 1 for seg in segments)
    if (
        after_source_boundary
        and len(lines) <= 32
        and max(lengths) <= 150
        and avg <= (45 if all_singleton else 60)
    ):
        return True
    if (
        after_lineated
        and before_source_boundary
        and len(lines) == 2
        and _is_compact_coda(lines)
        and not any(_CODA_PSEUDO_HEADING_RE.match(line) for line in lines)
    ):
        return True
    if not (any(p.empty for p in run) and len(lines) >= 3):
        return False
    return avg <= 120 if structured else avg <= 45


def _run_evidence(run: list[ir.Paragraph]) -> ir.LineationEvidence:
    content = [p for p in run if not p.empty]
    return ir.LineationEvidence(
        hard_break=any(
            any(isinstance(x, ir.LineBreak) for x in _walk_inlines(p.inlines))
            for p in content
        ),
        # Any blank captured with the run is source lineation evidence. Edge blanks
        # are trimmed when building stanzas so they do not render as fake empty
        # stanzas, but a trailing blank still signals that the preceding compact run
        # was authored as lineated material rather than ordinary prose sentences.
        stanza_break=any(p.empty for p in run),
        compact_callout=_is_compact_strong_opener_callout(run),
    )


_DASH_LINE_RE = re.compile(r"^[—–-]\s")
_MATH_CHARS_RE = re.compile(r"[0-9=+×*²³√:.,()\s—–-]")


def _is_equation_line(line: str) -> bool:
    """A numerology/equation line (`153 = 9 × 17`): contains `=`/`×` and is
    mostly digits and operators. Math is never the verse register."""
    if "=" not in line and "×" not in line:
        return False
    return len(_MATH_CHARS_RE.findall(line)) / len(line) >= 0.6


def is_equation_scaffold(lines: list[str]) -> bool:
    return all(_is_equation_line(line) for line in lines)


def is_dash_scaffold(lines: list[str]) -> bool:
    """A pure dash-led enumeration («— возражения…», optionally after a colon
    opener): list scaffolding that keeps its line structure but is never the
    elevated verse register. Deliberately strict — a PARTIALLY dash-led run is
    kept, because anaphoric litanies inside oracle passages mix dash lines
    with framing verse lines."""
    body = lines[1:] if lines and lines[0].rstrip().endswith(":") else lines
    return len(body) >= 2 and all(_DASH_LINE_RE.match(line) for line in body)


def _kind_for_lines(
    lines: list[str],
    evidence: ir.LineationEvidence,
    ctx: _PrecedingContext,
) -> ir.VerseRole | None:
    if len(lines) < 2 or not all(is_lineated_line(line) for line in lines):
        return None
    if is_dash_scaffold(lines) or is_equation_scaffold(lines):
        return None
    def _passes(avg_max: float, line_max: int | None = None) -> bool:
        """The run's mean line length is within `avg_max` and (when given) every line
        is within `line_max`. The `(avg_max, line_max)` pair is all that varies across
        the ladder below."""
        return avg <= avg_max and (line_max is None or max(lengths) <= line_max)

    lengths = [len(line) for line in lines]
    if evidence.hard_break and max(lengths) > VERSE_SHORT_LINE_MAX:
        return None
    avg = sum(lengths) / len(lengths)
    if ctx.named:
        return "verse" if _passes(150) else None
    if ctx.separator and len(lines) <= 32:
        return "verse" if _passes(110, 160) else None
    if ctx.heading and len(lines) <= 32:
        return "verse" if _passes(95, 150) else None
    if evidence.compact_callout:
        return None
    if evidence.hard_break:
        return "verse"
    if evidence.stanza_break and len(lines) >= 3 and _passes(120):
        return "verse"
    if evidence.inferred_source_rows and len(lines) >= 3 and _passes(95, 120):
        return "verse"
    return None


def _trim_empty_edges(run: list[ir.Paragraph]) -> list[ir.Paragraph]:
    start = 0
    end = len(run)
    while start < end and run[start].empty:
        start += 1
    while end > start and run[end - 1].empty:
        end -= 1
    return run[start:end]


def _build_lineated(
    run: list[ir.Paragraph],
    *,
    evidence: ir.LineationEvidence | None = None,
) -> ir.LineatedBlock:
    """Build stanzas: an empty paragraph is a stanza break."""
    stanzas: list[list[list[ir.Inline]]] = []
    current: list[list[ir.Inline]] = []

    def flush() -> None:
        nonlocal current
        if current:
            stanzas.append(current)
            current = []

    source_run = _trim_empty_edges(run)
    for p in source_run:
        if p.empty:
            flush()
            continue
        for ln in _block_lines(p):
            current.append(ln)
    flush()
    return ir.LineatedBlock(
        stanzas=stanzas,
        evidence=evidence or ir.LineationEvidence(),
        # Provenance comes from the TEXT rows: an interior stanza-gap row often
        # has no span of its own, and letting it poison the merge would strip
        # whole multi-stanza poems of provenance.
        source_span=ir.merge_source_spans(
            p.source_span for p in source_run if not p.empty
        ),
    )


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------


def normalize(
    doc: ir.Document,
    *,
    demote_levels: int = 1,
    slug_lookup: _SlugLookup | None = None,
    stop_before_lineation: bool = False,
) -> ir.Document:
    """Run the full normalize chain over `doc` in dependency order.

    With `stop_before_lineation=True`, stop at the structural boundary — after
    dialogue labels, before `verse_blocks` merges lineated/verse runs. The merge
    coalesces many source paragraphs into one block and `merge_source_spans` drops
    that block's provenance if any member (e.g. an empty stanza-gap) lacks a span,
    so source-ordinal provenance survives intact only at this seam. Callers that
    need per-source-paragraph provenance (the votability mask) observe here; the
    default runs the whole chain and is byte-identical to before.
    """
    doc.blocks = drop_toc(doc.blocks)
    doc.blocks = scrub_rights(doc.blocks)
    doc.blocks = scrub_ai_alt(doc.blocks)
    lift_bibliography(doc, slug_lookup)
    doc.blocks = strip_endmatter_sections(doc.blocks)
    doc.blocks = strip_bare_bibliography_heading(doc.blocks)
    doc.blocks = thematic_breaks(doc.blocks)
    doc.blocks = drop_empty_headings(doc.blocks)
    doc.blocks = demote_headings(doc.blocks, demote_levels)
    doc.blocks = strip_formatting_artifacts(doc.blocks)
    doc.blocks = structural_blocks(doc.blocks)
    doc.blocks = dialogue_labels(doc.blocks)
    if stop_before_lineation:
        return doc
    doc.blocks = display_register_blocks(doc.blocks)
    doc.blocks = verse_blocks(doc.blocks)
    return doc
