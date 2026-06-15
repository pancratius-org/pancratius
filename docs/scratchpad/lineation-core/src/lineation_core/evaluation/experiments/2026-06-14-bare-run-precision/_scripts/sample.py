"""Stratified sample of bare-run flip-runs for independent classification.

Strata: book lineated_pct bucket {verse>=0.7, mid 0.3-0.7, prose<0.3}
        x run-length bucket {6-8 (near floor), 9-20, 21+}.

Allocation: ~70 runs. Base allocation proportional to each stratum's LINE mass,
then risk strata OVERSAMPLED: any prose<0.3 stratum and any 6-8 length stratum
gets a floor so they are well-estimated. Each run carries a sampling weight =
(stratum population runs) / (stratum sampled runs) so per-run and line-weighted
rates can be reweighted to the population.

Deterministic: a fixed seed (0) drives random.Random; no wall-clock, no global
random. Within a stratum runs are shuffled by a seeded RNG and the first k taken.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parent.parent
SEED = 0
TARGET_N = 70

PCT_BUCKETS = ("verse", "mid", "prose")
LEN_BUCKETS = ("6-8", "9-20", "21+")


def pct_bucket(p: float | None) -> str:
    if p is None:
        return "mid"  # unknown book pct -> treat as mid (none expected)
    if p >= 0.7:
        return "verse"
    if p >= 0.3:
        return "mid"
    return "prose"


def len_bucket(n: int) -> str:
    if n <= 8:
        return "6-8"
    if n <= 20:
        return "9-20"
    return "21+"


def main() -> None:
    pop = json.loads((EXP_DIR / "population.json").read_text())
    runs = pop["runs"]
    for i, r in enumerate(runs):
        r["pop_idx"] = i
        r["pct_bucket"] = pct_bucket(r["book_lineated_pct"])
        r["len_bucket"] = len_bucket(r["n_lines"])
        r["stratum"] = f"{r['pct_bucket']}|{r['len_bucket']}"

    # group
    strata: dict[str, list[dict]] = {}
    for r in runs:
        strata.setdefault(r["stratum"], []).append(r)

    total_lines = sum(r["n_lines"] for r in runs)

    # base proportional-to-line-mass allocation
    alloc: dict[str, int] = {}
    for s, rs in strata.items():
        lines = sum(r["n_lines"] for r in rs)
        alloc[s] = round(TARGET_N * lines / total_lines)

    # risk oversample: prose<0.3 (any len) and any 6-8 len bucket get a floor
    for s, rs in strata.items():
        pct_b, len_b = s.split("|")
        is_risk = pct_b == "prose" or len_b == "6-8"
        floor = 8 if is_risk else 0
        # never sample more runs than exist in the stratum
        want = max(alloc[s], floor)
        alloc[s] = min(want, len(rs))
        # ensure at least 1 from any non-empty stratum
        if rs and alloc[s] == 0:
            alloc[s] = min(2, len(rs))

    # draw deterministically
    rng = random.Random(SEED)
    sample: list[dict] = []
    plan: list[dict] = []
    for s in sorted(strata):
        rs = sorted(strata[s], key=lambda r: r["pop_idx"])  # stable order
        k = alloc[s]
        idxs = list(range(len(rs)))
        rng.shuffle(idxs)
        picked = sorted(idxs[:k])
        weight = len(rs) / k if k else 0.0
        for j in picked:
            r = dict(rs[j])
            r["sampling_weight"] = weight
            r["stratum_pop_runs"] = len(rs)
            r["stratum_pop_lines"] = sum(x["n_lines"] for x in rs)
            r["stratum_sampled_runs"] = k
            sample.append(r)
        plan.append(
            {
                "stratum": s,
                "pop_runs": len(rs),
                "pop_lines": sum(x["n_lines"] for x in rs),
                "sampled_runs": k,
                "weight": round(weight, 4),
            }
        )

    sample.sort(key=lambda r: (r["lang"], r["book"], r["ord_lo"]))
    out = {
        "seed": SEED,
        "target_n": TARGET_N,
        "n_sampled": len(sample),
        "total_pop_runs": len(runs),
        "total_pop_lines": total_lines,
        "allocation": plan,
        "sample": sample,
    }
    (EXP_DIR / "sample.json").write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"sampled {len(sample)} runs across {len([p for p in plan if p['sampled_runs']])} strata")
    for p in plan:
        print(
            f"  {p['stratum']:14s} pop_runs={p['pop_runs']:5d} pop_lines={p['pop_lines']:6d} "
            f"sampled={p['sampled_runs']:3d} w={p['weight']}"
        )


if __name__ == "__main__":
    main()
