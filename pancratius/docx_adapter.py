"""DOCX → block IR (the one source adapter).

The parse stage of `docs/import-pipeline.md`: turn a DOCX into the typed IR and
stop. No Markdown string is produced here.

The primary parse is `pandoc --from docx+empty_paragraphs --to json`;
`+empty_paragraphs` keeps Word's empty paragraphs as `Para []` so stanza breaks
survive into the IR. The OOXML side-channel reads paragraph alignment `w:jc` and
visual lineation groups from `w:contextualSpacing`, which Pandoc drops; they are
reconciled onto the IR's `Paragraph` blocks by content.

NOT `import-pure`: it shells to pandoc, reads the DOCX zip, and extracts media into
a caller-provided scratch dir — that impurity is isolated here so downstream stages
stay pure. Footnotes arrive as inline `Note` nodes and are lowered to
`FootnoteRef`/`FootnoteDef` pairs renumbered densely by reference order.
"""

from __future__ import annotations

import json
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from bisect import bisect_left
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any, cast

from pancratius import ir

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
# OOXML markup-compatibility: a run can carry both an `mc:Choice` and an
# `mc:Fallback` rendering of the same content; the side-channel reader drops the
# `mc:Fallback` subtree so its `w:t` text is not double-counted.
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
MC_FALLBACK = f"{{{MC_NS}}}Fallback"

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _words(text: str) -> list[str]:
    """The casefolded reading-word stream of `text` (script-agnostic via `\\w` under
    `re.UNICODE`)."""
    return [m.group(0).casefold() for m in _WORD_RE.finditer(text)]


def _node(value: object) -> dict[str, Any] | None:
    """View an opaque value as a Pandoc `{"t":…, "c":…}` node when it is a dict. The
    cast is needed because a bare `isinstance(x, dict)` narrows to
    `dict[Unknown, Unknown]`, whose keys ty types as `Never`."""
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


# ---------------------------------------------------------------------------
# Pandoc JSON
# ---------------------------------------------------------------------------


# Wall-clock cap on a single pandoc invocation: a loose bound that fires only on a
# pathological input that would otherwise hang the import indefinitely.
PANDOC_TIMEOUT_SECONDS = 300


def run_pandoc_json(docx: Path, media_dir: Path) -> tuple[dict[str, Any], str]:
    """Parse `docx` to the Pandoc JSON AST, extracting media into `media_dir`.

    Returns `(ast, stderr)`. `+empty_paragraphs` preserves Word empty paragraphs
    so stanza structure reaches the IR. Raises on a non-zero pandoc exit, and on a
    `PANDOC_TIMEOUT_SECONDS` wall-clock overrun (a hung/pathological conversion is
    turned into a clear error instead of an indefinite hang).
    """
    cmd = [
        "pandoc", "--from", "docx+empty_paragraphs", "--to", "json",
        "--extract-media", str(media_dir), str(docx),
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=PANDOC_TIMEOUT_SECONDS, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"pandoc timed out after {PANDOC_TIMEOUT_SECONDS}s on {docx.name}; "
            "the conversion was aborted (no partial output is trusted)."
        ) from exc
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed on {docx.name}: {proc.stderr.strip()}")
    return json.loads(proc.stdout), proc.stderr.strip()


# ---------------------------------------------------------------------------
# OOXML w:jc side-channel (the one signal Pandoc drops)
# ---------------------------------------------------------------------------


@dataclass
class _SourceParagraph:
    """One top-level source `w:p`: its reading text plus the OOXML signals the
    importer reconciles onto Pandoc IR by content.

    `indented` is within-book DIRECTIONED, not the raw presence of `w:ind`: in a
    book whose body default carries a first-line indent on every paragraph, the
    raw bit discriminates nothing (verse rows carry it too). It is set by
    `_direction_indents` only where the paragraph's indent signature departs from
    the book-dominant one."""

    align: str
    text: str
    style: str = ""
    contextual_spacing: bool = False
    spacing: dict[str, str] = field(default_factory=dict)
    indent: tuple[tuple[str, str], ...] = ()
    indented: bool = False
    border: ir.BorderKind = ""
    heading: bool = False
    thematic: bool = False
    source_span: ir.SourceSpan | None = None
    source_segment: int = 0
    empty: bool = False
    reconcile: bool = True
    lineation_group: int | None = None


def _direction_indents(records: list[_SourceParagraph]) -> None:
    """Set `indented` to "indent departs from the book default".

    The dominant indent signature over text-bearing body records is the book's
    default paragraph shape; a paragraph is `indented` only when it carries an
    indent that is neither absent nor that default. Runs before lineation-group
    assignment and reconciliation so every consumer reads the directioned bit.
    """
    body = [p for p in records if p.reconcile and p.text.strip()]
    counts: dict[tuple[tuple[str, str], ...], int] = {}
    for p in body:
        counts[p.indent] = counts.get(p.indent, 0) + 1
    dominant = max(counts, key=lambda sig: counts[sig], default=())
    for p in records:
        p.indented = bool(p.indent) and p.indent != dominant


@dataclass(frozen=True)
class _StyleInfo:
    based_on: str
    contextual_spacing: bool
    spacing: dict[str, str]


def _indent_attrs(ppr: ET.Element | None) -> tuple[tuple[str, str], ...]:
    """The `w:ind` attrs of a paragraph as a canonical signature (sorted pairs)."""
    ind = ppr.find(f"{W}ind") if ppr is not None else None
    if ind is None:
        return ()
    return tuple(sorted((k.removeprefix(W), v) for k, v in ind.attrib.items()))


def _spacing_attrs(ppr: ET.Element | None) -> dict[str, str]:
    spacing = ppr.find(f"{W}spacing") if ppr is not None else None
    if spacing is None:
        return {}
    return {k.removeprefix(W): v for k, v in spacing.attrib.items()}


def _doc_default_spacing(zf: zipfile.ZipFile) -> dict[str, str]:
    """Document-wide paragraph spacing inherited when style/direct spacing is absent."""
    try:
        root = ET.fromstring(zf.read("word/styles.xml"))
    except KeyError:
        return {}
    sp = root.find(f"{W}docDefaults/{W}pPrDefault/{W}pPr/{W}spacing")
    return {k.removeprefix(W): v for k, v in sp.attrib.items()} if sp is not None else {}


def _paragraph_styles(zf: zipfile.ZipFile) -> tuple[dict[str, _StyleInfo], str]:
    try:
        root = ET.fromstring(zf.read("word/styles.xml"))
    except KeyError:
        return {}, "Normal"

    styles: dict[str, _StyleInfo] = {}
    default = "Normal"
    for st in root.findall(f".//{W}style"):
        if st.get(f"{W}type") != "paragraph":
            continue
        style_id = str(st.get(f"{W}styleId") or "")
        if not style_id:
            continue
        if st.get(f"{W}default") == "1":
            default = style_id
        ppr = st.find(f"{W}pPr")
        styles[style_id] = _StyleInfo(
            based_on=_w_val(st.find(f"{W}basedOn")),
            contextual_spacing=(
                ppr.find(f"{W}contextualSpacing") is not None if ppr is not None else False
            ),
            spacing=_spacing_attrs(ppr),
        )
    return styles, default


def _w_val(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return str(el.get(f"{W}val") or "")


_BORDER_SIDES = ("top", "bottom", "left", "right")


def border_kind(ppr: ET.Element | None) -> ir.BorderKind:
    """The paragraph's `w:pBdr` gesture kind.

    Reduced to the two side combinations that carry editorial meaning in this
    corpus: a full four-side box ("box") and a left-rule-only bar ("rule").
    `w:between`/`w:bar` edges and `val="none"` sides are not block-set-apart
    gestures and do not count."""
    pbdr = ppr.find(f"{W}pBdr") if ppr is not None else None
    if pbdr is None:
        return ""
    sides = {
        side
        for side in _BORDER_SIDES
        if (el := pbdr.find(f"{W}{side}")) is not None
        and el.get(f"{W}val", "none") not in {"none", "nil"}
    }
    if not sides:
        return ""
    if len(sides) == 4:
        return "box"
    if sides == {"left"}:
        return "rule"
    return "other"


def _style_chain(style: str, styles: dict[str, _StyleInfo]) -> list[_StyleInfo]:
    out: list[_StyleInfo] = []
    seen: set[str] = set()
    cur = style
    while cur and cur not in seen:
        seen.add(cur)
        got = styles.get(cur)
        if got is None:
            break
        out.append(got)
        cur = got.based_on
    return out


def _resolved_contextual_spacing(
    style: str,
    styles: dict[str, _StyleInfo],
    *,
    direct_contextual_spacing: bool,
) -> bool:
    return direct_contextual_spacing or any(
        info.contextual_spacing for info in _style_chain(style, styles)
    )


def _resolved_spacing(
    style: str, styles: dict[str, _StyleInfo], direct: dict[str, str]
) -> dict[str, str]:
    out: dict[str, str] = {}
    for info in reversed(_style_chain(style, styles)):
        out.update(info.spacing)
    out.update(direct)
    return out


def _spacing_before_is_real(p: _SourceParagraph) -> bool:
    if p.spacing.get("beforeAutospacing") == "1":
        return True
    try:
        return int(p.spacing.get("before") or "0") > 0
    except ValueError:
        return False


def _spacing_after_is_real(p: _SourceParagraph) -> bool:
    if p.spacing.get("afterAutospacing") == "1":
        return True
    try:
        return int(p.spacing.get("after") or "0") > 0
    except ValueError:
        return False


def _source_paragraphs_join(a: _SourceParagraph, b: _SourceParagraph) -> bool:
    """Whether adjacent Word paragraphs render as one visual lineated group.

    This is intentionally structural. It does not decide whether the group is
    verse-worthy; it only records that Word suppresses visible paragraph spacing
    between same-style neighbors.

    Debugging note: when this logic drifts, the fastest RCA has been a read-only
    one-off OOXML inspector that prints paragraph index, resolved style,
    `w:contextualSpacing`, spacing attrs, `numPr`/`ind`/`jc`/`pBdr`, and text around
    a failing phrase. Inspect the source signals first; do not prototype by
    rewriting the DOCX and then diffing Pandoc output.
    """
    if not (a.contextual_spacing and b.contextual_spacing):
        return False
    if a.style != b.style:
        return False
    if not a.text.strip() or not b.text.strip():
        return False
    if a.heading or b.heading or a.thematic or b.thematic:
        return False
    if a.indented or b.indented or a.border or b.border:
        return False
    if a.align != b.align:
        return False
    return _spacing_after_is_real(a) or _spacing_before_is_real(b)


def _assign_lineation_groups(paragraphs: list[_SourceParagraph]) -> None:
    if not paragraphs:
        return
    group_id = 1
    run = [paragraphs[0]]
    for prev, cur in pairwise(paragraphs):
        if _source_paragraphs_join(prev, cur):
            run.append(cur)
            continue
        if len(run) > 1:
            for p in run:
                p.lineation_group = group_id
            group_id += 1
        run = [cur]
    if len(run) > 1:
        for p in run:
            p.lineation_group = group_id


def _source_boundary(
    source_span: ir.SourceSpan | None = None,
    *,
    source_segment: int = 0,
) -> _SourceParagraph:
    """A top-level source block that must break visual grouping but does not
    reconcile onto a top-level Pandoc paragraph."""
    return _SourceParagraph(
        align="",
        text="",
        source_span=source_span,
        source_segment=source_segment,
        reconcile=False,
    )


def _paragraph_text(p: ET.Element) -> str:
    """The reading text of a `w:p` (its `w:t` runs, hard breaks as spaces,
    `mc:Fallback` duplicates dropped). Used only to MATCH the paragraph to its AST
    counterpart, so it must render the same glyphs Pandoc does: a `w:noBreakHyphen`/
    `w:softHyphen` is a textless element Pandoc surfaces as U+2011/U+00AD, and
    dropping it would fuse the words it sits between (`кто‑то`→`ктото`), desyncing
    the fingerprint and costing that paragraph its source span."""
    parts: list[str] = []

    def walk(el: ET.Element, *, in_fallback: bool) -> None:
        for child in el:
            if child.tag == MC_FALLBACK:
                walk(child, in_fallback=True)
            elif child.tag == f"{W}t":
                if not in_fallback:
                    parts.append(child.text or "")
            elif child.tag in {f"{W}br", f"{W}cr", f"{W}tab"}:
                parts.append(" ")
            elif child.tag == f"{W}noBreakHyphen":
                parts.append("‑")
            elif child.tag == f"{W}softHyphen":
                parts.append("­")
            else:
                walk(child, in_fallback=in_fallback)

    walk(p, in_fallback=False)
    return "".join(parts)


_NON_TEXT_SOURCE_CONTENT = {f"{W}br", f"{W}cr", f"{W}tab", f"{W}drawing", f"{W}pict", f"{W}object"}


def _paragraph_is_empty_source(p: ET.Element, text: str) -> bool:
    """True for a structural empty paragraph, not an image/object paragraph."""
    return not text.strip() and not any(el.tag in _NON_TEXT_SOURCE_CONTENT for el in p.iter())


def read_w_jc(docx: Path) -> list[_SourceParagraph]:
    """Per-body-paragraph source records in document order.

    Only top-level body paragraphs are walked: the body's direct `w:p` children plus
    those nested in `w:sdt` content controls, skipping `w:tbl` contents (table cells
    are not top-level AST paragraphs).

    List-item paragraphs (`w:numPr`) are skipped: Pandoc collapses a run of list
    `w:p` into one `OrderedList`/`BulletList` block, so they never surface as
    top-level `Para`s and emitting an entry per item would desync this vector from
    the AST sequence. The other collapse/fusion shapes are absorbed by the content
    reconciliation in `reconcile_source`, not enumerated here.
    """
    with zipfile.ZipFile(docx) as zf:
        styles, default_style = _paragraph_styles(zf)
        doc_default_spacing = _doc_default_spacing(zf)
        root = ET.fromstring(zf.read("word/document.xml"))
    body = root.find(f"{W}body")
    if body is None:
        return []
    records: list[_SourceParagraph] = []
    source_index = 0
    source_segment = 0

    def walk(el: ET.Element) -> None:
        nonlocal source_index, source_segment
        for child in el:
            if child.tag == f"{W}p":
                source_span = ir.SourceSpan(source_index, source_index)
                source_index += 1
                ppr = child.find(f"{W}pPr")
                if ppr is not None and ppr.find(f"{W}numPr") is not None:
                    records.append(_source_boundary(
                        source_span,
                        source_segment=source_segment,
                    ))
                    source_segment += 1
                    continue  # list item: Pandoc collapses it into a List block
                direct_style = _w_val(ppr.find(f"{W}pStyle") if ppr is not None else None)
                style = direct_style or default_style
                direct_spacing = _spacing_attrs(ppr)
                txt = _paragraph_text(child).strip()
                records.append(_SourceParagraph(
                    align=_w_val(ppr.find(f"{W}jc") if ppr is not None else None),
                    text=txt,
                    style=style,
                    contextual_spacing=_resolved_contextual_spacing(
                        style,
                        styles,
                        direct_contextual_spacing=(
                            ppr.find(f"{W}contextualSpacing") is not None
                            if ppr is not None
                            else False
                        ),
                    ),
                    spacing={
                        **doc_default_spacing,
                        **_resolved_spacing(style, styles, direct_spacing),
                    },
                    indent=_indent_attrs(ppr),
                    border=border_kind(ppr),
                    heading=bool(re.fullmatch(r"(?:Heading\d+|[1-9])", direct_style)),
                    thematic=txt in {"***", "* * *", "---"},
                    source_span=source_span,
                    source_segment=source_segment,
                    empty=_paragraph_is_empty_source(child, txt),
                ))
            elif child.tag == f"{W}tbl":
                records.append(_source_boundary(source_segment=source_segment))
                source_segment += 1
                continue  # table cells are not top-level AST paragraphs
            elif child.tag == f"{W}sdt":
                content = child.find(f"{W}sdtContent")
                if content is not None:
                    walk(content)

    walk(body)
    _direction_indents(records)
    _assign_lineation_groups(records)
    return [p for p in records if p.reconcile]


def _fingerprint(text: str) -> str:
    """A whitespace/case-insensitive fingerprint of a paragraph's reading words — the
    comparison key reconciliation diffs on. Joining the word stream makes the AST
    `_plain` rendering and the raw `w:t` text comparable."""
    return " ".join(_words(text))


def _source_match_key(text: str) -> str:
    """Content key for provenance matching.

    Word-only fingerprints intentionally ignore punctuation for prose matching, but
    structural punctuation paragraphs such as ``***`` still need source provenance.
    Fall back to normalized literal text only when there are no words to key on.
    """
    return _fingerprint(text) or re.sub(r"\s+", " ", text).strip().casefold()


def _has_contiguous_source_spans(records: list[_SourceParagraph]) -> bool:
    """True when records prove adjacent source paragraph ordinals."""
    if not records or records[0].source_span is None:
        return False
    prev = records[0].source_span
    segment = records[0].source_segment
    for record in records[1:]:
        cur = record.source_span
        if record.source_segment != segment or cur is None or cur.start != prev.end + 1:
            return False
        prev = cur
    return True


def _assign_bracketed_empty_spans(blocks: list[ir.Block], records: list[_SourceParagraph]) -> int:
    """Attach source spans to empty IR paragraphs only when neighbors prove them.

    Empty paragraphs have no text key, so matching them during the main content walk
    can move the cursor past real content. The truthful case is narrower: an empty
    IR run between two already-sourced blocks maps to exactly the empty DOCX
    paragraph ordinals between those neighbors.
    """
    empty_spans = {
        record.source_span.start: record.source_span
        for record in records
        if record.empty and record.source_span is not None
    }
    if not empty_spans:
        return 0

    assigned = 0
    i = 0
    while i < len(blocks):
        block = blocks[i]
        if not (isinstance(block, ir.Paragraph) and block.empty and block.source_span is None):
            i += 1
            continue

        start = i
        while i < len(blocks):
            current = blocks[i]
            if not (
                isinstance(current, ir.Paragraph)
                and current.empty
                and current.source_span is None
            ):
                break
            i += 1
        empty_run = blocks[start:i]
        prev_span = blocks[start - 1].source_span if start > 0 else None
        next_span = blocks[i].source_span if i < len(blocks) else None
        if prev_span is None or next_span is None:
            continue

        source_ordinals = range(prev_span.end + 1, next_span.start)
        candidate_spans = [empty_spans[ordinal] for ordinal in source_ordinals if ordinal in empty_spans]
        if len(candidate_spans) != len(empty_run):
            continue
        for empty_block, span in zip(empty_run, candidate_spans, strict=True):
            empty_block.source_span = span
            assigned += 1
    return assigned


def _block_plain_for_source_span(block: ir.Block) -> str:
    """Best-effort reading text for top-level source-span reconciliation."""
    from pancratius.ir.normalize import inline_plain

    match block:
        case ir.Heading() | ir.Paragraph():
            return inline_plain(block.inlines)
        case ir.LineatedBlock() | ir.VerseBlock():
            return " ".join(
                inline_plain(line)
                for stanza in block.stanzas
                for line in stanza
            )
        case ir.Signature():
            return " ".join(block.lines)
        case ir.Epigraph():
            return " ".join([*block.quote, *block.footer])
        case ir.DialogueLabel():
            return block.speaker
        case ir.ThematicBreak():
            return "***"
        case ir.BlockQuote():
            return " ".join(_block_plain_for_source_span(child) for child in block.blocks)
        case ir.ListBlock():
            return " ".join(
                _block_plain_for_source_span(child)
                for item in block.items
                for child in item
            )
        case ir.CodeBlock():
            return block.text
        case ir.Table():
            return " ".join(inline_plain(cell) for row in block.rows for cell in row)
        case ir.ImageBlock():
            return block.alt
        case ir.UnknownBlock():
            return block.text
        case _:
            return ""


@dataclass(frozen=True)
class _Match:
    """One reconciled correspondence: the AST block at `block` carries the text of
    `n_records` consecutive source records starting at `first_record` (n > 1 when
    Pandoc fused several `w:p` into one block)."""

    block: int
    first_record: int
    n_records: int


def _monotone_anchors(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """The longest subsequence of `(record, block)` pairs (already in record order)
    whose block indices strictly increase — the consistent anchor set. A text that
    truly moved (a non-monotone pair) is dropped rather than allowed to fold the
    alignment back on itself."""
    if not pairs:
        return []
    tails: list[int] = []          # tails[k] = smallest block ending an LIS of length k+1
    back: list[int] = []           # back[i] = predecessor pair index
    tail_idx: list[int] = []       # pair index achieving tails[k]
    for i, (_ri, bi) in enumerate(pairs):
        k = bisect_left(tails, bi)
        back.append(tail_idx[k - 1] if k else -1)
        if k == len(tails):
            tails.append(bi)
            tail_idx.append(i)
        else:
            tails[k] = bi
            tail_idx[k] = i
    out: list[tuple[int, int]] = []
    i = tail_idx[len(tails) - 1]
    while i >= 0:
        out.append(pairs[i])
        i = back[i]
    out.reverse()
    return out


def _match_window(
    block_fps: list[str],
    rec_fps: list[str],
    records: list[_SourceParagraph],
    *,
    blocks_range: range,
    records_range: range,
    out: list[_Match],
) -> None:
    """Greedy order-preserving scan inside one inter-anchor window. For each record
    in order, the cursor advances to the next block carrying its text, accepting
    EXACT first so a record never binds to an unrelated block sharing a prefix:

      * EXACT fingerprint — a standalone `w:p` → one block;
      * a FUSION — consecutive `w:p` Pandoc joined into one block whose fingerprint
        equals the records' concatenation (full equality, never a bare prefix),
        consuming them all; fusion never crosses a source gap (list/table boundary).

    A record whose text never surfaces in the window is skipped (collapsed away)."""

    def fusion_len(scan: int, start_ri: int) -> int:
        block_fp = block_fps[scan]
        built = rec_fps[start_ri]
        if not built or not block_fp.startswith(built):
            return 0
        k = start_ri + 1
        while k < records_range.stop and len(built) < len(block_fp):
            if not _has_contiguous_source_spans(records[start_ri:k + 1]):
                break
            nxt = rec_fps[k]
            if not nxt or not block_fp.startswith(f"{built} {nxt}"):
                break
            built = f"{built} {nxt}"
            k += 1
        return (k - start_ri) if built == block_fp else 0

    cursor = blocks_range.start
    ri = records_range.start
    while ri < records_range.stop:
        if not rec_fps[ri]:
            ri += 1
            continue
        scan = cursor
        consumed = 1
        while scan < blocks_range.stop:
            if block_fps[scan] == rec_fps[ri]:
                break
            if fl := fusion_len(scan, ri):
                consumed = fl
                break
            scan += 1
        if scan >= blocks_range.stop:
            ri += 1
            continue
        out.append(_Match(block=scan, first_record=ri, n_records=consumed))
        cursor = scan + 1
        ri += consumed


def _align_records(block_fps: list[str], rec_fps: list[str], records: list[_SourceParagraph]) -> list[_Match]:
    """THE source↔AST alignment: match every source record onto its AST block once.

    Position alone cannot be trusted (Pandoc collapses some `w:p` — lists, `Div`s,
    `Figure`s, image-only paragraphs — and FUSES others), and a single global greedy
    cursor cannot either: a duplicate prose fingerprint can advance it PAST an early
    signature/epigraph, silently costing that block its metadata (book #32). So the
    alignment is anchored: fingerprints unique on BOTH sides pair up first (kept
    monotone), and the greedy exact-or-fusion scan runs only inside the small
    windows between anchors, where a duplicate can no longer overshoot globally."""
    block_count: dict[str, int] = {}
    for fp in block_fps:
        if fp:
            block_count[fp] = block_count.get(fp, 0) + 1
    rec_count: dict[str, int] = {}
    for fp in rec_fps:
        if fp:
            rec_count[fp] = rec_count.get(fp, 0) + 1
    block_at = {fp: i for i, fp in enumerate(block_fps) if block_count.get(fp) == 1}
    anchors = _monotone_anchors([
        (ri, block_at[fp])
        for ri, fp in enumerate(rec_fps)
        if rec_count.get(fp) == 1 and fp in block_at
    ])

    matches: list[_Match] = []
    prev_r = 0
    prev_b = 0
    for ri, bi in [*anchors, (len(records), len(block_fps))]:
        _match_window(
            block_fps, rec_fps, records,
            blocks_range=range(prev_b, bi),
            records_range=range(prev_r, ri),
            out=matches,
        )
        if ri < len(records):
            matches.append(_Match(block=bi, first_record=ri, n_records=1))
        prev_r = ri + 1
        prev_b = bi + 1
    return matches


def reconcile_source(blocks: list[ir.Block], records: list[_SourceParagraph]) -> tuple[int, int]:
    """Reconcile source `w:p` records onto AST blocks by CONTENT, in one alignment.

    Each matched block gets its proven source span (provenance); a matched
    `Paragraph` additionally gets the OOXML metadata Pandoc drops: right/end `w:jc`
    (the sole alignment any downstream pass reads — signature/epigraph detection),
    `indented`, the `w:pBdr` `border` kind (only when the consumed records agree
    on one), and the visual-continuity `lineation_group` (only when unambiguous).
    Ambiguous or collapsed shapes stay unset rather than inventing a source.

    Returns `(spans_assigned, right_assigned)`.
    """
    if not records:
        return 0, 0
    block_fps = [_source_match_key(_block_plain_for_source_span(b)) for b in blocks]
    rec_fps = [_source_match_key(r.text) for r in records]
    spans = 0
    right = 0
    for m in _align_records(block_fps, rec_fps, records):
        consumed = records[m.first_record:m.first_record + m.n_records]
        block = blocks[m.block]
        span = ir.merge_source_spans(r.source_span for r in consumed)
        if span is not None:
            block.source_span = span
            spans += 1
        if not isinstance(block, ir.Paragraph):
            continue
        if any(r.indented for r in consumed):
            block.indented = True
        # Strict agreement: every text-bearing consumed record must carry the
        # SAME border kind. A Pandoc-fused block spanning bordered and plain
        # source rows stays unbordered — assigning the border would drag the
        # plain text into a set-apart register.
        text_borders = {r.border for r in consumed if r.text.strip()}
        if len(text_borders) == 1 and (kind := text_borders.pop()):
            block.border = kind
        groups = {r.lineation_group for r in consumed if r.lineation_group is not None}
        if len(groups) == 1:
            block.lineation_group = groups.pop()
        if any(r.align in {"right", "end"} for r in consumed) and not block.align:
            block.align = "right"
            right += 1
    spans += _assign_bracketed_empty_spans(blocks, records)
    return spans, right


# ---------------------------------------------------------------------------
# Inline lowering: Pandoc inline node -> IR Inline
# ---------------------------------------------------------------------------

_EMPH_MAP: dict[str, ir.EmphKind] = {
    "Strong": "strong", "Emph": "emph", "Strikeout": "strike",
    "Superscript": "sup", "Subscript": "sub",
}


class _Ctx:
    """Per-document state threaded through the inline/block walk: the running
    footnote index and the footnote definitions collected in reference order."""

    def __init__(self) -> None:
        self.fn_index = 0
        self.fn_defs: list[tuple[int, list[ir.Block]]] = []


def _inlines(nodes: list[dict[str, Any]], ctx: _Ctx) -> list[ir.Inline]:
    out: list[ir.Inline] = []
    for node in nodes:
        out.extend(_inline(node, ctx))
    return out


def _inline(node: dict[str, Any], ctx: _Ctx) -> list[ir.Inline]:
    # Dispatch on Pandoc's string tag; the `isinstance(c, list)` guards inside arms
    # are intrinsic — `c` is positional Pandoc JSON, not a typed shape.
    t = node.get("t")
    c = node.get("c")
    match t:
        case "Str":
            return [ir.Text(str(c))]
        case "Space":
            return [ir.Text(" ")]
        case "SoftBreak":
            return [ir.SoftBreak()]
        case "LineBreak":
            return [ir.LineBreak()]
        case "Strong" | "Emph" | "Strikeout" | "Superscript" | "Subscript":
            children = c if isinstance(c, list) else []
            return [ir.Emphasis(_EMPH_MAP[t], _inlines(children, ctx))]
        case "Underline" | "SmallCaps":  # production unwraps to plain text
            return _inlines(c if isinstance(c, list) else [], ctx)
        case "Quoted" if isinstance(c, list):
            qt, quoted = c
            kind: ir.QuoteKind = (
                "single" if isinstance(qt, dict) and qt.get("t") == "SingleQuote" else "double"
            )
            return [ir.Quoted(kind, _inlines(quoted, ctx))]
        case "Code" if isinstance(c, list):
            return [ir.Code(str(c[1]))]
        case "Link" if isinstance(c, list):
            _attr, label, target = c
            return [ir.Link(_inlines(label, ctx), str(target[0]))]
        case "Image" if isinstance(c, list):
            _attr, label, target = c
            return [ir.ImageInline(src=str(target[0]), alt=_plain(label))]
        case "Span" if isinstance(c, list):
            # Production unwraps a Span, EXCEPT a `dir` attribute (Hebrew/Arabic
            # bidi) governs visual ordering, so it survives as `DirectionalSpan`.
            # `attr` is `[id, classes, [(k, v), ...]]`; only `dir` is preserved.
            attr, span = c
            direction = ""
            if isinstance(attr, list) and len(attr) == 3 and isinstance(attr[2], list):
                for pair in attr[2]:
                    if isinstance(pair, list) and len(pair) == 2 and pair[0] == "dir":
                        direction = str(pair[1])
            children = _inlines(span, ctx)
            if direction:
                return [ir.DirectionalSpan(direction=direction, children=children)]
            return children
        case "Note" if isinstance(c, list):
            # `c` is footnote body blocks. Renumber densely by reference order so the
            # id never depends on Word's internal `w:id`.
            ctx.fn_index += 1
            idx = ctx.fn_index
            ctx.fn_defs.append((idx, _blocks(c, ctx)))
            return [ir.FootnoteRef(raw_index=idx, id=idx)]
        case "RawInline" if isinstance(c, list):
            fmt, raw = c
            return [ir.Text(str(raw))] if fmt in {"html", "markdown"} else []
        case _:
            if isinstance(c, list):
                return [ir.UnknownInline(note=str(t), children=_inlines(c, ctx))]
            return [ir.UnknownInline(note=str(t))]


def _plain(nodes: list[dict[str, Any]]) -> str:
    """Plain-text flatten of inlines (image alt + table cells)."""
    out: list[str] = []
    for node in nodes:
        t = node.get("t")
        c = node.get("c")
        match t:
            case "Str":
                out.append(str(c))
            case "Space" | "SoftBreak" | "LineBreak":
                out.append(" ")
            case _ if t in _EMPH_MAP or t in {"Underline", "SmallCaps", "Span"}:
                payload = c[1] if t == "Span" and isinstance(c, list) else c
                out.append(_plain(payload if isinstance(payload, list) else []))
            case "Quoted" if isinstance(c, list):
                out.append(_plain(c[1]))
            case "Code" if isinstance(c, list):
                out.append(str(c[1]))
            case "Link" | "Image" if isinstance(c, list):
                out.append(_plain(c[1]))
            case _ if isinstance(c, list):
                out.append(_plain(c))
    return "".join(out).strip()


def _node_plain(value: object) -> str:
    """Best-effort readable text of an arbitrary Pandoc node/subtree, so an
    UnknownBlock carries its content instead of dropping it at lowering.

    Structure-agnostic (never assumes the kind's `c` is inlines vs blocks): walks
    dicts/lists generically — a `Str` contributes its text, spacing nodes a space,
    any other `c` list recurses. Inert kinds (e.g. `Null`) yield `""`."""
    parts: list[str] = []

    def walk(v: object) -> None:
        nd = _node(v)
        if nd is not None:
            t = nd.get("t")
            c = nd.get("c")
            if t == "Str":
                parts.append(str(c))
            elif t in {"Space", "SoftBreak", "LineBreak"}:
                parts.append(" ")
            elif isinstance(c, (list, dict)):
                walk(c)
        elif isinstance(v, list):
            for item in v:
                walk(item)

    walk(value)
    return re.sub(r"\s+", " ", "".join(parts)).strip()


# ---------------------------------------------------------------------------
# Block lowering: Pandoc block node -> IR Block
# ---------------------------------------------------------------------------


def _blocks(nodes: list[Any] | None, ctx: _Ctx) -> list[ir.Block]:
    """A block sequence with `Div`/`Figure` children spliced in place.

    Production unwraps Divs; splicing at parse time means a quote block in the
    IR always carries reading semantics, never plumbing. A `Figure` contributes
    its content blocks then its caption blocks, so neither is lost."""
    out: list[ir.Block] = []
    for node in nodes or []:
        t = node.get("t") if isinstance(node, dict) else None
        c = node.get("c") if isinstance(node, dict) else None
        if t == "Div" and isinstance(c, list):
            _attr, children = c
            out.extend(_blocks(children, ctx))
        elif t == "Figure" and isinstance(c, list):
            _attr, caption, content = c
            out.extend(_blocks(content, ctx))
            cap_blocks = caption[1] if isinstance(caption, list) and len(caption) > 1 else None
            if cap_blocks:
                out.extend(_blocks(cap_blocks, ctx))
        else:
            out.append(_block(node, ctx))
    return out


def _block(node: dict[str, Any], ctx: _Ctx) -> ir.Block:
    # Dispatch on Pandoc's string tag; the `isinstance(c, list)` guards inside arms
    # are intrinsic — `c` is positional Pandoc JSON, not a typed shape.
    t = node.get("t")
    c = node.get("c")
    match t:
        case "Div" | "Figure":
            raise AssertionError(f"{t} reaches _block; containers are spliced in _blocks")
        case "Header" if isinstance(c, list):
            level, _attr, inlines = c
            return ir.Heading(level=int(level), inlines=_inlines(inlines, ctx))
        case "Para" | "Plain":
            inlines = c if isinstance(c, list) else []
            if not inlines:
                return ir.Paragraph(inlines=[], empty=True)
            para = ir.Paragraph(inlines=_inlines(inlines, ctx))
            para.italic = _all_italic(inlines)
            return para
        case "HorizontalRule":
            return ir.ThematicBreak()
        case "BlockQuote" if isinstance(c, list):
            return ir.BlockQuote(blocks=_blocks(c, ctx))
        case "BulletList" if isinstance(c, list):
            return ir.ListBlock(ordered=False, items=[_blocks(item, ctx) for item in c])
        case "OrderedList" if isinstance(c, list):
            attr, items = c  # attr = [start, style, delim]; keep the source start ordinal
            start = int(attr[0]) if isinstance(attr, list) and attr else 1
            return ir.ListBlock(
                ordered=True, start=start,
                items=[_blocks(item, ctx) for item in items],
            )
        case "LineBlock" if isinstance(c, list):
            # Pandoc `LineBlock` proves structural lineation, not verse register.
            # Normalization may promote it later if surrounding register context
            # warrants that; the adapter only preserves the authored line shape.
            stanza = [_inlines(line, ctx) for line in c if isinstance(line, list)]
            return ir.LineatedBlock(
                stanzas=[stanza],
                evidence=ir.LineationEvidence(pandoc_line_block=True),
            )
        case "CodeBlock" if isinstance(c, list):
            _attr, text = c
            return ir.CodeBlock(text=str(text))
        case "Table":
            return _table(node, ctx)
        case _:
            # Unmodelled kind: preserve best-effort reading text (lowering emits it +
            # surfaces a diagnostic) so content is never silently dropped.
            return ir.UnknownBlock(note=str(t), text=_node_plain(c))


def _all_italic(inlines: list[dict[str, Any]]) -> bool:
    """True when every text-bearing top-level inline is wrapped in `Emph` (the
    epigraph italic signal)."""
    saw = False
    for node in inlines:
        t = node.get("t")
        if t in {"Space", "SoftBreak", "LineBreak"}:
            continue
        if t == "Emph":
            saw = True
            continue
        return False
    return saw


_EMPH_WRAP: dict[str, tuple[str, str]] = {
    "Strong": ("**", "**"), "Emph": ("*", "*"), "Strikeout": ("~~", "~~"),
    "Superscript": ("^", "^"), "Subscript": ("~", "~"),
}


def _inline_md(nodes: list[dict[str, Any]]) -> str:
    """Plain Markdown render of Pandoc inlines — used only for table cells (the
    one place the adapter flattens inlines to text for `ir.Table.rows`)."""
    out: list[str] = []
    for node in nodes:
        t = node.get("t")
        c = node.get("c")
        match t:
            case "Str":
                out.append(str(c))
            case "Space" | "SoftBreak" | "LineBreak":
                out.append(" ")
            case "Strong" | "Emph" | "Strikeout" | "Superscript" | "Subscript" if isinstance(c, list):
                o, cl = _EMPH_WRAP[t]
                out.append(f"{o}{_inline_md(c)}{cl}")
            case ("Underline" | "SmallCaps") if isinstance(c, list):
                out.append(_inline_md(c))
            case "Quoted" if isinstance(c, list):
                qt, quoted = c
                o, cl = ("'", "'") if isinstance(qt, dict) and qt.get("t") == "SingleQuote" else ("«", "»")
                out.append(f"{o}{_inline_md(quoted)}{cl}")
            case "Code" if isinstance(c, list):
                out.append(f"`{c[1]}`")
            case "Link" if isinstance(c, list):
                _a, label, target = c
                out.append(f"[{_inline_md(label)}]({target[0]})")
            case "Image" if isinstance(c, list):
                _a, label, target = c
                out.append(f"![{_plain(label)}]({target[0]})")
            case "Span" if isinstance(c, list):
                out.append(_inline_md(c[1]))
            case _ if isinstance(c, list):
                out.append(_inline_md(c))
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _table(node: dict[str, Any], ctx: _Ctx) -> ir.Table:
    """Structure a Pandoc 3.x Table into `ir.Table`. `rows` carries STRUCTURED
    cell content (rows of cells of inlines) so reading-content table cells flow
    through the same AI-alt and asset passes as prose; `raw` keeps the node for the
    bibliography classifier (it needs hrefs + image alts)."""
    c = node.get("c")
    rows: list[list[list[ir.Inline]]] = []

    def cell_inlines(cell: object) -> list[ir.Inline]:
        # cell = [attr, alignment, rowspan, colspan, blocks]; narrow before indexing.
        if not isinstance(cell, list) or len(cell) < 5 or not isinstance(cell[4], list):
            return []
        out: list[ir.Inline] = []
        for raw in cell[4]:
            b = _node(raw)
            if b is not None and b.get("t") in {"Para", "Plain"}:
                if out:
                    out.append(ir.Text(" "))  # join multi-block cells with a space
                payload = b.get("c")
                out.extend(_inlines(payload if isinstance(payload, list) else [], ctx))
        return out

    def cells_of(row: object) -> list[list[ir.Inline]]:
        # row = [attr, cells]
        if not isinstance(row, list) or len(row) < 2 or not isinstance(row[1], list):
            return []
        return [cell_inlines(cell) for cell in row[1]]

    if isinstance(c, list):
        try:
            _attr, _cap, _cols, thead, tbodies, _tfoot = c
            for hrow in (thead[1] if thead else []):
                rows.append(cells_of(hrow))
            for tbody in tbodies:
                # tbody = [attr, rowheadcols, headerrows, bodyrows]
                for brow in tbody[3]:
                    rows.append(cells_of(brow))
        except (ValueError, IndexError, TypeError):
            # A table shape we don't recognize keeps `raw` for the classifier and
            # an empty `rows` (lowered to nothing rather than guessed).
            pass
    return ir.Table(rows=rows, raw=node)


# ---------------------------------------------------------------------------
# Top-level adapter
# ---------------------------------------------------------------------------


def adapt(docx: Path, media_dir: Path) -> ir.Document:
    """Parse `docx` into an `ir.Document`, extracting media into `media_dir`.

    `w:jc` alignment and visual lineation groups are assigned onto the top-level
    `Paragraph` blocks by CONTENT (`reconcile_source`); a `warning` fires when
    right-aligned source paragraphs exist but none reconcile, so a future drift
    can't ship silently. Footnote definitions collected during the inline walk are
    attached densely renumbered.
    """
    ast, warns = run_pandoc_json(docx, media_dir)
    records = read_w_jc(docx)

    ctx = _Ctx()
    doc = ir.Document()
    if warns:
        doc.diagnostics.append(ir.Diagnostic("info", "import.pandoc-warn", warns))

    raw_blocks = ast.get("blocks") or []
    if not isinstance(raw_blocks, list):
        raw_blocks = []
    doc.blocks.extend(_blocks(raw_blocks, ctx))

    span_assigned, assigned = reconcile_source(doc.blocks, records)
    paragraphs = [b for b in doc.blocks if isinstance(b, ir.Paragraph)]

    right_records = sum(1 for r in records if r.align in {"right", "end"} and r.text.strip())
    right_assigned = sum(1 for p in paragraphs if p.align in {"right", "end"})
    doc.diagnostics.append(ir.Diagnostic(
        "info", "import.align-zip",
        f"w:jc records={len(records)} assigned={assigned} "
        f"right-records={right_records} right-assigned={right_assigned} "
        f"source-spans={span_assigned}",
    ))
    # Right-aligned source paragraphs that none reconciled onto the AST — a warning
    # the caller propagates so a future drift fails loud.
    if right_records and not right_assigned:
        doc.diagnostics.append(ir.Diagnostic(
            "warning", "import.align-unreconciled",
            f"{right_records} right-aligned source paragraph(s) but 0 reconciled "
            f"onto the AST — alignment-driven signatures/epigraphs may be lost",
        ))

    doc.footnotes = [ir.FootnoteDef(id=i, blocks=bs) for i, bs in ctx.fn_defs]
    return doc
