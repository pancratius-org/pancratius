# lineation-core architecture

Organize by **lifecycle role**, not by artifact type. A line flows through six stages; every module
has one home, named for the stage it serves. (Data shapes + algorithms are in `SPEC.md`; this is the
code-organization contract.)

## The pipeline — an active-learning loop

    produce → estimate uncertainty → select → privileged teach → update student → judge → repeat

    store — the single disk boundary, used by every stage

- **produce** — DOCX → `LineRecord` (the feature substrate): `identity`, `records`, `source_view`,
  `physics`, `producer`.
- **estimate uncertainty** — the current student predicts a label AND estimates its uncertainty over
  the unlabelled pool (inference): `student/`.
- **select** — choose the lines worth labelling (uncertainty sampling / acquisition): `selection.py`
  writes `annotations/selections/`. It reads the student's uncertainty but the teacher consumes its
  output only as DATA (no import).
- **privileged teach** — the teacher panel + human see RICHER (LUPI-privileged) evidence than the
  student — the rendered page, the full reader panel — and create labels: `teacher/`.
- **update student** — retrain / recalibrate the student on the new labels (training): `student/`.
- **judge** — score methods (decision policies, readers, prompts, the student, acquisition) against
  committed truth; NEVER creates truth: `evaluation/`.
- **store** — the ONLY module that knows disk layout; every other module takes and returns data:
  `store.py` (the record cache is a store-internal detail, not a second boundary).

Inference (predict + estimate uncertainty) and training (update) are distinct STAGES but both are the
student, so they share the `student/` home.

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
are FROZEN as committed `eval_sets/` and NEVER folded into training, so they score prompts/policies
without leakage.

## Tree

    campaigns/                  authored run definitions
      prompts/                  model-facing reader instructions (e.g. lineation-v5-page-only.md)
      recipes/                  run config TOML (a recipe references a prompt by name)
    annotation-studio/
      adjudicate.html           the human adjudication tool — nothing else lives here
    annotations/                committed truth/evidence a campaign produces
      labels.jsonl  votes.jsonl  eval_sets/  selections/  tasks/  responses/  panel_runs/
    _artifacts/   _teacher/      gitignored, rebuildable
    src/lineation_core/
      identity.py  records.py  source_view.py  physics.py  producer.py      # produce
      annotations.py            # one typed annotation model: LineLabel (truth) + PanelVote (evidence)
      selection.py              # select
      store.py  paths.py  build_records.py                                  # store (the one boundary)
      teacher/                  # teach
      student/                  # predict + update (the student + sequence)
      evaluation/               # judge (incl. acquisition = the strategy eval)

The produce/store/select core stays flat: those modules are the cohesive substrate, and foldering
tightly-coupled produce modules buys nothing. Only the multi-module roles (teach / predict / judge)
earn folders.

## Invariants

- `store.py` is the single disk boundary — no other module reads or writes committed files or caches.
- The **teacher never imports the student.** `selection.py` writes a committed file; the teacher
  reads it as data. `evaluation/` may import both — it is the downstream judge.
- `annotations/` holds committed truth; `campaigns/` holds authored inputs; `annotation-studio/` is
  the human tool. A prompt is for model readers and a recipe is run config — neither is human
  annotation, so neither lives in the studio.
- `teacher/` (and `student/`) are named for the **agents** of the distillation, not for categories —
  the system is a teacher labelling a student.
- One annotation model: `LineLabel` (truth) and `PanelVote` (evidence) are distinct types in
  `annotations.py`, co-located but never conflated.
