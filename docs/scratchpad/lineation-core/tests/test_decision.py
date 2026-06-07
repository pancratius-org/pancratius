# research-pure: the grok-led cross-reader decision — accept on full support, route splits to human.
"""Locks the validated policy: the anchor (grok) leads, core support (gemini-pro, ds-flash-text) is a
disagreement detector, diagnostic readers (glm) never decide. ACCEPT only when the anchor has a
stable vote, enough support voted, it clears the optional confidence floor, AND no support reader
splits from it; otherwise ROUTE TO HUMAN. Equal-majority is deliberately NOT the rule."""
from __future__ import annotations

from lineation_core.identity import LineId
from lineation_core.panel_votes import PanelVote
from lineation_core.teacher import decision
from lineation_core.teacher.decision import Outcome, PanelRoster, Reason

ROSTER = PanelRoster(anchor="grok", support=("gemini-pro", "ds-flash-text"), diagnostic=("glm",))
LID = LineId.mapped("ru", "57", 10, 0)


def _v(tag: str, label: str, conf: float | None = None, lid: LineId = LID) -> PanelVote:
    return PanelVote(id=lid, tag=tag, label=label, conf=conf)


def _by(*votes: PanelVote) -> dict[str, PanelVote]:
    return {v.tag: v for v in votes}


def test_accept_when_anchor_and_all_support_agree():
    d = decision.decide_line(LID, _by(_v("grok", "lineated"), _v("gemini-pro", "lineated"),
                                      _v("ds-flash-text", "lineated")), ROSTER)
    assert d.outcome is Outcome.ACCEPT and d.reason is Reason.FULL_SUPPORT and d.label == "lineated"


def test_any_support_split_routes_human():
    d = decision.decide_line(LID, _by(_v("grok", "lineated"), _v("gemini-pro", "lineated"),
                                      _v("ds-flash-text", "prose")), ROSTER)
    assert d.outcome is Outcome.HUMAN and d.reason is Reason.SUPPORT_DISAGREES and d.label is None


def test_anchor_abstain_routes_human():
    d = decision.decide_line(LID, _by(_v("gemini-pro", "prose"), _v("ds-flash-text", "prose")), ROSTER)
    assert d.outcome is Outcome.HUMAN and d.reason is Reason.ANCHOR_ABSTAIN


def test_insufficient_coverage_routes_human():
    d = decision.decide_line(LID, _by(_v("grok", "lineated")), ROSTER, min_support=1)
    assert d.outcome is Outcome.HUMAN and d.reason is Reason.INSUFFICIENT_COVERAGE


def test_glm_is_diagnostic_only():
    # glm disagreeing must NOT route to human...
    d = decision.decide_line(LID, _by(_v("grok", "lineated"), _v("gemini-pro", "lineated"),
                                      _v("ds-flash-text", "lineated"), _v("glm", "prose")), ROSTER)
    assert d.outcome is Outcome.ACCEPT
    # ...and glm cannot stand in for missing core support.
    d2 = decision.decide_line(LID, _by(_v("grok", "lineated"), _v("glm", "lineated")), ROSTER)
    assert d2.reason is Reason.INSUFFICIENT_COVERAGE


def test_confidence_floor_routes_human_only_when_set():
    by = _by(_v("grok", "lineated", conf=0.4), _v("gemini-pro", "lineated"),
             _v("ds-flash-text", "lineated"))
    assert decision.decide_line(LID, by, ROSTER, min_conf=0.6).reason is Reason.LOW_CONFIDENCE
    assert decision.decide_line(LID, by, ROSTER).outcome is Outcome.ACCEPT      # no floor by default


def test_route_partitions_lines_and_ignores_diagnostic():
    a, b = LineId.mapped("ru", "57", 10, 0), LineId.mapped("ru", "57", 11, 0)
    votes = [
        _v("grok", "lineated", lid=a), _v("gemini-pro", "lineated", lid=a),
        _v("ds-flash-text", "lineated", lid=a),                          # a: full support → accept
        _v("grok", "lineated", lid=b), _v("gemini-pro", "prose", lid=b),
        _v("ds-flash-text", "lineated", lid=b), _v("glm", "prose", lid=b),  # b: a support split → human
    ]
    r = decision.route(votes, ROSTER)
    assert [d.id for d in r.accepted] == [a] and r.accepted[0].label == "lineated"
    assert [d.id for d in r.human] == [b] and r.human[0].reason is Reason.SUPPORT_DISAGREES
