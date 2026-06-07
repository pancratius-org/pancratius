# research-pure: the LLM panel's per-line votes — loaded from the canonical artifact.
"""The LLM panel votes (grok, deepseek, gemini, owl, mimo, minimax) on `prose`/`lineated`.

Each vote is one reader's call on one line: a `LineId`, the reader `tag`, the `label`, and an
optional `conf`. The committed votes are already `LineId`-keyed, so loading and joining here is by
`LineId` — the one identity.

`load()` reads the committed `votes.jsonl` through the `store` edge and groups votes by reader, so
`compare`/`contested` score each reader against the truth on the lines they share. It FAILS LOUD
on a missing store; it never rebuilds.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Self

from . import store
from .identity import Label, LineId, PanelVotes, ReaderTag

READERS = ("grok", "deepseek", "gemini", "owl", "mimo", "minimax")


@dataclass(frozen=True, slots=True)
class PanelVote:
    id: LineId
    tag: ReaderTag    # the reader (grok | deepseek | …)
    label: Label      # prose | lineated
    conf: float | None

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id.as_key(), "tag": self.tag, "label": self.label, "conf": self.conf}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Self:
        return cls(id=LineId.from_key(d["id"]), tag=d["tag"], label=d["label"],
                   conf=d.get("conf"))


def load(*, annotations: Path | None = None) -> list[PanelVote]:
    """Every panel vote from the committed `votes.jsonl` truth. FAILS LOUD if the file is
    missing; never rebuilds."""
    return [PanelVote.from_dict(d) for d in store.load_vote_rows(annotations=annotations)]


def by_reader(*, annotations: Path | None = None) -> PanelVotes:
    """`{reader_tag: {LineId: label}}` — the panel's calls keyed by line identity, ready to
    join against the truth and the student on the SAME `LineId`s."""
    out: PanelVotes = {tag: {} for tag in READERS}
    for v in load(annotations=annotations):
        out.setdefault(v.tag, {})[v.id] = v.label
    return out
