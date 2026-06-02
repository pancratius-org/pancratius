# research-pure: reads scratch data; writes only to the scratch dir.
"""Interpretable classifier + by-BOOK CV evaluation, vs the heuristic baseline.

Binary target on CONTENT paragraphs: verse(1) vs prose(0). `struct` paragraphs
(empty/heading/***/right/numbered/labels) are excluded from the prose/verse decision
(they bound runs and are handled deterministically downstream).

Features are grouped by epistemic status; incidental-styling NEGATIVE CONTROLS
(`nc_*`) are EXCLUDED by default and only added with `--with-nc` to prove they do
not help (or that they hurt by-book generalization — the prior heuristic's sin).

Evaluation: GroupKFold by book (no same-book leakage), out-of-fold predictions,
per-class P/R/F1, confusion, accuracy; bootstrap 95% CIs by resampling BOOKS; the
heuristic baseline scored on the identical gold paragraphs; feature importances.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import precision_recall_fscore_support, confusion_matrix

DATA = Path(__file__).resolve().parents[1] / "data"

_2ND = re.compile(r"\b(ты|тебя|тебе|тобой|тобою|твой|твоя|твоё|твои|твою|твоего|тебе)\b", re.I)
_SPEECH = re.compile(r"\b(сказал|сказала|спросил|спросила|ответил|ответила|произн[её]с|"
                     r"промолвил|потребовал|воскликнул|прошептал|прошептала|спросили|"
                     r"проговорил|молвил|шепнул|крикнул)\b", re.I)
_NAMES = re.compile(r"\b(Сергей|Олег|Александра|Анфиса|Ваня|Иисус|Мария|Пётр|Иоанн|"
                    r"Светозар|Панкратиус)\b")


def load_features() -> dict:
    out = {}
    for line in (DATA / "features.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        if r["source"] == "book":
            out[(r["key"], r["idx"])] = r
    return out


def load_gold(path: Path) -> list[dict]:
    seen = {}
    for line in path.open(encoding="utf-8"):
        r = json.loads(line)
        seen[(r["key"], r["idx"])] = r  # later wins
    return list(seen.values())


# engineered features (epistemically clean) + derived lexical/register cues
FEATS = [
    "fill", "wrap_lines", "wraps", "char_len", "word_count",
    "br_count", "prev_empty", "next_empty", "after_heading", "after_thematic",
    "ends_terminal", "ends_colon", "is_question", "starts_dash", "starts_upper",
    "run_len", "run_pos_frac", "anaphora_prev", "anaphora_next",
    "has_2nd_person", "has_speech_verb", "has_proper_name",
]
NC_FEATS = ["nc_contextual", "nc_first_indent", "nc_jc_both", "nc_jc_right",
            "nc_sp_after_nz", "nc_has_linegroup"]


def featurize(r: dict, with_nc: bool) -> list[float]:
    t = r["text"]
    rl = r["run_len"] or 1
    f = {
        "fill": r["fill"], "wrap_lines": r["wrap_lines"], "wraps": int(r["wraps"]),
        "char_len": r["char_len"], "word_count": r["word_count"],
        "br_count": r["br_count"], "prev_empty": int(r["prev_empty"]),
        "next_empty": int(r["next_empty"]), "after_heading": int(r["after_heading"]),
        "after_thematic": int(r["after_thematic"]),
        "ends_terminal": int(r["ends_terminal"]), "ends_colon": int(r["ends_colon"]),
        "is_question": int(r["is_question"]), "starts_dash": int(r["starts_dash"]),
        "starts_upper": int(r["starts_upper"]),
        "run_len": r["run_len"], "run_pos_frac": (r["run_pos"] / rl) if r["run_pos"] >= 0 else -1,
        "anaphora_prev": int(r["anaphora_prev"]), "anaphora_next": int(r["anaphora_next"]),
        "has_2nd_person": int(bool(_2ND.search(t))),
        "has_speech_verb": int(bool(_SPEECH.search(t))),
        "has_proper_name": int(bool(_NAMES.search(t))),
    }
    vec = [f[k] for k in FEATS]
    if with_nc:
        nc = {
            "nc_contextual": int(r["nc_contextual"]),
            "nc_first_indent": int(r["nc_first_indent"]),
            "nc_jc_both": int(r["nc_jc"] == "both"),
            "nc_jc_right": int(r["nc_jc"] in ("right", "end")),
            "nc_sp_after_nz": int(bool(r["nc_sp_after"]) and r["nc_sp_after"] not in ("0", "")),
            "nc_has_linegroup": int(r["nc_lineation_group"] is not None),
        }
        vec += [nc[k] for k in NC_FEATS]
    return vec


def metrics(y, p, name):
    pr, rc, f1, _ = precision_recall_fscore_support(y, p, labels=[0, 1], zero_division=0)
    acc = float((np.array(y) == np.array(p)).mean())
    print(f"\n{name}: acc={acc:.3f}")
    print(f"  prose : P={pr[0]:.3f} R={rc[0]:.3f} F1={f1[0]:.3f}")
    print(f"  verse : P={pr[1]:.3f} R={rc[1]:.3f} F1={f1[1]:.3f}")
    print(f"  macroF1={(f1[0]+f1[1])/2:.3f}  confusion[rows=gold prose/verse]:\n{confusion_matrix(y,p,labels=[0,1])}")
    return acc, (f1[0] + f1[1]) / 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gold", default=str(DATA / "gold.jsonl"))
    ap.add_argument("--with-nc", action="store_true", help="add incidental-styling negative controls")
    ap.add_argument("--model", choices=["gbt", "logreg"], default="gbt")
    ap.add_argument("--min-conf", choices=["lo", "med", "hi"], default="lo")
    args = ap.parse_args(argv)

    feats = load_features()
    base = {(r["key"], r["idx"]): r["is_verse"]
            for r in (json.loads(l) for l in (DATA / "baseline.jsonl").open())}
    gold = load_gold(Path(args.gold))
    conf_rank = {"lo": 0, "med": 1, "hi": 2}
    rows = [g for g in gold if g["label"] in ("prose", "verse")
            and (g["key"], g["idx"]) in feats
            and conf_rank.get(g.get("conf", "hi"), 2) >= conf_rank[args.min_conf]]

    X = np.array([featurize(feats[(g["key"], g["idx"])], args.with_nc) for g in rows], float)
    y = np.array([1 if g["label"] == "verse" else 0 for g in rows])
    groups = np.array([g["key"] for g in rows])
    nbooks = len(set(groups))
    print(f"gold content paragraphs: {len(y)}  verse={y.sum()} prose={(1-y).sum()}  books={nbooks}")
    print(f"features={X.shape[1]} ({'with NC' if args.with_nc else 'clean'})  model={args.model}")

    # out-of-fold predictions, GroupKFold by book
    oof = np.full(len(y), -1)
    gkf = GroupKFold(n_splits=min(8, nbooks))
    for tr, te in gkf.split(X, y, groups):
        if args.model == "gbt":
            clf = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.08,
                                                 max_iter=300, l2_regularization=1.0,
                                                 min_samples_leaf=15)
            clf.fit(X[tr], y[tr])
            oof[te] = clf.predict(X[te])
        else:
            sc = StandardScaler().fit(X[tr])
            clf = LogisticRegression(max_iter=2000, C=1.0, class_weight="balanced")
            clf.fit(sc.transform(X[tr]), y[tr])
            oof[te] = clf.predict(sc.transform(X[te]))

    _, model_macro = metrics(y, oof, f"MODEL ({args.model}, by-book OOF)")

    # heuristic baseline on identical paragraphs
    hb = np.array([int(base.get((g["key"], g["idx"]), False)) for g in rows])
    _, heur_macro = metrics(y, hb, "HEURISTIC baseline")

    # bootstrap 95% CI on macro-F1 difference, resampling BOOKS
    rng = np.random.default_rng(42)
    book_list = sorted(set(groups))
    by_book = {b: np.where(groups == b)[0] for b in book_list}
    diffs = []
    for _ in range(2000):
        samp = rng.choice(book_list, len(book_list), replace=True)
        idx = np.concatenate([by_book[b] for b in samp])
        def mf1(pred):
            _, _, f1, _ = precision_recall_fscore_support(y[idx], pred[idx], labels=[0, 1], zero_division=0)
            return (f1[0] + f1[1]) / 2
        diffs.append(mf1(oof) - mf1(hb))
    diffs = np.array(diffs)
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    print(f"\nmacroF1: model={model_macro:.3f} heuristic={heur_macro:.3f}  Δ={model_macro-heur_macro:+.3f}")
    print(f"bootstrap Δ macroF1 95% CI: [{lo:+.3f}, {hi:+.3f}]  (P(Δ>0)={(diffs>0).mean():.3f})")

    # feature importances (permutation on a single fit) — interpretability
    if args.model == "gbt":
        from sklearn.inspection import permutation_importance
        clf = HistGradientBoostingClassifier(max_depth=3, learning_rate=0.08, max_iter=300,
                                             l2_regularization=1.0, min_samples_leaf=15).fit(X, y)
        names = FEATS + (NC_FEATS if args.with_nc else [])
        imp = permutation_importance(clf, X, y, n_repeats=10, random_state=0, scoring="f1_macro")
        order = np.argsort(imp.importances_mean)[::-1]
        print("\ntop feature importances (permutation, f1_macro drop):")
        for i in order[:14]:
            print(f"  {names[i]:<18} {imp.importances_mean[i]:.4f} ± {imp.importances_std[i]:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
