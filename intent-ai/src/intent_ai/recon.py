# research-pure: the free corpus maps — production importer verdict + student posterior per line.
"""Tier-0/tier-1 reconnaissance over every compiler-scoped body line — the $0 signals the
budget ladder starts from.

Tier 0 is the production importer's OWN verdict, read back per source line
(`pancratius.docx_structure.fold_decisions`), totalized as lineated, prose, or uncovered. A missing
flow-bearing claim is explicit, never guessed. Tier 1 ranks where
to look: the deterministic verdict and the student posterior are two separately computed free
signals over one source, and their disagreement is the error detector.

Shape: `join_rows`, `summarize`, and the corpus aggregations are PURE (records + verdict maps
in, rows/census out) so the join and ledger logic are provable without a DOCX; `scan_book` is
the thin IO shell. The `__main__` driver scans the whole corpus in parallel (records must exist
— `python -m intent_ai.build_records --corpus` first), persists per-line rows via the
store, and writes the durable evidence to an experiment folder. Re-runs (the feedback loop
re-maps after every converter fix) pass a fresh experiment id, so a scan never overwrites the
evidence a prior decision was made on.

Suspicion here is v0 — a transparent ranking to SIZE the suspect slice, not the chosen router
(E2 picks that on unbiased data): the student posterior on `det=prose` lines (disagreement
strength in the importer's one weak direction), an auto-suspect band above it for uncovered
lines, and 0 on `det=lineated` (measured FP 1/261 — accepted, audited only by random sample)."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from statistics import median, quantiles
from typing import Self

from pancratius.docx_source import ParagraphOrdinal, SourceLineCoordinate

from . import paths, store
from .identity import (
    SOURCE_IDENTITY_VERSION,
    BookId,
    ExperimentId,
    JsonObject,
    LineId,
)
from .records import (
    FEATURE_SCHEMA_VERSION,
    PRODUCER_VERSION,
    Align,
    LineRecord,
    RecordDisposition,
)
from .student import LinearModel
from .wire import number, sequence


class Tier0(StrEnum):
    """The production importer's free verdict for one body source line.

    `UNCOVERED` means that exact line has no unanimous flow-bearing claim.
    """
    LINEATED = "lineated"
    PROSE = "prose"
    UNCOVERED = "uncovered"

    @classmethod
    def from_fold(cls, folded: bool | None) -> Tier0:
        """Totalize one fold-ledger read: absent → UNCOVERED, never a guess."""
        if folded is None:
            return cls.UNCOVERED
        return cls.LINEATED if folded else cls.PROSE


@dataclass(frozen=True, slots=True)
class LineRecon:
    """One votable line's free signals: the tier-0 importer verdict, the student
    posterior P(lineated), and the v0 suspicion rank."""

    id: LineId
    det: Tier0
    posterior: float | None  # P(lineated) from the current student; None = no model fitted
    suspicion: float

    def to_dict(self) -> JsonObject:
        return {"id": self.id.as_key(), "det": self.det.value,
                "posterior": self.posterior, "suspicion": self.suspicion}

    @classmethod
    def from_dict(cls, d: JsonObject) -> Self:
        post = d["posterior"]
        return cls(id=LineId.from_key(sequence(d["id"], field="recon.id")),
                   det=Tier0(str(d["det"])),
                   posterior=number(post, field="recon.posterior") if post is not None else None,
                   suspicion=number(d["suspicion"], field="recon.suspicion"))


def suspicion_v0(det: Tier0, posterior: float | None) -> float:
    """The v0 ranking signal (see module docstring — sizing, not the chosen router).
    A missing posterior ranks at maximum uncertainty (0.5), never silently at 0."""
    p = posterior if posterior is not None else 0.5
    if det is Tier0.UNCOVERED:
        return 1.0 + p           # the auto-suspect band: sorts above every covered line
    if det is Tier0.PROSE:
        return p                 # the importer's one weak direction — posterior IS disagreement
    return 0.0                   # det=lineated: accepted (measured FP 1/261)


def join_rows(
    records: list[LineRecord],
    det: Mapping[SourceLineCoordinate, bool],
    posteriors: Mapping[LineId, float],
) -> list[LineRecon]:
    """Join candidate records whose structural compiler fate remains body."""
    out: list[LineRecon] = []
    for r in records:
        if not r.votable:
            continue
        coordinate = SourceLineCoordinate(ParagraphOrdinal(r.id.src_ordinal), r.id.sub)
        verdict = Tier0.from_fold(det.get(coordinate))
        p = posteriors.get(r.id)
        out.append(LineRecon(
            id=r.id,
            det=verdict,
            posterior=p,
            suspicion=suspicion_v0(verdict, p),
        ))
    return out


@dataclass(frozen=True)
class BookRecon:
    """One (book, lang)'s recon summary: the tier-0 census, the two-view disagreement counts,
    and the structural φ profile (the EN-envelope comparison reads these)."""

    book_id: BookId
    lang: str
    n_records: int
    n_votable: int
    n_fold_unjoined: int        # compiler fold coordinates matching no canonical record
    n_importer_lost: int        # source lines whose paragraph identity never reached the compiler
    det_lineated: int
    det_prose: int
    det_uncovered: int
    disagree_prose: int         # det=prose but posterior ≥ 0.5 — the suspect slice core
    disagree_lineated: int      # det=lineated but posterior < 0.5 — audit-only (det is trusted)
    posterior_mean: float | None
    pct_align_just: float       # φ profile over compiler-scoped body lines
    pct_align_left: float
    pct_align_center: float
    pct_wraps: float
    fill_median: float

    def __post_init__(self) -> None:
        tier0 = self.det_lineated + self.det_prose + self.det_uncovered
        if tier0 != self.n_votable:
            raise ValueError(
                f"body tier-0 census ({tier0}) does not partition body lines "
                f"({self.n_votable})"
            )

    @property
    def lineated_pct(self) -> float:
        """The book prior: det-lineated share of covered body lines."""
        covered = self.det_lineated + self.det_prose
        return self.det_lineated / covered if covered else 0.0

    def to_dict(self) -> JsonObject:
        return {
            "book_id": self.book_id, "lang": self.lang,
            "n_records": self.n_records, "n_votable": self.n_votable,
            "n_fold_unjoined": self.n_fold_unjoined,
            "n_importer_lost": self.n_importer_lost,
            "det_lineated": self.det_lineated, "det_prose": self.det_prose,
            "det_uncovered": self.det_uncovered,
            "lineated_pct": round(self.lineated_pct, 4),
            "disagree_prose": self.disagree_prose, "disagree_lineated": self.disagree_lineated,
            "posterior_mean": (round(self.posterior_mean, 4)
                               if self.posterior_mean is not None else None),
            "pct_align_just": round(self.pct_align_just, 4),
            "pct_align_left": round(self.pct_align_left, 4),
            "pct_align_center": round(self.pct_align_center, 4),
            "pct_wraps": round(self.pct_wraps, 4),
            "fill_median": round(self.fill_median, 4),
        }


def summarize(book_id: BookId, lang: str, records: list[LineRecord],
              rows: list[LineRecon],
              det: Mapping[SourceLineCoordinate, bool]) -> BookRecon:
    """Aggregate one book's body census and separate source/import diagnostics."""
    votable = [r for r in records if r.votable]
    source_lines = {
        SourceLineCoordinate(ParagraphOrdinal(r.id.src_ordinal), r.id.sub)
        for r in records
    }
    det_count = {Tier0.LINEATED: 0, Tier0.PROSE: 0, Tier0.UNCOVERED: 0}
    disagree_p = disagree_l = 0
    posts: list[float] = []
    for row in rows:
        det_count[row.det] += 1
        if row.posterior is not None:
            posts.append(row.posterior)
            if row.det is Tier0.PROSE and row.posterior >= 0.5:
                disagree_p += 1
            elif row.det is Tier0.LINEATED and row.posterior < 0.5:
                disagree_l += 1

    if len(votable) != len(rows):
        raise ValueError("body record census disagrees with joined tier-0 rows")
    n_vot = len(votable) or 1   # guard: an empty book yields zero percentages, not a crash
    return BookRecon(
        book_id=book_id, lang=lang, n_records=len(records), n_votable=len(rows),
        n_fold_unjoined=len(det.keys() - source_lines),
        n_importer_lost=sum(r.disposition is RecordDisposition.LOST for r in records),
        det_lineated=det_count[Tier0.LINEATED], det_prose=det_count[Tier0.PROSE],
        det_uncovered=det_count[Tier0.UNCOVERED],
        disagree_prose=disagree_p, disagree_lineated=disagree_l,
        posterior_mean=(sum(posts) / len(posts)) if posts else None,
        pct_align_just=sum(r.features.align is Align.JUST for r in votable) / n_vot,
        pct_align_left=sum(r.features.align is Align.LEFT for r in votable) / n_vot,
        pct_align_center=sum(r.features.align is Align.CENTER for r in votable) / n_vot,
        pct_wraps=sum(r.features.wraps for r in votable) / n_vot,
        fill_median=median(r.features.fill for r in votable) if votable else 0.0,
    )


# corpus aggregations — pure over the per-book censuses ------------------------------------------

_SUM_FIELDS = ("n_records", "n_votable", "n_fold_unjoined", "n_importer_lost",
               "det_lineated", "det_prose", "det_uncovered",
               "disagree_prose", "disagree_lineated")
_ENVELOPE_FIELDS = ("pct_align_just", "pct_wraps", "fill_median", "lineated_pct")


def corpus_totals(summaries: Sequence[BookRecon]) -> JsonObject:
    return {k: sum(getattr(s, k) for s in summaries) for k in _SUM_FIELDS}


def ru_envelope(summaries: Sequence[BookRecon]) -> dict[str, tuple[float, float]]:
    """The 5–95% band each φ-profile field spans across the ru books — min/max would be
    vacuous over 74 heterogeneous books, so the band is what "structurally alien" is read
    against. Requires ≥2 ru books (quantiles need them; the corpus has 74)."""
    ru = [s for s in summaries if s.lang == "ru"]
    out: dict[str, tuple[float, float]] = {}
    for k in _ENVELOPE_FIELDS:
        vals = [getattr(s, k) for s in ru]
        q = quantiles(vals, n=20, method="inclusive")   # q[0]=5%, q[18]=95%
        out[k] = (q[0], q[18])
    return out


def en_outliers(summaries: Sequence[BookRecon],
                envelope: Mapping[str, tuple[float, float]]) -> list[JsonObject]:
    """The en books whose profile falls outside the ru band, with the offending fields —
    the strategy's "structurally alien" flag (a routing aid, never a verdict)."""
    return [
        {"book_id": s.book_id,
         "outside": {k: round(getattr(s, k), 4) for k, (lo, hi) in envelope.items()
                     if not lo <= getattr(s, k) <= hi}}
        for s in summaries if s.lang == "en"
        if any(not lo <= getattr(s, k) <= hi for k, (lo, hi) in envelope.items())
    ]


# the IO shell ------------------------------------------------------------------------------------


def scan_book(book_id: BookId, lang: str, *,
              model: LinearModel | None = None) -> tuple[list[LineRecon], BookRecon]:
    """Load the book's records through the rails, read the production verdicts off its DOCX,
    score posteriors with the given student, join, summarize. Persists nothing — the driver
    owns writes."""
    from pancratius import docx_source, docx_structure
    from pancratius.locales import is_locale

    if not is_locale(lang):
        raise ValueError(f"unsupported locale {lang!r}")
    records = store.load_records(book_id, lang)
    docx = paths.book_docx(book_id, lang)
    source = docx_source.read(docx)
    observation = docx_structure.observe_fold(source, lang=lang)
    det = {
        coordinate: decision.disposition is docx_structure.FoldDisposition.FOLDED
        for coordinate, decision in observation.decisions
    }
    posteriors: dict[LineId, float] = {}
    if model is not None:
        from . import sequence
        posteriors = sequence.score_document(records, model).by_id
    rows = join_rows(records, det, posteriors)
    return rows, summarize(book_id, lang, records, rows, det)


def _scan_and_save(book_id: BookId, lang: str, model: LinearModel | None) -> BookRecon:
    """Pool worker: scan one (book, lang) and persist its rows. Returns the summary only —
    the rows live on disk, never shuttled back through the pool."""
    rows, summary = scan_book(book_id, lang, model=model)
    store.save_recon_rows(book_id, lang, [r.to_dict() for r in rows])
    return summary


def fit_current_student() -> tuple[LinearModel, int]:
    """The deployable student fitted on ALL trainable labels — the tier-1 posterior source.
    Bilingual since the (lang, book) re-key: the dataset joins and the CV groups key by
    `BookKey`, so ru:01 and en:01 never collide and en lines carry real posteriors. The features
    are structural (language-agnostic), so one model serves both corpora."""
    from . import student
    from .annotations import load_labels

    labelset = load_labels()
    books = sorted({g.id.book_key for g in labelset.labels})
    records = store.load_records_many(books)
    ds = student.build_dataset(records, labelset)
    return student.fit_full(ds), len(ds.y)


if __name__ == "__main__":
    import json
    import sys
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from datetime import UTC, datetime

    experiment_id = ExperimentId(
        sys.argv[sys.argv.index("--id") + 1]
        if "--id" in sys.argv
        else f"{datetime.now(UTC).date()}-{SOURCE_IDENTITY_VERSION}-corpus-recon"
    )

    model, n_trainable = fit_current_student()
    print(f"student fitted on {n_trainable} trainable labels", flush=True)

    pairs = [(b, lang) for lang in ("ru", "en") for b in paths.corpus_books(lang)]
    summaries: list[BookRecon] = []
    failed: list[JsonObject] = []
    with ProcessPoolExecutor() as pool:
        futures = {pool.submit(_scan_and_save, b, lang, model): (b, lang) for b, lang in pairs}
        for i, fut in enumerate(as_completed(futures), 1):
            b, lang = futures[fut]
            try:
                s = fut.result()
            except Exception as e:
                failed.append({"book_id": b, "lang": lang, "error": f"{type(e).__name__}: {e}"})
                print(f"[{i}/{len(pairs)}] {b}-{lang} FAILED: {e}", flush=True)
                continue
            summaries.append(s)
            print(f"[{i}/{len(pairs)}] {b}-{lang}: votable={s.n_votable} "
                  f"lineated%={s.lineated_pct:.3f} uncov={s.det_uncovered} "
                  f"disagree_p={s.disagree_prose}", flush=True)
    summaries.sort(key=lambda s: (s.lang, s.book_id))

    totals = corpus_totals(summaries)
    by_lang = {
        lang: {k: sum(getattr(s, k) for s in summaries if s.lang == lang)
               for k in ("n_votable", "det_lineated", "det_prose", "det_uncovered",
                         "disagree_prose")}
        for lang in ("ru", "en")
    }
    envelope = ru_envelope(summaries)
    outliers = en_outliers(summaries, envelope)
    uncovered_editions = [
        f"{s.lang}:{s.book_id} ({s.det_uncovered})"
        for s in summaries
        if s.det_uncovered
    ]
    lost_editions = [
        f"{s.lang}:{s.book_id} ({s.n_importer_lost})"
        for s in summaries
        if s.n_importer_lost
    ]

    scorecard: JsonObject = {
        "totals": totals, "by_lang": by_lang,
        "ru_envelope_p5_p95": {k: [round(lo, 4), round(hi, 4)]
                               for k, (lo, hi) in envelope.items()},
        "en_outliers": outliers,
        "failed": failed,
        "books": [s.to_dict() for s in summaries],
    }
    manifest: JsonObject = {
        "git_sha": store.git_sha(),
        "timestamp": datetime.now(UTC).isoformat(),
        "labels_sha256": store.sha256_file(paths.ANNOTATIONS / store.LABELS_FILE),
        "n_trainable_labels": n_trainable,
        "producer_version": PRODUCER_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "n_books": len(pairs),
        "n_failed": len(failed),
        "suspicion": "v0 (see recon.py docstring)",
    }

    n_en = sum(s.lang == "en" for s in summaries)
    body_tier0 = sum(s.det_lineated + s.det_prose + s.det_uncovered for s in summaries)
    lines = [
        "# Corpus reconnaissance (free signals, corpus-wide)", "",
        f"{len(summaries)}/{len(pairs)} (book, lang) scanned; student fitted on "
        f"{n_trainable} trainable labels."
        + (f" **{len(failed)} FAILED** (see scorecard)." if failed else ""),
        "",
        f"- body lines: **{totals['n_votable']}** "
        f"(ru {by_lang['ru']['n_votable']}, en {by_lang['en']['n_votable']})",
        f"- body tier-0: lineated {totals['det_lineated']}, prose {totals['det_prose']}, "
        f"uncovered {totals['det_uncovered']} "
        f"(sum {body_tier0})",
        "- body tier-0 gaps"
        + (f": {', '.join(uncovered_editions)}" if uncovered_editions else ": none"),
        f"- source/import diagnostics: importer-lost {totals['n_importer_lost']}"
        + (f" — {', '.join(lost_editions)}" if lost_editions else "")
        + f"; fold-unjoined {totals['n_fold_unjoined']}",
        f"- det-vs-student disagreement: prose-side {totals['disagree_prose']} "
        f"(the suspect slice core), lineated-side {totals['disagree_lineated']} (audit-only)",
        f"- EN envelope (5–95% ru band): {len(outliers)} of {n_en} en books outside"
        + (f" — {', '.join(str(o['book_id']) for o in outliers)}" if outliers else ""),
        "",
        "Per-book census in `scorecard.json`; per-line rows in `_artifacts/recon/`.",
    ]
    store.write_experiment(experiment_id, scorecard=scorecard, report="\n".join(lines) + "\n",
                           manifest=manifest)
    print(f"\nwrote {experiment_id}: {json.dumps(totals)}", file=sys.stderr)
