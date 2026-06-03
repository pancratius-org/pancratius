# research-only: few-shot policy A/B with 3-run majority aggregation (single-pass noise is real).
"""Does a few-shot policy prompt (concrete human-adjudicated examples + counterexamples) teach the
lineated-prose boundary, or — like the abstract v1/v2 briefs — only slide the threshold?
Aggregates 3 runs per (reader, prompt) by majority. PASS = target lineated-recall up >=10pp AND
prose guardrail down <=5pp, consistent across readers. g23 held out as an example (guardrail=g09+g10)."""
from __future__ import annotations

import json
from pathlib import Path

import bench_models as bm

DATA = bm.DATA / "phaseb" / "bench"
READERS = ["grok", "gemini-pro", "glm", "ds-flash-text"]
TARGET = ["g00_b64_t2", "g29_b69_t0", "g05_b37", "g18_b60_t3"]   # lineated-by-intent
GUARD = ["g09_b16_t2", "g10_b19"]                                # genuine prose (g23 held out)
RUNS = ["1", "2", "3"]


def load(p: Path) -> dict:
    if not p.exists():
        return {}
    return {(bm.book_of(r["rid"]), r["idx"], r["sub"]): r["label"]
            for r in (json.loads(x) for x in p.read_text().splitlines() if x.strip())}


def majority3(reader: str, prefix: str) -> dict:
    """Per-line majority label over the 3 runs reader_<reader>_<prefix><run>.jsonl."""
    runs = [load(DATA / f"reader_{reader}_{prefix}{i}.jsonl") for i in RUNS]
    out, keys = {}, set().union(*[set(r) for r in runs]) if runs else set()
    for k in keys:
        votes = [r[k] for r in runs if k in r]
        if votes:
            out[k] = "lineated" if votes.count("lineated") > len(votes) / 2 else "prose"
    return out


def keys_for(rids: list, truth: dict, pkg: dict, want: str) -> list:
    return [(bm.book_of(r), k[0], k[1]) for r in rids if r in pkg for k in pkg[r]["keys"]
            if (bm.book_of(r), k[0], k[1]) in truth and truth[(bm.book_of(r), k[0], k[1])] == want]


def main() -> int:
    truth = bm.load_truth()
    pkg = {e["rid"]: e for e in json.loads((bm.DATA / "phaseb/reader_pkg.json").read_text())}
    tgt = keys_for(TARGET, truth, pkg, "lineated")
    grd = keys_for(GUARD, truth, pkg, "prose")
    print(f"3-run majority | target lineated lines={len(tgt)}  guardrail prose lines={len(grd)}\n")
    print(f"{'reader':14} | target lin-rec base->fs (d) | guardrail prose-rec base->fs (d) | PASS?")
    passes = []
    for rdr in READERS:
        base, fs = majority3(rdr, "b"), majority3(rdr, "f")
        if not base or not fs:
            print(f"{rdr:14} | (missing runs)")
            continue

        def rec(lab_map: dict, keys: list, want: str) -> float:
            kk = [k for k in keys if k in lab_map]
            return sum(lab_map[k] == want for k in kk) / len(kk) if kk else float("nan")
        tb, tf = rec(base, tgt, "lineated"), rec(fs, tgt, "lineated")
        gb, gf = rec(base, grd, "prose"), rec(fs, grd, "prose")
        ok = (tf - tb) >= 0.10 and (gb - gf) <= 0.05
        passes.append(ok)
        print(f"{rdr:14} | {tb:>4.0%} ->{tf:>4.0%} ({tf - tb:>+4.0%}) | "
              f"{gb:>5.0%} ->{gf:>5.0%} ({gf - gb:>+4.0%}) | {'YES' if ok else 'no'}")
    verdict = ("PASS — few-shot examples teach the boundary" if passes and all(passes)
               else "FAIL — examples do not separate the boundary either; stop prompt work")
    print(f"\nPass rule: target +>=10pp AND guardrail -<=5pp, consistent.  VERDICT: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
