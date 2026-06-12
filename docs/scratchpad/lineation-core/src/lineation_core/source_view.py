# research-pure: the per-source-line structural view — ONE production pipeline pass.
"""Per-`<w:br>`-line structural view of a DOCX, built in a SINGLE `adapt` pass.

Two facts about each source line are needed: its geometry (the intact `<w:p>` segment's
text + physics) and its structural role (is this `<w:p>` body, or a heading / signature /
dialogue turn / dropped TOC?). The geometry is read from the adapted, *un-normalized*
paragraphs — the physical line a reader sees, never re-cut. The role is read from a
`normalize(stop_before_lineation=True)` classification of the SAME adapted document.

One `adapt` feeds both views: its blocks are the intact-line geometry, and a normalized copy
of them yields the per-ordinal role verdict. The two structural reads (geometry, role) share
a single parse — no second adapt, no join of two independently-parsed views.

Why the seam, not full normalize: `verse_blocks` merges runs and `merge_source_spans` drops
provenance when a member lacks a span, poisoning per-ordinal provenance; and the lineation
verdict (the decision this system re-judges) is computed only after the seam, so observing
before it keeps every body line addressable and leakage-free. Structural roles are already
assigned at the seam.

Why the line stays intact: lineation is the author's break decision on a physical line as
seen on the page. `normalize`'s dialogue-label pass *re-segments* such a line (a `Speaker:`
prefix becomes its own block, stripped from the body), which would change the very unit
being judged and skew a learner trained on labels assigned against the full line. So the role
is taken from the classification, but the body line's text/features are the intact `<w:p>` segment.
"""
from __future__ import annotations

import copy
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pancratius import docx_adapter as da
from pancratius import ir
from pancratius.ir.normalize import inline_lines, inline_plain, normalize

from . import physics

# a literal *** paragraph pandoc kept as text rather than a thematic break
_THEMATIC_TEXTS = {"***", "* * *", "* * * *", "* * *  *"}


@dataclass(frozen=True)
class Line:
    text: str
    bold: bool          # every non-break inline on this line is strong
    italic: bool        # any emphasis em on this line
    fill: float         # natural single-line advance / reading column (per LINE)
    wraps: bool         # this LINE wraps at the reading column


class Role(StrEnum):
    BODY = "body"
    HEADING = "heading"
    THEMATIC = "thematic"
    TABLE = "table"
    LIST = "list"
    SIGNATURE = "signature"
    EPIGRAPH = "epigraph"
    BLOCKQUOTE = "blockquote"
    IMAGE = "image"
    OTHER = "other"
    EMPTY = "empty"                # blank para — stanza/section separator (NOT a hard boundary)
    CONTEXT = "context"            # normalize classifies this <w:p> as non-body structure


@dataclass
class Para:
    index: int                    # document-order index over IR blocks we keep
    role: Role
    lines: list[Line] = field(default_factory=list)
    align: str = ""
    src_start: int | None = None  # source <w:p> ordinal (span start); None if unmapped
    needs_review: bool = False
    indented: bool = False

    @property
    def text(self) -> str:
        return " ".join(ln.text for ln in self.lines)

    @property
    def empty(self) -> bool:
        return self.role == Role.EMPTY


def _walk(inlines: list[ir.Inline]) -> Iterator[ir.Inline]:
    for n in inlines:
        yield n
        if isinstance(n, ir.ContainerInline):
            yield from _walk(n.children)


def _line_of(inlines: list[ir.Inline], geom: physics.PageGeom) -> Line:
    nz = [n for n in inlines if not isinstance(n, (ir.SoftBreak, ir.LineBreak))]
    bold = bool(nz) and all(isinstance(n, ir.Emphasis) and n.kind == "strong" for n in nz)
    italic = any(isinstance(n, ir.Emphasis) and n.kind == "emph" for n in inlines)
    txt = inline_plain(inlines)
    ws = physics.wrap_stat(txt, geom)
    return Line(text=txt, bold=bold, italic=italic, fill=ws.fill, wraps=ws.wraps)


def _plain_line(s: str) -> Line:
    """A structural-context line (table cell / list item / signature) — text only, no physics."""
    return Line(text=s, bold=False, italic=False, fill=0.0, wraps=False)


# ---------------------------------------------------------------------------
# per-source-ordinal role classification (from the normalized seam)
# ---------------------------------------------------------------------------

# IR block kinds that are non-body STRUCTURE: an ordinal that became ONLY these is
# confidently not a prose/verse candidate. `Paragraph` is body; anything else not listed
# is UNKNOWN → flagged (votable-with-review), never silently masked.
_STRUCTURAL_KINDS = frozenset({
    "Heading", "ThematicBreak", "Table", "ListBlock", "Signature",
    "Epigraph", "BlockQuote", "ImageBlock", "DialogueLabel",
})


class Verdict(StrEnum):
    BODY = "body"        # the classifier says this ordinal is prose body — a votable candidate
    CONTEXT = "context"  # ONLY non-body structure (heading, table, dialogue label, …)
    REVIEW = "review"    # votable like BODY but flagged: mixed / unknown / unmapped / merged


def _verdict_for(kinds: frozenset[str], span: ir.SourceSpan) -> Verdict:
    """Reduce one source ordinal's normalized block-kind set to a votability verdict.
    Mirrors the production `docx_inspect` mask: a mixed `<w:p>` (e.g. label + body) or an
    unexpected merge is flagged, never collapsed to its structural half."""
    if not kinds <= (_STRUCTURAL_KINDS | {"Paragraph"}):
        return Verdict.REVIEW  # an IR kind we do not model — flag, never mask
    if len(kinds) > 1:
        return Verdict.REVIEW  # mixed kinds: the <w:p> split into structure + body
    (kind,) = tuple(kinds)
    if kind in _STRUCTURAL_KINDS:
        return Verdict.CONTEXT
    if kind == "Paragraph" and span.end != span.start:
        return Verdict.REVIEW  # a Paragraph spanning >1 ordinal is an unexpected merge
    return Verdict.BODY


def _classify(doc: ir.Document) -> dict[int, Verdict]:
    """Per source-ordinal verdict from a normalize(stop_before_lineation) document. Only
    ordinals a surviving block carries are in the map; an ordinal ABSENT was dropped by a
    structural pass (ToC/endmatter/contacts) — `read_view` reads that absence as CONTEXT, not body."""
    kinds: dict[int, set[str]] = {}
    spans: dict[int, ir.SourceSpan] = {}
    for block in doc.blocks:
        span = getattr(block, "source_span", None)
        if span is None:
            continue
        name = type(block).__name__
        for ordinal in range(span.start, span.end + 1):
            kinds.setdefault(ordinal, set()).add(name)
            prev = spans.get(ordinal)
            spans[ordinal] = span if prev is None else ir.SourceSpan(
                start=min(prev.start, span.start), end=max(prev.end, span.end))
    return {o: _verdict_for(frozenset(ks), spans[o]) for o, ks in kinds.items()}


# ---------------------------------------------------------------------------
# the view
# ---------------------------------------------------------------------------


def read_view(docx: Path) -> list[Para]:
    """Per-paragraph view from a single `adapt`. Line geometry from the intact un-normalized
    paragraphs; role refined by the normalized per-ordinal classification."""
    geom = physics.page_geom(docx)
    with tempfile.TemporaryDirectory(prefix="lineation-core-") as td:
        doc = da.adapt(docx, Path(td))
    verdicts = _classify(normalize(copy.deepcopy(doc), stop_before_lineation=True))

    out: list[Para] = []
    for idx, b in enumerate(doc.blocks):
        before = len(out)
        match b:
            case ir.Heading():
                out.append(Para(index=idx, role=Role.HEADING, lines=[_line_of(b.inlines, geom)]))
            case ir.ThematicBreak():
                out.append(Para(index=idx, role=Role.THEMATIC))
            case ir.Table():
                rows = [_plain_line(s) for row in b.rows
                        if (s := " | ".join(inline_plain(cell) for cell in row).strip())]
                out.append(Para(index=idx, role=Role.TABLE, lines=rows))
            case ir.ListBlock():
                items = [_plain_line(s) for item in b.items
                         if (s := " ".join(inline_plain(blk.inlines) for blk in item
                                           if hasattr(blk, "inlines")).strip())]
                out.append(Para(index=idx, role=Role.LIST, lines=items))
            case ir.Signature():
                out.append(Para(index=idx, role=Role.SIGNATURE,
                                lines=[_plain_line(s) for s in b.lines], align="right"))
            case ir.Epigraph():
                lines = [_plain_line(s) for s in (*b.quote, *b.footer)]
                out.append(Para(index=idx, role=Role.EPIGRAPH, lines=lines, align="right"))
            case ir.ImageBlock():
                out.append(Para(index=idx, role=Role.IMAGE))
            case ir.Paragraph():
                raw_lines = inline_lines(b.inlines, soft_break=False)
                lines = [_line_of(ln, geom) for ln in raw_lines if inline_plain(ln)]
                has_image = any(isinstance(n, ir.ImageInline) for n in _walk(b.inlines))
                if has_image and not lines:
                    out.append(Para(index=idx, role=Role.IMAGE))
                elif len(lines) == 1 and lines[0].text.strip() in _THEMATIC_TEXTS:
                    out.append(Para(index=idx, role=Role.THEMATIC))
                elif b.empty or not lines:
                    out.append(Para(index=idx, role=Role.EMPTY))
                else:
                    out.append(Para(index=idx, role=Role.BODY, lines=lines, align=b.align,
                                    indented=b.indented))
            case ir.BlockQuote():
                qlines = [_plain_line(inline_plain(blk.inlines))
                          for blk in b.blocks if hasattr(blk, "inlines")]
                out.append(Para(index=idx, role=Role.BLOCKQUOTE, lines=qlines))
            case _:
                out.append(Para(index=idx, role=Role.OTHER))
        sp = getattr(b, "source_span", None)
        if sp is not None:
            for p in out[before:]:
                p.src_start = sp.start

    # refine body roles by the normalize classification.
    for p in out:
        if p.role != Role.BODY:
            continue
        if p.src_start is None:
            p.needs_review = True             # unmapped tail (§14-P1 span-drop)
            continue
        # An ordinal ABSENT from the normalized classification was REMOVED by a structural pass
        # (drop_toc / strip_endmatter / scrub contacts/rights) — it is front/back matter the
        # production importer does not emit as body, so it is CONTEXT (non-votable), NOT a votable
        # review line. Only an ordinal the classifier saw and flagged MIXED/UNKNOWN is REVIEW. This
        # aligns the producer's votable set with `docx_inspect.lineation_decisions` (both treat a
        # dropped ordinal as non-body); the rare §14-P1 span-drop is excluded too — the safe
        # direction (an unmappable line is not gold-worthy), not kept as votable junk.
        verdict = verdicts.get(p.src_start, Verdict.CONTEXT)
        if verdict is Verdict.CONTEXT:
            p.role = Role.CONTEXT
        elif verdict is Verdict.REVIEW:
            p.needs_review = True
    return out
