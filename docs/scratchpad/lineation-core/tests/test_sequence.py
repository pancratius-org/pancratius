# research-pure: tests for the sequence-shaped prediction API (predict_document).
"""Proves run smoothing is a strict superset of the i.i.d. student (alpha=0), bounds runs at
structural slots, and soft-smooths (so confident within-run splits survive). Stub posterior →
no training needed, fast."""
from __future__ import annotations

import pytest
from lineation_core import sequence
from lineation_core.identity import LineId
from lineation_core.records import (
    EndPunct,
    IndentVsBook,
    InlineRun,
    LineFeatures,
    LineMeta,
    LineRecord,
    Role,
    SourceFate,
    SpacingVsBook,
)


def _feat(fill=0.4):
    return LineFeatures(
        fill=fill, wraps=False, char_len=10, word_count=2, end_punct=EndPunct.NONE,
        starts_lower=False, next_line_lower=False, enjambs=False, colon_opens=False,
        align="left", indent_vs_book=IndentVsBook.DEFAULT,
        spacing_after_vs_book=SpacingVsBook.TYPICAL, align_is_book_default=True,
        numbered=False, sub=0, n_subs=1, run_len=1, run_pos=0, prev_structural=False,
        next_structural=False, fill_pctile_in_book=0.5,
    )


def _rec(ordn, votable=True, role=Role.BODY, fill=0.4):
    return LineRecord(
        id=LineId("ru", "01", ordn, 0), text=f"line {ordn}",
        inlines=(InlineRun(f"line {ordn}", ""),), role=role, votable=votable,
        source_fate=SourceFate.NORMAL, features=_feat(fill),
        paragraph_text_hash="h", line_text_hash="h",
        meta=LineMeta(style_id="", block_index=ordn, src_ordinal=ordn),
    )


class StubPosterior:
    def __init__(self, by_fill: dict[float, float]):
        self.by_fill = by_fill

    def __call__(self, features: LineFeatures) -> float:
        return self.by_fill[features.fill]


def test_alpha_zero_is_pure_iid_superset():
    recs = [_rec(1, fill=0.1), _rec(2, fill=0.9), _rec(3, fill=0.1)]
    post = StubPosterior({0.1: 0.2, 0.9: 0.8})
    out = sequence.predict_document(recs, post, alpha=0.0)
    assert [d.label for d in out] == ["prose", "lineated", "prose"]
    assert [round(d.posterior, 3) for d in out] == [0.2, 0.8, 0.2]
    assert all(d.posterior == d.base_posterior for d in out)


def test_runs_bounded_by_nonvotable():
    recs = [_rec(1), _rec(2), _rec(3, votable=False, role=Role.HEADING), _rec(4), _rec(5)]
    post = StubPosterior({0.4: 0.5})
    out = sequence.predict_document(recs, post, alpha=0.0)
    assert len(out) == 4
    run_ids = [d.run_id for d in out]
    assert run_ids == [0, 0, 1, 1]


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


def test_nonvotable_lines_not_emitted():
    recs = [_rec(1, votable=False, role=Role.BLANK), _rec(2)]
    out = sequence.predict_document(recs, StubPosterior({0.4: 0.5}), alpha=0.0)
    assert [d.id.src_ordinal for d in out] == [2]
