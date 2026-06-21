# import-pure: no filesystem mutation
"""Mechanical noise-removal passes: TOC, rights boilerplate, AI alt text,
thematic breaks, empty/demoted headings, formatting artifacts."""

from __future__ import annotations

import re
from dataclasses import replace

from pancratius import ir
from pancratius.ir.inlines import inline_plain, walk_inlines
from pancratius.thematic import is_thematic_marker

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

# ---------------------------------------------------------------------------
# TOC drop — auto-generated table-of-contents link runs
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
    links = [n for n in walk_inlines(p.inlines) if isinstance(n, ir.Link)]
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
# rights-boilerplate scrub
# ---------------------------------------------------------------------------


def head_region_end(blocks: list[ir.Block]) -> int:
    """The exclusive end of the source headmatter window: up to the first H1, but
    never past the first ~3% of the document (with a 20-block floor). Shared by
    the rights scrub and the endmatter strip so both read one head region."""
    n = len(blocks)
    first_h1 = next((i for i, b in enumerate(blocks) if isinstance(b, ir.Heading) and b.level == 1), n)
    return min(first_h1, max(20, int(n * 0.03)))


def scrub_rights(blocks: list[ir.Block]) -> list[ir.Block]:
    """Drop rights boilerplate without touching ordinary book body text.

    This pass only handles standalone boilerplate paragraphs near the beginning of
    a source. Heading-delimited sections are stripped later by
    `strip_endmatter`, after bibliography tables have had a chance to lift
    into the sidecar.
    """
    n = len(blocks)
    if n == 0:
        return blocks
    window_end = head_region_end(blocks)
    out: list[ir.Block] = []
    for i, b in enumerate(blocks):
        if i < window_end and isinstance(b, ir.Paragraph) and not b.empty:
            text = inline_plain(b.inlines)
            if any(pat.fullmatch(text) for pat in RIGHTS_PATTERNS):
                continue
        out.append(b)
    return out


# ---------------------------------------------------------------------------
# AI-alt scrub — strip machine-vision alt text
# ---------------------------------------------------------------------------


def is_ai_alt(alt: str) -> bool:
    return any(frag in alt for frag in AI_ALT_FRAGMENTS)


def _scrub_alt_in_inlines(inlines: list[ir.Inline]) -> list[ir.Inline]:
    out: list[ir.Inline] = []
    for n in inlines:
        # isinstance, not match: the container arm tests `ir.ContainerInline`
        # (a runtime tuple), which can't appear in a `case`.
        if isinstance(n, ir.ImageInline) and is_ai_alt(n.alt):
            out.append(ir.ImageInline(src=n.src, alt="", asset_id=n.asset_id))
        elif isinstance(n, ir.ContainerInline):
            out.append(ir.rebuild_container(n, _scrub_alt_in_inlines(n.children)))
        else:
            out.append(n)
    return out


def scrub_ai_alt(blocks: list[ir.Block]) -> list[ir.Block]:
    out: list[ir.Block] = []
    for b in blocks:
        # An `ImageBlock`'s alt is a block field the shared inline-descent can't
        # reach; rebuild it here. Every inline-list leaf is reached by the skeleton.
        if isinstance(b, ir.ImageBlock):
            out.append(replace(b, alt="") if is_ai_alt(b.alt) else b)
        else:
            out.append(ir.map_block_inlines(b, _scrub_alt_in_inlines))
    return out


# ---------------------------------------------------------------------------
# ChatGPT-citation scrub — strip auto-injected web-search citations
# ---------------------------------------------------------------------------

# ChatGPT appends this tracking tag to every web-search citation URL it injects. No
# author-typed URL carries it, so it is an exact, safe discriminator: the author's own
# conversation links (`chatgpt.com/share/…`, `…/c/…`) have no `utm_source`, so they stay.
_CHATGPT_CITATION_TAG = "utm_source=chatgpt.com"


def _is_citation_link(n: ir.Inline) -> bool:
    return isinstance(n, ir.Link) and _CHATGPT_CITATION_TAG in n.target


def _is_ws_text(n: ir.Inline) -> bool:
    return isinstance(n, ir.Text) and n.value.isspace()


def _is_literal(n: ir.Inline, ch: str) -> bool:
    return isinstance(n, ir.Text) and n.value == ch


def _drop_lead_ws(out: list[ir.Inline]) -> None:
    """Drop the single whitespace run that led into a just-removed citation, so the
    sentence closes cleanly (`дхарму. [pill](url)` → `дхарму.`)."""
    if out and _is_ws_text(out[-1]):
        out.pop()


def _scrub_citations_in_inlines(inlines: list[ir.Inline]) -> list[ir.Inline]:
    out: list[ir.Inline] = []
    i = 0
    while i < len(inlines):
        node = inlines[i]
        nxt = inlines[i + 1] if i + 1 < len(inlines) else None
        nxt2 = inlines[i + 2] if i + 2 < len(inlines) else None
        # a parenthesized citation `([pill](url))` — drop the wrapping parens with it
        if _is_literal(node, "(") and nxt is not None and _is_citation_link(nxt) \
                and nxt2 is not None and _is_literal(nxt2, ")"):
            _drop_lead_ws(out)
            i += 3
            continue
        if _is_citation_link(node):
            _drop_lead_ws(out)
            i += 1
            continue
        if isinstance(node, ir.ContainerInline):
            node = ir.rebuild_container(node, _scrub_citations_in_inlines(node.children))
        out.append(node)
        i += 1
    return out


def scrub_chatgpt_citations(blocks: list[ir.Block]) -> list[ir.Block]:
    """Remove ChatGPT's auto-injected web-search citation links (`[pill](url?utm_source=
    chatgpt.com)`, e.g. a `Википедия+2` / `Encyclopedia Britannica` pill). Pandoc used to drop
    these; the namespace-canonicalization image recovery now carries them forward, so they are
    scrubbed here at the IR boundary — the author's own conversation links carry no tracking tag
    and are kept. The pass also removes a `(...)` wrapper and the lead-in space around a removed
    citation so the surrounding prose closes cleanly."""
    return [ir.map_block_inlines(b, _scrub_citations_in_inlines) for b in blocks]


# ---------------------------------------------------------------------------
# thematic breaks: a Paragraph whose only text is a divider -> ThematicBreak
# ---------------------------------------------------------------------------


def thematic_breaks(blocks: list[ir.Block]) -> list[ir.Block]:
    out: list[ir.Block] = []
    for b in blocks:
        if isinstance(b, ir.Paragraph) and not b.empty and is_thematic_marker(inline_plain(b.inlines)):
            out.append(ir.ThematicBreak(source_span=b.source_span))
            continue
        out.append(b)
    return out


# ---------------------------------------------------------------------------
# heading demotion: source H1 -> H2 (page title is the only H1)
# ---------------------------------------------------------------------------


def demote_headings(blocks: list[ir.Block], levels: int = 1) -> list[ir.Block]:
    if levels <= 0:
        return blocks
    return [
        replace(b, level=min(6, b.level + levels)) if isinstance(b, ir.Heading) else b
        for b in blocks
    ]


# ---------------------------------------------------------------------------
# formatting-artifact strip — empty-emphasis husks
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


def strip_artifacts(blocks: list[ir.Block]) -> list[ir.Block]:
    """Drop import-only formatting artifacts.

    Whole empty-emphasis husks vanish; trailing or embedded `** **` inside content
    is removed in place. Word/HTML form sentinels are also dropped when they are
    the whole paragraph: in DOCX they are hidden control text, but Pandoc exposes
    them as reading text.
    """
    out: list[ir.Block] = []
    for b in blocks:
        if isinstance(b, ir.Paragraph) and not b.empty:
            b = replace(b, inlines=_drop_empty_emphasis(_hoist_boundary_breaks(b.inlines)))
            if _is_form_marker_text(inline_plain(b.inlines)):
                continue
            if not inline_plain(b.inlines) and all(
                isinstance(n, (ir.SoftBreak, ir.LineBreak)) for n in b.inlines
            ):
                continue  # nothing left but breaks/whitespace → drop the husk
        out.append(b)
    return out
