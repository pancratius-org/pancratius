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
`LineId`-keyed — no key remap, no source-shard reader). The active type can address only canonical
source lines; pre-canonical addresses exist only as `LegacyLineId` values in migration history.
The truth is committed `LineId`-keyed; this package only loads it, never re-derives it.

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

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Self, cast

from . import store
from .identity import (
    JsonObject,
    Label,
    LineId,
    LineTextHash,
    PanelVotes,
    ReaderTag,
    TaskId,
    strict_bool,
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
    line_text_hash: LineTextHash
    holdout: bool = False    # True = eval-only truth, never a training target

    def __post_init__(self) -> None:
        if self.label not in ("prose", "lineated"):
            raise ValueError(f"label must be prose|lineated, got {self.label!r}")
        if not self.line_text_hash:
            raise ValueError("active labels require a line_text_hash")
        if self.confidence is not None and (
            not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0
        ):
            raise ValueError("label confidence must be finite and between 0 and 1")

    def to_dict(self) -> JsonObject:
        return {
            "id": self.id.as_key(), "label": self.label, "source": self.source.value,
            "confidence": self.confidence, "audit_status": self.audit_status,
            "notes": self.notes, "provenance": dict(self.provenance),
            "line_text_hash": self.line_text_hash, "holdout": self.holdout,
        }

    @classmethod
    def from_dict(cls, d: JsonObject) -> Self:
        line_text_hash = d.get("line_text_hash")
        if not isinstance(line_text_hash, str) or not line_text_hash:
            raise ValueError("active label row is missing line_text_hash")
        confidence = d.get("confidence")
        provenance = d.get("provenance", {})
        if not isinstance(provenance, Mapping):
            raise ValueError("label provenance must be an object")
        return cls(
            id=LineId.from_key(cast(Iterable[object], d["id"])),
            label=to_label(str(d["label"])),
            source=LabelSource(str(d["source"])),
            confidence=float(str(confidence)) if confidence is not None else None,
            audit_status=str(d.get("audit_status", "")),
            notes=str(d.get("notes", "")),
            provenance={str(key): value for key, value in provenance.items()},
            line_text_hash=LineTextHash(line_text_hash),
            holdout=strict_bool(d.get("holdout", False), field="label.holdout"),
        )


@dataclass(frozen=True)
class LabelSet:
    """Canonical per-line truth. All labels are scoring truth; only the non-`holdout` subset
    (`trainable`) may become training targets."""

    labels: tuple[LineLabel, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "labels", tuple(self.labels))
        ids = [label.id for label in self.labels]
        if len(ids) != len(set(ids)):
            raise ValueError("label set contains duplicate LineIds")

    @property
    def trainable(self) -> tuple[LineLabel, ...]:
        """The training-eligible labels — every label not held out for eval-only use."""
        return tuple(g for g in self.labels if not g.holdout)


def load_labels(*, annotations: Path | None = None) -> LabelSet:
    """Read committed canonical truth; never rebuild or filter it at load time."""
    labels = [LineLabel.from_dict(d) for d in store.load_label_rows(annotations=annotations)]
    labels.sort(key=lambda label: label.id)
    return LabelSet(labels=tuple(labels))


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

    def __post_init__(self) -> None:
        if self.conf is not None and (
            not math.isfinite(self.conf) or not 0.0 <= self.conf <= 1.0
        ):
            raise ValueError("vote confidence must be finite and between 0 and 1")

    def to_dict(self) -> JsonObject:
        return {"id": self.id.as_key(), "tag": self.tag, "label": self.label, "conf": self.conf,
                "task": self.task}

    @classmethod
    def from_dict(cls, d: JsonObject) -> Self:
        confidence = d.get("conf")
        task = d.get("task")
        return cls(
            id=LineId.from_key(cast(Iterable[object], d["id"])),
            tag=str(d["tag"]),
            label=to_label(str(d["label"])),
            conf=float(str(confidence)) if confidence is not None else None,
            task=str(task) if task is not None else None,
        )


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
        calls = out.setdefault(v.tag, {})
        if v.id in calls:
            raise ValueError(f"duplicate vote for reader {v.tag!r} and line {v.id}")
        calls[v.id] = v.label
    return out
