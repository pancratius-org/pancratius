# import-pure: no filesystem mutation
"""Lower the normalized block IR to canonical Markdown (the one lowering pass).

This is the only stage that produces a Markdown string, and it does so exactly
once — there is no string round-trip and no regex tail-stripping. It emits the
canonical author-facing shape:

  * lineated prose as `<div class="lineated">` with two-space hard breaks
  * verse as `<div class="lineated verse">` with the same lineation plus register
  * `<p class="signature">` and `<blockquote class="epigraph">`
  * footnote refs `[^N]` inline plus a generated `[^N]:` appendix AT THE TAIL —
    generated last, from typed `FootnoteDef`s, so a definition can never be lost
    to tail-stripping
  * `./images/<hash>.<ext>` body image refs + planned assets (the writer copies)
  * bibliography already lifted to the sidecar; reading-content tables kept as GFM
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import assert_never

from pancratius import ir
from pancratius.ir.inlines import inline_lines, inline_plain
from pancratius.passes.sanitize import sanitize_urls


def _body_image_alt(lang: str) -> str:
    return "Illustration" if lang == "en" else "Иллюстрация"


# Quotation glyphs by language: a `Quoted` inline carries the quote SEMANTICS (single/double);
# the marks are typographic and locale-specific. RU uses guillemets for doubles; EN uses American
# curly quotes. Any other language falls back to RU (the corpus default).
_QUOTE_MARKS: dict[str, dict[str, tuple[str, str]]] = {
    "ru": {"double": ("«", "»"), "single": ("'", "'")},
    "en": {"double": ("“", "”"), "single": ("‘", "’")},
}


def _quote_marks(lang: str, kind: str) -> tuple[str, str]:
    return _QUOTE_MARKS.get(lang, _QUOTE_MARKS["ru"])[kind]


# A literal guillemet in EN text is a mistyped quote (English has no guillemets), so it normalizes
# to the same American curly double a `Quoted` inline lowers to. RU text keeps its guillemets.
_EN_LITERAL_QUOTES = str.maketrans({"«": "“", "»": "”"})


def _typographic_text(value: str, lang: str) -> str:
    return value.translate(_EN_LITERAL_QUOTES) if lang == "en" else value


def _escape_markdown_alt(alt: str) -> str:
    # Escape `[`/`]` in image alt text. `re.sub` (not str.replace) so the PAN018
    # purity scan — which flags the bare `.replace` attribute name, unable to tell
    # `str.replace` from `os.replace`/`Path.replace` — stays green in this
    # import-pure module.
    return re.sub(r"[\[\]]", r"\\\g<0>", alt)


# ---------------------------------------------------------------------------
# inline -> markdown (prose)
# ---------------------------------------------------------------------------

# Emphasis-kind lowerings: the Markdown delimiter pair per `EmphKind`.
# `test_emph_tables_total` pins it to the full `EmphKind` set. Lineated-wrapper
# emphasis lowers through this same Markdown path (the blank line after `<div>`
# lets CommonMark parse the inside), so this is the one emphasis table.
_EMPH_MD: dict[ir.EmphKind, tuple[str, str]] = {
    "strong": ("**", "**"), "emph": ("*", "*"), "strike": ("~~", "~~"),
    "sup": ("^", "^"), "sub": ("~", "~"),
}

# The mid-line Markdown/HTML markup characters a LITERAL `ir.Text` value must be
# escaped against, so a DOCX literal (e.g. `[x](y)`, `*not emphasis*`, `<script>`)
# lowers as inert text rather than being re-interpreted as real markup — exactly
# what Pandoc's GFM writer did for `Str` runs. The backslash MUST be first in the
# class so an inserted `\` is never itself re-escaped. NOT included here:
#   * `#` — a LINE-LEADING concern (mid-word `#` like "C#" is literal); handled in
#     `_escape_leading_list_marker`.
#   * `>` — also handled below (per-char escape would mangle nothing, but a leading
#     `>` is the only structural case); kept in the per-char class because a literal
#     `>` mid-line is harmless to escape and a leading one must be.
#   * `|` — only structural INSIDE a GFM table; escaped once in the table-cell path
#     (`_table_md`), so a prose `|` stays literal and a cell `|` is not double-escaped.
# Applied ONLY to `Text` node values — never to the markup the IR nodes themselves
# emit (Emphasis `*…*`, Link `[…](…)`, Code backticks, DirectionalSpan `<span…>`),
# so intentional markup is never over-escaped.
_LITERAL_MD_ESCAPE_RE = re.compile(r"[\\`*_\[\]<>~]")


def _escape_literal_text(value: str) -> str:
    """Escape the mid-line Markdown/HTML markup chars in a LITERAL text run.

    A Pandoc `Str`/IR `Text` value is literal source text, not markup; emitting it
    raw lets `[x](y)` become a real link, `*x*` emphasis, `<b>` raw HTML, `a|b` a
    table-cell split. Each markup char gets a leading backslash (`*` → `\\*`). The
    backslash-first regex class keeps the inserted `\\` from being doubled. Code
    content and the IR's own emitted markup are NOT routed through here.
    """
    return _LITERAL_MD_ESCAPE_RE.sub(lambda m: "\\" + m.group(0), value)


def _longest_backtick_run(value: str) -> int:
    """The length of the longest consecutive run of backticks in `value` (0 if
    none). Drives the variable-length fence/delimiter sizing below."""
    return max((len(m.group(0)) for m in re.finditer(r"`+", value)), default=0)


def _inline_code_md(value: str) -> str:
    """Lower inline code with a CommonMark-safe variable-length backtick delimiter.

    A FIXED single-backtick delimiter lets a literal backtick in the content close
    the span early, leaking the rest of the run back into prose markup (a Markdown
    breakout). The delimiter is therefore a run of N+1 backticks where N is the
    longest internal backtick run — strictly longer than anything inside, so the
    span cannot be terminated early. Per CommonMark, when the content begins or ends
    with a backtick (or is all whitespace) a single space pad inside the fence keeps
    the leading/trailing backtick from being stripped; the renderer drops exactly
    one such pad, so the literal content round-trips inert.
    """
    fence = "`" * (_longest_backtick_run(value) + 1)
    pad = " " if value and (value[0] == "`" or value[-1] == "`" or value.strip() == "") else ""
    return f"{fence}{pad}{value}{pad}{fence}"


def _inline_md(n: ir.Inline, lang: str) -> str:
    match n:
        case ir.Text():
            return _escape_literal_text(_typographic_text(n.value, lang))
        case ir.SoftBreak() | ir.LineBreak():
            return "\n"
        case ir.Emphasis():
            o, c = _EMPH_MD[n.kind]
            return f"{o}{_inlines_md(n.children, lang)}{c}"
        case ir.Code():
            return _inline_code_md(n.value)
        case ir.Quoted():
            open_q, close_q = _quote_marks(lang, n.kind)
            return f"{open_q}{_inlines_md(n.children, lang)}{close_q}"
        case ir.Link():
            label = _inlines_md(n.children, lang).strip()
            return f"[{label}]({n.target})" if label else ""
        case ir.DirectionalSpan():
            inner = _inlines_md(n.children, lang)
            return f'<span dir="{html.escape(n.direction, quote=True)}">{inner}</span>'
        case ir.ImageInline():
            target = f"./images/{n.asset_id}" if n.asset_id else n.src
            if not target:
                return ""  # a dropped (unresolvable-local) image emits nothing
            alt = n.alt or _body_image_alt(lang)
            return f"![{_escape_markdown_alt(alt)}]({target})"
        case ir.FootnoteRef():
            return f"[^{n.id}]"
        case ir.UnknownInline():
            return _inlines_md(n.children, lang)
    assert_never(n)


def _inlines_md(nodes: list[ir.Inline], lang: str) -> str:
    return "".join(_inline_md(n, lang) for n in nodes)


def _paragraph_image_blocks_md(inlines: list[ir.Inline], lang: str, *, poem: bool) -> str | None:
    """Lower direct paragraph images as standalone Markdown blocks.

    Pandoc can place an image inline beside prose when a DOCX picture is anchored
    in a text paragraph. Canonical source Markdown treats body illustrations as
    block images, so split the paragraph at each direct ``ImageInline`` while
    preserving the surrounding text order.
    """
    parts: list[str] = []
    current: list[ir.Inline] = []

    def flush_text() -> None:
        if not current:
            return
        text = _inlines_md(current, lang)
        current.clear()
        if poem:
            lines = [ln.rstrip() for ln in text.split("\n")]
            text = "\n".join(ln for ln in lines if ln.strip())
        else:
            text = _escape_leading_list_marker(re.sub(r"\s*\n\s*", " ", text).strip())
        if text:
            parts.append(text)

    for node in inlines:
        if isinstance(node, ir.ImageInline):
            flush_text()
            image = _inline_md(node, lang).strip()
            if image:
                parts.append(image)
        else:
            current.append(node)
    flush_text()
    return "\n\n".join(parts) or None


# ---------------------------------------------------------------------------
# block -> markdown
# ---------------------------------------------------------------------------


def _lineated_lines(line_inlines: list[ir.Inline], lang: str) -> list[str]:
    """The display lines a single lineated line's inlines lower to.

    Inlines render to Markdown (emphasis as `*`/`**`, footnote refs as `[^N]`),
    with any internal soft/hard break (a `\\n` from `_inline_md`) splitting into a
    further display line. Blank results are dropped so a stray break never emits an
    empty line."""
    md = _inlines_md(line_inlines, lang)
    return [_escape_leading_list_marker(ln.strip()) for ln in md.split("\n") if ln.strip()]


@dataclass(frozen=True)
class _LineatedImage:
    md: str


type _LineatedPart = str | _LineatedImage


def _lineated_parts(line_inlines: list[ir.Inline], lang: str) -> list[_LineatedPart]:
    """Lower one source display line, splitting direct body images into block parts.

    A DOCX drawing can be anchored in the same paragraph as text. The content
    contract still treats body illustrations as standalone blocks, so a direct
    ``ImageInline`` interrupts the current lineated wrapper instead of becoming
    ``text ![](…)`` inside it.
    """
    parts: list[_LineatedPart] = []
    current: list[ir.Inline] = []

    def flush_text() -> None:
        if not current:
            return
        parts.extend(_lineated_lines(current, lang))
        current.clear()

    for node in line_inlines:
        if isinstance(node, ir.ImageInline):
            flush_text()
            image = _inline_md(node, lang).strip()
            if image:
                parts.append(_LineatedImage(image))
        else:
            current.append(node)
    flush_text()
    return parts


def _lineated_wrapper_from_lines(classes: str, stanzas: list[list[str]]) -> str | None:
    out: list[str] = [f'<div class="{classes}">', ""]
    emitted = False
    for stanza in stanzas:
        if stanza:
            emitted = True
            out.extend(line if idx == len(stanza) - 1 else line + "  " for idx, line in enumerate(stanza))
            out.append("")
    if not emitted:
        return None
    out.append("</div>")
    return "\n".join(out)


def _lineated_wrapper_md(classes: str, stanzas: ir.LineatedStanzas, lang: str) -> str | None:
    """Lower lineated stanzas into a raw wrapper whose inside is parsed Markdown.

    The blank line after the opening `<div>` is load-bearing: it makes CommonMark
    parse the wrapper body as Markdown, so emphasis remains `*`/`**` and
    LINEATION is encoded as TWO TRAILING SPACES. Stanzas are separated by blank
    lines; a trailing blank before `</div>` mirrors the leading one.
    """
    chunks: list[str] = []
    wrapper_stanzas: list[list[str]] = []
    stanza_lines: list[str] = []

    def finish_stanza() -> None:
        nonlocal stanza_lines
        if stanza_lines:
            wrapper_stanzas.append(stanza_lines)
            stanza_lines = []

    def flush_wrapper() -> None:
        finish_stanza()
        wrapper = _lineated_wrapper_from_lines(classes, wrapper_stanzas)
        if wrapper:
            chunks.append(wrapper)
        wrapper_stanzas.clear()

    for stanza in stanzas:
        for line in stanza:
            for part in _lineated_parts(line.inlines, lang):
                if isinstance(part, _LineatedImage):
                    flush_wrapper()
                    chunks.append(part.md)
                else:
                    stanza_lines.append(part)
        finish_stanza()
    flush_wrapper()
    return "\n\n".join(chunks) or None

# The register→wrapper-class registry for lineated runs. Total over `Register`
# (test-pinned, like the emphasis tables); registers without a product CSS
# contract take the conservative base class — under-styling is the cheap failure.
LINEATED_CLASS: Mapping[ir.Register, str] = {
    ir.Register.ORDINARY: "lineated",
    ir.Register.VERSE: "lineated verse",
    ir.Register.SCRIPTURE: "lineated",
    ir.Register.INSET: "lineated",
    ir.Register.VOICE: "lineated",
}


def _lineated_md(lb: ir.LineatedBlock, lang: str) -> str | None:
    """Lower a lineated run to the cross-consumer canonical encoding: the
    `<div class="lineated …">` wrapper, classed by register."""
    return _lineated_wrapper_md(LINEATED_CLASS[lb.register], lb.stanzas, lang)


def _signature_md(s: ir.Signature) -> str:
    body = "\n".join(html.escape(line, quote=False) for line in s.lines)
    return f'<p class="signature">\n{body}\n</p>'


def _epigraph_md(e: ir.Epigraph) -> str:
    q = "\n".join(html.escape(line, quote=False) for line in e.quote)
    f = "\n".join(html.escape(line, quote=False) for line in e.footer)
    return "\n".join(['<blockquote class="epigraph">', "<p>", q, "</p>", "<footer>", f, "</footer>", "</blockquote>"])


def _quote_member_md(blk: ir.Block, lang: str) -> str | None:
    """Lower one quote-member block, preserving authored hard line breaks.

    The generic prose paragraph path collapses in-paragraph breaks to spaces;
    inside a set-apart quote a hard `w:br` is an authored display line, encoded
    as the corpus two-trailing-space hard break. Soft breaks remain prose
    wrapping (joined as a space by `inline_lines`)."""
    if (
        isinstance(blk, ir.Paragraph)
        and not blk.empty
        and not any(isinstance(n, ir.ImageInline) for n in blk.inlines)
    ):
        lines = [
            _escape_leading_list_marker(
                re.sub(r"\s*\n\s*", " ", _inlines_md(line, lang)).strip()
            )
            for line in inline_lines(blk.inlines, soft_break=False)
        ]
        lines = [ln for ln in lines if ln]
        if len(lines) > 1:
            return "  \n".join(lines)
    return _block_md(blk, lang)


def _quote_member_mds(b: ir.QuoteBlock, lang: str) -> list[str]:
    """The non-empty lowered member blocks of a quote."""
    return [md for blk in b.blocks if (md := _quote_member_md(blk, lang))]


def _gt_prefixed(md: str) -> str:
    """Every line of `md` behind the `>` quote prefix."""
    return "\n".join("> " + line for line in md.splitlines())


def _plain_quote_md(b: ir.QuoteBlock, lang: str) -> str | None:
    """An ordinary quote (Pandoc-born, from a Word Quote style/indent): the
    line-prefix join, whose members fuse by lazy continuation."""
    inner = "\n".join(_gt_prefixed(md) for blk in b.blocks if (md := _block_md(blk, lang)))
    return inner or None


def _scripture_quote_md(b: ir.QuoteBlock, lang: str) -> str | None:
    """Quoted canonical text (the boxed `w:pBdr` register): a classed HTML
    blockquote whose inside is parsed Markdown (the blank line after the
    opening tag is load-bearing, as in the lineated wrapper)."""
    members = _quote_member_mds(b, lang)
    if not members:
        return None
    return "\n".join(
        ['<blockquote class="scripture">', "", "\n\n".join(members), "", "</blockquote>"]
    )


def _inset_quote_md(b: ir.QuoteBlock, lang: str) -> str | None:
    """A set-apart passage in another voice (the left-rule register): a plain
    portable Markdown quote; member blocks are separated by a bare `>` line so
    they stay distinct paragraphs instead of fusing by lazy continuation, and
    authored hard breaks inside members stay display lines."""
    members = _quote_member_mds(b, lang)
    if not members:
        return None
    return "\n>\n".join(_gt_prefixed(md) for md in members)


# The register→emission registry for quote blocks. Total over `Register`
# (test-pinned); registers without a quote treatment take the plain emission.
QUOTE_LOWERING: Mapping[ir.Register, Callable[[ir.QuoteBlock, str], str | None]] = {
    ir.Register.ORDINARY: _plain_quote_md,
    ir.Register.VERSE: _plain_quote_md,
    ir.Register.SCRIPTURE: _scripture_quote_md,
    ir.Register.INSET: _inset_quote_md,
    ir.Register.VOICE: _plain_quote_md,
}


def _table_md(t: ir.Table, lang: str) -> str | None:
    """Render a non-bibliography (reading-content) table as a GFM pipe table —
    reading-content tables are kept in the body. Cells are rendered from their inlines
    (so images get the default body alt + `./images/<hash>` ref like prose), with
    internal breaks collapsed to spaces and pipes escaped for the cell grid."""
    if not t.rows:
        return None
    ncol = max(len(r) for r in t.rows)

    def cell_md(cell: list[ir.Inline]) -> str:
        text = _inlines_md(cell, lang)
        text = re.sub(r"\s*\n\s*", " ", text).strip()
        # Escape pipes for the GFM cell grid. `re.sub` (not str.replace) so the
        # PAN018 purity scan — which flags the bare `.replace` attribute name,
        # unable to tell `str.replace` from `os.replace`/`Path.replace` — stays
        # clean on this import-pure module.
        return re.sub(r"\|", r"\\|", text)

    def row(cells: list[list[ir.Inline]]) -> str:
        rendered = [cell_md(c) for c in cells] + [""] * (ncol - len(cells))
        return "| " + " | ".join(rendered) + " |"

    out = [row(t.rows[0]), "|" + "|".join(["----"] * ncol) + "|"]
    for r in t.rows[1:]:
        out.append(row(r))
    return "\n".join(out)


# A leading list marker in PROSE: an ordinal `N.`/`N)` or a bullet `-`/`*`/`+`,
# in EACH case followed by whitespace (or end of line) — the actual CommonMark
# list-item syntax. When the author typed a literal "1. " in a normal paragraph
# (the source has NO `OrderedList` — e.g. `книга-огня`'s numbered prose), an
# UNESCAPED marker makes the downstream Markdown parser emit an `<ol>`/`<ul>`.
# Escaping the delimiter with a backslash (mirroring Pandoc's GFM writer: `1. ` →
# `1\. `) keeps the paragraph a `<p>`. REAL source `OrderedList`/`BulletList`s are
# lowered by `ListBlock` (not this prose path), so they still render as lists.
#
# The trailing-whitespace requirement is what keeps a DATE safe: `25.06.2025` is
# `25.` followed by a DIGIT (no space), so it is never a list start and stays
# untouched — only `1. ` / `1) ` / `- ` at a real marker boundary is escaped.
_LEADING_LIST_MARKER_RE = re.compile(r"^(\s*)(\d{1,9}|[-*+])([.)]?)(?=\s|$)")


# A leading ATX-heading run `#`..`######` followed by whitespace/end: a literal
# leading `#` in a normal paragraph (the author typed it; the source has no
# `Header`) would otherwise be parsed as a heading. Escaping the first `#` (`# x`
# → `\# x`) keeps the paragraph a `<p>`, mirroring Pandoc's GFM writer. `#` is NOT
# in the per-char literal set because mid-word `#` ("C#", "F#") is not markup and
# must stay literal — only a line-LEADING `#` is structural.
_LEADING_HEADING_RE = re.compile(r"^(\s*)(#{1,6})(?=\s|$)")

# A full line of hyphens or equals is inert only if escaped. Otherwise CommonMark
# may reinterpret it as a setext-heading underline for the preceding paragraph,
# especially inside parsed lineated wrappers.
_SETEXT_UNDERLINE_RE = re.compile(r"^(\s*)((?P<c>[-=])(?P=c)*)(\s*)$")


def _escape_leading_list_marker(text: str) -> str:
    # A leading literal `#…` ATX run is escaped first (it cannot coexist with a
    # list marker on the same line). `>` is handled by the per-char literal escape
    # (a leading literal `>` is already `\>` by the time this runs).
    hm = _LEADING_HEADING_RE.match(text)
    if hm:
        lead, hashes = hm.group(1), hm.group(2)
        return f"{lead}\\{hashes}{text[hm.end():]}"
    sm = _SETEXT_UNDERLINE_RE.match(text)
    if sm:
        lead, marker, tail = sm.group(1), sm.group(2), sm.group(4)
        return f"{lead}\\{marker}{tail}"
    m = _LEADING_LIST_MARKER_RE.match(text)
    if not m:
        return text
    lead, token, delim = m.group(1), m.group(2), m.group(3)
    if token in {"-", "*", "+"}:
        # A bullet marker (`- ` → `\- `): there is no ordinal delimiter to escape;
        # escape the bullet glyph itself. (A leading `*` bullet is already escaped
        # by the per-char literal pass, so only `-`/`+` reach this branch.)
        return f"{lead}\\{token}{text[m.end():]}"
    if not delim:
        # A bare number with no `.`/`)` delimiter is not a list marker — leave it.
        return text
    # An ordinal `N.`/`N)` → escape the trailing delimiter (`1. ` → `1\. `).
    return f"{lead}{token}\\{delim}{text[m.end():]}"


def _heading_md(b: ir.Heading, lang: str) -> str:
    """Lower a heading to an ATX line PRESERVING inline footnote refs + emphasis.

    Headings cannot use ``inline_plain`` (which drops ``FootnoteRef`` and flattens
    emphasis): a footnote anchored to a heading (`### Глава 25[^3]. …`) would lose
    its `[^3]` ref, ORPHANING the `[^3]:` definition — a real footnote-integrity
    regression. The marker must stay on the heading line, so we render through the
    inline-markdown path (emits `[^N]`, `*…*`, `**…**`, links), collapse internal
    soft/hard breaks to spaces (a heading is one line), and then strip a FULLY-bold
    wrapper (`# **TEXT**` → `# TEXT`) — partial emphasis is kept."""
    text = _inlines_md(b.inlines, lang)
    text = re.sub(r"\s*\n\s*", " ", text).strip()
    # `# **TEXT**` → `# TEXT`: a heading wrapped entirely in bold loses the wrapper;
    # partial bold survives.
    m = re.fullmatch(r"\*\*(.+?)\*\*", text)
    if m:
        text = m.group(1).strip()
    return f"{'#' * b.level} {text}"


def _block_md(b: ir.Block, lang: str, *, poem: bool = False) -> str | None:
    match b:
        case ir.Heading():
            return _heading_md(b, lang)
        case ir.Paragraph():
            if b.empty:
                return None
            if any(isinstance(node, ir.ImageInline) for node in b.inlines):
                return _paragraph_image_blocks_md(b.inlines, lang, poem=poem)
            text = _inlines_md(b.inlines, lang)
            if poem:
                # Verse: keep hard/soft breaks as lines (one verse line each), trimming
                # only trailing spaces — the poem path's line-per-line shape.
                lines = [ln.rstrip() for ln in text.split("\n")]
                return "\n".join(ln for ln in lines if ln.strip()) or None
            # Prose: collapse internal soft/hard breaks to spaces (Pandoc --wrap=none).
            text = re.sub(r"\s*\n\s*", " ", text).strip()
            return _escape_leading_list_marker(text) or None
        case ir.LineatedBlock():
            return _lineated_md(b, lang)
        case ir.Signature():
            return _signature_md(b)
        case ir.Epigraph():
            return _epigraph_md(b)
        case ir.DialogueLabel():
            return f"**{b.speaker}:**"
        case ir.ThematicBreak():
            return "***"
        case ir.ImageBlock():
            target = f"./images/{b.asset_id}" if b.asset_id else b.src
            if not target:
                return None  # a dropped (unresolvable-local) block image emits nothing
            alt = b.alt or _body_image_alt(lang)
            return f"![{_escape_markdown_alt(alt)}]({target})"
        case ir.QuoteBlock():
            return QUOTE_LOWERING[b.register](b, lang)
        case ir.ListBlock():
            parts: list[str] = []
            for idx, item in enumerate(b.items):
                marker = f"{b.start + idx}." if b.ordered else "-"
                item_md = "\n\n".join(filter(None, (_block_md(x, lang) for x in item)))
                parts.append(f"{marker} {item_md}")
            return "\n".join(parts) or None
        case ir.CodeBlock():
            # A FIXED triple-fence lets a ``` line inside the content close the block
            # early, leaking the remainder as raw Markdown. Size the fence to be strictly
            # longer than the longest internal backtick run (min 3), so the block cannot
            # be terminated early — CommonMark info-string-less variable-length fence.
            fence = "`" * max(3, _longest_backtick_run(b.text) + 1)
            return f"{fence}\n{b.text}\n{fence}"
        case ir.Table():
            return _table_md(b, lang)
        case ir.UnknownBlock():
            # PRESERVE the unknown block's readable text (escaped — it is literal source
            # text) rather than dropping it. A diagnostic is surfaced separately in
            # `lower` (it owns the document); a kind with no recoverable text emits
            # nothing here but is still surfaced.
            text = re.sub(r"\s*\n\s*", " ", b.text).strip()
            if not text:
                return None
            return _escape_literal_text(text) or None
    assert_never(b)


# ---------------------------------------------------------------------------
# footnote appendix (generated last; cannot be tail-stripped)
# ---------------------------------------------------------------------------


def _footnote_appendix(doc: ir.Document, lang: str) -> str:
    if not doc.footnotes:
        return ""
    parts: list[str] = []
    for fn in doc.footnotes:
        body = "\n\n".join(filter(None, (_block_md(b, lang) for b in fn.blocks)))
        body = re.sub(r"\n{2,}", " ", body).strip()  # single-line def like Pandoc GFM
        parts.append(f"[^{fn.id}]: {body}")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# top-level lower
# ---------------------------------------------------------------------------


def _is_strong_only_para(b: ir.Block) -> bool:
    """True when a paragraph's single inline is one `Strong` span — a bold title
    paragraph."""
    return (
        isinstance(b, ir.Paragraph)
        and not b.empty
        and len(b.inlines) == 1
        and isinstance(b.inlines[0], ir.Emphasis)
        and b.inlines[0].kind == "strong"
    )


def _lower_poem_body(doc: ir.Document, lang: str, diagnostics: ir.DiagnosticSink) -> str:
    """Lower a poem as stanza-grouped verse lines:

      * an empty paragraph is a stanza break (flush the accumulator);
      * a `***` paragraph / thematic break is its own one-line stanza;
      * a NON-EMPTY paragraph that yields MORE THAN ONE display line (it carries
        internal hard/soft breaks) is its OWN stanza — flushed before and after;
      * a non-empty paragraph that yields a SINGLE line ACCUMULATES into the
        current stanza, which is flushed only at the next empty paragraph.

    The multi-line-paragraph-is-its-own-stanza rule matters because many poems
    store ONE STANZA PER non-empty Word paragraph (the stanza's lines live as
    internal hard breaks, with NO empty paragraph between stanzas). A paragraph
    boundary between two multi-line verse paragraphs IS a stanza break (without
    this, every such stanza merges into one giant stanza, e.g. "Весна" 3→1,
    "Бог видит сон" 5→1)."""
    stanzas: list[list[str]] = []
    current: list[str] = []
    seen_content = False

    def flush() -> None:
        nonlocal current
        if current:
            stanzas.append(current)
            current = []

    for b in doc.blocks:
        if isinstance(b, ir.Paragraph) and b.empty:
            flush()
            continue
        if isinstance(b, ir.ThematicBreak):
            flush()
            stanzas.append(["***"])
            continue
        if isinstance(b, ir.QuoteBlock):
            # The poem lowering renders ONLY top-level `Para`/`Plain`; a
            # `QuoteBlock` flushes and is not emitted. In the
            # corpus a poem `QuoteBlock` only ever wraps the poem TITLE (the page
            # masthead already renders that title), so this drop is a title-duplicate
            # drop, not reading-content loss — and it keeps the head stanza count
            # equal to the DOCX stanza oracle (#08 head 2→1). The diagnostic
            # records what was dropped so a real quoted passage cannot vanish
            # silently.
            dropped = " ".join(
                inline_plain(p.inlines)
                for p in b.blocks
                if isinstance(p, ir.Paragraph) and not p.empty
            ).strip()
            if dropped:
                diagnostics.append(ir.Diagnostic(
                    "info", "lower.poem-quote-drop",
                    f"poem quote block dropped (title-duplicate rule): {dropped[:80]!r}",
                ))
            flush()
            continue
        if isinstance(b, ir.Paragraph) and any(isinstance(node, ir.ImageInline) for node in b.inlines):
            flush()
            md = _paragraph_image_blocks_md(b.inlines, lang, poem=True)
            if md:
                for part in md.split("\n\n"):
                    lines = [line for line in part.splitlines() if line.strip()]
                    if lines:
                        stanzas.append(lines)
            seen_content = True
            continue
        md = _block_md(b, lang, poem=True)
        if md is None or md == "":
            continue
        lines = [line for line in md.split("\n") if line.strip()]
        if not lines:
            continue
        # A poem illustration (a block image, or a paragraph that is ONLY an image)
        # is never a verse line: flush the current stanza and emit the image as its
        # own block, so it is separated by blank lines (otherwise it fuses into the
        # last stanza and inflates that stanza's line count, e.g. #36/#38 last
        # stanza 4→5): a non-text block flushes.
        if isinstance(b, ir.ImageBlock) or (
            isinstance(b, ir.Paragraph)
            and len(b.inlines) == 1
            and isinstance(b.inlines[0], ir.ImageInline)
        ):
            flush()
            stanzas.append(lines)
            continue
        # The FIRST strong-only paragraph (before any verse content) is the poem's
        # title paragraph — its own group. Kept separate so the source-duplicate
        # -title strip can drop it cleanly (otherwise a bold title line would fuse
        # into the first stanza, e.g. #36 first stanza 4→3).
        if not seen_content and _is_strong_only_para(b):
            flush()
            stanzas.append(lines)
            seen_content = True
            continue
        seen_content = True
        if len(lines) > 1:
            # A multi-line paragraph is a self-contained stanza (its lines are the
            # authored line breaks of one stanza): flush the accumulator, emit it
            # as its own group, and keep the next paragraph a fresh stanza.
            flush()
            stanzas.append(lines)
        else:
            # A single-line paragraph accumulates; the stanza is closed by the next
            # empty paragraph / thematic break / multi-line paragraph.
            current.append(lines[0])
    flush()
    return "\n\n".join(_stanza_md(stanza) for stanza in stanzas).strip() + "\n"


def _stanza_md(lines: list[str]) -> str:
    """Join a poem stanza's display lines with the cross-consumer hard break.

    LINEATION inside a stanza is encoded the same way as in a `.lineated` block: TWO
    TRAILING SPACES on every non-final line (the CommonMark hard break that
    survives Astro, pandoc PDF/EPUB, AND the public-Markdown export), the final
    line closed by the blank-line stanza separator instead. A `***` verse-break
    line is its own one-line stanza and never carries a break. Poems are whole-body
    verse (no wrapper); their REGISTER comes from `kind: poem` / the poem
    component, not a `.lineated.verse` class."""
    return "\n".join(
        line if (idx == len(lines) - 1 or line == "***") else line + "  "
        for idx, line in enumerate(lines)
    )


def _surface_unknown_block_diagnostics(
    doc: ir.Document, diagnostics: ir.DiagnosticSink
) -> None:
    """Append one `warning` diagnostic per `UnknownBlock` reachable in `doc.blocks`
    (descending into the container blocks that nest others), so an unmodeled block is
    SURFACED, never silently dropped — the design's "unknown → preserve content /
    emit a diagnostic". The block's text is still preserved at lowering; this only
    makes its presence visible to the caller (which forwards `warning`/`fatal`)."""

    def visit(b: ir.Block) -> None:
        # PARTIAL (a `case _` no-op, NOT `assert_never`): only an
        # `UnknownBlock` surfaces a diagnostic, and only the container blocks
        # (`QuoteBlock`/`ListBlock`) nest others to descend into; every other block
        # kind is a non-nesting leaf with nothing to surface, so it is skipped.
        match b:
            case ir.UnknownBlock():
                preserved = "preserved its text" if b.text.strip() else "no recoverable text"
                diagnostics.append(ir.Diagnostic(
                    "warning", "import.unknown-block",
                    f"unmodeled block kind {b.note!r} ({preserved}); surfaced rather than "
                    "silently dropped.",
                ))
            case ir.QuoteBlock():
                for inner in b.blocks:
                    visit(inner)
            case ir.ListBlock():
                for item in b.items:
                    for inner in item:
                        visit(inner)
            case _:
                pass  # non-nesting leaf blocks have nothing to surface

    for b in doc.blocks:
        visit(b)


def lower(
    doc: ir.Document,
    lang: str,
    diagnostics: ir.DiagnosticSink,
    *,
    poem: bool = False,
) -> str:
    """Lower the document to the canonical Markdown body string.

    `diagnostics` is the composition point's sink (unsafe-url and unknown-block
    findings land there). `poem` selects verse lowering for the work-as-a-whole:
    paragraphs become stanza-grouped verse lines (one line each, stanzas split on
    empty paragraphs) rather than prose, in a line-per-line shape."""
    # Neutralize unsafe link/image schemes BEFORE any Markdown/HTML is emitted (the
    # import is the only sanitizer the unsanitized renderer has). Idempotent — on
    # the production path the pipeline's sanitize pass has already run; this gate
    # also covers direct `lower` callers.
    doc = sanitize_urls(doc, diagnostics)
    _surface_unknown_block_diagnostics(doc, diagnostics)
    if poem:
        body = _lower_poem_body(doc, lang, diagnostics)
    else:
        body = "\n\n".join(md for b in doc.blocks if (md := _block_md(b, lang)))
    appendix = _footnote_appendix(doc, lang)
    if appendix:
        body = body.rstrip("\n") + "\n\n" + appendix
    return re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"
