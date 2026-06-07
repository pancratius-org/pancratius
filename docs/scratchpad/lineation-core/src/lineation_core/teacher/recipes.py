# research-pure: recipes — turn a SELECTION of votable lines into task items, honoring authorial units.
"""The selection→tiling layer (the toml/CLI orchestration lands on top of this later). `tile_regions`
groups a book's selected lines into regions of WHOLE runs (~`target` votable lines, never splitting a
run / display-line group / stanza to hit the cap) plus a small context radius, in document order — so
what a reader is shown is deterministic and OWNED here, not reconstructed by sorting."""
from __future__ import annotations

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass

from ..identity import BookId, LineId, ModelId, ReaderTag
from ..records import LineRecord
from ..sequence import Run, runs
from .tasks import ItemSpec, Modality


@dataclass(frozen=True, slots=True)
class ReaderSpec:
    """One panel reader in a recipe: its tag, the model behind it, and the modality it reads in."""
    tag: ReaderTag
    model: ModelId
    modality: Modality = Modality.TEXT


@dataclass(frozen=True, slots=True)
class Recipe:
    """A declarative annotation campaign (a committed toml). It binds everything needed to
    deterministically rebuild the derived task payload: the bundle id, the books + line selector,
    the reader-facing instructions, the panel readers + reps, and the tiling parameters. The task's
    vision-ness is derived from the readers — composites are rendered iff any reader reads vision."""
    task_id: str
    title: str
    instructions: str
    books: tuple[BookId, ...]
    selector: str                  # "all" | "eval_set:<name>" | "selection_file:<path>"
    readers: tuple[ReaderSpec, ...]
    lang: str = "ru"
    reps: int = 1
    target: int = 10
    context_radius: int = 1

    @property
    def vision(self) -> bool:
        return any(r.modality is Modality.VISION for r in self.readers)


def load_recipe(toml_text: str) -> Recipe:
    """Parse + validate a recipe toml. FAILS LOUD on a missing required field, an unknown reader
    modality, empty books, or duplicate reader tags."""
    d = tomllib.loads(toml_text)
    readers = tuple(ReaderSpec(tag=r["tag"], model=r["model"],
                               modality=Modality(r.get("modality", "text")))
                    for r in d.get("readers", []))
    tags = [r.tag for r in readers]
    if len(set(tags)) != len(tags):
        raise ValueError(f"duplicate reader tags in recipe: {tags}")
    sel = d["selection"]
    books = tuple(str(b) for b in sel["books"])
    if not books:
        raise ValueError("recipe selection.books is empty")
    return Recipe(
        task_id=d["task_id"], title=d.get("title", ""), instructions=d.get("instructions", ""),
        books=books, selector=sel.get("selector", "all"), readers=readers,
        lang=sel.get("lang", "ru"), reps=int(d.get("reps", 1)),
        target=int(sel.get("target", 10)), context_radius=int(sel.get("context_radius", 1)))


def tile_regions(book_id: BookId, records: Sequence[LineRecord], selected: set[LineId], *,
                 target: int = 10, context_radius: int = 1) -> list[ItemSpec]:
    """Group `selected` votable lines of one book into task regions. A region accumulates WHOLE runs
    (a run = a maximal BODY block — a stanza / paragraph-group / display-line group) up to ~`target`
    votable lines and NEVER splits one, so an authorial unit stays intact even past the target. Each
    region shows its runs' lines plus `context_radius` neighbours (un-keyed context); the selected
    lines are the votable ones, in document order. Region ids are deterministic."""
    sel = set(selected)
    active = [run for run in runs(records) if any(records[i].id in sel for i in run)]
    regions: list[ItemSpec] = []
    cur: list[Run] = []
    cur_votes = 0

    def flush() -> None:
        nonlocal cur, cur_votes
        if not cur:
            return
        lo = max(0, cur[0][0] - context_radius)
        hi = min(len(records) - 1, cur[-1][-1] + context_radius)
        region = tuple(records[i].id for i in range(lo, hi + 1))
        votable = frozenset(records[i].id for run in cur for i in run if records[i].id in sel)
        regions.append(ItemSpec(region_id=f"b{book_id}-r{len(regions)}",
                                region=region, votable=votable))
        cur, cur_votes = [], 0

    for run in active:
        cur.append(run)
        cur_votes += sum(1 for i in run if records[i].id in sel)
        if cur_votes >= target:
            flush()
    flush()
    return regions
