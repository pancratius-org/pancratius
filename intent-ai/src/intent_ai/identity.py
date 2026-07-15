# research-pure: pure-stdlib line identity + content hashes. No DOCX, no I/O.
"""Identity and content hashes — the join key and the safety rails.

Stdlib-only so its proofs run without building a single DOCX. Everything downstream
(the record, the producer, the student) keys off `LineId` and validates against these
hashes.

Identity (proven against the real corpus):
    LineId(lang, book_id, src_ordinal, sub)
      src_ordinal = the source <w:p> ordinal == ir.SourceSpan.start == ParaRow.index.
      (src_ordinal, sub) is UNIQUE per votable body line in every labeled book — 0
      collisions across 200k+ keys. So this 4-tuple is a real identity, not a guess.

Hashes are SAFETY RAILS, never silent: on a docx change the loader FAILS LOUD unless
the caller opts into an explicit migration. `src_ordinal` alone is never trusted.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, NewType, Self

from pancratius.locales import Locale, is_locale

# --- domain vocabulary (the greppable names every module shares) ------------------------------
# Plain `str` aliases, not Literal/NewType: labels come from JSON and `LineLabel.__post_init__`
# is the runtime enforcer of `prose | lineated`, so a Literal would only force casts at the
# boundary without adding a guarantee the runtime check does not already give.

type Label = Literal["prose", "lineated"]   # the two-class verdict for one line (validated)
type ReaderTag = str    # one panel reader: grok | deepseek | gemini | owl | mimo | minimax
type ModelId = str      # an OpenRouter model id behind a reader, e.g. "x-ai/grok-4"
type ListingKey = str   # the OUTWARD key shown for a line in a rendered listing — opaque to the
                        # renderer; the caller picks the scheme (teacher: task-local "L001"; debug:
                        # "src_ordinal.sub"). NOT a stable identity — that is `LineId`.

# Content hashes — the SAFETY RAILS. Three DISTINCT roles, named so a reader/agent never has to
# guess which `str` is which: each is a `_sha` hex prefix of a different scope (see the functions
# below). They are NOT interchangeable — a line-text hash equals a paragraph-text hash only for a
# one-line paragraph, by coincidence, never by contract — so they get distinct names even though
# the bytes are the same shape.
LineTextHash = NewType("LineTextHash", str)
DocxPackageHash = NewType("DocxPackageHash", str)
RequestFingerprint = NewType("RequestFingerprint", str)
                               # config + response contract) a reader was sent — the resume-cache key
                               # carries it so an edited prompt, a changed temp/max_tokens, OR a changed
                               # response schema re-calls instead of reusing a reply made under another

# Teacher-loop identifiers. A `TaskId` names a built task bundle (its manifest resolves the opaque
# keys); a `RunId` names a saved panel run's per-rep evidence. Distinct concepts that both spell a
# bare `str` otherwise — naming them keeps a task bundle and a panel run greppable apart.
type TaskId = str
type RunId = str

SOURCE_IDENTITY_VERSION = "source-v3"


class ExperimentId(str):
    """A study folder identity, never a path or history namespace."""

    def __new__(cls, value: object) -> ExperimentId:
        text = str(value)
        if not re.fullmatch(r"[a-z0-9]+(?:[a-z0-9-]*[a-z0-9])?", text):
            raise ValueError(f"experiment id must be a lowercase slug, got {text!r}")
        if text == "history":
            raise ValueError("experiment id uses the reserved history namespace")
        return str.__new__(cls, text)

# line → label maps. `LabelByLine` is the shared scoring surface: a truth map, a prediction map,
# the contested eval slice — all interchangeable as either side of a per-line join. `ReaderCalls`
# names the SAME shape in its distinct role of ONE reader's calls, so `PanelVotes` reads as what
# it is (reader → that reader's calls) rather than a bare double-nested dict.
type LabelByLine = dict[LineId, Label]
type ReaderCalls = LabelByLine
type PanelVotes = dict[ReaderTag, ReaderCalls]

# The serialized `LineId` — the heterogeneous 4-list `[lang, book_id, src_ordinal, sub]` that is
# the on-disk key shape. Named (not bare `list[Any]`) so the disk↔`LineId` boundary is explicit and
# every loader that reads a key reads the SAME documented tuple shape.
type LineKey = list[object]

# The raw JSON boundary. `JsonObject` is one decoded JSON object; `JsonRow` is the SAME shape in its
# role as ONE jsonl row at the `store` IO edge — deliberately open (`object` values, not `Any`), so
# a reader sees "untyped wire data, narrow it before use" and the typed interpretation lives in the
# owning model's `from_dict` (`annotations.py`, `records.py`, …), never leaks inward as `dict[str,
# Any]`. Use a `TypedDict` instead wherever the keys are actually known and fixed.
type JsonObject = dict[str, object]
type JsonRow = dict[str, object]


def to_label(value: str) -> Label:
    """The single str→`Label` gate: validate a raw string into one of the two verdicts, fail loud
    otherwise. Used at every JSON / reader-reply boundary so a `Label`-typed value is always
    validated, while raw untrusted reader output stays plain `str`."""
    if value == "prose":
        return "prose"
    if value == "lineated":
        return "lineated"
    raise ValueError(f"label must be prose|lineated, got {value!r}")

_HEX = 16  # hash prefix length kept on disk: 16 hex = 64 bits, collision-safe for a corpus


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:_HEX]


def text_hash(text: str) -> LineTextHash:
    """Stable content hash of a source line. NFC-normalized so a
    cosmetic unicode re-encoding of the same glyphs does not spuriously fail the rail,
    but any real character change does. The caller pins which role the result plays (a
    line vs a paragraph) by the field it stores it in."""
    return LineTextHash(_sha(unicodedata.normalize("NFC", text).encode("utf-8")))


def docx_package_hash(docx: Path) -> DocxPackageHash:
    """Hash of the DOCX package bytes — the coarsest rail. If the file changes at all,
    this changes, and the loader refuses stored labels/records until migration."""
    return DocxPackageHash(_sha(docx.read_bytes()))


class BookId(str):
    """Validated zero-padded folder identity for one book number."""

    def __new__(cls, value: object) -> BookId:
        text = str(value)
        if not re.fullmatch(r"\d{2,}", text):
            raise ValueError(f"book_id must be a zero-padded number, got {text!r}")
        return str.__new__(cls, text)


@dataclass(frozen=True, slots=True, order=True)
class BookKey:
    """Identity of one book edition: (lang, book_id). THE cross-language book key — the CV group,
    the `RecordsByBook` key, the per-book cap unit. A bare `BookId` collides across languages
    (ru:01 vs en:01), so anything that spans both corpora keys by this instead. `order=True` so
    groups sort deterministically; `str()` reads "ru:01", matching `LineId`'s prefix."""

    lang: Locale
    book_id: BookId

    def __post_init__(self) -> None:
        object.__setattr__(self, "book_id", BookId(self.book_id))

    def __str__(self) -> str:
        return f"{self.lang}:{self.book_id}"


@dataclass(frozen=True, slots=True, order=True)
class LineId:
    """Canonical address of one source line.

    `src_ordinal` is always the source `<w:p>` ordinal; `sub` is the line within that
    paragraph. A line without source provenance has no `LineId` and cannot enter the active
    record/truth model. Pre-canonical addresses use `LegacyLineId` in migration history.

    `order=True` preserves document order within a (lang, book). The compact wire form is
    `[lang, book_id, src_ordinal, sub]`.
    """

    lang: Locale
    book_id: BookId
    src_ordinal: int
    sub: int

    def __post_init__(self) -> None:
        if self.lang not in ("ru", "en"):
            raise ValueError(f"lang must be ru|en, got {self.lang!r}")
        if self.src_ordinal < 0 or self.sub < 0:
            raise ValueError(f"negative ordinal/sub in {self!r}")
        object.__setattr__(self, "book_id", BookId(self.book_id))

    @classmethod
    def mapped(cls, lang: Locale, book_id: BookId | str, src_ordinal: int, sub: int) -> Self:
        """Construct a canonical line from a source `<w:p>` ordinal."""
        return cls(lang, BookId(book_id), src_ordinal, sub)

    @property
    def book_key(self) -> BookKey:
        """The line's book edition — the cross-language join/CV key."""
        return BookKey(self.lang, self.book_id)

    def as_key(self) -> LineKey:
        return [self.lang, self.book_id, self.src_ordinal, self.sub]

    @classmethod
    def from_key(cls, key: Iterable[object]) -> Self:
        lang, book_id, src_ordinal, sub = key
        locale = str(lang)
        if not is_locale(locale):
            raise ValueError(f"lang must be ru|en, got {locale!r}")
        return cls(locale, BookId(book_id), int(str(src_ordinal)), int(str(sub)))

    def __str__(self) -> str:
        return f"{self.lang}:{self.book_id}:{self.src_ordinal}.{self.sub}"


@dataclass(frozen=True, slots=True, order=True)
class LegacyLineId:
    """A pre-canonical source-v2 address, valid only in lineage and history."""

    lang: Locale
    book_id: BookId
    src_ordinal: int
    sub: int

    def __post_init__(self) -> None:
        if self.lang not in ("ru", "en"):
            raise ValueError(f"lang must be ru|en, got {self.lang!r}")
        if self.src_ordinal < 0 or self.sub < 0:
            raise ValueError(f"negative ordinal/sub in {self!r}")
        object.__setattr__(self, "book_id", BookId(self.book_id))

    @property
    def book_key(self) -> BookKey:
        return BookKey(self.lang, self.book_id)

    def as_key(self) -> LineKey:
        return [self.lang, self.book_id, self.src_ordinal, self.sub]

    @classmethod
    def from_key(cls, key: Iterable[object]) -> Self:
        lang, book_id, src_ordinal, sub = key
        locale = str(lang)
        if not is_locale(locale):
            raise ValueError(f"lang must be ru|en, got {locale!r}")
        return cls(locale, BookId(book_id), int(str(src_ordinal)), int(str(sub)))

    def __str__(self) -> str:
        return f"legacy:{self.lang}:{self.book_id}:{self.src_ordinal}.{self.sub}"


def strict_bool(value: object, *, field: str) -> bool:
    """Decode a JSON Boolean without Python truthiness accepting strings or numbers."""
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field} must be a JSON boolean, got {value!r}")
