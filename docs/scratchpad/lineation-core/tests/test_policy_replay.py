# research-pure: the decision-policy REPLAY harness — offline policy comparison over committed data.
"""Two layers of lock:

  - the TOML config loader + the pure replay/score on small synthetic data (fast, exact);
  - the HEADLINE LOCK on the REAL committed aligned set (~515 lines) for the three example policies,
    asserting the better-or-worse FINDING as robust directional invariants (legacy routes far less
    than unanimous; legacy is at least as accurate as the equal-majority control; legacy makes ZERO
    prose→lineated false accepts) plus that the policies are genuinely RANKABLE (they diverge on >30
    lines), rather than brittle exact counts the data could legitimately shift.
"""
from __future__ import annotations

import pytest
from lineation_core.annotations import PanelVote
from lineation_core.evaluation import datasets
from lineation_core.evaluation.datasets import AlignedLine, AlignedSet
from lineation_core.evaluation.policy_replay import (
    PolicySpec,
    load_policy_specs,
    replay,
    replay_and_score,
)
from lineation_core.identity import LineId
from lineation_core.teacher.decision import (
    AnchorLedGates,
    AnchorLedPolicy,
    EqualMajorityPolicy,
    PanelRoster,
)

RECIPE = """
[roster]
anchor = "grok"
support = ["deepseek", "gemini"]

[[policy]]
name = "legacy"
kind = "anchor_led"
  [policy.params]
  min_support = 2
  min_core_agree = 2
  conf_floor = 0.7
  require_no_split = false

[[policy]]
name = "unanimous"
kind = "anchor_led"
  [policy.params]
  min_support = 2
  require_no_split = true

[[policy]]
name = "equal_majority"
kind = "equal_majority"
"""


# --- the TOML config loader ---------------------------------------------------------------------

def test_loader_builds_the_three_specs():
    specs = load_policy_specs(RECIPE)
    assert [s.name for s in specs] == ["legacy", "unanimous", "equal_majority"]
    assert all(s.roster == PanelRoster(anchor="grok", support=("deepseek", "gemini")) for s in specs)
    legacy = specs[0].policy
    assert isinstance(legacy, AnchorLedPolicy)
    assert legacy.gates.conf_floor == 0.7 and legacy.gates.require_no_split is False
    assert isinstance(specs[2].policy, EqualMajorityPolicy)


def test_loader_rejects_unknown_kind():
    bad = '[roster]\nanchor="grok"\nsupport=["deepseek"]\n[[policy]]\nname="x"\nkind="bogus"\n'
    with pytest.raises(ValueError, match="unknown policy kind"):
        load_policy_specs(bad)


def test_loader_rejects_anchor_in_support():
    bad = '[roster]\nanchor="grok"\nsupport=["grok"]\n[[policy]]\nname="x"\nkind="equal_majority"\n'
    with pytest.raises(ValueError, match="also appears in support"):
        load_policy_specs(bad)


def test_loader_rejects_roster_reader_absent_from_data():
    with pytest.raises(ValueError, match="not present in the data"):
        load_policy_specs(RECIPE, present_readers=frozenset({"grok", "deepseek"}))  # gemini missing


# --- the pure replay/score on synthetic lines ---------------------------------------------------

def _line(ordinal: int, truth: str, votes: tuple[PanelVote, ...], stratum: str = "easy") -> AlignedLine:
    return AlignedLine(id=LineId.mapped("ru", "57", ordinal, 0), truth=truth, votes=votes,
                       stratum=stratum)


def _v(lid: LineId, tag: str, label: str, conf: float | None = None) -> PanelVote:
    return PanelVote(id=lid, tag=tag, label=label, conf=conf)


def test_replay_scores_accept_quality_and_load_separately():
    roster = PanelRoster(anchor="grok", support=("deepseek", "gemini"))
    unanimous = AnchorLedPolicy("u", AnchorLedGates(min_support=2, require_no_split=True))
    a, b, c = (LineId.mapped("ru", "57", i, 0) for i in (1, 2, 3))
    aligned = AlignedSet(
        lines=(
            # a: full support, correctly lineated → accept, correct.
            _line(1, "lineated", (_v(a, "grok", "lineated"), _v(a, "deepseek", "lineated"),
                                  _v(a, "gemini", "lineated"))),
            # b: full support but truth is prose → accept, a prose→lineated FALSE accept.
            _line(2, "prose", (_v(b, "grok", "lineated"), _v(b, "deepseek", "lineated"),
                               _v(b, "gemini", "lineated"))),
            # c: a support split → routed to human (a TERMINAL split, not escalatable).
            _line(3, "lineated", (_v(c, "grok", "lineated"), _v(c, "deepseek", "prose"),
                                  _v(c, "gemini", "lineated"))),
        ),
        n_prose=1, n_lineated=2)
    [m] = replay_and_score(aligned, (PolicySpec("u", roster, unanimous),))
    assert m.accept.n_accepted == 2
    assert m.accept.false_accept_prose_as_lineated == 1
    assert m.load.human_routed == 1 and m.load.escalatable_routed == 0
    assert m.coverage == pytest.approx(2 / 3)


def test_replay_keeps_decisions_inspectable():
    roster = PanelRoster(anchor="grok", support=("deepseek",))
    a = LineId.mapped("ru", "57", 1, 0)
    aligned = AlignedSet(
        lines=(_line(1, "lineated", (_v(a, "grok", "lineated"), _v(a, "deepseek", "lineated"))),),
        n_prose=0, n_lineated=1)
    [outcome] = replay(aligned, (PolicySpec("u", roster, AnchorLedPolicy("u")),))
    assert len(outcome.decisions) == 1
    d, truth, book, stratum = outcome.decisions[0]
    assert d.id == a and truth == "lineated" and book == "57" and stratum == "easy"


# --- the HEADLINE LOCK on the real committed aligned set ----------------------------------------

def test_real_policy_comparison_locks_the_finding():
    aligned = datasets.from_store()
    specs = load_policy_specs(RECIPE,
                              present_readers=frozenset(v.tag for ln in aligned.lines for v in ln.votes))
    results = {m.name: m for m in replay_and_score(aligned, specs)}
    legacy, unanimous, equal = results["legacy"], results["unanimous"], results["equal_majority"]

    # the policies are genuinely RANKABLE — they diverge on far more than 30 lines.
    assert unanimous.load.human_routed - legacy.load.human_routed > 30
    assert equal.accept.n_accepted - legacy.accept.n_accepted > 30

    # finding 1: legacy routes FAR LESS human work than unanimous, at no accuracy cost.
    assert legacy.load.human_routed * 3 < unanimous.load.human_routed
    assert legacy.accept.balanced_acc >= unanimous.accept.balanced_acc - 0.01

    # finding 2: legacy is at least as accurate as the equal-majority control, with fewer false accepts.
    assert legacy.accept.balanced_acc >= equal.accept.balanced_acc
    assert legacy.accept.false_accepts < equal.accept.false_accepts

    # finding 3: the costly mistake is ZERO — no prose accepted as lineated, all 63 prose captured.
    assert legacy.accept.false_accept_prose_as_lineated == 0
    assert legacy.accept.prose_recall == pytest.approx(1.0)

    # the brief's measured shape (robust bands around the probe's 482 / 33 / 367 / 148 / 514 counts).
    assert 470 <= legacy.accept.n_accepted <= 490
    assert legacy.accept.acc == pytest.approx(0.946, abs=0.02)
    assert equal.accept.n_accepted >= 510
    assert equal.accept.acc == pytest.approx(0.909, abs=0.02)

    # both strata are scored in the per-stratum breakdown.
    assert set(legacy.by_stratum) == {"contested", "easy"}
