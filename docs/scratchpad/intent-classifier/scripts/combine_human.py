# research-pure: writes only to the scratch dir.
"""Build neutral side-by-side A/B comparison renders for the HUMAN to judge.

Each pair shows the SAME text region grouped two ways, labelled neutrally (① / ②)
so the human's "which reads better" is unbiased. A private key (data/human_key.json)
records which variant is which method, for scoring against the LLM panel and my
anchors.
"""
from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_candidates as G
import model as M

D = Path(__file__).resolve().parents[1] / "data"
OUT = Path(__file__).resolve().parents[1] / "renders" / "human"

# (rid, methodLeftConceptually, methodRight) — the comparison to show.
PAIRS = [
    (1, "heuristic", "wrap"),    # #02 Маленький царь: clean prose vs over-grouped italic
    (1, "heuristic", "llm"),     # #02: does light context-grouping help or hurt?
    (9, "heuristic", "llm"),     # #10: Q&A answer-stanza grouping
    (7, "allprose", "llm"),      # #08: litany flattened to gappy prose vs grouped
    (3, "heuristic", "gbt"),     # #04: expository — does gbt over-group?
    (6, "wrap", "llm"),          # #07: over-group vs conservative
]


def article_only(full_html: str) -> str:
    m = re.search(r"<article.*?</article>", full_html, re.S)
    return m.group(0) if m else full_html


def main() -> int:
    feats = M.load_features()
    base = {(r["key"], r["idx"]): r["is_verse"] for r in (json.loads(l) for l in (D / "baseline.jsonl").open())}
    gold = [r for r in (json.loads(l) for l in (D / "gold.jsonl").open())
            if r["label"] in ("prose", "verse") and (r["key"], r["idx"]) in feats]
    X = np.array([M.featurize(feats[(r["key"], r["idx"])], False) for r in gold], float)
    yv = np.array([1 if r["label"] == "verse" else 0 for r in gold])
    clf = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.08, max_iter=300,
                                         l2_regularization=1.0, min_samples_leaf=15).fit(X, yv)
    def load(f): return {(r["key"], r["idx"]): r["label"] for r in (json.loads(l) for l in (D / f).open())}
    JA, JB, JC = load("judgeG_A.jsonl"), load("judgeG_B.jsonl"), load("judgeG_C.jsonl")
    regions = {r["rid"]: r for r in json.loads((D / "group_regions.json").read_text())}

    def labels_for(rid, method, seq, key):
        if method == "allprose":
            return {r["idx"]: ("struct" if G.is_struct_flag(r) else "prose") for r in seq}
        if method == "heuristic":
            return {r["idx"]: ("struct" if G.is_struct_flag(r) else ("verse" if base.get((key, r["idx"])) else "prose")) for r in seq}
        if method == "wrap":
            return G.wrap_labels(seq)
        if method == "gbt":
            out = {}
            for r in seq:
                out[r["idx"]] = "struct" if G.is_struct_flag(r) else ("verse" if clf.predict(np.array([M.featurize(r, False)], float))[0] == 1 else "prose")
            return out
        if method == "llm":
            out = {}
            for r in seq:
                labs = [j.get((key, r["idx"])) for j in (JA, JB, JC)]; labs = [x for x in labs if x]
                out[r["idx"]] = (max(set(labs), key=labs.count) if labs else G.wrap_labels(seq)[r["idx"]])
            return out
        raise ValueError(method)

    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(20260529)
    key = {}
    manifest = []
    for n, (rid, mA, mB) in enumerate(PAIRS):
        reg = regions[rid]
        seq = [feats[(reg["key"], i)] for i in range(reg["lo"], reg["hi"] + 1) if (reg["key"], i) in feats]
        bodyA = article_only(G.render_html(seq, labels_for(rid, mA, seq, reg["key"]), ""))
        bodyB = article_only(G.render_html(seq, labels_for(rid, mB, seq, reg["key"]), ""))
        # neutral random display order
        if rng.random() < 0.5:
            left, right, lm, rm = bodyA, bodyB, mA, mB
        else:
            left, right, lm, rm = bodyB, bodyA, mB, mA
        cid = f"case{n+1:02d}_book{reg['key']}"
        html = (f'<!doctype html><meta charset=utf-8><style>{G.CSS}'
                '.wrap{display:flex;gap:0;}.col{width:50%;padding:1.6rem 1.8rem;border-right:1px solid #ccd;}'
                '.hd{font-family:ui-sans-serif,sans-serif;font-weight:700;color:#a86b1f;margin-bottom:1rem;font-size:1rem;}'
                '</style>'
                f'<div class="wrap"><div class="col"><div class="hd">① variant</div>{left}</div>'
                f'<div class="col"><div class="hd">② variant</div>{right}</div></div>')
        hp = OUT / f"{cid}.html"; hp.write_text(html, encoding="utf-8")
        manifest.append({"html": str(hp), "png": str(OUT / f"{cid}.png")})
        key[cid] = {"book": reg["key"], "①": lm, "②": rm}
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    (D / "human_key.json").write_text(json.dumps(key, ensure_ascii=False, indent=1))
    print(f"wrote {len(manifest)} human side-by-side cases -> {OUT}")
    for cid, k in key.items():
        print(f"  {cid}: ①={k['①']} ②={k['②']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
