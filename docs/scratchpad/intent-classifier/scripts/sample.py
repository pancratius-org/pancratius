# research-pure: reads the scratch feature table; writes only to the scratch dir.
"""Stratified window sampler for gold adjudication.

The decision is about RUNS, so we adjudicate WINDOWS (a paragraph ± context) and
label every content paragraph inside. Anchors are drawn per book across strata so
the gold set covers the true distribution AND over-samples the ambiguous middle:

  random   — a uniformly chosen content paragraph (unbiased per-book coverage; the
             honest test distribution).
  hard_run — a short non-wrapping line in a SHORT run (run_len 2..6): the
             litany-vs-short-prose ambiguity.
  isolated — a short non-wrapping line alone amid prose (run_len==1): likely a short
             prose sentence, the false-positive trap.
  boundary — fill in [0.85, 1.05]: the wrap/no-wrap knife edge.
  easyverse— inside a long run (run_len>=12): confident lineation (calibration).
  easyprose— a clear wrap (fill>=1.6): confident prose (calibration).

Windows are clipped to ±RADIUS and de-duplicated by overlap within a book. Output:
`data/gold_windows.json`.
"""
from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
RADIUS = 8


def load_rows() -> dict[str, list[dict]]:
    by_book: dict[str, list[dict]] = defaultdict(list)
    for line in (DATA / "features.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        if r["source"] == "book":
            by_book[r["key"]].append(r)
    for k in by_book:
        by_book[k].sort(key=lambda r: r["idx"])
    return by_book


def content(r: dict) -> bool:
    return not (r["empty"] or r["heading"] or r["thematic"] or r["numbered"])


def strata_for(r: dict) -> list[str]:
    out: list[str] = []
    if not content(r):
        return out
    if not r["wraps"]:
        rl = r["run_len"]
        if rl == 1:
            out.append("isolated")
        elif 2 <= rl <= 6:
            out.append("hard_run")
        elif rl >= 12:
            out.append("easyverse")
        if 0.85 <= r["fill"] <= 1.05:
            out.append("boundary")
    elif r["fill"] >= 1.6:
        out.append("easyprose")
    return out


# per-book anchor quota by stratum (caps total work; ambiguity over-sampled)
QUOTA = {"random": 1, "hard_run": 2, "isolated": 1, "boundary": 1,
         "easyverse": 0, "easyprose": 0}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=20260529)
    ap.add_argument("--out", default=str(DATA / "gold_windows.json"))
    args = ap.parse_args(argv)
    rng = random.Random(args.seed)

    by_book = load_rows()
    windows: list[dict] = []
    for key in sorted(by_book):
        rows = by_book[key]
        idx_of = {r["idx"]: i for i, r in enumerate(rows)}
        pools: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            for s in strata_for(r):
                pools[s].append(r)
        pools["random"] = [r for r in rows if content(r)]
        chosen: list[tuple[str, int]] = []
        for stratum, q in QUOTA.items():
            pool = pools.get(stratum, [])
            if not pool:
                continue
            picks = rng.sample(pool, min(q, len(pool)))
            chosen += [(stratum, p["idx"]) for p in picks]
        # de-dup by overlapping windows (keep first; prefer harder strata order)
        order = {s: i for i, s in enumerate(
            ["hard_run", "isolated", "boundary", "easyverse", "easyprose", "random"])}
        chosen.sort(key=lambda si: order.get(si[0], 99))
        used: list[tuple[int, int]] = []
        for stratum, aidx in chosen:
            i = idx_of[aidx]
            lo = max(0, i - RADIUS)
            hi = min(len(rows) - 1, i + RADIUS)
            lo_idx, hi_idx = rows[lo]["idx"], rows[hi]["idx"]
            if any(not (hi_idx < a or lo_idx > b) for a, b in used):
                continue
            used.append((lo_idx, hi_idx))
            windows.append({
                "key": key, "anchor_idx": aidx, "stratum": stratum,
                "lo": lo_idx, "hi": hi_idx,
            })
    Path(args.out).write_text(json.dumps(windows, ensure_ascii=False, indent=1))
    from collections import Counter
    c = Counter(w["stratum"] for w in windows)
    print(f"{len(windows)} windows over {len(by_book)} books")
    for s, n in c.most_common():
        print(f"  {s}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
