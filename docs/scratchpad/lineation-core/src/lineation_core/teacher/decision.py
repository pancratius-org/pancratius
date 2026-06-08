# research-pure: the cross-reader decision POLICIES — pluggable rules turning per-reader votes into
# accept / route-to-human, plus the shared decision vocabulary.
"""Turns per-reader panel votes (`votes.jsonl`, ONE vote per reader after rep aggregation) into a
DECISION per line. There is NO single right rule — the policy is PLUGGABLE so the eval harness can
replay several on the same votes and pick the empirical winner (see `evaluation/policy_replay`):

  `AnchorLedPolicy`     the configured ANCHOR leads; the rest of the core panel is a disagreement
                      detector. Two configs of one class:
                        - legacy gate: accept the panel MAJORITY iff the anchor is in it AND
                          ≥`min_core_agree` core agree AND anchor-conf ≥ `conf_floor` (tolerates a dissent);
                        - unanimous (`require_no_split`): accept only when every present core support
                          agrees with the anchor (any split → human).
  `EqualMajorityPolicy`  the control: strict majority of all deciding readers, anchor unprivileged —
                      it should UNDER-lineate the hard prose-shaped lineated lines (why anchor-led exists).

WHICH reader fills the anchor/support roles is RECIPE CONFIG (a TOML `PanelRoster`), never baked into
Python; a reader that voted but is not in the roster is recorded in `votes.jsonl` and ignored — that
is what 'diagnostic' (e.g. glm) means, an upstream run-for-observation choice. This layer does NOT
promote truth — it emits an accepted-candidate queue and a human-adjudication queue; promotion stays
a separate, explicit step. Policies are pure (data-in, data-out) and stateless; the adaptive-reps
escalation loop is a LIVE-run concern that wraps a policy, never part of it."""
from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ..identity import Label, LineId, ReaderTag
from ..panel_votes import PanelVote


class Outcome(StrEnum):
    ACCEPT = "accept"           # a candidate label
    HUMAN = "human"             # routed to adjudication — never auto-promoted


class Reason(StrEnum):
    FULL_SUPPORT = "full_support"                  # accept: anchor + every present core agree
    ACCEPTED_MAJORITY = "accepted_majority"        # accept: a majority (with a tolerated dissent)
    ANCHOR_ABSTAIN = "anchor_abstain"              # human: the anchor had no stable vote
    SUPPORT_DISAGREES = "support_disagrees"        # human: a core support reader split (unanimous mode)
    ANCHOR_PANEL_SPLIT = "anchor_panel_split"          # human: the anchor is not in the panel majority
    INSUFFICIENT_AGREEMENT = "insufficient_agreement"  # human: a majority exists but < min_core_agree
    NO_PANEL_MAJORITY = "no_panel_majority"        # human: deciding readers tied
    INSUFFICIENT_COVERAGE = "insufficient_coverage"  # human: too few core readers voted
    LOW_CONFIDENCE = "low_confidence"              # human: anchor confidence below the floor


# A reason is TERMINAL (intrinsic ambiguity → a human, more reps cannot help) or OPERATIONAL (a
# coverage gap → a live run can ESCALATE more reps; the offline harness just counts it as load).
TERMINAL_REASONS = frozenset({Reason.SUPPORT_DISAGREES, Reason.ANCHOR_PANEL_SPLIT,
                              Reason.INSUFFICIENT_AGREEMENT, Reason.NO_PANEL_MAJORITY,
                              Reason.LOW_CONFIDENCE})
OPERATIONAL_REASONS = frozenset({Reason.ANCHOR_ABSTAIN, Reason.INSUFFICIENT_COVERAGE})


@dataclass(frozen=True, slots=True)
class PanelRoster:
    """The DECIDING readers, by role (the specific readers are recipe config, never named here): the
    ANCHOR leads; SUPPORT readers are the disagreement detector. A reader that voted but is NOT in
    the roster is recorded in `votes.jsonl` and simply ignored by the decision — that is what makes a
    reader 'diagnostic' (an upstream recipe choice to run it for observation, not a decision role),
    so it needs no field here."""
    anchor: ReaderTag
    support: tuple[ReaderTag, ...]


@dataclass(frozen=True, slots=True)
class LineDecision:
    id: LineId
    outcome: Outcome
    reason: Reason
    label: Label | None                  # the accepted label (None when routed to human)
    anchor_label: Label | None           # what the anchor said (None if it abstained)
    support_labels: tuple[Label, ...]    # present core support readers' labels (anchor excluded)


@dataclass(frozen=True, slots=True)
class Routing:
    accepted: tuple[LineDecision, ...]   # candidate labels
    human: tuple[LineDecision, ...]      # the adjudication queue (splits / abstains / thin coverage)


class DecisionPolicy(Protocol):
    """A cross-reader rule: one line's per-reader votes → a routed `LineDecision`. Pure + stateless;
    concrete policies are frozen dataclasses holding their own thresholds and satisfy this
    structurally (no base class, mirroring `Posterior`/`ChatCompleter`)."""
    name: str
    def decide(self, lid: LineId, votes: Mapping[ReaderTag, PanelVote],
               roster: PanelRoster) -> LineDecision: ...


def _majority(labels: Sequence[Label]) -> Label | None:
    """The strict-majority label, or None on a tie / empty."""
    if not labels:
        return None
    top, n = Counter(labels).most_common(1)[0]
    return top if n * 2 > len(labels) else None


@dataclass(frozen=True, slots=True)
class AnchorLedGates:
    """Parameterized anchor-led acceptance. `require_no_split=True` is the unanimous rule (any present
    core support split → human); else the legacy gate (accept the majority iff the anchor is in it
    and ≥`min_core_agree` core agree). `min_support` = min present core support readers; `conf_floor`
    (if set) is the anchor-confidence floor (skipped when the anchor reported no conf)."""
    min_support: int = 1
    min_core_agree: int = 0
    conf_floor: float | None = None
    require_no_split: bool = True


@dataclass(frozen=True, slots=True)
class AnchorLedPolicy:
    name: str
    gates: AnchorLedGates = AnchorLedGates()

    def decide(self, lid: LineId, votes: Mapping[ReaderTag, PanelVote],
               roster: PanelRoster) -> LineDecision:
        g = self.gates
        anchor = votes.get(roster.anchor)
        support = [votes[t] for t in roster.support if t in votes]
        support_labels = tuple(v.label for v in support)

        def human(reason: Reason) -> LineDecision:
            return LineDecision(id=lid, outcome=Outcome.HUMAN, reason=reason, label=None,
                                anchor_label=anchor.label if anchor else None,
                                support_labels=support_labels)

        if anchor is None:
            return human(Reason.ANCHOR_ABSTAIN)
        if len(support) < g.min_support:
            return human(Reason.INSUFFICIENT_COVERAGE)
        if g.conf_floor is not None and anchor.conf is not None and anchor.conf < g.conf_floor:
            return human(Reason.LOW_CONFIDENCE)

        if g.require_no_split:
            if any(v.label != anchor.label for v in support):
                return human(Reason.SUPPORT_DISAGREES)
            return LineDecision(id=lid, outcome=Outcome.ACCEPT, reason=Reason.FULL_SUPPORT,
                                label=anchor.label, anchor_label=anchor.label,
                                support_labels=support_labels)

        core_labels = [anchor.label, *support_labels]      # present core (anchor always present here)
        maj = _majority(core_labels)
        if maj is None:
            return human(Reason.NO_PANEL_MAJORITY)
        if anchor.label != maj:
            return human(Reason.ANCHOR_PANEL_SPLIT)
        n_agree = sum(label == maj for label in core_labels)
        if n_agree < g.min_core_agree:
            return human(Reason.INSUFFICIENT_AGREEMENT)
        reason = Reason.FULL_SUPPORT if n_agree == len(core_labels) else Reason.ACCEPTED_MAJORITY
        return LineDecision(id=lid, outcome=Outcome.ACCEPT, reason=reason, label=maj,
                            anchor_label=anchor.label, support_labels=support_labels)


@dataclass(frozen=True, slots=True)
class EqualMajorityPolicy:
    """The control: strict majority of all deciding (anchor + support) readers, anchor unprivileged.
    Demonstrates WHY anchor-led exists — it under-lineates hard prose-shaped lineated lines."""
    name: str
    min_voters: int = 1

    def decide(self, lid: LineId, votes: Mapping[ReaderTag, PanelVote],
               roster: PanelRoster) -> LineDecision:
        anchor = votes.get(roster.anchor)
        support_labels = tuple(votes[t].label for t in roster.support if t in votes)
        deciding = [votes[t].label for t in (roster.anchor, *roster.support) if t in votes]
        base = dict(id=lid, anchor_label=anchor.label if anchor else None,
                    support_labels=support_labels)
        if len(deciding) < self.min_voters:
            return LineDecision(**base, outcome=Outcome.HUMAN,
                                reason=Reason.INSUFFICIENT_COVERAGE, label=None)
        maj = _majority(deciding)
        if maj is None:
            return LineDecision(**base, outcome=Outcome.HUMAN,
                                reason=Reason.NO_PANEL_MAJORITY, label=None)
        return LineDecision(**base, outcome=Outcome.ACCEPT, reason=Reason.ACCEPTED_MAJORITY, label=maj)


def route_with(policy: DecisionPolicy, votes: Sequence[PanelVote], roster: PanelRoster) -> Routing:
    """Group per-reader votes by line and apply `policy.decide` to each. Diagnostic readers are
    dropped (they live in `votes.jsonl`, not the decision). Accepted + human queues in document
    order — NOTHING is promoted. The ONE grouping+partition loop every policy + path reuses."""
    deciding = {roster.anchor, *roster.support}
    by_line: dict[LineId, dict[ReaderTag, PanelVote]] = defaultdict(dict)
    for v in votes:
        if v.tag in deciding:
            by_line[v.id][v.tag] = v
    decisions = [policy.decide(lid, by_line[lid], roster) for lid in sorted(by_line)]
    return Routing(accepted=tuple(d for d in decisions if d.outcome is Outcome.ACCEPT),
                   human=tuple(d for d in decisions if d.outcome is Outcome.HUMAN))


# Back-compat shims: the live `promote`/recipe path and tests call these; they are the default
# (unanimous) anchor-led policy expressed as free functions. The pluggable policies above supersede them.
def _default(min_support: int, min_conf: float | None) -> AnchorLedPolicy:
    return AnchorLedPolicy(name="anchor_led",
                         gates=AnchorLedGates(min_support=min_support, conf_floor=min_conf,
                                            require_no_split=True))


def decide_line(lid: LineId, by_tag: Mapping[ReaderTag, PanelVote], roster: PanelRoster, *,
                min_support: int = 1, min_conf: float | None = None) -> LineDecision:
    return _default(min_support, min_conf).decide(lid, by_tag, roster)


def route(votes: Sequence[PanelVote], roster: PanelRoster, *, min_support: int = 1,
          min_conf: float | None = None) -> Routing:
    return route_with(_default(min_support, min_conf), votes, roster)
