# research-pure: reads src/content DOCX read-only via the IR; writes only to scratch.
"""IR-faithful substrate — replaces the lossy `docx_inspect.ParaRow` for research.

The audit (audit_external.md) and the user's repeated point share one root cause: the
old substrate was blind to the author's STRUCTURE. Two failures it inverted/dropped:
  - `<w:br>` hard line breaks were joined into one string (a 7-line verse stanza →
    one long row flagged `wraps=True` → fed to a judge as "proof of prose").
  - emphasis (bold/italic) and the header/`***` boundary skeleton were absent.

This view is built from the typed IR that `docx_adapter.adapt` already produces (the
SAME path production uses; pandoc keeps `LineBreak`), so it preserves:
  - per-`<w:br>` LINES, never joined — each with its own wrap stat + emphasis;
  - the HARD-BOUNDARY skeleton: real headings (any level), thematic breaks (`***`/
    `<hr>`), tables, lists, right-aligned signature/epigraph. A run never crosses one.
  - a per-block structural class, incl. inferred bold-pseudo-header / speaker-label
    (lower-confidence, used only where the author left no real headings).

Units:
  Line   — one rendered line (a `<w:p>` with no `<w:br>` = 1 line; with N breaks =
           N+1 lines). Carries text, emphasis(bold/italic), wrap stat.
  Para   — one source `<w:p>`: its lines + block role + alignment + br_count.
The hard boundary skeleton is `Para.boundary` (a hard break before this para).
"""
from __future__ import annotations

import html as _html
import re as _re
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import wrap as wrapmod  # noqa: E402

from pancratius import docx_adapter as da  # noqa: E402
from pancratius import ir  # noqa: E402
from pancratius.ir.normalize import inline_lines, inline_plain  # noqa: E402

_RIGHT = {"right", "end"}
_THEMATIC_TEXTS = {"***", "* * *", "* * * *", "* * *  *"}  # a literal *** paragraph pandoc kept as text


class LineKey(NamedTuple):
    """A body line's address: its `Para.index` and the 0-based sub-line within that
    paragraph (a `<w:p>` with N `<w:br>` spans sub 0..N). The unit the panel votes on.
    Serializes through json as a 2-element list, matching the on-disk `keys`."""

    idx: int
    sub: int


def _walk(inlines: list[ir.Inline]) -> Iterator[ir.Inline]:
    """Depth-first inline walk (recurse containers) — to find inline images, etc."""
    for n in inlines:
        yield n
        if isinstance(n, ir.ContainerInline):
            yield from _walk(n.children)


def inline_md(inlines: list[ir.Inline]) -> str:
    """Reading text with INLINE emphasis preserved as Markdown (**strong**, *em*, `code`).
    Mirrors inline_plain but keeps partial-line emphasis — one bold word renders bold, not
    the whole line."""
    out: list[str] = []
    for n in inlines:
        match n:
            case ir.Text():
                out.append(n.value)
            case ir.SoftBreak() | ir.LineBreak():
                out.append(" ")
            case ir.Quoted():
                o, c = ("'", "'") if n.single else ("«", "»")
                out.append(o + inline_md(n.children) + c)
            case ir.Code():
                out.append(f"`{n.value}`")
            case ir.Emphasis():
                inner = inline_md(n.children)
                mark = {"strong": "**", "emph": "*", "strike": "~~"}.get(n.kind, "*")
                out.append(f"{mark}{inner}{mark}" if inner else "")
            case ir.ImageInline():
                out.append(n.alt)
            case ir.Link() | ir.DirectionalSpan() | ir.UnknownInline():
                out.append(inline_md(n.children))
            # FootnoteRef and anything else carry no reading text
    return _re.sub(r"\s+", " ", "".join(out)).strip()


def inline_html(inlines: list[ir.Inline]) -> str:
    """Escaped HTML with inline emphasis as <strong>/<em>/<code>, for faithful candidate
    rendering — partial-line emphasis survives instead of collapsing to a whole-line flag."""
    out: list[str] = []
    for n in inlines:
        match n:
            case ir.Text():
                out.append(_html.escape(n.value))
            case ir.SoftBreak() | ir.LineBreak():
                out.append(" ")
            case ir.Quoted():
                o, c = ("'", "'") if n.single else ("«", "»")
                out.append(o + inline_html(n.children) + c)
            case ir.Code():
                out.append(f"<code>{_html.escape(n.value)}</code>")
            case ir.Emphasis():
                inner = inline_html(n.children)
                tag = {"strong": "strong", "emph": "em", "strike": "s",
                       "sup": "sup", "sub": "sub"}.get(n.kind, "em")
                out.append(f"<{tag}>{inner}</{tag}>" if inner else "")
            case ir.ImageInline():
                out.append(_html.escape(n.alt))
            case ir.Link() | ir.DirectionalSpan() | ir.UnknownInline():
                out.append(inline_html(n.children))
    return "".join(out).strip()


@dataclass(frozen=True)
class Line:
    text: str
    bold: bool          # every non-break inline on this line is strong
    italic: bool        # any emphasis em on this line
    fill: float         # natural single-line advance / reading column (per LINE)
    wraps: bool         # this LINE wraps at the reading column
    md: str = ""        # reading text with inline emphasis (Markdown) — for the listing
    html: str = ""      # reading text with inline emphasis (HTML) — for candidate render


class Role(StrEnum):
    """A paragraph's structural role. `BODY` paragraphs are the prose/lineated candidates;
    everything else is a boundary or non-body content. `StrEnum` so a role round-trips
    through json as its lowercase string and stays `==`-comparable to legacy data."""

    BODY = "body"
    HEADING = "heading"            # real heading (any level) — HARD boundary
    THEMATIC = "thematic"          # *** / <hr> — HARD boundary
    TABLE = "table"                # HARD boundary
    LIST = "list"                  # HARD boundary
    SIGNATURE = "signature"        # right-aligned signature — HARD boundary (struct)
    EPIGRAPH = "epigraph"          # right-aligned epigraph — HARD boundary (struct)
    BLOCKQUOTE = "blockquote"      # quoted material (holds blocks) — HARD boundary
    IMAGE = "image"                # an image (block or image-only paragraph) — HARD boundary
    OTHER = "other"                # any other IR block (code/footnote/…) — HARD boundary
    EMPTY = "empty"                # blank para — stanza/section separator (NOT hard)
    PSEUDO_HEADER = "pseudo_header"   # inferred bold section head — SOFT boundary
    SPEAKER_LABEL = "speaker_label"   # inferred **Speaker:** — SOFT boundary

    @property
    def is_hard_boundary(self) -> bool:
        return self in HARD_BOUNDARY_ROLES

    @property
    def is_soft_boundary(self) -> bool:
        return self in SOFT_BOUNDARY_ROLES


# Back-compat module-level names so call sites keep reading `iv.ROLE_BODY`.
ROLE_BODY = Role.BODY
ROLE_HEADING = Role.HEADING
ROLE_THEMATIC = Role.THEMATIC
ROLE_TABLE = Role.TABLE
ROLE_LIST = Role.LIST
ROLE_SIGNATURE = Role.SIGNATURE
ROLE_EPIGRAPH = Role.EPIGRAPH
ROLE_BLOCKQUOTE = Role.BLOCKQUOTE
ROLE_IMAGE = Role.IMAGE
ROLE_OTHER = Role.OTHER
ROLE_EMPTY = Role.EMPTY
ROLE_PSEUDO_HEADER = Role.PSEUDO_HEADER
ROLE_SPEAKER_LABEL = Role.SPEAKER_LABEL

HARD_BOUNDARY_ROLES = frozenset({Role.HEADING, Role.THEMATIC, Role.TABLE, Role.LIST,
                                 Role.SIGNATURE, Role.EPIGRAPH, Role.BLOCKQUOTE,
                                 Role.IMAGE, Role.OTHER})
SOFT_BOUNDARY_ROLES = frozenset({Role.PSEUDO_HEADER, Role.SPEAKER_LABEL})


@dataclass
class Para:
    index: int                    # document-order index over IR blocks we keep
    role: Role
    lines: list[Line] = field(default_factory=list)
    align: str = ""
    br_count: int = 0             # hard <w:br> in the source para (lines = br_count+1)
    level: int = 0                # heading level (role==heading)
    boundary: bool = False        # a HARD boundary begins at/just-before this para
    bold_all: bool = False        # whole para all-bold (header/label signal)
    src_start: int | None = None  # source <w:p> ordinal range (render-slice index space):
    src_end: int | None = None    # from the IR block's SourceSpan. None if block has none.

    @property
    def text(self) -> str:
        return " ".join(ln.text for ln in self.lines)

    @property
    def empty(self) -> bool:
        return self.role == ROLE_EMPTY


def _line_of(inlines: list[ir.Inline], geom: wrapmod.PageGeom) -> Line:
    nz = [n for n in inlines if not isinstance(n, (ir.SoftBreak, ir.LineBreak))]
    bold = bool(nz) and all(isinstance(n, ir.Emphasis) and n.kind == "strong" for n in nz)
    italic = any(isinstance(n, ir.Emphasis) and n.kind == "emph" for n in inlines)
    txt = inline_plain(inlines)
    ws = wrapmod.wrap_stat(txt, geom)
    return Line(text=txt, bold=bold, italic=italic, fill=ws.fill, wraps=ws.wraps,
                md=inline_md(inlines), html=inline_html(inlines))


def _looks_pseudo_header(p: Para) -> bool:
    """A short all-bold standalone body paragraph the author used as a section head:
    one line, all-bold, not sentence-final punctuation. LOWER confidence than a real
    heading — used only as a soft boundary where real headings are sparse."""
    if p.role != ROLE_BODY or len(p.lines) != 1 or not p.lines[0].bold:
        return False
    t = p.lines[0].text.strip()
    # A short all-bold line ending in sentence/ellipsis/colon/comma OR a trailing dash is a
    # sentence or a mid-thought continuation (e.g. "Но не извне —" → "а изнутри."), not a
    # section head — a trailing em/en/hyphen dash marks an obvious continuation into the next line.
    return bool(t) and len(t) <= 60 and not t.endswith((".", "!", "?", "…", ":", ";", ",", "—", "–", "-"))


def _looks_speaker_label(p: Para) -> bool:
    """An all-bold `Speaker:`-style turn (ends with ':' or is a known label)."""
    # F10: only a SINGLE-line all-bold "Speaker:" is a label; a multi-line paragraph whose
    # first line ends ":" is a colon-opener of a stanza, not a speaker label.
    if p.role != ROLE_BODY or len(p.lines) != 1 or not p.lines[0].bold:
        return False
    t = p.lines[0].text.strip()
    return t.endswith(":") and len(t) <= 50


def read_view(docx: Path) -> list[Para]:
    """Faithful per-paragraph view from the IR, with the hard-boundary skeleton."""
    geom = wrapmod.page_geom(docx)
    with tempfile.TemporaryDirectory(prefix="ir-view-") as td:
        doc = da.adapt(docx, Path(td))
    out: list[Para] = []
    idx = 0
    for b in doc.blocks:
        _before = len(out)
        match b:
            case ir.Heading():
                out.append(Para(index=idx, role=ROLE_HEADING,
                                lines=[_line_of(b.inlines, geom)], level=b.level,
                                boundary=True))
            case ir.ThematicBreak():
                out.append(Para(index=idx, role=ROLE_THEMATIC, boundary=True))
            case ir.Table():
                # surface row text as context (still a hard boundary, never a body candidate)
                rows = []
                for row in b.rows:
                    s = " | ".join(inline_plain(cell) for cell in row).strip()
                    if s:
                        rows.append(Line(s, False, False, 0.0, False, md=s, html=_html.escape(s)))
                out.append(Para(index=idx, role=ROLE_TABLE, lines=rows, boundary=True))
            case ir.ListBlock():
                # surface list-item text as context (hard boundary, not a candidate)
                items = []
                for item in b.items:
                    s = " ".join(inline_plain(blk.inlines) for blk in item
                                 if hasattr(blk, "inlines")).strip()
                    if s:
                        items.append(Line(s, False, False, 0.0, False, md="- " + s,
                                          html=_html.escape(s)))
                out.append(Para(index=idx, role=ROLE_LIST, lines=items, boundary=True))
            case ir.Signature():
                out.append(Para(index=idx, role=ROLE_SIGNATURE,
                                lines=[Line(s, False, False, 0.0, False) for s in b.lines],
                                align="right", boundary=True))
            case ir.Epigraph():
                lines = [Line(s, False, False, 0.0, False) for s in (*b.quote, *b.footer)]
                out.append(Para(index=idx, role=ROLE_EPIGRAPH, lines=lines,
                                align="right", boundary=True))
            case ir.ImageBlock():
                out.append(Para(index=idx, role=ROLE_IMAGE, boundary=True))
            case ir.Paragraph():
                raw_lines = inline_lines(b.inlines, soft_break=False)
                lines = [_line_of(ln, geom) for ln in raw_lines if inline_plain(ln)]
                has_image = any(isinstance(n, ir.ImageInline) for n in _walk(b.inlines))
                if has_image and not lines:
                    # an image-only paragraph (e.g. #02 idx1022 between two scene beats):
                    # a HARD boundary, NOT a blank/stanza separator. A run cannot cross it.
                    # (This was the bug: it was lumped into ROLE_EMPTY and silently dropped,
                    # so an illustration between two narrative lines vanished and the lines
                    # fused.)
                    out.append(Para(index=idx, role=ROLE_IMAGE, boundary=True))
                elif len(lines) == 1 and lines[0].text.strip() in _THEMATIC_TEXTS:
                    # a literal "***" paragraph (pandoc kept it as text, not a ThematicBreak):
                    # a thematic separator / hard boundary, never a votable body line.
                    out.append(Para(index=idx, role=ROLE_THEMATIC, boundary=True))
                elif b.empty or not lines:
                    # blank, or a husk that flattens to nothing (e.g. an empty `** **`
                    # emphasis artifact production strips later) — a stanza/section
                    # separator, never a body candidate. No ghost body paragraphs.
                    out.append(Para(index=idx, role=ROLE_EMPTY))
                else:
                    br = len(lines) - 1
                    bold_all = all(ln.bold for ln in lines)
                    out.append(Para(index=idx, role=ROLE_BODY, lines=lines,
                                    align=b.align, br_count=br, bold_all=bold_all))
            case ir.BlockQuote():
                # holds BLOCKS, not inlines — flatten its paragraphs' reading text for
                # the preview, but it is a hard boundary (quoted material is not part of
                # an adjacent prose/verse run). Never a body candidate.
                qlines = [Line(inline_plain(blk.inlines), False, False, 0.0, False)
                          for blk in b.blocks if hasattr(blk, "inlines")]
                out.append(Para(index=idx, role=ROLE_BLOCKQUOTE, lines=qlines, boundary=True))
            case _:
                # Any block we don't model (CodeBlock, ImageBlock, FootnoteDef,
                # DialogueLabel, VerseBlock-from-a-prior-pass, …). NEVER silently a
                # body candidate (that was the bug): treat as an opaque hard boundary.
                out.append(Para(index=idx, role=ROLE_OTHER, boundary=True))
        # stamp the IR block's SourceSpan (real source <w:p> ordinals, render-slice index
        # space) onto every Para this block produced — the robust render↔structure bridge.
        sp = getattr(b, "source_span", None)
        if sp is not None:
            for p in out[_before:]:
                p.src_start, p.src_end = sp.start, sp.end
        idx += 1
    # second pass: infer soft boundaries (pseudo-header / speaker-label) on body paras. A
    # pseudo-header must be ISOLATED — a short all-bold line whose body neighbours are NOT
    # all-bold. A run of adjacent all-bold body lines is a bold SENTENCE wrapped across <w:p>
    # rows (e.g. #64 "тишина воспринимается / как отсутствие звуков"), not a stack of headers.
    body_order = [i for i, p in enumerate(out) if p.role == ROLE_BODY]
    bold_at = {i for i in body_order if out[i].bold_all}
    for k, i in enumerate(body_order):
        p = out[i]
        if _looks_speaker_label(p):
            p.role = ROLE_SPEAKER_LABEL
        elif _looks_pseudo_header(p):
            prev_bold = k > 0 and body_order[k - 1] in bold_at
            next_bold = k + 1 < len(body_order) and body_order[k + 1] in bold_at
            if not (prev_bold or next_bold):
                p.role = ROLE_PSEUDO_HEADER
    return out


# ---------------------------------------------------------------------------
# run segmentation honoring the boundary skeleton
# ---------------------------------------------------------------------------


def segments(paras: list[Para], *, soft_boundaries: bool = True) -> list[tuple[int, int]]:
    """Maximal spans of consecutive BODY paragraphs (empties allowed inside as stanza
    separators), bounded by HARD boundaries (always) and SOFT boundaries (optional).
    Returns (lo_index, hi_index) inclusive over `paras` list positions of each run's
    body span. A run never crosses a boundary; empties at the edges are trimmed."""
    bounds = set(HARD_BOUNDARY_ROLES)
    if soft_boundaries:
        bounds |= SOFT_BOUNDARY_ROLES
    # F5: a run extends across BODY and EMPTY (stanza separators) but stops at any role
    # in `bounds`. The previous code hardcoded BODY/EMPTY and never consulted `bounds`,
    # so soft boundaries always split regardless of the flag (silent no-op).
    runs: list[tuple[int, int]] = []
    i, n = 0, len(paras)
    while i < n:
        if paras[i].role == ROLE_BODY:
            j = i
            body_positions: list[int] = []
            while j < n and paras[j].role not in bounds:
                if paras[j].role == ROLE_BODY:
                    body_positions.append(j)
                j += 1
            if body_positions:
                runs.append((body_positions[0], body_positions[-1]))
            i = j
        else:
            i += 1
    return runs


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="render the IR-faithful view of a book region")
    ap.add_argument("--book", required=True)
    ap.add_argument("--around", help="substring to locate")
    ap.add_argument("--ctx", type=int, default=10)
    args = ap.parse_args()
    import features as feat
    bd = {f"{n:02d}": p for n, p in feat.book_dirs()}
    paras = read_view(bd[args.book])
    center = 0
    if args.around:
        center = next((k for k, p in enumerate(paras) if args.around in p.text), 0)
    lo, hi = max(0, center - args.ctx), min(len(paras), center + args.ctx + 1)
    for p in paras[lo:hi]:
        for li, ln in enumerate(p.lines):
            em = ("B" if ln.bold else "") + ("I" if ln.italic else "")
            mark = f"{p.role[:5]:<5}" if li == 0 else "  ·  "
            bnd = "║" if (li == 0 and p.boundary) else " "
            print(f"{bnd}{p.index:>4} {mark} {em:<2} f{ln.fill:>4.2f}{'W' if ln.wraps else ' '}  {ln.text[:60]}")
        if not p.lines:
            print(f"{'║' if p.boundary else ' '}{p.index:>4} {p.role[:5]:<5}  ∅")
