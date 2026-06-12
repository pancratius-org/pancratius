# research-pure: pins the production importer's lineation floors — the converter-change gate.
"""Any `pancratius/` change that can move the importer's lineation verdict must hold these
floors. The memberships are FROZEN, so truth growth never moves the gate; only a converter
change or a member's deliberate re-adjudication can — both are investigated, and an intentional
improvement ratchets the floor UP in the same change. `prose_recall` guards the trusted
direction — "det=lineated is essentially never wrong" is the budget ladder's load-bearing
beam — and `n_uncovered == 0` proves every member line still receives a verdict."""
from __future__ import annotations

import pytest

from lineation_core.evaluation import det_regression

# (balanced_acc, prose_recall) floors per frozen membership — measured 2026-06-12 with the first
# correction sidecars applied (importer + corrections IS the scored system), truncated to 6
# decimals so the exact value passes and any real drop fails. prose_recall = 1.0 everywhere:
# the trusted direction is perfect on gold; any false-lineated regression fails immediately.
FLOORS = {
    "det-gate": (0.972537, 1.0),
    "reader_bench": (0.963483, 1.0),
    "contested": (0.918960, 1.0),
    "prompt_structural": (0.812500, 1.0),
}


@pytest.fixture(scope="module")
def scores() -> dict[str, det_regression.DetScore]:
    return {s.name: s for s in det_regression.score_all()}


def test_every_truth_set_is_scored(scores):
    assert set(scores) == set(FLOORS)


@pytest.mark.parametrize("name", sorted(FLOORS))
def test_det_verdict_holds_its_floor(scores, name):
    s = scores[name]
    bal_floor, prose_floor = FLOORS[name]
    assert s.n_uncovered == 0, f"{name}: {s.n_uncovered} truth lines lost their verdict"
    assert s.metrics.balanced_acc >= bal_floor, (name, s.metrics)
    assert s.metrics.prose_recall >= prose_floor, (name, s.metrics)
