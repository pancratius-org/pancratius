# research-pure: the per-line annotation model — truth labels and panel votes, loaded from the artifact.
"""One typed annotation model: `LineLabel` (truth) and `PanelVote` (evidence), co-located but distinct.

Per-line truth — a `prose`/`lineated` LABEL for a line, with provenance and lineage:

`LineLabel` is a label attached to a `LineId`, plus where it came from (`source` =
human|gate|panel|override|transfer), how sure (`confidence`), whether it is eval-only
(`holdout`), and an opaque `provenance` record (the pre-canonical key etc.) so a correction
stays reasoned about. Training projects the non-holdout labels to `{LineId: label}`, but the
stored truth keeps its lineage. This file is THE truth store: every eval reads its labels from
here — a committed eval set is membership only, never a second copy of the labels.

`load_labels()` reads the committed `labels.jsonl` truth through the `store` edge (already
`LineId`-keyed — no key remap, no source-shard reader). It REJECTS any label whose line is unmapped (a §14-P1
span-drop has no real source ordinal, so it is not a trainable target) at the boundary, surfacing
the rejected count — never silently. The truth is committed `LineId`-keyed; this package only
loads it, never re-derives it.

Per-line evidence — the LLM panel votes on `prose`/`lineated` (the readers present are whatever
the campaign recipe ran; this model does not hard-code a panel):

Each vote is one reader's call on one line: a `LineId`, the reader `tag`, the `label`, and an
optional `conf`. The committed votes are already `LineId`-keyed, so loading and joining here is by
`LineId` — the one identity.

`load_votes()` reads the committed `votes.jsonl` through the `store` edge and `by_reader()` groups
votes by reader, so `compare`/`contested` score each reader against the truth on the lines they
share. It FAILS LOUD on a missing store; it never rebuilds.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Self

from . import store
from .identity import (
    JsonObject,
    Label,
    LineId,
    LineTextHash,
    PanelVotes,
    ReaderTag,
    TaskId,
    to_label,
)

# Opaque lineage carried on a label/vote on disk (the pre-canonical key, the gate's policy+reason+
# votes, the task title…). No consumer joins on it — the join key is the `LineId` — so it stays an
# open object map by design; named so it reads as "lineage, do not branch on its shape" rather than
# an anonymous `Mapping[str, Any]`.
type Provenance = Mapping[str, object]


class LabelSource(StrEnum):
    HUMAN = "human"          # a human page adjudication — the strongest truth tier
    GATE = "gate"            # auto-accepted by the decision policy over panel votes
    PANEL = "panel"          # a raw panel consensus (no gate, no human)
    OVERRIDE = "override"    # a reasoned correction of an earlier label
    TRANSFER = "transfer"    # derived truth carried across an alignment (e.g. RU→EN ordinal
                             # transfer) — never independent evidence for the source label


@dataclass(frozen=True, slots=True)
class LineLabel:
    """One per-line truth record. `label` projects to training; the rest is provenance and
    lineage so a correction (e.g. the g05 IR-bug note) stays reasoned about. `provenance` is
    opaque lineage carried on disk — no consumer joins on it (the join key is `id`).

    `holdout=True` marks EVAL-ONLY truth: it scores readers/policies/the student but is never a
    training target (`build_dataset` skips it). Set at promote time — e.g. labels minted for a
    frozen acceptance slice, or adjudications whose criterion was an eval design, not training."""

    id: LineId
    label: Label  # prose | lineated
    source: LabelSource
    confidence: float | None
    audit_status: str
    notes: str
    provenance: Provenance
    line_text_hash: LineTextHash | None = None  # hash of the line text this label applies to, if known
    holdout: bool = False    # True = eval-only truth, never a training target

    def __post_init__(self) -> None:
        if self.label not in ("prose", "lineated"):
            raise ValueError(f"label must be prose|lineated, got {self.label!r}")

    def to_dict(self) -> JsonObject:
        return {
            "id": self.id.as_key(), "label": self.label, "source": self.source.value,
            "confidence": self.confidence, "audit_status": self.audit_status,
            "notes": self.notes, "provenance": dict(self.provenance),
            "line_text_hash": self.line_text_hash, "holdout": self.holdout,
        }

    @classmethod
    def from_dict(cls, d: JsonObject) -> Self:
        return cls(
            id=LineId.from_key(d["id"]), label=to_label(d["label"]),
            source=LabelSource(d["source"]), confidence=d.get("confidence"),
            audit_status=d.get("audit_status", ""), notes=d.get("notes", ""),
            provenance=dict(d.get("provenance", {})),
            line_text_hash=d.get("line_text_hash"),
            holdout=bool(d.get("holdout", False)),
        )


@dataclass(frozen=True)
class LabelSet:
    """The per-line truth, loaded from the canonical artifact. `labels` holds ONLY mapped lines —
    labels on unmapped (span-dropped) lines are REJECTED at the boundary and counted in
    `n_rejected_unmapped`, surfaced rather than silently kept or dropped. ALL labels are scoring
    truth; only the non-`holdout` subset (`trainable`) may become training targets."""

    labels: list[LineLabel]
    n_rejected_unmapped: int

    @property
    def trainable(self) -> list[LineLabel]:
        """The training-eligible labels — every label not held out for eval-only use."""
        return [g for g in self.labels if not g.holdout]


def load_labels(*, annotations: Path | None = None) -> LabelSet:
    """Read the committed `labels.jsonl` truth (the single store-level annotation file),
    reject unmapped-line labels (surfaced count), and return the `LabelSet`. FAILS LOUD
    if the file is missing — it never rebuilds; the truth is committed, not derived."""
    kept: list[LineLabel] = []
    n_rejected = 0
    for d in store.load_label_rows(annotations=annotations):
        g = LineLabel.from_dict(d)
        if not g.id.is_mapped:
            n_rejected += 1
            continue
        kept.append(g)
    kept.sort(key=lambda g: g.id)
    return LabelSet(labels=kept, n_rejected_unmapped=n_rejected)


type VoteKey = tuple[ReaderTag, LineId]   # the (reader, line) identity of a vote — its dedup key


@dataclass(frozen=True, slots=True)
class PanelVote:
    id: LineId
    tag: ReaderTag    # the reader (grok | deepseek | …)
    label: Label      # prose | lineated
    conf: float | None
    task: TaskId | None = None   # the campaign that produced this vote — `route` consumes only its own
                                 # task's votes, so a superseded older campaign's row reads as uncovered.
                                 # None on legacy/eval rows committed before task-stamping.

    def to_dict(self) -> JsonObject:
        return {"id": self.id.as_key(), "tag": self.tag, "label": self.label, "conf": self.conf,
                "task": self.task}

    @classmethod
    def from_dict(cls, d: JsonObject) -> Self:
        return cls(id=LineId.from_key(d["id"]), tag=d["tag"], label=to_label(d["label"]),
                   conf=d.get("conf"), task=d.get("task"))


def load_votes(*, annotations: Path | None = None) -> list[PanelVote]:
    """Every panel vote from the committed `votes.jsonl` truth. FAILS LOUD if the file is
    missing; never rebuilds."""
    return [PanelVote.from_dict(d) for d in store.load_vote_rows(annotations=annotations)]


def by_reader(*, annotations: Path | None = None) -> PanelVotes:
    """`{reader_tag: {LineId: label}}` — the panel's calls keyed by line identity, ready to
    join against the truth and the student on the SAME `LineId`s. The readers are DERIVED from
    the votes present — no panel roster is baked into this model."""
    out: PanelVotes = {}
    for v in load_votes(annotations=annotations):
        out.setdefault(v.tag, {})[v.id] = v.label
    return out
