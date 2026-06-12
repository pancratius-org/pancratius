# research-pure: proofs for the tier-0/tier-1 join, the v0 suspicion order, and the census.
"""`join_rows`/`summarize`/`suspicion_v0` and the corpus aggregations are pure, so their
contracts are proven on synthetic records — no DOCX, no store. The IO shell (`scan_book`) is
exercised corpus-wide by the driver, not re-proven here."""
from __future__ import annotations

from lineation_core import recon
from lineation_core.identity import LineId
from lineation_core.records import (
    Align,
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
from lineation_core.recon import Mask, Tier0


def _feat(fill=0.4, align=Align.LEFT, wraps=False):
    return LineFeatures(
        fill=fill, wraps=wraps, char_len=10, word_count=2, end_punct=EndPunct.NONE,
        starts_lower=False, next_line_lower=False, enjambs=False, colon_opens=False,
        align=align, indent_vs_book=IndentVsBook.DEFAULT,
        spacing_after_vs_book=SpacingVsBook.TYPICAL, align_is_book_default=True,
        numbered=False, sub=0, n_subs=1, run_len=1, run_pos=0, prev_structural=False,
        next_structural=False, fill_pctile_in_book=0.5,
    )


def _rec(ordn, sub=0, votable=True, role=Role.BODY, fate=SourceFate.NORMAL, book="01",
         **feat_kw):
    return LineRecord(
        id=LineId("ru", book, ordn, sub), text=f"line {ordn}.{sub}",
        inlines=(InlineRun(f"line {ordn}.{sub}", ""),), role=role, votable=votable,
        source_fate=fate, features=_feat(**feat_kw),
        paragraph_text_hash="h", line_text_hash="h",
        meta=LineMeta(style_id="", block_index=ordn, src_ordinal=ordn),
    )


def test_join_covers_every_votable_line_and_only_them():
    recs = [
        _rec(1), _rec(2),
        _rec(3, votable=False, role=Role.HEADING),                       # structure: no row
        _rec(4, votable=False, role=Role.BODY, fate=SourceFate.UNMAPPED),  # held: no row
    ]
    rows = recon.join_rows(recs, det={1: True, 2: False},
                           mask={1: Mask.BODY, 2: Mask.BODY}, posteriors={})
    assert [r.id.src_ordinal for r in rows] == [1, 2]
    assert rows[0].det is Tier0.LINEATED and rows[1].det is Tier0.PROSE


def test_uncovered_ordinal_and_absent_mask_are_flagged_never_guessed():
    rows = recon.join_rows([_rec(7)], det={}, mask={}, posteriors={})
    (row,) = rows
    assert row.det is Tier0.UNCOVERED
    assert row.mask is Mask.REVIEW
    assert row.suspicion > 1.0  # the auto-suspect band sorts above every covered line


def test_subs_share_their_ordinal_verdict():
    recs = [_rec(5, sub=0), _rec(5, sub=1), _rec(5, sub=2)]
    rows = recon.join_rows(recs, det={5: True}, mask={5: Mask.BODY}, posteriors={})
    assert {r.det for r in rows} == {Tier0.LINEATED}


def test_suspicion_v0_total_order_matches_the_ladder():
    # uncovered/review > det=prose-by-posterior > det=lineated (accepted at 0)
    assert recon.suspicion_v0(Tier0.UNCOVERED, Mask.BODY, 0.1) > 1.0
    assert recon.suspicion_v0(Tier0.PROSE, Mask.REVIEW, 0.1) > 1.0
    assert recon.suspicion_v0(Tier0.PROSE, Mask.BODY, 0.9) == 0.9
    assert recon.suspicion_v0(Tier0.PROSE, Mask.BODY, 0.1) == 0.1
    assert recon.suspicion_v0(Tier0.PROSE, Mask.BODY, None) == 0.5  # no model ≠ silently safe
    assert recon.suspicion_v0(Tier0.LINEATED, Mask.BODY, 0.1) == 0.0


def test_summarize_census_disagreement_sides_and_desync_counter():
    recs = [
        _rec(1, align=Align.JUST), _rec(2, align=Align.JUST), _rec(3, align=Align.CENTER),
        _rec(4),
        _rec(9, votable=False, role=Role.BODY, fate=SourceFate.UNMAPPED),
    ]
    # 4 uncovered; 99 matches no record (real desync); 50 is a span-interior blank (not desync)
    det = {1: False, 2: False, 3: True, 50: True, 99: True}
    mask = {1: Mask.BODY, 2: Mask.BODY, 3: Mask.BODY}  # 4 absent → review
    posteriors = {
        LineId("ru", "01", 1, 0): 0.9,   # det=prose, student says lineated → disagree_prose
        LineId("ru", "01", 2, 0): 0.1,   # agree prose
        LineId("ru", "01", 3, 0): 0.2,   # det=lineated, student says prose → disagree_lineated
        LineId("ru", "01", 4, 0): 0.5,
    }
    rows = recon.join_rows(recs, det, mask, posteriors)
    s = recon.summarize("01", "ru", recs, rows, det, frozenset({50}))
    assert (s.n_votable, s.n_records) == (4, 5)
    assert (s.det_lineated, s.det_prose, s.det_uncovered) == (1, 2, 1)
    assert (s.disagree_prose, s.disagree_lineated) == (1, 1)
    assert s.n_unmapped_records == 1
    assert s.n_mask_review == 1
    assert s.n_det_unjoined == 1                   # ordinal 99: importer covers, producer lost
    assert s.lineated_pct == 1 / 3                 # of covered votable lines only
    assert s.pct_align_just == 0.5


def test_summarize_survives_a_book_with_no_votable_lines():
    recs = [_rec(1, votable=False, role=Role.HEADING)]
    s = recon.summarize("01", "ru", recs, [], {})  # empty_ordinals defaults empty
    assert (s.n_votable, s.lineated_pct, s.fill_median, s.posterior_mean) == (0, 0.0, 0.0, None)


def test_line_recon_round_trips_through_dict():
    row = recon.LineRecon(id=LineId("en", "75", 12, 1), det=Tier0.PROSE, mask=Mask.BODY,
                          posterior=0.25, suspicion=0.25)
    assert recon.LineRecon.from_dict(row.to_dict()) == row
    held = recon.LineRecon(id=LineId("ru", "01", 3, 0), det=Tier0.UNCOVERED,
                           mask=Mask.REVIEW, posterior=None, suspicion=1.5)
    assert recon.LineRecon.from_dict(held.to_dict()) == held


def _summary(book, lang, *, just=0.5, wraps=0.5, fill=0.5, lin=50, pro=50):
    return recon.BookRecon(
        book_id=book, lang=lang, n_records=100, n_votable=100, n_unmapped_records=0,
        n_det_unjoined=0, det_lineated=lin, det_prose=pro, det_uncovered=0, n_mask_review=0,
        disagree_prose=0, disagree_lineated=0, posterior_mean=0.5,
        pct_align_just=just, pct_align_left=1 - just, pct_align_center=0.0,
        pct_wraps=wraps, fill_median=fill,
    )


def test_corpus_totals_and_en_envelope_flagging():
    ru = [_summary(f"{i:02d}", "ru", just=0.4 + i / 100) for i in range(1, 21)]
    inside = _summary("75", "en", just=0.5)
    outside = _summary("76", "en", just=0.99)
    summaries = [*ru, inside, outside]

    totals = recon.corpus_totals(summaries)
    assert totals["n_votable"] == 100 * len(summaries)

    env = recon.ru_envelope(summaries)
    lo, hi = env["pct_align_just"]
    assert 0.4 < lo < hi < 0.61                     # the 5–95% band, not min..max
    flagged = recon.en_outliers(summaries, env)
    assert [o["book_id"] for o in flagged] == ["76"]
    assert "pct_align_just" in flagged[0]["outside"]
