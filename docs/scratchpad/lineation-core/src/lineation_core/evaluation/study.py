# research-pure: the STUDY runner — a TOML experiment is run as an INSTRUMENT, producing evidence.
"""A study is the lab-notebook unit: one `experiment.toml` (a research question + hypothesis in the top
comment + a frozen dataset + a one-axis sweep + the metrics) run by THIS runner, never the recipe
runner — the name keeps "produces evidence" apart from "produces truth". The runner invokes the
teacher panel as an INSTRUMENT and writes ONLY into the experiment folder (`scorecard.json` +
`report.md` + `manifest.json` + a derived `replies.jsonl` resume cache); it NEVER writes
`annotations/`.

Two layers, like `policy_replay`:

  - the config layer (`load_experiment`/`sweep_recipes`) — pure parse of the experiment toml, reusing
    `recipe_from_dict` for the panel half (`[[readers]]`/`[prompts]`/`reps`/`contract`/`temperature`);
  - the run shell (`run_study`) — select → tile → page-size → render(if vision) → panel(resumable) →
    resolve → score per reader → stamp the manifest → write the durable files. The network is the
    injected `ChatCompleter`; the price table is injected.

A study sweeps ONE axis (contract OR temperature) — never a cross-product — so a scorecard reads as a
single controlled comparison.
"""
from __future__ import annotations

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from .. import paths, store
from . import datasets
from ..identity import BookId, Label, LineId, ReaderTag
from ..teacher import recipes, responses, tasks
from ..teacher.panel import (ChatCompleter, PanelConfig, ReaderConfig, ResponseContract,
                             resume_cache, run_panel)
from ..teacher.recipes import Recipe
from ..teacher.tasks import ItemSpec
from .manifest import Manifest, PromptFingerprint
from .prices import PriceTable
from .reader_metrics import (DecisionQuality, ProtocolHealth, ReaderResult, class_recall, coverage,
                             instability, reader_cost)

# the sweep axes a study may vary — exactly one per experiment.
SWEEP_AXES = frozenset({"contract", "temperature"})
# the per-reader metrics a study may request (the scorecard always carries all three dimensions; this
# is the AUTHORED declaration of what the experiment is about, validated fail-loud against this set).
METRICS = frozenset({"balanced_acc", "prose_recall", "lineated_recall", "instability",
                     "coverage", "truncated", "usd", "usd_per_1k_lines"})
DATASET_SOURCES = frozenset({"eval_set"})   # the only dataset kind in v1


@dataclass(frozen=True, slots=True)
class ExperimentMeta:
    """The lab-notebook header: a stable id (the folder slug), the experiment kind, the research
    question (echoed into report.md), the active-learning round, and the seed."""
    id: str
    kind: str
    question: str
    round: int
    seed: int


@dataclass(frozen=True, slots=True)
class Sweep:
    """The ONE axis a study varies and the points along it. `axis` is `contract` (vary the output
    schema) or `temperature` (vary sampling); `points` are the values, in order."""
    axis: str
    points: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Experiment:
    """A parsed experiment: its header, the base panel `Recipe` (readers — sampling included —
    /prompts/reps/contract + the eval-set selector with books derived from the dataset), the dataset
    name, the sweep, and the requested metrics. `sweep_recipes` expands `base` along the sweep into
    one Recipe per point."""
    meta: ExperimentMeta
    base: Recipe
    dataset_name: str
    sweep: Sweep
    metrics: tuple[str, ...]
    prompt_files: Mapping[str, str]    # modality value → prompt filename (for the manifest fingerprint)


def load_experiment(toml_text: str, *, prompts_dir: Path | None = None,
                    annotations: Path | None = None) -> Experiment:
    """Parse + validate an experiment toml. Reuses `load_recipe` for the panel half, then parses
    `[experiment]`/`[dataset]`/`[sweep]`/`[metrics]`. `base.books` is DERIVED from the eval set's own
    LineIds (the author lists no books — `select_lines` fails loud on a stray book, so the derived set
    is exactly the dataset's). FAILS LOUD: a `[sweep]` with ≠1 key (a study sweeps ONE axis — no
    cross-product), an unknown axis/metric, a dataset source other than `eval_set`."""
    d = tomllib.loads(toml_text)
    exp_d = d.get("experiment", {})
    ds = d["dataset"]
    if ds.get("source") not in DATASET_SOURCES:
        raise ValueError(f"unknown dataset source {ds.get('source')!r}; v1 supports {sorted(DATASET_SOURCES)}")
    dataset_name = str(ds["name"])

    sweep_d = d["sweep"]
    if len(sweep_d) != 1:
        raise ValueError(f"a study sweeps ONE axis (got keys {sorted(sweep_d)}) — no cross-product; "
                         f"split it into separate experiments")
    axis, points = next(iter(sweep_d.items()))
    if axis not in SWEEP_AXES:
        raise ValueError(f"unknown sweep axis {axis!r}; known: {sorted(SWEEP_AXES)}")
    sweep = Sweep(axis=str(axis), points=tuple(str(p) for p in points))

    metrics = tuple(str(m) for m in d.get("metrics", {}).get("report", sorted(METRICS)))
    unknown = sorted(set(metrics) - METRICS)
    if unknown:
        raise ValueError(f"unknown metric(s) {unknown}; known: {sorted(METRICS)}")

    books = _dataset_books(dataset_name, annotations=annotations)
    base = _base_recipe(d, exp_d, dataset_name, books, prompts_dir=prompts_dir)

    meta = ExperimentMeta(
        id=str(exp_d.get("id", base.task_id)), kind=str(exp_d.get("kind", "reader")),
        question=str(exp_d.get("question", "")), round=int(exp_d.get("round", 0)),
        seed=int(exp_d.get("seed", 0)))
    prompt_files = {str(mod): str(fname) for mod, fname in d.get("prompts", {}).items()}
    return Experiment(meta=meta, base=base, dataset_name=dataset_name, sweep=sweep, metrics=metrics,
                      prompt_files=prompt_files)


def _dataset_books(name: str, *, annotations: Path | None) -> tuple[BookId, ...]:
    """The books a frozen eval slice spans, from its own LineIds — so the author need not re-list
    them (and a stale author list can't diverge from the data)."""
    keys = store.load_eval_set(name, annotations=annotations)
    return tuple(sorted({LineId.from_key(k).book_id for k in keys}))


def _base_recipe(d: Mapping[str, object], exp_d: Mapping[str, object], dataset_name: str,
                 books: tuple[BookId, ...], *, prompts_dir: Path | None) -> Recipe:
    """The base panel recipe: the experiment dict augmented with the derived `[selection]` (the
    derived books, the tiling params) and the experiment's id as `task_id`, validated through the
    ONE recipe loader (`recipe_from_dict`), not a second grammar. The selector is the dataset
    itself — constructed as the `EvalSet` ADT directly, never re-spelled in the author grammar."""
    sel = d.get("selection", {})
    aug = dict(d)
    aug["task_id"] = str(exp_d.get("id", d.get("task_id", "experiment")))
    aug["selection"] = {
        "books": list(books),
        "target": int(sel.get("target", 10)), "context_radius": int(sel.get("context_radius", 2)),
        "lang": str(sel.get("lang", "ru"))}
    return replace(recipes.recipe_from_dict(aug, prompts_dir=prompts_dir),
                   selector=recipes.EvalSet(dataset_name))


def sweep_recipes(exp: Experiment) -> list[tuple[str, Recipe]]:
    """Expand the base recipe along the sweep into `[(point_label, Recipe)]` — one Recipe per point.
    The CONTRACT axis varies `Recipe.contract`; the TEMPERATURE axis is applied per reader by
    `_readers_at` (temperature lives on each `ReaderConfig`), so here it leaves a marker point label
    — the Recipe itself is unchanged across temperature points."""
    out: list[tuple[str, Recipe]] = []
    for point in exp.sweep.points:
        if exp.sweep.axis == "contract":
            out.append((point, replace(exp.base, contract=ResponseContract(point))))
        else:                                   # temperature: applied to ReaderConfig in the run shell
            out.append((point, exp.base))
    return out


# --- the run shell: select → tile → page-size → render → panel → resolve → score ----------------

@dataclass(frozen=True, slots=True)
class Scorecard:
    """The durable result of a study: one `ReaderResult` per reader per sweep point, plus the
    experiment header and provenance manifest. `results[point_label]` is that point's readers. EVIDENCE,
    not truth — it scores a frozen eval set, never writes labels."""
    experiment_id: str
    question: str
    sweep_axis: str
    results: Mapping[str, tuple[ReaderResult, ...]]
    manifest: Manifest

    def to_dict(self) -> dict[str, object]:
        return {
            "experiment_id": self.experiment_id, "question": self.question,
            "sweep_axis": self.sweep_axis,
            "results": {point: [_reader_result_dict(r) for r in readers]
                        for point, readers in self.results.items()},
            "manifest": self.manifest.to_dict()}


def _reader_result_dict(r: ReaderResult) -> dict[str, object]:
    return {
        "tag": r.tag, "modality": r.modality.value,
        "health": {"coverage": r.health.coverage, "truncated": r.health.truncated,
                   "faults": dict(r.health.faults)},
        "quality": {"balanced_acc": r.quality.balanced_acc, "prose_recall": r.quality.prose_recall,
                    "lineated_recall": r.quality.lineated_recall, "instability": r.quality.instability,
                    "n_prose": r.quality.n_prose, "n_lineated": r.quality.n_lineated},
        "cost": {"usd": r.cost.usd, "prompt_tokens": r.cost.prompt_tokens,
                 "completion_tokens": r.cost.completion_tokens,
                 "usd_per_1k_lines": r.cost.usd_per_1k_lines}}


type Truth = Mapping[LineId, Label]


def _eval_truth(dataset_name: str, *, annotations: Path | None) -> tuple[Truth, list[LineId]]:
    """The frozen eval slice's `{LineId: truth}` and its line list — the scoring denominator.
    Truth comes from `labels.jsonl` through the ONE join (`datasets.eval_slice`); the slice file
    is membership only, so a study and a policy replay can never score the same line against two
    different stores."""
    s = datasets.eval_slice(dataset_name, annotations=annotations)
    return s.truth, list(s.lines)


def _build_specs(recipe: Recipe, *, annotations: Path | None) -> tuple[list[ItemSpec], dict]:
    """Select the eval lines → tile into regions → page-size (so an over-page region splits, keeping
    every votable line). Returns the specs + the `{book: records}` the task build needs."""
    selection = recipes.select_lines(recipe, annotations=annotations)
    records = store.load_records_many(recipe.books, recipe.lang)
    specs: list[ItemSpec] = []
    for book in recipe.books:
        tiled = recipes.tile_regions(book, records[book], selection.get(book, set()),
                                     target=recipe.target, context_radius=recipe.context_radius)
        specs.extend(recipes.page_size_regions(tiled, records[book],
                                               context_radius=recipe.context_radius))
    return specs, records


def _render_assets(specs: Sequence[ItemSpec]):
    """The LibreOffice page composites for a vision study (one per page) — built lazily so the import
    and LibreOffice are touched ONLY on a vision run."""
    from ..teacher import render as render_mod
    return render_mod.make_compositor(render_mod.libreoffice_pages())(specs)


def _readers_at(recipe: Recipe, point_label: str, axis: str) -> tuple[ReaderConfig, ...]:
    """The panel readers for one sweep point. A temperature sweep overrides each reader's temp with
    the point; any other axis runs the readers exactly as authored (the experiment's top-level
    `temperature`/`max_tokens` already inherited into them through the ONE recipe loader)."""
    if axis != "temperature":
        return recipe.readers
    return tuple(replace(r, temperature=float(point_label)) for r in recipe.readers)


def run_study(exp: Experiment, completer: ChatCompleter, prices: PriceTable, *,
              now: datetime, git_sha: str, experiments_dir: Path | None = None,
              annotations: Path | None = None, prompts_dir: Path | None = None) -> Scorecard:
    """Run an experiment as an INSTRUMENT and write its evidence into the experiment folder ONLY. For
    each (point_label, recipe): select → tile → page-size → render(iff vision) → run the resumable
    panel (reusing the folder's `replies.jsonl`, persisting each fresh reply before parse) → resolve →
    score each reader on the three dimensions (`reader_metrics`). Stamps the provenance `Manifest` with
    the PASSED-IN `now`/`git_sha`. Writes `scorecard.json`+`report.md`+`manifest.json`. NEVER writes
    `annotations/`. Network is the injected `completer`; pricing the injected `prices`."""
    truth, eval_lines = _eval_truth(exp.dataset_name, annotations=annotations)
    # resume from the folder's replies.jsonl; persist each fresh reply (the request's `to_row`) the
    # instant it lands, before parse — a re-run over a complete cache costs $0.
    cached = resume_cache(store.load_experiment_replies(exp.meta.id, experiments=experiments_dir))

    results: dict[str, tuple[ReaderResult, ...]] = {}
    models: dict[ReaderTag, str] = {}
    for point_label, recipe in sweep_recipes(exp):
        specs, records = _build_specs(recipe, annotations=annotations)
        assets = _render_assets(specs) if recipe.vision else {}
        task = tasks.build_task(title=exp.meta.id, instructions=recipe.instructions,
                                specs=specs, records=records, assets=assets)
        readers = _readers_at(recipe, point_label, exp.sweep.axis)
        cfg = PanelConfig(readers=readers, reps=recipe.reps, contract=recipe.contract)
        reps = run_panel(
            task, cfg, completer, cached=cached,
            on_call=lambda req, reply: store.save_experiment_reply(
                exp.meta.id, req.to_row(reply), experiments=experiments_dir),
            instructions_by_modality=recipe.prompts or None, max_workers=recipe.max_workers)
        results[point_label] = _score_readers(task.manifest, reps, records, readers, truth,
                                              eval_lines, prices)
        models.update({r.tag: r.model for r in readers})

    # the evidence's timestamp is WHEN IT WAS FIRST PRODUCED: a prior run's stamp wins, so a $0 replay
    # rewrites byte-identical files and never back-dates the evidence to the replay moment.
    produced = store.load_experiment_timestamp(exp.meta.id, experiments=experiments_dir) or now.isoformat()
    manifest = _stamp_manifest(exp, models, prices, timestamp=produced, git_sha=git_sha,
                               annotations=annotations, prompts_dir=prompts_dir)
    scorecard = Scorecard(experiment_id=exp.meta.id, question=exp.meta.question,
                          sweep_axis=exp.sweep.axis, results=results, manifest=manifest)
    store.write_experiment(exp.meta.id, scorecard=scorecard.to_dict(),
                           report=_report(scorecard, exp), manifest=manifest.to_dict(),
                           experiments=experiments_dir)
    return scorecard


def _score_readers(manifest, reps, records, readers: Sequence[ReaderConfig], truth: Truth,
                   eval_lines: Sequence[LineId], prices: PriceTable) -> tuple[ReaderResult, ...]:
    """Score each reader on the three dimensions from this point's reps. Resolution is done PER READER —
    each reader's responses resolve in isolation, so its `ProtocolHealth` (faults incl. per-reader
    MISSING_KEY, coverage, truncation) and its votes come from its OWN resolution, never a task-level
    `rv` that would smear one reader's faults across the whole panel."""
    from collections import Counter, defaultdict

    from ..teacher.panel import FinishReason

    reps_by_tag: dict[ReaderTag, list] = defaultdict(list)
    truncated: Counter[ReaderTag] = Counter()
    for rep in reps:
        reps_by_tag[rep.tag].append(rep)
        if rep.finish_reason == FinishReason.LENGTH:
            truncated[rep.tag] += 1
    n_lines = len(eval_lines)

    out: list[ReaderResult] = []
    for r in readers:
        my_reps = reps_by_tag.get(r.tag, [])
        rv = responses.resolve_panel(manifest, [rep.response for rep in my_reps], records,
                                     complete=False)   # this reader's responses ONLY
        votes = list(rv.votes)
        faults = Counter(f.fault.value for f in rv.faults)   # this reader's own faults
        bal, pr, lr, n_p, n_l = class_recall(votes, truth, eval_lines)
        health = ProtocolHealth(coverage=coverage(votes, eval_lines),
                                truncated=truncated.get(r.tag, 0), faults=dict(faults))
        quality = DecisionQuality(balanced_acc=bal, prose_recall=pr, lineated_recall=lr,
                                  instability=instability(votes), n_prose=n_p, n_lineated=n_l)
        cost = reader_cost(my_reps, prices.price(r.model), n_lines=n_lines)
        out.append(ReaderResult(tag=r.tag, modality=r.modality, health=health, quality=quality,
                                cost=cost))
    return tuple(out)


def _stamp_manifest(exp: Experiment, models: Mapping[ReaderTag, str], prices: PriceTable, *,
                    timestamp: str, git_sha: str, annotations: Path | None,
                    prompts_dir: Path | None) -> Manifest:
    """Build the provenance manifest, fingerprinting the eval slice (membership file AND the joined
    truth) + the per-modality prompts; the git SHA and timestamp are resolved by the caller
    (timestamp = when the evidence was first produced)."""
    eval_path = (annotations or paths.ANNOTATIONS) / "eval_sets" / f"{exp.dataset_name}.json"
    prompt_fps: dict[str, PromptFingerprint] = {}
    for modality, filename in exp.prompt_files.items():
        path = (prompts_dir or paths.PROMPTS) / filename
        prompt_fps[modality] = PromptFingerprint(filename=filename, sha256=store.sha256_file(path))
    temperature, max_tokens = _sampling(exp)
    truth_sha = datasets.truth_fingerprint(
        datasets.eval_slice(exp.dataset_name, annotations=annotations))
    return Manifest(
        git_sha=git_sha, timestamp=timestamp, eval_set=exp.dataset_name,
        eval_set_sha256=store.sha256_file(eval_path), truth_sha256=truth_sha, prompts=prompt_fps,
        base_response_contract=exp.base.contract.value, models=dict(sorted(models.items())),
        temperature=temperature, max_tokens=max_tokens,
        reps=exp.base.reps, seed=exp.meta.seed, price_table_version=prices.version,
        sweep_axis=exp.sweep.axis, sweep_points=exp.sweep.points)


def _sampling(exp: Experiment) -> tuple[float, int]:
    """The sampling config the manifest stamps — the readers' REAL values, never an assumed default.
    A temperature sweep's base is its first point (`sweep_points` is the authoritative record).
    Mixed per-reader values cannot be honestly stamped in the manifest's one slot — fail loud."""
    temps = {r.temperature for r in exp.base.readers}
    tokens = {r.max_tokens for r in exp.base.readers}
    if len(temps) != 1 or len(tokens) != 1:
        raise ValueError(f"manifest stamps ONE sampling config but the experiment's readers mix "
                         f"temperatures {sorted(temps)} / max_tokens {sorted(tokens)}")
    if exp.sweep.axis == "temperature" and exp.sweep.points:
        return float(exp.sweep.points[0]), tokens.pop()
    return temps.pop(), tokens.pop()


def _report(scorecard: Scorecard, exp: Experiment) -> str:
    """A human-readable report.md: the research question + a per-reader × sweep-point table of the
    headline numbers (balanced accuracy, per-class recall, coverage, protocol FAULTS, instability,
    truncations, cost) — faults are the protocol-health signal, never omitted from the table."""
    lines = [f"# {scorecard.experiment_id}", "", f"**Question.** {scorecard.question}", "",
             f"Sweep axis: `{scorecard.sweep_axis}` over {list(exp.sweep.points)}; "
             f"eval set `{exp.dataset_name}`; git `{scorecard.manifest.git_sha}`.", ""]
    head = "| point | reader | modality | balAcc | prose | lin | cover | faults | instab | trunc | $/1k |"
    rule = "|" + "|".join(["---"] * 11) + "|"
    lines += [head, rule]
    for point, readers in scorecard.results.items():
        for r in readers:
            lines.append(
                f"| {point} | {r.tag} | {r.modality.value} | {r.quality.balanced_acc:.3f} | "
                f"{r.quality.prose_recall:.3f} | {r.quality.lineated_recall:.3f} | "
                f"{r.health.coverage:.3f} | {_faults_cell(r.health.faults)} | "
                f"{r.quality.instability:.2f} | {r.health.truncated} | "
                f"{r.cost.usd_per_1k_lines:.4f} |")
    return "\n".join(lines) + "\n"


def _faults_cell(faults: Mapping[str, int]) -> str:
    """A compact `key:count` rendering of a reader's resolution faults — `clean` when none."""
    return " ".join(f"{k}:{v}" for k, v in sorted(faults.items())) if faults else "clean"


def _main() -> None:
    """CLI: `python -m lineation_core.evaluation.study run <experiment.toml>` — runs the study live
    (the SDK extra), stamping the timestamp + git SHA from the shell. The experiment folder is the
    toml's parent (so the durable files land beside it). A live run needs the live extra:
        `uv run --extra live python -m lineation_core.evaluation.study run <experiment.toml>`."""
    import argparse
    from datetime import UTC

    from ..teacher.openrouter import OpenRouterCompleter

    parser = argparse.ArgumentParser(prog="lineation-study", description="run a lineation study")
    parser.add_argument("command", choices=("run",))
    parser.add_argument("experiment", help="path to an experiment .toml")
    args = parser.parse_args()

    path = Path(args.experiment)
    exp = load_experiment(path.read_text())
    scorecard = run_study(exp, OpenRouterCompleter(), PriceTable.from_dict(store.load_prices()),
                          now=datetime.now(UTC), git_sha=store.git_sha(),
                          experiments_dir=path.resolve().parent.parent)
    print(f"study {exp.meta.id}: {sum(len(rs) for rs in scorecard.results.values())} reader-results "
          f"over {len(scorecard.results)} sweep points -> {exp.meta.id}/")


if __name__ == "__main__":
    _main()
