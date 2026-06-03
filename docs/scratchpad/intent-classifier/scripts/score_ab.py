# research-only: brief-fix A/B — does the "lineated prose" brief lift the hard lineated regions
# WITHOUT degrading the genuine-prose guardrails? Baseline = current brief; treatment = _lp brief.
from __future__ import annotations

import argparse
import json
from pathlib import Path

import bench_models as bm

DATA = bm.DATA / "phaseb"
KEEPERS = ["grok", "gemini-pro", "ds-flash-text"]
# target regions (lineated-by-intent; should gain lineated-recall) vs guardrails (genuine prose)
TARGET = ["g00_b64_t2", "g29_b69_t0", "g05_b37", "g18_b60_t3"]
GUARD = ["g09_b16_t2", "g23_b17", "g10_b19"]


def load(p: Path) -> dict:
    if not p.exists():
        return {}
    return {(bm.book_of(r["rid"]), r["idx"], r["sub"]): r["label"]
            for r in (json.loads(x) for x in p.read_text().splitlines() if x.strip())}


def keys_for(rids: list, truth: dict, pkg: dict, lab_wanted: str) -> list:
    out = []
    for rid in rids:
        if rid not in pkg:
            continue
        for k in pkg[rid]["keys"]:
            key = (bm.book_of(rid), k[0], k[1])
            if key in truth and truth[key] == lab_wanted:
                out.append(key)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suffix", default="_lp", help="treatment file suffix (e.g. _lp, _lp2)")
    sfx = ap.parse_args().suffix
    truth = bm.load_truth()
    pkg = {e["rid"]: e for e in json.loads((DATA / "reader_pkg.json").read_text())}
    tgt = keys_for(TARGET, truth, pkg, "lineated")    # want these labeled LINEATED
    grd = keys_for(GUARD, truth, pkg, "prose")         # want these to STAY prose
    print(f"target (lineated-prose) lines: {len(tgt)} | prose guardrail lines: {len(grd)}\n")
    print(f"{'reader':14} | target lineated-recall  | guardrail prose-recall  [treatment {sfx}]")
    print(f"{'':14} | base -> trt   (delta)   | base -> trt   (delta)")
    for t in KEEPERS:
        base = load(DATA / "bench" / f"reader_{t}.jsonl")
        lp = load(DATA / "bench" / f"reader_{t}{sfx}.jsonl")
        if not base or not lp:
            print(f"{t:14} | (missing run)")
            continue

        def rec(keys: list, lab: str, d: dict) -> float:
            kk = [k for k in keys if k in d]
            return sum(d[k] == lab for k in kk) / len(kk) if kk else float("nan")
        tb, tl = rec(tgt, "lineated", base), rec(tgt, "lineated", lp)
        gb, gl = rec(grd, "prose", base), rec(grd, "prose", lp)
        print(f"{t:14} | {tb:>4.0%} ->{tl:>4.0%}  ({tl - tb:>+4.0%})  | "
              f"{gb:>4.0%} ->{gl:>4.0%}  ({gl - gb:>+4.0%})")
    print("\nWANT: target lineated-recall UP, guardrail prose-recall FLAT (no degradation). "
          "Single run each — read cross-model CONSISTENCY, not any one number (~19% run noise).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
