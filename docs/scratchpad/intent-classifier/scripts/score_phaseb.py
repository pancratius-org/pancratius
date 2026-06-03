# research-only: Phase B calibration — did corrected renders move the panel toward the human truth?
"""Compares each panel reader's labels on the human-adjudicated contested lines, OLD
(gold_block2, buggy renders + guessed roles) vs NEW (phaseb, corrected renders + seam mask).
Keyed by (book, idx, sub) so tile re-packaging between the two runs doesn't break the join.
Truth = the human's 235 adjudicated lines (page-grounded, independent of the renderer)."""
from __future__ import annotations

import json
import re
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data"
ADJ = Path(__file__).resolve().parents[1] / "adjudicate"
TAGS = ["grok", "gemini", "owl", "deepseek", "mimo", "minimax", "careful"]


def book_of(rid: str) -> str | None:
    m = re.search(r"_b(\d+)", rid.removeprefix("audit_"))
    return m.group(1) if m else None


def load_truth() -> dict[tuple[str | None, int, int], str]:
    adj = json.loads((ADJ / "responses-lineation-adjudication-gold-block2-contested-lines.json"
                      ).read_text())["responses"]
    truth: dict[tuple[str | None, int, int], str] = {}
    for rid, v in adj.items():
        bk = book_of(rid)
        for k, lab in v.get("lines", {}).items():
            i, s = k.split(".")
            truth[(bk, int(i), int(s))] = lab
    return truth


def load_reader(path: Path) -> dict[tuple[str | None, int, int], str]:
    out: dict[tuple[str | None, int, int], str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        out[(book_of(r["rid"]), r["idx"], r["sub"])] = r["label"]
    return out


def main() -> int:
    truth = load_truth()
    print(f"truth: {len(truth)} human-adjudicated contested lines "
          f"({sum(v == 'lineated' for v in truth.values())} lineated, "
          f"{sum(v == 'prose' for v in truth.values())} prose)\n")
    print(f"{'reader':10} {'OLD-acc':>9} {'NEW-acc':>9} {'delta':>7} {'n':>5}  "
          f"{'fixed':>6} {'broke':>6}")
    panel_old: dict[tuple, list[str]] = {}
    panel_new: dict[tuple, list[str]] = {}
    for tag in TAGS:
        old = load_reader(DATA / "gold_block2" / f"reader_{tag}.jsonl")
        new = load_reader(DATA / "phaseb" / f"reader_{tag}.jsonl")
        keys = [k for k in truth if k in old and k in new]
        if not keys:
            print(f"{tag:10} {'(no NEW labels yet)':>40}")
            continue
        oa = sum(old[k] == truth[k] for k in keys) / len(keys)
        na = sum(new[k] == truth[k] for k in keys) / len(keys)
        fixed = sum(old[k] != truth[k] and new[k] == truth[k] for k in keys)
        broke = sum(old[k] == truth[k] and new[k] != truth[k] for k in keys)
        print(f"{tag:10} {oa:>8.1%} {na:>8.1%} {na - oa:>+7.1%} {len(keys):>5}  "
              f"{fixed:>6} {broke:>6}")
        for k in keys:
            panel_old.setdefault(k, []).append(old[k])
            panel_new.setdefault(k, []).append(new[k])

    # panel majority vote per line (ties → prose, the safe default)
    def majority(votes: list[str]) -> str:
        return "lineated" if votes.count("lineated") > len(votes) / 2 else "prose"

    common = [k for k in truth if k in panel_old and k in panel_new]
    if common:
        oa = sum(majority(panel_old[k]) == truth[k] for k in common) / len(common)
        na = sum(majority(panel_new[k]) == truth[k] for k in common) / len(common)
        print(f"\nPANEL majority vote over {len(common)} lines: "
              f"OLD {oa:.1%} -> NEW {na:.1%} ({na - oa:+.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
