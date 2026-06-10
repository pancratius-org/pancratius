# research-pure: per-reader study scoring — protocol/quality/cost kept apart, cost pure on injected price.
"""Locks the lifted reader scoring: per-class recall is byte-identical to `metrics.balanced`, an unvoted
eval line is a coverage miss, instability counts reps that disagreed, and cost is tokens × an INJECTED
price ($0 on empty) with no disk read or module-global price table."""
from __future__ import annotations

import pytest

from lineation_core.annotations import PanelVote
from lineation_core.evaluation import reader_metrics as rm
from lineation_core.evaluation.metrics import balanced
from lineation_core.identity import LineId
from lineation_core.teacher.panel import PanelRep
from lineation_core.teacher.responses import RawReaderResponse


def _lid(o: int) -> LineId:
    return LineId.mapped("ru", "57", o, 0)


def _v(o: int, label: str) -> PanelVote:
    return PanelVote(id=_lid(o), tag="grok", label=label, conf=None)


def _rep(usage: dict | None) -> PanelRep:
    return PanelRep(item_id="r", tag="grok", rep=0, model="x/grok", content="",
                    response=RawReaderResponse(item_id="r", tag="grok", rows=()),
                    finish_reason="stop", usage=usage)


def test_reader_cost_exact_usd_on_synthetic_reps():
    reps = [_rep({"prompt_tokens": 1000, "completion_tokens": 500}),
            _rep({"prompt_tokens": 2000, "completion_tokens": 250})]
    c = rm.reader_cost(reps, price=(1e-6, 2e-6), n_lines=10)
    assert c.prompt_tokens == 3000 and c.completion_tokens == 750
    assert c.usd == pytest.approx(3000 * 1e-6 + 750 * 2e-6)        # exact billing, not an estimate
    assert c.usd_per_1k_lines == pytest.approx(c.usd / 10 * 1000)


def test_reader_cost_zero_on_empty():
    c = rm.reader_cost([], price=(1.25e-6, 2.5e-6), n_lines=0)
    assert c.usd == 0.0 and c.prompt_tokens == 0 and c.usd_per_1k_lines == 0.0


def test_instability_counts_reps_that_disagreed():
    # line 1: both reps prose (stable); line 2: prose then lineated (disagree) → 1/2 covered lines.
    votes = [_v(1, "prose"), _v(1, "prose"), _v(2, "prose"), _v(2, "lineated")]
    assert rm.instability(votes) == pytest.approx(0.5)
    assert rm.instability([]) == 0.0                               # no covered lines → stable


def test_coverage_counts_an_unvoted_eval_line_as_a_miss():
    evals = [_lid(1), _lid(2), _lid(3), _lid(4)]
    votes = [_v(1, "prose"), _v(2, "lineated")]                    # 3 and 4 unanswered
    assert rm.coverage(votes, evals) == pytest.approx(0.5)


def test_class_recall_byte_identical_to_metrics_balanced():
    truth = {_lid(1): "prose", _lid(2): "prose", _lid(3): "lineated", _lid(4): "lineated"}
    evals = list(truth)
    # reader: line1 prose-right, line2 wrong (lineated), line3 lineated-right, line4 UNVOTED (a miss).
    votes = [_v(1, "prose"), _v(2, "lineated"), _v(3, "lineated")]
    bal, pr, lr, n_p, n_l = rm.class_recall(votes, truth, evals)
    # the same per-line (truth, pred) the function feeds balanced — line4's miss is a sentinel pred.
    expect = balanced(["prose", "prose", "lineated", "lineated"],
                      ["prose", "lineated", "lineated", "__miss__"])  # type: ignore[list-item]
    assert (bal, pr, lr) == (expect.balanced_acc, expect.prose_recall, expect.lineated_recall)
    assert n_p == 2 and n_l == 2
    assert pr == pytest.approx(0.5)                                # 1 of 2 prose recovered
    assert lr == pytest.approx(0.5)                               # 1 of 2 lineated (line4 missed)
