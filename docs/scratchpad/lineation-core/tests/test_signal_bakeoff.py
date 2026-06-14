# research-pure: the E2 bakeoff's pure stats + its load-bearing frozen-leak guard, without DOCX.
"""The bakeoff sizes paid work, so its frozen-leak guard must FAIL LOUD and its ranking math must be
right. The stats are stdlib-pure (no DOCX, no sklearn); the leak guard runs before any DOCX read, so
a synthetic eval-set pair exercises it with no corpus."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from lineation_core.evaluation import signal_bakeoff as sb


def _write_set(ann: Path, name: str, keys: list[list]) -> None:
    (ann / "eval_sets").mkdir(parents=True, exist_ok=True)
    (ann / "eval_sets" / f"{name}.json").write_text(json.dumps(keys))


def test_frozen_leak_fails_loud(tmp_path: Path) -> None:
    shared = ["ru", "01", 5, 0]
    _write_set(tmp_path, sb.WORKING_SET, [shared, ["ru", "01", 6, 0]])
    _write_set(tmp_path, sb.FROZEN_SET, [shared, ["ru", "01", 7, 0]])   # overlaps on `shared`
    with pytest.raises(AssertionError, match="leaks"):
        sb._build_rows(annotations=tmp_path)


def test_roc_auc_perfect_and_random() -> None:
    # a signal that perfectly ranks positives above negatives → AUC 1.0; reversed → 0.0
    assert sb.roc_auc([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1]) == 1.0
    assert sb.roc_auc([0.9, 0.8, 0.2, 0.1], [0, 0, 1, 1]) == 0.0
    # all-ties → 0.5 (mid-ranks); single-class → undefined
    assert sb.roc_auc([0.5, 0.5, 0.5, 0.5], [0, 0, 1, 1]) == 0.5
    assert sb.roc_auc([0.1, 0.2], [1, 1]) is None


def _fork(*, inside: bool) -> sb.PhiFork:
    """A minimal PhiFork — the router only reads `inside_phi` (for the audit/co-signal note)."""
    table = sb.cross_terciles([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])
    return sb.PhiFork(rho=None, table=table, diag_monotone=True, inside_phi=inside, n=3)


_RECON = sb.CorpusRecon(det_prose=69194, disagree_prose=32090, disagree_lineated=11813)


def test_router_rejects_gate_circular_auc_all_leader() -> None:
    """The router ORDERS a whole-slice sweep, so it must be robust on INDEPENDENT (human) truth. A
    signal that wins AUC(all) but collapses to ~chance on the human subset (the det_student_disagree
    failure mode: AUC(all) edge from ranking det=lineated by 1−posterior) must NOT be chosen — this
    pins that class of bug so it cannot regress."""
    circular = sb.SignalScore(name="det_student_disagree", auc_all=0.918, auc_human=0.55)
    robust = sb.SignalScore(name="suspicion_v0", auc_all=0.866, auc_human=0.869)
    scored = [circular, robust]  # the AUC(all) leader is the circular one

    router, rationale, suspect, basis = sb._choose_router(scored, _fork(inside=False), _RECON)
    assert "suspicion_v0" in router            # the robust signal orders the sweep
    assert "det_student_disagree" not in router
    # the rationale names the disqualified leader and the whole-band sweep, not a gate
    assert "det_student_disagree" in rationale and "gate-circular" in rationale
    assert suspect == _RECON.det_prose         # sweep the WHOLE band, sized from the recon counts


def test_router_corpus_counts_come_from_recon() -> None:
    """The corpus projection is READ from the recon scorecard, never hard-coded — a different recon
    flows straight through to the suspect size and basis."""
    recon = sb.CorpusRecon(det_prose=100, disagree_prose=40, disagree_lineated=20)
    robust = sb.SignalScore(name="suspicion_v0", auc_all=0.8, auc_human=0.8)
    _, rationale, suspect, basis = sb._choose_router([robust], _fork(inside=False), recon)
    assert suspect == 100 and "100" in rationale and "40" in basis and "20" in basis


def test_router_raises_when_no_signal_clears_human_floor() -> None:
    """If every signal is ~chance on independent truth, no honest router can be named — fail loud
    rather than ship a gate-circular AUC(all) leader."""
    scored = [sb.SignalScore(name="a", auc_all=0.9, auc_human=0.5),
              sb.SignalScore(name="b", auc_all=0.8, auc_human=None)]
    with pytest.raises(AssertionError, match="chance floor"):
        sb._choose_router(scored, _fork(inside=False), _RECON)


def test_spearman_monotone_and_constant() -> None:
    assert sb.spearman([1.0, 2.0, 3.0], [10.0, 20.0, 30.0]) == 1.0
    assert sb.spearman([1.0, 2.0, 3.0], [30.0, 20.0, 10.0]) == -1.0
    assert sb.spearman([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None  # constant → undefined


def test_terciles_equal_count() -> None:
    t = sb.terciles([5.0, 1.0, 9.0, 2.0, 8.0, 3.0])   # n=6 → two per bin
    assert sorted(t) == [0, 0, 1, 1, 2, 2]
    # the largest value lands in the top tercile, the smallest in the bottom
    vals = [5.0, 1.0, 9.0, 2.0, 8.0, 3.0]
    assert t[vals.index(9.0)] == 2 and t[vals.index(1.0)] == 0


def test_cross_terciles_diagonal_and_monotone() -> None:
    # perfectly correlated → all mass on the diagonal, monotone, zero off-diagonal
    u = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
    table = sb.cross_terciles(u, list(u))
    assert table.off_diagonal_frac == 0.0
    assert sb._monotone_diag(table) is True
    # anti-correlated → mass on the anti-diagonal, not monotone
    anti = sb.cross_terciles(u, list(reversed(u)))
    assert anti.off_diagonal_frac > 0.0
    assert sb._monotone_diag(anti) is False
