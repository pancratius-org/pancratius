# research-pure: pins the production importer's lineation floors — the converter-change gate.
"""The source-v3 paragraph-unit receipt for deterministic lineation quality.

Fold is a paragraph decision, so line truth is reduced before scoring. Exact scored, mixed,
incomplete, and uncovered counts pin that denominator; the quality floors apply only to unanimous
paragraph truth the compiler can represent.
"""
from __future__ import annotations

import pytest
from intent_ai.evaluation import det_regression

pytestmark = pytest.mark.corpus_source

# (scored, mixed, incomplete, balanced-accuracy floor, prose-recall floor). Measured from the
# repaired post-fold seam. `contested` has one genuinely mixed paragraph (ru37:414) and retains
# one representable false fold (holdout ru24:1522); neither is hidden by lowering a floor.
EXPECTATIONS = {
    "det-gate": (601, 0, 4, 0.973684, 1.0),
    "reader_bench": (253, 0, 8, 0.966666, 1.0),
    "contested": (395, 1, 4, 0.927902, 0.989583),
    "prompt_structural": (32, 0, 0, 0.812500, 1.0),
}


@pytest.fixture(scope="module")
def scores() -> dict[str, det_regression.DetScore]:
    return {s.name: s for s in det_regression.score_all()}


def test_every_truth_set_is_scored(scores):
    assert set(scores) == set(EXPECTATIONS)


@pytest.mark.parametrize("name", sorted(EXPECTATIONS))
def test_det_verdict_holds_its_floor(scores, name):
    s = scores[name]
    n, mixed, incomplete, bal_floor, prose_floor = EXPECTATIONS[name]
    assert (s.n, s.n_uncovered, s.n_mixed, s.n_incomplete) == (n, 0, mixed, incomplete)
    assert s.metrics.balanced_acc >= bal_floor, (name, s.metrics)
    assert s.metrics.prose_recall >= prose_floor, (name, s.metrics)
