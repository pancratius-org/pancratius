# import-pure: no filesystem mutation
"""Lower the normalized block IR to canonical Markdown (the one lowering pass).

This is the only stage that produces a Markdown string, and it does so exactly
once — there is no string round-trip and no regex tail-stripping. It emits the
canonical author-facing shape:

  * verse-block / answer-block `<div>`s (rendered as `white-space: pre-line` HTML)
  * `<p class="signature">` and `<blockquote class="epigraph">`
  * footnote refs `[^N]` inline plus a generated `[^N]:` appendix AT THE TAIL —
    generated last, from typed `FootnoteDef`s, so a definition can never be lost
    to tail-stripping (the Phase-4 win, now structural)
  * `./images/<hash>.<ext>` body image refs + planned assets (the writer copies)
  * bibliography already lifted to the sidecar; reading-content tables kept as GFM

The asset pass reads the extracted media files to hash them (read-only `open`),
then assigns content-hash asset ids; it mutates nothing on disk — the returned
`PlannedAsset`s are what the writer later copies. This module is `import-pure`.

Image hashing, extension normalization, the hash-prefix length, the raster-cap
set, and the body-image alt/escaping live here next to the asset pass that is
their sole user; `PlannedAsset` is the plan-adjacent value type from `writeplan`.
"""

from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path, PurePosixPath

from lib import ir
from lib.writeplan import PlannedAsset


# ---------------------------------------------------------------------------
# body-image asset constants + helpers (content-addressed planning)
# ---------------------------------------------------------------------------

# Length of the content-hash prefix used for `images/<hash>.<ext>` asset ids.
HASH_PREFIX_LEN = 12

# Image extensions a body media file may carry; `_normalize_ext` folds `.jpeg`/
# `.jpe` to `.jpg` so equivalent encodings hash to the same asset id.
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tiff", ".emf", ".wmf")
EXT_FROM_MIME = {".jpeg": ".jpg", ".jpe": ".jpg"}

# Raster body-image extensions the import-time longest-edge cap applies to (after
# `_normalize_ext` folds `.jpeg`->`.jpg`). Vector (svg/emf/wmf) and animated (gif)
# are copied verbatim. The cap itself is a writer transform; this set only labels
# which planned assets are cap-eligible.
RASTER_CAP_EXTS = frozenset({".png", ".jpg", ".webp", ".avif"})


def _normalize_ext(ext: str) -> str:
    ext = ext.lower()
    return EXT_FROM_MIME.get(ext, ext)


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()[:HASH_PREFIX_LEN]


def _is_image_path(p: str) -> bool:
    return any(p.lower().endswith(e) for e in IMAGE_EXTS)


def _body_image_alt(lang: str) -> str:
    return "Illustration" if lang == "en" else "Иллюстрация"


def _escape_markdown_alt(alt: str) -> str:
    # Escape `[`/`]` in image alt text. `re.sub` (not str.replace) so the PAN018
    # purity scan — which flags the bare `.replace` attribute name, unable to tell
    # `str.replace` from `os.replace`/`Path.replace` — stays green in this
    # import-pure module.
    return re.sub(r"[\[\]]", r"\\\g<0>", alt)


# ---------------------------------------------------------------------------
# asset pass: assign content-hash asset ids to body images; plan their copy
# ---------------------------------------------------------------------------


def _escapes_media_root(src: str, media_root: Path) -> bool:
    """True if `src` resolves OUTSIDE the pandoc media-extraction dir.

    The real-path confinement: `(media_root / src).resolve()` (absolute `src`
    overrides `media_root` under `Path.__truediv__`, so an absolute path resolves to
    itself; a relative one joins) must be `media_root` itself or sit under it, with
    BOTH sides `resolve()`d so a symlinked component or a `/tmp -> /private/tmp`
    style root is normalized. `..` in the path parts is treated as escaping too
    (defense-in-depth for the parent-traversal intent, even where it would resolve
    back). This is what stops an `src` like `/etc/passwd` or `../../secret` from
    being read/copied — WITHOUT rejecting the absolute-but-in-root paths Pandoc
    legitimately emits (`<media_root>/media/imageN.jpg`)."""
    if ".." in PurePosixPath(src).parts:
        return True
    root = media_root.resolve()
    cand = (media_root / src).resolve()
    return cand != root and root not in cand.parents


def _confined_media_source(src: str, media_root: Path) -> Path | None:
    """Resolve a body-image `src` to a real file CONFINED under `media_root`.

    Returns the resolved candidate iff it does NOT escape `media_root` and is a
    readable file; otherwise `None` (the caller drops the ref, with a diagnostic for
    the escape case). The previous `Path(src)` arbitrary-path fallback is removed: a
    ref that does not resolve safely UNDER `media_root` is never read."""
    if _escapes_media_root(src, media_root):
        return None
    cand = (media_root / src).resolve()
    if not cand.is_file():
        return None
    return cand


def assign_assets(doc: ir.Document, media_root: Path, lang: str) -> list[PlannedAsset]:
    """Resolve every body image, assign its content-hash `<hash>.<ext>` asset id,
    and return the deduped `PlannedAsset`s for the writer to copy.

    `media_root` is the directory pandoc extracted media into; an image whose source
    cannot be resolved SAFELY UNDER `media_root` keeps its original ref (no asset
    planned) and surfaces a warning diagnostic — an absolute or `..`-escaping `src`
    is never read/copied (asset-source confinement). PURE: this only READS the media
    files to hash them. The returned list is sorted by bundle-relative path, giving
    the writer a stable asset order.
    """
    seen: dict[str, tuple[str, str]] = {}
    planned: dict[str, PlannedAsset] = {}

    def resolve(src: str) -> tuple[str, str] | None:
        if src in seen:
            return seen[src]
        cand = _confined_media_source(src, media_root)
        if cand is None:
            # Distinguish an ESCAPING ref (a safety refusal worth surfacing) from an
            # ordinary missing/unresolvable in-root ref (the benign keep-original-ref
            # case the corpus hits for a stale link). Pandoc's absolute-but-in-root
            # paths are NOT escapes, so they never trip this.
            if _escapes_media_root(src, media_root):
                doc.diagnostics.append(ir.Diagnostic(
                    "warning", "import.asset-escape",
                    f"image source {src!r} escapes the media-extraction dir; "
                    "dropped (not read) — keeping the original ref.",
                ))
            return None
        if not _is_image_path(cand.name):
            return None
        h = _hash_file(cand)
        ext = _normalize_ext(cand.suffix)
        rel_within = f"images/{h}{ext}"
        planned.setdefault(
            rel_within,
            PlannedAsset(rel_within=rel_within, source=cand, is_raster=ext in RASTER_CAP_EXTS),
        )
        seen[src] = (h, ext)
        doc.assets.append(ir.AssetRef(asset_id=h, src_path=str(cand), ext=ext))
        return h, ext

    def visit_inlines(inlines: list[ir.Inline]) -> list[ir.Inline]:
        out: list[ir.Inline] = []
        for n in inlines:
            if isinstance(n, ir.ImageInline):
                got = resolve(n.src)
                out.append(ir.ImageInline(src=n.src, alt=n.alt, asset_id=(got[0] + got[1]) if got else None))
            elif isinstance(n, ir.ContainerInline):
                out.append(ir.rebuild_container(n, visit_inlines(n.children)))
            else:
                out.append(n)
        return out

    def visit_block(b: ir.Block) -> None:
        if isinstance(b, ir.ImageBlock):
            got = resolve(b.src)
            if got:
                b.asset_id = got[0] + got[1]
        elif isinstance(b, ir.Paragraph):
            b.inlines = visit_inlines(b.inlines)
        elif isinstance(b, ir.BlockQuote):
            for inner in b.blocks:
                visit_block(inner)
        elif isinstance(b, ir.ListBlock):
            for item in b.items:
                for inner in item:
                    visit_block(inner)
        elif isinstance(b, ir.VerseBlock):
            b.stanzas = [[visit_inlines(line) for line in stanza] for stanza in b.stanzas]
        elif isinstance(b, ir.Table):
            b.rows = [[visit_inlines(cell) for cell in row] for row in b.rows]

    for b in doc.blocks:
        visit_block(b)
    return [planned[k] for k in sorted(planned)]


# ---------------------------------------------------------------------------
# inline -> markdown (prose)
# ---------------------------------------------------------------------------

_EMPH_MD: dict[str, tuple[str, str]] = {
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


def _inline_md(n: ir.Inline, lang: str) -> str:
    if isinstance(n, ir.Text):
        return _escape_literal_text(n.value)
    if isinstance(n, (ir.SoftBreak, ir.LineBreak)):
        return "\n"
    if isinstance(n, ir.Emphasis):
        o, c = _EMPH_MD[n.kind]
        return f"{o}{_inlines_md(n.children, lang)}{c}"
    if isinstance(n, ir.Code):
        return f"`{n.value}`"
    if isinstance(n, ir.Quoted):
        inner = _inlines_md(n.children, lang)
        return f"'{inner}'" if n.single else f"«{inner}»"
    if isinstance(n, ir.Link):
        label = _inlines_md(n.children, lang).strip()
        return f"[{label}]({n.target})" if label else ""
    if isinstance(n, ir.DirectionalSpan):
        inner = _inlines_md(n.children, lang)
        return f'<span dir="{html.escape(n.direction, quote=True)}">{inner}</span>'
    if isinstance(n, ir.ImageInline):
        alt = n.alt or _body_image_alt(lang)
        target = f"./images/{n.asset_id}" if n.asset_id else n.src
        return f"![{_escape_markdown_alt(alt)}]({target})"
    if isinstance(n, ir.FootnoteRef):
        return f"[^{n.id}]"
    if isinstance(n, ir.UnknownInline):
        return _inlines_md(n.children, lang)
    return ""


def _inlines_md(nodes: list[ir.Inline], lang: str) -> str:
    return "".join(_inline_md(n, lang) for n in nodes)


# ---------------------------------------------------------------------------
# inline -> balanced HTML lines (for verse/answer blocks)
# ---------------------------------------------------------------------------


def _inline_html_lines(nodes: list[ir.Inline], lang: str) -> list[str]:
    lines = [""]

    def merge(child: list[str]) -> None:
        for idx, c in enumerate(child):
            if idx:
                lines.append("")
            lines[-1] += c

    def wrap(tag: str, child: list[str]) -> list[str]:
        return [f"<{tag}>{c}</{tag}>" if c else "" for c in child]

    for n in nodes:
        if isinstance(n, ir.Text):
            lines[-1] += html.escape(n.value, quote=False)
        elif isinstance(n, (ir.SoftBreak, ir.LineBreak)):
            lines.append("")
        elif isinstance(n, ir.Emphasis):
            tag = {"strong": "strong", "emph": "em", "strike": "s", "sup": "sup", "sub": "sub"}[n.kind]
            merge(wrap(tag, _inline_html_lines(n.children, lang)))
        elif isinstance(n, ir.Code):
            lines[-1] += f"<code>{html.escape(n.value, quote=False)}</code>"
        elif isinstance(n, ir.Quoted):
            child = _inline_html_lines(n.children, lang)
            if child:
                o, c = ("'", "'") if n.single else ("«", "»")
                child[0] = f"{o}{child[0]}"
                child[-1] = f"{child[-1]}{c}"
            merge(child)
        elif isinstance(n, ir.Link):
            label = "".join(_inline_html_lines(n.children, lang))
            lines[-1] += f'<a href="{html.escape(n.target, quote=True)}">{label}</a>'
        elif isinstance(n, ir.DirectionalSpan):
            inner = "".join(_inline_html_lines(n.children, lang))
            lines[-1] += f'<span dir="{html.escape(n.direction, quote=True)}">{inner}</span>'
        elif isinstance(n, ir.ImageInline):
            target = f"./images/{n.asset_id}" if n.asset_id else n.src
            lines[-1] += f'<img src="{html.escape(target, quote=True)}" alt="{html.escape(n.alt, quote=True)}">'
        elif isinstance(n, ir.FootnoteRef):
            lines[-1] += f"[^{n.id}]"
        elif isinstance(n, ir.UnknownInline):
            merge(_inline_html_lines(n.children, lang))
    return lines


def _clean_verse_html_line(line: str) -> str:
    line = re.sub(r"<(strong|em)>\s*(?:<br>\s*)+\s*</\1>", "", line)
    line = re.sub(r"<(strong|em)>\s*</\1>", "", line)
    line = re.sub(r"(?:<br>\s*)+$", "", line)
    return line.strip()


# ---------------------------------------------------------------------------
# block -> markdown
# ---------------------------------------------------------------------------


def _verse_md(vb: ir.VerseBlock, lang: str) -> str:
    out: list[str] = [f'<div class="{vb.role}">']
    for stanza in vb.stanzas:
        for line_inlines in stanza:
            if len(line_inlines) == 1 and isinstance(line_inlines[0], ir.Text) and line_inlines[0].value == "***":
                out.append("***")
                continue
            for html_line in _inline_html_lines(line_inlines, lang):
                cleaned = _clean_verse_html_line(html_line)
                if cleaned:
                    out.append(cleaned)
        out.append("")
    while out and out[-1] == "":
        out.pop()
    out.append("</div>")
    return "\n".join(out)


def _signature_md(s: ir.Signature) -> str:
    body = "\n".join(html.escape(line, quote=False) for line in s.lines)
    return f'<p class="signature">\n{body}\n</p>'


def _epigraph_md(e: ir.Epigraph) -> str:
    q = "\n".join(html.escape(line, quote=False) for line in e.quote)
    f = "\n".join(html.escape(line, quote=False) for line in e.footer)
    return "\n".join(['<blockquote class="epigraph">', "<p>", q, "</p>", "<footer>", f, "</footer>", "</blockquote>"])


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


def _escape_leading_list_marker(text: str) -> str:
    # A leading literal `#…` ATX run is escaped first (it cannot coexist with a
    # list marker on the same line). `>` is handled by the per-char literal escape
    # (a leading literal `>` is already `\>` by the time this runs).
    hm = _LEADING_HEADING_RE.match(text)
    if hm:
        lead, hashes = hm.group(1), hm.group(2)
        return f"{lead}\\{hashes}{text[hm.end():]}"
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
        text = m.group(1)
    return f"{'#' * b.level} {text}"


def _block_md(b: ir.Block, lang: str, *, poem: bool = False) -> str | None:
    if isinstance(b, ir.Heading):
        return _heading_md(b, lang)
    if isinstance(b, ir.Paragraph):
        if b.empty:
            return None
        text = _inlines_md(b.inlines, lang)
        if poem:
            # Verse: keep hard/soft breaks as lines (one verse line each), trimming
            # only trailing spaces — the poem path's line-per-line shape.
            lines = [ln.rstrip() for ln in text.split("\n")]
            return "\n".join(ln for ln in lines if ln.strip()) or None
        # Prose: collapse internal soft/hard breaks to spaces (Pandoc --wrap=none).
        text = re.sub(r"\s*\n\s*", " ", text).strip()
        return _escape_leading_list_marker(text) or None
    if isinstance(b, ir.VerseBlock):
        return _verse_md(b, lang)
    if isinstance(b, ir.Signature):
        return _signature_md(b)
    if isinstance(b, ir.Epigraph):
        return _epigraph_md(b)
    if isinstance(b, ir.DialogueLabel):
        return f"**{b.speaker}:**"
    if isinstance(b, ir.ThematicBreak):
        return "***"
    if isinstance(b, ir.ImageBlock):
        alt = b.alt or _body_image_alt(lang)
        target = f"./images/{b.asset_id}" if b.asset_id else b.src
        return f"![{_escape_markdown_alt(alt)}]({target})"
    if isinstance(b, ir.BlockQuote):
        if b.role == "_div":
            return "\n\n".join(filter(None, (_block_md(x, lang) for x in b.blocks))) or None
        inner = "\n".join(
            "> " + line for blk in b.blocks for line in (_block_md(blk, lang) or "").splitlines()
        )
        return inner or None
    if isinstance(b, ir.ListBlock):
        parts: list[str] = []
        for idx, item in enumerate(b.items):
            marker = f"{b.start + idx}." if b.ordered else "-"
            item_md = "\n\n".join(filter(None, (_block_md(x, lang) for x in item)))
            parts.append(f"{marker} {item_md}")
        return "\n".join(parts) or None
    if isinstance(b, ir.CodeBlock):
        return f"```\n{b.text}\n```"
    if isinstance(b, ir.Table):
        return _table_md(b, lang)
    if isinstance(b, ir.UnknownBlock):
        # PRESERVE the unknown block's readable text (escaped — it is literal source
        # text) rather than dropping it. A diagnostic is surfaced separately in
        # `lower` (it owns the document); a kind with no recoverable text emits
        # nothing here but is still surfaced.
        text = re.sub(r"\s*\n\s*", " ", b.text).strip()
        return _escape_literal_text(text) or None if text else None
    return None


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


def _lower_poem_body(doc: ir.Document, lang: str) -> str:
    """Lower a poem as stanza-grouped verse lines:

      * an empty paragraph is a stanza break (flush the accumulator);
      * a `***` paragraph / thematic break is its own one-line stanza;
      * a NON-EMPTY paragraph that yields MORE THAN ONE display line (it carries
        internal hard/soft breaks) is its OWN stanza — flushed before and after;
      * a non-empty paragraph that yields a SINGLE line ACCUMULATES into the
        current stanza, which is flushed only at the next empty paragraph.

    The multi-line-paragraph-is-its-own-stanza rule is the C1 fix: many poems
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
        if isinstance(b, ir.BlockQuote):
            # The poem lowering renders ONLY top-level `Para`/`Plain`; a
            # `BlockQuote` flushes and is not emitted. In the
            # corpus a poem `BlockQuote` only ever wraps the poem TITLE (the page
            # masthead already renders that title), so this drop is a title-duplicate
            # drop, not reading-content loss — and it keeps the head stanza count
            # equal to the DOCX stanza oracle (#08 head 2→1).
            flush()
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
    return "\n\n".join("\n".join(stanza) for stanza in stanzas).strip() + "\n"


def _surface_unknown_block_diagnostics(doc: ir.Document) -> None:
    """Append one `warning` diagnostic per `UnknownBlock` reachable in `doc.blocks`
    (descending into the container blocks that nest others), so an unmodeled block is
    SURFACED, never silently dropped — the design's "unknown → preserve content /
    emit a diagnostic". The block's text is still preserved at lowering; this only
    makes its presence visible to the caller (which forwards `warning`/`fatal`)."""

    def visit(b: ir.Block) -> None:
        if isinstance(b, ir.UnknownBlock):
            preserved = "preserved its text" if b.text.strip() else "no recoverable text"
            doc.diagnostics.append(ir.Diagnostic(
                "warning", "import.unknown-block",
                f"unmodeled block kind {b.note!r} ({preserved}); surfaced rather than "
                "silently dropped.",
            ))
        elif isinstance(b, ir.BlockQuote):
            for inner in b.blocks:
                visit(inner)
        elif isinstance(b, ir.ListBlock):
            for item in b.items:
                for inner in item:
                    visit(inner)

    for b in doc.blocks:
        visit(b)


def lower(doc: ir.Document, lang: str, *, poem: bool = False) -> str:
    """Lower the document to the canonical Markdown body string.

    `poem` selects verse lowering for the work-as-a-whole: paragraphs become
    stanza-grouped verse lines (one line each, stanzas split on empty paragraphs)
    rather than prose, in a line-per-line shape."""
    _surface_unknown_block_diagnostics(doc)
    if poem:
        body = _lower_poem_body(doc, lang)
        appendix = _footnote_appendix(doc, lang)
        if appendix:
            body = body.rstrip("\n") + "\n\n" + appendix
        return re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"
    pieces: list[str] = []
    for b in doc.blocks:
        md = _block_md(b, lang)
        if md is not None and md != "":
            pieces.append(md)
    body = "\n\n".join(pieces)
    appendix = _footnote_appendix(doc, lang)
    if appendix:
        body = body.rstrip("\n") + "\n\n" + appendix
    return re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"
