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
from typing import Any, Self

# --- domain vocabulary (the greppable names every module shares) ------------------------------
# Plain `str` aliases, not Literal/NewType: labels come from JSON and `LineLabel.__post_init__`
# is the runtime enforcer of `prose | lineated`, so a Literal would only force casts at the
# boundary without adding a guarantee the runtime check does not already give.

type Label = str        # "prose" | "lineated" — the two-class verdict for one line
type ReaderTag = str    # one panel reader: grok | deepseek | gemini | owl | mimo | minimax
type BookId = str       # zero-padded book folder number ("01", "64") — the CV group + join key

# line → label maps. `LabelByLine` is the shared scoring surface: a truth map, a prediction map,
# the contested eval slice — all interchangeable as either side of a per-line join. `ReaderCalls`
# names the SAME shape in its distinct role of ONE reader's calls, so `PanelVotes` reads as what
# it is (reader → that reader's calls) rather than a bare double-nested dict.
type LabelByLine = dict[LineId, Label]
type ReaderCalls = LabelByLine
type PanelVotes = dict[ReaderTag, ReaderCalls]

_HEX = 16  # hash prefix length kept on disk: 16 hex = 64 bits, collision-safe for a corpus


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:_HEX]


def text_hash(text: str) -> str:
    """Stable content hash of a text unit (paragraph or line). NFC-normalized so a
    cosmetic unicode re-encoding of the same glyphs does not spuriously fail the rail,
    but any real character change does."""
    return _sha(unicodedata.normalize("NFC", text).encode("utf-8"))


def docx_package_hash(docx) -> str:
    """Hash of the DOCX package bytes — the coarsest rail. If the file changes at all,
    this changes, and the loader refuses stored labels/records until migration."""
    return _sha(docx.read_bytes())


# Source <w:p> ordinals are small (the largest corpus book is ~40k paragraphs). An
# UNMAPPED line (no SourceSpan — a §14-P1 span-drop) has no real ordinal, so it is
# addressed in a disjoint reserved band starting here. `LineId.is_mapped` reads this
# boundary, so no business-logic code hand-rolls a magic number: the band lives in ONE
# place and `LineId` is the only thing that knows about it.
_UNMAPPED_BAND = 9_000_000


@dataclass(frozen=True, slots=True, order=True)
class LineId:
    """Address of one source line. `order=True` so records sort document-order within
    a (lang, book). Serializes as a 4-element list `[lang, book_id, src_ordinal, sub]`
    — compact, and matches the on-disk key shape.

    `src_ordinal` is the source <w:p> ordinal for a MAPPED line (the join key), or a
    reserved-band value for an UNMAPPED line (build it via `LineId.unmapped`, read the
    distinction via `is_mapped`). It is always a real, non-negative int — never optional —
    so consumers never thread an `int | None`."""

    lang: str
    book_id: str
    src_ordinal: int
    sub: int

    def __post_init__(self) -> None:
        if self.lang not in ("ru", "en"):
            raise ValueError(f"lang must be ru|en, got {self.lang!r}")
        if self.src_ordinal < 0 or self.sub < 0:
            raise ValueError(f"negative ordinal/sub in {self!r}")
        # book_id is the zero-padded folder number ("01", "64"); reject sloppy ints.
        if not re.fullmatch(r"\d{2,}", self.book_id):
            raise ValueError(f"book_id must be a zero-padded number, got {self.book_id!r}")

    @classmethod
    def mapped(cls, lang: str, book_id: str, src_ordinal: int, sub: int) -> Self:
        """A line WITH provenance: src_ordinal is a real source <w:p> ordinal."""
        if src_ordinal >= _UNMAPPED_BAND:
            raise ValueError(f"src_ordinal {src_ordinal} is in the reserved unmapped band")
        return cls(lang, book_id, src_ordinal, sub)

    @classmethod
    def unmapped(cls, lang: str, book_id: str, doc_position: int, sub: int) -> Self:
        """A line WITHOUT provenance (§14-P1 span-drop). Addressed in the reserved band by
        its document position, so it is unique and ordered but provably not a real ordinal."""
        return cls(lang, book_id, _UNMAPPED_BAND + doc_position, sub)

    @property
    def is_mapped(self) -> bool:
        return self.src_ordinal < _UNMAPPED_BAND

    def as_key(self) -> list[Any]:
        return [self.lang, self.book_id, self.src_ordinal, self.sub]

    @classmethod
    def from_key(cls, key: Iterable[Any]) -> Self:
        lang, book_id, src_ordinal, sub = key
        return cls(str(lang), str(book_id), int(src_ordinal), int(sub))

    def __str__(self) -> str:
        return f"{self.lang}:{self.book_id}:{self.src_ordinal}.{self.sub}"
