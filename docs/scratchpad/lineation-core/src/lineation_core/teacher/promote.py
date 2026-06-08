# research-pure: resolved votes/labels → the committed truth, validated + merged (idempotent).
"""The promote step: take RESOLVED `PanelVote`s / `LineLabel`s (LineId-keyed, from the choke point)
and write them into the committed truth the eval half loads — MERGING with what's already there
(one row per (LineId, reader) for votes, per LineId for labels; the new value wins, so re-promoting
a task is idempotent) and VALIDATING before the write (a label must be on a mapped line; the
two-class constraint is enforced by `LineLabel` itself). All IO is at the `store` edge."""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .. import store
from ..annotations import LineLabel, PanelVote, VoteKey
from ..identity import LineId


def _existing(read_rows, *, annotations: Path | None) -> list:
    try:
        return read_rows(annotations=annotations)
    except FileNotFoundError:
        return []          # first promote into a fresh store — start from empty


def promote_votes(votes: Sequence[PanelVote], *, annotations: Path | None = None) -> int:
    """Merge resolved panel votes into `votes.jsonl` (one row per (LineId, reader); new wins).
    The INPUT must already be one vote per (reader, line) — raw multi-rep is REJECTED so reps can't
    silently collapse (aggregate them first). Idempotent across re-promotes."""
    seen: set[VoteKey] = set()
    for v in votes:
        if (v.tag, v.id) in seen:
            raise ValueError(
                f"duplicate (reader, LineId) in promote input — {v.tag} / {v.id}; aggregate reps "
                f"to one vote per reader before promoting")
        seen.add((v.tag, v.id))
    merged: dict[VoteKey, PanelVote] = {}
    for d in _existing(store.load_vote_rows, annotations=annotations):
        v = PanelVote.from_dict(d)
        merged[(v.tag, v.id)] = v
    for v in votes:
        merged[(v.tag, v.id)] = v
    rows = [v.to_dict() for v in sorted(merged.values(), key=lambda v: (v.id, v.tag))]
    store.write_vote_rows(rows, annotations=annotations)
    return len(votes)


def promote_labels(labels: Sequence[LineLabel], *, annotations: Path | None = None) -> int:
    """Merge resolved human labels into `labels.jsonl` (one row per LineId; new wins). Rejects an
    unmapped line (a span-dropped line is not a trainable target). Returns the count promoted."""
    merged: dict[LineId, LineLabel] = {}
    for d in _existing(store.load_label_rows, annotations=annotations):
        g = LineLabel.from_dict(d)
        merged[g.id] = g
    for g in labels:
        if not g.id.is_mapped:
            raise ValueError(f"refusing to promote an unmapped-line label: {g.id}")
        merged[g.id] = g
    rows = [g.to_dict() for g in sorted(merged.values(), key=lambda g: g.id)]
    store.write_label_rows(rows, annotations=annotations)
    return len(labels)
