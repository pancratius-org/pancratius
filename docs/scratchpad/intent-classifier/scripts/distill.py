# research-pure: reads scratch data; writes only to the scratch dir.
"""Distillation + accuracy↔complexity tradeoff.

normalize.py prizes ONE editable rule, so we measure how much of the GBT's by-book
macroF1 a deployable, interpretable form retains: a depth-2/3 decision tree (reads
as nested if/else) and a small logistic-regression score. The depth-3 tree is
printed as text so it can be transcribed into `_para_lineated`/`_run_kind`.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.tree import DecisionTreeClassifier, export_text
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import GroupKFold
from sklearn.metrics import precision_recall_fscore_support

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model as M

D = Path(__file__).resolve().parents[1] / "data"


def macroF1(yt, pt):
    _, _, f, _ = precision_recall_fscore_support(yt, pt, labels=[0, 1], zero_division=0)
    return (f[0] + f[1]) / 2


def oof(make, X, y, groups, scale=False):
    o = np.full(len(y), -1)
    for tr, te in GroupKFold(8).split(X, y, groups):
        if scale:
            sc = StandardScaler().fit(X[tr])
            clf = make().fit(sc.transform(X[tr]), y[tr]); o[te] = clf.predict(sc.transform(X[te]))
        else:
            clf = make().fit(X[tr], y[tr]); o[te] = clf.predict(X[te])
    return o


def main() -> int:
    feats = M.load_features()
    gold = M.load_gold(D / "gold.jsonl")
    base = {(r["key"], r["idx"]): r["is_verse"] for r in (json.loads(l) for l in (D / "baseline.jsonl").open())}
    rows = [g for g in gold if g["label"] in ("prose", "verse") and (g["key"], g["idx"]) in feats]
    X = np.array([M.featurize(feats[(g["key"], g["idx"])], False) for g in rows], float)
    y = np.array([1 if g["label"] == "verse" else 0 for g in rows])
    groups = np.array([g["key"] for g in rows])
    hb = np.array([int(base.get((g["key"], g["idx"]), 0)) for g in rows])

    print("accuracy↔complexity (by-book OOF macroF1):")
    print(f"  heuristic (current normalize.py)            {macroF1(y, hb):.3f}")
    # single-threshold rule: char_len <= T (fit T on train fold)
    cl = X[:, M.FEATS.index("char_len")]
    o = np.full(len(y), -1)
    for tr, te in GroupKFold(8).split(X, y, groups):
        ts = sorted(set(cl[tr].astype(int)))
        bestT = max(ts, key=lambda T: macroF1(y[tr], (cl[tr] <= T).astype(int)))
        o[te] = (cl[te] <= bestT).astype(int)
    print(f"  1-rule: char_len<=T (T fit per fold)        {macroF1(y, o):.3f}")
    for depth in (2, 3, 4):
        o = oof(lambda d=depth: DecisionTreeClassifier(max_depth=d, min_samples_leaf=30, class_weight="balanced"), X, y, groups)
        print(f"  decision tree depth {depth}                       {macroF1(y, o):.3f}")
    o = oof(lambda: LogisticRegression(max_iter=2000, C=1.0, class_weight="balanced"), X, y, groups, scale=True)
    print(f"  logistic regression (22 feat)               {macroF1(y, o):.3f}")
    o = oof(lambda: HistGradientBoostingClassifier(max_depth=3, learning_rate=0.08, max_iter=300, l2_regularization=1.0, min_samples_leaf=15), X, y, groups)
    print(f"  GBT (22 feat, the model)                    {macroF1(y, o):.3f}")

    # the depth-3 tree, as transcribable rules (fit on all gold)
    dt = DecisionTreeClassifier(max_depth=3, min_samples_leaf=30, class_weight="balanced").fit(X, y)
    print("\nDEPTH-3 TREE (verse=1; transcribe into normalize.py):")
    print(export_text(dt, feature_names=list(M.FEATS), max_depth=3))

    # logreg coefficients (standardized) — an interpretable additive score
    sc = StandardScaler().fit(X)
    lr = LogisticRegression(max_iter=2000, C=1.0, class_weight="balanced").fit(sc.transform(X), y)
    coef = lr.coef_[0]
    order = np.argsort(np.abs(coef))[::-1]
    print("logreg standardized coefficients (verse positive):")
    for i in order[:12]:
        print(f"   {M.FEATS[i]:<18} {coef[i]:+.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
