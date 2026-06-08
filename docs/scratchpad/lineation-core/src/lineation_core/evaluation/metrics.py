# research-pure: the shared scoring vocabulary — the two-class balanced metric AND the
# multi-dimensional decision-policy metrics. NEVER collapses correctness and load into one scalar.
"""The metrics the evaluation half scores everything with.

Two layers, both pure (data-in, frozen-dataclass-out):

  - `balanced()` / `Metrics` — the two-class (prose/lineated) accuracy + macro-F1 + per-class
    recall used by `compare`/`contested` to score a reader or the student against the truth. Lifted
    here so it has ONE home; `compare`/`contested` import it (proven byte-identical by their locks).

  - the DECISION-POLICY metrics — `AcceptMetrics` (how good are the labels a policy ACCEPTED) and
    `LoadMetrics` (how much human work did it create), kept SEPARATE on purpose. A policy that
    accepts everything has perfect coverage and bad accuracy; one that accepts nothing is the
    reverse. There is no single right scalar, so the tradeoff stays two numbers, and `compare`
    prints them side by side as a TABLE — never as one ranked score.

`AcceptMetrics` scores only the lines a policy ACCEPTED (emitted a label for); the routed-to-human
lines are NOT errors — they are the policy correctly declining, and they are counted by
`LoadMetrics` instead. `false_accept_prose_as_lineated` is broken out from `false_accept_lineated_as_prose`
because the two mistakes are NOT symmetric in this corpus: prose mislabelled lineated is the costly
one the anchor-led design exists to avoid, and tracking it at zero is the headline safety property.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..identity import Label


@dataclass(frozen=True)
class Metrics:
    """Two-class (prose/lineated) scores for one set of predictions against one truth."""

    acc: float
    balanced_acc: float
    macro_f1: float
    prose_recall: float
    lineated_recall: float


def balanced(y_true: list[Label], y_pred: list[Label]) -> Metrics:
    """Balanced accuracy + macro-F1 computed from scratch (no sklearn) on prose/lineated."""
    recalls: list[float] = []
    f1s: list[float] = []
    for c in ("prose", "lineated"):
        tp = sum(t == c and p == c for t, p in zip(y_true, y_pred))
        fn = sum(t == c and p != c for t, p in zip(y_true, y_pred))
        fp = sum(t != c and p == c for t, p in zip(y_true, y_pred))
        recalls.append(tp / (tp + fn) if tp + fn else 0.0)
        prec = tp / (tp + fp) if tp + fp else 0.0
        f1s.append(2 * prec * recalls[-1] / (prec + recalls[-1]) if prec + recalls[-1] else 0.0)
    acc = sum(t == p for t, p in zip(y_true, y_pred)) / len(y_true) if y_true else 0.0
    return Metrics(acc=acc, balanced_acc=sum(recalls) / 2, macro_f1=sum(f1s) / 2,
                   prose_recall=recalls[0], lineated_recall=recalls[1])


# --- the multi-dimensional decision-policy metrics --------------------------------------------

@dataclass(frozen=True, slots=True)
class AcceptMetrics:
    """Quality of the labels a policy ACCEPTED, scored against truth on those lines only. The two
    false-accept directions are kept apart because their costs differ; `false_accepts` is their sum
    plus any class neither maps to (there is none in two-class, so it equals the sum)."""

    n_accepted: int
    acc: float
    balanced_acc: float
    accepted_prose_recall: float             # recall WITHIN accepted lines — NOT global prose capture
    accepted_lineated_recall: float          # (a policy can show 1.000 here yet route most prose away)
    false_accepts: int                       # accepted lines whose label != truth
    false_accept_prose_as_lineated: int      # truth=prose, accepted=lineated (the costly mistake)
    false_accept_lineated_as_prose: int      # truth=lineated, accepted=prose


def accept_metrics(pairs: list[tuple[Label, Label]]) -> AcceptMetrics:
    """`pairs` is `(truth, accepted_label)` for the ACCEPTED lines only. Empty → all-zero (a policy
    that accepts nothing has no accept-quality to report, not a spurious 0.0 accuracy claim)."""
    m = balanced([t for t, _ in pairs], [p for _, p in pairs])
    fa_p_as_l = sum(t == "prose" and p == "lineated" for t, p in pairs)
    fa_l_as_p = sum(t == "lineated" and p == "prose" for t, p in pairs)
    return AcceptMetrics(
        n_accepted=len(pairs), acc=m.acc, balanced_acc=m.balanced_acc,
        accepted_prose_recall=m.prose_recall, accepted_lineated_recall=m.lineated_recall,
        false_accepts=fa_p_as_l + fa_l_as_p,
        false_accept_prose_as_lineated=fa_p_as_l, false_accept_lineated_as_prose=fa_l_as_p)


@dataclass(frozen=True, slots=True)
class CaptureMetrics:
    """The GLOBAL, total-population view (NOT accept-set recall): of ALL the truly-prose lines in the
    aligned set, the fraction the policy AUTO-captured correctly (accepted AND labelled prose), and
    how many it routed to a human instead. This is the number to read for 'how much true prose did
    the policy actually decide' — `AcceptMetrics.accepted_prose_recall` only ranges over accepts and
    will read 1.000 even when most prose was routed away."""

    total_prose: int
    total_lineated: int
    auto_prose_capture: float       # accepted-correct-prose / total_prose
    auto_lineated_capture: float    # accepted-correct-lineated / total_lineated
    routed_prose: int               # truly-prose lines sent to a human (not auto-captured)
    routed_lineated: int


def capture_metrics(rows: list[tuple[Label, Label | None]]) -> CaptureMetrics:
    """`rows` is `(truth, accepted_label_or_None)` for EVERY aligned line — `None` = routed to human.
    Pairs with `accept_metrics` (which sees accepts only) to give the total-population capture."""
    total_p = sum(t == "prose" for t, _ in rows)
    total_l = sum(t == "lineated" for t, _ in rows)
    auto_p = sum(t == "prose" and p == "prose" for t, p in rows)
    auto_l = sum(t == "lineated" and p == "lineated" for t, p in rows)
    return CaptureMetrics(
        total_prose=total_p, total_lineated=total_l,
        auto_prose_capture=auto_p / total_p if total_p else 0.0,
        auto_lineated_capture=auto_l / total_l if total_l else 0.0,
        routed_prose=sum(t == "prose" and p is None for t, p in rows),
        routed_lineated=sum(t == "lineated" and p is None for t, p in rows))


@dataclass(frozen=True, slots=True)
class LoadMetrics:
    """The human work a policy created. `human_routed` is every line it sent to a human;
    `escalatable_routed` is the subset routed for an OPERATIONAL reason (a coverage gap a LIVE run
    could escalate more reps against — the offline harness just counts it as would-escalate load).
    Rates are over `n_total` decided lines, so two policies on the same data are comparable."""

    n_total: int
    human_routed: int
    human_rate: float
    escalatable_routed: int
    escalatable_rate: float


def load_metrics(n_total: int, human_routed: int, escalatable_routed: int) -> LoadMetrics:
    return LoadMetrics(
        n_total=n_total, human_routed=human_routed,
        human_rate=human_routed / n_total if n_total else 0.0,
        escalatable_routed=escalatable_routed,
        escalatable_rate=escalatable_routed / n_total if n_total else 0.0)


type Breakdown = Mapping[str, AcceptMetrics]   # accept-quality sliced by a key (book / stratum)


@dataclass(frozen=True, slots=True)
class PolicyMetrics:
    """One policy's full scorecard: its accept-quality and load overall, the same accept-quality
    sliced `by_book` and `by_stratum`, and `coverage` = the fraction of decided lines it AUTO-accepted
    (1 - human_rate when every line is decided). Correctness (`accept`) and load (`load`) are
    deliberately separate fields — `compare` renders both, never a fused scalar."""

    name: str
    accept: AcceptMetrics
    capture: CaptureMetrics
    load: LoadMetrics
    by_book: Breakdown
    by_stratum: Breakdown
    coverage: float


def compare(metrics: tuple[PolicyMetrics, ...]) -> str:
    """A TABLE, one row per policy: accept-set balanced_acc | GLOBAL auto-capture of prose & lineated
    | the costly prose→lineated false-accepts | human load | coverage. It shows the TOTAL-POPULATION
    capture (NOT accept-set recall, which reads 1.000 even when most prose is routed away) so no row
    can be overclaimed; correctness×load stays VISIBLE across columns — no single 'best' column."""
    head = (f"{'policy':16} {'balAcc':>7} {'autoPro':>8} {'autoLin':>8} "
            f"{'P->L':>5} {'humanRt':>8} {'cover':>6}")
    lines = [head, "-" * len(head)]
    for m in metrics:
        lines.append(
            f"{m.name:16} {m.accept.balanced_acc:>7.3f} "
            f"{m.capture.auto_prose_capture:>8.3f} {m.capture.auto_lineated_capture:>8.3f} "
            f"{m.accept.false_accept_prose_as_lineated:>5} {m.load.human_rate:>8.3f} "
            f"{m.coverage:>6.3f}")
    return "\n".join(lines)
