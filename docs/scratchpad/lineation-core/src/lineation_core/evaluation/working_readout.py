# research-pure: the E1 working-half readout — det verdict ⋈ committed truth, both directions.
"""How wrong is the importer, and in which direction? Joins the deterministic tier-0 verdict to the
committed truth on the E1 WORKING half only, both error directions, stratified by truth source and
language. The diagnostic that SIZES E3: `P(truth=lineated | det=prose)` (the importer's weak side)
and `P(truth=prose | det=lineated)` (assumed near-free; rechecked here).

Guardrails the readout depends on:
  - WORKING HALF ONLY — the frozen acceptance half is scored once in E4; FAILS LOUD on a frozen leak.
  - GATE TRUTH IS NOT INDEPENDENT — most working truth is `gate` (the panel's own verdict), so a
    det⊕gate disagreement is det-vs-PANEL, a candidate error, never a proven det error. Stratified by
    source so the circularity stays visible; only the (small) `human` subset is ground truth. No
    "gate accuracy" is claimed.
  - NO STUDENT POSTERIOR — the det/truth rates need none; student-signal eval is E2 (held-out).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .. import paths, store
from ..annotations import load_labels
from ..identity import JsonObject, Label, LineId
from ..recon import Tier0

WORKING_SET = "e1-instrument-working"
FROZEN_SET = "e1-instrument-frozen"

CAVEATS = (
    "working half only; frozen scored once in E4",
    "gate truth = panel verdict (not independent); only human is ground truth",
    "no student posterior — det/truth rates only",
)


@dataclass(frozen=True, slots=True)
class Cell:
    """Of `n` lines carrying a given det verdict (within some slice), `k` had the truth label asked
    about — so `rate` = P(that truth | that det) on the slice, None when the slice is empty."""

    k: int
    n: int

    @property
    def rate(self) -> float | None:
        return self.k / self.n if self.n else None

    def to_dict(self) -> JsonObject:
        return {"k": self.k, "n": self.n, "rate": self.rate}


@dataclass(frozen=True, slots=True)
class Directional:
    """One direction's P(truth | det) — overall, and the same stratified by truth source and lang."""

    overall: Cell
    by_source: dict[str, Cell]
    by_lang: dict[str, Cell]

    def to_dict(self) -> JsonObject:
        return {"overall": self.overall.to_dict(),
                "by_source": {s: c.to_dict() for s, c in self.by_source.items()},
                "by_lang": {ln: c.to_dict() for ln, c in self.by_lang.items()}}


@dataclass(frozen=True, slots=True)
class Readout:
    """The working-half readout: both error directions + the disagreement count that sizes E3."""

    n: int
    det_distribution: dict[str, int]
    truth_source: dict[str, int]
    weak_side: Directional        # P(truth=lineated | det=prose) — verse the importer missed
    over_lineated: Directional    # P(truth=prose | det=lineated) — the "free" direction, rechecked

    @property
    def disagreement(self) -> int:
        return self.weak_side.overall.k + self.over_lineated.overall.k

    def to_dict(self) -> JsonObject:
        return {
            "set": WORKING_SET, "n": self.n,
            "det_distribution": self.det_distribution, "truth_source": self.truth_source,
            "p_lineated_given_det_prose": self.weak_side.to_dict(),
            "p_prose_given_det_lineated": self.over_lineated.to_dict(),
            "disagreement_lines": {"verse_missed": self.weak_side.overall.k,
                                   "over_lineated": self.over_lineated.overall.k,
                                   "total": self.disagreement},
            "caveats": list(CAVEATS),
        }


def _det_by_line(ids: set[LineId]) -> dict[LineId, Tier0]:
    """Each line's tier-0 importer verdict, off the live DOCX through the production importer
    (per-<w:p> ordinal; sub-lines share their paragraph's verdict)."""
    from pancratius import docx_inspect as di

    out: dict[LineId, Tier0] = {}
    for lang, book in sorted({(i.lang, i.book_id) for i in ids}):
        dec = di.lineation_decisions(paths.book_docx(book, lang))
        for lid in ids:
            if lid.lang == lang and lid.book_id == book:
                hit = dec.get(lid.src_ordinal)
                out[lid] = Tier0.UNCOVERED if hit is None else (Tier0.LINEATED if hit else Tier0.PROSE)
    return out


def compute(*, annotations: Path | None = None) -> Readout:
    """Build the readout. FAILS LOUD on a frozen-half leak before computing anything."""
    work = {LineId.from_key(k) for k in store.load_eval_set(WORKING_SET, annotations=annotations)}
    frozen = {LineId.from_key(k) for k in store.load_eval_set(FROZEN_SET, annotations=annotations)}
    if work & frozen:
        raise AssertionError(f"working∩frozen = {len(work & frozen)} ids — the split leaks")

    truth: dict[LineId, tuple[Label, str]] = {
        g.id: (g.label, g.source.value)
        for g in load_labels(annotations=annotations).labels if g.id in work}
    # eval_sets are MEMBERSHIP; a member with no committed label FAILS LOUD — never a silently
    # smaller denominator (the same contract `datasets.eval_slice` enforces).
    missing = work - set(truth)
    if missing:
        raise ValueError(
            f"{WORKING_SET} has {len(missing)} member line(s) with no label in labels.jsonl "
            f"(e.g. {sorted(missing)[:3]}) — promote their labels or fix the membership")
    ids = set(truth)
    det = _det_by_line(ids)

    # Strata keys come FROM the data, never a hardcoded list — so a new truth source (panel/override/
    # transfer) or a third language can't be counted in `n` yet silently dropped from every stratum.
    sources = sorted({s for _, s in truth.values()})
    langs = sorted({i.lang for i in ids})

    def cell(verdict: Tier0, want: Label, *, source: str | None = None, lang: str | None = None) -> Cell:
        sl = [i for i in ids if det[i] is verdict
              and (source is None or truth[i][1] == source)
              and (lang is None or i.lang == lang)]
        return Cell(k=sum(truth[i][0] == want for i in sl), n=len(sl))

    def directional(verdict: Tier0, want: Label) -> Directional:
        d = Directional(overall=cell(verdict, want),
                        by_source={s: cell(verdict, want, source=s) for s in sources},
                        by_lang={ln: cell(verdict, want, lang=ln) for ln in langs})
        # tripwire: the strata must PARTITION the verdict's lines, or a denominator is silently wrong.
        assert sum(c.n for c in d.by_source.values()) == d.overall.n == \
            sum(c.n for c in d.by_lang.values()), f"strata don't partition det={verdict}"
        return d

    return Readout(
        n=len(ids),
        det_distribution=dict(Counter(v.value for v in det.values())),
        truth_source=dict(Counter(s for _, s in truth.values())),
        weak_side=directional(Tier0.PROSE, "lineated"),
        over_lineated=directional(Tier0.LINEATED, "prose"))


def _fmt(d: Directional) -> str:
    def r(c: Cell) -> str:
        return f"{c.k}/{c.n} = {c.rate:.3f}" if c.rate is not None else f"{c.k}/{c.n}"
    src = "  ".join(f"{s} {r(c)}" for s, c in d.by_source.items())
    lang = "  ".join(f"{ln} {r(c)}" for ln, c in d.by_lang.items())
    return f"{r(d.overall)}  |  {src}  |  {lang}"


def report(r: Readout) -> str:
    return "\n".join([
        f"# E1 working-half readout — {WORKING_SET} (n={r.n})", "",
        f"det: {r.det_distribution}   truth source: {r.truth_source}", "",
        "## How wrong is the importer, by direction",
        f"P(truth=lineated | det=prose)  [the weak side] : {_fmt(r.weak_side)}",
        f"P(truth=prose | det=lineated)  [assumed free]  : {_fmt(r.over_lineated)}", "",
        f"det⊕truth disagreement (E2 router target): {r.disagreement} "
        f"({r.weak_side.overall.k} verse-missed + {r.over_lineated.overall.k} over-lineated)", "",
        "## Caveats", *(f"- {c}" for c in CAVEATS),
    ]) + "\n"


def _eval_set_path(name: str) -> Path:
    return paths.ANNOTATIONS / "eval_sets" / f"{name}.json"


if __name__ == "__main__":
    from . import datasets

    r = compute()
    print(report(r))
    folder = store.write_experiment(
        "2026-06-14-working-readout", scorecard=r.to_dict(), report=report(r),
        manifest={
            # +dirty here is unrelated data/ graph files, not these modules — left as-is.
            "git_sha": store.git_sha(),
            "labels_sha256": store.sha256_file(paths.ANNOTATIONS / store.LABELS_FILE),
            # SPEC: a study manifest pins the membership sha AND the scored-truth sha.
            "working_set": WORKING_SET, "frozen_set_excluded": FROZEN_SET,
            "working_set_sha256": store.sha256_file(_eval_set_path(WORKING_SET)),
            "frozen_set_sha256": store.sha256_file(_eval_set_path(FROZEN_SET)),
            "truth_sha256": datasets.truth_fingerprint(datasets.eval_slice(WORKING_SET)),
        })
    print(f"\nwrote {folder}")
