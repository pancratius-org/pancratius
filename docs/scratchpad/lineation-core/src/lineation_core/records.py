# research-pure: the canonical per-line record (features + structure). Pure data, no I/O.
"""The canonical record and its feature schema.

`LineRecord` is the one artifact every consumer reads: teacher annotation, the
distilled student, and serve-time all vectorize the SAME `LineFeatures`. `meta` is
provenance/debug and is explicitly NOT a feature source. The field set of `LineFeatures`
IS the feature schema — `feature_field_names()` derives the schema from the dataclass so
the two can never drift.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Self

from .identity import BookId, JsonObject, LineId, LineTextHash, ParagraphTextHash

# A flattened one-line feature vector: column name → numeric value (bools→0/1, categoricals
# one-hot expanded). `FeatureName` is one such column. Produced by `producer.vectorize_fixed`,
# consumed by the student matrix.
type FeatureName = str
type FeatureVector = dict[FeatureName, float]

# Records keyed by book — the whole-corpus map domain functions take as data (loaded once at the
# shell by `store.load_records_many`) instead of reaching for each book themselves.
type RecordsByBook = dict[BookId, list["LineRecord"]]

# A run = the indices of one maximal BODY block (an authorial unit). `runs()` is the foundation
# grouping the sequence model AND the teacher tiler both read, so "run" means one thing.
type RecordIndex = int           # a 0-based position into a records sequence — NOT a src_ordinal
type Run = list[RecordIndex]


class Align(StrEnum):
    """A line's paragraph alignment, normalized to a closed vocabulary (the producer's `_align`
    folds Word's `both`/`""` into `just`/`left`). One-hot expanded in the vector; `LEFT` is the
    silent default in the listing."""
    LEFT = "left"
    JUST = "just"
    RIGHT = "right"
    CENTER = "center"


class IndentVsBook(StrEnum):
    DEFAULT = "default"   # indentation matches what this book usually does
    PRESENT = "present"   # indented where the book usually is not
    ABSENT = "absent"     # un-indented where the book usually indents


class SpacingVsBook(StrEnum):
    TYPICAL = "typical"
    MORE = "more"
    LESS = "less"


class EndPunct(StrEnum):
    SENTENCE = "sentence"
    COLON = "colon"
    COMMA = "comma"
    DASH = "dash"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class LineFeatures:
    """The features. The ONLY thing a model (teacher prompt OR student vector) reads. Every field
    is source-derived and computable at serve time — NO label, NO prediction, NO raw
    book/style id. The field set IS the feature schema; the producer's `to_vector`
    flattens it."""

    # physics / text-length (first-class) — read PER SOURCE LINE, never on joined paragraph
    # text (a line's fill/wraps describe that one line, not its whole paragraph).
    fill: float
    wraps: bool
    char_len: int
    word_count: int
    # boundary (source-only, language-agnostic)
    end_punct: EndPunct
    starts_lower: bool
    next_line_lower: bool
    enjambs: bool
    colon_opens: bool
    # layout (within-book DIRECTIONED)
    align: Align
    indent_vs_book: IndentVsBook
    spacing_after_vs_book: SpacingVsBook
    align_is_book_default: bool
    numbered: bool
    sub: int
    n_subs: int                      # explicit-<w:br> segment count of the owning paragraph
    # context (SOURCE-ONLY)
    run_len: int
    run_pos: int
    prev_structural: bool
    next_structural: bool
    fill_pctile_in_book: float

    def to_dict(self) -> JsonObject:
        out: JsonObject = {}
        for f in dataclasses.fields(self):
            v = getattr(self, f.name)
            out[f.name] = v.value if isinstance(v, StrEnum) else v
        return out


class Role(StrEnum):
    BODY = "body"
    HEADING = "heading"
    LIST = "list"
    TABLE = "table"
    BLANK = "blank"
    THEMATIC = "thematic"
    SIGNATURE = "signature"
    EPIGRAPH = "epigraph"
    BLOCKQUOTE = "blockquote"
    IMAGE = "image"
    CONTEXT = "context"      # a <w:p> the normalize classification calls non-body structure
    OTHER = "other"


class SourceFate(StrEnum):
    NORMAL = "normal"        # mapped 1:1 to a source ordinal, confident body
    UNMAPPED = "unmapped"    # no SourceSpan (§14-P1 span-drop) — held (non-votable), flagged
    MIXED = "mixed"          # the <w:p> split into structure + body fragments — votable, flagged


@dataclass(frozen=True, slots=True)
class InlineRun:
    text: str
    emphasis: str  # "" | strong | emph | strike | code

    def to_dict(self) -> JsonObject:
        return {"text": self.text, "emphasis": self.emphasis}


@dataclass(frozen=True, slots=True)
class LineMeta:
    """Provenance & debug for a line — explicitly NOT a feature source (a model never sees
    it). Typed (not a `dict[str, object]`) so consumers read `meta.block_index`, not a
    stringly-typed lookup. `src_ordinal` is None ONLY for an unmapped line — the one place
    the optional is honest, and it is paired with `id.is_mapped`."""

    style_id: str
    block_index: int        # the structural-view block index this line came from (label idx space)
    src_ordinal: int | None  # the real source <w:p> ordinal, or None if unmapped

    def to_dict(self) -> JsonObject:
        return {"style_id": self.style_id, "block_index": self.block_index,
                "src_ordinal": self.src_ordinal}

    @classmethod
    def from_dict(cls, d: Mapping[str, object]) -> Self:
        return cls(style_id=d.get("style_id", ""), block_index=int(d["block_index"]),
                   src_ordinal=d["src_ordinal"])


@dataclass(frozen=True, slots=True)
class LineRecord:
    """The canonical per-source-line artifact. `features` is the feature set; `meta` is NOT a feature
    source. Carries its own validation hashes so a single record can be checked in
    isolation."""

    id: LineId
    text: str
    inlines: tuple[InlineRun, ...]
    role: Role
    votable: bool
    source_fate: SourceFate
    features: LineFeatures
    paragraph_text_hash: ParagraphTextHash
    line_text_hash: LineTextHash
    meta: LineMeta

    def to_dict(self) -> JsonObject:
        return {
            "id": self.id.as_key(),
            "text": self.text,
            "inlines": [r.to_dict() for r in self.inlines],
            "role": self.role.value,
            "votable": self.votable,
            "source_fate": self.source_fate.value,
            "features": self.features.to_dict(),
            "paragraph_text_hash": self.paragraph_text_hash,
            "line_text_hash": self.line_text_hash,
            "meta": self.meta.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, object]) -> Self:
        f = d["features"]
        feats = LineFeatures(
            fill=f["fill"], wraps=f["wraps"], char_len=f["char_len"],
            word_count=f["word_count"], end_punct=EndPunct(f["end_punct"]),
            starts_lower=f["starts_lower"], next_line_lower=f["next_line_lower"],
            enjambs=f["enjambs"], colon_opens=f["colon_opens"], align=Align(f["align"]),
            indent_vs_book=IndentVsBook(f["indent_vs_book"]),
            spacing_after_vs_book=SpacingVsBook(f["spacing_after_vs_book"]),
            align_is_book_default=f["align_is_book_default"], numbered=f["numbered"],
            sub=f["sub"], n_subs=f["n_subs"], run_len=f["run_len"], run_pos=f["run_pos"],
            prev_structural=f["prev_structural"], next_structural=f["next_structural"],
            fill_pctile_in_book=f["fill_pctile_in_book"],
        )
        return cls(
            id=LineId.from_key(d["id"]), text=d["text"],
            inlines=tuple(InlineRun(r["text"], r["emphasis"]) for r in d["inlines"]),
            role=Role(d["role"]), votable=d["votable"],
            source_fate=SourceFate(d["source_fate"]), features=feats,
            paragraph_text_hash=d["paragraph_text_hash"],
            line_text_hash=d["line_text_hash"], meta=LineMeta.from_dict(d["meta"]),
        )


# ---------------------------------------------------------------------------
# feature schema + the zero-support rail
# ---------------------------------------------------------------------------


def feature_field_names() -> list[FeatureName]:
    """The feature field order — the schema. Derived from the dataclass so it can never drift
    from `to_vector`."""
    return [f.name for f in dataclasses.fields(LineFeatures)]


@dataclass(frozen=True)
class FeatureSchema:
    feature_schema_version: str
    producer_version: str
    fields: list[FeatureName]
    feature_support: dict[FeatureName, int]  # column -> count of rows where it is non-default/observed

    def to_dict(self) -> JsonObject:
        return {
            "feature_schema_version": self.feature_schema_version,
            "producer_version": self.producer_version,
            "fields": self.fields,
            "feature_support": self.feature_support,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, object]) -> Self:
        return cls(
            feature_schema_version=d["feature_schema_version"],
            producer_version=d["producer_version"], fields=list(d["fields"]),
            feature_support=dict(d["feature_support"]),
        )

    def zero_support(self) -> list[FeatureName]:
        """Fields that NEVER varied in the corpus — they must remain VISIBLE in analysis
        (the speaker-label=0 lesson), never silently dropped."""
        return [k for k, v in self.feature_support.items() if v == 0]


def runs(records: Sequence[LineRecord]) -> list[Run]:
    """Indices grouped into runs: maximal spans of consecutive BODY lines (`role == BODY`),
    bounded by any structural record — the block level of the hierarchy, and the SAME predicate
    the producer's `run_len`/`run_pos` features use, so the two notions of "run" agree. An
    interior unmapped body line (`role == BODY` but `votable == False`) CONTINUES its stanza
    rather than splitting it; `smooth_runs` averages over the votable members only, and the teacher
    tiler keeps a whole run together as one authorial unit."""
    out: list[Run] = []
    cur: Run = []
    for i, r in enumerate(records):
        if r.role is Role.BODY:
            cur.append(i)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out
