# research-pure: writes only to the scratch dir.
"""Build the modality-gate A/B set: render degenerate extremes (all-prose, all-verse)
for a few regions so the 'better' option is objective, plus reuse the real method
renders, and emit `data/modality_pairs.json` with my human-anchored preferences.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_candidates as G
import model as M

D = Path(__file__).resolve().parents[1] / "data"
OUT = Path(__file__).resolve().parents[1] / "renders" / "candidates"


def main() -> int:
    feats = M.load_features()
    regions = {r["rid"]: r for r in json.loads((D / "group_regions.json").read_text())}
    manifest = []

    def emit(rid, variant, labelfn):
        reg = regions[rid]
        seq = [feats[(reg["key"], i)] for i in range(reg["lo"], reg["hi"] + 1) if (reg["key"], i) in feats]
        labels = {r["idx"]: labelfn(r) for r in seq}
        html = G.render_html(seq, labels, f'#{reg["key"]} · {variant}')
        hp = OUT / f"r{rid:02d}_{variant}.html"
        hp.write_text(html, encoding="utf-8")
        manifest.append({"html": str(hp), "png": str(OUT / f"r{rid:02d}_{variant}.png")})

    allprose = lambda r: "struct" if G.is_struct_flag(r) else "prose"
    allverse = lambda r: "struct" if G.is_struct_flag(r) else "verse"
    for rid in (7, 8, 11):   # verse regions: grouped should beat all-prose
        emit(rid, "allprose", allprose)
    for rid in (1, 3):       # prose/narrative regions: prose should beat all-verse
        emit(rid, "allverse", allverse)
    (OUT / "extremes_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))

    # The anchored A/B pairs. pref = the option I (human-anchor) judge reads better.
    P = lambda rid, a, b, pref, conf, why: {
        "pair": f"r{rid:02d}_{a}_vs_{b}", "rid": rid,
        "A": f"r{rid:02d}_{a}", "B": f"r{rid:02d}_{b}", "pref": pref, "conf": conf, "why": why}
    pairs = [
        # constructed extremes (objective)
        P(7, "llm", "allprose", "A", "hi", "free verse grouped vs flattened to gappy prose"),
        P(8, "llm", "allprose", "A", "hi", "free verse grouped vs flattened to gappy prose"),
        P(11, "llm", "allprose", "A", "hi", "narrative-poem grouped vs flattened to gappy prose"),
        P(1, "heuristic", "allverse", "A", "hi", "narrative as prose vs everything italic-versed"),
        P(3, "heuristic", "allverse", "A", "hi", "expository prose vs everything italic-versed"),
        # real method contrasts (my viewed preference)
        P(1, "heuristic", "wrap", "A", "hi", "wrap over-groups narrative dialogue into italic verse"),
        P(1, "llm", "wrap", "A", "hi", "wrap over-groups; llm keeps narrative prose"),
        P(9, "llm", "heuristic", "A", "med", "llm groups Q&A answer-stanzas coherently; heuristic choppy"),
        P(3, "heuristic", "gbt", "A", "med", "gbt over-groups an expository region"),
        P(6, "llm", "wrap", "A", "med", "wrap/gbt over-group; llm conservative"),
    ]
    (D / "modality_pairs.json").write_text(json.dumps(pairs, ensure_ascii=False, indent=1))
    print(f"wrote {len(manifest)} extreme renders + {len(pairs)} anchored pairs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
