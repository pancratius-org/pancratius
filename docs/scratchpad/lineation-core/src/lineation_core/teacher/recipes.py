# research-pure: recipes — turn a SELECTION of votable lines into task items, honoring authorial units.
"""The selection→tiling layer (the toml/CLI orchestration lands on top of this later). `tile_regions`
groups a book's selected lines into regions of WHOLE runs (~`target` votable lines, never splitting a
run / display-line group / stanza to hit the cap) plus a small context radius, in document order — so
what a reader is shown is deterministic and OWNED here, not reconstructed by sorting."""
from __future__ import annotations

import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .. import store
from ..identity import BookId, LineId, ModelId, ReaderTag
from ..records import LineRecord
from ..sequence import Run, runs
from . import tasks
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
                 target: int = 10, context_radius: int = 1,
                 modality: Modality = Modality.TEXT) -> list[ItemSpec]:
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
                                region=region, votable=votable, modality=modality))
        cur, cur_votes = [], 0

    for run in active:
        cur.append(run)
        cur_votes += sum(1 for i in run if records[i].id in sel)
        if cur_votes >= target:
            flush()
    flush()
    return regions


# --- the orchestration shell: selector → records → tiled task bundle --------------------------

type Selection = dict[BookId, set[LineId]]               # the votable lines to poll, per book
type RenderFn = Callable[[Sequence[ItemSpec], dict[BookId, list[LineRecord]]],
                         dict[str, tuple]]               # specs+records → {region_id: assets} (render.py)


def select_lines(recipe: Recipe, *, annotations=None) -> Selection:
    """Resolve the recipe's selector to the votable LineIds to poll, per book. Selection is DATA:
    `all` = every votable line of the books; `eval_set:<name>` reads a committed eval slice;
    `selection_file:<name>` reads a committed LineId list (e.g. the active-learning acquire set,
    written by the student/eval side — never imported here)."""
    kind, _, arg = recipe.selector.partition(":")
    if kind == "all":
        return {b: {r.id for r in store.load_records(b, recipe.lang) if r.votable}
                for b in recipe.books}
    if kind == "eval_set":
        ids = [LineId.from_key(d["id"]) for d in store.load_eval_set(arg, annotations=annotations)]
    elif kind == "selection_file":
        ids = [LineId.from_key(k) for k in store.load_selection(arg, annotations=annotations)]
    else:
        raise ValueError(f"unknown selector: {recipe.selector!r}")
    books = set(recipe.books)
    out: Selection = {b: set() for b in recipe.books}
    for lid in ids:
        if lid.book_id in books:
            out[lid.book_id].add(lid)
    return out


def build(recipe: Recipe, *, annotations=None, teacher_store=None,
          render: RenderFn | None = None) -> tasks.Task:
    """Resolve selection → tile into regions → build + persist the task bundle (manifest committed,
    payload derived). Records come from the record cache; vision composites come from the injected
    `render` builder (`render.py`), none → a text task. The recipe's deterministic, reproducible
    task build — the provenance layer the derived payload can be rebuilt from."""
    selected = select_lines(recipe, annotations=annotations)
    records = {b: store.load_records(b, recipe.lang) for b in recipe.books}
    modality = Modality.VISION if recipe.vision else Modality.TEXT
    specs: list[ItemSpec] = []
    for b in recipe.books:
        specs.extend(tile_regions(b, records[b], selected[b], target=recipe.target,
                                   context_radius=recipe.context_radius, modality=modality))
    assets = render(specs, records) if (recipe.vision and render is not None) else {}
    task = tasks.build_task(title=recipe.title, instructions=recipe.instructions,
                            specs=specs, records=records, assets=assets)
    store.save_task_bundle(recipe.task_id, task.to_payload(), task.manifest.to_dict(),
                           annotations=annotations, store=teacher_store)
    return task
