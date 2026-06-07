# research-pure: recipes — turn a SELECTION of votable lines into task items, honoring authorial units.
"""The selection→tiling layer (the toml/CLI orchestration lands on top of this later). `tile_regions`
groups a book's selected lines into regions of WHOLE runs (~`target` votable lines, never splitting a
run / display-line group / stanza to hit the cap) plus a small context radius, in document order — so
what a reader is shown is deterministic and OWNED here, not reconstructed by sorting."""
from __future__ import annotations

from collections.abc import Sequence

from ..identity import BookId, LineId
from ..records import LineRecord
from ..sequence import Run, runs
from .tasks import ItemSpec


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
