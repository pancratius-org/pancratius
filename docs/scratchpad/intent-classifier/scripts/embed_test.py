# research-pure: reads scratch data; writes only to the scratch dir.
"""Embedding ceiling test — does a Russian/multilingual line encoder beat the
engineered GBT (0.814)? Bounds the research ceiling vs the deployable model.

Encodes each gold line with a small multilingual sentence encoder, trains logreg
by-book on (a) embeddings only and (b) embeddings + engineered features. If it does
not clearly beat 0.814, the engineered model is at the practical ceiling and the
remaining gap is irreducible ambiguity / label noise.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import GroupKFold
from sklearn.metrics import precision_recall_fscore_support

sys.path.insert(0, str(Path(__file__).resolve().parent))
import model as M

D = Path(__file__).resolve().parents[1] / "data"


def macroF1(yt, pt):
    _, _, f, _ = precision_recall_fscore_support(yt, pt, labels=[0, 1], zero_division=0)
    return (f[0] + f[1]) / 2


def oof(X, y, groups):
    o = np.full(len(y), -1)
    for tr, te in GroupKFold(8).split(X, y, groups):
        sc = StandardScaler().fit(X[tr])
        clf = LogisticRegression(max_iter=3000, C=1.0, class_weight="balanced").fit(sc.transform(X[tr]), y[tr])
        o[te] = clf.predict(sc.transform(X[te]))
    return o


def main() -> int:
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as e:  # noqa: BLE001
        print(f"sentence-transformers unavailable: {e}")
        return 1
    feats = M.load_features()
    gold = M.load_gold(D / "gold.jsonl")
    rows = [g for g in gold if g["label"] in ("prose", "verse") and (g["key"], g["idx"]) in feats]
    texts = [feats[(g["key"], g["idx"])]["text"] for g in rows]
    y = np.array([1 if g["label"] == "verse" else 0 for g in rows])
    groups = np.array([g["key"] for g in rows])

    model_name = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    print(f"encoding {len(texts)} lines with {model_name} ...", flush=True)
    enc = SentenceTransformer(model_name)
    E = np.asarray(enc.encode(texts, batch_size=64, show_progress_bar=False), float)
    print(f"embeddings shape {E.shape}")

    eng = np.array([M.featurize(feats[(g["key"], g["idx"])], False) for g in rows], float)
    print(f"embeddings-only logreg by-book macroF1 = {macroF1(y, oof(E, y, groups)):.3f}")
    EE = np.hstack([E, eng])
    print(f"embeddings+engineered logreg macroF1   = {macroF1(y, oof(EE, y, groups)):.3f}")
    print("(engineered GBT baseline = 0.814)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
