# research-pure: the ALIGNED evaluation dataset — truth ⋈ panel votes on shared lines, for replay.
"""The shared substrate a policy replay scores against: the lines that have BOTH a committed human
LABEL (truth) and at least one panel VOTE, joined on `LineId`. Pure data — the only IO is `store`
(via `annotations.load_labels` / `load_votes`), lifted at `from_store`.

`AlignedSet` surfaces the class imbalance UP FRONT (`n_prose` / `n_lineated`): the corpus is ~452
lineated to ~63 prose on these lines, so a bare accuracy hides whether a policy actually CAPTURES the
rare prose. Every `AlignedLine` carries its `stratum` ("contested" iff the line is in the committed
`eval_sets/contested` slice the human re-adjudicated, else "easy") so a replay can break its accept
metrics down by difficulty without re-reading disk.

This module does NOT decide anything — it only assembles the (truth, votes) the policies in
`teacher/decision.py` consume. The replay loop is `policy_replay.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..annotations import PanelVote, load_labels, load_votes
from ..identity import Label, LineId
from .. import store


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


def from_store(*, annotations: Path | None = None) -> AlignedSet:
    """Join the committed truth (`labels.jsonl`) with the panel votes (`votes.jsonl`) on `LineId`,
    keeping the lines that have BOTH — the population a policy can be scored on (truth to grade
    against, votes to decide from). A line's `stratum` is "contested" iff it is in the committed
    `eval_sets/contested` slice, else "easy". Span-dropped (unmapped) labels are already rejected by
    `load_labels`, so every aligned line has a real source ordinal."""
    truth: dict[LineId, Label] = {g.id: g.label for g in load_labels(annotations=annotations).labels}

    votes_by_line: dict[LineId, list[PanelVote]] = {}
    for v in load_votes(annotations=annotations):
        votes_by_line.setdefault(v.id, []).append(v)

    contested = {LineId.from_key(d["id"])
                 for d in store.load_eval_set("contested", annotations=annotations)}

    lines: list[AlignedLine] = []
    for lid in sorted(truth.keys() & votes_by_line.keys()):
        votes = tuple(sorted(votes_by_line[lid], key=lambda v: v.tag))   # stable reader order
        lines.append(AlignedLine(
            id=lid, truth=truth[lid], votes=votes,
            stratum=Stratum.CONTESTED if lid in contested else Stratum.EASY))

    n_prose = sum(ln.truth == "prose" for ln in lines)
    return AlignedSet(lines=tuple(lines), n_prose=n_prose, n_lineated=len(lines) - n_prose)
