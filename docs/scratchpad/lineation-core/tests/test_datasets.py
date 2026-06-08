# research-pure: the aligned (truth ⋈ votes) evaluation set.
"""Locks the aligned-set join over the REAL committed data — the population every policy replay is
scored on. Asserts the headline shape (515 lines, the class imbalance surfaced, contested stratum
marked) so a silent change to the join or the committed truth is caught."""
from __future__ import annotations

from lineation_core.evaluation import datasets


def test_aligned_set_join_locked():
    a = datasets.from_store()
    assert a.n_total == 515                            # lines with BOTH a label and >=1 vote
    assert a.n_prose == 63 and a.n_lineated == 452     # the imbalance surfaced up front
    assert a.n_prose + a.n_lineated == a.n_total
    # every aligned line has truth, >=1 vote, and a difficulty stratum.
    assert all(ln.votes and ln.truth in ("prose", "lineated") for ln in a.lines)
    assert all(ln.stratum in ("contested", "easy") for ln in a.lines)
    # both strata are populated (the contested slice overlaps the aligned lines).
    strata = {ln.stratum for ln in a.lines}
    assert strata == {"contested", "easy"}


def test_aligned_lines_are_in_document_order():
    a = datasets.from_store()
    ids = [ln.id for ln in a.lines]
    assert ids == sorted(ids)
