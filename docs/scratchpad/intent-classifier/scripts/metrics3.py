# research-pure: pure functions over label sequences; no I/O, no representation deps.
"""Evaluation metrics for the 3-way {flowing, lineated-prose, verse} cascade.

These are pure functions over label/segment sequences — independent of how a
paragraph is represented, so they are stable regardless of the representation
rework. They implement the eval stack agreed for the benchmark:

  - per-label macro-F1 (paragraph level)
  - run-boundary F1 (did we segment runs at the right places)
  - WindowDiff / Pk (segmentation error, penalizes near-misses less than exact-F1)
  - register precision/recall (verse), evaluable on gold OR predicted runs
  - by-book bootstrap CIs (helper)

A "run" here is a maximal span of same-segment paragraphs; boundaries are the
indices where the segment id changes. Stage-1 lineation produces a segmentation
(flowing vs lineated runs); Stage-2 register labels each lineated run.
"""
from __future__ import annotations

from collections import Counter


def macro_f1(gold: list[str], pred: list[str], labels: list[str]) -> dict[str, float]:
    """Per-label P/R/F1 + macro-F1 over aligned label sequences."""
    out: dict[str, float] = {}
    f1s = []
    for lab in labels:
        tp = sum(g == lab and p == lab for g, p in zip(gold, pred, strict=True))
        fp = sum(g != lab and p == lab for g, p in zip(gold, pred, strict=True))
        fn = sum(g == lab and p != lab for g, p in zip(gold, pred, strict=True))
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        out[f"{lab}_P"], out[f"{lab}_R"], out[f"{lab}_F1"] = prec, rec, f1
        f1s.append(f1)
    out["macro_F1"] = sum(f1s) / len(f1s) if f1s else 0.0
    out["accuracy"] = sum(g == p for g, p in zip(gold, pred, strict=True)) / len(gold) if gold else 0.0
    return out


def boundaries(seg: list[int]) -> set[int]:
    """Indices i where a new segment starts (i>0 and seg[i] != seg[i-1])."""
    return {i for i in range(1, len(seg)) if seg[i] != seg[i - 1]}


def boundary_f1(gold_seg: list[int], pred_seg: list[int]) -> dict[str, float]:
    """Precision/recall/F1 on the SET of segment-boundary positions."""
    g, p = boundaries(gold_seg), boundaries(pred_seg)
    tp = len(g & p)
    prec = tp / len(p) if p else (1.0 if not g else 0.0)
    rec = tp / len(g) if g else (1.0 if not p else 0.0)
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else (1.0 if not g and not p else 0.0)
    return {"boundary_P": prec, "boundary_R": rec, "boundary_F1": f1,
            "n_gold_boundaries": len(g), "n_pred_boundaries": len(p)}


def _mass_boundaries(seg: list[int]) -> list[int]:
    """0/1 boundary mass per position (1 at a segment change), Beeferman-style."""
    return [0] + [1 if seg[i] != seg[i - 1] else 0 for i in range(1, len(seg))]


def window_diff(gold_seg: list[int], pred_seg: list[int], k: int | None = None) -> float:
    """WindowDiff (Pevzner & Hearst 2002): fraction of length-k windows where the
    gold and predicted boundary COUNTS differ. Lower is better; penalizes
    near-misses gently, unlike exact boundary-F1. k defaults to half the mean
    gold-segment length."""
    n = len(gold_seg)
    if n < 2:
        return 0.0
    gb, pb = _mass_boundaries(gold_seg), _mass_boundaries(pred_seg)
    if k is None:
        nseg = len(set(gold_seg)) or 1
        k = max(2, round((n / nseg) / 2))
    k = min(k, n - 1)
    diff = 0
    win = n - k
    for i in range(win):
        gcount = sum(gb[i + 1:i + k + 1])
        pcount = sum(pb[i + 1:i + k + 1])
        diff += int(gcount != pcount)
    return diff / win if win else 0.0


def pk(gold_seg: list[int], pred_seg: list[int], k: int | None = None) -> float:
    """Pk (Beeferman 1999): prob. that two positions k apart are wrongly classified
    as same/different segment. Lower is better."""
    n = len(gold_seg)
    if n < 2:
        return 0.0
    if k is None:
        nseg = len(set(gold_seg)) or 1
        k = max(2, round((n / nseg) / 2))
    k = min(k, n - 1)
    err = 0
    tot = n - k
    for i in range(tot):
        gsame = gold_seg[i] == gold_seg[i + k]
        psame = pred_seg[i] == pred_seg[i + k]
        err += int(gsame != psame)
    return err / tot if tot else 0.0


def verse_precision_recall(gold: list[str], pred: list[str]) -> dict[str, float]:
    """Verse precision/recall — the asymmetric objective: precision is the one that
    matters now (every emitted verse must be earned); recall is reported, not chased."""
    tp = sum(g == "verse" and p == "verse" for g, p in zip(gold, pred, strict=True))
    fp = sum(g != "verse" and p == "verse" for g, p in zip(gold, pred, strict=True))
    fn = sum(g == "verse" and p != "verse" for g, p in zip(gold, pred, strict=True))
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return {"verse_P": prec, "verse_R": rec, "verse_tp": tp, "verse_fp": fp, "verse_fn": fn}


def bootstrap_ci(
    per_book: dict[str, tuple[list[str], list[str]]],
    metric_fn,
    n_boot: int = 2000,
    seed: int = 20260530,
) -> tuple[float, float, float]:
    """Resample BOOKS (keys) with replacement; return (point, lo95, hi95) of a metric
    computed on the pooled resampled paragraphs. metric_fn(gold, pred) -> float.
    Deterministic LCG (no Math.random/Date dependency)."""
    books = sorted(per_book)
    pooled_g = [x for b in books for x in per_book[b][0]]
    pooled_p = [x for b in books for x in per_book[b][1]]
    point = metric_fn(pooled_g, pooled_p)
    state = seed & 0xFFFFFFFF
    def rnd() -> float:
        nonlocal state
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        return state / 0x7FFFFFFF
    vals = []
    m = len(books)
    for _ in range(n_boot):
        samp = [books[int(rnd() * m) % m] for _ in range(m)]
        g = [x for b in samp for x in per_book[b][0]]
        p = [x for b in samp for x in per_book[b][1]]
        vals.append(metric_fn(g, p))
    vals.sort()
    lo = vals[int(0.025 * len(vals))]
    hi = vals[int(0.975 * len(vals))]
    return point, lo, hi


if __name__ == "__main__":
    # self-test on a tiny synthetic example
    gold = ["flowing", "flowing", "verse", "verse", "verse", "lineated-prose", "lineated-prose"]
    pred = ["flowing", "flowing", "verse", "verse", "lineated-prose", "lineated-prose", "lineated-prose"]
    seg_g = [0, 0, 1, 1, 1, 2, 2]
    seg_p = [0, 0, 1, 1, 2, 2, 2]
    print("macro:", {k: round(v, 3) for k, v in macro_f1(gold, pred, ["flowing", "lineated-prose", "verse"]).items()})
    print("boundary:", {k: round(v, 3) for k, v in boundary_f1(seg_g, seg_p).items()})
    print("windowdiff:", round(window_diff(seg_g, seg_p), 3), "pk:", round(pk(seg_g, seg_p), 3))
    print("verse:", verse_precision_recall(gold, pred))
