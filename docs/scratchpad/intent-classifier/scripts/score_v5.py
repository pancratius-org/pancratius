# research-only: v5 A/B — does the patched brief hold prose on FRESH unseen books while keeping
# lineated-recall high? Truth = human 235 (eval) + human fresh-prose-guardrail batch (unseen books).
"""baseline = _c1/_c2/_c3 runs (current brief); v5 = _w1/_w2/_w3 (reader_brief_v5.txt). 3-run majority.
Reports balanced accuracy on ALL, on EVAL-only, and on FRESH-only (the generalization test).
PASS = on FRESH, prose-recall stays high (no over-lineation) AND lineated-recall stays high."""
from __future__ import annotations

import json
import re
from pathlib import Path

import bench_models as bm

BENCH = bm.DATA / "phaseb" / "bench"
ADJ = bm.DATA.parent / "adjudicate"
READERS = ["grok", "gemini-pro", "glm", "ds-flash-text"]
EVAL_TARGET = ["g00_b64_t2", "g29_b69_t0", "g05_b37", "g18_b60_t3"]
EVAL_GUARD = ["g09_b16_t2", "g10_b19"]
FRESH = ["g35_b41", "g19_b24", "g28_b71", "g26_b30", "g07_b47", "g13_b01"]


def bk(rid: str) -> str:
    m = re.search(r"_b(\d+)", rid.removeprefix("audit_"))
    return m.group(1) if m else "??"


def load(p: Path) -> dict:
    return ({(bk(r["rid"]), r["idx"], r["sub"]): r["label"]
             for r in (json.loads(x) for x in p.read_text().splitlines() if x.strip())}
            if p.exists() else {})


def maj3(reader: str, pre: str) -> dict:
    runs = [load(BENCH / f"reader_{reader}_{pre}{i}.jsonl") for i in "123"]
    keys = set().union(*[set(r) for r in runs]) if runs else set()
    return {k: ("lineated" if [r[k] for r in runs if k in r].count("lineated")
                > len([r for r in runs if k in r]) / 2 else "prose") for k in keys}


def truth_all() -> dict:
    t = {}
    adj = json.loads((ADJ / "responses-lineation-adjudication-gold-block2-contested-lines.json"
                      ).read_text())["responses"]
    fresh = json.loads(next(ADJ.glob("responses-fresh-prose*.json")).read_text())["responses"]
    for src in (adj, fresh):
        for rid, v in src.items():
            for k, lab in v.get("lines", {}).items():
                i, s = k.split(".")
                t[(bk(rid), int(i), int(s))] = lab
    return t


def keys_in(rids: list, truth: dict, pkg: dict, want: str) -> list:
    out = []
    for r in rids:
        if r not in pkg:
            continue
        for k in pkg[r]["keys"]:
            key = (bk(r), k[0], k[1])
            if key in truth and truth[key] == want:
                out.append(key)
    return out


def main() -> int:
    truth = truth_all()
    pkg = {e["rid"]: e for e in json.loads((bm.DATA / "phaseb/reader_pkg.json").read_text())}

    def block(title: str, tgt_rids: list, grd_rids: list) -> None:
        tgt = keys_in(tgt_rids, truth, pkg, "lineated")
        grd = keys_in(grd_rids, truth, pkg, "prose")
        print(f"\n== {title}  (lineated={len(tgt)}, prose={len(grd)}) ==")
        print(f"{'reader':14} | lin-rec c->v5 | prose-rec c->v5 | BAL-ACC c->v5")
        for rdr in READERS:
            c, w = maj3(rdr, "c"), maj3(rdr, "w")
            if not c or not w:
                print(f"{rdr:14} | (missing runs)")
                continue
            def rec(d: dict, ks: list, want: str) -> float:
                kk = [k for k in ks if k in d]
                return sum(d[k] == want for k in kk) / len(kk) if kk else float("nan")
            tc, tw = rec(c, tgt, "lineated"), rec(w, tgt, "lineated")
            gc, gw = rec(c, grd, "prose"), rec(w, grd, "prose")
            bc, bw = (tc + gc) / 2, (tw + gw) / 2
            print(f"{rdr:14} | {tc:>4.0%}->{tw:>4.0%}  | {gc:>4.0%}->{gw:>4.0%}   | "
                  f"{bc:>4.0%}->{bw:>4.0%} ({bw - bc:>+4.0%})")

    block("ALL (eval + fresh)", EVAL_TARGET + FRESH, EVAL_GUARD + FRESH)
    block("EVAL only (in-sample)", EVAL_TARGET, EVAL_GUARD)
    block("FRESH only — UNSEEN books (the generalization gate)", FRESH, FRESH)
    print("\nGATE: on FRESH, prose-recall must stay high (no over-lineation) AND lineated-recall high.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
