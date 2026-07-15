# research-pure: tests for the sequence-shaped prediction API (predict_document).
"""Proves run smoothing is a strict superset of the i.i.d. student (alpha=0), bounds runs at
structural slots, and soft-smooths (so confident within-run splits survive). Stub posterior →
no training needed, fast."""
from __future__ import annotations

from collections.abc import Sequence

import pytest
from intent_ai import identity, sequence
from intent_ai.identity import LineId
from intent_ai.records import (
    EndPunct,
    IndentVsBook,
    LineFeatures,
    LineRecord,
    Role,
    SpacingVsBook,
)


def _feat(fill=0.4, *, run_len=3, run_pos=1):
    return LineFeatures(
        fill=fill, wraps=False, char_len=10, word_count=2, end_punct=EndPunct.NONE,
        starts_lower=False, next_line_lower=False, enjambs=False, colon_opens=False,
        align="left", indent_vs_book=IndentVsBook.DEFAULT,
        spacing_after_vs_book=SpacingVsBook.TYPICAL, align_is_book_default=True,
        sub=0, n_subs=1, run_len=run_len, run_pos=run_pos,
        fill_pctile_in_book=0.5,
    )


def _rec(ordn, role=Role.BODY, fill=0.4, *, book="01", **feature_kw):
    return LineRecord(
        id=LineId("ru", book, ordn, 0), text=f"line {ordn}",
        role=role, features=_feat(fill, **feature_kw),
        line_text_hash=identity.text_hash(f"line {ordn}"),
    )


class StubPosterior:
    def __init__(self, by_fill: dict[float, float]):
        self.by_fill = by_fill
        self.calls: list[tuple[LineFeatures, ...]] = []

    def posteriors(self, features: Sequence[LineFeatures]) -> list[float]:
        self.calls.append(tuple(features))
        return [self.by_fill[feature.fill] for feature in features]


def test_alpha_zero_is_pure_iid_superset():
    recs = [_rec(1, fill=0.1), _rec(2, fill=0.9), _rec(3, fill=0.1)]
    post = StubPosterior({0.1: 0.2, 0.9: 0.8})
    out = sequence.predict_document(recs, post, alpha=0.0)
    assert [d.label for d in out] == ["prose", "lineated", "prose"]
    assert [round(d.posterior, 3) for d in out] == [0.2, 0.8, 0.2]
    assert all(d.posterior == d.base_posterior for d in out)


def test_runs_bounded_by_nonvotable():
    recs = [
        _rec(1, fill=0.1), _rec(2, fill=0.3), _rec(3, role=Role.HEADING),
        _rec(4, fill=0.7), _rec(5, fill=0.9),
    ]
    post = StubPosterior({fill: fill for fill in (0.1, 0.3, 0.7, 0.9)})
    out = sequence.predict_document(recs, post, alpha=1.0)
    assert len(out) == 4
    assert [decision.posterior for decision in out] == pytest.approx([0.2, 0.2, 0.8, 0.8])
    assert post.calls == [tuple(record.features for record in recs if record.votable)]


def test_runs_preserve_an_invisible_structural_boundary():
    recs = [
        _rec(1, fill=0.1),
        _rec(2, fill=0.3, run_len=2, run_pos=1),
        _rec(4, fill=0.7, run_len=2, run_pos=0),
        _rec(5, fill=0.9),
    ]
    post = StubPosterior({fill: fill for fill in (0.1, 0.3, 0.7, 0.9)})
    out = sequence.predict_document(recs, post, alpha=1.0)
    assert [decision.posterior for decision in out] == pytest.approx([0.2, 0.2, 0.8, 0.8])


def test_soft_smoothing_pulls_outlier_toward_run_mean():
    recs = [_rec(i, fill=float(i)) for i in (1, 2, 3, 4, 5)]
    post = StubPosterior({1.0: 0.9, 2.0: 0.9, 3.0: 0.2, 4.0: 0.9, 5.0: 0.9})
    iid = sequence.predict_document(recs, post, alpha=0.0)
    assert iid[2].label == "prose"
    run_mean = (0.9 * 4 + 0.2) / 5  # = 0.76
    sm = sequence.predict_document(recs, post, alpha=0.5)
    assert sm[2].base_posterior == 0.2
    assert sm[2].posterior == pytest.approx(0.5 * 0.2 + 0.5 * run_mean)
    strong = sequence.predict_document(recs, post, alpha=0.8)
    assert strong[2].posterior > 0.5 and strong[2].label == "lineated"


def test_hard_consensus_at_alpha_one_uses_run_mean_only():
    recs = [_rec(i, fill=float(i)) for i in (1, 2, 3)]
    post = StubPosterior({1.0: 0.9, 2.0: 0.1, 3.0: 0.9})
    out = sequence.predict_document(recs, post, alpha=1.0)
    mean = (0.9 + 0.1 + 0.9) / 3
    assert all(d.posterior == pytest.approx(mean) for d in out)


def test_genuine_split_survives_when_model_is_confident():
    recs = [_rec(i, fill=float(i)) for i in (1, 2, 3, 4, 5)]
    post = StubPosterior({1.0: 0.05, 2.0: 0.05, 3.0: 0.97, 4.0: 0.97, 5.0: 0.97})
    out = sequence.predict_document(recs, post, alpha=0.3)
    labels = [d.label for d in out]
    assert labels == ["prose", "prose", "lineated", "lineated", "lineated"]


def test_alpha_out_of_range_raises():
    with pytest.raises(ValueError):
        sequence.predict_document([_rec(1)], StubPosterior({0.4: 0.5}), alpha=1.5)


def test_threshold_out_of_range_raises():
    with pytest.raises(ValueError):
        sequence.predict_document([_rec(1)], StubPosterior({0.4: 0.5}), threshold=-0.1)


def test_nonvotable_lines_not_emitted():
    recs = [_rec(1, role=Role.CONTEXT), _rec(2)]
    out = sequence.predict_document(recs, StubPosterior({0.4: 0.5}), alpha=0.0)
    assert [d.id.src_ordinal for d in out] == [2]


def test_score_document_fails_loud_on_wrong_batch_cardinality():
    class ShortScorer:
        def posteriors(self, features):
            return [0.5] * max(0, len(features) - 1)

    with pytest.raises(ValueError, match="1 values for 2 votable"):
        sequence.score_document([_rec(1), _rec(2)], ShortScorer())


def test_structural_only_document_does_not_call_scorer():
    post = StubPosterior({})
    assert sequence.predict_document([_rec(1, role=Role.HEADING)], post) == []
    assert post.calls == []


def test_scored_document_rejects_scores_from_another_record_order():
    first, second = _rec(1), _rec(2)
    with pytest.raises(ValueError, match="document order"):
        sequence.ScoredDocument(
            (first, second),
            (sequence.BasePosterior(second.id, 0.8), sequence.BasePosterior(first.id, 0.2)),
        )


def test_scored_document_is_one_unique_book():
    record = _rec(1)
    score = sequence.BasePosterior(record.id, 0.5)
    with pytest.raises(ValueError, match="unique"):
        sequence.ScoredDocument((record, record), (score, score))
    other_book = _rec(1, book="02")
    with pytest.raises(ValueError, match="mix"):
        sequence.ScoredDocument(
            (record, other_book),
            (score, sequence.BasePosterior(other_book.id, 0.5)),
        )
    later = _rec(2)
    with pytest.raises(ValueError, match="source-ordered"):
        sequence.ScoredDocument(
            (later, record),
            (sequence.BasePosterior(later.id, 0.5), score),
        )


@pytest.mark.parametrize("value", [-0.1, 1.1, float("nan"), float("inf")])
def test_base_posterior_rejects_non_probability(value):
    with pytest.raises(ValueError, match="posterior"):
        sequence.BasePosterior(_rec(1).id, value)
