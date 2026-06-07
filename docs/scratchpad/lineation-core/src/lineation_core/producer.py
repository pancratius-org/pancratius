# research-pure: reads src/content DOCX read-only via the production IR; scratch only.
"""The ONE feature producer + the thin views over its records.

`read_lines(docx, lang, book_id) -> [LineRecord]` is the single feature producer. It reads the
`source_view` substrate — one production `adapt + normalize(stop_before_lineation)` pass,
with per-line physics from the vendored `physics` simulator and per-`<w:p>` layout from
`docx_inspect.ParaRow` joined by `src_ordinal`. Every feature is computed exactly ONCE here.

The views — `to_vector(features)` and `render_listing(records)` — read `record.features` and
recompute NOTHING. There is structurally no second feature path: the student vector and the teacher
listing are two renderings of the SAME dataclass, so a feature can never live in one but not
the other (the parity test proves it by perturbation).
"""
from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from statistics import median

from pancratius import docx_inspect as di

from . import identity, records, source_view
from .identity import BookId, LineId, ListingKey
from .records import (
    EndPunct,
    FeatureName,
    FeatureVector,
    IndentVsBook,
    InlineRun,
    LineFeatures,
    LineMeta,
    LineRecord,
    Role,
    SourceFate,
    SpacingVsBook,
)

# source_view.Role -> the canonical record Role.
_ROLE = {
    source_view.Role.BODY: Role.BODY, source_view.Role.HEADING: Role.HEADING,
    source_view.Role.LIST: Role.LIST, source_view.Role.TABLE: Role.TABLE,
    source_view.Role.EMPTY: Role.BLANK, source_view.Role.THEMATIC: Role.THEMATIC,
    source_view.Role.SIGNATURE: Role.SIGNATURE, source_view.Role.EPIGRAPH: Role.EPIGRAPH,
    source_view.Role.BLOCKQUOTE: Role.BLOCKQUOTE, source_view.Role.IMAGE: Role.IMAGE,
    source_view.Role.CONTEXT: Role.CONTEXT, source_view.Role.OTHER: Role.OTHER,
}

_WORD = re.compile(r"\w+", re.UNICODE)


def _align(raw: str) -> str:
    return {"both": "just", "": "left", "left": "left",
            "center": "center", "right": "right"}.get(raw, raw or "left")


def _twips(d: dict[str, str], k: str) -> int:
    try:
        return int(d.get(k, "") or 0)
    except ValueError:
        return 0


def _end_punct(text: str) -> EndPunct:
    """REAL final punctuation after stripping trailing closers. Language-agnostic
    (punctuation only)."""
    closers = "»\"”’')]"
    s = text.rstrip()
    while s and s[-1] in closers:
        s = s[:-1].rstrip()
    if not s:
        return EndPunct.NONE
    c = s[-1]
    if c in ".!?…":
        return EndPunct.SENTENCE
    if c == ":":
        return EndPunct.COLON
    if c in ",;":
        return EndPunct.COMMA
    if c in "—–-":
        return EndPunct.DASH
    return EndPunct.NONE


def _starts_lower(text: str) -> bool:
    lead = " \t«»\"'`—–-*„“”·•"
    for ch in text.lstrip(lead):
        if ch.isalpha():
            return ch.islower()
    return False


# ---------------------------------------------------------------------------
# the producer
# ---------------------------------------------------------------------------

# Building a book's view is expensive (the largest corpus book is ~40k paragraphs and the
# adapt+normalize pipeline runs per docx). We cache by the SAME provenance rail the artifact
# loader trusts — the docx CONTENT hash — NOT path+mtime, which are not durable truth (a
# docx can change with mtime preserved). Keying on content means the cache can never serve
# records from a different docx than the caller asks about.
_CACHE: dict[tuple[str, str, str], list[LineRecord]] = {}


def read_lines(docx: Path, lang: str, book_id: BookId) -> list[LineRecord]:
    """All LineRecords for one (book, lang), cached by docx CONTENT hash (never path/mtime)."""
    key = (identity.docx_package_hash(docx), lang, book_id)
    cached = _CACHE.get(key)
    if cached is None:
        cached = _read_lines(docx, lang, book_id)
        _CACHE[key] = cached
    return cached


@dataclass(frozen=True, slots=True)
class _Slot:
    """One position in the flat document sequence: a body LINE (line is set) or a STRUCTURAL
    slot (heading/blank/***/image — line is None). `is_body_line` is the single predicate the
    run-segmentation and boundary logic read, so the body-vs-structure distinction has one
    definition instead of `role==BODY and line is not None` repeated everywhere."""

    para: source_view.Para
    sub: int
    line: source_view.Line | None

    @property
    def has_line(self) -> bool:
        return self.line is not None

    @property
    def is_body_line(self) -> bool:
        """A votable-eligible body line — the unit runs/boundaries are built over. A heading
        or table line has_line but is NOT a body line, so it bounds a run."""
        return self.line is not None and self.para.role == source_view.Role.BODY


def _slots(paras: list[source_view.Para]) -> list[_Slot]:
    out: list[_Slot] = []
    for p in paras:
        if p.lines:
            out.extend(_Slot(p, li, ln) for li, ln in enumerate(p.lines))
        else:
            out.append(_Slot(p, 0, None))  # a blank/structural para: one structural slot
    return out


def _read_lines(docx: Path, lang: str, book_id: BookId) -> list[LineRecord]:
    """All LineRecords for one (book, lang). Features computed once per line.

    Physics is read off the source line, NEVER recomputed on joined paragraph text. Layout
    is joined from ParaRow by src_ordinal. Within-book norms are computed per book."""
    paras = source_view.read_view(docx)
    rows = {r.index: r for r in di.read_rows(docx)}

    # within-book references (on BODY lines only).
    body_paras = [p for p in paras if p.role == source_view.Role.BODY and p.src_start is not None]
    aligns = [_align(rows[p.src_start].align) for p in body_paras if p.src_start in rows]
    default_align = max(set(aligns), key=aligns.count) if aligns else "left"
    body_fills = sorted(ln.fill for p in body_paras for ln in p.lines) or [0.0]
    sp_after = [_twips(rows[p.src_start].spacing, "after")
                for p in body_paras if p.src_start in rows]
    med_sp_after = median(sp_after) if sp_after else 0
    n_indent_books = sum(1 for p in body_paras if p.src_start in rows and (
        _twips(rows[p.src_start].indent, "firstLine") or _twips(rows[p.src_start].indent, "left")))
    book_indents = bool(body_paras) and n_indent_books > 0.5 * len(body_paras)

    def pctile(v: float) -> float:
        # body_fills is sorted, so the count of fills strictly < v is bisect_left — O(log n)
        # per line instead of O(n), and provably the same integer rank.
        return round(bisect_left(body_fills, v) / len(body_fills), 3)

    # A flat document sequence of Slots. `is_body_line` is the ONE named predicate the
    # run/boundary logic consults, so "body line vs structural break" is never re-spelled.
    flat = _slots(paras)
    n = len(flat)

    # run segmentation: a maximal span of consecutive body-line slots, bounded by any
    # structural slot (a blank para is structural — it ends a run).
    run_of: dict[int, tuple[int, int]] = {}
    i = 0
    while i < n:
        if flat[i].is_body_line:
            j = i
            while j < n and flat[j].is_body_line:
                j += 1
            run_len = j - i
            for pos, k in enumerate(range(i, j)):
                run_of[k] = (run_len, pos)
            i = j
        else:
            i += 1

    out: list[LineRecord] = []
    for k, slot in enumerate(flat):
        if not slot.has_line:
            continue  # blank paras carry no line record (structural-only slot)
        p, li, ln = slot.para, slot.sub, slot.line
        assert ln is not None  # has_line guarantees it; narrows the type
        role = _ROLE.get(p.role, Role.OTHER)
        src_ord = p.src_start  # int | None — None ONLY for an unmapped (span-dropped) para
        if src_ord is None:
            fate = SourceFate.UNMAPPED
            votable = False                      # no provenance → held, never silently body
        elif p.needs_review:
            fate = SourceFate.MIXED
            votable = role == Role.BODY          # mixed body stays votable but flagged
        else:
            fate = SourceFate.NORMAL
            votable = role == Role.BODY

        # boundary: previous/next STRUCTURAL neighbour and the next CONTENT line.
        prev_structural = k == 0 or not flat[k - 1].is_body_line
        next_structural = k + 1 >= n or not flat[k + 1].is_body_line
        nxt_line = flat[k + 1].line if (k + 1 < n and flat[k + 1].is_body_line) else None
        next_lc = bool(nxt_line and _starts_lower(nxt_line.text))
        ep = _end_punct(ln.text)

        # layout joined from ParaRow by src_ordinal. Fallback to the para's own align when
        # no row (unmapped) — never invent layout.
        row = rows.get(src_ord) if src_ord is not None else None
        if row is not None:
            align = _align(row.align)
            ind_fl, ind_l = _twips(row.indent, "firstLine"), _twips(row.indent, "left")
            has_indent = bool(ind_fl or ind_l)
            sp_a = _twips(row.spacing, "after")
            numbered = row.numbered
        else:
            align = _align(p.align)
            has_indent = p.indented
            sp_a = 0
            numbered = False

        indent_vs = (IndentVsBook.DEFAULT if has_indent == book_indents
                     else (IndentVsBook.PRESENT if has_indent else IndentVsBook.ABSENT))
        if not med_sp_after:
            sp_vs = SpacingVsBook.MORE if sp_a else SpacingVsBook.TYPICAL
        elif sp_a > med_sp_after * 1.5:
            sp_vs = SpacingVsBook.MORE
        elif sp_a < med_sp_after * 0.5:
            sp_vs = SpacingVsBook.LESS
        else:
            sp_vs = SpacingVsBook.TYPICAL

        run_len, run_pos = run_of.get(k, (1, 0))

        feats = LineFeatures(
            fill=ln.fill, wraps=ln.wraps, char_len=len(ln.text),
            word_count=len(_WORD.findall(ln.text)),
            end_punct=ep, starts_lower=_starts_lower(ln.text), next_line_lower=next_lc,
            enjambs=(next_lc and ep in (EndPunct.NONE, EndPunct.COMMA, EndPunct.DASH)),
            colon_opens=(next_lc and ep == EndPunct.COLON),
            align=align, indent_vs_book=indent_vs, spacing_after_vs_book=sp_vs,
            align_is_book_default=(align == default_align), numbered=numbered,
            sub=li, n_subs=len(p.lines), run_len=run_len, run_pos=run_pos,
            prev_structural=prev_structural, next_structural=next_structural,
            fill_pctile_in_book=pctile(ln.fill),
        )

        emphasis = "strong" if ln.bold else ("emph" if ln.italic else "")
        lid = (identity.LineId.mapped(lang, book_id, src_ord, li) if src_ord is not None
               else identity.LineId.unmapped(lang, book_id, k, li))
        out.append(LineRecord(
            id=lid, text=ln.text, inlines=(InlineRun(ln.text, emphasis),),
            role=role, votable=votable, source_fate=fate, features=feats,
            paragraph_text_hash=identity.text_hash(p.text),
            line_text_hash=identity.text_hash(ln.text),
            meta=LineMeta(style_id=row.style if row else "", block_index=p.index,
                          src_ordinal=src_ord),
        ))
    return out


# ---------------------------------------------------------------------------
# views over records — NEITHER recomputes features
# ---------------------------------------------------------------------------


def to_vector(features: LineFeatures) -> FeatureVector:
    """Flatten the features to a numeric feature map (the student/serve input). Categorical enums are
    one-hot expanded; bools→0/1. Reads `features` only — no docx, no recompute. The KEYS are
    derived from the dataclass so the vector can never silently drift from the schema."""
    out: FeatureVector = {}
    d = features.to_dict()
    for name in records.feature_field_names():
        v = d[name]
        if isinstance(v, bool):
            out[name] = float(v)
        elif isinstance(v, (int, float)):
            out[name] = float(v)
        else:  # categorical string -> one-hot
            out[f"{name}={v}"] = 1.0
    return out


# the categorical vocab (so a zero-support category still yields a column even if unseen)
_CAT_VOCAB: dict[FeatureName, list[str]] = {
    "end_punct": [e.value for e in EndPunct],
    "align": ["left", "just", "right", "center"],
    "indent_vs_book": [e.value for e in IndentVsBook],
    "spacing_after_vs_book": [e.value for e in SpacingVsBook],
}


@lru_cache(maxsize=1)
def vector_columns() -> tuple[FeatureName, ...]:
    """The full, fixed column space of `to_vector` — every numeric field plus every
    categorical level (including zero-support ones). Stable across books so a model matrix is
    well-defined and zero-support columns stay visible. A pure function of the schema +
    categorical vocab, so it is computed once and cached; `vectorize_fixed` consults it per
    line."""
    cols: list[FeatureName] = []
    d_default = _DEFAULT_FEATURES.to_dict()
    for name in records.feature_field_names():
        v = d_default[name]
        if isinstance(v, (int, float)):  # bool ⊂ int — one numeric column
            cols.append(name)
        else:
            cols.extend(f"{name}={lvl}" for lvl in _CAT_VOCAB[name])
    return tuple(cols)


def vectorize_fixed(features: LineFeatures) -> FeatureVector:
    """`to_vector` projected onto the fixed `vector_columns()` space (missing categorical
    levels = 0.0). This is what the student matrix uses."""
    sparse = to_vector(features)
    return {c: sparse.get(c, 0.0) for c in vector_columns()}


def render_listing(records_in: list[LineRecord], *, keys: Mapping[LineId, ListingKey],
                   with_features: bool) -> str:
    """The ONE listing builder (replaces the three hand-synced ones). Each body line shows its
    caller-chosen `ListingKey` from `keys`; a body line absent from `keys` is context (shown for
    orientation, no vote key). Structural roles are separators. With `with_features`, the feature
    columns are formatted from the SAME `record.features` the vector reads — so the teacher's
    evidence and the student's vector are provably one feature set. The caller owns the key scheme
    (teacher mints task-local `L001`; debug uses `src_ordinal_keys`), so a source ordinal never
    reaches a reader/UI payload through here."""
    lines: list[str] = []
    for r in records_in:
        if r.role != Role.BODY:
            lines.append(f"  ---- [{r.role.value}]" + (f" {r.text[:60]}" if r.text else ""))
            continue
        key = keys.get(r.id, "·")            # absent ⇒ a context body line, no vote key
        flag = "" if r.votable else "  (not voted)"
        if with_features:
            lines.append(f"  {key}  [{_feature_tokens(r.features)}] {r.text}{flag}")
        else:
            w = "WRAPS " if r.features.wraps else "nowrap"
            lines.append(f"  {key}  {w} | {r.text}{flag}")
    return "\n".join(lines)


def src_ordinal_keys(records_in: list[LineRecord]) -> dict[LineId, ListingKey]:
    """`src_ordinal.sub` listing keys for human-readable DEBUG dumps ONLY. A source ordinal must
    never reach a reader/UI payload, so the teacher mints task-local keys instead — this is the
    explicit, debug-only opt-in to ordinal keying."""
    return {r.id: f"{r.id.src_ordinal}.{r.id.sub}" for r in records_in}


def _feature_tokens(f: LineFeatures) -> str:
    """Human-readable feature tokens for the listing — DERIVED FROM `f`, the same dataclass the
    vector flattens. Default layout is silent; the values are identical to `to_vector`'s."""
    parts = [f"fill={f.fill:.2f}", "WRAP" if f.wraps else "nowr",
             f"fill_pctile_in_book={f.fill_pctile_in_book:.2f}"]
    if f.align != "left":
        parts.append(f"align={f.align}")
    if not f.align_is_book_default:
        parts.append("align-unusual-for-book")
    if f.indent_vs_book is not IndentVsBook.DEFAULT:
        parts.append(f"indent=unusual-{f.indent_vs_book.value}")
    if f.spacing_after_vs_book is not SpacingVsBook.TYPICAL:
        parts.append(f"spacing-after={f.spacing_after_vs_book.value}")
    if f.numbered:
        parts.append("list-item")
    parts.append(f"end={f.end_punct.value}")
    if f.next_line_lower:
        parts.append("next-line-lowercase")
    return " ".join(parts)


_DEFAULT_FEATURES = LineFeatures(
    fill=0.0, wraps=False, char_len=0, word_count=0, end_punct=EndPunct.NONE,
    starts_lower=False, next_line_lower=False, enjambs=False, colon_opens=False,
    align="left", indent_vs_book=IndentVsBook.DEFAULT,
    spacing_after_vs_book=SpacingVsBook.TYPICAL, align_is_book_default=True,
    numbered=False, sub=0, n_subs=1, run_len=1, run_pos=0, prev_structural=False,
    next_structural=False, fill_pctile_in_book=0.5,
)
