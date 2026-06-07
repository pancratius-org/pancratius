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
from . import panel as panel_mod
from . import promote, responses, tasks
from .tasks import AssetKind, ItemSpec, Modality


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
                 target: int = 10, context_radius: int = 1, max_gap: int = 8,
                 modality: Modality = Modality.TEXT) -> list[ItemSpec]:
    """Group `selected` votable lines of one book into task regions. A region accumulates WHOLE runs
    (a run = a maximal BODY block — a stanza / paragraph-group / display-line group) up to ~`target`
    votable lines and NEVER splits one, so an authorial unit stays intact even past the target. It
    ALSO breaks when the next active run is more than `max_gap` records away, so a sparse selection
    (distant uncertain lines) yields separate regions, not one giant span. Each region shows its
    runs' lines plus `context_radius` neighbours (un-keyed context); the selected lines are the
    votable ones, in document order. Region ids are deterministic."""
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
        if cur and run[0] - cur[-1][-1] - 1 > max_gap:     # next run too far — don't span the gap
            flush()
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
    stray = sorted({lid.book_id for lid in ids if lid.book_id not in books})
    if stray:
        raise ValueError(f"selection has lines from books {stray} not in recipe.books "
                         f"{sorted(books)} — likely a recipe scope bug")
    out: Selection = {b: set() for b in recipe.books}
    for lid in ids:
        out[lid.book_id].add(lid)
    return out


def build(recipe: Recipe, *, annotations=None, teacher_store=None,
          render: RenderFn | None = None) -> tasks.Task:
    """Resolve selection → tile into regions → build + persist the task bundle (manifest committed,
    payload derived). Records come from the record cache. A VISION recipe MUST be given a `render`
    builder and every region MUST get a COMPOSITE — a vision task with no images is REFUSED (a
    silent text fallback would corrupt the panel). The recipe's deterministic, reproducible task
    build — the provenance layer the derived payload can be rebuilt from."""
    if recipe.vision and render is None:
        raise ValueError(
            f"recipe {recipe.task_id!r} reads vision but no render builder was given — refusing to "
            f"build a vision task with no images")
    selected = select_lines(recipe, annotations=annotations)
    records = {b: store.load_records(b, recipe.lang) for b in recipe.books}
    modality = Modality.VISION if recipe.vision else Modality.TEXT
    specs: list[ItemSpec] = []
    for b in recipe.books:
        specs.extend(tile_regions(b, records[b], selected[b], target=recipe.target,
                                   context_radius=recipe.context_radius, modality=modality))
    assets = render(specs, records) if recipe.vision else {}
    if recipe.vision:
        bare = [s.region_id for s in specs
                if not any(a.kind is AssetKind.COMPOSITE for a in assets.get(s.region_id, ()))]
        if bare:
            raise ValueError(f"vision recipe: render produced no COMPOSITE for regions {bare}")
    task = tasks.build_task(title=recipe.title, instructions=recipe.instructions,
                            specs=specs, records=records, assets=assets)
    store.save_task_bundle(recipe.task_id, task.to_payload(), task.manifest.to_dict(),
                           annotations=annotations, store=teacher_store)
    return task


def panel(recipe: Recipe, completer: panel_mod.ChatCompleter, *,
          annotations=None, teacher_store=None) -> int:
    """Run the panel for a built task: load the bundle → readers × reps → resolve each rep →
    aggregate to ONE vote per (reader, line) → save the per-rep evidence and promote the aggregated
    votes. Returns the count of canonical votes promoted. Network is the injected `completer`."""
    payload, manifest_d = store.load_task_bundle(recipe.task_id, annotations=annotations,
                                                 store=teacher_store)
    task = tasks.Task.from_bundle(payload, manifest_d)
    records = {b: store.load_records(b, recipe.lang) for b in recipe.books}
    cfg = panel_mod.PanelConfig(
        readers=tuple(panel_mod.ReaderConfig(r.tag, r.model, r.modality) for r in recipe.readers),
        reps=recipe.reps)
    reps = panel_mod.run_panel(task, cfg, completer)
    store.save_panel_reps(recipe.task_id, _rep_rows(reps), annotations=annotations)
    by_rep: dict[int, list[responses.RawReaderResponse]] = {}
    for rep in reps:
        by_rep.setdefault(rep.rep, []).append(rep.response)
    per_rep = [responses.resolve_panel(task.manifest, rs, records).votes
               for _, rs in sorted(by_rep.items())]
    return promote.promote_votes(panel_mod.aggregate_reps(per_rep), annotations=annotations)


def ingest(recipe: Recipe, *, annotations=None, teacher_store=None) -> int:
    """Ingest the human adjudication for a built task: load the responses → parse → resolve against
    the manifest → promote labels. Returns the count of labels promoted."""
    _, manifest_d = store.load_task_bundle(recipe.task_id, annotations=annotations,
                                           store=teacher_store)
    manifest = tasks.TaskManifest.from_dict(manifest_d)
    records = {b: store.load_records(b, recipe.lang) for b in recipe.books}
    parsed = responses.parse_ui_responses(store.load_human_responses(recipe.task_id,
                                                                      annotations=annotations))
    resolved = responses.resolve_adjudication(manifest, parsed, records, title=recipe.title,
                                              complete=True)
    return promote.promote_labels(resolved.labels, annotations=annotations)


def _rep_rows(reps: Sequence[panel_mod.PanelRep]) -> list[dict]:
    """Flatten per-rep panel output to committed evidence rows (model + rep + key + verdict +
    status) — the raw reps kept in `panel_runs` behind the resolved `votes.jsonl`."""
    return [{"item_id": rep.item_id, "tag": rep.tag, "rep": rep.rep, "model": rep.model,
             "key": row.key, "label": row.label, "conf": row.conf,
             "finish_reason": rep.finish_reason}
            for rep in reps for row in rep.response.rows]


def _main() -> None:
    """CLI: `python -m lineation_core.teacher.recipes <build|panel|ingest> <recipe.toml>`. `build`
    persists the task bundle (text recipes; a vision recipe needs `render.py` wired); `panel` runs
    the live OpenRouter panel and promotes votes; `ingest` resolves the downloaded human responses
    and promotes labels."""
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(prog="lineation-teacher",
                                     description="build / panel / ingest a lineation recipe")
    parser.add_argument("command", choices=("build", "panel", "ingest"))
    parser.add_argument("recipe", help="path to a recipe .toml")
    args = parser.parse_args()
    recipe = load_recipe(Path(args.recipe).read_text())

    if args.command == "build":
        task = build(recipe)
        print(f"built {recipe.task_id}: {len(task.items)} items, "
              f"{len(task.manifest.by_key)} votable lines")
    elif args.command == "panel":
        from .openrouter import OpenRouterCompleter
        print(f"promoted {panel(recipe, OpenRouterCompleter())} panel votes for {recipe.task_id}")
    else:
        print(f"promoted {ingest(recipe)} human labels for {recipe.task_id}")


if __name__ == "__main__":
    _main()
