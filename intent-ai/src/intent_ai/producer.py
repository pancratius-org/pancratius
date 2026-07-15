# research-pure: reads src/content DOCX read-only via the production IR; scratch only.
"""The one feature producer and thin views over its records.

`read_lines` hydrates one canonical source document. Natural lines, paragraph
identity, layout, and roles all project from that aggregate; only font physics
is added here. Every feature is computed exactly once.

The views — `to_vector(features)` and `render_listing(records)` — read `record.features` and
recompute NOTHING. There is structurally no second feature path: the student vector and the teacher
listing are two renderings of the SAME dataclass, so a feature can never live in one but not
the other (the parity test proves it by perturbation).
"""
from __future__ import annotations

import re
from bisect import bisect_left
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from statistics import median

from pancratius import docx_source, docx_structure
from pancratius.locales import Locale

from . import identity, physics, records, source_view
from .identity import BookId, LineId, ListingKey
from .records import (
    Align,
    EndPunct,
    FeatureName,
    FeatureVector,
    IndentVsBook,
    LineFeatures,
    LineRecord,
    SpacingVsBook,
)

_WORD = re.compile(r"\w+", re.UNICODE)


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
_CACHE: dict[tuple[str, str, str], tuple[LineRecord, ...]] = {}


def read_lines(docx: Path, lang: Locale, book_id: BookId) -> list[LineRecord]:
    """All LineRecords for one (book, lang), cached by docx CONTENT hash (never path/mtime)."""
    key = (identity.docx_package_hash(docx), lang, book_id)
    cached = _CACHE.get(key)
    if cached is None:
        cached = tuple(_read_lines(docx, lang, book_id))
        _CACHE[key] = cached
    return list(cached)


@dataclass(frozen=True, slots=True)
class _LineSlot:
    para: source_view.Para
    sub: int
    line: source_view.Line

    @property
    def is_body_line(self) -> bool:
        return self.para.role.is_body


@dataclass(frozen=True, slots=True)
class _StructuralSlot:
    """A paragraph or package segment boundary with no line record."""

    @property
    def is_body_line(self) -> bool:
        return False


type _Slot = _LineSlot | _StructuralSlot


def _slots(paras: list[source_view.Para]) -> list[_Slot]:
    out: list[_Slot] = []
    previous_segment: docx_source.SourceSegment | None = None
    for p in paras:
        segment = p.source.segment
        if previous_segment is not None and segment != previous_segment:
            out.append(_StructuralSlot())
        if p.lines:
            out.extend(_LineSlot(p, sub, line) for sub, line in enumerate(p.lines))
        else:
            out.append(_StructuralSlot())
        previous_segment = segment
    return out


def _read_lines(docx: Path, lang: Locale, book_id: BookId) -> list[LineRecord]:
    """All LineRecords for one (book, lang). Features computed once per line.

    Physics is read off the canonical source line, never recomputed on joined
    paragraph text. Within-book norms are computed per book."""
    source = docx_source.read(docx)
    observation = docx_structure.observe_structure(source, lang=lang)
    paras = list(source_view.read_view(
        source,
        observation,
        physics.page_geom(source.layout),
    ))

    # within-book references (on BODY lines only).
    body_paras = [p for p in paras if p.role.is_body]
    aligns = [p.source.layout.alignment for p in body_paras]
    default_align = Counter(aligns).most_common(1)[0][0] if aligns else Align.LEFT
    body_fills = sorted(ln.fill for p in body_paras for ln in p.lines) or [0.0]
    sp_after = [p.source.layout.spacing_after.value for p in body_paras]
    med_sp_after = median(sp_after) if sp_after else 0
    n_indent_books = sum(
        1
        for p in body_paras
        if p.source.layout.first_line_indent.value or p.source.layout.left_indent.value
    )
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
        if isinstance(slot, _StructuralSlot):
            continue
        p, li, ln = slot.para, slot.sub, slot.line
        role = p.role
        src_ord = int(p.source.ordinal)
        # The next content line is a lexical feature; run boundaries derive from run_pos/run_len.
        next_slot = flat[k + 1] if k + 1 < n else None
        nxt_line = (
            next_slot.line
            if isinstance(next_slot, _LineSlot) and next_slot.is_body_line
            else None
        )
        next_lc = bool(nxt_line and _starts_lower(nxt_line.text))
        ep = _end_punct(ln.text)

        align = p.source.layout.alignment
        ind_fl = p.source.layout.first_line_indent.value
        ind_l = p.source.layout.left_indent.value
        has_indent = bool(ind_fl or ind_l)
        sp_a = p.source.layout.spacing_after.value

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
            align_is_book_default=(align == default_align),
            sub=li, n_subs=len(p.lines), run_len=run_len, run_pos=run_pos,
            fill_pctile_in_book=pctile(ln.fill),
        )

        lid = identity.LineId.mapped(lang, book_id, src_ord, li)
        out.append(LineRecord(
            id=lid, text=ln.text, role=role, features=feats,
            line_text_hash=identity.text_hash(ln.text),
        ))
    return out


# ---------------------------------------------------------------------------
# views over records — NEITHER recomputes features
# ---------------------------------------------------------------------------


def to_vector(features: LineFeatures) -> FeatureVector:
    """Flatten the features to a numeric feature map (the student/serve input). Categorical enums are
    one-hot expanded; bools→0/1. Reads `features` only — no docx, no recompute. The KEYS are
    derived from the typed feature schema so the vector cannot silently drift from it."""
    out: FeatureVector = {}
    for name in records.feature_field_names():
        v = records.feature_value(features, name)
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
    "align": [e.value for e in Align],
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
    for name in records.feature_field_names():
        v = records.feature_value(_DEFAULT_FEATURES, name)
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
    """The ONE listing builder. Each body line shows its
    caller-chosen `ListingKey` from `keys`; a body line absent from `keys` is context (shown for
    orientation, no vote key). Structural roles are separators. With `with_features`, the feature
    columns are formatted from the SAME `record.features` the vector reads — so the teacher's
    evidence and the student's vector are provably one feature set. The caller owns the key scheme
    (teacher mints task-local `L001`; debug uses `src_ordinal_keys`), so a source ordinal never
    reaches a reader/UI payload through here."""
    lines: list[str] = []
    for r in records_in:
        if not r.votable:
            lines.append(f"  ---- [{r.role.value}]" + (f" {r.text[:60]}" if r.text else ""))
            continue
        key = keys.get(r.id, "·")            # absent ⇒ a context body line, no vote key
        flag = "  (review)" if r.requires_review else ""
        if with_features:
            lines.append(f"  {key}  [{_feature_tokens(r.features)}] {r.text}{flag}")
        else:
            w = "WRAPS " if r.features.wraps else "nowrap"
            lines.append(f"  {key}  {w} | {r.text}{flag}")
    return "\n".join(lines)


def _feature_tokens(f: LineFeatures) -> str:
    """Human-readable feature tokens for the listing — DERIVED FROM `f`, the same dataclass the
    vector flattens. Default layout is silent; the values are identical to `to_vector`'s."""
    parts = [f"fill={f.fill:.2f}", "WRAP" if f.wraps else "nowr",
             f"fill_pctile_in_book={f.fill_pctile_in_book:.2f}"]
    if f.align is not Align.LEFT:
        parts.append(f"align={f.align.value}")
    if not f.align_is_book_default:
        parts.append("align-unusual-for-book")
    if f.indent_vs_book is not IndentVsBook.DEFAULT:
        parts.append(f"indent=unusual-{f.indent_vs_book.value}")
    if f.spacing_after_vs_book is not SpacingVsBook.TYPICAL:
        parts.append(f"spacing-after={f.spacing_after_vs_book.value}")
    parts.append(f"end={f.end_punct.value}")
    if f.next_line_lower:
        parts.append("next-line-lowercase")
    return " ".join(parts)


_DEFAULT_FEATURES = LineFeatures(
    fill=0.0, wraps=False, char_len=0, word_count=0, end_punct=EndPunct.NONE,
    starts_lower=False, next_line_lower=False, enjambs=False, colon_opens=False,
    align=Align.LEFT, indent_vs_book=IndentVsBook.DEFAULT,
    spacing_after_vs_book=SpacingVsBook.TYPICAL, align_is_book_default=True,
    sub=0, n_subs=1, run_len=1, run_pos=0, fill_pctile_in_book=0.5,
)
