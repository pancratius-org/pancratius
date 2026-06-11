# research-pure: the aligned (truth ⋈ votes) evaluation set.
"""Locks the aligned-set join over the REAL committed data — the population every policy replay is
scored on. Asserts the headline shape (529 lines, the class imbalance surfaced, contested stratum
marked) so a silent change to the join or the committed truth is caught."""
from __future__ import annotations

from lineation_core.evaluation import datasets


def test_aligned_set_join_locked():
    a = datasets.from_store()
    # 529 = the historical 515 + 14 voted lines whose human labels lived only in the contested
    # eval set until they were homed in labels.jsonl (holdout) — a deliberate re-lock.
    assert a.n_total == 529                            # lines with BOTH a label and >=1 vote
    # 59/470 after the recency resolution (latest-mtime human pass wins): 11 aligned lines went
    # prose→lineated and 3 lineated→prose.
    assert a.n_prose == 59 and a.n_lineated == 470     # the imbalance surfaced up front
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


# --- the eval-slice join: one truth store, fail-loud membership ---------------------------------

def test_eval_slice_reads_truth_from_the_one_store():
    """Every committed slice joins cleanly: full membership coverage, truth from labels.jsonl."""
    from lineation_core.annotations import load_labels

    human = {g.id: g.label for g in load_labels().labels}
    for name in ("contested", "reader_bench", "prompt_structural"):
        s = datasets.eval_slice(name)
        assert s.lines and set(s.truth) == set(s.lines)
        assert all(s.truth[lid] == human[lid] for lid in s.lines)


def test_eval_slice_fails_loud_on_a_member_with_no_label(tmp_path):
    import json

    import pytest
    from lineation_core.identity import LineId

    labeled = LineId.mapped("ru", "57", 1, 0)
    unlabeled = LineId.mapped("ru", "57", 2, 0)
    (tmp_path / "eval_sets").mkdir()
    (tmp_path / "eval_sets" / "s.json").write_text(
        json.dumps([labeled.as_key(), unlabeled.as_key()]))
    (tmp_path / "labels.jsonl").write_text(
        json.dumps({"id": labeled.as_key(), "label": "prose", "source": "human"}) + "\n")
    with pytest.raises(ValueError, match="no label"):
        datasets.eval_slice("s", annotations=tmp_path)


def test_truth_fingerprint_pins_the_labels_not_just_the_membership():
    """Same membership, one flipped label → a different fingerprint — the freeze model: an eval
    is frozen by PINNING its truth, not by carrying a second copy of the labels."""
    from lineation_core.identity import LineId

    a = LineId.mapped("ru", "57", 1, 0)
    b = LineId.mapped("ru", "57", 2, 0)
    s1 = datasets.EvalSlice("s", (a, b), {a: "prose", b: "lineated"})
    s2 = datasets.EvalSlice("s", (a, b), {a: "prose", b: "prose"})
    assert datasets.truth_fingerprint(s1) != datasets.truth_fingerprint(s2)
    assert datasets.truth_fingerprint(s1) == datasets.truth_fingerprint(s1)
