# research-pure: the free corpus maps — production importer verdict + student posterior per line.
"""Tier-0/tier-1 reconnaissance over every votable line — the $0 signals the budget ladder
starts from.

Tier 0 is the production importer's OWN verdict, read back per source ordinal
(`pancratius.docx_inspect.lineation_decisions`): coverage exists by construction, so the task
downstream is finding the importer's MISTAKES, not labelling the corpus. Tier 1 ranks where to
look: the deterministic verdict and the student posterior are two independent free views of one
source, and their disagreement is the error detector.

Shape: `join_rows`, `summarize`, and the corpus aggregations are PURE (records + verdict maps
in, rows/census out) so the join and ledger logic are provable without a DOCX; `scan_book` is
the thin IO shell. The `__main__` driver scans the whole corpus in parallel (records must exist
— `python -m lineation_core.build_records --corpus` first), persists per-line rows via the
store, and writes the durable evidence to an experiment folder. Re-runs (the feedback loop
re-maps after every converter fix) pass a fresh experiment id, so a scan never overwrites the
evidence a prior decision was made on.

Suspicion here is v0 — a transparent ranking to SIZE the suspect slice, not the chosen router
(E2 picks that on unbiased data): the student posterior on `det=prose` lines (disagreement
strength in the importer's one weak direction), an auto-suspect band above it for lines with no
trustworthy verdict (uncovered / REVIEW mask), and 0 on `det=lineated` (measured FP 1/261 —
accepted, audited only by random sample)."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from statistics import median, quantiles
from typing import Self

from . import artifact, paths, store
from .identity import BookId, JsonObject, LineId
from .records import Align, LineRecord, Role, SourceFate
from .student import FittedModel


class Tier0(StrEnum):
    """The production importer's free verdict for one votable line, read per source ordinal.
    `UNCOVERED` = the ordinal has no verdict (no span survived, or claimed by both kinds) —
    a fate-ledger entry, never a guess."""
    LINEATED = "lineated"
    PROSE = "prose"
    UNCOVERED = "uncovered"


class Mask(StrEnum):
    """The structural-seam votability verdict, mirroring (never importing) the production
    `docx_inspect.MaskVerdict` vocabulary. An ordinal ABSENT from the mask reads as `REVIEW`
    per that contract."""
    BODY = "body"
    CONTEXT = "context"
    REVIEW = "review"


@dataclass(frozen=True, slots=True)
class LineRecon:
    """One votable line's free signals: the tier-0 importer verdict, the structural-seam mask
    verdict, the student posterior P(lineated), and the v0 suspicion rank."""

    id: LineId
    det: Tier0
    mask: Mask
    posterior: float | None  # P(lineated) from the current student; None = no model fitted
    suspicion: float

    def to_dict(self) -> JsonObject:
        return {"id": self.id.as_key(), "det": self.det.value, "mask": self.mask.value,
                "posterior": self.posterior, "suspicion": self.suspicion}

    @classmethod
    def from_dict(cls, d: JsonObject) -> Self:
        post = d["posterior"]
        return cls(id=LineId.from_key(d["id"]), det=Tier0(str(d["det"])),
                   mask=Mask(str(d["mask"])),
                   posterior=float(post) if post is not None else None,
                   suspicion=float(d["suspicion"]))


def suspicion_v0(det: Tier0, mask: Mask, posterior: float | None) -> float:
    """The v0 ranking signal (see module docstring — sizing, not the chosen router).
    A missing posterior ranks at maximum uncertainty (0.5), never silently at 0."""
    p = posterior if posterior is not None else 0.5
    if det is Tier0.UNCOVERED or mask is Mask.REVIEW:
        return 1.0 + p           # the auto-suspect band: sorts above every covered line
    if det is Tier0.PROSE:
        return p                 # the importer's one weak direction — posterior IS disagreement
    return 0.0                   # det=lineated: accepted (measured FP 1/261)


def join_rows(
    records: list[LineRecord],
    det: Mapping[int, bool],
    mask: Mapping[int, Mask],
    posteriors: Mapping[LineId, float],
) -> list[LineRecon]:
    """Join one book's votable records to the per-ordinal verdict maps. Pure — every input is
    data. Only votable lines get rows (non-votable records are structure/context/unmapped, held
    by the producer); every `sub` segment of one `<w:p>` shares its ordinal's verdicts."""
    out: list[LineRecon] = []
    for r in records:
        if not r.votable:
            continue
        hit = det.get(r.id.src_ordinal)
        verdict = (Tier0.UNCOVERED if hit is None
                   else Tier0.LINEATED if hit else Tier0.PROSE)
        mask_verdict = mask.get(r.id.src_ordinal, Mask.REVIEW)
        p = posteriors.get(r.id)
        out.append(LineRecon(id=r.id, det=verdict, mask=mask_verdict, posterior=p,
                             suspicion=suspicion_v0(verdict, mask_verdict, p)))
    return out


@dataclass(frozen=True)
class BookRecon:
    """One (book, lang)'s recon summary: the tier-0 census, the two-view disagreement counts,
    and the structural φ profile (the EN-envelope comparison reads these)."""

    book_id: BookId
    lang: str
    n_records: int
    n_votable: int
    n_unmapped_records: int     # producer-held body lines (no provenance) — non-votable, ledgered
    n_det_unjoined: int         # non-empty det ordinals matching NO record — producer/importer
                                # desync (a block span covers its interior blanks; those are not it)
    det_lineated: int
    det_prose: int
    det_uncovered: int
    n_mask_review: int
    disagree_prose: int         # det=prose but posterior ≥ 0.5 — the suspect slice core
    disagree_lineated: int      # det=lineated but posterior < 0.5 — audit-only (det is trusted)
    posterior_mean: float | None
    pct_align_just: float       # φ profile over votable lines
    pct_align_left: float
    pct_align_center: float
    pct_wraps: float
    fill_median: float

    @property
    def lineated_pct(self) -> float:
        """The book prior: det-lineated share of the COVERED votable lines."""
        covered = self.det_lineated + self.det_prose
        return self.det_lineated / covered if covered else 0.0

    def to_dict(self) -> JsonObject:
        return {
            "book_id": self.book_id, "lang": self.lang,
            "n_records": self.n_records, "n_votable": self.n_votable,
            "n_unmapped_records": self.n_unmapped_records,
            "n_det_unjoined": self.n_det_unjoined,
            "det_lineated": self.det_lineated, "det_prose": self.det_prose,
            "det_uncovered": self.det_uncovered, "n_mask_review": self.n_mask_review,
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
              rows: list[LineRecon], det: Mapping[int, bool],
              empty_ordinals: frozenset[int] = frozenset()) -> BookRecon:
    """Aggregate one book's rows + records into the `BookRecon` census. Pure.
    `empty_ordinals` names the source paragraphs with no text — a det span covers its interior
    blanks, so they are excluded from the desync counter rather than drowning it."""
    votable = [r for r in records if r.votable]
    n_unmapped = sum(1 for r in records
                     if r.role is Role.BODY and r.source_fate is SourceFate.UNMAPPED)
    mapped_ordinals = {r.id.src_ordinal for r in records if r.id.is_mapped}
    det_count = {Tier0.LINEATED: 0, Tier0.PROSE: 0, Tier0.UNCOVERED: 0}
    disagree_p = disagree_l = review = 0
    posts: list[float] = []
    for row in rows:
        det_count[row.det] += 1
        if row.mask is Mask.REVIEW:
            review += 1
        if row.posterior is not None:
            posts.append(row.posterior)
            if row.det is Tier0.PROSE and row.posterior >= 0.5:
                disagree_p += 1
            elif row.det is Tier0.LINEATED and row.posterior < 0.5:
                disagree_l += 1

    n_vot = len(votable) or 1   # guard: an empty book yields zero percentages, not a crash
    return BookRecon(
        book_id=book_id, lang=lang, n_records=len(records), n_votable=len(votable),
        n_unmapped_records=n_unmapped,
        n_det_unjoined=len(det.keys() - mapped_ordinals - empty_ordinals),
        det_lineated=det_count[Tier0.LINEATED], det_prose=det_count[Tier0.PROSE],
        det_uncovered=det_count[Tier0.UNCOVERED], n_mask_review=review,
        disagree_prose=disagree_p, disagree_lineated=disagree_l,
        posterior_mean=(sum(posts) / len(posts)) if posts else None,
        pct_align_just=sum(r.features.align is Align.JUST for r in votable) / n_vot,
        pct_align_left=sum(r.features.align is Align.LEFT for r in votable) / n_vot,
        pct_align_center=sum(r.features.align is Align.CENTER for r in votable) / n_vot,
        pct_wraps=sum(r.features.wraps for r in votable) / n_vot,
        fill_median=median(r.features.fill for r in votable) if votable else 0.0,
    )


# corpus aggregations — pure over the per-book censuses ------------------------------------------

_SUM_FIELDS = ("n_records", "n_votable", "n_unmapped_records", "n_det_unjoined",
               "det_lineated", "det_prose", "det_uncovered", "n_mask_review",
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
              model: FittedModel | None = None) -> tuple[list[LineRecon], BookRecon]:
    """Load the book's records through the rails, read the production verdicts off its DOCX,
    score posteriors with the given student, join, summarize. Persists nothing — the driver
    owns writes."""
    from pancratius import docx_inspect as di

    records = store.load_records(book_id, lang)
    docx = paths.book_docx(book_id, lang)
    det = di.lineation_decisions(docx)
    mask = {ordn: Mask(v.value) for ordn, v in di.votability_mask(docx).items()}
    empty = frozenset(r.index for r in di.read_rows(docx) if r.empty)

    votable = [r for r in records if r.votable]
    posteriors: dict[LineId, float] = {}
    if model is not None and votable:
        for r, p in zip(votable, model.posteriors([r.features for r in votable]), strict=True):
            posteriors[r.id] = p
    rows = join_rows(records, det, mask, posteriors)
    return rows, summarize(book_id, lang, records, rows, det, empty)


def _scan_and_save(book_id: BookId, lang: str, model: FittedModel | None) -> BookRecon:
    """Pool worker: scan one (book, lang) and persist its rows. Returns the summary only —
    the rows live on disk, never shuttled back through the pool."""
    rows, summary = scan_book(book_id, lang, model=model)
    store.save_recon_rows(book_id, lang, [r.to_dict() for r in rows])
    return summary


def fit_current_student() -> tuple[FittedModel, int]:
    """The deployable student fitted on ALL trainable labels — the tier-1 posterior source.
    Bilingual since the (lang, book) re-key: the dataset joins and the CV groups key by
    `BookKey`, so ru:01 and en:01 never collide and en lines carry real posteriors. The features
    are structural (language-agnostic), so one model serves both corpora."""
    from . import student
    from .annotations import load_labels

    labelset = load_labels()
    books = sorted({g.id.book_key for g in labelset.trainable})
    records = store.load_records_many(books)
    ds = student.build_dataset(records, labelset)
    return student.fit_full(ds), len(ds.y)


if __name__ == "__main__":
    import json
    import sys
    from concurrent.futures import ProcessPoolExecutor, as_completed
    from datetime import UTC, datetime

    experiment_id = (sys.argv[sys.argv.index("--id") + 1] if "--id" in sys.argv
                     else f"{datetime.now(UTC).date()}-corpus-recon")

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
            except Exception as e:  # noqa: BLE001 — one bad book is a ledger entry, not an abort
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
        "producer_version": artifact.PRODUCER_VERSION,
        "feature_schema_version": artifact.FEATURE_SCHEMA_VERSION,
        "n_books": len(pairs),
        "n_failed": len(failed),
        "suspicion": "v0 (see recon.py docstring)",
    }

    n_en = sum(s.lang == "en" for s in summaries)
    lines = [
        "# Corpus reconnaissance (free signals, corpus-wide)", "",
        f"{len(summaries)}/{len(pairs)} (book, lang) scanned; student fitted on "
        f"{n_trainable} trainable labels."
        + (f" **{len(failed)} FAILED** (see scorecard)." if failed else ""),
        "",
        f"- votable lines: **{totals['n_votable']}** "
        f"(ru {by_lang['ru']['n_votable']}, en {by_lang['en']['n_votable']})",
        f"- tier-0: lineated {totals['det_lineated']}, prose {totals['det_prose']}, "
        f"uncovered {totals['det_uncovered']}; mask-review {totals['n_mask_review']}; "
        f"unmapped records {totals['n_unmapped_records']}; "
        f"det-unjoined {totals['n_det_unjoined']}",
        f"- det-vs-student disagreement: prose-side {totals['disagree_prose']} "
        f"(the suspect slice core), lineated-side {totals['disagree_lineated']} (audit-only)",
        f"- EN envelope (5–95% ru band): {len(outliers)} of {n_en} en books outside"
        + (f" — {', '.join(o['book_id'] for o in outliers)}" if outliers else ""),
        "",
        "Per-book census in `scorecard.json`; per-line rows in `_artifacts/recon/`.",
    ]
    store.write_experiment(experiment_id, scorecard=scorecard, report="\n".join(lines) + "\n",
                           manifest=manifest)
    print(f"\nwrote {experiment_id}: {json.dumps(totals)}", file=sys.stderr)
