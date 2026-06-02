# research-pure: writes only to the scratch dir.
"""The inviolate human-anchored gold seed — my own visual adjudications of rendered
regions (several pages viewed directly). Kept separate from scaled judge labels; it
is the calibration anchor the auto-adjudicator is measured against (Cohen's kappa)
and is never overwritten by a model or judge.

Encoding: explicit per-idx verse/prose/struct calls below. Paragraphs not listed in
a window's dict but inside its [lo,hi] that are E/H/***/R/N in the feature table are
auto-labelled `struct`. `conf` defaults hi; med/lo noted where the page left doubt.
"""
from __future__ import annotations

import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"

# window -> (key, lo, hi, {idx: (label, conf)})  — only confident content calls.
V, P, S = "verse", "prose", "struct"
HI, MED = "hi", "med"
SEED = [
    # #71 litany seed (rendered): wrapping prose vs the 9-line litany.
    ("71", 440, 465, {
        440: (P, HI), 448: (P, HI), 449: (P, HI), 450: (P, HI), 451: (P, HI),
        452: (P, HI), 453: (V, HI), 454: (V, HI), 455: (V, HI), 456: (V, HI),
        457: (V, HI), 458: (V, HI), 459: (V, HI), 460: (V, HI), 461: (V, MED),
        462: (P, HI), 463: (P, HI), 464: (P, HI), 465: (P, HI),
        # 441-447 enumeration deliberately omitted: genuine ambiguous middle.
    }),
    # #34 (rendered): anaphoric litany, tight rule-separated stanzas; wraps but verse.
    ("34", 305, 320, {
        305: (V, HI), 306: (V, HI), 307: (V, HI), 308: (V, HI),
        312: (V, HI), 313: (V, HI), 314: (V, HI), 315: (V, HI),
        319: (V, HI), 320: (V, HI),
    }),
    # #13 (rendered): justified/indented narrative fiction, dialogue. All prose.
    ("13", 1288, 1301, {
        1288: (P, HI), 1289: (P, HI), 1290: (P, HI), 1291: (P, HI), 1292: (P, HI),
        1293: (P, HI), 1294: (P, HI), 1295: (P, HI), 1296: (P, HI), 1297: (P, HI),
        1299: (P, HI), 1301: (P, HI),
    }),
    # #13 (rendered): narrative prose + a 3-line quoted vow (verse).
    ("13", 728, 744, {
        728: (P, HI), 729: (P, HI), 730: (P, MED), 731: (P, MED), 732: (P, HI),
        735: (P, HI), 737: (P, HI), 738: (P, HI),
        739: (V, HI), 740: (V, HI), 741: (V, HI),
        742: (P, HI), 743: (P, HI), 744: (P, HI),
    }),
    # #25 free verse (inspected): all verse, *** structural.
    ("25", 6035, 6051, {
        6035: (V, HI), 6036: (V, HI), 6038: (V, HI), 6039: (V, HI), 6040: (V, HI),
        6043: (V, HI), 6044: (V, HI), 6045: (V, HI), 6047: (V, HI), 6048: (V, HI),
        6049: (V, HI),
    }),
    ("25", 15354, 15370, {
        15354: (V, HI), 15356: (V, HI), 15357: (V, HI), 15358: (V, HI),
        15359: (V, HI), 15360: (V, HI), 15362: (V, HI), 15363: (V, MED),
        15365: (V, HI), 15366: (V, HI), 15367: (V, HI), 15368: (V, HI),
    }),
    # #55 free verse (inspected): anaphora + enjambment.
    ("55", 587, 603, {
        587: (V, HI), 588: (V, HI), 589: (V, HI), 590: (V, HI), 592: (V, HI),
        593: (V, HI), 594: (V, MED), 595: (V, HI), 596: (V, HI), 597: (V, HI),
        600: (V, HI), 601: (V, HI), 602: (V, HI), 603: (V, HI),
    }),
    # #68 (rendered): parallel "Тем, кто…" lists = verse/list; pseudo-headings struct.
    ("68", 21, 37, {
        21: (V, HI), 22: (V, HI), 23: (V, HI), 25: (S, HI), 26: (P, HI),
        28: (S, HI), 29: (V, HI), 30: (V, HI), 31: (V, HI), 32: (V, HI),
        33: (V, MED), 35: (S, HI), 36: (V, HI), 37: (V, MED),
    }),
    # #02 (rendered): narrative dialogue scene = prose; "Иллюстрация" captions struct.
    ("02", 292, 308, {
        292: (S, HI), 293: (P, HI), 294: (P, HI), 295: (P, HI), 296: (P, HI),
        297: (P, HI), 298: (P, HI), 300: (S, HI), 301: (P, HI), 302: (P, HI),
        303: (P, HI), 304: (P, HI), 305: (P, MED), 306: (P, MED), 307: (P, HI),
    }),
    # #05 (inspected): teaching prose + speaker labels; numbered teaching = prose.
    ("05", 46, 62, {
        46: (S, HI), 47: (P, HI), 48: (P, HI), 49: (P, HI), 50: (P, MED),
        52: (P, HI), 53: (S, HI), 54: (S, HI), 55: (P, HI), 56: (P, HI),
        57: (P, HI), 58: (P, HI), 59: (P, HI), 60: (P, HI), 61: (P, HI), 62: (P, HI),
    }),
]


def main() -> int:
    feats = {}
    for line in (DATA / "features.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        if r["source"] == "book":
            feats[(r["key"], r["idx"])] = r
    out = []
    for key, lo, hi, calls in SEED:
        for idx in range(lo, hi + 1):
            r = feats.get((key, idx))
            if r is None:
                continue
            if idx in calls:
                label, conf = calls[idx]
            elif r["empty"] or r["heading"] or r["thematic"] or r["align_right"] or r["numbered"]:
                label, conf = "struct", "hi"
            else:
                continue  # unlisted content paragraph = deliberately abstained
            out.append({"key": key, "idx": idx, "label": label, "conf": conf,
                        "adjudicator": "human-anchor"})
    outp = DATA / "seed_gold.jsonl"
    with outp.open("w", encoding="utf-8") as f:
        for rec in out:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    from collections import Counter
    c = Counter(r["label"] for r in out)
    print(f"wrote {len(out)} seed labels -> {outp}: {dict(c)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
