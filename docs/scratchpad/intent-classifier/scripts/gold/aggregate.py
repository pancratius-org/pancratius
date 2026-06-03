# research-pure: turn per-reader panel votes into a routed gold decision.
"""The adjudication gate. Two stages, both pure:

  1. reader_verdict  — collapse one reader's N reps for a line to a single label (or abstain),
                       gate-strict: a confident label needs a strict majority of the present reps;
                       a lone first rep stands (the adaptive protocol's 1-rep stage).
  2. decide_line     — combine the core readers' verdicts + the lead reader's confidence + any
                       substrate flags into a `LineDecision` (accept / escalate / route_human /
                       needs_rerun).

Aggregation POLICY (not final truth): the lead reader (grok) decides; the rest of the panel is a
disagreement detector. Once enough human truth exists, replace this with learned per-reader
reliability weights — the call sites stay the same.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from .types import (
    OPERATIONAL_REASONS,
    TERMINAL_REASONS,
    Gates,
    Label,
    LineDecision,
    LineKey,
    ReaderId,
    Reason,
    Status,
    Vote,
)


def reader_verdict(votes: Sequence[Label]) -> Label | None:
    """One reader's confident label across its reps, or None (abstain).

    - 0 reps → abstain.
    - 1 rep  → that label (the adaptive protocol's initial stage; no majority possible yet).
    - 2 reps → the label iff both agree, else abstain (a 2-rep tie is unresolved).
    - ≥3 reps → strict majority of present reps, else abstain. Ties never resolve to a default.
    """
    n = len(votes)
    if n == 0:
        return None
    if n == 1:
        return votes[0]
    c = Counter(votes)
    top, count = c.most_common(1)[0]
    if n == 2:
        return top if count == 2 else None
    return top if count > n / 2 else None


def lead_confidence(votes: Sequence[Vote], verdict: Label | None) -> float | None:
    """Mean recorded confidence of the reps that voted the verdict label. None if the verdict is
    an abstain or no rep carried a confidence (an unrecorded conf must not silently clear the floor)."""
    if verdict is None:
        return None
    confs = [c for lab, c in votes if lab == verdict and c is not None]
    return sum(confs) / len(confs) if confs else None


def panel_majority(verdicts: Mapping[ReaderId, Label | None], gates: Gates) -> Label | None:
    """Strict-majority label among the CORE readers' verdicts (abstains excluded). Tie → None.
    This measures only WHICH label leads among voters; the `min_core_agree` count gate is applied
    separately in `decide_line` so the two concerns stay legible."""
    labels = [v for r in gates.core if (v := verdicts.get(r)) is not None]
    if not labels:
        return None
    top, count = Counter(labels).most_common(1)[0]
    return top if count > len(labels) / 2 else None


def decide_line(
    key: LineKey,
    reps: Mapping[ReaderId, Sequence[Vote]],
    *,
    gates: Gates,
    needs_review: bool = False,
    soft: bool = False,
) -> LineDecision:
    """Gate one body line. `reps[reader]` is that reader's list of (label, conf) over its reps."""
    verdicts: dict[ReaderId, Label | None] = {
        r: reader_verdict([lab for lab, _ in reps.get(r, ())]) for r in gates.core
    }
    lead_v = verdicts.get(gates.lead)
    lead_conf = lead_confidence(reps.get(gates.lead, ()), lead_v)
    majority = panel_majority(verdicts, gates)
    rep_count = max((len(reps.get(r, ())) for r in gates.core), default=0)

    reasons: list[Reason] = []
    if soft:
        reasons.append(Reason.SOFT)
    if needs_review:
        reasons.append(Reason.NEEDS_REVIEW)
    # Distinguish an operational gap (reader produced nothing) from a genuine abstain (reader voted
    # but its reps didn't reach a confident verdict) — they route differently.
    if any(not reps.get(r) for r in gates.core):
        reasons.append(Reason.READER_MISSING)
    if any(reps.get(r) and verdicts[r] is None for r in gates.core):
        reasons.append(Reason.CORE_ABSTAIN)
    if majority is None:
        reasons.append(Reason.NO_PANEL_MAJORITY)
    elif lead_v is not None and lead_v != majority:
        reasons.append(Reason.GROK_PANEL_SPLIT)
    if majority is not None:
        n_agree = sum(verdicts[r] == majority for r in gates.core)
        if n_agree < gates.min_core_agree:
            reasons.append(Reason.INSUFFICIENT_AGREEMENT)
    if gates.conf_floor > 0:           # conf_floor == 0 disables the confidence gate
        if lead_conf is None:
            reasons.append(Reason.CONF_MISSING)
        elif lead_conf < gates.conf_floor:
            reasons.append(Reason.LOW_CONF)

    status = _route(reasons, rep_count, gates)
    label = majority if status is Status.ACCEPT else (majority or lead_v)
    return LineDecision(
        key=key, status=status, label=label, reasons=tuple(reasons),
        panel_majority=majority, lead_label=lead_v, lead_conf=lead_conf,
        verdicts=verdicts, rep_count=rep_count,
    )


def _route(reasons: Sequence[Reason], rep_count: int, gates: Gates) -> Status:
    """ACCEPT iff no reasons. Then, in precedence order:
    - a TERMINAL reason (intrinsic ambiguity) → the human, regardless of reps;
    - an OPERATIONAL reason (missing output) → re-run while reps remain, else NEEDS_RERUN (never human);
    - otherwise ESCALATE while reps remain, then route the still-unresolved line to the human."""
    if not reasons:
        return Status.ACCEPT
    if any(r in TERMINAL_REASONS for r in reasons):
        return Status.ROUTE_HUMAN
    if any(r in OPERATIONAL_REASONS for r in reasons):
        return Status.ESCALATE if rep_count < gates.escalate_reps else Status.NEEDS_RERUN
    return Status.ESCALATE if rep_count < gates.escalate_reps else Status.ROUTE_HUMAN


def decide_region(
    keys: Sequence[LineKey],
    reps_by_line: Mapping[LineKey, Mapping[ReaderId, Sequence[Vote]]],
    *,
    gates: Gates,
    needs_review: frozenset[LineKey] = frozenset(),
    soft: frozenset[LineKey] = frozenset(),
) -> list[LineDecision]:
    """Gate every body line in a region, in key order."""
    return [
        decide_line(
            k, reps_by_line.get(k, {}), gates=gates,
            needs_review=k in needs_review, soft=k in soft,
        )
        for k in keys
    ]
