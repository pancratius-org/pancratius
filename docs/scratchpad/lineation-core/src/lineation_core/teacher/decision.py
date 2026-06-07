# research-pure: the cross-reader decision layer — grok-led accept, splits route to a human.
"""Turns per-reader panel votes (`votes.jsonl`, ONE vote per reader after rep aggregation) into a
DECISION per line. Equal-majority is WRONG here: the prose-biased core readers can outvote the
anchor on hard unseen lineated lines, so the panel under-lineates. Instead the ANCHOR (grok, the
best reader) LEADS and the rest of the core panel is a disagreement detector:

  ACCEPT the anchor's label  ⇐  the anchor has a stable vote, enough core support voted, the
                                anchor clears the optional confidence floor, AND no core support
                                reader splits from it;
  ROUTE TO HUMAN             ⇐  the anchor abstained, support coverage is insufficient, the anchor
                                is under-confident, or any core support reader disagrees (a split).

DIAGNOSTIC readers (e.g. glm) are recorded in `votes.jsonl` but NEVER decide here. This layer does
NOT promote truth — it emits an accepted-candidate queue and a human-adjudication queue; promotion
stays a separate, explicit step. It is deliberately its own module: `aggregate_reps` is correctly
scoped to reps → one vote per reader, and this cross-reader policy belongs beside it, not inside it."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from ..identity import Label, LineId, ReaderTag
from ..panel_votes import PanelVote


class Outcome(StrEnum):
    ACCEPT = "accept"           # a grok-led candidate label
    HUMAN = "human"             # routed to adjudication — never auto-promoted


class Reason(StrEnum):
    FULL_SUPPORT = "full_support"                  # accepted: anchor + every present support agree
    ANCHOR_ABSTAIN = "anchor_abstain"             # human: the anchor had no stable vote
    SUPPORT_DISAGREES = "support_disagrees"       # human: a core support reader split from the anchor
    INSUFFICIENT_COVERAGE = "insufficient_coverage"  # human: too few core support readers voted
    LOW_CONFIDENCE = "low_confidence"             # human: anchor confidence below the floor


@dataclass(frozen=True, slots=True)
class PanelRoster:
    """Who decides. The ANCHOR (grok) leads; SUPPORT readers (gemini-pro, ds-flash-text) are the
    disagreement detector; DIAGNOSTIC readers (glm) are recorded in votes but NEVER decide."""
    anchor: ReaderTag
    support: tuple[ReaderTag, ...]
    diagnostic: tuple[ReaderTag, ...] = ()


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
    accepted: tuple[LineDecision, ...]   # grok-led candidate labels
    human: tuple[LineDecision, ...]      # the adjudication queue (splits / abstains / thin coverage)


def decide_line(lid: LineId, by_tag: Mapping[ReaderTag, PanelVote], roster: PanelRoster, *,
                min_support: int = 1, min_conf: float | None = None) -> LineDecision:
    """Apply the grok-led policy to ONE line's per-reader votes. `min_support` is the minimum number
    of core support readers that must have voted; `min_conf` (if set) is the anchor-confidence floor
    (skipped when the anchor reported no conf)."""
    anchor = by_tag.get(roster.anchor)
    support = [by_tag[t] for t in roster.support if t in by_tag]
    support_labels = tuple(v.label for v in support)

    def human(reason: Reason) -> LineDecision:
        return LineDecision(id=lid, outcome=Outcome.HUMAN, reason=reason, label=None,
                            anchor_label=anchor.label if anchor else None,
                            support_labels=support_labels)

    if anchor is None:
        return human(Reason.ANCHOR_ABSTAIN)
    if len(support) < min_support:
        return human(Reason.INSUFFICIENT_COVERAGE)
    if min_conf is not None and anchor.conf is not None and anchor.conf < min_conf:
        return human(Reason.LOW_CONFIDENCE)
    if any(v.label != anchor.label for v in support):       # any core split from the anchor → human
        return human(Reason.SUPPORT_DISAGREES)
    return LineDecision(id=lid, outcome=Outcome.ACCEPT, reason=Reason.FULL_SUPPORT,
                        label=anchor.label, anchor_label=anchor.label, support_labels=support_labels)


def route(votes: Sequence[PanelVote], roster: PanelRoster, *, min_support: int = 1,
          min_conf: float | None = None) -> Routing:
    """Group per-reader votes by line and apply the grok-led policy to each. Diagnostic readers are
    dropped from the decision (they live in `votes.jsonl`, not here). Returns the accepted candidate
    labels + the human queue, each in document order — NOTHING is promoted."""
    deciding = {roster.anchor, *roster.support}
    by_line: dict[LineId, dict[ReaderTag, PanelVote]] = defaultdict(dict)
    for v in votes:
        if v.tag in deciding:
            by_line[v.id][v.tag] = v
    decisions = [decide_line(lid, by_line[lid], roster, min_support=min_support, min_conf=min_conf)
                 for lid in sorted(by_line)]
    return Routing(accepted=tuple(d for d in decisions if d.outcome is Outcome.ACCEPT),
                   human=tuple(d for d in decisions if d.outcome is Outcome.HUMAN))
