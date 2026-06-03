# research-only: consistent re-score of all benchmark candidate label files (no API).
"""Scores every committed bench/reader_*.jsonl + the incumbents on the SAME problematic subset
with the SAME metric (missing-as-wrong recall, balanced accuracy). Keeps the two DeepSeek runs
SEPARATE — phaseb panel run vs fresh bench rerun — because their gap IS the run-instability
evidence, not one number to average."""
from __future__ import annotations

import json
from pathlib import Path

import bench_models as bm

DATA = bm.DATA / "phaseb"


def load(p: Path) -> dict:
    if not p.exists():
        return {}
    return {(bm.book_of(r["rid"]), r["idx"], r["sub"]): r["label"]
            for r in (json.loads(x) for x in p.read_text().splitlines() if x.strip())}


def main() -> int:
    truth = bm.load_truth()
    pkg = {e["rid"]: e for e in json.loads((DATA / "reader_pkg.json").read_text())}
    tkeys = [(bm.book_of(r), k[0], k[1]) for r in bm.PROBLEM_RIDS if r in pkg
             for k in pkg[r]["keys"]]
    tkeys = [k for k in tkeys if k in truth]
    all_p = [k for k in tkeys if truth[k] == "prose"]
    all_l = [k for k in tkeys if truth[k] == "lineated"]
    print(f"problematic subset: {len(tkeys)} lines ({len(all_p)} prose, {len(all_l)} lineated)\n")
    print(f"{'reader':16} {'cov':>4} | {'prose-r':>7} {'lin-r':>6} {'bal-acc':>7}   (missing=wrong)")

    def row(name: str, lab: dict) -> None:
        cov = len([k for k in tkeys if k in lab]) / len(tkeys)
        pr = sum(lab.get(k) == "prose" for k in all_p) / len(all_p)
        lr = sum(lab.get(k) == "lineated" for k in all_l) / len(all_l)
        print(f"{name:16} {cov:>3.0%} | {pr:>6.0%} {lr:>5.0%} {(pr + lr) / 2:>6.0%}")

    print("-- incumbents (phaseb panel run) --")
    for t in ["grok", "gemini", "deepseek"]:
        row(t + (" (pro)" if t == "gemini" else " (flash)" if t == "deepseek" else ""),
            load(DATA / f"reader_{t}.jsonl"))
    print("-- candidates (fresh bench runs) --")
    for t in ["ds-flash-text", "ds-pro-text", "step", "qwen3", "glm", "gemini-flash",
              "perceptron", "nemotron", "gemini-lite", "ring"]:
        lab = load(DATA / "bench" / f"reader_{t}.jsonl")
        if lab:
            row(t, lab)
    print("\nNOTE: deepseek(flash) appears TWICE — phaseb panel run vs bench 'ds-flash-text' rerun, "
          "same model, different scores = single-pass run instability.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
