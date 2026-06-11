# research-pure: recipes — turn a SELECTION of votable lines into task items, honoring authorial units.
"""The selection→tiling layer (the toml/CLI orchestration lands on top of this later). `tile_regions`
groups a book's selected lines into regions of WHOLE runs (~`target` votable lines, never splitting a
run / display-line group / stanza to hit the cap) plus a small context radius, in document order — so
what a reader is shown is deterministic and OWNED here, not reconstructed by sorting."""
from __future__ import annotations

import tomllib
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import assert_never

from .. import store
from ..annotations import LabelSource, LineLabel, PanelVote, load_votes
from ..identity import BookId, JsonRow, LineId, LineTextHash, ReaderTag, TaskId
from ..records import LineRecord, Run, runs
from . import decision as decision_mod
from . import panel as panel_mod
from . import promote, responses, tasks
from .decision import DecisionPolicy, LineDecision, PanelRoster
from .panel import ReaderConfig
from .tasks import PAGE_SPAN_CAP, AssetKind, ItemSpec, Modality


# Where the lines to poll come from — the closed selection ADT. Authored as a string
# ("all" | "eval_set:<name>" | "selection_file:<name>") parsed ONCE at the toml edge
# (`parse_selector`); everything downstream matches on the ADT, never re-parses the grammar.

@dataclass(frozen=True, slots=True)
class AllVotable:
    """Every votable line of the recipe's books."""


@dataclass(frozen=True, slots=True)
class EvalSet:
    """A committed frozen eval slice's membership (`eval_sets/<name>.json` — LineId keys)."""
    name: str


@dataclass(frozen=True, slots=True)
class SelectionFile:
    """A committed LineId list (`selections/<name>.json`) — e.g. the active-learning acquire set,
    written as DATA by the student/eval side, never imported here."""
    name: str


type Selector = AllVotable | EvalSet | SelectionFile


def parse_selector(text: str) -> Selector:
    """The author syntax → the ADT, at the toml edge ONLY. FAILS LOUD on an unknown kind or a
    missing name."""
    kind, _, arg = text.partition(":")
    match kind, arg:
        case "all", "":
            return AllVotable()
        case "eval_set", name if name:
            return EvalSet(name)
        case "selection_file", name if name:
            return SelectionFile(name)
    raise ValueError(f"unknown selector: {text!r}")


@dataclass(frozen=True, slots=True)
class Recipe:
    """A declarative annotation campaign (a committed toml). It binds everything needed to
    deterministically rebuild the derived task payload: the bundle id, the books + line selector,
    the reader-facing instructions, the panel readers + reps, and the tiling parameters. The task's
    vision-ness is derived from the readers — composites are rendered iff any reader reads vision."""
    task_id: TaskId
    title: str
    instructions: str              # the default prompt (vision / human-adjudicator); see `prompts`
    books: tuple[BookId, ...]
    selector: Selector             # where the lines to poll come from (parsed at the toml edge)
    # the panel's readers, sampling included — the SAME spec `run_panel` queries with; the toml may
    # set `temperature`/`max_tokens` top-level (all readers) or per reader.
    readers: tuple[ReaderConfig, ...]
    lang: str = "ru"
    reps: int = 1
    target: int = 10
    context_radius: int = 1
    # the response CONTRACT every reader returns — json_array (free-enum key), json_keyed (keys ARE
    # the schema), or tsv (schemaless). See `teacher.contracts`. Default json_array; from the TOML.
    contract: panel_mod.ResponseContract = panel_mod.ResponseContract.JSON_ARRAY
    # panel fetch concurrency — the calls are I/O-bound on the completer, so a small worker pool is the
    # default; `1` forces the sequential path. Config (TOML), never a hardcoded global in the runner.
    max_workers: int = 8
    # per-MODALITY reader prompts (from a `[prompts]` table referencing files in campaigns/prompts/):
    # a vision reader gets the page-authority prompt, a text reader a listing/structure-authority one
    # (a text reader cannot use a page it never receives). Empty ⇒ all readers use `instructions`.
    prompts: Mapping[Modality, str] = field(default_factory=dict)
    # The decision config — present together iff the recipe is ROUTED (`build`/`panel` ignore them;
    # `route` requires them). `roster` names which readers decide (anchor + disagreement detectors);
    # `decision` is the ONE settled live policy (the legacy anchor-led gate). Both come from the same
    # TOML grammar the eval harness uses, so a routed recipe and its policy-eval stay in lockstep.
    roster: PanelRoster | None = None
    decision: DecisionPolicy | None = None

    @property
    def vision(self) -> bool:
        return any(r.modality is Modality.VISION for r in self.readers)


def load_recipe(toml_text: str, *, prompts_dir: Path | None = None) -> Recipe:
    """Recipe toml text → `Recipe`; parsing + validation live in `recipe_from_dict`."""
    return recipe_from_dict(tomllib.loads(toml_text), prompts_dir=prompts_dir)


def recipe_from_dict(d: Mapping[str, object], *, prompts_dir: Path | None = None) -> Recipe:
    """Validate a parsed recipe dict (the toml grammar; the study runner passes its augmented dict
    directly). FAILS LOUD on a missing required field, an unknown reader modality, empty books, or
    duplicate reader tags. The optional `[roster]`/`[decision]` tables (a ROUTED recipe) are
    validated together — both or neither — with the roster confined to the recipe's own readers. An
    optional `[prompts]` table maps a MODALITY to a prompt FILE in `prompts_dir` (default
    `campaigns/prompts/`), read at load — so a 3-reader vision+text panel gives each reader the
    right authority; an explicit `human` key names the adjudicator's prompt, else the vision prompt
    doubles as it. A recipe gives EITHER `[prompts]` OR an inline `instructions`."""
    temperature = float(d.get("temperature", panel_mod.DEFAULT_TEMPERATURE))
    max_tokens = int(d.get("max_tokens", panel_mod.DEFAULT_MAX_TOKENS))
    readers = tuple(ReaderConfig(tag=r["tag"], model=r["model"],
                                 modality=Modality(r.get("modality", "text")),
                                 temperature=float(r.get("temperature", temperature)),
                                 max_tokens=int(r.get("max_tokens", max_tokens)))
                    for r in d.get("readers", []))
    tags = [r.tag for r in readers]
    if len(set(tags)) != len(tags):
        raise ValueError(f"duplicate reader tags in recipe: {tags}")
    sel = d["selection"]
    books = tuple(str(b) for b in sel["books"])
    if not books:
        raise ValueError("recipe selection.books is empty")
    if ("roster" in d) != ("decision" in d):
        raise ValueError("a routed recipe needs BOTH [roster] and [decision] (or neither)")
    roster = (decision_mod.parse_roster(d["roster"], known=frozenset(tags),
                                        known_desc="the recipe's readers")
              if "roster" in d else None)
    dec = decision_mod.policy_from_toml(d["decision"]) if "decision" in d else None
    if d.get("prompts") and "instructions" in d:
        raise ValueError("recipe gives both [prompts] and inline instructions — choose one")
    prompt_files = {str(k): str(v) for k, v in d.get("prompts", {}).items()}
    human_file = prompt_files.pop("human", None)       # the adjudicator's prompt, named explicitly
    prompts = {Modality(mod): store.load_prompt(fname, prompts_dir=prompts_dir)
               for mod, fname in prompt_files.items()}             # via the store boundary, fail-loud
    instructions = (d.get("instructions")
                    or (store.load_prompt(human_file, prompts_dir=prompts_dir) if human_file else None)
                    or prompts.get(Modality.VISION) or next(iter(prompts.values()), ""))
    contract = panel_mod.ResponseContract(d.get("contract", panel_mod.ResponseContract.JSON_ARRAY.value))
    return Recipe(
        task_id=d["task_id"], title=d.get("title", ""), instructions=instructions,
        books=books, selector=parse_selector(sel.get("selector", "all")), readers=readers,
        lang=sel.get("lang", "ru"), reps=int(d.get("reps", 1)),
        target=int(sel.get("target", 10)), context_radius=int(sel.get("context_radius", 1)),
        max_workers=int(d.get("max_workers", 8)), contract=contract,
        roster=roster, decision=dec, prompts=prompts)


def tile_regions(book_id: BookId, records: Sequence[LineRecord], selected: set[LineId], *,
                 target: int = 10, context_radius: int = 1, max_gap: int = 8) -> list[ItemSpec]:
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
                                region=region, votable=votable))
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


def page_size_regions(specs: Sequence[ItemSpec], records: Sequence[LineRecord], *,
                      max_span: int = PAGE_SPAN_CAP, context_radius: int = 1) -> list[ItemSpec]:
    """Split any region whose votable lines span more source paragraphs than fits on one rendered page
    into consecutive page-sized sub-regions, keeping every votable line. `tile_regions` never splits an
    authorial run, so a long single-block work (a 100+ line litany) can yield one region wider than the
    render's page cap — which the renderer REFUSES. A region already within a page passes through
    unchanged. The pure post-pass a VISION study runs over `tile_regions` so it never re-implements
    page sizing; document order and votable membership are preserved."""
    return [out for spec in specs
            for out in _page_size(spec, records, max_span=max_span, context_radius=context_radius)]


def _page_size(spec: ItemSpec, records: Sequence[LineRecord], *, max_span: int,
               context_radius: int) -> list[ItemSpec]:
    """One region → its page-sized sub-regions (one per cluster of votable lines within `max_span`
    source paragraphs), each with `context_radius` neighbours; a within-page region passes through.
    UNMAPPED votable lines (§14-P1 span-drops, which carry no source ordinal to bin by) are carried
    through too — each attached to the cluster nearest it in DOCUMENT position — so the union of the
    sub-regions' votables is exactly `spec.votable`, never a silent drop."""
    pos = {r.id: i for i, r in enumerate(records)}
    mapped = sorted((lid for lid in spec.votable if lid.is_mapped), key=lambda x: x.src_ordinal)
    unmapped = [lid for lid in spec.votable if not lid.is_mapped]
    if not mapped or mapped[-1].src_ordinal - mapped[0].src_ordinal <= max_span:
        return [spec]
    clusters: list[list[LineId]] = [[mapped[0]]]
    for lid in mapped[1:]:
        if lid.src_ordinal - clusters[-1][0].src_ordinal <= max_span:
            clusters[-1].append(lid)
        else:
            clusters.append([lid])
    extra: list[list[LineId]] = [[] for _ in clusters]   # unmapped votables, binned to nearest cluster
    for lid in unmapped:
        k = min(range(len(clusters)),
                key=lambda i: min(abs(pos[lid] - pos[c]) for c in clusters[i]))
        extra[k].append(lid)
    out: list[ItemSpec] = []
    for k, cl in enumerate(clusters):
        members = [*cl, *extra[k]]                        # mapped cluster + its nearest unmapped votables
        lo = max(0, min(pos[m] for m in members) - context_radius)
        hi = min(len(records) - 1, max(pos[m] for m in members) + context_radius)
        out.append(ItemSpec(region_id=f"{spec.region_id}s{k}",
                            region=tuple(records[i].id for i in range(lo, hi + 1)),
                            votable=frozenset(members)))
    return out


# --- the orchestration shell: selector → records → tiled task bundle --------------------------

type Selection = dict[BookId, set[LineId]]               # the votable lines to poll, per book
# specs → {region_id: assets}; the authored page render comes from each region's src_ordinal span,
# so the renderer needs only the specs (implemented in render.py, injected when a recipe is vision).
type RenderFn = Callable[[Sequence[ItemSpec]], dict[tasks.RegionId, tuple[tasks.EvidenceAsset, ...]]]


def select_lines(recipe: Recipe, *, annotations: Path | None = None) -> Selection:
    """Resolve the recipe's selector to the votable LineIds to poll, per book. Selection is DATA —
    a committed eval slice / LineId list is read through the store, never imported."""
    match recipe.selector:
        case AllVotable():
            return {b: {r.id for r in store.load_records(b, recipe.lang) if r.votable}
                    for b in recipe.books}
        case EvalSet(name):
            ids = [LineId.from_key(k) for k in store.load_eval_set(name, annotations=annotations)]
        case SelectionFile(name):
            ids = [LineId.from_key(k) for k in store.load_selection(name, annotations=annotations)]
        case _:
            assert_never(recipe.selector)
    books = set(recipe.books)
    stray = sorted({lid.book_id for lid in ids if lid.book_id not in books})
    if stray:
        raise ValueError(f"selection has lines from books {stray} not in recipe.books "
                         f"{sorted(books)} — likely a recipe scope bug")
    out: Selection = {b: set() for b in recipe.books}
    for lid in ids:
        out[lid.book_id].add(lid)
    return out


def _build_and_save(recipe: Recipe, selection: Selection, task_id: TaskId, *,
                    annotations: Path | None, teacher_store: Path | None,
                    render: RenderFn | None) -> tasks.Task:
    """Tile a per-book `selection` into regions → build + persist the task bundle under `task_id`
    (manifest committed, payload derived). A VISION recipe MUST be given a `render` builder and every
    region MUST get a COMPOSITE — a vision task with no images is REFUSED (a silent text fallback
    would corrupt the panel). The ONE bundle builder, shared by the initial `build` and the
    `route`-derived human-adjudication sub-task, so both mint keys + render identically."""
    if recipe.vision and render is None:
        raise ValueError(
            f"recipe {recipe.task_id!r} reads vision but no render builder was given — refusing to "
            f"build a vision task with no images")
    records = {b: store.load_records(b, recipe.lang) for b in recipe.books}
    specs: list[ItemSpec] = []
    for b in recipe.books:
        specs.extend(tile_regions(b, records[b], selection.get(b, set()), target=recipe.target,
                                  context_radius=recipe.context_radius))
    assets = render(specs) if recipe.vision else {}
    if recipe.vision:
        bare = [s.region_id for s in specs
                if not any(a.kind is AssetKind.COMPOSITE for a in assets.get(s.region_id, ()))]
        if bare:
            raise ValueError(f"vision recipe: render produced no COMPOSITE for regions {bare}")
    task = tasks.build_task(title=recipe.title, instructions=recipe.instructions,
                            specs=specs, records=records, assets=assets)
    store.save_task_bundle(task_id, task.to_payload(), task.manifest.to_dict(),
                           annotations=annotations, store=teacher_store)
    return task


def build(recipe: Recipe, *, annotations: Path | None = None, teacher_store: Path | None = None,
          render: RenderFn | None = None) -> tasks.Task:
    """Resolve the recipe's selector → the votable lines to poll → tile + persist the task bundle.
    Records come from the record cache; the deterministic, reproducible task build is the provenance
    layer the derived payload can be rebuilt from."""
    selected = select_lines(recipe, annotations=annotations)
    return _build_and_save(recipe, selected, recipe.task_id, annotations=annotations,
                           teacher_store=teacher_store, render=render)


class PanelRefused(Exception):
    """A live panel run that is NOT safe to promote. Raised — never a silent partial promote — when
    a rep was truncated, produced no parseable rows, or the resolution surfaced any fault. The
    per-rep evidence and the raw calls are already persisted, so the refusal is investigable and the
    next run RESUMES from the saved replies."""


def panel(recipe: Recipe, completer: panel_mod.ChatCompleter, *,
          annotations: Path | None = None, teacher_store: Path | None = None) -> int:
    """Run the panel for a built task and promote ONLY if the run is clean. Loads the bundle →
    readers × reps (RESUMING any saved `(item, reader, rep)` reply, persisting each fresh one before
    parse) → save the per-rep evidence → REFUSE to promote on any unclean condition → else resolve,
    aggregate to one vote per (reader, line), and promote. Returns the count of canonical votes
    promoted. Network is the injected `completer`.

    Refuses (raises `PanelRefused`, never a silent subset) when ANY:
      - a rep's `finish_reason == "length"` (truncated output, under-covered);
      - any `(reader, item, rep)` parsed ZERO rows (empty / malformed / all-reasoning reply);
      - `resolve_panel(..., complete=True)` returns ANY fault (unmapped/dup/bad-label/text-drift/
        missing/unknown-item/key-mismatch).
    A call that fails mid-run raises out of `run_panel` before any promote; the calls saved so far
    are the resumable record (re-run reuses them, re-calling only the missing tuples)."""
    payload, manifest_d = store.load_task_bundle(recipe.task_id, annotations=annotations,
                                                 store=teacher_store)
    task = tasks.Task.from_bundle(payload, manifest_d)
    records = {b: store.load_records(b, recipe.lang) for b in recipe.books}
    cfg = panel_mod.PanelConfig(readers=recipe.readers, reps=recipe.reps, contract=recipe.contract)

    # resume: reuse saved replies; persist each fresh one (the row shape is the request's `to_row`)
    # the instant it lands, before parse, so a crash mid-run loses no paid call.
    cached = panel_mod.resume_cache(store.load_panel_calls(recipe.task_id, store=teacher_store))
    reps = panel_mod.run_panel(
        task, cfg, completer, cached=cached,
        on_call=lambda req, reply: store.save_panel_call(recipe.task_id, req.to_row(reply),
                                                         store=teacher_store),
        instructions_by_modality=recipe.prompts or None, max_workers=recipe.max_workers)
    store.save_panel_reps(recipe.task_id, _rep_rows(reps, recipe.contract), annotations=annotations)

    truncated = [(r.item_id, r.tag, r.rep) for r in reps
                 if r.finish_reason == panel_mod.FinishReason.LENGTH]
    if truncated:
        raise PanelRefused(f"refusing to promote {recipe.task_id!r}: truncated reps "
                           f"(finish_reason={panel_mod.FinishReason.LENGTH}): {truncated}")
    empty = [(r.item_id, r.tag, r.rep) for r in reps if not r.response.rows]
    if empty:
        raise PanelRefused(f"refusing to promote {recipe.task_id!r}: reps with ZERO parsed rows "
                           f"(empty/malformed reply): {empty}")

    by_rep: dict[int, list[responses.RawReaderResponse]] = {}
    for rep in reps:
        by_rep.setdefault(rep.rep, []).append(rep.response)
    resolved = [responses.resolve_panel(task.manifest, rs, records, complete=True)
                for _, rs in sorted(by_rep.items())]
    faults = [f for rv in resolved for f in rv.faults]
    if faults:
        raise PanelRefused(f"refusing to promote {recipe.task_id!r}: {len(faults)} resolution "
                           f"fault(s), e.g. {[f'{f.fault}:{f.key or f.item_id}' for f in faults[:5]]}")
    # per-READER coverage: `resolve_panel`'s MISSING_KEY check counts a key answered if ANY reader
    # answered it, so a reader silently OMITTING a key another reader covered slips through (the
    # structured-output enum forbids invalid keys, not omission). Under the gate that can thin the
    # deciding panel or flip a line's routing invisibly — so refuse if any reader left a votable line
    # unanswered in any rep.
    votable = set(task.manifest.by_key.values())
    tags = {r.tag for r in recipe.readers}
    for rv in resolved:
        covered: dict[ReaderTag, set[LineId]] = defaultdict(set)
        for v in rv.votes:
            covered[v.tag].add(v.id)
        gaps = sorted((tag, str(lid)) for tag in tags for lid in votable - covered[tag])
        if gaps:
            raise PanelRefused(f"refusing to promote {recipe.task_id!r}: {len(gaps)} (reader, line) "
                               f"votes missing — a reader omitted keys: {gaps[:5]}")
    # stamp each canonical vote with THIS campaign so `route` consumes only its own task's evidence
    votes = [replace(v, task=recipe.task_id)
             for v in panel_mod.aggregate_reps([rv.votes for rv in resolved])]
    return promote.promote_votes(votes, annotations=annotations)


class IngestRefused(Exception):
    """A human adjudication that is NOT safe to promote. Raised — never a silent partial promote —
    when resolving the downloaded responses against the manifest surfaces ANY fault (unmapped/dup/
    bad-label/text-drift/missing/unknown-item/key-mismatch), symmetric with `PanelRefused`. The raw
    responses are already committed, so the refusal is investigable and fixable."""


def ingest(recipe: Recipe, *, task_id: TaskId | None = None, annotations: Path | None = None,
           teacher_store: Path | None = None) -> int:
    """Ingest the human adjudication for a built task: load the responses → parse → resolve against
    the manifest → REFUSE to promote on any resolution fault → else promote labels. Returns the
    count of labels promoted. `task_id` defaults to the recipe's own task; pass the
    `<task_id>-adjudication` sub-task id to ingest the queue `route` produced (same recipe, same
    books/lang/title — only the bundle differs)."""
    tid = task_id or recipe.task_id
    _, manifest_d = store.load_task_bundle(tid, annotations=annotations, store=teacher_store)
    manifest = tasks.TaskManifest.from_dict(manifest_d)
    records = {b: store.load_records(b, recipe.lang) for b in recipe.books}
    parsed = responses.parse_ui_responses(store.load_human_responses(tid, annotations=annotations))
    resolved = responses.resolve_adjudication(manifest, parsed, records, title=recipe.title,
                                              complete=True)
    if resolved.faults:
        raise IngestRefused(
            f"refusing to promote {tid!r}: {len(resolved.faults)} resolution "
            f"fault(s), e.g. {[f'{f.fault}:{f.key or f.item_id}' for f in resolved.faults[:5]]}")
    return promote.promote_labels(resolved.labels, annotations=annotations)


# --- route: the SETTLED decision policy, wired into the live promote path ----------------------

ADJUDICATION_SUFFIX = "-adjudication"   # the human-queue sub-task id is <task_id> + this
GATE_AUDIT_STATUS = "gate_accepted"     # the `audit_status` a gate-accepted label carries
_PROTECTED_SOURCES = frozenset({LabelSource.HUMAN, LabelSource.OVERRIDE})


@dataclass(frozen=True, slots=True)
class RouteResult:
    """The outcome of routing a campaign's panel votes through the live decision policy. `accepted`
    lines became `gate` truth in `labels.jsonl`; `queued_human` lines were written to the
    `adjudication_task_id` sub-task for `adjudicate.html`. `accepts_protected`/`human_protected`
    count lines the gate would have accepted/queued but a HUMAN/override label already settled — never
    overwritten, never re-adjudicated (this is also what keeps a frozen eval-set adjudication out of
    the loop). `operational` is the count of queued routes whose reason is a coverage gap a live
    re-run could ESCALATE with more reps (vs a terminal ambiguity only a human resolves); `by_reason`
    breaks the queue down. `uncovered` counts the task's votable lines NO deciding reader voted on —
    they get no decision and are surfaced here rather than silently dropped (an under-covered panel;
    the live fix is to re-panel, not to invent a label). The escalation LOOP itself is a deferred
    live-run concern — this only surfaces the seam."""
    accepted: int
    accepts_protected: int
    queued_human: int
    human_protected: int
    operational: int
    uncovered: int
    by_reason: dict[str, int]
    adjudication_task_id: TaskId | None


def _prior_queued_lines(adj_task_id: TaskId, *, annotations: Path | None,
                        teacher_store: Path | None) -> set[LineId] | None:
    """The line set an existing adjudication sub-task was minted over, or None if none exists yet.
    `route` compares it to the lines it is about to queue: an identical set re-mints identical keys
    (tiling is deterministic) so the rebuild is safe, but a CHANGED set would reassign keys under any
    human responses already filed against the old manifest — so route refuses that."""
    if not store.task_manifest_exists(adj_task_id, annotations=annotations):
        return None
    _, manifest_d = store.load_task_bundle(adj_task_id, annotations=annotations, store=teacher_store)
    return set(tasks.TaskManifest.from_dict(manifest_d).by_key.values())


def _existing_labels(*, annotations: Path | None) -> dict[LineId, LineLabel]:
    """The committed truth keyed by line, or empty on a fresh store — the precedence lookup `route`
    consults before it lets the gate write or queue a line."""
    try:
        rows = store.load_label_rows(annotations=annotations)
    except FileNotFoundError:
        return {}
    return {g.id: g for g in (LineLabel.from_dict(d) for d in rows)}


def _is_protected(label: LineLabel | None) -> bool:
    """A line a human (or an explicit override) already settled — the gate must not touch it."""
    return label is not None and label.source in _PROTECTED_SOURCES


def _gate_label(d: LineDecision, line_votes: Mapping[ReaderTag, PanelVote], *, task_id: TaskId,
                roster: PanelRoster, policy_name: str, text_hash: LineTextHash | None) -> LineLabel:
    """An ACCEPT decision → a `gate`-sourced `LineLabel`. The confidence is the anchor's self-report
    (the gate's confidence proxy); the provenance records the policy, the reason, the anchor role, and
    the per-reader votes — the DECIDING (roster) votes under `votes`, any diagnostic readers' votes
    separately under `diagnostic_votes`, so the audit trail says what the gate actually weighed vs
    what was merely observed. The roster + policy are the ones `route` already resolved."""
    assert d.label is not None                  # ACCEPT always carries a label
    deciding = {roster.anchor, *roster.support}
    anchor_vote = line_votes.get(roster.anchor)
    provenance: dict[str, object] = {
        "task": task_id, "policy": policy_name, "reason": d.reason.value, "anchor": roster.anchor,
        "votes": {t: line_votes[t].label for t in sorted(line_votes) if t in deciding}}
    diagnostic = {t: line_votes[t].label for t in sorted(line_votes) if t not in deciding}
    if diagnostic:
        provenance["diagnostic_votes"] = diagnostic
    return LineLabel(
        id=d.id, label=d.label, source=LabelSource.GATE,
        confidence=anchor_vote.conf if anchor_vote else None,
        audit_status=GATE_AUDIT_STATUS, notes="", provenance=provenance, line_text_hash=text_hash)


def route(recipe: Recipe, *, allow_partial: bool = False, annotations: Path | None = None,
          teacher_store: Path | None = None, render: RenderFn | None = None) -> RouteResult:
    """Wire the SETTLED decision policy into the live promote path: take THIS campaign's promoted panel
    votes (`votes.jsonl`, restricted to the task's votable lines AND to votes the task itself
    produced), apply the recipe's `decision` policy over its `roster`, and split the result — ACCEPT
    lines are promoted as `gate` truth, HUMAN lines are tiled into a `<task_id>-adjudication` sub-task
    `adjudicate.html` consumes (then `ingest` brings the human verdicts back). Precedence-safe: a line
    a human/override already settled is never relabeled by the gate nor re-queued. Re-running on
    UNCHANGED votes is idempotent (gate labels merge; the human sub-task re-mints identically); a
    re-route whose human set CHANGED is refused, since re-minting the sub-task's keys would corrupt
    human responses already filed against it.

    Because `panel` promotes all-or-nothing, a SUCCESSFUL panel covers every task line — so a line with
    no vote from this task means the panel did not (successfully) run. `route` REFUSES on any such
    `uncovered` line (re-run the panel) unless `allow_partial`, rather than route on stale or partial
    evidence. Pure decision logic lives in `teacher.decision`; this is its live IO shell."""
    if recipe.roster is None or recipe.decision is None:
        raise ValueError(f"recipe {recipe.task_id!r} is not routed — it has no [roster]/[decision]; "
                         f"route needs the settled decision config")
    roster, policy = recipe.roster, recipe.decision
    _, manifest_d = store.load_task_bundle(recipe.task_id, annotations=annotations,
                                           store=teacher_store)
    task_lines = set(tasks.TaskManifest.from_dict(manifest_d).by_key.values())

    all_votes = [v for v in load_votes(annotations=annotations)
                 if v.id in task_lines and v.task == recipe.task_id]
    by_line: dict[LineId, dict[ReaderTag, PanelVote]] = defaultdict(dict)
    for v in all_votes:
        by_line[v.id][v.tag] = v
    routing = decision_mod.route_with(policy, all_votes, roster)

    existing = _existing_labels(annotations=annotations)
    records = store.load_records_many(recipe.books, recipe.lang)
    hash_by_id = {r.id: r.line_text_hash for recs in records.values() for r in recs}

    # Pure pass first — decide every line; perform NO writes until all the raise-prone validation
    # (the re-route key-stability guard, a vision recipe's render precondition) has passed, so a
    # misconfigured route can never half-write truth.
    gate_labels: list[LineLabel] = []
    accepts_protected = 0
    for d in routing.accepted:
        if _is_protected(existing.get(d.id)):
            accepts_protected += 1
            continue
        gate_labels.append(_gate_label(d, by_line[d.id], task_id=recipe.task_id, roster=roster,
                                       policy_name=policy.name, text_hash=hash_by_id.get(d.id)))

    human_sel: Selection = {b: set() for b in recipe.books}
    human_protected = 0
    operational = 0
    by_reason: Counter[str] = Counter()
    for d in routing.human:
        if _is_protected(existing.get(d.id)):
            human_protected += 1
            continue
        human_sel[d.id.book_id].add(d.id)
        by_reason[d.reason.value] += 1
        if d.reason in decision_mod.OPERATIONAL_REASONS:
            operational += 1

    decided = {d.id for d in routing.accepted} | {d.id for d in routing.human}
    uncovered = len(task_lines - decided)               # no vote from this task's panel covered them
    if uncovered and not allow_partial:
        raise ValueError(
            f"route {recipe.task_id!r}: {uncovered} of {len(task_lines)} votable lines have no vote "
            f"from this task's panel — re-run the panel (it promotes all-or-nothing) so coverage is "
            f"complete, or pass allow_partial=True to route only the covered lines")
    queued_ids = {lid for ids in human_sel.values() for lid in ids}

    # Build the human queue (the raise-prone step) BEFORE promoting accepts. Re-minting the sub-task's
    # opaque keys over a DIFFERENT line set would silently corrupt any human responses already filed
    # against the old manifest — so refuse a changed set; an identical set re-mints identically (safe).
    adj_task_id = f"{recipe.task_id}{ADJUDICATION_SUFFIX}"
    prior = _prior_queued_lines(adj_task_id, annotations=annotations, teacher_store=teacher_store)
    if prior is not None and prior != queued_ids:
        raise ValueError(
            f"adjudication sub-task {adj_task_id!r} already exists for a different line set "
            f"(was {len(prior)}, now {len(queued_ids)}) — it is single-use: ingest it, then remove the "
            f"bundle, before re-routing. Re-minting its keys would corrupt human responses already "
            f"filed against the old manifest")
    adjudication_task_id = None
    if queued_ids:
        _build_and_save(recipe, human_sel, adj_task_id, annotations=annotations,
                        teacher_store=teacher_store, render=render)
        adjudication_task_id = adj_task_id
    if gate_labels:
        promote.promote_labels(gate_labels, annotations=annotations)

    return RouteResult(accepted=len(gate_labels), accepts_protected=accepts_protected,
                       queued_human=len(queued_ids), human_protected=human_protected,
                       operational=operational, uncovered=uncovered, by_reason=dict(by_reason),
                       adjudication_task_id=adjudication_task_id)


def _rep_rows(reps: Sequence[panel_mod.PanelRep],
              contract: panel_mod.ResponseContract) -> list[JsonRow]:
    """Flatten per-rep panel output to committed evidence rows in `panel_runs`, behind the resolved
    `votes.jsonl`. Each rep emits ONE header row carrying the RAW completion `content` + finish
    status + the response `contract` it was shaped under (so a malformed/empty reply leaves evidence
    even with no parsed rows), then one verdict row per parsed `{key, label, conf}`."""
    out: list[JsonRow] = []
    for rep in reps:
        base: JsonRow = {"item_id": rep.item_id, "tag": rep.tag, "rep": rep.rep, "model": rep.model,
                         "finish_reason": rep.finish_reason, "contract": contract.value}
        out.append({**base, "kind": "raw", "content": rep.content,
                    "n_rows": len(rep.response.rows), "usage": rep.usage})
        out.extend({**base, "kind": "verdict", "key": row.key, "label": row.label, "conf": row.conf}
                   for row in rep.response.rows)
    return out


def _render_fn(recipe: Recipe) -> RenderFn | None:
    """The LibreOffice page compositor a VISION recipe needs (the page is the authority), or None for
    a text recipe. Built lazily so the import + LibreOffice are only touched on a vision run."""
    if not recipe.vision:
        return None
    from . import render as render_mod
    return render_mod.make_compositor(render_mod.libreoffice_pages())


def _main() -> None:
    """CLI: `uv run python -m lineation_core.teacher.recipes <build|panel|route|ingest> <recipe.toml>`.
    `build` persists the task bundle (text recipes; a vision recipe needs `render.py` wired); `panel`
    runs the live OpenRouter panel and promotes votes ONLY if the run is clean (a truncated/empty/
    faulted run raises `PanelRefused` and promotes nothing — re-run resumes from saved replies);
    `route` applies the recipe's settled decision policy to the promoted votes — auto-accepting `gate`
    labels and tiling the rest into a `<task_id>-adjudication` sub-task; `ingest` resolves the
    downloaded human responses and promotes labels (`--task-id` targets the adjudication sub-task). A
    live `panel` run needs the SDK extra:
        `uv run --extra live python -m lineation_core.teacher.recipes panel <recipe.toml>`."""
    import argparse

    parser = argparse.ArgumentParser(prog="lineation-teacher",
                                     description="build / panel / route / ingest a lineation recipe")
    parser.add_argument("command", choices=("build", "panel", "route", "ingest"))
    parser.add_argument("recipe", help="path to a recipe .toml")
    parser.add_argument("--task-id", default=None,
                        help="ingest: the task bundle to resolve (default: the recipe's own task; "
                             "pass <task_id>-adjudication for the route queue)")
    parser.add_argument("--allow-partial", action="store_true",
                        help="route: proceed even if some task lines have no vote from this task's "
                             "panel (default: refuse and ask you to re-run the panel)")
    args = parser.parse_args()
    recipe = load_recipe(Path(args.recipe).read_text())

    if args.command == "build":
        task = build(recipe, render=_render_fn(recipe))
        print(f"built {recipe.task_id}: {len(task.items)} items, "
              f"{len(task.manifest.by_key)} votable lines")
    elif args.command == "panel":
        from .openrouter import OpenRouterCompleter
        print(f"promoted {panel(recipe, OpenRouterCompleter())} panel votes for {recipe.task_id}")
    elif args.command == "route":
        r = route(recipe, allow_partial=args.allow_partial, render=_render_fn(recipe))
        print(f"routed {recipe.task_id}: accepted {r.accepted} (gate), "
              f"queued {r.queued_human} to human"
              + (f" → {r.adjudication_task_id}" if r.adjudication_task_id else "")
              + (f"; {r.operational} escalatable" if r.operational else "")
              + (f"; protected {r.accepts_protected + r.human_protected} (human/override)"
                 if r.accepts_protected or r.human_protected else "")
              + (f"; {r.uncovered} UNCOVERED (re-panel)" if r.uncovered else ""))
        if r.by_reason:
            print("  human routes by reason: "
                  + ", ".join(f"{k}={v}" for k, v in sorted(r.by_reason.items())))
    else:
        print(f"promoted {ingest(recipe, task_id=args.task_id)} human labels "
              f"for {args.task_id or recipe.task_id}")


if __name__ == "__main__":
    _main()
