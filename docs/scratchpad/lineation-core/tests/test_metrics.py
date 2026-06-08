# research-pure: the decision-policy metric definitions (accept-quality + load), kept apart.
"""Unit-locks the multi-dimensional policy metrics: that accept-quality is scored only over accepted
lines, the two false-accept directions are split, load counts OPERATIONAL routes as escalatable, and
`compare` renders one row per policy with both correctness AND load columns (never a fused scalar).
The `balanced()` lift itself is byte-identical-locked by test_compare/test_contested."""
from __future__ import annotations

from lineation_core.evaluation.metrics import (
    AcceptMetrics,
    LoadMetrics,
    PolicyMetrics,
    accept_metrics,
    balanced,
    compare,
    load_metrics,
)


def test_balanced_lift_present():
    m = balanced(["prose", "lineated"], ["prose", "lineated"])
    assert m.balanced_acc == 1.0 and m.prose_recall == 1.0 and m.lineated_recall == 1.0


def test_accept_metrics_split_the_two_false_accept_directions():
    # truth, accepted: one prose→lineated (costly), two lineated→prose, one correct.
    pairs = [("prose", "lineated"), ("lineated", "prose"), ("lineated", "prose"),
             ("lineated", "lineated")]
    a = accept_metrics(pairs)
    assert a.n_accepted == 4
    assert a.false_accept_prose_as_lineated == 1
    assert a.false_accept_lineated_as_prose == 2
    assert a.false_accepts == 3                       # the sum, two-class


def test_accept_metrics_empty_is_all_zero_not_a_spurious_claim():
    a = accept_metrics([])
    assert a.n_accepted == 0 and a.acc == 0.0 and a.false_accepts == 0


def test_load_metrics_rates_and_escalatable():
    lm = load_metrics(n_total=100, human_routed=20, escalatable_routed=5)
    assert lm.human_rate == 0.20 and lm.escalatable_rate == 0.05


def test_compare_table_keeps_correctness_and_load_separate():
    def pm(name: str, fa: int, human_rate: float, cov: float) -> PolicyMetrics:
        a = AcceptMetrics(n_accepted=10, acc=0.9, balanced_acc=0.9, prose_recall=1.0,
                          lineated_recall=0.8, false_accepts=fa,
                          false_accept_prose_as_lineated=0, false_accept_lineated_as_prose=fa)
        lm = LoadMetrics(n_total=10, human_routed=int(human_rate * 10), human_rate=human_rate,
                         escalatable_routed=0, escalatable_rate=0.0)
        return PolicyMetrics(name=name, accept=a, load=lm, by_book={}, by_stratum={}, coverage=cov)

    table = compare((pm("legacy", 2, 0.06, 0.94), pm("equal_majority", 5, 0.0, 1.0)))
    # both a correctness column (falseAcc) and a load column (humanRt/coverage) appear per row.
    assert "falseAcc" in table and "humanRt" in table and "coverage" in table
    assert "legacy" in table and "equal_majority" in table
    assert table.count("\n") == 3                     # header + rule + two policy rows
