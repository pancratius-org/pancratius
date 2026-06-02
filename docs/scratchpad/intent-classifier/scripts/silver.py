# research-pure: reads scratch data; writes only to the scratch dir.
"""Silver labels by weak supervision (high-precision labeling functions).

For pretraining / augmentation / full-corpus coverage ONLY — never an eval target.
Each LF abstains unless confident; a paragraph's silver label is the majority of
firing LFs (ties / no-fire -> abstain). Run `--eval` to measure each LF's precision
and coverage against the merged gold (the honest check the brief demands).

LFs (epistemically grounded, NO incidental styling):
  wrap_prose      — wraps (>=2 lines): provable prose.
  dialogue_prose  — opens with a dialogue dash or carries a speech verb: fiction.
  longrun_verse   — short non-wrapping content line inside a long run (run_len>=8)
                    that is not dialogue: confident lineation by the inversion.
  anaphora_verse  — short non-wrapping line sharing an opening token with a neighbour
                    (anaphora/parallelism) and not dialogue.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
_SPEECH = re.compile(r"\b(сказал|сказала|спросил|спросила|ответил|ответила|произн[её]с|"
                     r"промолвил|потребовал|воскликнул|прошептал|прошептала|молвил)\b", re.I)


def is_struct(r: dict) -> bool:
    return bool(r["empty"] or r["heading"] or r["thematic"] or r["align_right"] or r["numbered"])


def lfs(r: dict) -> dict[str, str]:
    """Each LF -> 'prose'|'verse'|'' (abstain)."""
    out = {}
    dash = r["starts_dash"]
    speech = bool(_SPEECH.search(r["text"]))
    out["wrap_prose"] = "prose" if r["wraps"] else ""
    out["dialogue_prose"] = "prose" if (dash or speech) else ""
    short = (not r["wraps"]) and r["run_len"] >= 1
    out["longrun_verse"] = "verse" if (short and r["run_len"] >= 8 and not dash and not speech) else ""
    out["anaphora_verse"] = "verse" if (short and (r["anaphora_prev"] or r["anaphora_next"])
                                        and not dash and not speech) else ""
    return out


def silver_label(r: dict) -> str:
    votes = [v for v in lfs(r).values() if v]
    if not votes:
        return ""
    p, v = votes.count("prose"), votes.count("verse")
    if p > v:
        return "prose"
    if v > p:
        return "verse"
    return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eval", action="store_true", help="report LF precision/coverage vs gold")
    ap.add_argument("--gold", default=str(DATA / "gold.jsonl"))
    args = ap.parse_args(argv)

    rows = [json.loads(l) for l in (DATA / "features.jsonl").open(encoding="utf-8")]
    book = [r for r in rows if r["source"] == "book"]

    if args.eval:
        gold = {(r["key"], r["idx"]): r["label"]
                for r in (json.loads(l) for l in Path(args.gold).open())}
        feat_by = {(r["key"], r["idx"]): r for r in book}
        names = list(lfs(book[0]).keys())
        print(f"{'LF':<16} {'cover':>7} {'gold-hit':>9} {'prec':>6}")
        for name in names:
            cov = corr = hit = 0
            for r in book:
                vote = lfs(r).get(name, "")
                if not vote:
                    continue
                cov += 1
                g = gold.get((r["key"], r["idx"]))
                if g in ("prose", "verse"):
                    hit += 1
                    corr += int(vote == g)
            print(f"{name:<16} {cov:>7} {hit:>9} {corr/hit if hit else 0:>6.3f}")
        # combined silver vs gold
        n = c = 0
        for r in book:
            s = silver_label(r)
            if not s:
                continue
            g = gold.get((r["key"], r["idx"]))
            if g in ("prose", "verse"):
                n += 1
                c += int(s == g)
        print(f"\ncombined silver: gold-overlap={n} precision={c/n if n else 0:.3f}")
        return 0

    out = DATA / "silver.jsonl"
    cnt = {"prose": 0, "verse": 0, "struct": 0, "abstain": 0}
    with out.open("w", encoding="utf-8") as f:
        for r in book:
            if is_struct(r):
                lab = "struct"
            else:
                lab = silver_label(r) or "abstain"
            cnt[lab] += 1
            if lab not in ("abstain",):
                f.write(json.dumps({"key": r["key"], "idx": r["idx"], "label": lab,
                                    "source": "silver"}, ensure_ascii=False) + "\n")
    print(f"silver labels -> {out}: {cnt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
