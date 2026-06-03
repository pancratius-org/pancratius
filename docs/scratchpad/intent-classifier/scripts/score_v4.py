# research-only: v4 brief A/B — balanced, leakage-aware, soft-break-safety-aware. 3-run majority.
"""Reads the prose-bias critique seriously: reports BALANCED ACCURACY (not prose-skewed recall),
splits SEEN (agent reconciled against) vs UNSEEN eval regions (generalization proxy), reports a
SINGLE-LINE (.0) view (excludes the free soft-break gains), and a SOFT-BREAK SAFETY check on
g05_b37 wrapping-prose .1 sub-lines (does the 'sub-group => lineated' cue over-lineate prose?).
Baseline = _b1/_b2/_b3 runs (current brief); treatment = _v1/_v2/_v3 (v4 brief)."""
from __future__ import annotations

import json
from pathlib import Path

import bench_models as bm

BENCH = bm.DATA / "phaseb" / "bench"
READERS = ["grok", "gemini-pro", "glm", "ds-flash-text"]
TARGET = ["g00_b64_t2", "g29_b69_t0", "g05_b37", "g18_b60_t3"]   # lineated-by-intent
GUARD = ["g09_b16_t2", "g10_b19"]                                # genuine prose
SEEN = {"g05_b37", "g09_b16_t2"}                                 # eval regions the agent reconciled
# soft-break safety: g05_b37 body lines are WRAPPING prose that are .1 sub-lines (consensus+page).
SAFETY = {("37", 388, 1): "prose", ("37", 390, 1): "prose",
          ("37", 392, 1): "prose", ("37", 394, 1): "prose"}


def load(p: Path) -> dict:
    return ({(bm.book_of(r["rid"]), r["idx"], r["sub"]): r["label"]
             for r in (json.loads(x) for x in p.read_text().splitlines() if x.strip())}
            if p.exists() else {})


def maj3(reader: str, pre: str) -> dict:
    runs = [load(BENCH / f"reader_{reader}_{pre}{i}.jsonl") for i in "123"]
    keys = set().union(*[set(r) for r in runs]) if runs else set()
    out = {}
    for k in keys:
        v = [r[k] for r in runs if k in r]
        if v:
            out[k] = "lineated" if v.count("lineated") > len(v) / 2 else "prose"
    return out


def keys_for(rids: list, truth: dict, pkg: dict, want: str, singleline: bool = False) -> list:
    out = []
    for r in rids:
        if r not in pkg:
            continue
        subs: dict = {}
        for k in pkg[r]["keys"]:
            subs.setdefault(k[0], []).append(k[1])
        for k in pkg[r]["keys"]:
            key = (bm.book_of(r), k[0], k[1])
            if key in truth and truth[key] == want and (not singleline or max(subs[k[0]]) == 0):
                out.append(key)
    return out


def rec(lab: dict, keys: list, want: str) -> float:
    kk = [k for k in keys if k in lab]
    return sum(lab[k] == want for k in kk) / len(kk) if kk else float("nan")


def main() -> int:
    truth = bm.load_truth()
    pkg = {e["rid"]: e for e in json.loads((bm.DATA / "phaseb/reader_pkg.json").read_text())}

    def bal_block(title: str, tgt_rids: list, grd_rids: list, single: bool) -> None:
        tgt = keys_for(tgt_rids, truth, pkg, "lineated", single)
        grd = keys_for(grd_rids, truth, pkg, "prose", single)
        print(f"\n== {title}  (lineated target={len(tgt)}, prose guard={len(grd)}) ==")
        print(f"{'reader':14} | lin-rec b->v4 | prose-rec b->v4 | BAL-ACC b->v4")
        for rdr in READERS:
            b, v = maj3(rdr, "b"), maj3(rdr, "v")
            if not b or not v:
                print(f"{rdr:14} | (missing)")
                continue
            tb, tv = rec(b, tgt, "lineated"), rec(v, tgt, "lineated")
            gb, gv = rec(b, grd, "prose"), rec(v, grd, "prose")
            bb, bv = (tb + gb) / 2, (tv + gv) / 2
            print(f"{rdr:14} | {tb:>4.0%}->{tv:>4.0%}  | {gb:>4.0%}->{gv:>4.0%}   | "
                  f"{bb:>4.0%}->{bv:>4.0%} ({bv - bb:>+4.0%})")

    bal_block("ALL eval", TARGET, GUARD, single=False)
    bal_block("SINGLE-LINE only (.0 — real discrimination, no free soft-break)", TARGET, GUARD, True)
    bal_block("UNSEEN regions (generalization proxy)",
              [r for r in TARGET if r not in SEEN], [r for r in GUARD if r not in SEEN], False)
    bal_block("SEEN regions (in-sample / agent-reconciled)",
              [r for r in TARGET if r in SEEN], [r for r in GUARD if r in SEEN], False)

    # soft-break safety: g05_b37 wrapping-prose .1 lines — does v4 wrongly lineate them?
    print("\n== SOFT-BREAK SAFETY: g05_b37 .1 wrapping-prose lines (truth=prose) ==")
    sk = list(SAFETY)
    for rdr in READERS:
        b, v = maj3(rdr, "b"), maj3(rdr, "v")
        bk = [k for k in sk if k in b]
        vk = [k for k in sk if k in v]
        pb = sum(b[k] == "prose" for k in bk) / len(bk) if bk else float("nan")
        pv = sum(v[k] == "prose" for k in vk) / len(vk) if vk else float("nan")
        cov = f"{len(vk)}/{len(sk)} polled"
        print(f"  {rdr:14} prose-recall base {pb:>4.0%} -> v4 {pv:>4.0%}   ({cov})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
