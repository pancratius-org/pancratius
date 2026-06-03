# research-only: v5 confirmation on the WIDE prose guardrail (8 unseen books, human-adjudicated).
"""Tests whether v5 over-lineates fresh prose across MULTIPLE books (closes the QA one-block gap).
WIDE_PROSE = 3 fresh prose books; WIDE_LIN = 5 fresh lineated regions. baseline=_pc, v5=_pw, 5-rep
gate-strict (reuses score_v5.maj3). Reports per-reader + grok-led + equal-panel, baseline->v5."""
from __future__ import annotations

import json

import bench_models as bm
import score_v5 as s

WIDE_PROSE = ["p01_b14", "p02_b23", "p04_b33"]                       # human=prose (18 lines, 3 books)
WIDE_LIN = ["p00_b06", "p03_b32", "g16_b38", "g17_b14", "g01_b57"]   # human=lineated (43 lines, 5 books)


def wide_truth() -> dict:
    f = next((bm.DATA.parent / "adjudicate").glob("responses-wide-prose*.json"))
    d = json.loads(f.read_text())["responses"]
    return {(s.bk(rid), int(k.split(".")[0]), int(k.split(".")[1])): lab
            for rid, v in d.items() for k, lab in v.get("lines", {}).items()}


def rec(d: dict, ks: list, want: str) -> float:
    return sum(d.get(k) == want for k in ks) / len(ks) if ks else float("nan")


def main() -> int:
    truth = wide_truth()
    pkg = {e["rid"]: e for e in json.loads((bm.DATA / "phaseb/reader_pkg.json").read_text())}
    pk = s.keys_in(WIDE_PROSE, truth, pkg, "prose")
    lk = s.keys_in(WIDE_LIN, truth, pkg, "lineated")
    print(f"WIDE guardrail: {len(pk)} prose lines (3 books) | {len(lk)} lineated lines (5 books)\n")
    print(f"{'reader':14} | prose-rec c->v5 | lin-rec c->v5 | bal c->v5")
    pc_all, pw_all = {}, {}
    for rdr in s.READERS:
        c, w = s.maj3(rdr, "pc"), s.maj3(rdr, "pw")
        if not c or not w:
            print(f"{rdr:14} | (missing)")
            continue
        pc_all[rdr], pw_all[rdr] = c, w
        pc, pw = rec(c, pk, "prose"), rec(w, pk, "prose")
        lc, lw = rec(c, lk, "lineated"), rec(w, lk, "lineated")
        print(f"{rdr:14} | {pc:>4.0%}->{pw:>4.0%}   | {lc:>4.0%}->{lw:>4.0%}  | "
              f"{(pc+lc)/2:>4.0%}->{(pw+lw)/2:>4.0%}")

    def agg(labs: dict, ks: list, want: str, grokled: bool) -> float:
        ok = 0
        for k in ks:
            if grokled:
                pred = labs.get("grok", {}).get(k)
            else:
                v = [labs[r][k] for r in s.READERS if k in labs.get(r, {})]
                pred = ("lineated" if v.count("lineated") > len(v) / 2
                        else ("prose" if v.count("prose") > len(v) / 2 else None)) if v else None
            ok += pred == want
        return ok / len(ks) if ks else float("nan")
    # soft/prior-dependent labels (per the human's notes): p00_b06 (voted lineated "for consistency,
    # but prose looks better"), p04_b33 (voted prose "but the book is lineated… don't know"). Report
    # ALL vs HARD-only (excluding the soft regions) as a sensitivity view.
    soft = {"p00_b06", "p04_b33"}
    pk_hard = s.keys_in([r for r in WIDE_PROSE if r not in soft], truth, pkg, "prose")
    lk_hard = s.keys_in([r for r in WIDE_LIN if r not in soft], truth, pkg, "lineated")
    for label, P, L in [("ALL labels", pk, lk), ("HARD-only (soft p00/p04 excluded)", pk_hard, lk_hard)]:
        print(f"\n  [{label}]  (prose={len(P)}, lineated={len(L)})")
        print("    v5 EQUAL-panel : prose {:.0%}  lineated {:.0%}".format(
            agg(pw_all, P, "prose", False), agg(pw_all, L, "lineated", False)))
        print("    v5 GROK-LED    : prose {:.0%}  lineated {:.0%}".format(
            agg(pw_all, P, "prose", True), agg(pw_all, L, "lineated", True)))
    print("\nGATE: v5 prose-recall stays high across the fresh prose books (no over-lineation).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
