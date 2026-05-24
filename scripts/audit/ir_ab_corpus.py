#!/usr/bin/env -S uv run --quiet
"""Corpus-wide A/B oracle for the import-pipeline IR migration.

For EVERY book + poem with a source DOCX under ``legacy/``, this converts the
document under BOTH conversion paths — the LIVE typed-IR pipeline
(``convert_single_docx``) and the legacy GFM engine kept as the ORACLE
(``convert_single_docx_gfm``) — and measures:

  * **content loss (the hard gate):** the prose TOKEN MULTISET of the GFM body
    must be a SUBSET of the IR body's. Any reading-content word present in the GFM
    output but missing from the IR output is content the IR DROPPED — a BLOCKER.
    Tokenization strips markup/whitespace and compares reading words, so footnote
    definition bodies, table cell text, and verse lines all count (they are all in
    the lowered body string for both engines).
  * **line similarity %** per work (difflib ratio over body lines).
  * the aggregate distribution + the works with the largest cosmetic deltas.

This is the migration ORACLE, not a CI gate: it runs over the real local
``legacy/`` corpus and is skipped when pandoc/pillow are absent (like the import
tests). It does NOT cut over the live path — it proves the candidate is safe to.

CLI: ``uv run scripts/audit/ir_ab_corpus.py [--limit N] [--books-only]
[--full]``. Importable: ``run_corpus()`` returns structured ``WorkResult``s for
the pytest wrapper in ``tests/test_ir_ab_corpus.py``.
"""

from __future__ import annotations

import argparse
import difflib
import html
import re
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from lib import footnotes  # noqa: E402
from lib.docx_conversion import (  # noqa: E402
    convert_single_docx,
    convert_single_docx_gfm,
)
from lib.docx_engine import to_ascii_slug  # noqa: E402

LEGACY = ROOT / "legacy"


# ---------------------------------------------------------------------------
# corpus enumeration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Work:
    """One source DOCX to A/B: its path, product kind, and language."""

    docx: Path
    kind: str
    lang: str

    @property
    def rel(self) -> str:
        return self.docx.relative_to(ROOT).as_posix()

    @property
    def work_key(self) -> str:
        # A stable per-work slug for the converters' cross-ref/bibliography
        # resolution. Both engines get the SAME key, so any resolution difference
        # cancels in the comparison; only the body content is being compared.
        return to_ascii_slug(self.docx.stem) or "work"


def enumerate_corpus(*, books_only: bool = False) -> list[Work]:
    """Every book + poem DOCX under ``legacy/`` (books by language; poems are RU).

    Projects are deliberately excluded — they are authored sections, not converter
    output (``docs/import-pipeline.md``), so the importer never converts them."""
    works: list[Work] = []
    for lang in ("ru", "en"):
        book_dir = LEGACY / "books" / lang
        if book_dir.is_dir():
            for docx in sorted(book_dir.glob("*.docx")):
                works.append(Work(docx=docx, kind="book", lang=lang))
    if not books_only:
        poetry_dir = LEGACY / "poetry"
        if poetry_dir.is_dir():
            for docx in sorted(poetry_dir.rglob("*.docx")):
                works.append(Work(docx=docx, kind="poem", lang="ru"))
    return works


# ---------------------------------------------------------------------------
# prose tokenization (the no-content-loss measure)
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_FOOTNOTE_MARKER_RE = re.compile(r"\[\^[^\]]+\]:?")
_IMG_LINK_TARGET_RE = re.compile(r"\]\([^)]*\)")  # drop link/image targets, keep labels
# Inline emphasis markers glue to adjacent text (`**р**ождённых` is ONE word
# `рождённых` once unwrapped) — REMOVE them so a stray Word bold on a word's first
# letter doesn't fragment the token. Both engines see the same removal, so this
# normalizes the GFM engine's emphasis-fragmentation artifacts away on both sides.
_INLINE_MARK_RE = re.compile(r"[*_`~^]+")
# Block markers are line prefixes — turn them into separators, not glue.
_BLOCK_MARK_RE = re.compile(r"[#>|]+")
# A reading token: a maximal run of word characters (any script) — drops markup,
# punctuation, and standalone separators. `\w` under `re.UNICODE` covers Cyrillic.
_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _strip_markup(body: str) -> str:
    """Reduce a lowered Markdown body to its reading text: drop HTML tags, link /
    image *targets* (paths/URLs are not reading content; labels stay), footnote
    markers, entities, and Markdown emphasis/block markers. Both engines are
    reduced the SAME way, so the only thing the comparison sees is reading content,
    not which markup syntax each engine chose (HTML `<sup>` vs `^…^`, etc.)."""
    text = _IMG_LINK_TARGET_RE.sub("]", body)
    text = _TAG_RE.sub(" ", text)
    text = _FOOTNOTE_MARKER_RE.sub(" ", text)
    text = html.unescape(text)
    text = _INLINE_MARK_RE.sub("", text)   # unwrap emphasis: keep words whole
    text = _BLOCK_MARK_RE.sub(" ", text)   # block prefixes are separators
    return text


def prose_tokens(body: str) -> Counter[str]:
    """The reading-content WORD multiset (for human-readable delta reporting).

    Footnote definition bodies, table cells, verse lines, signatures, and epigraph
    text all survive — they are all plain reading words in the body string. Used to
    SHOW which words differ; the hard gate is `reading_chars` (below), which is
    immune to the two engines grouping the same characters into words differently
    (e.g. `2<sup>ℵ0</sup>` vs `2^ℵ0^` — identical reading content, different word
    boundaries)."""
    return Counter(m.group(0).casefold() for m in _WORD_RE.finditer(_strip_markup(body)))


def reading_chars(body: str) -> Counter[str]:
    """The reading-content CHARACTER multiset — the no-content-loss invariant.

    Every alphanumeric reading character, case-folded, with whitespace and
    punctuation dropped. This is the hard gate because it is immune to word-boundary
    artifacts the word multiset is sensitive to: a stray Word bold on a word's first
    letter (`**р**ождённых`) and a math sub/superscript rendered as HTML by one
    engine and as `^…^`/`~…~` by the other both carry the SAME characters, so a true
    drop (a missing word, verse line, footnote body, or table cell) still shows as
    missing characters, while a pure markup/spacing difference does not."""
    return Counter(ch.casefold() for ch in _strip_markup(body) if ch.isalnum())


def line_similarity(a: str, b: str) -> float:
    """difflib ratio over body lines (the cosmetic-delta measure).

    Lines are compared right-stripped: trailing whitespace is never meaningful
    (Pandoc leaves it on verse lines; the IR trims it), so it should not register
    as a structural delta. Blank lines are kept (stanza/spacing structure is real)."""
    al = [ln.rstrip() for ln in a.splitlines()]
    bl = [ln.rstrip() for ln in b.splitlines()]
    return difflib.SequenceMatcher(None, al, bl).ratio()


# ---------------------------------------------------------------------------
# structural-parity (the gap that hid C1): count the typed-structure markers each
# engine emits, so a signature/epigraph/rtl span the char multiset is blind to
# (it survives as plain reading text either way) shows up as a count delta.
# ---------------------------------------------------------------------------

# The two REGRESSION classes for the verdict: a NET LOSS here is the C1/I2 bug
# class and FAILS the gate (alignment-driven signatures/epigraphs + bidi spans).
STRUCT_REGRESSION_KEYS = ("signature", "epigraph", "rtl")
# The two TRACKED classes: verse/answer-block deltas are the DEFERRED I4 (verse
# over-detection / litany grouping, handled in a later TDD session). They are
# REPORTED so they stay visible but they do NOT block.
STRUCT_TRACKED_KEYS = ("verse-block", "answer-block")
STRUCT_KEYS = (*STRUCT_REGRESSION_KEYS, *STRUCT_TRACKED_KEYS)


def structural_counts(body: str) -> dict[str, int]:
    """Count the typed-structure markers in a lowered body: the `class="…"`
    wrappers for signature/epigraph/verse-block/answer-block, plus `<span dir=`
    bidi spans. These are the structures the reading-character multiset cannot see
    (their text survives as plain words regardless), so they need their OWN parity
    check — the blind spot that let C1 ship."""
    return {
        "signature": body.count('class="signature"'),
        "epigraph": body.count('class="epigraph"'),
        "verse-block": body.count('class="verse-block"'),
        "answer-block": body.count('class="answer-block"'),
        "rtl": body.count("<span dir="),
    }


# ---------------------------------------------------------------------------
# per-work A/B
# ---------------------------------------------------------------------------


@dataclass
class WorkResult:
    work: Work
    gfm_lines: int
    ir_lines: int
    gfm_words: int
    ir_words: int
    # GENUINE content loss (the hard gate): GFM reading characters missing from IR
    # that are NOT accounted for by a GFM footnote-duplication artifact (see below).
    lost_chars: int = 0
    # GFM reading characters missing from IR that ARE covered by a GFM footnote
    # definition body — i.e. content GFM duplicated inline (its `[^N]` ref was
    # mangled into the footnote's text) while the IR keeps it once, correctly.
    # This is an IR IMPROVEMENT, not loss; reported separately, never a blocker.
    fn_dup_chars: int = 0
    # WORDS present in GFM but missing from IR — for human-readable reporting of
    # WHAT changed (sensitive to word-boundary artifacts; not the gate).
    lost: Counter[str] = field(default_factory=Counter)
    # Words only in the IR body (additions/improvements; informational).
    gained: Counter[str] = field(default_factory=Counter)
    similarity: float = 1.0
    error: str | None = None
    # Per-structure counts for both engines (signature/epigraph/verse-block/
    # answer-block/rtl). The structural-parity verdict diffs these — the blind
    # spot the reading-character multiset never saw.
    gfm_struct: dict[str, int] = field(default_factory=dict)
    ir_struct: dict[str, int] = field(default_factory=dict)

    @property
    def lost_total(self) -> int:
        """The hard-gate measure: genuine reading characters the IR dropped (MUST
        be 0). Excludes GFM footnote-duplication artifacts (`fn_dup_chars`)."""
        return self.lost_chars

    @property
    def gained_total(self) -> int:
        return sum(self.gained.values())

    def struct_delta(self, key: str) -> int:
        """IR count minus GFM count for `key` (negative = the IR emitted FEWER —
        a net loss vs the oracle)."""
        return self.ir_struct.get(key, 0) - self.gfm_struct.get(key, 0)

    @property
    def struct_regressions(self) -> dict[str, int]:
        """Net LOSSES in the regression classes (signature/epigraph/rtl) — the
        C1/I2 bug class. A non-empty dict FAILS the gate."""
        return {
            k: self.struct_delta(k)
            for k in STRUCT_REGRESSION_KEYS
            if self.struct_delta(k) < 0
        }

    @property
    def tracked_deltas(self) -> dict[str, int]:
        """Non-zero deltas in the tracked classes (verse-block/answer-block) — the
        DEFERRED I4. Reported, never blocking."""
        return {k: self.struct_delta(k) for k in STRUCT_TRACKED_KEYS if self.struct_delta(k)}


def ab_one(work: Work) -> WorkResult:
    """Convert `work` under both engines and diff their reading content.

    Content loss (the hard gate) is the reading-CHARACTER multiset difference; the
    word multiset is also computed, for human-readable reporting of what differs.
    """
    with tempfile.TemporaryDirectory(prefix="ir-ab-") as td:
        tdp = Path(td)
        wd = tdp / "wd"
        wd.mkdir(parents=True, exist_ok=True)
        try:
            # GFM is now the ORACLE (`convert_single_docx_gfm`); IR is the LIVE
            # path (`convert_single_docx`). The gate still asserts IR loses no
            # reading content vs the GFM oracle (now: live vs oracle, not
            # candidate vs live).
            gfm = convert_single_docx_gfm(
                work.docx, kind=work.kind, lang=work.lang, work_key=work.work_key,
                title=work.docx.stem, work_dir=wd, title_index={}, media_out=tdp / "mg",
            )
            ir_out = convert_single_docx(
                work.docx, kind=work.kind, lang=work.lang, work_key=work.work_key,
                title=work.docx.stem, work_dir=wd, title_index={}, media_out=tdp / "mi",
            )
        except Exception as exc:  # surface a hard parse/lower gap as a per-work error
            return WorkResult(work=work, gfm_lines=0, ir_lines=0, gfm_words=0,
                              ir_words=0, error=f"{type(exc).__name__}: {exc}")

    g = prose_tokens(gfm.body)
    i = prose_tokens(ir_out.body)
    char_diff = reading_chars(gfm.body) - reading_chars(ir_out.body)
    # Split the apparent loss: characters covered by a GFM footnote-definition body
    # are GFM duplicating footnote text inline (its `[^N]` ref mangled) — the IR
    # keeps that content once, which is correct, so it is an improvement, not loss.
    _body, gfm_defs = footnotes.extract_footnote_defs(gfm.body)
    fn_def_chars = reading_chars("\n".join(d.text for d in gfm_defs))
    genuine = char_diff - fn_def_chars
    fn_dup = char_diff & fn_def_chars
    return WorkResult(
        work=work,
        gfm_lines=gfm.body.count("\n"),
        ir_lines=ir_out.body.count("\n"),
        gfm_words=sum(g.values()),
        ir_words=sum(i.values()),
        lost_chars=sum(genuine.values()),
        fn_dup_chars=sum(fn_dup.values()),
        lost=g - i,   # multiset difference: GFM words not covered by IR (report)
        gained=i - g,
        similarity=line_similarity(gfm.body, ir_out.body),
        gfm_struct=structural_counts(gfm.body),
        ir_struct=structural_counts(ir_out.body),
    )


def run_corpus(works: list[Work] | None = None) -> list[WorkResult]:
    """A/B every work; returns one ``WorkResult`` each."""
    works = works if works is not None else enumerate_corpus()
    return [ab_one(w) for w in works]


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, int(q * (len(sorted_vals) - 1) + 0.5))
    return sorted_vals[idx]


def report(results: list[WorkResult], *, full: bool = False) -> int:
    errors = [r for r in results if r.error]
    ok = [r for r in results if not r.error]
    losers = [r for r in ok if r.lost_total > 0]
    sims = sorted(r.similarity for r in ok)

    print(f"\n=== IR vs GFM A/B over {len(results)} works "
          f"({sum(1 for r in results if r.work.kind == 'book')} books, "
          f"{sum(1 for r in results if r.work.kind == 'poem')} poems) ===")

    if errors:
        print(f"\n!! {len(errors)} work(s) FAILED to convert under one engine:")
        for r in errors:
            print(f"   {r.work.rel}: {r.error}")

    print(f"\nCONTENT LOSS (the hard gate, reading-character multiset): "
          f"{len(losers)} work(s) lose content (MUST be 0)")
    for r in losers:
        sample = ", ".join(f"{w}×{n}" for w, n in r.lost.most_common(12))
        print(f"   LOSS {r.work.rel}: {r.lost_chars} genuine reading char(s) dropped; "
              f"word-level sample [{r.lost.__len__()} distinct]: {sample}")

    improved = [r for r in ok if r.fn_dup_chars > 0]
    if improved:
        print(f"\nIR IMPROVEMENTS (GFM footnote-duplication artifacts the IR fixes; "
              f"NOT loss): {len(improved)} work(s)")
        for r in improved:
            print(f"   FIX  {r.work.rel}: IR drops {r.fn_dup_chars} char(s) of footnote "
                  f"text GFM duplicated inline (mangled `[^N]` ref)")

    # STRUCTURAL PARITY (the gap that hid C1): signature/epigraph/rtl losses FAIL;
    # verse-block/answer-block deltas are REPORTED as tracked (deferred I4).
    struct_regressors = [r for r in ok if r.struct_regressions]
    print(f"\nSTRUCTURAL PARITY — regression classes "
          f"({'/'.join(STRUCT_REGRESSION_KEYS)}): "
          f"{len(struct_regressors)} work(s) with a NET LOSS vs GFM (MUST be 0)")
    for r in struct_regressors:
        detail = ", ".join(
            f"{k} {r.gfm_struct.get(k, 0)}→{r.ir_struct.get(k, 0)} ({d:+d})"
            for k, d in r.struct_regressions.items()
        )
        print(f"   REGRESSION {r.work.rel}: {detail}")

    tracked = [r for r in ok if r.tracked_deltas]
    print(f"\nSTRUCTURAL PARITY — tracked classes "
          f"({'/'.join(STRUCT_TRACKED_KEYS)}, I4 deferred — REPORTED, NOT blocking): "
          f"{len(tracked)} work(s) with a verse/answer-block delta")
    for r in sorted(tracked, key=lambda r: sum(abs(v) for v in r.tracked_deltas.values()), reverse=True):
        detail = ", ".join(
            f"{k} {r.gfm_struct.get(k, 0)}→{r.ir_struct.get(k, 0)} ({d:+d})"
            for k, d in r.tracked_deltas.items()
        )
        print(f"   tracked {r.work.rel}: {detail}")

    if sims:
        print(f"\nLINE SIMILARITY (IR vs GFM body): min={sims[0]:.4f} "
              f"p10={_percentile(sims, 0.10):.4f} median={_percentile(sims, 0.50):.4f} "
              f"p90={_percentile(sims, 0.90):.4f} max={sims[-1]:.4f}")

    by_delta = sorted(ok, key=lambda r: r.similarity)
    print("\nTop works by cosmetic delta (lowest line-similarity, content-safe):")
    shown = by_delta if full else by_delta[:10]
    for r in shown:
        flag = "  LOSS" if r.lost_total else ("  STRUCT-REGRESSION" if r.struct_regressions else "")
        print(f"   {r.similarity:.4f}  {r.work.rel}  "
              f"(GFM {r.gfm_words}w/{r.gfm_lines}L  IR {r.ir_words}w/{r.ir_lines}L  "
              f"+{r.gained_total}w){flag}")

    blocked = bool(errors) or bool(losers) or bool(struct_regressors)
    if blocked:
        verdict = "BLOCKED — see content loss / errors / structural regressions above"
    else:
        verdict = (
            "CLEAN — zero reading-content loss AND no signature/epigraph/rtl "
            f"regression corpus-wide ({len(tracked)} verse/answer-block delta(s) "
            "tracked as I4, deferred)"
        )
    print(f"\nRESULT: {verdict}\n")
    return 1 if blocked else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="IR vs GFM corpus A/B no-content-loss oracle")
    ap.add_argument("--limit", type=int, default=None, help="cap works for a fast smoke pass")
    ap.add_argument("--books-only", action="store_true", help="skip poems")
    ap.add_argument("--full", action="store_true", help="list every work, not just the top deltas")
    args = ap.parse_args(argv)

    works = enumerate_corpus(books_only=args.books_only)
    if args.limit is not None:
        works = works[: args.limit]
    if not works:
        print("no source DOCX found under legacy/ — nothing to A/B", file=sys.stderr)
        return 1
    return report(run_corpus(works), full=args.full)


if __name__ == "__main__":
    sys.exit(main())
