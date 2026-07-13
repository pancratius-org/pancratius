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

import io
import json
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

from pancratius import docx_source, ir
from pancratius.docx_source import SourceParagraph
from pancratius.ir.inlines import inline_plain
from pancratius.ooxml import W_NS
from pancratius.pandoc import pandoc_argv0

W = docx_source.W

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

# The conventional OOXML prefixes pandoc 3.x resolves drawing/image embeds by. Some
# source DOCX (tool-exported) bind these correct URIs to GENERIC prefixes
# (`ns3:`/`ns5:`/`ns7:` …); pandoc then drops every image despite a spec-valid file.
# `_canonical_pandoc_input` re-prefixes such a doc before pandoc reads it.
_CANONICAL_NS = {
    W_NS: "w",
    "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing": "wp",
    "http://schemas.openxmlformats.org/drawingml/2006/main": "a",
    "http://schemas.openxmlformats.org/drawingml/2006/picture": "pic",
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships": "r",
}
_DOCUMENT_XML = "word/document.xml"


def _ns_bindings(document_xml: bytes) -> dict[str, str]:
    """Every `prefix -> uri` namespace binding declared in the document (last wins)."""
    out: dict[str, str] = {}
    for _event, (prefix, uri) in ET.iterparse(io.BytesIO(document_xml), events=("start-ns",)):
        out[prefix] = uri
    return out


def _needs_canonicalization(bindings: Mapping[str, str]) -> bool:
    """True when any conventional OOXML drawing URI is bound to a NON-conventional prefix —
    the condition under which pandoc, resolving embeds by prefix, drops images. Keyed on the
    embed-resolving URIs themselves (not mere prefix presence), so a doc that binds a canonical
    URI to a generic alias is still caught."""
    return any(uri in _CANONICAL_NS and prefix != _CANONICAL_NS[uri]
               for prefix, uri in bindings.items())


def _reserialize_canonical(document_xml: bytes) -> bytes:
    """Re-serialize `document.xml` forcing conventional prefixes for the drawing URIs. The
    URIs — and therefore the meaning — are unchanged; only prefixes move. Per the package's
    register-before-serialize convention (`docx_render`, `docx_outline`), the canonical prefixes
    are registered immediately before `tostring`; they are the STANDARD OOXML prefixes, so the
    last-wins global registry only ever yields conventional, valid XML for any later serializer."""
    for uri, prefix in _CANONICAL_NS.items():
        ET.register_namespace(prefix, uri)
    return ET.tostring(ET.fromstring(document_xml), encoding="UTF-8", xml_declaration=True)


def _canonical_pandoc_input(docx: Path, work_dir: Path) -> Path:
    """`docx` ready for pandoc's image reader: the original when its drawing namespaces
    already use conventional prefixes, else a re-prefixed temp copy under `work_dir`.

    The rewrite changes ONLY the namespace prefixes of `word/document.xml` (the URIs,
    and therefore the meaning, are unchanged), so the recovered images are real and no
    text is lost. Only `document.xml` is rewritten — images in this corpus live there, not
    in headers/footers/notes. The canonical source model matches by URI and reads
    the ORIGINAL docx — only pandoc, which resolves embeds by prefix, needs this form.

    A docx that cannot be read as a zip/XML here is passed through unchanged: pandoc stays
    the single authority on a malformed file, so this pre-pass never converts a clean pandoc
    error into an obscure one.
    """
    try:
        document_xml = zipfile.ZipFile(docx).read(_DOCUMENT_XML)
    except (OSError, zipfile.BadZipFile, KeyError):
        return docx
    if not _needs_canonicalization(_ns_bindings(document_xml)):
        return docx                      # already conventional — pandoc reads it directly

    src = zipfile.ZipFile(docx)
    parts = {name: src.read(name) for name in src.namelist()}
    parts[_DOCUMENT_XML] = _reserialize_canonical(document_xml)
    out_dir = work_dir / "_canonical_docx"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{docx.stem}.docx"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for name, data in parts.items():
            dst.writestr(name, data)
    return out


def run_pandoc_json(docx: Path, media_dir: Path) -> tuple[dict[str, Any], str]:
    """Parse `docx` to the Pandoc JSON AST, extracting media into `media_dir`.

    Returns `(ast, stderr)`. `+empty_paragraphs` preserves Word empty paragraphs
    so stanza structure reaches the IR. A DOCX whose drawing namespaces use generic
    prefixes is canonicalized first (`_canonical_pandoc_input`) so pandoc resolves its
    images. Raises on a non-zero pandoc exit, and on a `PANDOC_TIMEOUT_SECONDS`
    wall-clock overrun (a hung/pathological conversion is turned into a clear error
    instead of an indefinite hang).
    """
    pandoc_docx = _canonical_pandoc_input(docx, media_dir)
    cmd = [
        pandoc_argv0(), "--from", "docx+empty_paragraphs", "--to", "json",
        "--extract-media", str(media_dir), str(pandoc_docx),
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


def _record_span(record: SourceParagraph) -> ir.SourceSpan:
    ordinal = int(record.ordinal)
    return ir.SourceSpan(ordinal, ordinal)


def _has_contiguous_source_spans(records: Sequence[SourceParagraph]) -> bool:
    """True when records prove adjacent source paragraph ordinals."""
    if not records:
        return False
    previous = records[0]
    for record in records[1:]:
        if record.segment != previous.segment or int(record.ordinal) != int(previous.ordinal) + 1:
            return False
        previous = record
    return True


def _assign_bracketed_empty_spans(
    blocks: list[ir.Block], records: Sequence[SourceParagraph]
) -> int:
    """Attach source spans to empty IR paragraphs only when neighbors prove them.

    Empty paragraphs have no text key, so matching them during the main content walk
    can move the cursor past real content. The truthful case is narrower: an empty
    IR run between two already-sourced blocks maps to exactly the empty DOCX
    paragraph ordinals between those neighbors.
    """
    empty_spans = {
        int(record.ordinal): _record_span(record)
        for record in records
        if record.structural_empty
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
        run_indices = range(start, i)
        prev_span = blocks[start - 1].source_span if start > 0 else None
        next_span = blocks[i].source_span if i < len(blocks) else None
        if prev_span is None or next_span is None:
            continue

        source_ordinals = range(prev_span.end + 1, next_span.start)
        candidate_spans = [empty_spans[ordinal] for ordinal in source_ordinals if ordinal in empty_spans]
        if len(candidate_spans) != len(run_indices):
            continue
        for idx, span in zip(run_indices, candidate_spans, strict=True):
            blocks[idx] = replace(blocks[idx], source_span=span)
            assigned += 1
    return assigned


def _block_plain_for_source_span(block: ir.Block) -> str:
    """Best-effort reading text for top-level source-span reconciliation."""
    match block:
        case ir.Heading() | ir.Paragraph():
            return inline_plain(block.inlines)
        case ir.LineatedBlock():
            return " ".join(
                inline_plain(line.inlines)
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
        case ir.QuoteBlock():
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
    records: Sequence[SourceParagraph],
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


def _align_records(
    block_fps: list[str], rec_fps: list[str], records: Sequence[SourceParagraph]
) -> list[_Match]:
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


def reconcile_source(
    blocks: list[ir.Block], records: Sequence[SourceParagraph]
) -> tuple[int, int]:
    """Reconcile source `w:p` records onto AST blocks by CONTENT, in one alignment.

    Each matched block is REBUILT in its `blocks` slot (nodes are frozen; the
    list is the adapter's own) with its proven source span (provenance); a
    matched `Paragraph` additionally gets the OOXML facts Pandoc drops:
    right/end `w:jc` (the sole alignment any downstream pass reads —
    signature/epigraph detection), `indented`, the `w:pBdr` `border` kind (only
    when the consumed records agree on one), and the visual-continuity
    `lineation_group` (only when unambiguous). Ambiguous or collapsed shapes
    stay unset rather than inventing a source.

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
        span = ir.merge_source_spans(_record_span(record) for record in consumed)
        if span is not None:
            spans += 1
        else:
            span = block.source_span
        if not isinstance(block, ir.Paragraph):
            blocks[m.block] = replace(block, source_span=span)
            continue
        facts = block.facts
        if any(record.indent_departure for record in consumed):
            facts = replace(facts, indented=True)
        # Strict agreement: every text-bearing consumed record must carry the
        # SAME border kind. A Pandoc-fused block spanning bordered and plain
        # source rows stays unbordered — assigning the border would drag the
        # plain text into a set-apart register.
        text_borders = {record.border.value for record in consumed if record.text}
        if len(text_borders) == 1 and (kind := text_borders.pop()):
            facts = replace(facts, border=cast("ir.BorderKind", kind))
        groups = {
            record.visual_group.value
            for record in consumed
            if record.visual_group is not None
        }
        if len(groups) == 1:
            facts = replace(facts, lineation_group=groups.pop())
        if any(r.alignment.is_right_edge for r in consumed) and not facts.align:
            facts = replace(facts, align="right")
            right += 1
        blocks[m.block] = replace(block, facts=facts, source_span=span)
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
                return ir.Paragraph(inlines=[], facts=ir.SourceFacts(empty=True))
            return ir.Paragraph(
                inlines=_inlines(inlines, ctx),
                facts=ir.SourceFacts(italic=_all_italic(inlines)),
            )
        case "HorizontalRule":
            return ir.ThematicBreak()
        case "BlockQuote" if isinstance(c, list):
            return ir.QuoteBlock(blocks=_blocks(c, ctx), register=ir.Register.ORDINARY)
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
            # Spans are unknown at parse time; reconciliation never reaches inside
            # a LineBlock, so its lines stay span-less.
            stanza = [ir.Line(_inlines(line, ctx)) for line in c if isinstance(line, list)]
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


def adapt(docx: Path, media_dir: Path, diagnostics: ir.DiagnosticSink) -> ir.Document:
    """Parse `docx` into an `ir.Document`, extracting media into `media_dir`.

    `diagnostics` is the caller's sink — the same one the passes and the backend
    take. `w:jc` alignment and visual lineation groups are assigned onto the
    top-level `Paragraph` blocks by CONTENT (`reconcile_source`); a `warning`
    fires when right-aligned source paragraphs exist but none reconcile, so a
    future drift can't ship silently. Footnote definitions collected during the
    inline walk are attached densely renumbered.
    """
    ast, warns = run_pandoc_json(docx, media_dir)
    records = docx_source.read(docx).reconciliation_paragraphs

    ctx = _Ctx()
    if warns:
        diagnostics.append(ir.Diagnostic("info", "import.pandoc-warn", warns))

    raw_blocks = ast.get("blocks") or []
    if not isinstance(raw_blocks, list):
        raw_blocks = []
    blocks = _blocks(raw_blocks, ctx)

    span_assigned, assigned = reconcile_source(blocks, records)
    paragraphs = [b for b in blocks if isinstance(b, ir.Paragraph)]

    right_records = sum(1 for r in records if r.alignment.is_right_edge and r.text)
    right_assigned = sum(1 for p in paragraphs if p.align in {"right", "end"})
    diagnostics.append(ir.Diagnostic(
        "info", "import.align-zip",
        f"w:jc records={len(records)} assigned={assigned} "
        f"right-records={right_records} right-assigned={right_assigned} "
        f"source-spans={span_assigned}",
    ))
    # Right-aligned source paragraphs that none reconciled onto the AST — a warning
    # the caller propagates so a future drift fails loud.
    if right_records and not right_assigned:
        diagnostics.append(ir.Diagnostic(
            "warning", "import.align-unreconciled",
            f"{right_records} right-aligned source paragraph(s) but 0 reconciled "
            f"onto the AST — alignment-driven signatures/epigraphs may be lost",
        ))

    return ir.Document(
        blocks=blocks,
        footnotes=[ir.FootnoteDef(id=i, blocks=bs) for i, bs in ctx.fn_defs],
    )
