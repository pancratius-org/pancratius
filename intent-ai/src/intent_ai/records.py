# research-pure: the canonical per-line record (features + structure). Pure data, no I/O.
"""The canonical record and its feature schema.

`LineRecord` is the one artifact every consumer reads: teacher annotation, the
distilled student, and serve-time all vectorize the SAME `LineFeatures`. `meta` is
provenance/debug and is explicitly NOT a feature source. Stored facts come from the
`LineFeatures` dataclass; model-only coordinates are typed projections of those facts.
"""
from __future__ import annotations

import dataclasses
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Self, assert_never, cast

from pancratius.docx_source import TextAlignment as Align

from .identity import (
    BookId,
    BookKey,
    JsonObject,
    LineId,
    LineTextHash,
    strict_bool,
    text_hash,
)

# A flattened one-line feature vector: column name → numeric value (bools→0/1, categoricals
# one-hot expanded). `FeatureName` is one such column. Produced by `producer.vectorize_fixed`,
# consumed by the student matrix.
type FeatureName = str
type FeatureVector = dict[FeatureName, float]

# Records keyed by book edition — the CROSS-LANGUAGE whole-corpus map domain functions take as
# data (loaded once at the shell by `store.load_records_many`) instead of reaching for each book
# themselves. Keyed by `BookKey`, never bare `BookId` (ru:01 and en:01 are different books).
type RecordsByBook = dict[BookKey, list["LineRecord"]]

# Records for ONE language's books — a recipe's scope (`recipe.lang` fixes the language, so the
# bare folder number is unambiguous there). Distinct from `RecordsByBook` so the cross-language
# boundary stays visible in signatures.
type BookRecords = dict[BookId, list["LineRecord"]]

# A run = the indices of one maximal BODY block (an authorial unit). `runs()` is the foundation
# grouping the sequence model AND the teacher tiler both read, so "run" means one thing.
type RecordIndex = int           # a 0-based position into a records sequence — NOT a src_ordinal
type Run = list[RecordIndex]


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
    sub: int
    n_subs: int                      # explicit-<w:br> segment count of the owning paragraph
    # context (SOURCE-ONLY)
    run_len: int
    run_pos: int
    fill_pctile_in_book: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.fill) or self.fill < 0:
            raise ValueError("line fill must be finite and non-negative")
        if self.char_len < 0 or self.word_count < 0:
            raise ValueError("line lengths must be non-negative")
        if self.n_subs <= 0 or not 0 <= self.sub < self.n_subs:
            raise ValueError("sub must address one segment of its paragraph")
        if self.run_len <= 0 or not 0 <= self.run_pos < self.run_len:
            raise ValueError("run_pos must address one line of its run")
        if not math.isfinite(self.fill_pctile_in_book) or not 0 <= self.fill_pctile_in_book <= 1:
            raise ValueError("fill percentile must be finite and between zero and one")

    @property
    def prev_structural(self) -> bool:
        return self.run_pos == 0

    @property
    def next_structural(self) -> bool:
        return self.run_pos == self.run_len - 1

    def to_dict(self) -> JsonObject:
        out: JsonObject = {}
        for f in dataclasses.fields(self):
            v = getattr(self, f.name)
            out[f.name] = v.value if isinstance(v, StrEnum) else v
        return out


class DerivedFeature(StrEnum):
    """Model signal derived from canonical coordinates, never serialized as parallel state."""

    PREV_STRUCTURAL = "prev_structural"
    NEXT_STRUCTURAL = "next_structural"

    def project(self, features: LineFeatures) -> bool:
        match self:
            case DerivedFeature.PREV_STRUCTURAL:
                return features.prev_structural
            case DerivedFeature.NEXT_STRUCTURAL:
                return features.next_structural
            case unsupported:
                assert_never(unsupported)


_DERIVED_FEATURES_BY_NAME = {feature.value: feature for feature in DerivedFeature}


class Role(StrEnum):
    BODY = "body"
    BODY_REVIEW = "body_review"
    HEADING = "heading"
    LIST = "list"
    THEMATIC = "thematic"
    CONTEXT = "context"      # a <w:p> the normalize classification calls non-body structure

    @property
    def is_body(self) -> bool:
        return self in {Role.BODY, Role.BODY_REVIEW}

    @property
    def requires_review(self) -> bool:
        return self is Role.BODY_REVIEW


@dataclass(frozen=True, slots=True)
class LineRecord:
    """The canonical per-source-line artifact. `features` is the feature set; `meta` is NOT a feature
    source. Carries its own validation hashes so a single record can be checked in
    isolation."""

    id: LineId
    text: str
    role: Role
    features: LineFeatures
    line_text_hash: LineTextHash

    def __post_init__(self) -> None:
        if self.id.sub != self.features.sub:
            raise ValueError(f"record {self.id} disagrees with its feature sub-index")
        if text_hash(self.text) != self.line_text_hash:
            raise ValueError(f"record {self.id} has a stale line-text hash")

    @property
    def votable(self) -> bool:
        return self.role.is_body

    @property
    def requires_review(self) -> bool:
        return self.role.requires_review

    def to_dict(self) -> JsonObject:
        return {
            "id": self.id.as_key(),
            "text": self.text,
            "role": self.role.value,
            "features": self.features.to_dict(),
            "line_text_hash": self.line_text_hash,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, object]) -> Self:
        f = cast(Mapping[str, object], d["features"])
        feats = LineFeatures(
            fill=float(str(f["fill"])), wraps=strict_bool(f["wraps"], field="features.wraps"),
            char_len=int(str(f["char_len"])), word_count=int(str(f["word_count"])),
            end_punct=EndPunct(str(f["end_punct"])),
            starts_lower=strict_bool(f["starts_lower"], field="features.starts_lower"),
            next_line_lower=strict_bool(
                f["next_line_lower"], field="features.next_line_lower"
            ),
            enjambs=strict_bool(f["enjambs"], field="features.enjambs"),
            colon_opens=strict_bool(f["colon_opens"], field="features.colon_opens"),
            align=Align(str(f["align"])),
            indent_vs_book=IndentVsBook(str(f["indent_vs_book"])),
            spacing_after_vs_book=SpacingVsBook(str(f["spacing_after_vs_book"])),
            align_is_book_default=strict_bool(
                f["align_is_book_default"], field="features.align_is_book_default"
            ),
            sub=int(str(f["sub"])), n_subs=int(str(f["n_subs"])),
            run_len=int(str(f["run_len"])), run_pos=int(str(f["run_pos"])),
            fill_pctile_in_book=float(str(f["fill_pctile_in_book"])),
        )
        return cls(
            id=LineId.from_key(cast(Iterable[object], d["id"])), text=str(d["text"]),
            role=Role(str(d["role"])), features=feats,
            line_text_hash=LineTextHash(str(d["line_text_hash"])),
        )


# ---------------------------------------------------------------------------
# feature schema + the zero-support rail
# ---------------------------------------------------------------------------


def feature_field_names() -> list[FeatureName]:
    """The model feature order: stored facts plus non-stored boundary projections."""
    stored = [f.name for f in dataclasses.fields(LineFeatures)]
    boundary = stored.index("fill_pctile_in_book")
    return [
        *stored[:boundary],
        *(feature.value for feature in DerivedFeature),
        *stored[boundary:],
    ]


def feature_value(features: LineFeatures, name: FeatureName) -> object:
    """Project one member of the closed model schema from canonical line facts."""
    derived = _DERIVED_FEATURES_BY_NAME.get(name)
    if derived is None:
        return getattr(features, name)
    return derived.project(features)


# Contract versions — bump when the producer or the feature set changes; stamped into the cache
# manifest and read back as a drift rail. Pure constants (no IO), so any module needing the version
# for provenance reads them here, not from the cache-IO module.
FEATURE_SCHEMA_VERSION = "features-5"
PRODUCER_VERSION = "read_lines-5"


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
        fields = cast(Iterable[object], d["fields"])
        support = cast(Mapping[str, object], d["feature_support"])
        return cls(
            feature_schema_version=str(d["feature_schema_version"]),
            producer_version=str(d["producer_version"]),
            fields=[str(field) for field in fields],
            feature_support={name: int(str(value)) for name, value in support.items()},
        )

    def zero_support(self) -> list[FeatureName]:
        """Fields that NEVER varied in the corpus — they must remain VISIBLE in analysis
        (the speaker-label=0 lesson), never silently dropped."""
        return [k for k, v in self.feature_support.items() if v == 0]


def runs(records: Sequence[LineRecord]) -> list[Run]:
    """Indices grouped into runs: maximal spans of consecutive BODY lines (`role == BODY`),
    bounded by any structural record — the block level of the hierarchy, and the SAME predicate
    the producer's `run_len`/`run_pos` features use, so the two notions of "run" agree. The teacher
    tiler keeps a whole run together as one authorial unit."""
    out: list[Run] = []
    cur: Run = []
    for i, r in enumerate(records):
        if not r.votable:
            if cur:
                out.append(cur)
                cur = []
            continue
        if r.features.prev_structural and cur:
            out.append(cur)
            cur = []
        cur.append(i)
        if r.features.next_structural:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out
