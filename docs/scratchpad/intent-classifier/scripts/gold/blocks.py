# research-pure: per-line labels → rendered blocks, and block-level agreement metrics.
"""The site renders BLOCKS (runs of one label), not isolated lines, so the gold is judged at block
grain too: one misplaced boundary is one whole error, not 1/N. A block is a maximal run of
consecutive same-label body lines in key order.

Metrics between two labelings of the SAME ordered keys:
  boundary_f1        — F1 over the internal positions where the label flips (segmentation quality).
  exact_block_match  — fraction of reference blocks reproduced exactly (same key span AND label).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from .types import Block, Label, LineKey


def reconstruct(keys: Sequence[LineKey], labels: Mapping[LineKey, Label]) -> list[Block]:
    """Collapse a per-line labeling into blocks. Keys must be the region's body lines in order;
    a key with no label breaks the run (an unlabeled line is not part of either neighbour's block)."""
    blocks: list[Block] = []
    run: list[tuple[LineKey, Label]] = []   # (key, label) pairs — label is never None while in a run
    for k in keys:
        lab = labels.get(k)
        if lab is not None and run and lab == run[-1][1]:
            run.append((k, lab))
            continue
        if run:
            blocks.append(Block(run[0][1], tuple(kk for kk, _ in run)))
        run = [(k, lab)] if lab is not None else []
    if run:
        blocks.append(Block(run[0][1], tuple(kk for kk, _ in run)))
    return blocks


def boundaries(keys: Sequence[LineKey], labels: Mapping[LineKey, Label]) -> set[int]:
    """Internal positions i (1-based gap before keys[i]) where the label flips. Unlabeled lines on
    either side of a gap count as a flip — a coverage hole is a boundary, not a silent join."""
    return {
        i for i in range(1, len(keys))
        if labels.get(keys[i - 1]) != labels.get(keys[i])
    }


def boundary_f1(
    keys: Sequence[LineKey],
    pred: Mapping[LineKey, Label],
    gold: Mapping[LineKey, Label],
) -> float:
    """F1 of predicted vs gold flip positions. Two labelings with no flips (one block each) agree
    perfectly → 1.0."""
    p, g = boundaries(keys, pred), boundaries(keys, gold)
    if not p and not g:
        return 1.0
    tp = len(p & g)
    prec = tp / len(p) if p else 0.0
    rec = tp / len(g) if g else 0.0
    return 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0


def exact_block_match(
    keys: Sequence[LineKey],
    pred: Mapping[LineKey, Label],
    gold: Mapping[LineKey, Label],
) -> float:
    """Fraction of GOLD blocks reproduced exactly (identical key tuple and label) in the prediction."""
    g_blocks = reconstruct(keys, gold)
    if not g_blocks:
        return 1.0
    p_set = {(b.label, b.keys) for b in reconstruct(keys, pred)}
    hits = sum((b.label, b.keys) in p_set for b in g_blocks)
    return hits / len(g_blocks)
