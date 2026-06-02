# research-pure: reads scratch data; writes only to the scratch dir.
"""Emit per-window context blocks for the (text+physics) judge.

Produces `data/judge_windows.json`: one record per gold window with the compact
context a judge labels. Also emits `data/seed_windows.json` for the SAME ranges as
the human seed, so a judge can be scored (Cohen's kappa) against the inviolate seed
before being trusted at scale.
"""
from __future__ import annotations

import json
from pathlib import Path

import adjudicate as adj  # same dir on sys.path when run via -m or with cwd

DATA = Path(__file__).resolve().parents[1] / "data"


def _emit(by_book, ranges, outp):
    recs = []
    for wid, key, lo, hi, anchor, stratum in ranges:
        recs.append({
            "wid": wid, "key": key, "lo": lo, "hi": hi,
            "anchor": anchor, "stratum": stratum,
            "context": adj.context_block(by_book[key], lo, hi, anchor),
        })
    Path(outp).write_text(json.dumps(recs, ensure_ascii=False, indent=1))
    return len(recs)


def main() -> int:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    by_book = adj.load_by_book()

    windows = json.loads((DATA / "gold_windows.json").read_text())
    ranges = [(wi, w["key"], w["lo"], w["hi"], w["anchor_idx"], w["stratum"])
              for wi, w in enumerate(windows)]
    n = _emit(by_book, ranges, DATA / "judge_windows.json")
    print(f"wrote {n} judge windows -> data/judge_windows.json")

    # seed ranges (mirror seed_gold.SEED)
    import seed_gold
    sranges = [(f"seed{i}", key, lo, hi, lo, "seed")
               for i, (key, lo, hi, _calls) in enumerate(seed_gold.SEED)]
    m = _emit(by_book, sranges, DATA / "seed_windows.json")
    print(f"wrote {m} seed windows -> data/seed_windows.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
