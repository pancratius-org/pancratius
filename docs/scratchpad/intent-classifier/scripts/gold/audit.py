# research-pure: deterministic random audit of ACCEPTED lines.
"""Routing only disagreements misses systematic bias the whole panel shares (an earlier audit
caught panel UNDER-lineation that every reader agreed on). So sample ACCEPTED high-confidence
lines for a human spot-check — stratified, and biased toward the failure mode that hides:
lead-led PROSE calls (where over/under-lineation is silent because nobody disagreed).

Pure and seeded: same (lines, rate, seed) → same sample, recorded in the manifest. Operates on
region-qualified `AuditLine`s so identity never collides across books.
"""
from __future__ import annotations

import random
from collections.abc import Mapping, Sequence

from .types import AuditLine, Label


def sample_accepted(
    lines: Sequence[AuditLine],
    *,
    rate: float = 0.08,
    seed: int = 0,
    prose_bias: float = 2.0,
) -> list[AuditLine]:
    """Stratified sample of accepted lines for human spot-check.

    Within each stratum, lines are sampled at `rate`; accepted PROSE lines are weighted up by
    `prose_bias` (they are the silent-error risk). At least one line per non-empty stratum is
    audited (a stratum is never wholly trusted). Returned in deterministic (stratum, ident) order."""
    by_stratum: dict[str, list[AuditLine]] = {}
    for ln in lines:
        by_stratum.setdefault(ln.stratum, []).append(ln)

    rng = random.Random(seed)
    picked: list[AuditLine] = []
    for stratum in sorted(by_stratum):
        pool = sorted(by_stratum[stratum], key=lambda ln: ln.ident)
        weights = [prose_bias if ln.label == "prose" else 1.0 for ln in pool]
        target = max(1, round(len(pool) * rate))
        picked.extend(_weighted_sample(pool, weights, target, rng))
    return sorted(picked, key=lambda ln: (ln.stratum, ln.ident))


def _weighted_sample(
    pool: Sequence[AuditLine], weights: Sequence[float], k: int, rng: random.Random,
) -> list[AuditLine]:
    """Deterministic weighted sample WITHOUT replacement (Efraimidis–Spirakis keys)."""
    k = min(k, len(pool))
    if k == 0:
        return []
    keyed = sorted(
        zip(pool, weights, strict=True),
        key=lambda pw: rng.random() ** (1.0 / pw[1]),
        reverse=True,
    )
    return [ln for ln, _ in keyed[:k]]


def audit_report(
    lines: Sequence[AuditLine],
    human: Mapping[tuple[str, int, int], Label],
) -> dict[str, dict[str, float]]:
    """Accepted-line error rate per stratum, once a human has labeled the audit sample. A stratum
    with a non-zero rate is systematically biased → re-open it (do not trust the rest of its block)."""
    out: dict[str, dict[str, float]] = {}
    for ln in lines:
        if ln.ident not in human:
            continue
        s = out.setdefault(ln.stratum, {"n": 0.0, "wrong": 0.0})
        s["n"] += 1
        s["wrong"] += ln.label != human[ln.ident]
    for s in out.values():
        s["error_rate"] = s["wrong"] / s["n"] if s["n"] else 0.0
    return out
