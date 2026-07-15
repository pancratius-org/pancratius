"""Estimate the bare-run FALSE-FOLD rate with Wilson 95% bounds.

Joins the independent labels onto the stratified sample, then:
 - per-run rate (unweighted and population-reweighted by sampling weights);
 - line-weighted rate (each run weighted by its n_lines, reweighted to population);
 - Wilson 95% intervals;
 - per-stratum rates;
 - E1-truth cross-check: E1-instrument labelled LineIds whose ordinal falls inside
   any flip-run, and the false-fold count among them (truth-grounded), with Wilson.

A 'false fold' counts verdict=='prose'. 'ambiguous' is treated two ways:
counted as NOT-false (lower bound on false-fold) and as false (conservative upper).
The ship threshold is line-weighted false-fold upper bound <= 2%.

Writes scorecard.json into the experiment dir.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from lineation_core import paths
from lineation_core.annotations import load_labels

EXP_DIR = Path(__file__).resolve().parent.parent


def wilson(k: float, n: float, z: float = 1.959963984540054) -> tuple[float, float, float]:
    """Wilson score interval (point, lo, hi) for k successes in n trials.
    Accepts fractional k/n (effective counts) for weighted estimates."""
    if n <= 0:
        return (0.0, 0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (p, max(0.0, centre - half), min(1.0, centre + half))


def weighted_wilson(
    items: list[tuple[float, bool]],
) -> tuple[float, float, float, float]:
    """Design-weighted proportion with an effective-sample-size Wilson interval.
    items = list of (weight, is_false). Returns (point, lo, hi, n_eff)."""
    W = sum(w for w, _ in items)
    if W <= 0:
        return (0.0, 0.0, 1.0, 0.0)
    p = sum(w for w, f in items if f) / W
    # Kish effective sample size
    n_eff = W * W / sum(w * w for w, _ in items)
    _, lo, hi = wilson(p * n_eff, n_eff)
    return (p, lo, hi, n_eff)


def main() -> None:
    sample = json.loads((EXP_DIR / "sample.json").read_text())["sample"]
    labels = json.loads((EXP_DIR / "_scripts" / "labels.json").read_text())["labels"]

    # attach verdicts by sample order (labels keyed by sample index after sort)
    # sample.json is sorted by (lang, book, ord_lo); the render manifest used the
    # same sort, and labels are keyed 0..n-1 in that order.
    for i, r in enumerate(sample):
        lab = labels[str(i)]
        r["verdict"] = lab["verdict"]
        r["why"] = lab["why"]
        r["false_lo"] = lab["verdict"] == "prose"  # ambiguous NOT false
        r["false_hi"] = lab["verdict"] in ("prose", "ambiguous")  # conservative

    def estimates(false_key: str) -> dict:
        per_run = weighted_wilson([(r["sampling_weight"], r[false_key]) for r in sample])
        line_w = weighted_wilson(
            [(r["sampling_weight"] * r["n_lines"], r[false_key]) for r in sample]
        )
        # also unweighted (sample-level) for transparency
        k_run = sum(1 for r in sample if r[false_key])
        un_run = wilson(k_run, len(sample))
        return {
            "per_run_weighted": {"rate": per_run[0], "lo": per_run[1], "hi": per_run[2], "n_eff": per_run[3]},
            "line_weighted": {"rate": line_w[0], "lo": line_w[1], "hi": line_w[2], "n_eff": line_w[3]},
            "per_run_unweighted": {"k": k_run, "n": len(sample), "rate": un_run[0], "lo": un_run[1], "hi": un_run[2]},
        }

    est_lo = estimates("false_lo")  # ambiguous counted as verse
    est_hi = estimates("false_hi")  # ambiguous counted as prose (conservative)

    # per-stratum (population-reweighted line-weighted rate, conservative band)
    strata: dict[str, list[dict]] = {}
    for r in sample:
        strata.setdefault(r["stratum"], []).append(r)
    per_stratum = {}
    for s, rs in sorted(strata.items()):
        lo = weighted_wilson([(r["n_lines"], r["false_lo"]) for r in rs])
        hi = weighted_wilson([(r["n_lines"], r["false_hi"]) for r in rs])
        per_stratum[s] = {
            "n_runs": len(rs),
            "n_lines": sum(r["n_lines"] for r in rs),
            "false_lo_lineweighted": lo[0],
            "false_hi_lineweighted": hi[0],
            "verdicts": {
                v: sum(1 for r in rs if r["verdict"] == v)
                for v in ("verse", "prose", "ambiguous")
            },
        }

    confirmed_prose = [
        {"idx": sample.index(r), "lang": r["lang"], "book": r["book"],
         "ord_lo": r["ord_lo"], "ord_hi": r["ord_hi"], "n_lines": r["n_lines"],
         "stratum": r["stratum"], "why": r["why"]}
        for r in sample if r["verdict"] == "prose"
    ]
    ambiguous = [
        {"lang": r["lang"], "book": r["book"], "ord_lo": r["ord_lo"], "ord_hi": r["ord_hi"],
         "n_lines": r["n_lines"], "stratum": r["stratum"]}
        for r in sample if r["verdict"] == "ambiguous"
    ]

    # ---- E1 truth cross-check ----
    pop = json.loads((EXP_DIR / "population.json").read_text())["runs"]
    # index population runs by (lang, book) -> list of (lo, hi)
    by_book: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for r in pop:
        by_book.setdefault((r["lang"], r["book"]), []).append((r["ord_lo"], r["ord_hi"]))
    ls = load_labels()
    hits = []
    for lab in ls.labels:
        key = (lab.id.lang, lab.id.book_id)
        for lo, hi in by_book.get(key, []):
            if lo <= lab.id.src_ordinal <= hi:
                hits.append({
                    "id": f"{lab.id.lang}:{lab.id.book_id}:{lab.id.src_ordinal}",
                    "truth": lab.label,
                    "in_run": [lo, hi],
                    "provenance_task": lab.provenance.get("task") if isinstance(lab.provenance, dict) else None,
                })
                break
    n_truth = len(hits)
    n_false_truth = sum(1 for h in hits if h["truth"] == "prose")
    truth_wilson = wilson(n_false_truth, n_truth) if n_truth else (0.0, 0.0, 1.0)

    out = {
        "population": {"n_runs": pop.__len__(), "n_lines": sum(r["n_lines"] for r in pop)},
        "sample": {"n_runs": len(sample), "n_lines": sum(r["n_lines"] for r in sample)},
        "ship_threshold_line_weighted_upper": 0.02,
        "estimate_ambiguous_as_verse": est_lo,
        "estimate_ambiguous_as_prose_conservative": est_hi,
        "per_stratum": per_stratum,
        "confirmed_false_folds": confirmed_prose,
        "ambiguous_runs": ambiguous,
        "e1_truth_crosscheck": {
            "n_e1_labels_in_flip_runs": n_truth,
            "n_false_fold_truth_prose": n_false_truth,
            "rate": truth_wilson[0],
            "wilson_lo": truth_wilson[1],
            "wilson_hi": truth_wilson[2],
            "hits": hits,
        },
        "verdict_distribution": {
            v: sum(1 for r in sample if r["verdict"] == v)
            for v in ("verse", "prose", "ambiguous")
        },
        "labeled_sample": [
            {"idx": i, "lang": r["lang"], "book": r["book"], "ord_lo": r["ord_lo"],
             "ord_hi": r["ord_hi"], "n_lines": r["n_lines"], "mean_len": r["mean_len"],
             "max_len": r["max_len"], "book_lineated_pct": r["book_lineated_pct"],
             "stratum": r["stratum"], "sampling_weight": r["sampling_weight"],
             "stratum_pop_runs": r["stratum_pop_runs"], "stratum_pop_lines": r["stratum_pop_lines"],
             "verdict": r["verdict"], "why": r["why"]}
            for i, r in enumerate(sample)
        ],
    }
    (EXP_DIR / "scorecard.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))

    def fmt(d):
        return f"{d['rate']*100:.2f}% [{d['lo']*100:.2f}, {d['hi']*100:.2f}]"

    print("=== BARE-RUN FALSE-FOLD ESTIMATE ===")
    print(f"population: {out['population']['n_runs']} runs / {out['population']['n_lines']} lines")
    print(f"sample: {len(sample)} runs / {out['sample']['n_lines']} lines")
    print(f"verdicts: {out['verdict_distribution']}")
    print("\n-- ambiguous treated as VERSE (lower bound on false-fold) --")
    print("  per-run (weighted):  " + fmt(est_lo["per_run_weighted"]))
    print("  line-weighted:       " + fmt(est_lo["line_weighted"]))
    print("\n-- ambiguous treated as PROSE (conservative upper bound) --")
    print("  per-run (weighted):  " + fmt(est_hi["per_run_weighted"]))
    print("  line-weighted:       " + fmt(est_hi["line_weighted"]))
    print("\n-- per stratum (line-weighted false-fold lo..hi point) --")
    for s, d in per_stratum.items():
        print(f"  {s:14s} runs={d['n_runs']:2d} lines={d['n_lines']:5d}  "
              f"false {d['false_lo_lineweighted']*100:.1f}..{d['false_hi_lineweighted']*100:.1f}%  {d['verdicts']}")
    print(f"\nE1 truth cross-check: {n_false_truth}/{n_truth} truth=prose in flip-runs  "
          f"Wilson [{truth_wilson[1]*100:.1f},{truth_wilson[2]*100:.1f}]%")
    print(f"\nconfirmed false folds: {[(c['lang'],c['book'],c['ord_lo'],c['ord_hi']) for c in confirmed_prose]}")
    print(f"ambiguous runs: {len(ambiguous)}")

    lw_hi = est_hi["line_weighted"]["hi"]
    lw_lo_hi = est_lo["line_weighted"]["hi"]
    print(f"\nSHIP (line-weighted upper <= 2%)?  ambiguous=verse: upper={lw_lo_hi*100:.2f}%  "
          f"ambiguous=prose: upper={lw_hi*100:.2f}%")


if __name__ == "__main__":
    main()
