# research-pure: the domain vocabulary of the lineation gold adjudication.
"""Shared types for the gold-rebuild core. Pure values — no I/O, no substrate import — so the
aggregation/blocks/audit logic unit-tests without pandoc or LibreOffice. `run.py` is the only
module that bridges these to `ir_view`/disk.

Grain: one BODY display-line, keyed `LineKey(idx, sub)` (a paragraph ordinal + its <w:br> segment).
The panel votes a two-class `Label` per votable line; the gate turns per-reader votes into a
`LineDecision` with a routing `Status`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, NamedTuple

type Label = Literal["prose", "lineated"]
type ReaderId = str
type Vote = tuple[Label, float | None]   # one rep's (label, confidence); conf None ⇒ unrecorded


class LineKey(NamedTuple):
    """A body display-line: paragraph ordinal `idx`, <w:br> segment `sub`. Same shape as
    `ir_view.LineKey`; defined here so the core needs no substrate import. NOT globally unique —
    `idx` is per-book; qualify with a rid/book before crossing region boundaries (see AuditLine)."""
    idx: int
    sub: int


def normalize_label(raw: str) -> Label:
    """Canonical label. Accepts the legacy pilot alias `flowing` (== prose) so old anchors can be
    adapted into the current `prose|lineated` schema."""
    s = raw.strip().lower()
    if s in ("prose", "flowing"):
        return "prose"
    if s == "lineated":
        return "lineated"
    raise ValueError(f"not a lineation label: {raw!r}")


class Status(StrEnum):
    """What the gate decided to DO with a line."""
    ACCEPT = "accept"            # high-confidence consensus → goes straight to gold
    ESCALATE = "escalate"        # resolvable noise → run more reps, re-gate
    ROUTE_HUMAN = "route_human"  # intrinsic ambiguity or persistent split → the human decides
    NEEDS_RERUN = "needs_rerun"  # operational failure (missing/parse-fail output) → re-run, not editorial


class Reason(StrEnum):
    """Why a line was NOT accepted. A line accepts iff it carries zero reasons."""
    GROK_PANEL_SPLIT = "grok!=panel"        # the lead reader disagrees with the panel majority
    LOW_CONF = "grok_conf<floor"            # the lead reader's confidence is below the gate floor
    CONF_MISSING = "grok_conf_missing"      # no confidence recorded — cannot clear the floor honestly
    READER_MISSING = "reader_missing"       # a core reader produced NO output (operational, not editorial)
    CORE_ABSTAIN = "core_abstain"           # a core reader voted but reached no confident verdict
    NO_PANEL_MAJORITY = "no_panel_majority"  # core voters tied — no majority label
    INSUFFICIENT_AGREEMENT = "insufficient_agreement"  # a majority exists but <min_core_agree share it
    NEEDS_REVIEW = "needs_review"           # substrate flagged the line (span-drop / unmapped)
    SOFT = "soft"                           # prior-dependent (book-consistency) — human is authority


# Intrinsic ambiguity — more reps cannot resolve it; route straight to the human.
TERMINAL_REASONS: frozenset[Reason] = frozenset({Reason.NEEDS_REVIEW, Reason.SOFT})
# Operational gaps — a missing/failed reader output is repaired by re-running, never by a human edit.
OPERATIONAL_REASONS: frozenset[Reason] = frozenset({Reason.READER_MISSING})


@dataclass(frozen=True, slots=True)
class Gates:
    """The acceptance thresholds (GOLD_REBUILD_PLAN §acceptance gates; `[calibrate]` defaults)."""
    core: tuple[ReaderId, ...] = ("grok", "gemini-pro", "ds-flash-text")
    lead: ReaderId = "grok"            # the best-calibrated reader; leads the decision
    conf_floor: float = 0.7            # 0 disables the confidence gate
    min_core_agree: int = 2            # ≥this many core readers must share the majority label
    escalate_reps: int = 3             # rep count past which a still-split line routes to the human

    def __post_init__(self) -> None:
        if self.lead not in self.core:
            raise ValueError(f"lead reader {self.lead!r} must be one of core {self.core}")
        if not 0.0 <= self.conf_floor <= 1.0:
            raise ValueError(f"conf_floor out of range: {self.conf_floor}")
        if not 1 <= self.min_core_agree <= len(self.core):
            raise ValueError(
                f"min_core_agree={self.min_core_agree} must be in 1..{len(self.core)} "
                f"(else no line can ever clear the agreement gate)")


@dataclass(frozen=True, slots=True)
class LineDecision:
    """The gate's verdict for one body line, with the evidence that produced it."""
    key: LineKey
    status: Status
    label: Label | None                       # the consensus label (None when no majority)
    reasons: tuple[Reason, ...]               # empty iff ACCEPT
    panel_majority: Label | None
    lead_label: Label | None
    lead_conf: float | None
    verdicts: dict[ReaderId, Label | None] = field(default_factory=dict)
    rep_count: int = 1                        # max reps run among core readers (escalation rounds)

    @property
    def accepted(self) -> bool:
        return self.status is Status.ACCEPT


@dataclass(frozen=True, slots=True)
class AuditLine:
    """An accepted gold line, region-qualified so it never collides across books."""
    rid: str
    key: LineKey
    label: Label
    stratum: str

    @property
    def ident(self) -> tuple[str, int, int]:
        return self.rid, self.key.idx, self.key.sub


@dataclass(frozen=True, slots=True)
class Block:
    """A maximal run of consecutive same-label body lines — the unit the site renders."""
    label: Label
    keys: tuple[LineKey, ...]

    @property
    def span(self) -> tuple[LineKey, LineKey]:
        return self.keys[0], self.keys[-1]
