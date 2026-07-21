# intent-ai architecture

## Boundary

`pancratius.docx_source` owns package traversal, ordered rich content, break kinds,
paragraph identity, layout, and source dispositions. `pancratius.docx_structure` binds
that source document to one total, locale-specific compiler observation. `intent-ai` is a
downstream research package:

```text
DOCX package
  -> DocxSourceDocument            pancratius: syntax and source semantics
  -> StructuralObservation         pancratius: source + locale + compiler roles
  -> canonical natural lines       intent-ai/source_view: physics-only projection
  -> LineRecord                    intent-ai/producer: one feature producer
  -> labels / student / evaluation intent-ai: learning loop
  -> correction sidecars           explicit projection back to the importer
```

The dependency is one-way. Production code never imports `intent_ai`. The correction exporter is a
data boundary, not a Python dependency.

## Source phase

The source document is hydrated once per operation and bound to its locale-specific compiler
observation. All consumers receive that aggregate or a projection from it. They do not reopen
`document.xml`, invoke another document frontend, or store correlated reading/lineated copies.

`ParagraphContent` is an ordered algebra of text and typed breaks. Reading text and natural lines
are exhaustive projections of that same value; rich blocks from the same parse carry the exact
physical paragraph identity into the IR adapter. Page and column breaks remain pagination; only
line breaks form natural-line boundaries. Unknown break syntax fails closed.

Raw paragraph ordinals remain source identity. Semantic adjacency across package segments is
explicit and never inferred by renumbering.

## Learning phase

```text
produce -> estimate uncertainty -> select -> privileged teach -> update -> judge
```

- `identity`, `records`: source-line identity, hashes, and the feature schema.
- `source_view`, `physics`, `producer`: canonical source projection and feature production.
- `student`, `posterior`, `sequence`: training, deployable weights, and run-aware decoding.
- `selection`: acquisition from student uncertainty.
- `teacher/`: panel evidence, routing, and human adjudication.
- `evaluation/`: read-only judges; experiments produce evidence, never truth.
- `annotations`: typed truth and panel evidence.
- `artifact`, `store`: derived-cache and committed-truth persistence.
- `corrections`: the sole projection into importer sidecars.

Teacher and student consume the same `LineFeatures`; the teacher may additionally receive rendered
page evidence. Evaluation consumes predictions as data and does not own model fitting.

## Persistence

- `annotations/labels.jsonl` is active truth.
- `annotations/votes.jsonl` is evidence, not truth.
- `annotations/panel_runs/*.jsonl.gz` stores immutable raw/per-rep evidence in deterministic
  compressed form; it is an artifact, not a line-review surface.
- `annotations/eval_sets/` stores membership only; truth joins from `labels.jsonl`.
- `annotations/migrations/` records total, typed source-identity migrations.
- `annotations/history/` retains unresolved or retired truth outside the active store.
- `_artifacts/` is reproducible and ignored.

Writes inside the importable package are confined to `artifact`, `store`, and `corrections`.
One-shot migration procedures are not retained as product code; their typed ledger is.

## Invariants

1. One typed source aggregate; no parallel OOXML reader.
2. One feature producer; consumers never recompute features.
3. Every active label total-joins a votable canonical record and matching text hash.
4. Source/schema/model version skew fails loud.
5. Teacher truth creation and evaluation remain separate.
6. Derived caches are rebuilt, never committed.
