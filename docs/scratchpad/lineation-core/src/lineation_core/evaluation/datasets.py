# research-pure: the evaluation datasets — slice truth and the aligned (truth ⋈ votes) set.
"""The shared substrate every eval family scores against. Pure data — the only IO is `store`
(via `annotations.load_labels` / `load_votes`), lifted at `from_store` / `eval_slice`.

Two dataset shapes, one truth:

  - `EvalSlice` — a committed membership (`eval_sets/<name>.json`, `LineId` keys only) joined to
    the ONE truth store (`labels.jsonl`). Every member line MUST have a label — a member with no
    truth is a config error, surfaced loud. `truth_fingerprint` pins the joined truth so a study
    manifest can prove the truth it scored against, byte-for-byte.
  - `AlignedSet` — the lines that have BOTH a committed LABEL (truth) and at least one panel VOTE,
    joined on `LineId` — the population a policy replay decides over.

`AlignedSet` surfaces the class imbalance UP FRONT (`n_prose` / `n_lineated`): the corpus is heavily
lineated on these lines, so a bare accuracy hides whether a policy actually CAPTURES the rare prose.
Every `AlignedLine` carries its `stratum` ("contested" iff the line is in the committed
`eval_sets/contested` membership the human re-adjudicated, else "easy") so a replay can break its
accept metrics down by difficulty without re-reading disk.

This module does NOT decide anything — it only assembles the (truth, votes) the policies in
`teacher/decision.py` consume. The replay loop is `policy_replay.py`.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..annotations import LabelSource, PanelVote, load_labels, load_votes
from ..identity import Label, LineId
from .. import store

# Truth a decision policy may be GRADED against: labels no panel policy produced. A `gate` label
# is a policy's own output — scoring a policy on it is self-grading.
INDEPENDENT_TRUTH = frozenset({LabelSource.HUMAN, LabelSource.OVERRIDE})


@dataclass(frozen=True, slots=True)
class EvalSlice:
    """A committed eval membership joined to the one truth store: the slice's lines (membership
    order) and `{LineId: label}` truth for every member, read from `labels.jsonl`."""

    name: str
    lines: tuple[LineId, ...]
    truth: Mapping[LineId, Label]


def eval_slice(name: str, *, annotations: Path | None = None) -> EvalSlice:
    """Join a committed eval membership (`eval_sets/<name>.json`) to the `labels.jsonl` truth.
    FAILS LOUD on a member line with no committed label — membership without truth is a config
    error (a stale slice or an unpromoted label), never a silently smaller denominator."""
    lines = tuple(LineId.from_key(k) for k in store.load_eval_set(name, annotations=annotations))
    labels: dict[LineId, Label] = {g.id: g.label
                                   for g in load_labels(annotations=annotations).labels}
    missing = [lid for lid in lines if lid not in labels]
    if missing:
        raise ValueError(
            f"eval slice {name!r} has {len(missing)} member line(s) with no label in labels.jsonl "
            f"(first: {missing[0]}) — promote their labels or fix the membership")
    return EvalSlice(name=name, lines=lines, truth={lid: labels[lid] for lid in lines})


def truth_fingerprint(slice_: EvalSlice) -> str:
    """The sha256 of the slice's truth AS SCORED — canonical `[[key, label], …]` in `LineId`
    order. A study manifest pins it beside the membership-file hash, so a replayed scorecard
    fails an audit loudly if any member's truth label drifted since the evidence was produced."""
    canon = [[lid.as_key(), slice_.truth[lid]] for lid in sorted(slice_.lines)]
    return hashlib.sha256(json.dumps(canon, ensure_ascii=False).encode()).hexdigest()


class Stratum(StrEnum):
    """A line's evaluation difficulty bucket. `CONTESTED` lines are in the committed
    `eval_sets/contested` slice the human re-adjudicated; everything else is `EASY`. Every aligned
    line has exactly one — the field is never absent."""
    CONTESTED = "contested"
    EASY = "easy"


@dataclass(frozen=True, slots=True)
class AlignedLine:
    """One line with truth AND evidence: the human `truth` label, every panel `votes` cast on it
    (one `PanelVote` per reader — already rep-aggregated on disk), and its difficulty `stratum`."""

    id: LineId
    truth: Label
    votes: tuple[PanelVote, ...]
    stratum: Stratum


@dataclass(frozen=True, slots=True)
class AlignedSet:
    """The aligned lines plus the class counts surfaced for the imbalance to stay in view. `lines`
    is in `LineId` (document) order, so a replay over it is deterministic."""

    lines: tuple[AlignedLine, ...]
    n_prose: int
    n_lineated: int

    @property
    def n_total(self) -> int:
        return len(self.lines)


def from_store(*, annotations: Path | None = None,
               truth_sources: frozenset[LabelSource] = INDEPENDENT_TRUTH) -> AlignedSet:
    """Join the committed truth (`labels.jsonl`) with the panel votes (`votes.jsonl`) on `LineId`,
    keeping the lines that have BOTH — the population a policy can be scored on (truth to grade
    against, votes to decide from). `truth_sources` defaults to PANEL-INDEPENDENT truth
    (human/override): a `gate` label was MINTED from these very votes by a policy, so grading
    policies against it is circular — widen the filter only for analyses that are not scoring a
    decision policy. A line's `stratum` is "contested" iff it is in the committed
    `eval_sets/contested` slice, else "easy". Span-dropped (unmapped) labels are already rejected by
    `load_labels`, so every aligned line has a real source ordinal."""
    truth: dict[LineId, Label] = {g.id: g.label for g in load_labels(annotations=annotations).labels
                                  if g.source in truth_sources}

    votes_by_line: dict[LineId, list[PanelVote]] = {}
    for v in load_votes(annotations=annotations):
        votes_by_line.setdefault(v.id, []).append(v)

    contested = {LineId.from_key(k)
                 for k in store.load_eval_set("contested", annotations=annotations)}

    lines: list[AlignedLine] = []
    for lid in sorted(truth.keys() & votes_by_line.keys()):
        votes = tuple(sorted(votes_by_line[lid], key=lambda v: v.tag))   # stable reader order
        lines.append(AlignedLine(
            id=lid, truth=truth[lid], votes=votes,
            stratum=Stratum.CONTESTED if lid in contested else Stratum.EASY))

    n_prose = sum(ln.truth == "prose" for ln in lines)
    return AlignedSet(lines=tuple(lines), n_prose=n_prose, n_lineated=len(lines) - n_prose)
