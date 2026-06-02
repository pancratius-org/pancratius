# research-pure: reads scratch data; writes only to the scratch dir.
"""Adversarial checks against the model's claims, plus the signature/right-align
analysis. Run via `uv run --with scikit-learn --with cleanlab python redteam.py`.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.model_selection import GroupKFold
from sklearn.metrics import precision_recall_fscore_support

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model as M

D = Path(__file__).resolve().parents[1] / "data"


def newclf():
    return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.08, max_iter=300,
                                          l2_regularization=1.0, min_samples_leaf=15)


def prf(yt, pt):
    p, r, f, _ = precision_recall_fscore_support(yt, pt, labels=[0, 1], zero_division=0)
    return (f[0] + f[1]) / 2, p, r, f


def main() -> int:
    feats = M.load_features()
    allbook = [r for r in (json.loads(l) for l in (D / "features.jsonl").open()) if r["source"] == "book"]
    gold = M.load_gold(D / "gold.jsonl")
    base = {(r["key"], r["idx"]): r["is_verse"] for r in (json.loads(l) for l in (D / "baseline.jsonl").open())}
    bkind = {(r["key"], r["idx"]): r["block_kind"] for r in (json.loads(l) for l in (D / "baseline.jsonl").open())}
    rows = [g for g in gold if g["label"] in ("prose", "verse") and (g["key"], g["idx"]) in feats]
    X = np.array([M.featurize(feats[(g["key"], g["idx"])], False) for g in rows], float)
    y = np.array([1 if g["label"] == "verse" else 0 for g in rows])
    groups = np.array([g["key"] for g in rows])
    conf = np.array([g.get("conf", "hi") for g in rows])
    o = np.full(len(y), -1); proba = np.zeros((len(y), 2))
    for tr, te in GroupKFold(8).split(X, y, groups):
        c = newclf().fit(X[tr], y[tr]); o[te] = c.predict(X[te]); proba[te] = c.predict_proba(X[te])
    hb = np.array([int(base.get((g["key"], g["idx"]), 0)) for g in rows])

    mf, p, r, f = prf(y, o)
    print(f"MODEL by-book OOF: macroF1={mf:.3f}  prose P/R/F1={p[0]:.3f}/{r[0]:.3f}/{f[0]:.3f}  verse P/R/F1={p[1]:.3f}/{r[1]:.3f}/{f[1]:.3f}")
    mfh, ph, rh, fh = prf(y, hb)
    print(f"HEURISTIC      : macroF1={mfh:.3f}  prose P/R/F1={ph[0]:.3f}/{rh[0]:.3f}/{fh[0]:.3f}  verse P/R/F1={ph[1]:.3f}/{rh[1]:.3f}/{fh[1]:.3f}")

    # RT1 worst books
    bk = defaultdict(list)
    for i, g in enumerate(rows):
        bk[g["key"]].append(i)
    worst = []
    for b, ids in bk.items():
        if len(ids) >= 15:
            ids = np.array(ids)
            worst.append((b, len(ids), prf(y[ids], o[ids])[0], prf(y[ids], hb[ids])[0]))
    worst.sort(key=lambda t: t[2])
    print("\n[RT1] worst books by model macroF1 (n>=15):")
    for b, n, m, h in worst[:6]:
        print(f"   #{b} n={n} modelF1={m:.2f} heurF1={h:.2f}")
    print(f"   books model<heur: {sum(1 for _,_,m,h in worst if m<h)}/{len(worst)}")

    # RT2 cleanlab
    try:
        from cleanlab.filter import find_label_issues
        issues = find_label_issues(y, proba, return_indices_ranked_by="self_confidence", n_jobs=1)
        keep = np.ones(len(y), bool); keep[issues] = False
        print(f"\n[RT2] cleanlab: {len(issues)} potential label issues ({len(issues)/len(y):.1%}); "
              f"flagged-by-conf={dict(Counter(conf[issues]))}")
        print(f"   macroF1 on cleanlab-cleaned gold: model={prf(y[keep],o[keep])[0]:.3f} heur={prf(y[keep],hb[keep])[0]:.3f}")
    except Exception as e:  # noqa: BLE001
        print(f"\n[RT2] cleanlab skipped: {type(e).__name__}: {e}")

    # RT3 strict holdout: train on judge labels (seed books fully excluded), test human seed
    seed = [json.loads(l) for l in (D / "seed_gold.jsonl").open()]
    seedbooks = {r["key"] for r in seed}
    seed_pairs = {(r["key"], r["idx"]): (1 if r["label"] == "verse" else 0)
                  for r in seed if r["label"] in ("prose", "verse")}
    tr_idx = [i for i, g in enumerate(rows) if g["key"] not in seedbooks]
    te = [(k, v) for k, v in seed_pairs.items() if k in feats]
    c = newclf().fit(X[tr_idx], y[tr_idx])
    Xte = np.array([M.featurize(feats[k], False) for k, _ in te], float)
    yte = np.array([v for _, v in te])
    pte = c.predict(Xte)
    print(f"\n[RT3] strict holdout (train=judge, ALL {len(seedbooks)} seed books excluded; test=human seed n={len(yte)}): "
          f"acc={(pte==yte).mean():.3f} macroF1={prf(yte,pte)[0]:.3f}")

    # SIGNATURE / RIGHT-ALIGN analysis
    ra = [r for r in allbook if r["align_right"] and not r["empty"]]
    print(f"\n[SIG] right-aligned non-empty paragraphs in corpus: {len(ra)} / {len(allbook)} ({len(ra)/len(allbook):.2%})")
    kinds = Counter(bkind.get((r["key"], r["idx"]), "?") for r in ra)
    print(f"   heuristic block_kind for right-aligned: {dict(kinds)}")
    # how many right-aligned got folded into VerseBlock (would be a leak of signature into verse)
    leaked = sum(1 for r in ra if bkind.get((r["key"], r["idx"])) == "VerseBlock")
    print(f"   right-aligned folded into VerseBlock (potential signature->verse drift): {leaked}")
    # are any GOLD content (prose/verse) paragraphs right-aligned? (we excluded R as struct)
    g_ra = [g for g in gold if (g["key"], g["idx"]) in feats and feats[(g["key"], g["idx"])]["align_right"]]
    print(f"   gold paragraphs that are right-aligned: {len(g_ra)} labels={dict(Counter(x['label'] for x in g_ra))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
