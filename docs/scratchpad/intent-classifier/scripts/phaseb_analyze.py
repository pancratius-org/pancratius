# research-only: Phase B deep analysis — actual denominators, per-region deltas, grok indent-bias.
"""Aggregates hide instability (Codex). This prints, with REAL denominators:
  - per reader: n, prose-recall, lineated-recall, balanced accuracy (OLD->NEW)
  - per adjudication region: panel-majority correct OLD->NEW (surfaces the regressions)
  - grok's regressions: how many are source-indented (the indent-bias hypothesis)
Truth = the human's page-grounded contested adjudications, keyed (book, idx, sub)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
DATA = HERE.parent / "data"
ADJ = HERE.parent / "adjudicate"
ROOT = HERE.parents[3]   # repo root (for src/content)
READERS = ["grok", "gemini", "owl", "deepseek", "mimo", "minimax"]


def book_of(rid: str) -> str | None:
    m = re.search(r"_b(\d+)", rid.removeprefix("audit_"))
    return m.group(1) if m else None


def load(p: Path) -> dict:
    o: dict = {}
    if p.exists():
        for line in p.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                o[(book_of(r["rid"]), r["idx"], r["sub"])] = r["label"]
    return o


def main() -> int:
    adj = json.loads((ADJ / "responses-lineation-adjudication-gold-block2-contested-lines.json"
                      ).read_text())["responses"]
    truth, truth_region = {}, {}   # key->label, key->adjudication rid
    for rid, v in adj.items():
        bk = book_of(rid)
        for k, lab in v.get("lines", {}).items():
            i, s = k.split(".")
            key = (bk, int(i), int(s))
            truth[key] = lab
            truth_region[key] = rid
    old = {t: load(DATA / "gold_block2" / f"reader_{t}.jsonl") for t in READERS}
    new = {t: load(DATA / "phaseb" / f"reader_{t}.jsonl") for t in READERS}

    print("== PER READER (actual denominators) ==")
    print(f"{'reader':9} {'n':>4} | prose-rec OLD NEW | lin-rec OLD NEW | bal-acc OLD NEW")
    for t in READERS:
        keys = [k for k in truth if k in old[t] and k in new[t]]
        pk = [k for k in keys if truth[k] == "prose"]
        lk = [k for k in keys if truth[k] == "lineated"]
        def r(d, ks, lab):
            return (sum(d[k] == lab for k in ks) / len(ks)) if ks else float("nan")
        po, pn = r(old[t], pk, "prose"), r(new[t], pk, "prose")
        lo, ln = r(old[t], lk, "lineated"), r(new[t], lk, "lineated")
        bo, bn = (po + lo) / 2, (pn + ln) / 2
        print(f"{t:9} {len(keys):>4} | n_p={len(pk):>2} {po:>4.0%} {pn:>4.0%} "
              f"| n_l={len(lk):>3} {lo:>4.0%} {ln:>4.0%} | {bo:>4.0%} {bn:>4.0%}")

    # panel majority per region
    def maj(votes):
        return "lineated" if votes.count("lineated") > len(votes) / 2 else "prose"
    regions: dict[str, list] = {}
    for k in truth:
        regions.setdefault(truth_region[k], []).append(k)
    print("\n== PER REGION (panel majority correct, OLD -> NEW) — biggest moves ==")
    rows = []
    for rid, keys in regions.items():
        oc = nc = n = 0
        for k in keys:
            ov = [old[t][k] for t in READERS if k in old[t]]
            nv = [new[t][k] for t in READERS if k in new[t]]
            if not ov or not nv:
                continue
            n += 1
            oc += maj(ov) == truth[k]
            nc += maj(nv) == truth[k]
        if n:
            rows.append((nc - oc, rid, oc, nc, n, truth[keys[0]]))
    for d, rid, oc, nc, n, lab in sorted(rows):
        flag = "  <== REGRESSION" if d < 0 else ("  (gain)" if d > 0 else "")
        print(f"  {rid:16} {lab:8} {oc:>2}/{n} -> {nc:>2}/{n}  ({d:+d}){flag}")

    # grok indent-bias: of grok's regressions (old right, new wrong), how many indented?
    import glob
    import ir_view as iv
    keys_g = [k for k in truth if k in old["grok"] and k in new["grok"]]
    regr = [k for k in keys_g if old["grok"][k] == truth[k] and new["grok"][k] != truth[k]]
    books = {k[0] for k in regr}
    indented = {}
    for bk in books:
        gl = glob.glob(str(ROOT / f"src/content/books/{bk}-*/ru.docx"))
        if gl:
            indented[bk] = {p.index: p.indented for p in iv.read_view(Path(gl[0]))}
    n_ind = sum(indented.get(k[0], {}).get(k[1], False) for k in regr)
    by_region = {}
    for k in regr:
        by_region[truth_region[k]] = by_region.get(truth_region[k], 0) + 1
    print(f"\n== GROK regressions: {len(regr)} (old right -> new wrong); "
          f"{n_ind} are source-indented ==")
    print("   by region:", dict(sorted(by_region.items(), key=lambda x: -x[1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
