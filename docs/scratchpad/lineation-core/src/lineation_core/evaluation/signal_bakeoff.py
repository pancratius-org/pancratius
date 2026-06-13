# research-pure: E2 — rank the free signals that surface importer errors + the inside/outside-φ fork.
"""E2, the $0 replay on the E1 WORKING half (747 lines). Two questions, one experiment:

(b) **Signal bakeoff.** The target is `det ≠ truth` — the lines where the deterministic tier-0
    verdict disagrees with committed truth on the working half (the readout found 61: 47 verse-missed
    det=prose/truth=lineated, 14 over-lineated det=lineated/truth=prose). Rank the free candidate
    signals by how well they SURFACE those disagreements (ROC-AUC on all 747 AND on the 21 human
    ground-truth lines — a signal must hold up on BOTH):
      - det⊕student disagreement  (posterior pulls AGAINST the det verdict),
      - raw student uncertainty   (low margin |posterior−0.5|),
      - recon.suspicion_v0,
      - panel vote-spread         (minority fraction across the 3 readers).
    E3 does NOT gate on a router — it sweeps the WHOLE det=prose band (ds-flash-cheap). The router
    only ORDERS that sweep, so the choice must be ROBUST on independent (human) truth, not the
    AUC(all) leader. `det_student_disagree` wins AUC(all) only because it ranks det=lineated lines by
    1−posterior — gate-circular, and it collapses to ~chance on the human subset.

(c) **The inside/outside-φ fork.** Spearman(student uncertainty, panel vote-spread) under
    book-held-out scoring on the working half. ρ ≥ +0.30 with monotone terciles → INSIDE-φ
    (student uncertainty is a usable free acquisition signal); weaker → OUTSIDE-φ (student
    uncertainty is audit-only).

Hard guards this replay depends on (mirroring `working_readout.compute`):
  - WORKING HALF ONLY — the frozen acceptance half is scored once in E4; FAILS LOUD on a frozen leak.
  - BOOK-HELD-OUT POSTERIOR ONLY — the deployed student is trained INCLUDING the working-half gate
    labels, so its `fit_full` posterior is IN-SAMPLE on these lines and would leak. The bakeoff uses
    the out-of-fold posterior (`student.oof_smoothed` at alpha=0 over ALL trainable labels — each line
    scored by a model that never saw its book). The corpus router (recon) uses the unsmoothed
    `fit_full` posterior; alpha=0 keeps the OOF score per-line comparable to it.
  - GATE TRUTH IS NOT INDEPENDENT — 726 of 747 working-truth labels are `gate` (the panel's own
    verdict), so `det ≠ truth` is mostly det-vs-PANEL: a candidate error, NOT a proven det error. The
    human subset (21 lines, 5 of them det=disagreement) is the only ground truth; AUC is reported on
    it separately. No ground-truth detection rate is claimed.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .. import paths, store
from ..annotations import LabelSource, PanelVote, load_labels, load_votes
from ..identity import JsonObject, Label, LineId
from ..recon import Mask, Tier0, suspicion_v0

WORKING_SET = "e1-instrument-working"
FROZEN_SET = "e1-instrument-frozen"

PHI_RHO_THRESHOLD = 0.30  # pre-registered: ρ ≥ this with monotone terciles → inside-φ

CAVEATS = (
    "working half only; frozen scored once in E4",
    "book-held-out OOF posterior (alpha=0) — never the in-sample fit_full",
    "target det≠truth is mostly det-vs-PANEL (gate truth); only 21 human lines are ground truth",
    "AUC on the human ground-truth subset reported separately and is tiny-N (caveat, not a claim)",
)


# --- pure statistics (stdlib only — provable without sklearn/scipy) -----------------------------


def _rankdata(xs: list[float]) -> list[float]:
    """Average (mid) ranks, 1-based — shared by Spearman and the AUC rank-sum."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        mid = (i + j) / 2.0 + 1.0  # ranks are 1-based; average over the tie block
        for k in range(i, j + 1):
            ranks[order[k]] = mid
        i = j + 1
    return ranks


def roc_auc(scores: list[float], labels: list[int]) -> float | None:
    """ROC-AUC of `scores` ranking the positive class (`label==1`), via the rank-sum (Mann–Whitney)
    identity with tie-correction (mid-ranks). None when one class is absent (AUC undefined)."""
    pos = sum(labels)
    neg = len(labels) - pos
    if pos == 0 or neg == 0:
        return None
    ranks = _rankdata(scores)
    rank_sum_pos = sum(ranks[i] for i in range(len(labels)) if labels[i] == 1)
    return (rank_sum_pos - pos * (pos + 1) / 2.0) / (pos * neg)


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman's ρ (Pearson on mid-ranks) — None if either side is constant (ρ undefined)."""
    if len(xs) != len(ys):
        raise ValueError("spearman: length mismatch")
    rx, ry = _rankdata(xs), _rankdata(ys)
    n = len(rx)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx == 0.0 or vy == 0.0:
        return None
    return cov / (vx * vy) ** 0.5


def terciles(xs: list[float]) -> list[int]:
    """Assign each value its tercile 0|1|2 by its rank position (equal-count, so a skewed signal
    still yields three comparably sized bins). Ties land by the stable rank order."""
    n = len(xs)
    order = sorted(range(n), key=lambda i: (xs[i], i))
    out = [0] * n
    for pos, i in enumerate(order):
        out[i] = min(2, pos * 3 // n)
    return out


@dataclass(frozen=True, slots=True)
class TercileTable:
    """The 3×3 student-uncertainty × panel-disagreement tercile cross-tab and its off-diagonal mass —
    the monotonicity check behind the φ-fork verdict (a usable signal concentrates ON the diagonal)."""

    counts: list[list[int]]   # counts[u][d]: student-uncertainty tercile u × panel-disagree tercile d
    n: int

    @property
    def on_diagonal(self) -> int:
        return sum(self.counts[t][t] for t in range(3))

    @property
    def off_diagonal_frac(self) -> float:
        return (self.n - self.on_diagonal) / self.n if self.n else 0.0

    def to_dict(self) -> JsonObject:
        return {"counts": self.counts, "n": self.n,
                "on_diagonal": self.on_diagonal,
                "off_diagonal_frac": round(self.off_diagonal_frac, 4)}


def cross_terciles(u: list[float], d: list[float]) -> TercileTable:
    """Cross-tabulate two signals by their equal-count terciles."""
    tu, td = terciles(u), terciles(d)
    counts = [[0, 0, 0] for _ in range(3)]
    for a, b in zip(tu, td, strict=True):
        counts[a][b] += 1
    return TercileTable(counts=counts, n=len(u))


# --- the per-line signal substrate --------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LineSignals:
    """Every free signal for one working-half line, plus its target (`det ≠ truth`) and source.
    All signals are oriented so HIGHER = MORE SUSPECT (a candidate det error)."""

    id: LineId
    det: Tier0
    truth: Label
    source: str                  # gate | human (the truth's provenance)
    posterior: float             # book-held-out OOF P(lineated) — never the in-sample fit
    det_student_disagree: float  # posterior if det=prose else 1−posterior (pulls AGAINST det)
    student_uncertainty: float   # 1 − 2·|posterior−0.5|  (high = uncertain)
    suspicion_v0: float          # recon.suspicion_v0(det, mask, posterior)
    panel_vote_spread: float     # minority fraction across the readers (0 = unanimous)

    @property
    def is_error(self) -> int:
        return int((self.det is Tier0.LINEATED) != (self.truth == "lineated"))

    @property
    def student_margin(self) -> float:
        return abs(self.posterior - 0.5)

    def signal(self, name: str) -> float:
        return getattr(self, name)

    def to_dict(self) -> JsonObject:
        return {"id": self.id.as_key(), "det": self.det.value, "truth": self.truth,
                "source": self.source, "is_error": self.is_error,
                "posterior": round(self.posterior, 4),
                **{n: round(self.signal(n), 4) for n in SIGNALS}}


SIGNALS = ("det_student_disagree", "student_uncertainty", "suspicion_v0", "panel_vote_spread")


def _vote_spread(votes: tuple[PanelVote, ...]) -> float:
    """Minority fraction across one line's per-reader votes: 0 when the readers are unanimous, up to
    (R−1)/R at maximum split."""
    if not votes:
        return 0.0
    majority = max(Counter(v.label for v in votes).values())
    return (len(votes) - majority) / len(votes)


@dataclass(frozen=True, slots=True)
class SignalScore:
    """One signal's detector scorecard against `det ≠ truth`: AUC on all 747 (gate+human) and on the
    21 human-only ground-truth lines. The human AUC is the one that matters for the router choice —
    it is the only independent truth; AUC(all) is mostly det-vs-PANEL and can be gate-circular."""

    name: str
    auc_all: float | None
    auc_human: float | None

    def to_dict(self) -> JsonObject:
        return {"name": self.name, "auc_all": _r(self.auc_all), "auc_human": _r(self.auc_human)}


def _r(x: float | None) -> float | None:
    return round(x, 4) if x is not None else None


def score_signal(name: str, rows: list[LineSignals]) -> SignalScore:
    scores = [r.signal(name) for r in rows]
    labels = [r.is_error for r in rows]
    human = [(r.signal(name), r.is_error) for r in rows if r.source == LabelSource.HUMAN.value]
    auc_h = roc_auc([s for s, _ in human], [y for _, y in human]) if human else None
    return SignalScore(name=name, auc_all=roc_auc(scores, labels), auc_human=auc_h)


# --- the φ-fork ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PhiFork:
    """The inside/outside-φ resolution. `rho` = Spearman(student uncertainty, panel vote-spread):
    POSITIVE means more-uncertain lines are also more-contested by the panel — i.e. student
    uncertainty tracks panel disagreement, so it is a usable free acquisition signal. The
    pre-registered criterion: ρ ≥ +0.30 AND monotone terciles → INSIDE-φ; else OUTSIDE-φ."""

    rho: float | None
    table: TercileTable
    diag_monotone: bool       # the per-uncertainty-tercile modal disagreement-tercile is non-decreasing
    inside_phi: bool
    n: int

    def to_dict(self) -> JsonObject:
        return {"rho": _r(self.rho), "threshold": PHI_RHO_THRESHOLD,
                "tercile_table": self.table.to_dict(),
                "diag_monotone": self.diag_monotone,
                "verdict": "inside-phi" if self.inside_phi else "outside-phi", "n": self.n}


def _monotone_diag(table: TercileTable) -> bool:
    """The modal panel-disagreement tercile must not DECREASE as the student-uncertainty tercile
    rises — the weak monotonicity the criterion asks of the cross-tab."""
    modes = [max(range(3), key=lambda d: table.counts[u][d]) for u in range(3)]
    return all(modes[i] <= modes[i + 1] for i in range(2))


def resolve_fork(rows: list[LineSignals]) -> PhiFork:
    u = [r.student_uncertainty for r in rows]
    d = [r.panel_vote_spread for r in rows]
    rho = spearman(u, d)
    table = cross_terciles(u, d)
    mono = _monotone_diag(table)
    inside = rho is not None and rho >= PHI_RHO_THRESHOLD and mono
    return PhiFork(rho=rho, table=table, diag_monotone=mono, inside_phi=inside, n=len(rows))


# --- the experiment -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Bakeoff:
    """The E2 scorecard: the signal ranking, the φ-fork verdict, and the derived E3 router — the
    ORDERING of a whole-slice det=prose sweep, sized against the corpus recon."""

    n: int
    n_error: int
    n_human: int
    det_distribution: dict[str, int]
    signals: list[SignalScore]
    fork: PhiFork
    router: str
    router_rationale: str
    corpus_suspect_size: int
    corpus_basis: str
    rows: list[LineSignals]

    def to_dict(self) -> JsonObject:
        return {
            "set": WORKING_SET, "n": self.n, "n_error": self.n_error, "n_human": self.n_human,
            "det_distribution": self.det_distribution,
            "signals_ranked": [s.to_dict() for s in self.signals],
            "phi_fork": self.fork.to_dict(),
            "recommended_router": self.router,
            "router_rationale": self.router_rationale,
            "corpus_suspect_size": self.corpus_suspect_size,
            "corpus_basis": self.corpus_basis,
            "caveats": list(CAVEATS),
            "rows": [r.to_dict() for r in self.rows],
        }


# corpus recon counts the E3 sweep is sized against (E2 brief / recon-bilingual scorecard).
CORPUS_DET_PROSE = 69194           # the whole det=prose band E3 sweeps (ds-flash, ~$4)
CORPUS_DISAGREE_PROSE = 32090      # det=prose, OOF/fit posterior ≥ 0.5 — the ordering prioritizes these
CORPUS_DISAGREE_LINEATED = 11813   # det=lineated, posterior < 0.5 (audit-only)


def _oof_posteriors(work: set[LineId]) -> dict[LineId, float]:
    """Book-held-out OOF P(lineated) for the working lines, from a student fit on ALL trainable
    labels with each line's BOOK held out (alpha=0, the per-line i.i.d. score — same orientation as
    the corpus recon's unsmoothed `fit_full`, never the in-sample fit). FAILS LOUD if a working line
    is not trainable (it would have no OOF score — a frozen/holdout leak or a stale split)."""
    from .. import student
    from ..annotations import load_labels as _load

    labelset = _load()
    trainable_ids = {g.id for g in labelset.trainable}
    missing = work - trainable_ids
    if missing:
        raise AssertionError(
            f"{len(missing)} working line(s) are not trainable (no OOF score) — e.g. {sorted(missing)[0]}; "
            "a holdout/frozen leak or a stale working split")
    books = sorted({g.id.book_key for g in labelset.trainable})
    records = store.load_records_many(books)
    ds = student.build_dataset(records, labelset)
    decisions = student.oof_smoothed(ds, records, alpha=0.0)
    return {lid: decisions[lid].posterior for lid in work if lid in decisions}


def _det_and_mask(ids: set[LineId]) -> dict[LineId, tuple[Tier0, Mask]]:
    """Each line's tier-0 verdict and structural-seam mask, read off the live DOCX through the
    production importer (one DOCX read per (lang, book))."""
    from pancratius import docx_inspect as di

    out: dict[LineId, tuple[Tier0, Mask]] = {}
    for lang, book in sorted({(i.lang, i.book_id) for i in ids}):
        docx = paths.book_docx(book, lang)
        dec = di.lineation_decisions(docx)
        mask = {ordn: Mask(v.value) for ordn, v in di.votability_mask(docx).items()}
        for lid in ids:
            if lid.lang == lang and lid.book_id == book:
                hit = dec.get(lid.src_ordinal)
                det = Tier0.UNCOVERED if hit is None else (Tier0.LINEATED if hit else Tier0.PROSE)
                out[lid] = (det, mask.get(lid.src_ordinal, Mask.REVIEW))
    return out


def _build_rows(*, annotations: Path | None) -> list[LineSignals]:
    """Assemble the per-line signal substrate. FAILS LOUD on a frozen-half leak before any work."""
    work = {LineId.from_key(k) for k in store.load_eval_set(WORKING_SET, annotations=annotations)}
    frozen = {LineId.from_key(k) for k in store.load_eval_set(FROZEN_SET, annotations=annotations)}
    if work & frozen:
        raise AssertionError(f"working∩frozen = {len(work & frozen)} ids — the split leaks")

    truth = {g.id: (g.label, g.source.value)
             for g in load_labels(annotations=annotations).labels if g.id in work}
    posteriors = _oof_posteriors(work)
    det_mask = _det_and_mask(set(truth))

    votes_by_line: dict[LineId, list[PanelVote]] = {}
    for v in load_votes(annotations=annotations):
        if v.id in work:
            votes_by_line.setdefault(v.id, []).append(v)

    rows: list[LineSignals] = []
    for lid in sorted(truth):
        label, source = truth[lid]
        det, mask = det_mask[lid]
        post = posteriors[lid]
        votes = tuple(sorted(votes_by_line.get(lid, ()), key=lambda v: v.tag))
        disagree = post if det is Tier0.PROSE else 1.0 - post
        rows.append(LineSignals(
            id=lid, det=det, truth=label, source=source, posterior=post,
            det_student_disagree=disagree,
            student_uncertainty=1.0 - 2.0 * abs(post - 0.5),
            suspicion_v0=suspicion_v0(det, mask, post),
            panel_vote_spread=_vote_spread(votes)))
    return rows


HUMAN_COLLAPSE_FLOOR = 0.60  # AUC(human) at/below this on the 21 ground-truth lines reads as ~chance


def _robust_on_human(s: SignalScore) -> bool:
    """A signal usable as the ORDERING router must not collapse to ~chance on independent (human)
    truth. AUC(all) can be gate-circular (it is mostly det-vs-PANEL); only the human subset is
    independent, so a router-grade signal must clear the chance floor there."""
    return s.auc_human is not None and s.auc_human >= HUMAN_COLLAPSE_FLOOR


def _choose_router(scored: list[SignalScore], fork: PhiFork) -> tuple[str, str, int, str]:
    """DERIVE the E3 router (router, rationale, corpus suspect size, basis) from the evidence.

    E3 does NOT gate on a router — the whole det=prose band is sweepable with ds-flash, so the
    router only ORDERS that sweep (early-stop / human-queue priority). Ordering means the choice must
    be ROBUST on independent truth: a signal that wins AUC(all) but collapses to ~chance on the human
    subset (det_student_disagree, whose edge is just ranking det=lineated by 1−posterior) is
    gate-circular and disqualified, though it stays in the table as evidence. Among the signals robust
    on human truth, pick the strongest human-AUC."""
    auc_leader = max(scored, key=lambda s: (s.auc_all if s.auc_all is not None else -1.0))
    robust = [s for s in scored if _robust_on_human(s)]
    if not robust:
        raise AssertionError("no signal clears the human-AUC chance floor — cannot name a router")
    chosen = max(robust, key=lambda s: (s.auc_human if s.auc_human is not None else -1.0))

    circular = (f"(AUC(all)={_r(auc_leader.auc_all)} but AUC(human)={_r(auc_leader.auc_human)} "
                "— gate-circular, disqualified) "
                if auc_leader.name != chosen.name and not _robust_on_human(auc_leader) else "")
    fork_note = ("student uncertainty is a usable co-signal (inside-φ)" if fork.inside_phi
                 else "student uncertainty is audit-only (outside-φ)")
    router = (f"sweep the whole det=prose band; ORDER it by {chosen.name} "
              f"(robust on both gate AUC={_r(chosen.auc_all)} and human AUC={_r(chosen.auc_human)})")
    rationale = (f"E3 does not gate — it sweeps all {CORPUS_DET_PROSE} det=prose lines (ds-flash, "
                 f"~$4); the router only ORDERS the sweep. Chosen by robustness on independent "
                 f"truth: {chosen.name} (gate {_r(chosen.auc_all)} / human {_r(chosen.auc_human)}), "
                 f"NOT the AUC(all) leader {auc_leader.name} {circular}whose edge is gate-circular. "
                 f"{fork_note}.")
    suspect = CORPUS_DET_PROSE
    basis = (f"the whole det=prose band ({CORPUS_DET_PROSE}) is swept; the {chosen.name} ordering "
             f"prioritizes the {CORPUS_DISAGREE_PROSE} disagreement lines first. det=lineated "
             f"disagreement ({CORPUS_DISAGREE_LINEATED}) stays AUDIT-ONLY")
    return router, rationale, suspect, basis


def compute(*, annotations: Path | None = None) -> Bakeoff:
    rows = _build_rows(annotations=annotations)
    scored = sorted((score_signal(n, rows) for n in SIGNALS),
                    key=lambda s: (s.auc_all if s.auc_all is not None else -1.0), reverse=True)
    fork = resolve_fork(rows)
    router, rationale, suspect, basis = _choose_router(scored, fork)
    return Bakeoff(
        n=len(rows), n_error=sum(r.is_error for r in rows),
        n_human=sum(r.source == LabelSource.HUMAN.value for r in rows),
        det_distribution=dict(Counter(r.det.value for r in rows)),
        signals=scored, fork=fork, router=router, router_rationale=rationale,
        corpus_suspect_size=suspect, corpus_basis=basis, rows=rows)


# --- report -------------------------------------------------------------------------------------


def report(b: Bakeoff) -> str:
    lines = [
        f"# E2 — signal bakeoff + φ-fork ({WORKING_SET}, n={b.n})", "",
        f"Target: `det ≠ truth` on the working half = **{b.n_error}** lines "
        f"({b.det_distribution}). Truth is mostly `gate` (panel); only **{b.n_human}** are human "
        "ground truth. Posterior = book-held-out OOF (alpha=0), never the in-sample fit.", "",
        "## (b) Signal ranking — detectors of `det ≠ truth`",
        "Oriented so higher = more suspect. AUC(all) over 747 (mostly det-vs-PANEL, can be "
        f"gate-circular); AUC(human) over the {b.n_human} ground-truth lines — the only independent "
        "truth, and the one the router must hold up on:", "",
        "| signal | AUC(all) | AUC(human) |",
        "|---|---|---|",
    ]
    for s in b.signals:
        lines.append(f"| {s.name} | {_r(s.auc_all)} | {_r(s.auc_human)} |")
    top = b.signals[0]
    best_human = max(b.signals, key=lambda s: (s.auc_human if s.auc_human is not None else -1.0))
    lines.append("")
    lines.append(
        f"Note the all/human split: `{top.name}` tops AUC(all)={_r(top.auc_all)} but only "
        f"{_r(top.auc_human)} on the {b.n_human} human lines — its AUC(all) edge comes only from "
        "ranking det=lineated by 1−posterior, so it is GATE-CIRCULAR and collapses to ~chance where "
        f"truth is independent. `{best_human.name}` is robust on BOTH "
        f"(all {_r(best_human.auc_all)} / human {_r(best_human.auc_human)}), so it — not the AUC(all) "
        "leader — orders the sweep.")
    f = b.fork
    lines += [
        "",
        "## (c) The inside/outside-φ fork",
        f"Spearman(student uncertainty, panel vote-spread) = **{_r(f.rho)}** "
        f"(criterion ρ ≥ +{PHI_RHO_THRESHOLD}); terciles monotone: **{f.diag_monotone}**; "
        f"off-diagonal mass {f.table.off_diagonal_frac:.3f}.",
        f"Tercile cross-tab (rows = uncertainty 0..2, cols = vote-spread 0..2): "
        f"{f.table.counts}",
        f"**Verdict: {'INSIDE-φ' if f.inside_phi else 'OUTSIDE-φ'}** — "
        + ("student uncertainty tracks panel disagreement; usable as a free acquisition signal."
           if f.inside_phi else
           "student uncertainty does NOT track panel disagreement; it stays audit-only."),
        "",
        "## Recommended E3 router",
        f"**{b.router}**",
        f"- {b.router_rationale}",
        f"- corpus sweep ≈ **{b.corpus_suspect_size}** lines "
        f"({b.corpus_basis}).",
        "",
        "## Caveats", *(f"- {c}" for c in CAVEATS),
    ]
    return "\n".join(lines) + "\n"


EXPERIMENT_ID = "2026-06-13-e2-signal-bakeoff"


if __name__ == "__main__":
    b = compute()
    print(report(b))
    folder = store.write_experiment(
        EXPERIMENT_ID, scorecard=b.to_dict(), report=report(b),
        manifest={
            "git_sha": store.git_sha(),
            "labels_sha256": store.sha256_file(paths.ANNOTATIONS / store.LABELS_FILE),
            "votes_sha256": store.sha256_file(paths.ANNOTATIONS / "votes.jsonl"),
            "working_set": WORKING_SET, "frozen_set_excluded": FROZEN_SET,
            "posterior": "student.oof_smoothed alpha=0 over ALL trainable labels (book-held-out)",
            "signals": list(SIGNALS),
            "phi_rho_threshold": PHI_RHO_THRESHOLD,
            "corpus_recon": "2026-06-13-recon-bilingual",
        })
    print(f"\nwrote {folder}")
