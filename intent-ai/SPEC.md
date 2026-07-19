# Lineation contract

## Decision unit

A source line is one non-empty segment of a content paragraph, split only by an explicit OOXML line
break. Visual wrapping is a feature of that line, not another line. Page and column breaks do not
split it.

```text
LineId(language, book_id, source_ordinal, sub)
```

`source_ordinal` is the canonical `w:p` ordinal. `sub` is the natural-line index within that
paragraph. The compiler projects fold fate on this same coordinate; sibling lines do not inherit
one paragraph-level verdict. Structural scope reduces on the same coordinate: label+body
co-claims remain body, while label-only coordinates remain context and never enter the lineation
vote. A surviving display line may carry several ordered coordinates when a named compiler
transform removes their boundary. `LineId` can represent only active canonical identities; `LegacyLineId` is a distinct
type used only by migration lineage and historical manifests.

## Canonical record

```text
LineRecord
  id
  text
  disposition
  features
  line_text_hash
```

`disposition` is the closed research outcome: body, structural context, or importer-lost. Only
`BODY` contributes to the lineation model; `votable` is derived from that fact. The producer derives
the value once from `StructuralObservation`; consumers do not reclassify the source. There is no
copied inline tree or IR block index.

`LineFeatures` contains:

- physics: fill, wraps, character and word counts;
- boundaries: punctuation, casing, enjambment, colon opening;
- layout: alignment, indentation, spacing, and paragraph segment position;
- source context: run coordinates and within-book fill percentile. Boundaries derive from the
  coordinates; they are not independently stored facts.

Features are language-agnostic and source-derived. Labels, predictions, raw book identity, and raw
style IDs are not features. Teacher listings and student vectors are views over the same feature
value.

## Truth

`labels.jsonl` is the only active truth store. A `LineLabel` contains the line identity, two-class
label, source, holdout status, text hash, and opaque lineage. Eval sets contain identities only.

Loading truth is strict: every active identity must be canonical. Building a dataset is a total
join over records and checks record existence, votability, text-hash presence, and text-hash
equality. Any failure aborts the dataset.

Source changes use a typed migration ledger:

```text
moved | needs_adjudication | retired
```

Unchanged identities need no event row: their active key and text hash already prove the no-op.
Moved entries enter the active store only when old and new text hashes agree. Ambiguous or absent
lines remain in history. Migration is never a loader fallback.

## Model contract

The student predicts `P(lineated)` from the fixed feature schema. Cross-validation groups by
`(language, book)` so no edition appears in its own training fold. Run smoothing is a decoding step
over held-out posteriors, not a feature or label source.

Scoring is document-shaped: one ordered feature batch becomes a `ScoredDocument` of source-attributed
base posteriors. IID and smoothed decisions are interpretations of that same value; a policy sweep
never refits or re-scores the document.

Serialized weights pin both `feature_schema_version` and `producer_version`. Records, manifests,
and weights reject version skew.

## Artifacts

Derived per-edition cache:

```text
_artifacts/<book>-<lang>/
  line_records.jsonl
  feature_schema.json
  manifest.json
```

Committed annotation state:

```text
annotations/
  labels.jsonl
  votes.jsonl
  eval_sets/
  selections/
  tasks/
  responses/
  panel_runs/       # immutable raw/per-rep evidence as deterministic .jsonl.gz
  migrations/
  history/
```

Record caches are deterministic and ignored. Raw panel responses and adjudication lineage are
durable evidence.
