# research-pure: per-line truth labels (with provenance) — loaded from the canonical artifact.
"""Per-line truth: a `prose`/`lineated` LABEL for a line, with provenance and lineage.

`LineLabel` is a label attached to a `LineId`, plus where it came from (`source` =
human|gate|panel|override), how sure (`confidence`), and an opaque `provenance` record (the
pre-canonical key etc.) so a correction stays reasoned about. Training projects each label to
`{LineId: label}`, but the stored truth keeps its lineage.

`load()` reads the committed `labels.jsonl` truth through the `store` edge (already `LineId`-keyed
— no key remap, no source-shard reader). It REJECTS any label whose line is unmapped (a §14-P1
span-drop has no real source ordinal, so it is not a trainable target) at the boundary, surfacing
the rejected count — never silently. The truth is committed `LineId`-keyed; this package only
loads it, never re-derives it.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

from . import store
from .identity import Label, LineId, to_label


class LabelSource(StrEnum):
    HUMAN = "human"
    GATE = "gate"
    PANEL = "panel"
    OVERRIDE = "override"


@dataclass(frozen=True, slots=True)
class LineLabel:
    """One per-line truth record. `label` projects to training; the rest is provenance and
    lineage so a correction (e.g. the g05 IR-bug note) stays reasoned about. `provenance` is
    opaque lineage carried on disk — no consumer joins on it (the join key is `id`)."""

    id: LineId
    label: Label  # prose | lineated
    source: LabelSource
    confidence: float | None
    audit_status: str
    notes: str
    provenance: Mapping[str, Any]
    line_text_hash: str | None = None  # hash of the line text this label applies to, if known

    def __post_init__(self) -> None:
        if self.label not in ("prose", "lineated"):
            raise ValueError(f"label must be prose|lineated, got {self.label!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id.as_key(), "label": self.label, "source": self.source.value,
            "confidence": self.confidence, "audit_status": self.audit_status,
            "notes": self.notes, "provenance": dict(self.provenance),
            "line_text_hash": self.line_text_hash,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Self:
        return cls(
            id=LineId.from_key(d["id"]), label=to_label(d["label"]),
            source=LabelSource(d["source"]), confidence=d.get("confidence"),
            audit_status=d.get("audit_status", ""), notes=d.get("notes", ""),
            provenance=dict(d.get("provenance", {})),
            line_text_hash=d.get("line_text_hash"),
        )


@dataclass(frozen=True)
class LabelSet:
    """The trainable per-line truth, loaded from the canonical artifact. `labels` holds ONLY
    mapped lines — labels on unmapped (span-dropped) lines are REJECTED at the boundary and
    counted in `n_rejected_unmapped`, surfaced rather than silently kept or dropped."""

    labels: list[LineLabel]
    n_rejected_unmapped: int


def load(*, annotations: Path | None = None) -> LabelSet:
    """Read the committed `labels.jsonl` truth (the single store-level annotation file),
    reject unmapped-line labels (surfaced count), and return the trainable `LabelSet`. FAILS LOUD
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
