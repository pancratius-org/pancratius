# research-pure: reads the scratch feature table; writes only to the scratch dir.
"""Adjudication context for gold labeling.

For a sampled window it emits the compact view a judge (me, or a vision/text
subagent) labels: every paragraph with its trusted PHYSICS + DELIBERATE signals and
its text. The judge assigns each NON-structural paragraph a label per the rubric.

RUBRIC (the operational question: how should this paragraph render?):

  structural — empty (stanza/para break), heading, `***`, right-aligned
               signature/epigraph, numbered list item. Bounds runs; not a
               prose/verse decision.
  prose      — intended as a flowing prose paragraph. PROVABLE when it WRAPS
               (>=2 lines at the reading column: the author typed a block). Also a
               short sentence standing alone amid wrapping prose with no lineated
               run around it.
  verse      — a discrete short line meant to stand on its own within a lineated
               block: part of a run of short non-wrapping lines with litany / list /
               poetic / invocational / parallel rhythm (anaphora, address,
               enumeration, imagery).

  THE INVERSION (this author presses Enter for every line): among SHORT NON-WRAPPING
  lines that form a multi-line run bounded by deliberate breaks, DEFAULT to verse.
  Choose prose only if it WRAPS, or the run genuinely reads as ordinary flowing
  prose merely broken short (rare), or it is a dialogue turn / heading-like line.
  A wrapping line inside an otherwise-short run is prose and SPLITS the run.

Flags shown: W=wraps(prose tell)  f=fill  E=empty  H=heading  *=thematic
R=right-align  L#=hard<w:br/> count  N=numbered.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"


def load_by_book() -> dict[str, dict[int, dict]]:
    out: dict[str, dict[int, dict]] = {}
    for line in (DATA / "features.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        if r["source"] == "book":
            out.setdefault(r["key"], {})[r["idx"]] = r
    return out


def flags(r: dict) -> str:
    f = []
    if r["empty"]:
        return "E"
    if r["heading"]:
        f.append("H")
    if r["thematic"]:
        f.append("*")
    if r["align_right"]:
        f.append("R")
    if r["numbered"]:
        f.append("N")
    if r["br_count"]:
        f.append(f"L{r['br_count']}")
    f.append("W" if r["wraps"] else "·")
    f.append(f"f{r['fill']:.2f}")
    return " ".join(f)


def context_block(book: dict[int, dict], lo: int, hi: int, anchor: int | None = None) -> str:
    lines = []
    for idx in range(lo, hi + 1):
        r = book.get(idx)
        if r is None:
            continue
        mark = "»" if idx == anchor else " "
        txt = "∅" if r["empty"] else r["text"]
        lines.append(f"{mark}{idx:>5} [{flags(r):<16}] {txt}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--windows", default=str(DATA / "gold_windows.json"))
    ap.add_argument("--pick", help="comma list of window indices to print (default all)")
    ap.add_argument("--strata", help="comma list of strata to include")
    args = ap.parse_args(argv)

    by_book = load_by_book()
    windows = json.loads(Path(args.windows).read_text())
    picks = {int(x) for x in args.pick.split(",")} if args.pick else None
    strata = set(args.strata.split(",")) if args.strata else None
    for wi, w in enumerate(windows):
        if picks is not None and wi not in picks:
            continue
        if strata is not None and w["stratum"] not in strata:
            continue
        print(f"\n=== window {wi}  book #{w['key']}  stratum={w['stratum']}  "
              f"anchor={w['anchor_idx']}  [{w['lo']}..{w['hi']}] ===")
        print(context_block(by_book[w["key"]], w["lo"], w["hi"], w["anchor_idx"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
