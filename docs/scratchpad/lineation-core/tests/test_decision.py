# research-pure: the pluggable cross-reader decision policies — anchor-led (unanimous + legacy) + control.
"""Locks the policies over a fixed roster (anchor + a support pair + a diagnostic — the readers are
test config). The default unanimous anchor-led rule accepts only when every present support agrees;
the legacy gate tolerates one dissent (`min_core_agree=2`, `conf_floor=0.7`) and routes when the
anchor is outvoted; the equal-majority control gives the anchor no privilege. Diagnostic readers
never decide."""
from __future__ import annotations

from lineation_core.annotations import PanelVote
from lineation_core.identity import LineId
from lineation_core.teacher import decision
from lineation_core.teacher.decision import (
    AnchorLedGates,
    AnchorLedPolicy,
    EqualMajorityPolicy,
    Outcome,
    PanelRoster,
    Reason,
)

LEGACY = AnchorLedPolicy("legacy", AnchorLedGates(min_support=2, min_core_agree=2, conf_floor=0.7,
                                              require_no_split=False))
CONTROL = EqualMajorityPolicy("equal_majority")

ROSTER = PanelRoster(anchor="grok", support=("gemini-pro", "ds-flash-text"))   # glm votes but isn't in the roster
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


# --- the pluggable policies: legacy anchor-led gate + the equal-majority control -----------------

def test_legacy_gate_accepts_a_tolerated_split():
    # 2-1 split, anchor in the majority: legacy (min_core_agree=2) ACCEPTS; unanimous routes to human.
    by = _by(_v("grok", "lineated", conf=0.9), _v("gemini-pro", "lineated"), _v("ds-flash-text", "prose"))
    d = LEGACY.decide(LID, by, ROSTER)
    assert d.outcome is Outcome.ACCEPT and d.reason is Reason.ACCEPTED_MAJORITY and d.label == "lineated"
    assert decision.decide_line(LID, by, ROSTER).reason is Reason.SUPPORT_DISAGREES  # unanimous differs


def test_legacy_gate_routes_when_anchor_is_outvoted():
    # anchor in the minority (both support disagree): a majority exists but anchor-led → human.
    by = _by(_v("grok", "lineated", conf=0.9), _v("gemini-pro", "prose"), _v("ds-flash-text", "prose"))
    d = LEGACY.decide(LID, by, ROSTER)
    assert d.outcome is Outcome.HUMAN and d.reason is Reason.ANCHOR_PANEL_SPLIT


def test_legacy_gate_low_anchor_confidence_routes_human():
    by = _by(_v("grok", "lineated", conf=0.5), _v("gemini-pro", "lineated"), _v("ds-flash-text", "lineated"))
    assert LEGACY.decide(LID, by, ROSTER).reason is Reason.LOW_CONFIDENCE      # conf_floor=0.7


def test_legacy_gate_missing_anchor_conf_routes_human():
    # the anchor voted but reported NO conf: with a floor set, the validated legacy gate FAULTS this
    # (CONF_MISSING, terminal) rather than silently passing the floor.
    by = _by(_v("grok", "lineated", conf=None), _v("gemini-pro", "lineated"), _v("ds-flash-text", "lineated"))
    d = LEGACY.decide(LID, by, ROSTER)
    assert d.outcome is Outcome.HUMAN and d.reason is Reason.CONF_MISSING
    assert Reason.CONF_MISSING in decision.TERMINAL_REASONS
    # with no floor (the default unanimous policy) a missing conf is irrelevant — it still accepts.
    assert decision.decide_line(LID, by, ROSTER).outcome is Outcome.ACCEPT


def test_equal_majority_accepts_against_the_anchor():
    # the control gives the anchor no privilege: a 2-1 majority against it is accepted.
    by = _by(_v("grok", "lineated"), _v("gemini-pro", "prose"), _v("ds-flash-text", "prose"))
    d = CONTROL.decide(LID, by, ROSTER)
    assert d.outcome is Outcome.ACCEPT and d.label == "prose"


def test_route_with_runs_any_policy():
    # grok carries a passing conf so this exercises LEGACY's split-tolerance, not its conf gate.
    votes = [_v("grok", "lineated", conf=0.9), _v("gemini-pro", "lineated"), _v("ds-flash-text", "prose")]
    assert decision.route_with(LEGACY, votes, ROSTER).accepted                 # legacy tolerates it
    assert decision.route_with(AnchorLedPolicy("u"), votes, ROSTER).human       # unanimous routes it
