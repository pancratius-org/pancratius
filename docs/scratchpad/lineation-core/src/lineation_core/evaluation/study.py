# research-pure: the STUDY runner — a TOML experiment is run as an INSTRUMENT, producing evidence.
"""A study is the lab-notebook unit: one `experiment.toml` (a research question + hypothesis in the top
comment + a frozen dataset + a one-axis sweep + the metrics) run by THIS runner, never the recipe
runner — the name keeps "produces evidence" apart from "produces truth". The runner invokes the
teacher panel as an INSTRUMENT and writes ONLY into the experiment folder (`scorecard.json` +
`report.md` + `manifest.json` + a derived `replies.jsonl` resume cache); it NEVER writes
`annotations/`.

Two layers, like `policy_replay`:

  - the config layer (`load_experiment`/`sweep_recipes`) — pure parse of the experiment toml, reusing
    `load_recipe` for the panel half (`[[readers]]`/`[prompts]`/`reps`/`contract`/`temperature`);
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
from ..identity import BookId, Label, LineId, ReaderTag
from ..teacher import recipes, responses, tasks
from ..teacher.panel import (ChatCompleter, PanelConfig, ReaderConfig, ResponseContract, run_panel)
from ..teacher.recipes import Recipe
from ..teacher.tasks import ItemSpec, Modality
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
# the per-reader max_tokens the run shell uses (the panel `ReaderConfig` default), stamped into the
# manifest; named here so the manifest value and the readers it built stay one source.
_DEFAULT_MAX_TOKENS = ReaderConfig("", "", Modality.TEXT).max_tokens


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
    """A parsed experiment: its header, the base panel `Recipe` (readers/prompts/reps/contract/
    temperature + the eval-set selector with books derived from the dataset), the dataset name, the
    sweep, and the requested metrics. `sweep_recipes` expands `base` along the sweep into one Recipe
    per point."""
    meta: ExperimentMeta
    base: Recipe
    dataset_name: str
    sweep: Sweep
    metrics: tuple[str, ...]
    prompt_files: Mapping[str, str]    # modality value → prompt filename (for the manifest fingerprint)
    base_temperature: float = 0.0      # sampling temp for non-temperature sweeps (a contract sweep holds
                                       # it fixed across points); a temperature sweep overrides per point


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
                      prompt_files=prompt_files, base_temperature=float(d.get("temperature", 0.0)))


def _dataset_books(name: str, *, annotations: Path | None) -> tuple[BookId, ...]:
    """The books a frozen eval set spans, from its own LineIds — so the author need not re-list them
    (and a stale author list can't diverge from the data)."""
    rows = store.load_eval_set(name, annotations=annotations)
    return tuple(sorted({LineId.from_key(r["id"]).book_id for r in rows}))


def _base_recipe(d: Mapping[str, object], exp_d: Mapping[str, object], dataset_name: str,
                 books: tuple[BookId, ...], *, prompts_dir: Path | None) -> Recipe:
    """The base panel recipe: load the panel half via `load_recipe` over a toml augmented with the
    derived `[selection]` (`eval_set:<name>`, the derived books, the tiling params) and the
    experiment's id as `task_id`, so the panel config (readers/prompts/reps/contract/temperature)
    parses through the ONE recipe loader, not a second grammar."""
    sel = d.get("selection", {})
    aug = dict(d)
    aug["task_id"] = str(exp_d.get("id", d.get("task_id", "experiment")))
    aug["selection"] = {
        "books": list(books), "selector": f"eval_set:{dataset_name}",
        "target": int(sel.get("target", 10)), "context_radius": int(sel.get("context_radius", 2)),
        "lang": str(sel.get("lang", "ru"))}
    return recipes.load_recipe(_dumps(aug), prompts_dir=prompts_dir)


def _dumps(d: Mapping[str, object]) -> str:
    """Round-trip a parsed-then-augmented toml dict back to text for `load_recipe` (which parses text).
    A minimal emitter over the shapes a recipe uses — tables, arrays-of-tables, scalars."""
    import json as _json

    lines: list[str] = []
    scalars = {k: v for k, v in d.items() if not isinstance(v, (dict, list))}
    for k, v in scalars.items():
        lines.append(f"{k} = {_json.dumps(v)}")
    for k, v in d.items():
        if isinstance(v, dict):
            lines.append(f"\n[{k}]")
            for kk, vv in v.items():
                lines.append(f"{kk} = {_json.dumps(vv)}")
        elif isinstance(v, list) and v and all(isinstance(e, dict) for e in v):
            for e in v:
                lines.append(f"\n[[{k}]]")
                for kk, vv in e.items():
                    lines.append(f"{kk} = {_json.dumps(vv)}")
    return "\n".join(lines) + "\n"


def sweep_recipes(exp: Experiment) -> list[tuple[str, Recipe]]:
    """Expand the base recipe along the sweep into `[(point_label, Recipe)]` — one Recipe per point.
    The CONTRACT axis varies `Recipe.contract`; the TEMPERATURE axis is applied per-reader at
    PanelConfig build time (temperature lives on `ReaderConfig`, not `Recipe`), so here it leaves a
    marker point label the run shell reads — the Recipe itself is unchanged across temperature points."""
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
    """The frozen eval set's `{LineId: truth}` and its line list — the scoring denominator."""
    rows = store.load_eval_set(dataset_name, annotations=annotations)
    truth: dict[LineId, Label] = {}
    lines: list[LineId] = []
    for r in rows:
        lid = LineId.from_key(r["id"])
        truth[lid] = r["label"]
        lines.append(lid)
    return truth, lines


def _build_specs(recipe: Recipe, *, annotations: Path | None) -> tuple[list[ItemSpec], dict]:
    """Select the eval lines → tile into regions → page-size (so an over-page region splits, keeping
    every votable line). Returns the specs + the `{book: records}` the task build needs."""
    selection = recipes.select_lines(recipe, annotations=annotations)
    records = store.load_records_many(recipe.books, recipe.lang)
    modality = Modality.VISION if recipe.vision else Modality.TEXT
    specs: list[ItemSpec] = []
    for book in recipe.books:
        tiled = recipes.tile_regions(book, records[book], selection.get(book, set()),
                                     target=recipe.target, context_radius=recipe.context_radius,
                                     modality=modality)
        specs.extend(recipes.page_size_regions(tiled, records[book],
                                               context_radius=recipe.context_radius))
    return specs, records


def _render_assets(specs: Sequence[ItemSpec]):
    """The LibreOffice page composites for a vision study (one per page) — built lazily so the import
    and LibreOffice are touched ONLY on a vision run."""
    from ..teacher import render as render_mod
    return render_mod.make_compositor(render_mod.libreoffice_pages())(specs)


def _readers_at(recipe: Recipe, point_label: str, axis: str,
                base_temperature: float) -> tuple[ReaderConfig, ...]:
    """The panel readers for one sweep point: the recipe's readers as `ReaderConfig`s. A temperature
    sweep sets each reader's temp to the point; any other axis holds it at the experiment's base
    temperature (so a contract sweep compares the schemas at ONE fixed sampling temperature)."""
    temp = float(point_label) if axis == "temperature" else base_temperature
    return tuple(ReaderConfig(r.tag, r.model, r.modality, temperature=temp)
                 for r in recipe.readers)


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
    cached = _load_reply_cache(exp.meta.id, experiments_dir=experiments_dir)

    results: dict[str, tuple[ReaderResult, ...]] = {}
    models: dict[ReaderTag, str] = {}
    for point_label, recipe in sweep_recipes(exp):
        specs, records = _build_specs(recipe, annotations=annotations)
        assets = _render_assets(specs) if recipe.vision else {}
        task = tasks.build_task(title=exp.meta.id, instructions=recipe.instructions,
                                specs=specs, records=records, assets=assets)
        readers = _readers_at(recipe, point_label, exp.sweep.axis, exp.base_temperature)
        cfg = PanelConfig(readers=readers, reps=recipe.reps, contract=recipe.contract)
        reps = run_panel(task, cfg, completer, cached=cached,
                         on_call=_reply_saver(exp.meta.id, experiments_dir=experiments_dir),
                         instructions_by_modality=recipe.prompts or None,
                         max_workers=recipe.max_workers)
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
    """Build the provenance manifest, fingerprinting the eval set + the per-modality prompts; the git
    SHA and timestamp are resolved by the caller (timestamp = when the evidence was first produced)."""
    eval_path = (annotations or paths.ANNOTATIONS) / "eval_sets" / f"{exp.dataset_name}.json"
    prompt_fps: dict[str, PromptFingerprint] = {}
    for modality, filename in _prompt_filenames(exp).items():
        path = (prompts_dir or paths.PROMPTS) / filename
        prompt_fps[modality] = PromptFingerprint(filename=filename, sha256=store.sha256_file(path))
    return Manifest(
        git_sha=git_sha, timestamp=timestamp, eval_set=exp.dataset_name,
        eval_set_sha256=store.sha256_file(eval_path), prompts=prompt_fps,
        response_contract=exp.base.contract.value, models=dict(sorted(models.items())),
        temperature=_base_temperature(exp), max_tokens=_DEFAULT_MAX_TOKENS,
        reps=exp.base.reps, seed=exp.meta.seed, price_table_version=prices.version,
        sweep_axis=exp.sweep.axis, sweep_points=exp.sweep.points)


def _prompt_filenames(exp: Experiment) -> dict[str, str]:
    """The per-modality prompt FILENAMES the recipe loaded (for the manifest fingerprint), from the
    toml's `[prompts]` table; empty if the recipe used inline instructions."""
    return dict(exp.prompt_files)


def _base_temperature(exp: Experiment) -> float:
    """The base temperature stamped into the manifest: the first temperature sweep point if the study
    sweeps temperature, else the experiment's fixed base temperature."""
    if exp.sweep.axis == "temperature" and exp.sweep.points:
        return float(exp.sweep.points[0])
    return exp.base_temperature


def _report(scorecard: Scorecard, exp: Experiment) -> str:
    """A human-readable report.md: the research question + a per-reader × sweep-point table of the
    headline numbers (balanced accuracy, per-class recall, coverage, instability, truncations, cost)."""
    lines = [f"# {scorecard.experiment_id}", "", f"**Question.** {scorecard.question}", "",
             f"Sweep axis: `{scorecard.sweep_axis}` over {list(exp.sweep.points)}; "
             f"eval set `{exp.dataset_name}`; git `{scorecard.manifest.git_sha}`.", ""]
    head = "| point | reader | modality | balAcc | prose | lin | cover | instab | trunc | $/1k |"
    rule = "|" + "|".join(["---"] * 10) + "|"
    lines += [head, rule]
    for point, readers in scorecard.results.items():
        for r in readers:
            lines.append(
                f"| {point} | {r.tag} | {r.modality.value} | {r.quality.balanced_acc:.3f} | "
                f"{r.quality.prose_recall:.3f} | {r.quality.lineated_recall:.3f} | "
                f"{r.health.coverage:.3f} | {r.quality.instability:.2f} | {r.health.truncated} | "
                f"{r.cost.usd_per_1k_lines:.4f} |")
    return "\n".join(lines) + "\n"


# --- the resumable reply cache (experiment-folder replies.jsonl) -------------------------------

def _load_reply_cache(experiment_id: str, *, experiments_dir: Path | None):
    """The study's saved replies as a panel `CallCache` — last-saved wins per call identity. The
    resume source: `run_panel` reuses these instead of re-paying. A row from before prompt
    fingerprinting has no `prompt_hash` → `""`, which cannot match a live fingerprint (safely re-calls)."""
    from ..teacher.panel import CompletionRequest

    cache = {}
    for row in store.load_experiment_replies(experiment_id, experiments=experiments_dir):
        cache[(row["item_id"], row["tag"], int(row["rep"]), row["model"],
               row.get("prompt_hash", ""))] = CompletionRequest.reply_from_row(row)
    return cache


def _reply_saver(experiment_id: str, *, experiments_dir: Path | None):
    """A `run_panel` `on_call` that appends each fresh reply to the folder's `replies.jsonl` resume
    cache the instant it lands (before parse). The request owns the row shape (`to_row`)."""
    def save(req, reply) -> None:
        store.save_experiment_reply(experiment_id, req.to_row(reply), experiments=experiments_dir)
    return save


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
