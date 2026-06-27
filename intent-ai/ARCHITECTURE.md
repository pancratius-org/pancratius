# lineation-core architecture

Organize by **lifecycle role**, not by artifact type. A line flows through six stages; every module
has one home, named for the stage it serves. (Data shapes + algorithms are in `SPEC.md`; this is the
code-organization contract.)

## The pipeline — an active-learning loop

    produce → estimate uncertainty → select → privileged teach → update student → judge → repeat

    store — the persistence boundary (this package's own disk), used by every stage

- **produce** — DOCX → `LineRecord` (the feature substrate): pure substrate (`identity`, `records`),
  the DOCX input adapter (`physics`, `source_view` — read the source via the importer), and the
  assembler (`producer`, the ONE feature producer).
- **estimate uncertainty** — the current student predicts a label AND estimates its uncertainty over
  the unlabelled pool (inference): `student.py` (+ `sequence.py` for run-level smoothing).
- **select** — choose the lines worth labelling (uncertainty sampling / acquisition): `selection.py`
  writes `annotations/selections/`. It reads the student's uncertainty but the teacher consumes its
  output only as DATA (no import).
- **privileged teach** — the teacher panel + human see RICHER (LUPI-privileged) evidence than the
  student — the rendered page, the full reader panel — and create labels: `teacher/`.
- **update student** — retrain / recalibrate the student on the new labels (training): `student.py`.
- **judge** — score methods (decision policies, readers, prompts, the student, acquisition) against
  committed truth; NEVER creates truth: `evaluation/`.
- **wire to production** — outside the AL loop, makes no truth: `corrections.py` projects committed
  truth into per-book `lineation.<lang>.json` importer sidecars; `recon.py` runs the corpus census
  (det ⋈ student over every book — the E0 denominator + router input).
- **store** — the truth/evidence disk (`annotations/`, `_teacher/`, experiments): `store.py`, over the
  `artifact.py` cache IO (`_artifacts/`). See Invariants for the write boundary.

Inference (predict + estimate uncertainty) and training (update) are distinct STAGES but both are the
student, so they share the `student.py` home (with `sequence.py` alongside for the run-smoothing the
prediction API is shaped around).

The **privileged-teach** stage is itself a small pipeline of `teacher.recipes` steps, each a file
boundary, ending at the committed truth the judge reads:

    build → panel ── votes.jsonl ──(route)──┬─ ACCEPT → gate labels ──────────────────────→ labels.jsonl
                                            └─ HUMAN  → <task_id>-adjudication ─(adjudicate.html)─(ingest)─→ labels.jsonl

`route` applies the settled cross-reader policy (`teacher.decision`) to a task's votes: it auto-accepts
the confident agreements as `gate` truth and tiles the rest into a single-use human sub-task. It is
scoped to ITS task (votes carry their producing `task`) and refuses to proceed on partial coverage —
the live decision driver, distinct from the offline policy `evaluation/` judges.

## Bootstrapping vs active learning

This is **seeded, pool-based active learning** — round 0 has no student.

- **Round 0 — bootstrap (no student).** Sample diverse / random / problematic regions, run the
  privileged LLM teacher panel, route the panel's DISAGREEMENTS to human adjudication, and freeze
  held-out validation slices for prompt/model/policy tuning. This builds the seed labelled set.
- **Rounds 1..N — active learning.** The current student estimates uncertainty over the unlabelled
  pool; `selection.py` writes a committed selection; the privileged teacher/human labels it; the
  student is updated; `evaluation/` judges. Repeat.

Two acquisition signals feed the human oracle, at two stages: **student uncertainty** (`selection.py`
— which *unlabelled* lines to send the panel) and **panel/committee disagreement**
(`teacher/decision.py` — which *panel-labelled* lines to send the human). Held-out validation slices
are FROZEN as committed `eval_sets/` MEMBERSHIPS (LineId keys only); their truth lives in
`labels.jsonl` like all truth, marked `holdout` where it must never become a training target, so
they score prompts/policies without leakage and without a second label store.

## Authored configs — three TOML roles

Authored TOML shares grammar (readers/prompts/selection from a recipe; roster/decision from a policy)
but splits into three roles, DISTINCT in what each may produce:

- **Teacher recipe** (`campaigns/recipes/`) — operational, privileged: "run these readers on these
  lines." MAY create panel calls, votes, routes, human tasks, and labels. The ONLY role that makes
  truth.
- **Policy-eval config** (judge side) — replay-only: "given existing votes + human truth, compare
  decision policies." No paid calls, no new truth.
- **Experiment / study** (`evaluation/experiments/<date-slug>/`) — the lab-notebook unit: a research
  question + hypothesis + frozen dataset + sweep + metrics. MAY invoke the teacher panel as an
  INSTRUMENT, but its output is EVIDENCE (a scorecard), never truth.

An experiment folder is self-contained: `experiment.toml` (authored + committed, hypothesis in the top
comment), `scorecard.json` + `report.md` (the durable result), `manifest.json` (provenance — git SHA,
prompt/eval-set/response-contract fingerprints, model ids, sampling, price-table version), and a
derived `replies.jsonl` resume cache (committed only when a claim needs it to reproduce). It is run by
a STUDY runner, not the recipe runner — the name keeps "produces evidence" apart from "produces truth".

A production LABELLING campaign is a teacher recipe + a committed selection — never an experiment.

## Tree

    campaigns/                  authored run definitions
      prompts/                  model-facing reader instructions, per modality
                                (live: lineation-page.md for vision, lineation-structure-text.md for text)
      recipes/                  run config TOML (a recipe references a prompt PER modality by name)
    annotation-studio/
      adjudicate.html           the human adjudication tool — nothing else lives here
    annotations/                committed truth/evidence a campaign produces
      labels.jsonl  votes.jsonl  eval_sets/  selections/  tasks/  responses/  panel_runs/
    _artifacts/   _teacher/      gitignored, rebuildable
    src/lineation_core/
      identity.py  records.py  source_view.py  physics.py  producer.py      # produce
      annotations.py            # one typed annotation model: LineLabel (truth) + PanelVote (evidence)
      selection.py              # select
      student.py  sequence.py   # predict + update (the student; sequence = run-level smoothing)
      recon.py                  # corpus census: det-verdict ⋈ student-posterior over every book (E0/router input)
      corrections.py            # production wiring: committed truth → per-book lineation.<lang>.json importer sidecars
      store.py  artifact.py  build_records.py  paths.py                     # persistence: store (truth/evidence) over artifact (cache IO)
      vendor/                   # vendored third-party deps
      teacher/                  # teach
      evaluation/               # judge (incl. acquisition = the strategy eval)
        experiments/<slug>/     # lab-notebook units: experiment.toml + scorecard.json/report.md + manifest.json

Folders are for navigation: they group a multi-file SUBSYSTEM (`teacher/`, `evaluation/`). The flat
modules are separate for the ordinary reason — distinct layers/deps/consumers, not a subsystem to fold.
(e.g. `sequence.py` is the model-agnostic run-smoothed decoder — the sklearn-free `Posterior` seam —
that `student.py`'s model plugs into; two layers, not one unit with a hidden interior.) A deferred,
user-flagged reorg (`foundation/` / `model/` / `teacher/` / `evaluation/`) would regroup by lifecycle.

## Invariants

- **Disk writes live in three modules — `artifact` (cache), `store` (truth/evidence), `corrections`
  (the one write into production content). Enforced by `tests/test_io_boundary.py`, not prose.**
  Everything else reads only (the DOCX via the importer).
- The **teacher never imports the student.** `selection.py` writes a committed file; the teacher
  reads it as data. `evaluation/` may import both — it is the downstream judge.
- `annotations/` holds committed truth; `campaigns/` holds authored inputs; `annotation-studio/` is
  the human tool. A prompt is for model readers and a recipe is run config — neither is human
  annotation, so neither lives in the studio.
- `teacher/` and `student.py` are named for the **agents** of the distillation, not for categories —
  the system is a teacher labelling a student.
- One annotation model: `LineLabel` (truth) and `PanelVote` (evidence) are distinct types in
  `annotations.py`, co-located but never conflated.
- An **experiment produces evidence, never truth** — only a teacher recipe (+ a committed selection)
  makes labels. A study may call the teacher panel as an instrument; its committed output is a
  scorecard + provenance manifest, scored against a FROZEN `eval_set`, so research and truth never blur.
